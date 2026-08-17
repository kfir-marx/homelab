from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from ..interfaces import NormalizedJob
from ..normalization import canonicalize_url, extract_ats_job_id
from ..security import sanitize_html


class AtsSourceError(RuntimeError):
    pass


class PublicAtsAdapter:
    name: str

    def __init__(
        self, company: str, slug: str, timeout: float = 15, minimum_interval: float = 1
    ) -> None:
        if not slug or "REPLACE" in slug.upper():
            raise ValueError("ATS company slug is an explicit placeholder")
        self.company = company
        self.slug = slug
        self.timeout = timeout
        self.minimum_interval = minimum_interval
        self._last_request = 0.0

    def discover(self) -> list[NormalizedJob]:
        raise NotImplementedError

    def _get_json(self, url: str) -> dict[str, Any] | list[Any]:
        headers = {"User-Agent": "kfir-homelab-job-assistant/0.1 (+personal-use)"}
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            for attempt in range(3):
                delay = self.minimum_interval - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                response = client.get(url)
                self._last_request = time.monotonic()
                if response.status_code == 429:
                    try:
                        retry_after = min(
                            max(1.0, float(response.headers.get("retry-after", "60"))), 300
                        )
                    except ValueError:
                        retry_after = 60.0
                    if attempt < 2:
                        time.sleep(retry_after)
                        continue
                    raise AtsSourceError(f"source rate limited; retry after {retry_after:.0f}s")
                if response.status_code >= 500 and attempt < 2:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                value: dict[str, Any] | list[Any] = response.json()
                return value
        raise AtsSourceError("ATS request retries exhausted")


class GreenhouseAdapter(PublicAtsAdapter):
    name = "greenhouse"

    def discover(self) -> list[NormalizedJob]:
        data = self._get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        )
        if not isinstance(data, dict):
            raise AtsSourceError("unexpected Greenhouse response")
        jobs = []
        for item in data.get("jobs", []):
            html, text = sanitize_html(str(item.get("content", "")))
            url = str(item["absolute_url"])
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_job_id=str(item["id"]),
                    original_url=url,
                    canonical_url=canonicalize_url(url),
                    company=self.company,
                    title=str(item["title"]),
                    location=(item.get("location") or {}).get("name"),
                    description_html=html,
                    description_text=text,
                    published_at=_parse_datetime(item.get("updated_at")),
                    ats_job_id=str(item["id"]),
                    raw_metadata={
                        "departments": item.get("departments", []),
                        "offices": item.get("offices", []),
                    },
                )
            )
        return jobs


class LeverAdapter(PublicAtsAdapter):
    name = "lever"

    def discover(self) -> list[NormalizedJob]:
        data = self._get_json(f"https://api.lever.co/v0/postings/{self.slug}?mode=json")
        if not isinstance(data, list):
            raise AtsSourceError("unexpected Lever response")
        jobs = []
        for item in data:
            html, text = sanitize_html(
                str(item.get("description", "")) + str(item.get("additional", ""))
            )
            url = str(item.get("hostedUrl") or item.get("applyUrl"))
            categories = item.get("categories") or {}
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_job_id=str(item["id"]),
                    original_url=url,
                    canonical_url=canonicalize_url(url),
                    company=self.company,
                    title=str(item["text"]),
                    location=categories.get("location"),
                    workplace_type=item.get("workplaceType"),
                    employment_type=categories.get("commitment"),
                    description_html=html,
                    description_text=text,
                    ats_job_id=str(item["id"]),
                    raw_metadata={
                        "team": categories.get("team"),
                        "department": categories.get("department"),
                    },
                )
            )
        return jobs


class AshbyAdapter(PublicAtsAdapter):
    name = "ashby"

    def discover(self) -> list[NormalizedJob]:
        data = self._get_json(f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}")
        if not isinstance(data, dict):
            raise AtsSourceError("unexpected Ashby response")
        jobs = []
        for item in data.get("jobs", []):
            html, text = sanitize_html(str(item.get("descriptionHtml", "")))
            url = str(item.get("jobUrl") or item.get("applyUrl"))
            external_id = extract_ats_job_id(url) or str(item.get("id") or item.get("jobUrl"))
            jobs.append(
                NormalizedJob(
                    source=self.name,
                    external_job_id=external_id,
                    original_url=url,
                    canonical_url=canonicalize_url(url),
                    company=self.company,
                    title=str(item["title"]),
                    location=item.get("location"),
                    workplace_type="remote" if item.get("isRemote") else None,
                    employment_type=item.get("employmentType"),
                    description_html=html,
                    description_text=text,
                    published_at=_parse_datetime(item.get("publishedAt")),
                    ats_job_id=external_id,
                    raw_metadata={"team": item.get("team"), "department": item.get("department")},
                )
            )
        return jobs


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
