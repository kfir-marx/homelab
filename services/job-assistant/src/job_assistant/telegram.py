from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .artifacts import FilesystemArtifactStorage
from .career import load_inventory
from .config import Settings
from .contact_policy import ContactPolicyInput, automatic_email_allowed
from .domain import (
    create_application,
    get_application_by_code,
    ingest_job,
    normalize_company_name,
    queue_application_generation,
    record_search_feedback,
    transition_application,
    transition_job,
    transition_outreach,
)
from .generation import build_generation_payload
from .interfaces import NormalizedJob, Notification
from .models import (
    Application,
    ApplicationContact,
    Artifact,
    Company,
    Contact,
    DeliveryAttempt,
    Job,
    JobScore,
    TelegramConversation,
    TelegramUpdate,
)
from .normalization import canonicalize_url, description_hash
from .queue import put_outbox
from .security import validate_public_http_url, validate_upload
from .sources.manual import fetch_public_job
from .states import ApplicationStatus, JobStatus, OutreachStatus

HELP = """Commands:
/add <public-job-url> — add a job
/status <code> — show lifecycle state
/contact <code> — start contact entry
/final <code> — upload final CV, then paste final message
/approve <code> — review the selected delivery target
/manual <code> — mark manual outreach required
/submitted <code> — record the official application submission
/reopen <code> — explicitly reopen a skipped/expired job
/help — show this guide"""


@dataclass(frozen=True)
class TelegramReply:
    chat_id: int
    text: str
    buttons: tuple[tuple[str, str], ...] = ()


