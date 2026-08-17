from __future__ import annotations

import ipaddress
import re
import socket
import zipfile
from io import BytesIO
from pathlib import Path, PurePath
from urllib.parse import urlsplit

import bleach
from bs4 import BeautifulSoup

ALLOWED_UPLOADS = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
}


class UnsafeInput(ValueError):
    pass


def validate_public_http_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise UnsafeInput("only unauthenticated public HTTP(S) URLs are permitted")
    try:
        addresses = {
            entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise UnsafeInput("hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeInput("private, loopback, link-local, and reserved targets are blocked")
    return url


def sanitize_html(value: str) -> tuple[str, str]:
    unsafe = BeautifulSoup(value, "html.parser")
    for tag in unsafe(["script", "style", "iframe", "object", "embed", "img", "svg"]):
        tag.decompose()
    cleaned = bleach.clean(
        str(unsafe),
        tags=["p", "br", "ul", "ol", "li", "strong", "em", "h1", "h2", "h3"],
        attributes={},
        protocols=[],
        strip=True,
        strip_comments=True,
    )
    soup = BeautifulSoup(cleaned, "html.parser")
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return cleaned, text[:200_000]


def safe_filename(name: str) -> str:
    if "/" in name or "\\" in name:
        raise UnsafeInput("unsafe filename")
    leaf = PurePath(name.replace("\\", "/")).name
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", leaf)
    if safe in {"", ".", ".."} or safe != leaf:
        raise UnsafeInput("unsafe filename")
    return safe


def validate_upload(
    name: str,
    content_type: str,
    size: int,
    maximum: int,
    content: bytes | None = None,
) -> str:
    safe = safe_filename(name)
    extension = Path(safe).suffix.lower()
    if extension not in ALLOWED_UPLOADS or content_type not in ALLOWED_UPLOADS[extension]:
        raise UnsafeInput("only PDF and DOCX CV files are accepted")
    if size <= 0 or size > maximum:
        raise UnsafeInput("file size is outside the permitted range")
    if content is not None:
        if len(content) != size:
            raise UnsafeInput("reported and received file sizes differ")
        if extension == ".pdf" and not content.startswith(b"%PDF-"):
            raise UnsafeInput("uploaded PDF has an invalid file signature")
        if extension == ".docx":
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    names = set(archive.namelist())
            except (zipfile.BadZipFile, OSError) as exc:
                raise UnsafeInput("uploaded DOCX is not a valid ZIP container") from exc
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UnsafeInput("uploaded DOCX is missing required document parts")
    return safe
