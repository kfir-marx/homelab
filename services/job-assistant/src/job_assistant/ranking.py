from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .interfaces import NormalizedJob


class SearchCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    desired_titles: list[str]
    excluded_titles: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
    israel_locations: list[str] = Field(default_factory=list)
    fully_remote: bool = True
    acceptable_timezone_difference_hours: int = Field(default=4, ge=0, le=14)
    required_technologies: list[str] = Field(default_factory=list)
    preferred_technologies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    minimum_match_threshold: float = Field(default=0.65, ge=0, le=1)
    maximum_job_age_days: int = Field(default=30, ge=1, le=365)
    language_preferences: list[str] = Field(default_factory=lambda: ["English", "Hebrew"])
    minimum_salary: int | None = Field(default=None, ge=0, le=100_000_000)
    salary_currency: str | None = None

    @field_validator(
        "desired_titles",
        "excluded_titles",
        "seniority",
        "israel_locations",
        "required_technologies",
        "preferred_technologies",
        "excluded_companies",
        "preferred_companies",
        "language_preferences",
    )
    @classmethod
    def validate_list_values(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split()) for value in values if value.strip()]
        if len(cleaned) > 50 or any(len(value) > 100 for value in cleaned):
            raise ValueError("lists support at most 50 values of 100 characters each")
        if len(cleaned) != len({value.casefold() for value in cleaned}):
            raise ValueError("values must be unique (case-insensitive)")
        return cleaned

    @field_validator("desired_titles")
    @classmethod
    def require_desired_title(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("at least one desired title is required")
        return values

    @field_validator("seniority")
    @classmethod
    def validate_seniority(cls, values: list[str]) -> list[str]:
        allowed = {
            "junior",
            "entry",
            "mid",
            "senior",
            "staff",
            "lead",
            "principal",
            "manager",
            "director",
        }
        if any(value.casefold() not in allowed for value in values):
            raise ValueError("seniority contains an unsupported level")
        return values

    @field_validator("salary_currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        normalized = value.strip().upper() if value and value.strip() else None
        if normalized is not None and (len(normalized) != 3 or not normalized.isalpha()):
            raise ValueError("salary currency must be a three-letter code")
        return normalized

    @model_validator(mode="after")
    def validate_salary_pair(self) -> SearchCriteria:
        if (self.minimum_salary is None) != (self.salary_currency is None):
            raise ValueError("minimum_salary and salary_currency must be set together")
        if not self.israel_locations and not self.fully_remote:
            raise ValueError("configure an Israel location or allow fully remote roles")
        return self


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


_SENIORITY_ALIASES = {
    "entry": "junior",
    "jr": "junior",
    "junior": "junior",
    "mid": "mid",
    "middle": "mid",
    "senior": "senior",
    "sr": "senior",
    "staff": "staff",
    "lead": "lead",
    "principal": "principal",
    "manager": "manager",
    "director": "director",
}


def _normalized_seniority(job: NormalizedJob) -> str | None:
    if job.seniority:
        return _SENIORITY_ALIASES.get(job.seniority.casefold(), job.seniority.casefold())
    title_tokens = set(re.findall(r"[a-z]+", job.title.casefold()))
    for token, normalized in _SENIORITY_ALIASES.items():
        if token in title_tokens:
            return normalized
    return None


def _hard_failure(reason: str, unknown: list[str]) -> RankResult:
    explanation = reason
    if unknown:
        explanation += "; unknown: " + ", ".join(unknown)
    return RankResult(False, 0, explanation, (), {"hard_filter": 0})


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
    unknown: list[str] = []
    if _contains_any(title, criteria.excluded_titles):
        return _hard_failure("excluded title", unknown)
    if _contains_any(company, criteria.excluded_companies):
        return _hard_failure("excluded company", unknown)
    if job.published_at and (now - job.published_at).days > criteria.maximum_job_age_days:
        return _hard_failure("job is older than the configured maximum", unknown)
    if not job.published_at:
        unknown.append("job age")
    remote = (job.workplace_type or "").casefold() == "remote" or "remote" in (
        job.location or ""
    ).casefold()
    in_israel = _contains_any(job.location or "", criteria.israel_locations)
    if not in_israel and not (criteria.fully_remote and remote):
        if job.location or job.workplace_type:
            return _hard_failure("location is neither Israel-compatible nor fully remote", unknown)
        unknown.append("location/workplace")

    detected_seniority = _normalized_seniority(job)
    preferred_seniority = {
        _SENIORITY_ALIASES.get(value.casefold(), value.casefold()) for value in criteria.seniority
    }
    if preferred_seniority and detected_seniority and detected_seniority not in preferred_seniority:
        return _hard_failure(
            f"seniority {detected_seniority} is outside the preferred levels", unknown
        )
    if preferred_seniority and not detected_seniority:
        unknown.append("seniority")

    if job.timezone_difference_hours is not None:
        if abs(job.timezone_difference_hours) > criteria.acceptable_timezone_difference_hours:
            return _hard_failure("timezone difference exceeds the configured maximum", unknown)
    else:
        unknown.append("timezone difference")

    description_available = bool(job.description_text.strip())
    description = re.sub(r"[^a-z0-9+#.]+", " ", job.description_text.casefold())
    if criteria.required_technologies and description_available:
        absent = [
            tech for tech in criteria.required_technologies if tech.casefold() not in description
        ]
        if absent:
            return _hard_failure("missing required technologies: " + ", ".join(absent), unknown)
    elif criteria.required_technologies:
        unknown.append("required technologies")

    if criteria.minimum_salary is not None:
        assert criteria.salary_currency is not None
        if not job.salary_currency or (job.salary_min is None and job.salary_max is None):
            unknown.append("salary")
        elif job.salary_currency.casefold() != criteria.salary_currency.casefold():
            unknown.append("salary (currency differs)")
        elif job.salary_max is not None and job.salary_max < criteria.minimum_salary:
            return _hard_failure("salary maximum is below the configured minimum", unknown)

    title_score = 1.0 if _contains_any(title, criteria.desired_titles) else 0.35
    inventory = {item.casefold() for item in inventory_technologies}
    required = (
        [tech for tech in criteria.required_technologies if tech.casefold() in description]
        if description_available
        else []
    )
    supported_required = [tech for tech in required if tech.casefold() in inventory]
    gaps = tuple(tech for tech in required if tech.casefold() not in inventory)
    preferred_hits = sum(
        1
        for tech in criteria.preferred_technologies
        if tech.casefold() in description and tech.casefold() in inventory
    )
    weighted: list[tuple[str, float, float]] = [("title", title_score, 0.30)]
    if required:
        weighted.append(("skills", len(supported_required) / len(required), 0.30))
    if criteria.preferred_technologies and description_available:
        weighted.append(
            ("preferred_skills", preferred_hits / len(criteria.preferred_technologies), 0.15)
        )
    if job.published_at:
        weighted.append(
            (
                "freshness",
                max(0.0, 1 - (now - job.published_at).days / criteria.maximum_job_age_days),
                0.10,
            )
        )
    if job.location or job.workplace_type:
        weighted.append(("location", 1.0 if in_israel else 0.9 if remote else 0.0, 0.15))
    if preferred_seniority and detected_seniority:
        weighted.append(("seniority", 1.0, 0.10))
    if job.timezone_difference_hours is not None:
        weighted.append(("timezone", 1.0, 0.05))
    if criteria.language_preferences:
        if job.languages is None:
            unknown.append("languages")
        else:
            wanted = {language.casefold() for language in criteria.language_preferences}
            offered = {language.casefold() for language in job.languages}
            weighted.append(("languages", len(wanted & offered) / len(wanted), 0.05))
    if (
        criteria.minimum_salary is not None
        and criteria.salary_currency is not None
        and job.salary_currency
        and (job.salary_min is not None or job.salary_max is not None)
        and job.salary_currency.casefold() == criteria.salary_currency.casefold()
    ):
        salary_floor = job.salary_min if job.salary_min is not None else job.salary_max
        assert salary_floor is not None
        weighted.append(("salary", min(1.0, salary_floor / max(1, criteria.minimum_salary)), 0.10))
    company_bonus = 0.05 if _contains_any(company, criteria.preferred_companies) else 0.0
    components = {name: value for name, value, _ in weighted}
    if company_bonus:
        components["company_preference"] = company_bonus
    bounded_feedback = max(-0.08, min(0.08, feedback_adjustment))
    if bounded_feedback:
        components["feedback"] = bounded_feedback
    total_weight = sum(weight for _, _, weight in weighted)
    score = round(
        sum(value * weight for _, value, weight in weighted) / total_weight
        + company_bonus
        + bounded_feedback,
        4,
    )
    score = max(0.0, min(1.0, score))
    passed = score >= criteria.minimum_match_threshold
    explanation = "; ".join(f"{name.replace('_', ' ')} {value:.0%}" for name, value, _ in weighted)
    if company_bonus:
        explanation += f"; preferred company +{company_bonus:.0%}"
    if bounded_feedback:
        explanation += f"; feedback {bounded_feedback:+.0%}"
    if unknown:
        explanation += "; unknown (not scored): " + ", ".join(dict.fromkeys(unknown))
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
