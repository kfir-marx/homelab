from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContactPolicyInput:
    email: str | None
    company_domain: str | None
    confidence: str
    verified: bool
    user_approved: bool
    already_sent: bool


def automatic_email_allowed(value: ContactPolicyInput) -> tuple[bool, str]:
    if value.already_sent:
        return False, "this application/contact combination was already sent"
    if not value.user_approved:
        return False, "final materials and recipient require explicit approval"
    if value.confidence != "high" or not value.verified:
        return False, "contact is not high-confidence and verified"
    if not value.email or not value.company_domain:
        return False, "verified company-domain email is unavailable"
    domain = value.email.rsplit("@", 1)[-1].casefold() if "@" in value.email else ""
    company_domain = value.company_domain.casefold().lstrip("@")
    if domain != company_domain and not domain.endswith(f".{company_domain}"):
        return False, "recipient email is not on the verified company domain"
    return True, "verified high-confidence company-domain email"
