from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "refid",
    "trk",
    "trackingid",
}

ATS_PATTERNS = [
    re.compile(r"boards\.greenhouse\.io/[^/]+/jobs/(?P<id>\d+)", re.I),
    re.compile(r"jobs\.lever\.co/[^/]+/(?P<id>[0-9a-f-]{20,})", re.I),
    re.compile(r"jobs\.ashbyhq\.com/[^/]+/(?P<id>[0-9a-f-]{20,})", re.I),
]


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port
    authority = host
    if port and not (
        (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80)
    ):
        authority = f"{host}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), authority, path, urlencode(sorted(query)), ""))


def extract_ats_job_id(url: str) -> str | None:
    for pattern in ATS_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group("id")
    return None


def description_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def suspected_duplicate_similarity(
    company_a: str,
    title_a: str,
    location_a: str,
    description_a: str,
    company_b: str,
    title_b: str,
    location_b: str,
    description_b: str,
) -> float:
    def ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left.casefold().strip(), right.casefold().strip()).ratio()

    return round(
        ratio(company_a, company_b) * 0.25
        + ratio(title_a, title_b) * 0.30
        + ratio(location_a, location_b) * 0.10
        + ratio(description_a[:20_000], description_b[:20_000]) * 0.35,
        4,
    )
