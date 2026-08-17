from job_assistant.contact_policy import ContactPolicyInput, automatic_email_allowed


def test_only_verified_high_confidence_company_email_is_automatic() -> None:
    allowed, _ = automatic_email_allowed(
        ContactPolicyInput("person@acme.com", "acme.com", "high", True, True, False)
    )
    assert allowed


def test_guess_and_duplicate_are_blocked() -> None:
    assert not automatic_email_allowed(
        ContactPolicyInput("guess@gmail.com", "acme.com", "low", False, True, False)
    )[0]
    assert not automatic_email_allowed(
        ContactPolicyInput("person@acme.com", "acme.com", "high", True, True, True)
    )[0]
