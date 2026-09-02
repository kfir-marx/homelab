from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
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
    DiscoverySummary,
    Job,
    JobScore,
    TelegramConversation,
    TelegramUpdate,
    User,
    UserJobState,
    UserSearchProfile,
)
from .normalization import canonicalize_url, description_hash
from .profiles import DEFAULT_NOTIFICATION_PREFERENCES, load_user_criteria
from .queue import put_outbox
from .ranking import SearchCriteria, load_criteria
from .security import validate_public_http_url, validate_upload
from .sources.manual import fetch_public_job
from .states import ApplicationStatus, JobStatus, OutreachStatus

HELP = """Guided commands:
/job_setup — configure or review your search profile
/job_today — browse current recommendations
/job_applications — application dashboard

Fallback commands:
/job_add <public-job-url> — add a job
/job_status <code> — show lifecycle state
/job_contact <code> — start contact entry
/job_final <code> — upload final CV, then paste final message
/job_approve <code> — review the selected delivery target
/job_manual <code> — mark manual outreach required
/job_submitted <code> — record the official application submission
/job_reopen <code> — explicitly reopen a skipped/expired job
/job_help — show this guide"""

SETUP_FIELDS = (
    "desired_titles",
    "excluded_titles",
    "seniority",
    "israel_locations",
    "fully_remote",
    "required_technologies",
    "preferred_technologies",
    "preferred_companies",
    "excluded_companies",
    "minimum_match_threshold",
    "maximum_job_age_days",
    "salary",
    "notifications",
    "reminders",
)

