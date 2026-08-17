from __future__ import annotations

import imaplib
import ssl

from .sources.linkedin_email import parse_linkedin_alert


class LinkedInImapSource:
    name = "linkedin-email"

    def __init__(self, host: str, port: int, username: str, password: str, folder: str) -> None:
        self.host, self.port, self.username, self.password, self.folder = (
            host,
            port,
            username,
            password,
            folder,
        )

    def discover(self):  # type: ignore[no-untyped-def]
        jobs = []
        context = ssl.create_default_context()
        with imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context, timeout=30) as client:
            client.login(self.username, self.password)
            status, _ = client.select(self.folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"could not select IMAP folder {self.folder!r}")
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            for message_id in data[0].split()[-100:]:
                status, parts = client.fetch(message_id, "(RFC822)")
                if status != "OK":
                    continue
                raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                if isinstance(raw, bytes):
                    jobs.extend(parse_linkedin_alert(raw))
        return jobs
