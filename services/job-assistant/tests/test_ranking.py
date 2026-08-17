from datetime import UTC, datetime, timedelta

from job_assistant.interfaces import NormalizedJob
from job_assistant.ranking import SearchCriteria, diversify, rank_job


def criteria() -> SearchCriteria:
    return SearchCriteria(
        desired_titles=["DevOps", "Platform"],
        excluded_titles=["Intern"],
        israel_locations=["Israel", "Tel Aviv"],
        required_technologies=["Kubernetes", "Terraform"],
        preferred_technologies=["Argo CD"],
        minimum_match_threshold=0.5,
    )


def job(**overrides: object) -> NormalizedJob:
    values = {
        "source": "test",
        "external_job_id": "1",
        "original_url": "https://example.com/job/1",
        "canonical_url": "https://example.com/job/1",
        "company": "Acme",
        "title": "Senior DevOps Engineer",
        "location": "Tel Aviv, Israel",
        "workplace_type": "hybrid",
        "description_text": "Kubernetes, Terraform and Argo CD",
        "published_at": datetime.now(UTC) - timedelta(days=1),
    }
    values.update(overrides)
    return NormalizedJob.model_validate(values)


def test_ranking_uses_inventory_evidence_and_reports_gaps() -> None:
    result = rank_job(job(), criteria(), {"Kubernetes", "Argo CD"})
    assert result.passed
    assert "Terraform" in result.gaps
    assert result.score >= 0.5


def test_hard_filter_runs_before_score() -> None:
    result = rank_job(job(title="DevOps Intern"), criteria(), {"Kubernetes", "Terraform"})
    assert not result.passed
    assert result.score == 0


def test_remote_outside_israel_allowed() -> None:
    result = rank_job(
        job(location="Worldwide", workplace_type="remote"), criteria(), {"Kubernetes", "Terraform"}
    )
    assert result.passed


def test_preferences_and_feedback_affect_ranking() -> None:
    preferred = criteria().model_copy(update={"preferred_companies": ["Acme"]})
    baseline = rank_job(job(), criteria(), {"Kubernetes", "Terraform"})
    adjusted = rank_job(job(), preferred, {"Kubernetes", "Terraform"}, feedback_adjustment=0.04)
    assert adjusted.score > baseline.score
    assert adjusted.components["feedback"] == 0.04


def test_digest_never_exceeds_five() -> None:
    ranked = []
    for index in range(12):
        candidate = job(
            external_job_id=str(index),
            canonical_url=f"https://example.com/{index}",
            original_url=f"https://example.com/{index}",
            company=f"Company {index}",
        )
        ranked.append((candidate, rank_job(candidate, criteria(), {"Kubernetes", "Terraform"})))
    assert len(diversify(ranked)) == 5
