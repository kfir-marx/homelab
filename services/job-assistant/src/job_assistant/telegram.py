from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .artifacts import FilesystemArtifactStorage, user_storage_key
from .career import load_inventory
from .config import Settings
from .contact_policy import ContactPolicyInput, automatic_email_allowed
from .domain import (
    create_application,
    get_application_by_code,
    get_user_job_state,
    ingest_job,
    normalize_company_name,
    queue_application_generation,
    record_search_feedback,
    transition_application,
    transition_job,
    transition_outreach,
)
from .generation import build_generation_payload
from .interfaces import NormalizedJob
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
    User,
)
from .normalization import canonicalize_url, description_hash
from .queue import put_outbox
from .security import validate_public_http_url, validate_upload
from .sources.manual import fetch_public_job
from .states import ApplicationStatus, JobStatus, OutreachStatus

HELP = """Commands:
/job_add <public-job-url> — add a job
/job_status <code> — show lifecycle state
/job_contact <code> — start contact entry
/job_final <code> — upload final CV, then paste final message
/job_approve <code> — review the selected delivery target
/job_manual <code> — mark manual outreach required
/job_submitted <code> — record the official application submission
/job_reopen <code> — explicitly reopen a skipped/expired job
/job_help — show this guide"""


@dataclass(frozen=True)
class TelegramReply:
    chat_id: int
    text: str
    buttons: tuple[tuple[str, str], ...] = ()


