from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .interfaces import NormalizedJob


class SearchCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    desired_titles: list[str]
    excluded_titles: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
    israel_locations: list[str] = Field(default_factory=list)
    fully_remote: bool = True
    acceptable_timezone_difference_hours: int = 4
    required_technologies: list[str] = Field(default_factory=list)
    preferred_technologies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    minimum_match_threshold: float = Field(default=0.65, ge=0, le=1)
    maximum_job_age_days: int = Field(default=30, ge=1)
    language_preferences: list[str] = Field(default_factory=lambda: ["English", "Hebrew"])
    minimum_salary: int | None = None
    salary_currency: str | None = None


@dataclass(frozen=True)
class RankResult:
    passed: bool
    score: float
    explanation: str
    gaps: tuple[str, ...]
    components: dict[str, float]


def load_criteria(path: Path) -> tuple[SearchCriteria, str]:
    raw = path.read_bytes()
    return SearchCriteria.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()[:16]


def _contains_any(text: str, values: list[str]) -> bool:
    folded = text.casefold()
    return any(value.casefold() in folded for value in values)


def rank_job(
    job: NormalizedJob,
    criteria: SearchCriteria,
    inventory_technologies: set[str],
    now: datetime | None = None,
    feedback_adjustment: float = 0,
) -> RankResult:
    now = now or datetime.now(UTC)
    title = job.title.casefold()
    company = job.company.casefold()
    if _contains_any(title, criteria.excluded_titles):
        return RankResult(False, 0, "excluded title", (), {"hard_filter": 0})
    if _contains_any(company, criteria.excluded_companies):
        return RankResult(False, 0, "excluded company", (), {"hard_filter": 0})
    if job.published_at and (now - job.published_at).days > criteria.maximum_job_age_days:
        return RankResult(
            False, 0, "job is older than the configured maximum", (), {"hard_filter": 0}
        )
    remote = (job.workplace_type or "").casefold() == "remote" or "remote" in (
        job.location or ""
    ).casefold()
    in_israel = _contains_any(job.location or "", criteria.israel_locations)
    if not in_israel and not (criteria.fully_remote and remote):
        return RankResult(
            False,
            0,
            "location is neither Israel-compatible nor fully remote",
            (),
            {"hard_filter": 0},
        )

    title_score = 1.0 if _contains_any(title, criteria.desired_titles) else 0.35
    description = re.sub(r"[^a-z0-9+#.]+", " ", job.description_text.casefold())
    inventory = {item.casefold() for item in inventory_technologies}
    required = [tech for tech in criteria.required_technologies if tech.casefold() in description]
    supported_required = [tech for tech in required if tech.casefold() in inventory]
    gaps = tuple(tech for tech in required if tech.casefold() not in inventory)
    skill_score = len(supported_required) / max(1, len(required))
    preferred_hits = sum(
        1
        for tech in criteria.preferred_technologies
        if tech.casefold() in description and tech.casefold() in inventory
    )
    preferred_score = preferred_hits / max(1, len(criteria.preferred_technologies))
    freshness = 1.0
    if job.published_at:
        freshness = max(0.0, 1 - (now - job.published_at).days / criteria.maximum_job_age_days)
    location_score = 1.0 if in_israel else 0.9 if remote else 0.0
    company_bonus = 0.05 if _contains_any(company, criteria.preferred_companies) else 0.0
    components = {
        "title": title_score,
        "skills": skill_score,
        "preferred_skills": preferred_score,
        "freshness": freshness,
        "location": location_score,
        "company_preference": company_bonus,
        "feedback": feedback_adjustment,
    }
    score = round(
        title_score * 0.30
        + skill_score * 0.30
        + preferred_score * 0.15
        + freshness * 0.10
        + location_score * 0.15
        + company_bonus
        + max(-0.08, min(0.08, feedback_adjustment)),
        4,
    )
    score = max(0.0, min(1.0, score))
    passed = score >= criteria.minimum_match_threshold
    explanation = (
        f"title {title_score:.0%}; evidenced required skills {skill_score:.0%}; "
        f"location compatibility {location_score:.0%}; freshness {freshness:.0%}"
    )
    return RankResult(passed, score, explanation, gaps, components)


def diversify(
    results: list[tuple[NormalizedJob, RankResult]], limit: int = 5
) -> list[tuple[NormalizedJob, RankResult]]:
    selected: list[tuple[NormalizedJob, RankResult]] = []
    company_counts: dict[str, int] = {}
    title_tokens: set[str] = set()
    for job, result in sorted(results, key=lambda item: item[1].score, reverse=True):
        company_key = job.company.casefold()
        tokens = set(re.findall(r"[a-z]{3,}", job.title.casefold()))
        penalty = (
            company_counts.get(company_key, 0) * 0.15
            + (len(tokens & title_tokens) / max(1, len(tokens))) * 0.1
        )
        if result.score - penalty < result.score * 0.75 and len(results) > limit:
            continue
        selected.append((job, result))
        company_counts[company_key] = company_counts.get(company_key, 0) + 1
        title_tokens.update(tokens)
        if len(selected) == limit:
            break
    return selected
