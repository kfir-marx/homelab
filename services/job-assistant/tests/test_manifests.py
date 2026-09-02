from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
JOB_ROOT = REPOSITORY / "kubernetes/system/job-assistant"


def test_reminder_cronjob_is_database_only_and_non_privileged() -> None:
    documents = list(yaml.safe_load_all((JOB_ROOT / "discovery.yaml").read_text()))
    reminder = next(
        item for item in documents if item["metadata"]["name"] == "job-assistant-reminders"
    )
    pod = reminder["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    assert container["args"] == ["reminders"]
    assert {item["name"] for item in container["env"]} == {"JOB_ASSISTANT_DATABASE_URL"}
    assert "volumeMounts" not in container


def test_network_policy_only_adds_reminders_to_postgres_flow() -> None:
    policy = (JOB_ROOT / "network-policy.yaml").read_text()
    assert (
        "values: [api, worker, generation-broker, discovery, reminders, migrate, backup]" in policy
    )
    gateway_policy = (
        REPOSITORY / "kubernetes/system/shared-services-telegram/network-policy.yaml"
    ).read_text()
    for forbidden in ("postgres", "nfs", "external-ai", "smtp", "imap"):
        assert forbidden not in gateway_policy
