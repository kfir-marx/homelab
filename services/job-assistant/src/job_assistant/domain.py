from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .human_codes import generate_human_code
from .interfaces import NormalizedJob
from .models import (
    Application,
    ApplicationEvent,
    Company,
    Job,
    JobDuplicateCandidate,
    JobSourceOccurrence,
    SearchFeedback,
    User,
    UserJobState,
)
from .models import (
    JobSource as JobSourceModel,
)
from .normalization import description_hash, suspected_duplicate_similarity
from .queue import enqueue_work
from .states import (
    APPLICATION_TRANSITIONS,
    JOB_TRANSITIONS,
    OUTREACH_TRANSITIONS,
    ApplicationStatus,
    JobStatus,
    OutreachStatus,
    ensure_transition,
)


def normalize_company_name(name: str) -> str:
    return " ".join(name.casefold().split())


def ingest_job(session: Session, candidate: NormalizedJob) -> tuple[Job, bool]:
    source = session.scalar(select(JobSourceModel).where(JobSourceModel.name == candidate.source))
    if not source:
        source = JobSourceModel(name=candidate.source, kind=candidate.source)
        session.add(source)
        session.flush()
    occurrence = session.scalar(
        select(JobSourceOccurrence).where(
            JobSourceOccurrence.source_id == source.id,
            JobSourceOccurrence.external_job_id == candidate.external_job_id,
        )
    )
    if occurrence:
        job = session.get(Job, occurrence.job_id)
        assert job is not None
        occurrence.last_seen_at = datetime.now(UTC)
        job.last_seen_at = datetime.now(UTC)
        return job, False

    exact = None
    if candidate.ats_job_id:
        exact = session.scalar(select(Job).where(Job.ats_job_id == candidate.ats_job_id))
    if not exact:
        exact = session.scalar(select(Job).where(Job.canonical_url == candidate.canonical_url))
    digest = description_hash(candidate.description_text) if candidate.description_text else None
    if not exact and digest:
        exact = session.scalar(select(Job).where(Job.description_hash == digest))

    company_key = normalize_company_name(candidate.company)
    company = session.scalar(select(Company).where(Company.normalized_name == company_key))
    if not company:
        company = Company(name=candidate.company, normalized_name=company_key)
        session.add(company)
        session.flush()

    created = exact is None
    if exact:
        job = exact
    else:
        job = Job(
            company_id=company.id,
            title=candidate.title,
            location=candidate.location,
            workplace_type=candidate.workplace_type,
            employment_type=candidate.employment_type,
            original_url=candidate.original_url,
            canonical_url=candidate.canonical_url,
            ats_job_id=candidate.ats_job_id,
            description_html=candidate.description_html,
            description_text=candidate.description_text,
            description_hash=digest,
            raw_metadata=candidate.raw_metadata,
            published_at=candidate.published_at,
        )
        session.add(job)
        session.flush()
        _record_suspected_duplicates(session, job, candidate)
    session.add(
        JobSourceOccurrence(
            job_id=job.id,
            source_id=source.id,
            external_job_id=candidate.external_job_id,
            source_url=candidate.original_url,
            raw_metadata=candidate.raw_metadata,
        )
    )
    session.flush()
    return job, created


def _record_suspected_duplicates(session: Session, job: Job, candidate: NormalizedJob) -> None:
    recent = session.scalars(
        select(Job).where(Job.id != job.id).order_by(Job.created_at.desc()).limit(100)
    ).all()
    for other in recent:
        score = suspected_duplicate_similarity(
            candidate.company,
            candidate.title,
            candidate.location or "",
            candidate.description_text,
            other.company.name if other.company else "",
            other.title,
            other.location or "",
            other.description_text,
        )
        if score >= 0.82:
            session.add(
                JobDuplicateCandidate(
                    job_id=job.id,
                    candidate_job_id=other.id,
                    similarity=score,
                    reason="company/title/location/description similarity",
                )
            )


