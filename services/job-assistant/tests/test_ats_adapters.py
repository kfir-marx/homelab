from datetime import UTC

import httpx
import respx

from job_assistant.sources.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter


@respx.mock
def test_greenhouse_fixture() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 123,
                        "title": "Platform Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123?utm_source=x",
                        "location": {"name": "Tel Aviv"},
                        "content": "<p>Kubernetes</p><script>bad()</script>",
                        "updated_at": "2026-08-16T09:00:00Z",
                    }
                ]
            },
        )
    )
    job = GreenhouseAdapter("Acme", "acme", minimum_interval=0).discover()[0]
    assert job.external_job_id == "123"
    assert job.published_at and job.published_at.tzinfo == UTC
    assert "script" not in (job.description_html or "")
    assert "utm_source" not in job.canonical_url


@respx.mock
def test_lever_fixture() -> None:
    respx.get("https://api.lever.co/v0/postings/acme?mode=json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "12345678-abcd-1234-abcd-123456789abc",
                    "text": "DevOps Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/12345678-abcd-1234-abcd-123456789abc",
                    "description": "<p>Terraform</p>",
                    "categories": {"location": "Remote", "commitment": "Full-time"},
                    "workplaceType": "remote",
                }
            ],
        )
    )
    job = LeverAdapter("Acme", "acme", minimum_interval=0).discover()[0]
    assert job.workplace_type == "remote"
    assert job.employment_type == "Full-time"


@respx.mock
def test_ashby_fixture() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "ashby-one",
                        "title": "SRE",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/12345678-abcd-1234-abcd-123456789abc",
                        "descriptionHtml": "<p>Linux</p>",
                        "location": "Israel",
                        "isRemote": True,
                        "publishedAt": "2026-08-16T09:00:00Z",
                    }
                ]
            },
        )
    )
    job = AshbyAdapter("Acme", "acme", minimum_interval=0).discover()[0]
    assert job.ats_job_id == "12345678-abcd-1234-abcd-123456789abc"
    assert job.description_text == "Linux"
