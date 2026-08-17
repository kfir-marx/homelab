from job_assistant.sources.linkedin_email import parse_linkedin_alert


def test_linkedin_official_alert_fixture_is_isolated() -> None:
    raw = b"""From: LinkedIn Jobs <jobs-noreply@linkedin.com>\r
Subject: New DevOps jobs\r
Message-ID: <fixture@example>\r
MIME-Version: 1.0\r
Content-Type: text/html; charset=utf-8\r
\r
<html><body><a href=\"https://www.linkedin.com/jobs/view/devops-engineer-1234567890?trackingId=x\">
DevOps Engineer</a></body></html>
"""
    jobs = parse_linkedin_alert(raw)
    assert len(jobs) == 1
    assert jobs[0].external_job_id == "1234567890"
    assert "trackingId" not in jobs[0].canonical_url


def test_non_linkedin_sender_is_ignored() -> None:
    assert (
        parse_linkedin_alert(
            b"From: attacker@example.com\r\n\r\nhttps://linkedin.com/jobs/view/a-123"
        )
        == []
    )