def get_user_job_state(session: Session, user: User, job: Job) -> UserJobState:
    state = session.scalar(
        select(UserJobState).where(UserJobState.user_id == user.id, UserJobState.job_id == job.id)
    )
    if state is None:
        state = UserJobState(user_id=user.id, job_id=job.id)
        session.add(state)
        session.flush()
    return state


def transition_job(session: Session, user: User, job: Job, target: JobStatus, actor: str) -> None:
    state = get_user_job_state(session, user, job)
    current = JobStatus(state.status)
    ensure_transition(current, target, JOB_TRANSITIONS)
    state.status = target.value
    session.add(
        ApplicationEvent(
            user_id=user.id,
            job_id=job.id,
            aggregate="job",
            from_state=current.value,
            to_state=target.value,
            actor=actor,
        )
    )


def create_application(
    session: Session, user: User, job: Job, actor: str
) -> tuple[Application, bool]:
    existing = session.scalar(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    )
    if existing:
        return existing, False
    code = generate_human_code(
        lambda value: (
            session.scalar(select(Application.id).where(Application.human_code == value))
            is not None
        )
    )
    application = Application(user_id=user.id, job_id=job.id, human_code=code)
    session.add(application)
    session.flush()
    session.add(
        ApplicationEvent(
            user_id=user.id,
            application_id=application.id,
            job_id=job.id,
            aggregate="application",
            from_state=None,
            to_state=ApplicationStatus.SELECTED.value,
            actor=actor,
        )
    )
    return application, True


def queue_application_generation(
    session: Session,
    application: Application,
    actor: str,
    generation_payload: dict[str, object],
    notification_chat_id: int | None = None,
) -> None:
    if application.status == ApplicationStatus.SELECTED.value:
        transition_application(session, application, ApplicationStatus.GENERATION_QUEUED, actor)
    enqueue_work(
        session,
        "generation",
        "generate_application",
        {
            "application_id": str(application.id),
            "generation_payload": generation_payload,
            "notification_chat_id": notification_chat_id,
        },
        f"generate:{application.id}:v1",
        user_id=application.user_id,
    )


def transition_application(
    session: Session,
    application: Application,
    target: ApplicationStatus,
    actor: str,
    metadata: dict[str, object] | None = None,
) -> None:
    current = ApplicationStatus(application.status)
    ensure_transition(current, target, APPLICATION_TRANSITIONS)
    application.status = target.value
    session.add(
        ApplicationEvent(
            user_id=application.user_id,
            application_id=application.id,
            job_id=application.job_id,
            aggregate="application",
            from_state=current.value,
            to_state=target.value,
            actor=actor,
            metadata_json=metadata or {},
        )
    )


def transition_outreach(
    session: Session,
    application: Application,
    target: OutreachStatus,
    actor: str,
    metadata: dict[str, object] | None = None,
) -> None:
    current = OutreachStatus(application.outreach_status)
    ensure_transition(current, target, OUTREACH_TRANSITIONS)
    application.outreach_status = target.value
    session.add(
        ApplicationEvent(
            user_id=application.user_id,
            application_id=application.id,
            job_id=application.job_id,
            aggregate="outreach",
            from_state=current.value,
            to_state=target.value,
            actor=actor,
            metadata_json=metadata or {},
        )
    )


def get_application_by_code(session: Session, user: User, code: str) -> Application | None:
    return session.scalar(
        select(Application).where(
            Application.user_id == user.id, Application.human_code == code.upper()
        )
    )


def record_search_feedback(
    session: Session,
    user: User,
    job: Job,
    action: str,
    application: Application | None = None,
) -> None:
    existing = session.scalar(
        select(SearchFeedback.id).where(
            SearchFeedback.job_id == job.id,
            SearchFeedback.user_id == user.id,
            SearchFeedback.action == action,
        )
    )
    if not existing:
        session.add(
            SearchFeedback(
                user_id=user.id,
                job_id=job.id,
                application_id=application.id if application else None,
                action=action,
            )
        )
