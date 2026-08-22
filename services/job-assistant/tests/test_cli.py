from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from job_assistant import cli


def test_migrate_uses_configured_migration_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("JOB_ASSISTANT_MIGRATION_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "JOB_ASSISTANT_DATABASE_URL",
        "postgresql+psycopg://job_assistant:test@postgres/job_assistant",
    )
    monkeypatch.setenv(
        "JOB_ASSISTANT_GENERATION_DATABASE_URL",
        "postgresql+psycopg://generation:test@postgres/job_assistant",
    )
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "provision_generation_role", lambda *_: None)

    cli.migrate()

    assert captured["cwd"] == tmp_path
    assert captured["command"] == [
        cli.sys.executable,
        "-m",
        "alembic",
        "-c",
        str(tmp_path / "alembic.ini"),
        "upgrade",
        "head",
    ]