class TelegramUpdateHandler:
    def __init__(
        self,
        settings: Settings,
        artifact_storage: FilesystemArtifactStorage,
        *,
        trusted_gateway: bool = False,
    ) -> None:
        self.settings = settings
        self.artifact_storage = artifact_storage
        self.trusted_gateway = trusted_gateway

    def process(self, session: Session, update: dict[str, Any]) -> list[TelegramReply]:
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
        telegram_user_id, chat_id = int(actor["id"]), int(chat["id"])
        if chat.get("type") not in {None, "private"} or telegram_user_id != chat_id:
            return []
        if message and any(
            key in message
            for key in ("forward_origin", "forward_from", "sender_chat", "author_signature")
        ):
            return []
        user = session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id, User.active.is_(True))
        )
        if user is None:
            return []
        update_id = int(update["update_id"])
        payload_hash = hashlib.sha256(
            json.dumps(update, sort_keys=True, default=str).encode()
        ).digest()
        if session.get(TelegramUpdate, update_id):
            return []
        session.add(TelegramUpdate(update_id=update_id, user_id=user.id, payload_hash=payload_hash))
        if callback:
            return self._callback(session, user, chat_id, str(callback.get("data", "")))
        assert message is not None
        conversation = session.scalar(
            select(TelegramConversation).where(TelegramConversation.user_id == user.id)
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
            return [TelegramReply(chat_id, "Use /job_help to see available commands.")]
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].casefold()
        if command.startswith("/job_"):
            command = "/" + command.removeprefix("/job_")
        if command == "/help":
            return [TelegramReply(chat_id, HELP)]
        if command == "/add":
            if not argument:
                return [TelegramReply(chat_id, "Usage: /job_add <public-job-url>")]
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
                self._set_job_conversation(session, user, chat_id, job.id)
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
                return [TelegramReply(chat_id, f"Usage: /job_{command[1:]} <application-code>")]
            application = get_application_by_code(session, user, argument.strip())
            if not application:
                return [TelegramReply(chat_id, "Unknown application code.")]
            if command == "/status":
                return [TelegramReply(chat_id, self._status(application))]
            if command == "/final":
                self._set_conversation(session, user, chat_id, "awaiting_final_cv", application.id)
                return [
                    TelegramReply(chat_id, "Upload the final CV as a PDF or DOCX (maximum 10 MB).")
                ]
            if command == "/contact":
                self._set_conversation(session, user, chat_id, "awaiting_contact", application.id)
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
                    ApplicationStatus.SELECTED.value,
                    ApplicationStatus.REVIEW_READY.value,
                    ApplicationStatus.FINAL_MATERIAL_RECEIVED.value,
                    ApplicationStatus.APPROVED.value,
                }:
                    transition_application(
                        session,
                        application,
                        ApplicationStatus.MANUAL_REQUIRED,
                        f"telegram:{user.telegram_user_id}",
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
                        f"telegram:{user.telegram_user_id}",
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
                    session,
                    application,
                    ApplicationStatus.SUBMITTED,
                    f"telegram:{user.telegram_user_id}",
                )
                return [
                    TelegramReply(
                        chat_id,
                        "Official application recorded as submitted; outreach remains separate.",
                    )
                ]
            job = application.job
            state = get_user_job_state(session, user, job)
            if state.status not in {
                JobStatus.SKIPPED.value,
                JobStatus.SNOOZED.value,
                JobStatus.EXPIRED.value,
            }:
                return [
                    TelegramReply(
                        chat_id, "Only skipped, snoozed, or expired jobs can be reopened."
                    )
                ]
            transition_job(
                session,
                user,
                job,
                JobStatus.REOPENED,
                f"telegram:{user.telegram_user_id}",
            )
            return [TelegramReply(chat_id, "Job reopened explicitly.")]
        return [TelegramReply(chat_id, HELP)]

    def _callback(
        self, session: Session, user: User, chat_id: int, data: str
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
                application, created = create_application(
                    session, user, job, f"telegram:{user.telegram_user_id}"
                )
                record_search_feedback(session, user, job, "apply", application)
                state = get_user_job_state(session, user, job)
                if state.status in {JobStatus.DISCOVERED.value, JobStatus.REOPENED.value}:
                    transition_job(
                        session,
                        user,
                        job,
                        JobStatus.SHORTLISTED,
                        f"telegram:{user.telegram_user_id}",
                    )
                if application.status == ApplicationStatus.SELECTED.value:
                    if not user.generation_enabled:
                        return [
                            TelegramReply(
                                chat_id,
                                f"Application {application.human_code} selected. Generation is "
                                "disabled for this account; manual processing remains available.",
                            )
                        ]
                    try:
                        inventory = load_inventory(
                            self.settings.artifact_root / user.career_inventory_key
                        )
                    except (FileNotFoundError, ValueError) as exc:
                        user.inventory_valid = False
                        return [
                            TelegramReply(
                                chat_id,
                                f"Application {application.human_code} selected, but generation is "
                                "blocked until this account's private career inventory is valid "
                                f"({type(exc).__name__}).",
                            )
                        ]
                    user.inventory_valid = True
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
                        f"telegram:{user.telegram_user_id}",
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
            state = get_user_job_state(session, user, job)
            if action == "skip" and state.status in {
                JobStatus.DISCOVERED.value,
                JobStatus.SHORTLISTED.value,
                JobStatus.REOPENED.value,
            }:
                transition_job(
                    session, user, job, JobStatus.SKIPPED, f"telegram:{user.telegram_user_id}"
                )
                record_search_feedback(session, user, job, "skip")
                return [
                    TelegramReply(
                        chat_id, "Skipped. It will not be recommended again unless reopened."
                    )
                ]
            if action == "snooze" and state.status in {
                JobStatus.DISCOVERED.value,
                JobStatus.SHORTLISTED.value,
                JobStatus.REOPENED.value,
            }:
                transition_job(
                    session, user, job, JobStatus.SNOOZED, f"telegram:{user.telegram_user_id}"
                )
                record_search_feedback(session, user, job, "snooze")
                state.snoozed_until = datetime.now(UTC) + timedelta(days=7)
                return [TelegramReply(chat_id, "Snoozed for seven days.")]
            if action == "why":
                score = session.scalar(
                    select(JobScore)
                    .where(JobScore.job_id == job.id, JobScore.user_id == user.id)
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
        callback_application = session.scalar(
            select(Application).where(Application.id == object_id, Application.user_id == user.id)
        )
        if not callback_application:
            return [TelegramReply(chat_id, "Application no longer exists.")]
        application = callback_application
        if action == "cancel":
            return [TelegramReply(chat_id, "Cancelled; no external message was sent.")]
        if action == "verify-contact":
            conversation = session.scalar(
                select(TelegramConversation).where(
                    TelegramConversation.user_id == user.id,
                    TelegramConversation.application_id == application.id,
                    TelegramConversation.state == "awaiting_contact_verification",
                )
            )
            if not conversation or not conversation.data.get("candidate_email"):
                return [
                    TelegramReply(
                        chat_id, "Contact verification session expired; use /job_contact CODE."
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
            contact = Contact(
                user_id=user.id,
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
                    user_id=user.id,
                    application_id=application.id,
                    contact_id=contact.id,
                    selected=True,
                )
            )
            application.approved_contact_id = contact.id
            if application.outreach_status == OutreachStatus.NO_CONTACT.value:
                transition_outreach(
                    session,
                    application,
                    OutreachStatus.CONTACT_CANDIDATE_FOUND,
                    f"telegram:{user.telegram_user_id}",
                )
                transition_outreach(
                    session,
                    application,
                    OutreachStatus.CONTACT_VERIFIED,
                    f"telegram:{user.telegram_user_id}",
                )
                transition_outreach(
                    session,
                    application,
                    OutreachStatus.DRAFTED,
                    f"telegram:{user.telegram_user_id}",
                )
            session.delete(conversation)
            return [
                TelegramReply(
                    chat_id,
                    f"Verified contact selected: {address}. Use /job_approve "
                    f"{application.human_code}.",
                )
            ]
        if action == "confirm":
            return [self._confirm_send(session, user, application, chat_id)]
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
        application = session.scalar(
            select(Application).where(
                Application.id == conversation.application_id,
                Application.user_id == conversation.user_id,
            )
        )
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
            key = user_storage_key(
                application.user.storage_prefix,
                "applications",
                str(application.id),
                f"final-cv-v{version}{Path(name).suffix.lower()}",
            )
            stored = self.artifact_storage.put(key, raw, str(document["mime_type"]))
            session.add(
                Artifact(
                    user_id=application.user_id,
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
                    f"telegram:{conversation.telegram_user_id}",
                )
            session.delete(conversation)
            return TelegramReply(
                conversation.chat_id,
                "Final material received. Use /job_contact CODE, then /job_approve CODE. "
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
        self, session: Session, user: User, application: Any, chat_id: int
    ) -> TelegramReply:
        if not user.automated_delivery_enabled:
            return TelegramReply(
                chat_id,
                f"Automatic delivery is disabled for this account. Use /job_manual "
                f"{application.human_code}.",
            )
        if application.status != ApplicationStatus.FINAL_MATERIAL_RECEIVED.value:
            return TelegramReply(chat_id, "Final CV and message are not both ready.")
        if not application.approved_contact_id:
            return TelegramReply(
                chat_id,
                "No verified contact is selected; use /job_contact CODE or /job_manual CODE.",
            )
        contact = session.scalar(
            select(Contact).where(
                Contact.id == application.approved_contact_id, Contact.user_id == user.id
            )
        )
        already_sent = (
            session.scalar(
                select(DeliveryAttempt.id).where(
                    DeliveryAttempt.application_id == application.id,
                    DeliveryAttempt.user_id == user.id,
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
                f"Automatic delivery blocked: {reason}. Use /job_manual {application.human_code}.",
            )
        transition_application(
            session,
            application,
            ApplicationStatus.APPROVED,
            f"telegram:{user.telegram_user_id}",
        )
        if application.outreach_status == OutreachStatus.DRAFTED.value:
            transition_outreach(
                session,
                application,
                OutreachStatus.APPROVED,
                f"telegram:{user.telegram_user_id}",
            )
            transition_outreach(
                session,
                application,
                OutreachStatus.QUEUED,
                f"telegram:{user.telegram_user_id}",
            )
        put_outbox(
            session,
            "email",
            "recruiter_outreach",
            str(application.approved_contact_id),
            {"application_id": str(application.id)},
            f"outreach:{application.id}:{application.approved_contact_id}:v1",
            user_id=user.id,
        )
        return TelegramReply(chat_id, "Approved and queued exactly once.")

    @staticmethod
    def _status(application: Any) -> str:
        return (
            f"{application.human_code}: application={application.status}; "
            f"outreach={application.outreach_status}"
        )

    @staticmethod
    def _set_conversation(
        session: Session,
        user: User,
        chat_id: int,
        state: str,
        application_id: uuid.UUID,
    ) -> None:
        conversation = session.scalar(
            select(TelegramConversation).where(TelegramConversation.user_id == user.id)
        )
        if not conversation:
            conversation = TelegramConversation(
                chat_id=chat_id,
                telegram_user_id=user.telegram_user_id,
                user_id=user.id,
                state=state,
            )
            session.add(conversation)
        conversation.state = state
        conversation.application_id = application_id
        conversation.data = {}
        conversation.expires_at = datetime.now(UTC) + timedelta(hours=24)

    @staticmethod
    def _set_job_conversation(
        session: Session, user: User, chat_id: int, job_id: uuid.UUID
    ) -> None:
        conversation = session.scalar(
            select(TelegramConversation).where(TelegramConversation.user_id == user.id)
        )
        if not conversation:
            conversation = TelegramConversation(
                chat_id=chat_id,
                telegram_user_id=user.telegram_user_id,
                user_id=user.id,
                state="awaiting_job_metadata",
            )
            session.add(conversation)
        conversation.state = "awaiting_job_metadata"
        conversation.application_id = None
        conversation.data = {"job_id": str(job_id)}
        conversation.expires_at = datetime.now(UTC) + timedelta(hours=24)
