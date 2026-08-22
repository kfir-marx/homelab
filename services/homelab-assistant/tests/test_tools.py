from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx

from homelab_assistant.clients import LlmClient
from homelab_assistant.skills import load_skills
from homelab_assistant.tools import (
    AssistantTools,
    HandoffRequest,
    KubernetesClient,
    explicitly_requests_handoff,
)


class StubKubernetes:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(
        self,
        path: str,
        *,
        label_selector: str = "",
        field_selector: str = "",
        limit: int = 50,
    ) -> str:
        self.paths.append(path)
        return json.dumps({"path": path, "limit": limit})

    def pod_logs(
        self,
        namespace: str,
        pod: str,
        *,
        container: str = "",
        tail_lines: int = 100,
        previous: bool = False,
    ) -> str:
        return f"{namespace}/{pod} previous={previous}"


def test_handoff_requires_an_explicit_positive_current_prompt() -> None:
    assert explicitly_requests_handoff("Please hand this session over to external AI")
    assert explicitly_requests_handoff("Consult Codex for this diagnosis")
    assert not explicitly_requests_handoff("Do not hand this over to external AI")
    assert not explicitly_requests_handoff("Do not ever send this to external AI")
    assert not explicitly_requests_handoff("I am not asking you to use external AI")
    assert not explicitly_requests_handoff("The old transcript mentioned external AI")

    tools = AssistantTools(cast(KubernetesClient, StubKubernetes()), external_ai_enabled=True)
    rejected = tools.execute(
        "request_external_ai_handover",
        {"model": "gpt-5.6-sol", "reasoning": "high"},
        "Diagnose the cluster locally",
    )
    assert rejected.handoff is None and "rejected" in rejected.content
    accepted = tools.execute(
        "request_external_ai_handover",
        {"model": "gpt-5.6-sol", "reasoning": "max"},
        "Escalate this to external-ai",
    )
    assert accepted.handoff == HandoffRequest("gpt-5.6-sol", "max")


def test_kubernetes_client_is_get_only_bounded_and_redacts_secrets(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("projected-token", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/healthz":
            return httpx.Response(200, text="ok")
        if request.url.path == "/api/v1/secrets":
            return httpx.Response(
                200,
                json={
                    "kind": "SecretList",
                    "items": [{"metadata": {"name": "listed"}, "data": {"key": "dmFsdWU="}}],
                },
            )
        return httpx.Response(
            200,
            json={
                "kind": "Secret",
                "metadata": {"name": "private", "managedFields": [{"manager": "test"}]},
                "data": {"password": "c2VjcmV0"},
            },
        )

    client = KubernetesClient(
        "https://kubernetes.test",
        str(token),
        str(tmp_path / "unused-ca"),
        maximum_result_chars=1000,
        verify=False,
    )
    client._client = httpx.Client(  # noqa: SLF001
        base_url="https://kubernetes.test", transport=httpx.MockTransport(handler)
    )
    result = client.get("/api/v1/namespaces/test/secrets/private", limit=500)
    assert "c2VjcmV0" not in result and '"data": "[redacted]"' in result
    assert requests[0].headers["Authorization"] == "Bearer projected-token"
    assert requests[0].url.params["limit"] == "100"
    assert client.get("/healthz") == "ok"
    assert "dmFsdWU=" not in client.get("/api/v1/secrets")

    for unsafe in (
        "/api/v1/namespaces/test/pods/p/exec",
        "/api/v1/namespaces/test/pods/p/proxy",
        "/api/v1/pods?watch=true",
    ):
        try:
            client.get(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path was accepted: {unsafe}")


def test_llm_runs_read_tools_and_returns_a_handoff_signal() -> None:
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "kubernetes_get",
                                        "arguments": '{"path":"/api/v1/nodes"}',
                                    },
                                },
                                {
                                    "id": "call-2",
                                    "type": "function",
                                    "function": {
                                        "name": "request_external_ai_handover",
                                        "arguments": ('{"model":"gpt-5.6-sol","reasoning":"high"}'),
                                    },
                                },
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
            {
                "choices": [{"message": {"role": "assistant", "content": "Preparing preview."}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 3},
            },
        ]
    )
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json=next(responses))

    kubernetes = StubKubernetes()
    client = LlmClient(
        "http://llm.test/v1",
        "test-key",
        "test-model",
        10,
        AssistantTools(cast(KubernetesClient, kubernetes), external_ai_enabled=True),
    )
    client._client = httpx.Client(  # noqa: SLF001
        base_url="http://llm.test/v1", transport=httpx.MockTransport(handler)
    )
    completion = client.complete_with_tools(
        [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "Inspect nodes and hand over to external AI"},
        ],
        100,
        "Inspect nodes and hand over to external AI",
    )
    assert kubernetes.paths == ["/api/v1/nodes"]
    assert completion.handoff == HandoffRequest("gpt-5.6-sol", "high")
    assert completion.completion_tokens == 7
    assert request_bodies[0]["tool_choice"] == "auto"
    assert any(
        message.get("role") == "tool"
        for message in cast(list[dict[str, object]], request_bodies[1]["messages"])
    )


def test_packaged_skills_load_without_frontmatter(tmp_path: Path) -> None:
    skill = tmp_path / "diagnose" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: diagnose\ndescription: Test.\n---\n\n# Instructions\nRead.")
    loaded = load_skills(str(tmp_path))
    assert "Skill: diagnose" in loaded and "description: Test" not in loaded
