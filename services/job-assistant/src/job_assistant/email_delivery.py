from __future__ import annotations

import email.utils
import smtplib
import ssl
from email.message import EmailMessage

from .interfaces import Delivery


class SmtpDeliveryProvider:
    def __init__(self, host: str, port: int, username: str, password: str, sender: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender

    def send(self, delivery: Delivery) -> str:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = delivery.recipient
        message["Subject"] = delivery.subject
        message["Message-ID"] = email.utils.make_msgid(domain=self.sender.rsplit("@", 1)[-1])
        message["X-Job-Assistant-Idempotency-Key"] = delivery.idempotency_key
        message.set_content(delivery.body)
        for path in delivery.attachments:
            content = path.read_bytes()
            if path.suffix.lower() == ".pdf":
                maintype, subtype = "application", "pdf"
            elif path.suffix.lower() == ".docx":
                maintype, subtype = (
                    "application",
                    "vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            else:
                maintype, subtype = "text", "plain"
            message.add_attachment(content, maintype=maintype, subtype=subtype, filename=path.name)
        context = ssl.create_default_context()
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(self.username, self.password)
            refused = smtp.send_message(message)
        if refused:
            raise RuntimeError(f"SMTP refused {len(refused)} recipient(s)")
        return str(message["Message-ID"])


class FakeDeliveryProvider:
    def __init__(self) -> None:
        self.deliveries: list[Delivery] = []

    def send(self, delivery: Delivery) -> str:
        if any(item.idempotency_key == delivery.idempotency_key for item in self.deliveries):
            return f"fake:{delivery.idempotency_key}"
        self.deliveries.append(delivery)
        return f"fake:{delivery.idempotency_key}"