SETUP_PROMPTS = {
    "desired_titles": "Desired job titles (comma-separated):",
    "excluded_titles": "Excluded job titles (comma-separated, or none):",
    "seniority": (
        "Preferred seniority (junior, mid, senior, staff, lead, principal, manager, director):"
    ),
    "israel_locations": "Accepted Israel locations (comma-separated):",
    "fully_remote": "Accept fully remote roles outside Israel? (yes/no):",
    "required_technologies": "Required technologies (comma-separated, or none):",
    "preferred_technologies": "Preferred technologies (comma-separated, or none):",
    "preferred_companies": "Preferred companies (comma-separated, or none):",
    "excluded_companies": "Excluded companies (comma-separated, or none):",
    "minimum_match_threshold": "Minimum match threshold (0-100 percent):",
    "maximum_job_age_days": "Maximum job age in days (1-365):",
    "salary": "Optional minimum salary as AMOUNT CURRENCY (for example 30000 ILS), or none:",
    "notifications": "Send proactive recommendation notifications? (yes/no):",
    "reminders": "Enable Telegram reminders? (yes/no):",
}


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
        expired_setup = False
        if conversation and self._expired(conversation.expires_at):
            expired_setup = conversation.state.startswith("setup:")
            session.delete(conversation)
            conversation = None
        if conversation:
            reply = self._continue_conversation(session, conversation, message)
            if reply:
                return [reply]
        text = str(message.get("text", "")).strip()
        if not text.startswith("/"):
            if expired_setup:
                return [
                    TelegramReply(
                        chat_id,
                        "That setup session expired. Run /job_setup to restart; nothing was saved.",
                    )
                ]
            return [TelegramReply(chat_id, "Use /job_help to see available commands.")]
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].casefold()
        if command.startswith("/job_"):
            command = "/" + command.removeprefix("/job_")
        if command == "/help":
            return [TelegramReply(chat_id, HELP)]
        if command == "/setup":
            return [self._start_setup(session, user, chat_id)]
        if command == "/today":
            return [self._today(session, user, chat_id)]
        if command == "/applications":
            return self._applications(session, user, chat_id)
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
                        "Enter: Recruiter name | verified company email. Do not guess an address.",
                    )
                ]
            if command == "/approve":
                return [self._approval_summary(session, user, application, chat_id)]
            if command == "/manual":
                return [self._mark_manual(session, user, application, chat_id)]
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
                application.submitted_at = datetime.now(UTC)
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
        if action.startswith("setup-"):
            return [self._setup_callback(session, user, chat_id, action.removeprefix("setup-"))]
        try:
            object_id = uuid.UUID(raw_id)
        except ValueError:
            return [TelegramReply(chat_id, "Invalid action identifier.")]
        if action in {"apply", "skip", "snooze", "why", "open", "next"}:
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
            if action == "skip" and state.status == JobStatus.SKIPPED.value:
                return [TelegramReply(chat_id, "Already skipped.")]
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
            if action == "snooze" and state.status == JobStatus.SNOOZED.value:
                return [TelegramReply(chat_id, "Already snoozed.")]
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
            if action == "next":
                return [self._today(session, user, chat_id, after_job_id=job.id)]
        if action == "choose-contact":
            contact = session.scalar(
                select(Contact).where(
                    Contact.id == object_id,
                    Contact.user_id == user.id,
                    Contact.verification_status == "verified",
                )
            )
            conversation = session.scalar(
                select(TelegramConversation).where(
                    TelegramConversation.user_id == user.id,
                    TelegramConversation.state == "choosing_contact",
                )
            )
            if contact is None or conversation is None or conversation.application_id is None:
                return [TelegramReply(chat_id, "Contact choice is no longer available.")]
            chosen_application = session.scalar(
                select(Application).where(
                    Application.id == conversation.application_id,
                    Application.user_id == user.id,
                )
            )
            if (
                chosen_application is None
                or contact.company_id != chosen_application.job.company_id
            ):
                return [TelegramReply(chat_id, "Contact does not belong to this application.")]
            self._select_contact(session, user, chosen_application, contact)
            session.delete(conversation)
            return [self._approval_summary(session, user, chosen_application, chat_id)]
        callback_application = session.scalar(
            select(Application).where(Application.id == object_id, Application.user_id == user.id)
        )
        if not callback_application:
            return [TelegramReply(chat_id, "Application no longer exists.")]
        application = callback_application
        if action == "detail":
            return [self._application_detail(application, chat_id)]
        if action in {"submitted", "interview", "rejected", "offer", "withdrawn"}:
            target = ApplicationStatus(action)
            if target not in {
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.INTERVIEW,
                ApplicationStatus.REJECTED,
                ApplicationStatus.OFFER,
                ApplicationStatus.WITHDRAWN,
            }:
                return [TelegramReply(chat_id, "Unsupported outcome.")]
            if application.status == target.value:
                return [TelegramReply(chat_id, f"Already recorded as {target.value}.")]
            try:
                transition_application(
                    session, application, target, f"telegram:{user.telegram_user_id}"
                )
            except ValueError:
                return [TelegramReply(chat_id, "That outcome is not valid from the current state.")]
            now = datetime.now(UTC)
            if target == ApplicationStatus.SUBMITTED:
                application.submitted_at = now
            application.follow_up_at = None
            return [TelegramReply(chat_id, f"Application outcome recorded: {target.value}.")]
        if action == "follow-up":
            self._set_conversation(session, user, chat_id, "awaiting_follow_up", application.id)
            return [TelegramReply(chat_id, "Enter a follow-up date as YYYY-MM-DD.")]
        if action == "reminder-off":
            application.reminders_disabled = True
            application.follow_up_at = None
            return [TelegramReply(chat_id, "Reminders disabled for this application.")]
        if action == "remind-snooze":
            application.reminders_disabled = False
            application.follow_up_at = datetime.now(UTC) + timedelta(days=7)
            return [TelegramReply(chat_id, "Reminder snoozed for seven days.")]
        if action == "accept-draft":
            artifact = session.scalar(
                select(Artifact)
                .where(
                    Artifact.application_id == application.id,
                    Artifact.user_id == user.id,
                    Artifact.kind == "generated_cv_pdf",
                )
                .order_by(Artifact.version.desc())
            )
            if not artifact or application.status != ApplicationStatus.REVIEW_READY.value:
                return [TelegramReply(chat_id, "The generated CV is not ready to accept.")]
            application.final_cv_artifact_id = artifact.id
            application.cv_approved_at = datetime.now(UTC)
            return [
                TelegramReply(
                    chat_id,
                    "Generated CV accepted. Now review the recruiter message.",
                    (
                        ("Accept Message", f"accept-message:{application.id}"),
                        ("Edit Message", f"edit-message:{application.id}"),
                        ("Manual", f"manual:{application.id}"),
                    ),
                )
            ]
        if action == "upload-revision":
            self._set_conversation(session, user, chat_id, "awaiting_final_cv", application.id)
            return [TelegramReply(chat_id, "Upload your edited PDF or DOCX (maximum 10 MB).")]
        if action == "accept-message":
            if not application.draft_message:
                return [TelegramReply(chat_id, "No recruiter-message draft is available.")]
            if len(application.draft_message) > 2_500:
                return [
                    TelegramReply(
                        chat_id,
                        "The draft is too long for an exact Telegram approval summary. "
                        "Choose Edit Message.",
                    )
                ]
            application.final_message = application.draft_message
            application.message_approved_at = datetime.now(UTC)
            self._mark_material_ready(session, application, user)
            return [self._contact_prompt(session, user, application, chat_id)]
        if action == "edit-message":
            self._set_conversation(session, user, chat_id, "awaiting_final_message", application.id)
            return [TelegramReply(chat_id, "Paste the exact replacement recruiter message.")]
        if action == "add-contact":
            self._set_conversation(session, user, chat_id, "awaiting_contact", application.id)
            return [
                TelegramReply(
                    chat_id, "Enter the verified contact as: Recruiter name | company email"
                )
            ]
        if action == "final-review":
            return [self._approval_summary(session, user, application, chat_id)]
        if action == "manual":
            return [self._mark_manual(session, user, application, chat_id)]
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
            contact_name = str(conversation.data.get("candidate_name", "")).strip()
            if not contact_name:
                return [TelegramReply(chat_id, "Contact name is required; add the contact again.")]
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
                name=contact_name,
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
            return [self._approval_summary(session, user, application, chat_id)]
        if action == "confirm":
            return [self._confirm_send(session, user, application, chat_id)]
        return [TelegramReply(chat_id, "Unknown action.")]

    def _continue_conversation(
        self, session: Session, conversation: TelegramConversation, message: dict[str, Any]
    ) -> TelegramReply | None:
        if conversation.state.startswith("setup:"):
            user = session.get(User, conversation.user_id)
            if user is None:
                session.delete(conversation)
                return TelegramReply(conversation.chat_id, "Setup session is no longer available.")
            return self._continue_setup(session, user, conversation, message)
        if conversation.state == "today":
            return None
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
            final_artifact = Artifact(
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
            session.add(final_artifact)
            session.flush()
            application.final_cv_artifact_id = final_artifact.id
            application.cv_approved_at = datetime.now(UTC)
            if application.draft_message:
                conversation.state = "awaiting_message_choice"
                return TelegramReply(
                    conversation.chat_id,
                    "Revised CV stored. Review the recruiter message draft:\n\n"
                    + application.draft_message,
                    (
                        ("Accept Message", f"accept-message:{application.id}"),
                        ("Edit Message", f"edit-message:{application.id}"),
                    ),
                )
            conversation.state = "awaiting_final_message"
            return TelegramReply(
                conversation.chat_id, "CV stored. Now paste the exact final recruiter message."
            )
        if conversation.state == "awaiting_final_message":
            text = str(message.get("text", "")).strip()
            if not text:
                return TelegramReply(conversation.chat_id, "Paste a non-empty recruiter message.")
            if len(text) > 2_500:
                return TelegramReply(
                    conversation.chat_id, "Keep the recruiter message at 2,500 characters or fewer."
                )
            application.final_message = text
            application.message_approved_at = datetime.now(UTC)
            self._mark_material_ready(session, application, application.user)
            session.delete(conversation)
            return self._contact_prompt(
                session, application.user, application, conversation.chat_id
            )
        if conversation.state == "awaiting_contact":
            contact_parts = [part.strip() for part in str(message.get("text", "")).split("|", 1)]
            if len(contact_parts) != 2 or not contact_parts[0]:
                return TelegramReply(
                    conversation.chat_id,
                    "Use exactly: Recruiter name | company email. Do not guess an address.",
                )
            contact_name, address = contact_parts[0], contact_parts[1].casefold()
            if "@" not in address or any(character.isspace() for character in address):
                return TelegramReply(
                    conversation.chat_id, "Enter one syntactically valid company email."
                )
            conversation.data = {"candidate_email": address, "candidate_name": contact_name}
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
        if conversation.state == "awaiting_follow_up":
            raw_date = str(message.get("text", "")).strip()
            try:
                chosen = (
                    datetime.strptime(raw_date, "%Y-%m-%d")
                    .replace(hour=9, tzinfo=ZoneInfo(self.settings.timezone))
                    .astimezone(UTC)
                )
            except ValueError:
                return TelegramReply(conversation.chat_id, "Use a valid date as YYYY-MM-DD.")
            if chosen.date() <= datetime.now(UTC).date():
                return TelegramReply(conversation.chat_id, "Choose a future date.")
            application.follow_up_at = chosen
            application.reminders_disabled = False
            session.delete(conversation)
            return TelegramReply(conversation.chat_id, f"Follow-up reminder set for {raw_date}.")
        return None

    def _start_setup(self, session: Session, user: User, chat_id: int) -> TelegramReply:
        try:
            current, _ = load_user_criteria(
                session, user, self.settings.search_criteria_path, self.settings.artifact_root
            )
        except (FileNotFoundError, ValueError, ValidationError):
            current = SearchCriteria(desired_titles=["Platform Engineer"])
        profile = session.get(UserSearchProfile, user.id)
        draft = current.model_dump(mode="json")
        draft["_notifications"] = {
            **DEFAULT_NOTIFICATION_PREFERENCES,
            **(profile.notification_preferences if profile else {}),
        }
        conversation = session.scalar(
            select(TelegramConversation).where(TelegramConversation.user_id == user.id)
        )
        if conversation is None:
            conversation = TelegramConversation(
                chat_id=chat_id,
                telegram_user_id=user.telegram_user_id,
                user_id=user.id,
                state="setup:0",
            )
            session.add(conversation)
        conversation.chat_id = chat_id
        conversation.telegram_user_id = user.telegram_user_id
        conversation.state = "setup:0"
        conversation.application_id = None
        conversation.data = {"draft": draft}
        conversation.expires_at = datetime.now(UTC) + timedelta(hours=24)
        return self._setup_prompt(conversation)

    def _continue_setup(
        self,
        session: Session,
        user: User,
        conversation: TelegramConversation,
        message: dict[str, Any],
    ) -> TelegramReply:
        text = str(message.get("text", "")).strip()
        lowered = text.casefold()
        controls = {
            "back": "back",
            "/back": "back",
            "cancel": "cancel",
            "/cancel": "cancel",
            "keep": "keep",
            "keep current": "keep",
            "view": "view",
            "view current settings": "view",
            "reset": "reset",
            "reset to defaults": "reset",
            "/job_setup": "resume",
        }
        if lowered in controls:
            action = controls[lowered]
            if action == "resume":
                return self._setup_prompt(conversation, prefix="Resuming setup. ")
            return self._setup_callback(session, user, conversation.chat_id, action)
        if conversation.state == "setup:confirm":
            return TelegramReply(
                conversation.chat_id,
                "Use Confirm Save, Back, View, Reset, or Cancel. Nothing is saved yet.",
                self._setup_buttons(confirm=True),
            )
        try:
            index = int(conversation.state.partition(":")[2])
            field = SETUP_FIELDS[index]
            draft = dict(conversation.data.get("draft", {}))
            self._parse_setup_value(draft, field, text)
            criteria_data = {key: value for key, value in draft.items() if not key.startswith("_")}
            SearchCriteria.model_validate(criteria_data)
        except (IndexError, ValueError, ValidationError) as exc:
            detail = str(exc).splitlines()[0]
            return TelegramReply(conversation.chat_id, f"Invalid value: {detail}")
        conversation.data = {**conversation.data, "draft": draft}
        conversation.state = (
            "setup:confirm" if index + 1 == len(SETUP_FIELDS) else f"setup:{index + 1}"
        )
        conversation.expires_at = datetime.now(UTC) + timedelta(hours=24)
        return self._setup_prompt(conversation)

    def _setup_callback(
        self, session: Session, user: User, chat_id: int, action: str
    ) -> TelegramReply:
        conversation = session.scalar(
            select(TelegramConversation).where(
                TelegramConversation.user_id == user.id,
                TelegramConversation.chat_id == chat_id,
                TelegramConversation.state.like("setup:%"),
            )
        )
        if conversation is None or self._expired(conversation.expires_at):
            if conversation is not None:
                session.delete(conversation)
            return TelegramReply(chat_id, "Setup expired. Run /job_setup to restart.")
        if action == "cancel":
            session.delete(conversation)
            return TelegramReply(chat_id, "Setup cancelled; existing settings were not changed.")
        if action == "view":
            return TelegramReply(
                chat_id,
                "Current setup draft (not saved):\n" + self._criteria_summary(conversation.data),
                self._setup_buttons(confirm=conversation.state == "setup:confirm"),
            )
        if action == "reset":
            try:
                defaults, _ = load_criteria(self.settings.search_criteria_path)
            except (FileNotFoundError, ValueError, ValidationError):
                defaults = SearchCriteria(desired_titles=["Platform Engineer"])
            draft = defaults.model_dump(mode="json")
            draft["_notifications"] = dict(DEFAULT_NOTIFICATION_PREFERENCES)
            conversation.data = {"draft": draft}
            conversation.state = "setup:0"
            return self._setup_prompt(conversation, prefix="Draft reset to defaults. ")
        if action == "back":
            current = (
                len(SETUP_FIELDS)
                if conversation.state == "setup:confirm"
                else int(conversation.state.partition(":")[2])
            )
            conversation.state = f"setup:{max(0, current - 1)}"
            return self._setup_prompt(conversation)
        if action == "keep":
            current = int(conversation.state.partition(":")[2])
            conversation.state = (
                "setup:confirm" if current + 1 == len(SETUP_FIELDS) else f"setup:{current + 1}"
            )
            return self._setup_prompt(conversation)
        if action == "confirm" and conversation.state == "setup:confirm":
            draft = dict(conversation.data.get("draft", {}))
            notifications = dict(draft.pop("_notifications", {}))
            try:
                criteria = SearchCriteria.model_validate(draft)
            except ValidationError:
                return TelegramReply(chat_id, "The draft is invalid. Go back and correct it.")
            profile = session.get(UserSearchProfile, user.id)
            if profile is None:
                profile = UserSearchProfile(
                    user_id=user.id,
                    criteria=criteria.model_dump(mode="json"),
                    notification_preferences=notifications,
                )
                session.add(profile)
            else:
                profile.criteria = criteria.model_dump(mode="json")
                profile.notification_preferences = notifications
                profile.version += 1
                profile.confirmed_at = datetime.now(UTC)
            session.delete(conversation)
            return TelegramReply(chat_id, "Search profile saved for your account.")
        return TelegramReply(chat_id, "That setup action is not available here.")

    @staticmethod
    def _parse_setup_value(draft: dict[str, Any], field: str, text: str) -> None:
        if field in {
            "desired_titles",
            "excluded_titles",
            "seniority",
            "israel_locations",
            "required_technologies",
            "preferred_technologies",
            "preferred_companies",
            "excluded_companies",
        }:
            draft[field] = (
                []
                if text.casefold() == "none"
                else [part.strip() for part in text.split(",") if part.strip()]
            )
        elif field == "fully_remote":
            if text.casefold() not in {"yes", "no"}:
                raise ValueError("enter yes or no")
            draft[field] = text.casefold() == "yes"
        elif field == "minimum_match_threshold":
            value = float(text.removesuffix("%").strip())
            draft[field] = value / 100 if value > 1 else value
        elif field == "maximum_job_age_days":
            value = int(text)
            if not 1 <= value <= 365:
                raise ValueError("job age must be between 1 and 365 days")
            draft[field] = value
        elif field == "salary":
            if text.casefold() == "none":
                draft["minimum_salary"] = None
                draft["salary_currency"] = None
            else:
                amount, currency = text.split()
                draft["minimum_salary"] = int(amount)
                draft["salary_currency"] = currency.upper()
        elif field in {"notifications", "reminders"}:
            if text.casefold() not in {"yes", "no"}:
                raise ValueError("enter yes or no")
            notifications = dict(draft.get("_notifications", {}))
            key = "recommendations" if field == "notifications" else "reminders"
            notifications[key] = text.casefold() == "yes"
            draft["_notifications"] = notifications

    def _setup_prompt(self, conversation: TelegramConversation, prefix: str = "") -> TelegramReply:
        if conversation.state == "setup:confirm":
            return TelegramReply(
                conversation.chat_id,
                prefix
                + "Review the settings below. They will only be saved after confirmation.\n"
                + self._criteria_summary(conversation.data),
                self._setup_buttons(confirm=True),
            )
        index = int(conversation.state.partition(":")[2])
        field = SETUP_FIELDS[index]
        current = self._setup_current_value(dict(conversation.data.get("draft", {})), field)
        return TelegramReply(
            conversation.chat_id,
            f"{prefix}Step {index + 1}/{len(SETUP_FIELDS)} — {SETUP_PROMPTS[field]}\n"
            f"Current: {current}",
            self._setup_buttons(confirm=False),
        )

    @staticmethod
    def _setup_buttons(confirm: bool) -> tuple[tuple[str, str], ...]:
        if confirm:
            return (
                ("Confirm Save", "setup-confirm"),
                ("Back", "setup-back"),
                ("View", "setup-view"),
                ("Reset", "setup-reset"),
                ("Cancel", "setup-cancel"),
            )
        return (
            ("Keep Current", "setup-keep"),
            ("Back", "setup-back"),
            ("View", "setup-view"),
            ("Reset", "setup-reset"),
            ("Cancel", "setup-cancel"),
        )

    @staticmethod
    def _setup_current_value(draft: dict[str, Any], field: str) -> str:
        if field == "salary":
            return (
                f"{draft.get('minimum_salary')} {draft.get('salary_currency')}"
                if draft.get("minimum_salary") is not None
                else "none"
            )
        if field in {"notifications", "reminders"}:
            key = "recommendations" if field == "notifications" else "reminders"
            return "yes" if dict(draft.get("_notifications", {})).get(key, True) else "no"
        value = draft.get(field)
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "none"
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value)

    @staticmethod
    def _expired(value: datetime | None) -> bool:
        if value is None:
            return False
        deadline = value if value.tzinfo else value.replace(tzinfo=UTC)
        return deadline < datetime.now(UTC)

    def _criteria_summary(self, data: dict[str, Any]) -> str:
        draft = dict(data.get("draft", {}))
        lines = [
            f"Desired titles: {self._setup_current_value(draft, 'desired_titles')}",
            f"Excluded titles: {self._setup_current_value(draft, 'excluded_titles')}",
            f"Seniority: {self._setup_current_value(draft, 'seniority')}",
            f"Locations: {self._setup_current_value(draft, 'israel_locations')}",
            f"Fully remote: {self._setup_current_value(draft, 'fully_remote')}",
            f"Required tech: {self._setup_current_value(draft, 'required_technologies')}",
            f"Preferred tech: {self._setup_current_value(draft, 'preferred_technologies')}",
            f"Preferred companies: {self._setup_current_value(draft, 'preferred_companies')}",
            f"Excluded companies: {self._setup_current_value(draft, 'excluded_companies')}",
            f"Threshold: {float(draft.get('minimum_match_threshold', 0)):.0%}",
            f"Maximum age: {draft.get('maximum_job_age_days')} days",
            f"Salary: {self._setup_current_value(draft, 'salary')}",
            f"Recommendation notifications: {self._setup_current_value(draft, 'notifications')}",
            f"Reminders: {self._setup_current_value(draft, 'reminders')}",
        ]
        return "\n".join(lines)

    def _today(
        self,
        session: Session,
        user: User,
        chat_id: int,
        after_job_id: uuid.UUID | None = None,
    ) -> TelegramReply:
        rows = session.execute(
            select(Job, JobScore)
            .join(JobScore, JobScore.job_id == Job.id)
            .join(
                UserJobState,
                (UserJobState.job_id == Job.id) & (UserJobState.user_id == user.id),
            )
            .where(
                JobScore.user_id == user.id,
                JobScore.passed_hard_filters.is_(True),
                UserJobState.status.in_([JobStatus.SHORTLISTED.value, JobStatus.REOPENED.value]),
            )
            .order_by(JobScore.score.desc(), JobScore.created_at.desc())
        ).all()
        applications = set(
            session.scalars(select(Application.job_id).where(Application.user_id == user.id)).all()
        )
        recommendations: list[tuple[Job, JobScore]] = []
        seen: set[uuid.UUID] = set()
        now = datetime.now(UTC)
        for job, score in rows:
            if job.id in seen or job.id in applications:
                continue
            state = session.scalar(
                select(UserJobState).where(
                    UserJobState.user_id == user.id, UserJobState.job_id == job.id
                )
            )
            if state and state.snoozed_until and state.snoozed_until > now:
                continue
            seen.add(job.id)
            recommendations.append((job, score))
        if not recommendations:
            return TelegramReply(chat_id, self._empty_today_reason(session, user))
        index = 0
        if after_job_id:
            for position, (candidate, _) in enumerate(recommendations):
                if candidate.id == after_job_id:
                    index = position + 1
                    break
            if index >= len(recommendations):
                self._set_today_cursor(session, user, chat_id, None, len(recommendations))
                return TelegramReply(chat_id, "You reached the end of today's recommendations.")
        job, score = recommendations[index]
        self._set_today_cursor(session, user, chat_id, job.id, index)
        company = job.company.name if job.company else "Unknown company"
        gaps = ", ".join(score.gaps) if score.gaps else "none identified"
        return TelegramReply(
            chat_id,
            f"{company} — {job.title}\n"
            f"{job.location or 'Location unspecified'} / "
            f"{job.workplace_type or 'workplace unspecified'}\n"
            f"Match {score.score:.0%}: {score.explanation}\n"
            f"Important gaps: {gaps}\n{job.original_url}",
            (
                ("Apply", f"apply:{job.id}"),
                ("Skip", f"skip:{job.id}"),
                ("Snooze", f"snooze:{job.id}"),
                ("Why", f"why:{job.id}"),
                ("Next", f"next:{job.id}"),
            ),
        )

    @staticmethod
    def _set_today_cursor(
        session: Session,
        user: User,
        chat_id: int,
        job_id: uuid.UUID | None,
        position: int,
    ) -> None:
        conversation = session.scalar(
            select(TelegramConversation).where(TelegramConversation.user_id == user.id)
        )
        if conversation is None:
            conversation = TelegramConversation(
                chat_id=chat_id,
                telegram_user_id=user.telegram_user_id,
                user_id=user.id,
                state="today",
            )
            session.add(conversation)
        conversation.chat_id = chat_id
        conversation.telegram_user_id = user.telegram_user_id
        conversation.state = "today"
        conversation.application_id = None
        conversation.data = {
            "job_id": str(job_id) if job_id else None,
            "position": position,
        }
        conversation.expires_at = datetime.now(UTC) + timedelta(days=1)

    @staticmethod
    def _empty_today_reason(session: Session, user: User) -> str:
        summary = session.get(DiscoverySummary, user.id)
        if summary and summary.outcome == "profile_incomplete":
            prefix = "No current recommendations. Your search profile is incomplete."
        elif not user.inventory_valid:
            prefix = "No current recommendations. Your career inventory is unavailable or invalid."
        elif summary is None and session.get(UserSearchProfile, user.id) is None:
            prefix = "No current recommendations. Run /job_setup to confirm your search profile."
        else:
            prefix = "No new matching jobs."
        messages = {
            "no_sources_configured": "No discovery source is configured.",
            "sources_in_cooldown": "Discovery sources are temporarily in cooldown.",
            "sources_failed": "Discovery sources failed; they will be retried later.",
            "sources_returned_no_jobs": "Configured sources returned no jobs.",
            "jobs_filtered_out": "Jobs were found, but none matched your profile.",
            "all_jobs_reviewed": "All matching jobs have already been reviewed.",
            "profile_incomplete": "Your search profile is incomplete.",
        }
        detail = messages.get(summary.outcome, "") if summary else ""
        return " ".join(part for part in (prefix, detail) if part)

    def _applications(self, session: Session, user: User, chat_id: int) -> list[TelegramReply]:
        applications = session.scalars(
            select(Application)
            .where(
                Application.user_id == user.id,
                Application.status.notin_(
                    [
                        ApplicationStatus.REJECTED.value,
                        ApplicationStatus.WITHDRAWN.value,
                    ]
                ),
            )
            .order_by(Application.updated_at.desc())
            .limit(10)
        ).all()
        if not applications:
            return [TelegramReply(chat_id, "No active applications yet. Use /job_today to begin.")]
        return [
            TelegramReply(
                chat_id,
                f"{application.job.company.name if application.job.company else 'Unknown company'}"
                f" — {application.job.title}\n"
                f"{self._status_label(application)} · Next: "
                f"{self._recommended_action(application)}",
                (("View", f"detail:{application.id}"),),
            )
            for application in applications
        ]

    def _application_detail(self, application: Application, chat_id: int) -> TelegramReply:
        buttons: list[tuple[str, str]] = []
        if application.status == ApplicationStatus.REVIEW_READY.value:
            if not application.cv_approved_at:
                buttons.extend(
                    [
                        ("Accept Draft", f"accept-draft:{application.id}"),
                        ("Upload Revision", f"upload-revision:{application.id}"),
                    ]
                )
            else:
                buttons.extend(
                    [
                        ("Accept Message", f"accept-message:{application.id}"),
                        ("Edit Message", f"edit-message:{application.id}"),
                    ]
                )
            buttons.append(("Manual", f"manual:{application.id}"))
        if application.status == ApplicationStatus.FINAL_MATERIAL_RECEIVED.value:
            if application.approved_contact_id:
                buttons.append(("Review Outreach", f"final-review:{application.id}"))
            else:
                buttons.append(("Add Contact", f"add-contact:{application.id}"))
            buttons.append(("Manual", f"manual:{application.id}"))
        if application.status in {
            ApplicationStatus.APPROVED.value,
            ApplicationStatus.MANUAL_REQUIRED.value,
        }:
            buttons.append(("Record Submitted", f"submitted:{application.id}"))
        if application.status == ApplicationStatus.SUBMITTED.value:
            buttons.extend(
                [
                    ("Interview", f"interview:{application.id}"),
                    ("Rejected", f"rejected:{application.id}"),
                    ("Offer", f"offer:{application.id}"),
                    ("Follow Up", f"follow-up:{application.id}"),
                ]
            )
        if application.status == ApplicationStatus.INTERVIEW.value:
            buttons.extend(
                [
                    ("Rejected", f"rejected:{application.id}"),
                    ("Offer", f"offer:{application.id}"),
                    ("Follow Up", f"follow-up:{application.id}"),
                ]
            )
        if application.status not in {
            ApplicationStatus.REJECTED.value,
            ApplicationStatus.WITHDRAWN.value,
        }:
            buttons.append(("Withdraw", f"withdrawn:{application.id}"))
            buttons.append(("Disable Reminders", f"reminder-off:{application.id}"))
        return TelegramReply(
            chat_id,
            f"{application.job.company.name if application.job.company else 'Unknown company'}"
            f" — {application.job.title}\nStatus: {self._status_label(application)}\n"
            f"Outreach: {application.outreach_status.replace('_', ' ')}\n"
            f"Recommended next action: {self._recommended_action(application)}\n"
            f"Original job: {application.job.original_url}",
            tuple(buttons),
        )

    @staticmethod
    def _status_label(application: Application) -> str:
        labels = {
            "selected": "Drafting",
            "generation_queued": "Drafting",
            "generating": "Drafting",
            "review_ready": "Awaiting review",
            "final_material_received": "Ready to submit",
            "approved": "Ready to submit",
            "submitted": "Submitted",
            "interview": "Interview",
            "rejected": "Rejected",
            "offer": "Offer",
            "withdrawn": "Withdrawn",
            "manual_required": "Manual action required",
            "failed": "Failed",
        }
        return labels.get(application.status, application.status.replace("_", " ").title())

    @staticmethod
    def _recommended_action(application: Application) -> str:
        actions = {
            "selected": "wait for drafting or choose manual action",
            "generation_queued": "wait for draft generation",
            "generating": "wait for draft generation",
            "review_ready": "review the generated CV",
            "final_material_received": "verify a contact and review outreach",
            "approved": "record official submission when completed",
            "submitted": "record an outcome or schedule follow-up",
            "interview": "record the next outcome",
            "offer": "review the offer or withdraw",
            "manual_required": "complete the action manually, then record submission",
            "failed": "retry generation with the fallback command or act manually",
        }
        return actions.get(application.status, "no action required")

    @staticmethod
    def _mark_material_ready(session: Session, application: Application, user: User) -> None:
        if (
            application.cv_approved_at
            and application.message_approved_at
            and application.status == ApplicationStatus.REVIEW_READY.value
        ):
            transition_application(
                session,
                application,
                ApplicationStatus.FINAL_MATERIAL_RECEIVED,
                f"telegram:{user.telegram_user_id}",
            )

    def _contact_prompt(
        self, session: Session, user: User, application: Application, chat_id: int
    ) -> TelegramReply:
        self._set_conversation(session, user, chat_id, "choosing_contact", application.id)
        existing = session.scalars(
            select(Contact)
            .where(
                Contact.user_id == user.id,
                Contact.company_id == application.job.company_id,
                Contact.verification_status == "verified",
            )
            .order_by(Contact.updated_at.desc())
            .limit(2)
        ).all()
        buttons = [
            (f"Use {contact.name}"[:64], f"choose-contact:{contact.id}") for contact in existing
        ]
        buttons.extend(
            [
                ("Add Contact", f"add-contact:{application.id}"),
                ("Manual", f"manual:{application.id}"),
            ]
        )
        return TelegramReply(
            chat_id,
            "CV and recruiter message are accepted. Add a real contact only after verifying "
            "their identity; no address will be inferred.",
            tuple(buttons),
        )

    @staticmethod
    def _select_contact(
        session: Session, user: User, application: Application, contact: Contact
    ) -> None:
        link = session.scalar(
            select(ApplicationContact).where(
                ApplicationContact.application_id == application.id,
                ApplicationContact.contact_id == contact.id,
                ApplicationContact.user_id == user.id,
            )
        )
        if link is None:
            session.add(
                ApplicationContact(
                    user_id=user.id,
                    application_id=application.id,
                    contact_id=contact.id,
                    selected=True,
                )
            )
        else:
            link.selected = True
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

    def _approval_summary(
        self, session: Session, user: User, application: Application, chat_id: int
    ) -> TelegramReply:
        contact = session.scalar(
            select(Contact).where(
                Contact.id == application.approved_contact_id,
                Contact.user_id == user.id,
                Contact.verification_status == "verified",
            )
        )
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.id == application.final_cv_artifact_id,
                Artifact.user_id == user.id,
                Artifact.application_id == application.id,
            )
        )
        if not contact or not contact.email or not artifact or not application.final_message:
            return TelegramReply(chat_id, "Final CV, message, and verified contact are required.")
        subject = application.final_subject or f"Regarding the open role — {application.human_code}"
        application.final_subject = subject
        extension = "pdf" if artifact.mime_type == "application/pdf" else "docx"
        origin = "edited" if artifact.user_edited else "generated"
        attachment = f"{origin} CV v{artifact.version}.{extension} (ID {str(artifact.id)[:8]})"
        return TelegramReply(
            chat_id,
            "FINAL OUTREACH APPROVAL\n"
            f"Recipient: {contact.name} <{contact.email}>\n"
            f"Subject: {subject}\n"
            f"Attachment: {attachment}\n"
            f"Message:\n{application.final_message}\n\n"
            "Confirm only if every detail is correct. This does not submit the official "
            "application.",
            (
                ("Confirm Outreach", f"confirm:{application.id}"),
                ("Manual", f"manual:{application.id}"),
                ("Cancel", f"cancel:{application.id}"),
            ),
        )

    @staticmethod
    def _mark_manual(
        session: Session, user: User, application: Application, chat_id: int
    ) -> TelegramReply:
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
            OutreachStatus.CONTACT_VERIFIED.value,
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
        return TelegramReply(chat_id, "Marked for manual action; no external message was sent.")

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
        if not application.cv_approved_at or not application.message_approved_at:
            return TelegramReply(chat_id, "The CV and message require explicit acceptance.")
        approved_artifact = session.scalar(
            select(Artifact.id).where(
                Artifact.id == application.final_cv_artifact_id,
                Artifact.application_id == application.id,
                Artifact.user_id == user.id,
                Artifact.mime_type.in_(
                    [
                        "application/pdf",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ]
                ),
                Artifact.size_bytes <= self.settings.max_upload_bytes,
            )
        )
        if approved_artifact is None:
            return TelegramReply(chat_id, "The approved CV is unavailable or unsafe to deliver.")
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
