from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .broker import claim_fair
from .config import Settings
from .models import Job


class ExecutionFailure(RuntimeError):
    def __init__(self, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _usage(stdout: str) -> dict[str, int | float]:
    usage: dict[str, int | float] = {}
    for line in stdout.splitlines()[-500:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = event.get("usage") if isinstance(event, dict) else None
        if isinstance(raw, dict):
            usage = {
                str(key)[:80]: value
                for key, value in raw.items()
                if isinstance(value, (int, float))
            }
    return usage


def _classify(output: str) -> ExecutionFailure:
    folded = output.casefold()
    if any(
        value in folded
        for value in ("401", "unauthorized", "login required", "not logged in", "authentication")
    ):
        return ExecutionFailure("authentication", False)
    if any(value in folded for value in ("429", "rate limit", "usage limit", "quota")):
        return ExecutionFailure("usage_limit", True)
    return ExecutionFailure("execution_failed", False)


def command_for(job: Job, schema_path: Path | None, result_path: Path) -> list[str]:
    command = [
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--json",
        "--model",
        job.model,
        "--config",
        f'model_reasoning_effort="{job.reasoning}"',
        "--config",
        'web_search="disabled"',
    ]
    if schema_path:
        command.extend(["--output-schema", str(schema_path)])
    command.extend(["--output-last-message", str(result_path), "-"])
    return command


def execute(job: Job, settings: Settings) -> tuple[str, dict[str, Any]]:
    settings.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=settings.work_root) as temporary:
        root = Path(temporary)
        result_path = root / "result.txt"
        schema_path = root / "schema.json" if job.output_schema else None
        if schema_path:
            schema_path.write_text(json.dumps(job.output_schema), encoding="utf-8")
        argv = [settings.codex_executable, *command_for(job, schema_path, result_path)]
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(settings.codex_home),
            "CODEX_HOME": str(settings.codex_home),
            "LANG": "C.UTF-8",
        }
        try:
            process = subprocess.run(  # noqa: S603 - fixed argument vector, never a shell
                argv,
                input=job.prompt,
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                timeout=job.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionFailure("timeout", True) from exc
        usage = _usage(process.stdout)
        if process.returncode:
            raise _classify(process.stdout[-8000:] + process.stderr[-8000:])
        if not result_path.is_file():
            raise ExecutionFailure("missing_output", False)
        return result_path.read_text(encoding="utf-8"), usage


def process_one(factory: sessionmaker[Session], settings: Settings) -> bool:
    with factory.begin() as session:
        job = claim_fair(session)
    if not job:
        return False
    try:
        result, usage = execute(job, settings)
        with factory.begin() as session:
            current = session.get(Job, job.id)
            assert current
            if current.status == "cancel_requested":
                current.status = "cancelled"
                current.result = None
            else:
                current.status = "completed"
                current.result = result
                current.usage = usage
            current.finished_at = datetime.now(UTC)
    except ExecutionFailure as exc:
        with factory.begin() as session:
            current = session.get(Job, job.id)
            assert current
            current.error_code = exc.code
            current.finished_at = datetime.now(UTC)
            current.status = (
                "queued" if exc.retryable and current.attempts < current.max_attempts else "failed"
            )
    return True


def run(factory: sessionmaker[Session], settings: Settings) -> None:
    while True:
        if not process_one(factory, settings):
            time.sleep(settings.poll_seconds)
