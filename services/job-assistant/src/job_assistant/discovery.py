from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .career import load_inventory
from .config import Settings
from .domain import ingest_job, transition_job
from .interfaces import JobSource, NormalizedJob
from .metrics import JOBS_DEDUPLICATED, JOBS_DISCOVERED, JOBS_FILTERED, SOURCE_FAILURES
from .models import Company, Job, JobScore, SearchFeedback, User
from .models import JobSource as JobSourceModel
from .queue import put_outbox
from .ranking import RankResult, SearchCriteria, diversify, load_criteria, rank_job
from .sources.registry import load_adapters
from .states import JobStatus


def discover_sources(settings: Settings) -> list[JobSource]:
    sources: list[JobSource] = []
    sources.extend(load_adapters(settings.company_registry_path))
    if settings.imap_username and settings.imap_password:
        from .imap_source import LinkedInImapSource

        sources.append(
            LinkedInImapSource(
                settings.imap_host,
                settings.imap_port,
                settings.imap_username,
                settings.imap_password.get_secret_value(),
                settings.imap_folder,
            )
        )
    return sources


def run_discovery(
    session: Session, settings: Settings, sources: Iterable[JobSource] | None = None
) -> int:
    users = session.scalars(select(User).where(User.active.is_(True))).all()
    criteria_by_user: dict[uuid.UUID, tuple[SearchCriteria, str]] = {}
    for user in users:
        criteria_path = (
            settings.artifact_root / user.search_criteria_key
            if user.search_criteria_key
            else settings.search_criteria_path
        )
        try:
            criteria_by_user[user.id] = load_criteria(criteria_path)
        except (FileNotFoundError, ValueError):
            continue
    ranked: dict[object, list[tuple[NormalizedJob, RankResult]]] = {user.id: [] for user in users}
    persisted: dict[str, Job] = {}
    for source in sources or discover_sources(settings):
        source_record = session.scalar(
            select(JobSourceModel).where(JobSourceModel.name == source.name)
        )
        if not source_record:
            source_record = JobSourceModel(name=source.name, kind=source.name)
            session.add(source_record)
            session.flush()
        now = datetime.now(UTC)
        if source_record.cooldown_until and source_record.cooldown_until > now:
            continue
        try:
            candidates = list(source.discover())
        except Exception:
            SOURCE_FAILURES.labels(source=source.name).inc()
            source_record.consecutive_failures += 1
            cooldown_hours = min(24, 2 ** (source_record.consecutive_failures - 1))
            source_record.cooldown_until = now + timedelta(hours=cooldown_hours)
            continue
        source_record.consecutive_failures = 0
        source_record.cooldown_until = None
        for candidate in candidates:
            job, created = ingest_job(session, candidate)
            JOBS_DISCOVERED.labels(source=candidate.source).inc()
            if not created:
                JOBS_DEDUPLICATED.labels(source=candidate.source).inc()
                continue
            persisted[candidate.canonical_url] = job
            for user in users:
                selected_criteria = criteria_by_user.get(user.id)
                if selected_criteria is None:
                    continue
                criteria, criteria_version = selected_criteria
                technologies: set[str] = set()
                try:
                    inventory = load_inventory(settings.artifact_root / user.career_inventory_key)
                    technologies = {
                        technology
                        for experience in inventory.experiences
                        for fact in experience.responsibilities + experience.achievements
                        for technology in fact.technologies
                    } | {
                        technology
                        for project in inventory.projects
                        for fact in project.facts
                        for technology in fact.technologies
                    }
                    user.inventory_valid = True
                except (FileNotFoundError, ValueError):
                    user.inventory_valid = False
                feedback_rows = (
                    session.execute(
                        select(SearchFeedback.action, Job.title, Company.name)
                        .join(Job, SearchFeedback.job_id == Job.id)
                        .outerjoin(Company, Job.company_id == Company.id)
                        .where(SearchFeedback.user_id == user.id)
                    )
                    .tuples()
                    .all()
                )
                result = rank_job(
                    candidate,
                    criteria,
                    technologies,
                    feedback_adjustment=_feedback_adjustment(candidate, feedback_rows),
                )
                session.add(
                    JobScore(
                        user_id=user.id,
                        job_id=job.id,
                        criteria_version=criteria_version,
                        score=result.score,
                        passed_hard_filters=result.passed,
                        explanation=result.explanation,
                        gaps=list(result.gaps),
                        components=result.components,
                    )
                )
                if result.passed:
                    ranked[user.id].append((candidate, result))
                else:
                    JOBS_FILTERED.labels(reason=result.explanation[:40]).inc()
    queued = 0
    for user in users:
        selected_criteria = criteria_by_user.get(user.id)
        if selected_criteria is None:
            continue
        _, criteria_version = selected_criteria
        for index, (candidate, result) in enumerate(diversify(ranked[user.id], 5)):
            job = persisted[candidate.canonical_url]
            transition_job(session, user, job, JobStatus.SHORTLISTED, "discovery")
            location = candidate.location or "Location unspecified"
            workplace = candidate.workplace_type or "workplace unspecified"
            gaps = ", ".join(result.gaps) if result.gaps else "none identified"
            notification = (
                f"{candidate.company} — {candidate.title}\n{location} / {workplace}\n"
                f"Match {result.score:.0%}: {result.explanation}\n"
                f"Gaps: {gaps}\n{candidate.original_url}"
            )
            put_outbox(
                session,
                "telegram",
                "job_recommendation",
                str(user.telegram_user_id),
                {
                    "text": notification,
                    "buttons": [
                        ["Apply", f"apply:{job.id}"],
                        ["Skip", f"skip:{job.id}"],
                        ["Snooze", f"snooze:{job.id}"],
                        ["Why recommended?", f"why:{job.id}"],
                        ["Open job", f"open:{job.id}"],
                    ],
                },
                f"daily:{criteria_version}:{job.id}:{user.id}:{index}",
                user_id=user.id,
            )
            queued += 1
    return queued


def _feedback_adjustment(
    candidate: NormalizedJob, feedback_rows: Iterable[tuple[str, str, str | None]]
) -> float:
    candidate_tokens = set(re.findall(r"[a-z]{3,}", candidate.title.casefold()))
    adjustment = 0.0
    for action, prior_title, prior_company in feedback_rows:
        weight = 1 if action == "apply" else -1 if action == "skip" else -0.25
        if prior_company and prior_company.casefold() == candidate.company.casefold():
            adjustment += 0.04 * weight
        prior_tokens = set(re.findall(r"[a-z]{3,}", prior_title.casefold()))
        overlap = len(candidate_tokens & prior_tokens) / max(1, len(candidate_tokens))
        if overlap >= 0.5:
            adjustment += 0.03 * weight
    return max(-0.08, min(0.08, adjustment))
