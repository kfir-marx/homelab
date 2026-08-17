from __future__ import annotations

import email
import hashlib
import re
from email.message import Message

from bs4 import BeautifulSoup

from ..interfaces import NormalizedJob
from ..normalization import canonicalize_url, extract_ats_job_id
from ..security import sanitize_html

LINKEDIN_JOB_URL = re.compile(
    r"https?://(?:[a-z]+\.)?linkedin\.com/(?:comm/)?jobs/view/[^\s\"'<>]+", re.I
)


def _body(message: Message) -> str:
    parts: list[str] = []
    for part in message.walk() if message.is_multipart() else [message]:
        if (
            part.get_content_type() in {"text/html", "text/plain"}
            and part.get_content_disposition() != "attachment"
        ):
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                parts.append(
                    payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                )
    return "\n".join(parts)


def parse_linkedin_alert(raw_message: bytes) -> list[NormalizedJob]:
    message = email.message_from_bytes(raw_message)
    sender = message.get("From", "").casefold()
    if "linkedin" not in sender:
        return []
    body = _body(message)
    soup = BeautifulSoup(body, "html.parser")
    jobs: list[NormalizedJob] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        match = LINKEDIN_JOB_URL.search(str(anchor["href"]))
        if not match:
            continue
        original = match.group(0).replace("&amp;", "&")
        canonical = canonicalize_url(original)
        if canonical in seen:
            continue
        seen.add(canonical)
        title = anchor.get_text(" ", strip=True) or "LinkedIn alert job"
        parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else title
        _, plain = sanitize_html(parent_text)
        external_match = re.search(r"/jobs/view/(?:[^/?]+-)?(?P<id>\d+)", canonical)
        external_id = (
            external_match.group("id")
            if external_match
            else hashlib.sha256(canonical.encode()).hexdigest()[:24]
        )
        jobs.append(
            NormalizedJob(
                source="linkedin-email",
                external_job_id=external_id,
                original_url=original,
                canonical_url=canonical,
                company="Unknown company",
                title=title,
                description_text=plain,
                ats_job_id=extract_ats_job_id(canonical),
                raw_metadata={
                    "message_id": message.get("Message-ID"),
                    "subject": message.get("Subject"),
                },
            )
        )
    return jobs