class TelegramUpdateHandler:
    def __init__(self, settings: Settings, artifact_storage: FilesystemArtifactStorage) -> None:
        self.settings = settings
        self.artifact_storage = artifact_storage

    def process(self, session: Session, update: dict[str, Any]) -> list[TelegramReply]:
        update_id = int(update["update_id"])
        payload_hash = hashlib.sha256(
            json.dumps(update, sort_keys=True, default=str).encode()
        ).digest()
        if session.get(TelegramUpdate, update_id):
            return []
        session.add(TelegramUpdate(update_id=update_id, payload_hash=payload_hash))
        callback = update.get("callback_query")
        message = update.get("message") or update.get("edited_message")
        actor = callback.get("from") if callback else message.get("from") if message else None
        chat = (
            callback.get("message", {}).get("chat")
            if callback
            else message.get("chat")
            if message
            else None
        )
        if not actor or not chat:
            return []
        user_id, chat_id = int(actor["id"]), int(chat["id"])
        if user_id not in self.settings.telegram_allowed_user_ids:
            return []
        if callback:
            return self._callback(session, chat_id, user_id, str(callback.get("data", "")))
        assert message is not None
        conversation = session.scalar(
            select(TelegramConversation).where(
                TelegramConversation.chat_id == chat_id, TelegramConversation.user_id == user_id
            )
        )
        if conversation and conversation.expires_at and conversation.expires_at < datetime.now(UTC):
            session.delete(conversation)
            conversation = None
        if conversation:
            reply = self._continue_conversation(session, conversation, message)
            if reply:
                return [reply]
        text = str(message.get("text", "")).strip()
        if not text.startswith("/"):
            return [TelegramReply(chat_id, "Use /help to see available commands.")]
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].casefold()
        if command == "/help":
            return [TelegramReply(chat_id, HELP)]
        if command == "/add":
            if not argument:
                return [TelegramReply(chat_id, "Usage: /add <public-job-url>")]
            url = argument.strip()
            try:
                validate_public_http_url(url)
                canonical = canonicalize_url(url)
            except Exception as exc:
                return [TelegramReply(chat_id, f"Could not add that URL safely: {exc}")]
            try:
                candidate = fetch_public_job(
                    url,
                    self.settings.http_timeout_seconds,
                    self.settings.max_download_bytes,
                )
            except Exception:
                candidate = NormalizedJob(
                    source="manual",
                    external_job_id=hashlib.sha256(canonical.encode()).hexdigest()[:24],
                    original_url=url,
                    canonical_url=canonical,
                    company="Unknown company",
                    title="Title requires manual completion",
                    raw_metadata={"requires_manual_completion": True, "fetch_failed": True},
                )
            job, created = ingest_job(session, candidate)
            if candidate.raw_metadata.get("requires_manual_completion"):
                self._set_job_conversation(session, chat_id, user_id, job.id)
                return [
                    TelegramReply(
                        chat_id,
                        "The page could not be parsed completely. Reply with: "
                        "Company | Title | Location | Plain-text description",
                    )
                ]
            return [
                TelegramReply(
                    chat_id,
                    ("Added" if created else "Already tracked")
                    + f": {candidate.company} — {candidate.title}",
                    (
                        ("Apply", f"apply:{job.id}"),
                        ("Skip", f"skip:{job.id}"),
                        ("Open job", f"open:{job.id}"),
                    ),
                )
            ]
        if command in {
            "/status",
            "/final",
            "/contact",
            "/approve",
            "/manual",
            "/submitted",
            "/reopen",
        }:
            if not argument:
                return [TelegramReply(chat_id, f"Usage: {command} <application-code>")]
            application = get_application_by_code(session, argument.strip())
            if not application:
                return [TelegramReply(chat_id, "Unknown application code.")]
            if command == "/status":
                return [TelegramReply(chat_id, self._status(application))]
            if command == "/final":
                self._set_conversation(
                    session, chat_id, user_id, "awaiting_final_cv", application.id
                )
                return [
                    TelegramReply(chat_id, "Upload the final CV as a PDF or DOCX (maximum 10 MB).")
                ]
            if command == "/contact":
                self._set_conversation(
                    session, chat_id, user_id, "awaiting_contact", application.id
                )
                return [
                    TelegramReply(
                        chat_id,
                        "Paste the verified company email. It will still require final approval.",
                    )
                ]
            if command == "/approve":
                return [
                    TelegramReply(
                        chat_id,
                        "Confirming may enqueue email only when a high-confidence, verified "
                        "company-domain contact exists.",
                        (
                            ("Confirm Send", f"confirm:{application.id}"),
                            ("Cancel", f"cancel:{application.id}"),
                        ),
                    )
                ]
            if command == "/manual":
                if application.status in {
                    ApplicationStatus.FINAL_MATERIAL_RECEIVED.value,
                    ApplicationStatus.APPROVED.value,
                }:
                    transition_application(
                        session,
                        application,
                        ApplicationStatus.MANUAL_REQUIRED,
                        f"telegram:{user_id}",
                    )
                if application.outreach_status in {
                    OutreachStatus.NO_CONTACT.value,
                    OutreachStatus.CONTACT_CANDIDATE_FOUND.value,
                    OutreachStatus.DRAFTED.value,
                    OutreachStatus.APPROVED.value,
                    OutreachStatus.QUEUED.value,
                    OutreachStatus.FAILED.value,
                }:
                    transition_outreach(
                        session,
                        application,
                        OutreachStatus.MANUAL_REQUIRED,
                        f"telegram:{user_id}",
                    )
                return [
                    TelegramReply(
                        chat_id, "Marked for manual action; no external message was sent."
                    )
                ]
            if command == "/submitted":
                if application.status not in {
                    ApplicationStatus.APPROVED.value,
                    ApplicationStatus.MANUAL_REQUIRED.value,
                }:
                    return [
                        TelegramReply(
                            chat_id, "The application is not ready to record as submitted."
                        )
                    ]
                transition_application(
                    session, application, ApplicationStatus.SUBMITTED, f"telegram:{user_id}"
                )
                return [
                    TelegramReply(
                        chat_id,
                        "Official application recorded as submitted; outreach remains separate.",
                    )
                ]
            job = application.job
            if job.status not in {
                JobStatus.SKIPPED.value,
                JobStatus.SNOOZED.value,
                JobStatus.EXPIRED.value,
            }:
                return [
                    TelegramReply(
                        chat_id, "Only skipped, snoozed, or expired jobs can be reopened."
                    )
                ]
            transition_job(session, job, JobStatus.REOPENED, f"telegram:{user_id}")
            return [TelegramReply(chat_id, "Job reopened explicitly.")]
        return [TelegramReply(chat_id, HELP)]

    def _callback(
        self, session: Session, chat_id: int, user_id: int, data: str
    ) -> list[TelegramReply]:
        action, _, raw_id = data.partition(":")
        try:
            object_id = uuid.UUID(raw_id)
        except ValueError:
            return [TelegramReply(chat_id, "Invalid action identifier.")]
        if action in {"apply", "skip", "snooze", "why", "open"}:
            job = session.get(Job, object_id)
            if not job:
                return [TelegramReply(chat_id, "Job no longer exists.")]
            if action == "apply":
                application, created = create_application(session, job, f"telegram:{user_id}")
                record_search_feedback(session, job, "apply", application)
                if job.status in {JobStatus.DISCOVERED.value, JobStatus.REOPENED.value}:
                    transition_job(session, job, JobStatus.SHORTLISTED, f"telegram:{user_id}")
                if application.status == ApplicationStatus.SELECTED.value:
                    try:
                        inventory = load_inventory(self.settings.career_inventory_path)
                    except (FileNotFoundError, ValueError) as exc:
                        return [
                            TelegramReply(
                                chat_id,
                                f"Application {application.human_code} selected, but generation is "
                                f"blocked until the private career inventory is valid: {exc}",
                            )
                        ]
                    payload = build_generation_payload(
                        inventory,
                        {
                            "company": job.company.name if job.company else "",
                            "title": job.title,
                            "location": job.location,
                            "workplace_type": job.workplace_type,
                            "description_text": job.description_text,
                        },
                    )
                    queue_application_generation(
                        session,
                        application,
                        f"telegram:{user_id}",
                        payload,
                        notification_chat_id=chat_id,
                    )
                return [
                    TelegramReply(
                        chat_id,
                        f"Application {application.human_code} "
                        + ("queued." if created else "already exists."),
                    )
                ]
            if action == "skip" and job.status in {
                JobStatus.DISCOVERED.value,
                JobStatus.SHORTLISTED.value,
                JobStatus.REOPENED.value,
            }:
                transition_job(session, job, JobStatus.SKIPPED, f"telegram:{user_id}")
                record_search_feedback(session, job, "skip")
                return [
                    TelegramReply(
                        chat_id, "Skipped. It will not be recommended again unless reopened."
                    )
                ]
            if action == "snooze" and job.status in {
                JobStatus.DISCOVERED.value,
                JobStatus.SHORTLISTED.value,
                JobStatus.REOPENED.value,
            }:
                transition_job(session, job, JobStatus.SNOOZED, f"telegram:{user_id}")
                record_search_feedback(session, job, "snooze")
                job.snoozed_until = datetime.now(UTC) + timedelta(days=7)
                return [TelegramReply(chat_id, "Snoozed for seven days.")]
            if action == "why":
                score = session.scalar(
                    select(JobScore)
                    .where(JobScore.job_id == job.id)
                    .order_by(JobScore.created_at.desc())
                )
                return [
                    TelegramReply(
                        chat_id,
                        score.explanation if score else "No score explanation is available yet.",
                    )
                ]
            if action == "open":
                return [TelegramReply(chat_id, job.original_url)]
        callback_application = session.get(Application, object_id)
        if not callback_application:
            return [TelegramReply(chat_id, "Application no longer exists.")]
        application = callback_application
        if action == "cancel":
            return [TelegramReply(chat_id, "Cancelled; no external message was sent.")]
        if action == "verify-contact":
            conversation = session.scalar(
                select(TelegramConversation).where(
                    TelegramConversation.chat_id == chat_id,
                    TelegramConversation.user_id == user_id,
                    TelegramConversation.application_id == application.id,
                    TelegramConversation.state == "awaiting_contact_verification",
                )
            )
            if not conversation or not conversation.data.get("candidate_email"):
                return [
                    TelegramReply(
                        chat_id, "Contact verification session expired; use /contact CODE."
                    )
                ]
            address = str(conversation.data["candidate_email"])
            domain = address.rsplit("@", 1)[-1]
            company = application.job.company
            if company and company.domain and domain != company.domain.casefold():
                return [
                    TelegramReply(
                        chat_id, "Email domain does not match the company's verified domain."
                    )
                ]
            if company and not company.domain:
                company.domain = domain
            contact = Contact(
                company_id=company.id if company else None,
                name="User-verified named recruiter",
                role="Recruiter or job poster",
                email=address,
                source="telegram-user-verification",
                confidence="high",
                verification_status="verified",
                evidence=(
                    "User explicitly confirmed this company-domain recipient belongs to the "
                    "named recruiter/job poster."
                ),
                last_verified_at=datetime.now(UTC),
            )
            session.add(contact)
            session.flush()
            session.add(
                ApplicationContact(
                    application_id=application.id, contact_id=contact.id, selected=True
                )
            )
            application.approved_contact_id = contact.id
            if application.outreach_status == OutreachStatus.NO_CONTACT.value:
                transition_outreach(
                    session,
                    application,
                    OutreachStatus.CONTACT_CANDIDATE_FOUND,
                    f"telegram:{user_id}",
                )
                transition_outreach(
                    session, application, OutreachStatus.CONTACT_VERIFIED, f"telegram:{user_id}"
                )
                transition_outreach(
                    session, application, OutreachStatus.DRAFTED, f"telegram:{user_id}"
                )
            session.delete(conversation)
            return [
                TelegramReply(
                    chat_id,
                    f"Verified contact selected: {address}. Use /approve {application.human_code}.",
                )
            ]
        if action == "confirm":
            return [self._confirm_send(session, application, chat_id, user_id)]
        return [TelegramReply(chat_id, "Unknown action.")]

    def _continue_conversation(
        self, session: Session, conversation: TelegramConversation, message: dict[str, Any]
    ) -> TelegramReply | None:
        if conversation.state == "awaiting_job_metadata":
            text = str(message.get("text", "")).strip()
            parts = [part.strip() for part in text.split("|", 3)]
            if len(parts) != 4 or not all(parts):
                return TelegramReply(
                    conversation.chat_id,
                    "Use exactly: Company | Title | Location | Plain-text description",
                )
            try:
                job_id = uuid.UUID(str(conversation.data["job_id"]))
            except (KeyError, ValueError):
                session.delete(conversation)
                return TelegramReply(conversation.chat_id, "Manual job entry session is invalid.")
            job = session.get(Job, job_id)
            if not job:
                session.delete(conversation)
                return TelegramReply(conversation.chat_id, "Job no longer exists.")
            company_key = normalize_company_name(parts[0])
            company = session.scalar(select(Company).where(Company.normalized_name == company_key))
            if not company:
                company = Company(name=parts[0], normalized_name=company_key)
                session.add(company)
                session.flush()
            job.company = company
            job.title = parts[1]
            job.location = parts[2]
            job.description_text = parts[3][:200_000]
            job.description_hash = description_hash(job.description_text)
            job.raw_metadata = {**job.raw_metadata, "manual_completion": True}
            session.delete(conversation)
            return TelegramReply(
                conversation.chat_id,
                f"Completed: {company.name} — {job.title}",
                (
                    ("Apply", f"apply:{job.id}"),
                    ("Skip", f"skip:{job.id}"),
                    ("Open job", f"open:{job.id}"),
                ),
            )
        application = session.get(Application, conversation.application_id)
        if not application:
            session.delete(conversation)
            return TelegramReply(conversation.chat_id, "Application no longer exists.")
        if conversation.state == "awaiting_final_cv":
            document = message.get("document")
            raw = message.get("_file_bytes")
            if not document or not isinstance(raw, bytes):
                return TelegramReply(
                    conversation.chat_id, "Please upload the CV as a Telegram document."
                )
            name = validate_upload(
                str(document.get("file_name", "")),
                str(document.get("mime_type", "")),
                len(raw),
                self.settings.max_upload_bytes,
                raw,
            )
            maximum_version = session.scalar(
                select(func.coalesce(func.max(Artifact.version), 0)).where(
                    Artifact.application_id == application.id, Artifact.kind == "final_cv"
                )
            )
            version = int(maximum_version or 0) + 1
            key = f"{application.human_code}/final-cv-v{version}{Path(name).suffix.lower()}"
            stored = self.artifact_storage.put(key, raw, str(document["mime_type"]))
            session.add(
                Artifact(
                    application_id=application.id,
                    kind="final_cv",
                    version=version,
                    storage_key=stored.key,
                    sha256=stored.sha256,
                    size_bytes=stored.size_bytes,
                    mime_type=stored.mime_type,
                    user_edited=True,
                )
            )
            conversation.state = "awaiting_final_message"
            return TelegramReply(
                conversation.chat_id, "CV stored. Now paste the exact final recruiter message."
            )
        if conversation.state == "awaiting_final_message":
            text = str(message.get("text", "")).strip()
            if not text:
                return TelegramReply(conversation.chat_id, "Paste a non-empty recruiter message.")
            application.final_message = text
            if application.status == ApplicationStatus.REVIEW_READY.value:
                transition_application(
                    session,
                    application,
                    ApplicationStatus.FINAL_MATERIAL_RECEIVED,
                    f"telegram:{conversation.user_id}",
                )
            session.delete(conversation)
            return TelegramReply(
                conversation.chat_id,
                "Final material received. Use /contact CODE, then /approve CODE. "
                "Nothing has been sent.",
            )
        if conversation.state == "awaiting_contact":
            address = str(message.get("text", "")).strip().casefold()
            if "@" not in address or any(character.isspace() for character in address):
                return TelegramReply(
                    conversation.chat_id, "Enter one syntactically valid company email."
                )
            conversation.data = {"candidate_email": address}
            conversation.state = "awaiting_contact_verification"
            return TelegramReply(
                conversation.chat_id,
                f"You entered {address}. Confirm only if you verified it belongs to the "
                "named recruiter/job poster.",
                (
                    ("Verified Contact", f"verify-contact:{application.id}"),
                    ("Cancel", f"cancel:{application.id}"),
                ),
            )
        return None

    def _confirm_send(
        self, session: Session, application: Any, chat_id: int, user_id: int
    ) -> TelegramReply:
        if application.status != ApplicationStatus.FINAL_MATERIAL_RECEIVED.value:
            return TelegramReply(chat_id, "Final CV and message are not both ready.")
        if not application.approved_contact_id:
            return TelegramReply(
                chat_id, "No verified contact is selected; use /contact CODE or /manual CODE."
            )
        contact = session.get(Contact, application.approved_contact_id)
        already_sent = (
            session.scalar(
                select(DeliveryAttempt.id).where(
                    DeliveryAttempt.application_id == application.id,
                    DeliveryAttempt.contact_id == application.approved_contact_id,
                    DeliveryAttempt.status.in_(["sending", "sent", "delivered"]),
                )
            )
            is not None
        )
        allowed, reason = automatic_email_allowed(
            ContactPolicyInput(
                email=contact.email if contact else None,
                company_domain=application.job.company.domain if application.job.company else None,
                confidence=contact.confidence if contact else "low",
                verified=bool(contact and contact.verification_status == "verified"),
                user_approved=True,
                already_sent=already_sent,
            )
        )
        if not allowed:
            return TelegramReply(
                chat_id,
                f"Automatic delivery blocked: {reason}. Use /manual {application.human_code}.",
            )
        transition_application(
            session, application, ApplicationStatus.APPROVED, f"telegram:{user_id}"
        )
        if application.outreach_status == OutreachStatus.DRAFTED.value:
            transition_outreach(
                session, application, OutreachStatus.APPROVED, f"telegram:{user_id}"
            )
            transition_outreach(session, application, OutreachStatus.QUEUED, f"telegram:{user_id}")
        put_outbox(
            session,
            "email",
            "recruiter_outreach",
            str(application.approved_contact_id),
            {"application_id": str(application.id)},
            f"outreach:{application.id}:{application.approved_contact_id}:v1",
        )
        return TelegramReply(chat_id, "Approved and queued exactly once.")

    @staticmethod
    def _status(application: Any) -> str:
        return (
            f"{application.human_code}: application={application.status}; "
            f"outreach={application.outreach_status}; job={application.job.status}"
        )

    @staticmethod
    def _set_conversation(
        session: Session, chat_id: int, user_id: int, state: str, application_id: uuid.UUID
    ) -> None:
        conversation = session.scalar(
            select(TelegramConversation).where(
                TelegramConversation.chat_id == chat_id, TelegramConversation.user_id == user_id
            )
        )
        if not conversation:
            conversation = TelegramConversation(chat_id=chat_id, user_id=user_id, state=state)
            session.add(conversation)
        conversation.state = state
        conversation.application_id = application_id
        conversation.data = {}
        conversation.expires_at = datetime.now(UTC) + timedelta(hours=24)

    @staticmethod
    def _set_job_conversation(
        session: Session, chat_id: int, user_id: int, job_id: uuid.UUID
    ) -> None:
        conversation = session.scalar(
            select(TelegramConversation).where(
                TelegramConversation.chat_id == chat_id,
                TelegramConversation.user_id == user_id,
            )
        )
        if not conversation:
            conversation = TelegramConversation(
                chat_id=chat_id, user_id=user_id, state="awaiting_job_metadata"
            )
            session.add(conversation)
        conversation.state = "awaiting_job_metadata"
        conversation.application_id = None
        conversation.data = {"job_id": str(job_id)}
        conversation.expires_at = datetime.now(UTC) + timedelta(hours=24)


