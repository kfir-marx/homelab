from __future__ import annotations

import hashlib
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..interfaces import NormalizedJob
from ..normalization import canonicalize_url, extract_ats_job_id
from ..security import UnsafeInput, sanitize_html, validate_public_http_url


def fetch_public_job(
    url: str, timeout: float = 15, maximum_bytes: int = 2_000_000
) -> NormalizedJob:
    current = validate_public_http_url(url)
    headers = {"User-Agent": "kfir-homelab-job-assistant/0.1 (+personal-use; contact site owner)"}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=False) as client:
        for _ in range(5):
            response = client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeInput("redirect is missing a location")
                current = validate_public_http_url(urljoin(current, location))
                continue
            response.raise_for_status()
            if len(response.content) > maximum_bytes:
                raise UnsafeInput("job page exceeds maximum download size")
            content_type = response.headers.get("content-type", "").casefold()
            if "text/html" not in content_type:
                raise UnsafeInput("job URL did not return HTML")
            break
        else:
            raise UnsafeInput("too many redirects")
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "iframe", "object", "embed", "img", "svg"]):
        tag.decompose()
    title_tag = soup.find("meta", property="og:title")
    company_tag = soup.find("meta", property="og:site_name")
    title = (str(title_tag.get("content")) if title_tag and title_tag.get("content") else "") or (
        soup.title.string if soup.title and soup.title.string else ""
    )
    company = (
        str(company_tag.get("content"))
        if company_tag and company_tag.get("content")
        else "Unknown company"
    )
    cleaned, text = sanitize_html(str(soup.body or soup))
    canonical = canonicalize_url(current)
    external_id = (
        extract_ats_job_id(canonical) or hashlib.sha256(canonical.encode()).hexdigest()[:24]
    )
    return NormalizedJob(
        source="manual",
        external_job_id=external_id,
        original_url=url,
        canonical_url=canonical,
        company=str(company).strip(),
        title=str(title).strip() or "Title requires manual completion",
        description_html=cleaned,
        description_text=text,
        ats_job_id=extract_ats_job_id(canonical),
        raw_metadata={"fetched_url": current, "requires_manual_completion": not bool(title)},
    )
