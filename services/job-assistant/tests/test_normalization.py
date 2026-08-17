from job_assistant.normalization import (
    canonicalize_url,
    description_hash,
    extract_ats_job_id,
    suspected_duplicate_similarity,
)


def test_canonicalize_removes_tracking_and_normalizes() -> None:
    assert (
        canonicalize_url(
            "HTTPS://Jobs.Example.com:443//role/123/?utm_source=x&keep=yes&fbclid=no#fragment"
        )
        == "https://jobs.example.com/role/123?keep=yes"
    )


def test_extract_known_ats_ids() -> None:
    assert extract_ats_job_id("https://boards.greenhouse.io/acme/jobs/12345") == "12345"
    assert extract_ats_job_id("https://jobs.lever.co/acme/12345678-abcd-1234-abcd-123456789abc")
    assert extract_ats_job_id("https://example.invalid/jobs/123") is None


def test_hash_is_whitespace_and_case_stable() -> None:
    assert description_hash(" Kubernetes  PLATFORM ") == description_hash("kubernetes platform")


def test_suspected_duplicate_does_not_decide_merge() -> None:
    score = suspected_duplicate_similarity(
        "Acme",
        "Senior DevOps Engineer",
        "Tel Aviv",
        "Kubernetes Terraform Linux",
        "ACME",
        "Senior DevOps Engineer",
        "Tel Aviv",
        "Kubernetes Terraform Linux",
    )
    assert score > 0.95
