from __future__ import annotations

from enum import StrEnum


class InvalidTransition(ValueError):
    pass


class JobStatus(StrEnum):
    DISCOVERED = "discovered"
    SHORTLISTED = "shortlisted"
    SKIPPED = "skipped"
    SNOOZED = "snoozed"
    EXPIRED = "expired"
    REOPENED = "reopened"


class ApplicationStatus(StrEnum):
    SELECTED = "selected"
    GENERATION_QUEUED = "generation_queued"
    GENERATING = "generating"
    REVIEW_READY = "review_ready"
    FINAL_MATERIAL_RECEIVED = "final_material_received"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    MANUAL_REQUIRED = "manual_required"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class OutreachStatus(StrEnum):
    NO_CONTACT = "no_contact"
    CONTACT_CANDIDATE_FOUND = "contact_candidate_found"
    CONTACT_VERIFIED = "contact_verified"
    DRAFTED = "drafted"
    APPROVED = "approved"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    MANUAL_REQUIRED = "manual_required"
    FAILED = "failed"


JOB_TRANSITIONS = {
    JobStatus.DISCOVERED: {
        JobStatus.SHORTLISTED,
        JobStatus.SKIPPED,
        JobStatus.SNOOZED,
        JobStatus.EXPIRED,
    },
    JobStatus.SHORTLISTED: {JobStatus.SKIPPED, JobStatus.SNOOZED, JobStatus.EXPIRED},
    JobStatus.SKIPPED: {JobStatus.REOPENED},
    JobStatus.SNOOZED: {JobStatus.REOPENED, JobStatus.EXPIRED},
    JobStatus.EXPIRED: {JobStatus.REOPENED},
    JobStatus.REOPENED: {
        JobStatus.SHORTLISTED,
        JobStatus.SKIPPED,
        JobStatus.SNOOZED,
        JobStatus.EXPIRED,
    },
}

APPLICATION_TRANSITIONS = {
    ApplicationStatus.SELECTED: {ApplicationStatus.GENERATION_QUEUED, ApplicationStatus.WITHDRAWN},
    ApplicationStatus.GENERATION_QUEUED: {ApplicationStatus.GENERATING, ApplicationStatus.FAILED},
    ApplicationStatus.GENERATING: {ApplicationStatus.REVIEW_READY, ApplicationStatus.FAILED},
    ApplicationStatus.REVIEW_READY: {
        ApplicationStatus.FINAL_MATERIAL_RECEIVED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.FINAL_MATERIAL_RECEIVED: {
        ApplicationStatus.APPROVED,
        ApplicationStatus.MANUAL_REQUIRED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.APPROVED: {
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.MANUAL_REQUIRED,
        ApplicationStatus.FAILED,
    },
    ApplicationStatus.FAILED: {ApplicationStatus.GENERATION_QUEUED, ApplicationStatus.WITHDRAWN},
    ApplicationStatus.MANUAL_REQUIRED: {ApplicationStatus.SUBMITTED, ApplicationStatus.WITHDRAWN},
    ApplicationStatus.SUBMITTED: set(),
    ApplicationStatus.WITHDRAWN: set(),
}

OUTREACH_TRANSITIONS = {
    OutreachStatus.NO_CONTACT: {
        OutreachStatus.CONTACT_CANDIDATE_FOUND,
        OutreachStatus.MANUAL_REQUIRED,
    },
    OutreachStatus.CONTACT_CANDIDATE_FOUND: {
        OutreachStatus.CONTACT_VERIFIED,
        OutreachStatus.MANUAL_REQUIRED,
    },
    OutreachStatus.CONTACT_VERIFIED: {OutreachStatus.DRAFTED},
    OutreachStatus.DRAFTED: {OutreachStatus.APPROVED, OutreachStatus.MANUAL_REQUIRED},
    OutreachStatus.APPROVED: {OutreachStatus.QUEUED, OutreachStatus.MANUAL_REQUIRED},
    OutreachStatus.QUEUED: {
        OutreachStatus.SENT,
        OutreachStatus.FAILED,
        OutreachStatus.MANUAL_REQUIRED,
    },
    OutreachStatus.SENT: {OutreachStatus.DELIVERED, OutreachStatus.BOUNCED},
    OutreachStatus.FAILED: {OutreachStatus.QUEUED, OutreachStatus.MANUAL_REQUIRED},
    OutreachStatus.MANUAL_REQUIRED: set(),
    OutreachStatus.DELIVERED: set(),
    OutreachStatus.BOUNCED: set(),
}


def ensure_transition[StateT: StrEnum](
    current: StateT, target: StateT, graph: dict[StateT, set[StateT]]
) -> None:
    if target not in graph.get(current, set()):
        raise InvalidTransition(f"invalid transition: {current.value} -> {target.value}")
