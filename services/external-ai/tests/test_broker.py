from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Never

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from external_ai.api import create_app
from external_ai.broker import claim_fair, submit
from external_ai.config import Settings, resolve_model
from external_ai.database import initialize, make_engine, make_factory
from external_ai.models import Job
from external_ai.worker import ExecutionFailure, _classify, command_for, execute, process_one


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}"),
        homelab_assistant_token=SecretStr("homelab-token"),  # noqa: S106
        job_assistant_token=SecretStr("job-token"),  # noqa: S106
        work_root=tmp_path,
        codex_home=tmp_path / "codex",
    )


def body(requester: str = "homelab-assistant") -> dict[str, object]:
    return {
        "requester": requester,
        "idempotency_key": "idempotency-123",
        "prompt": "untrusted prompt",
        "model": "sol",
        "reasoning_effort": "high",
    }


def test_model_alias_and_no_fallback() -> None:
    assert resolve_model("sol", "max") == ("gpt-5.6-sol", "max")
    try:
        resolve_model("anything", "high")
    except ValueError as exc:
        assert "allowlisted" in str(exc)
    else:
        raise AssertionError("unknown model must fail closed")


def test_api_is_scoped_and_idempotent(tmp_path: Path) -> None:
    client = TestClient(create_app(settings(tmp_path)))
    headers = {"Authorization": "Bearer homelab-token"}
    first = client.post("/v1/jobs", json=body(), headers=headers)
    second = client.post("/v1/jobs", json=body(), headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    changed = body()
    changed["prompt"] = "different"
    assert client.post("/v1/jobs", json=changed, headers=headers).status_code == 409
    assert client.post("/v1/jobs", json=body("job-assistant"), headers=headers).status_code == 403
    job_id = first.json()["job_id"]
    other_headers = {"Authorization": "Bearer job-token"}
    assert client.get(f"/v1/jobs/{job_id}", headers=other_headers).status_code == 404
    cancelled = client.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert cancelled.json()["status"] == "cancelled"


def test_fair_claim_alternates_requesters(tmp_path: Path) -> None:
    engine = make_engine(settings(tmp_path))
    initialize(engine)
    factory = make_factory(engine)
    with factory.begin() as session:
        for requester in ("homelab-assistant", "homelab-assistant", "job-assistant"):
            submit(
                session,
                requester=requester,
                idempotency_key=f"key-{requester}-{len(session.new)}",
                prompt="p",
                model="gpt-5.6-sol",
                reasoning="high",
                output_schema=None,
                timeout_seconds=60,
                correlation={},
            )
    with factory.begin() as session:
        first = claim_fair(session)
        assert first and first.requester == "homelab-assistant"
        first.status = "completed"
    with factory.begin() as session:
        second = claim_fair(session)
        assert second and second.requester == "job-assistant"


def test_fake_codex_receives_canonical_model_and_reasoning(tmp_path: Path) -> None:
    job = Job(
        public_id="X1234567",
        requester="homelab-assistant",
        idempotency_key="key",
        prompt="p",
        model="gpt-5.6-sol",
        reasoning="xhigh",
        timeout_seconds=60,
        correlation={},
    )
    command = command_for(job, tmp_path / "schema.json", tmp_path / "result.json")
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="xhigh"' in command
    assert "--sandbox" in command and "read-only" in command

    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'args_file="$(dirname "$0")/args.txt"\n'
        ': > "$args_file"\n'
        "output_path=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  printf "%s\\n" "$1" >> "$args_file"\n'
        '  if [ "$1" = "--output-last-message" ]; then\n'
        '    shift; output_path="$1"; printf "%s\\n" "$1" >> "$args_file"\n'
        "  fi\n"
        "  shift\n"
        "done\n"
        'test -n "$output_path"\n'
        'printf "%s" "fake result" > "$output_path"\n'
        'printf "%s\\n" \'{"type":"turn.completed","usage":{"input_tokens":7}}\'\n',
        encoding="utf-8",
    )
    os.chmod(executable, 0o700)
    configured = settings(tmp_path).model_copy(update={"codex_executable": str(executable)})
    result, usage = execute(job, configured)
    received = (tmp_path / "args.txt").read_text(encoding="utf-8").splitlines()
    assert result == "fake result" and usage["input_tokens"] == 7
    assert "gpt-5.6-sol" in received
    assert 'model_reasoning_effort="xhigh"' in received


def test_failure_classification() -> None:
    assert _classify("401 unauthorized").code == "authentication"
    assert _classify("429 usage limit").code == "usage_limit"
    assert _classify("ordinary failure").code == "execution_failed"


def test_subprocess_timeout_is_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(
        public_id="X1234567",
        requester="homelab-assistant",
        idempotency_key="timeout-key",
        prompt="p",
        model="gpt-5.6-sol",
        reasoning="high",
        timeout_seconds=30,
        correlation={},
    )

    def timeout(*args: object, **kwargs: object) -> Never:
        raise subprocess.TimeoutExpired("codex", 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ExecutionFailure) as caught:
        execute(job, settings(tmp_path))
    assert caught.value.code == "timeout" and caught.value.retryable


def test_retry_reuses_the_durable_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = settings(tmp_path)
    engine = make_engine(configured)
    initialize(engine)
    factory = make_factory(engine)
    with factory.begin() as session:
        submitted = submit(
            session,
            requester="job-assistant",
            idempotency_key="retry-key",
            prompt="p",
            model="gpt-5.6-sol",
            reasoning="high",
            output_schema=None,
            timeout_seconds=60,
            correlation={},
        )
        job_id = submitted.id

    calls = 0

    def fake_execute(job: Job, current: Settings) -> tuple[str, dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExecutionFailure("usage_limit", True)
        return "completed", {"input_tokens": 3}

    monkeypatch.setattr("external_ai.worker.execute", fake_execute)
    assert process_one(factory, configured)
    with factory() as session:
        first = session.get(Job, job_id)
        assert first and first.status == "queued" and first.attempts == 1
    assert process_one(factory, configured)
    with factory() as session:
        completed = session.get(Job, job_id)
        assert completed and completed.status == "completed"
        assert completed.attempts == 2 and completed.result == "completed"


def test_worker_manifest_has_no_client_or_cluster_credentials() -> None:
    repository = Path(__file__).resolve().parents[3]
    manifest = (repository / "kubernetes/system/external-ai/workloads.yaml").read_text()
    worker = manifest.split("name: external-ai-worker", 1)[1]
    assert "replicas: 1" in worker
    worker = worker.split("volumes:", 1)[0]
    for forbidden in (
        "TELEGRAM",
        "SMTP",
        "HOMELAB_ASSISTANT_TOKEN",
        "JOB_ASSISTANT_TOKEN",
        "KUBECONFIG",
    ):
        assert forbidden not in worker
