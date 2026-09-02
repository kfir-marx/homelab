from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
GATEWAY_ROOT = REPOSITORY / "kubernetes/system/shared-services-telegram"


def test_gateway_has_no_service_account_storage_or_forbidden_credentials() -> None:
    documents = list(yaml.safe_load_all((GATEWAY_ROOT / "workload.yaml").read_text()))
    deployment = next(item for item in documents if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    assert "volumes" not in pod
    container = pod["containers"][0]
    names = {item["name"] for item in container["env"]}
    assert names == {
        "SHARED_SERVICES_TELEGRAM_TELEGRAM_TOKEN",
        "SHARED_SERVICES_TELEGRAM_JOB_ASSISTANT_API_TOKEN",
        "SHARED_SERVICES_TELEGRAM_JOB_ASSISTANT_NOTIFICATION_TOKEN",
        "SHARED_SERVICES_TELEGRAM_JOB_ASSISTANT_BASE_URL",
    }
    rendered = (GATEWAY_ROOT / "workload.yaml").read_text().casefold()
    for forbidden in ("smtp", "imap", "postgres", "external_ai", "openai", "codex", "nfs"):
        assert forbidden not in rendered


def test_shared_bot_token_occurs_in_only_the_gateway_kubernetes_manifest() -> None:
    matches = []
    for path in (REPOSITORY / "kubernetes").rglob("*.yaml"):
        if "TELEGRAM_TOKEN" in path.read_text():
            matches.append(path.relative_to(REPOSITORY).as_posix())
    assert matches == ["kubernetes/system/shared-services-telegram/workload.yaml"]


def test_gateway_network_policy_names_only_required_destinations() -> None:
    policy = (GATEWAY_ROOT / "network-policy.yaml").read_text()
    assert "api.telegram.org" in policy
    assert "kubernetes.io/metadata.name: job-assistant" in policy
    assert "component: api" in policy
    for forbidden in (
        "external-ai",
        "homelab-assistant",
        "smtp",
        "imap",
        "postgres",
        "192.168.",
        "0.0.0.0/0",
    ):
        assert forbidden not in policy