class TelegramHttpProvider:
    def __init__(self, token: str, timeout: float = 35) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return list(response.json()["result"])

    def download_document(self, file_id: str, maximum: int) -> bytes:
        metadata = httpx.get(
            f"{self.base}/getFile", params={"file_id": file_id}, timeout=self.timeout
        ).json()["result"]
        token = self.base.rsplit("bot", 1)[-1]
        response = httpx.get(
            f"https://api.telegram.org/file/bot{token}/{metadata['file_path']}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        if len(response.content) > maximum:
            raise ValueError("Telegram document exceeds maximum size")
        return response.content

    def send_reply(self, reply: TelegramReply) -> str:
        body: dict[str, Any] = {
            "chat_id": reply.chat_id,
            "text": reply.text,
            "disable_web_page_preview": True,
        }
        if reply.buttons:
            body["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data}] for label, data in reply.buttons
                ]
            }
        response = httpx.post(f"{self.base}/sendMessage", json=body, timeout=self.timeout)
        response.raise_for_status()
        return str(response.json().get("result", {}).get("message_id", ""))

    def send(self, notification: Notification) -> str:
        return self.send_reply(
            TelegramReply(int(notification.recipient), notification.text, notification.buttons)
        )


def run_long_polling(
    factory: sessionmaker[Session], handler: TelegramUpdateHandler, provider: TelegramHttpProvider
) -> None:
    offset: int | None = None
    while True:
        try:
            updates = provider.get_updates(offset)
            for update in updates:
                offset = int(update["update_id"]) + 1
                document = (update.get("message") or {}).get("document")
                if document:
                    update["message"]["_file_bytes"] = provider.download_document(
                        str(document["file_id"]), handler.settings.max_upload_bytes
                    )
                with factory.begin() as session:
                    replies = handler.process(session, update)
                    for index, reply in enumerate(replies):
                        put_outbox(
                            session,
                            "telegram",
                            "telegram_reply",
                            str(reply.chat_id),
                            {"text": reply.text, "buttons": list(reply.buttons)},
                            f"telegram-reply:{update['update_id']}:{index}",
                        )
        except (httpx.HTTPError, IntegrityError, ValueError):
            time.sleep(5)
