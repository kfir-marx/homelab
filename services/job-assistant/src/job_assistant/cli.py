from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer
import uvicorn

from .api import create_app
from .artifacts import FilesystemArtifactStorage
from .config import Settings
from .database import make_engine, make_session_factory
from .database_roles import provision_generation_role
from .discovery import run_discovery
from .logging import configure_logging
from .telegram import TelegramHttpProvider, TelegramUpdateHandler, run_long_polling
from .workers import run_general_worker, run_generation_worker

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def _runtime() -> tuple[Settings, object]:
    settings = Settings()
    configure_logging(settings.log_level)
    return settings, make_session_factory(make_engine(settings))


@app.command()
def api() -> None:
    """Run the private health/metrics API."""
    settings = Settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings), host=settings.api_host, port=settings.api_port, access_log=False
    )


@app.command()
def telegram() -> None:
    """Run the allowlisted Telegram bot using long polling."""
    settings, factory = _runtime()
    if not settings.telegram_token:
        raise typer.BadParameter("JOB_ASSISTANT_TELEGRAM_TOKEN is required")
    handler = TelegramUpdateHandler(settings, FilesystemArtifactStorage(settings.artifact_root))
    provider = TelegramHttpProvider(settings.telegram_token.get_secret_value())
    run_long_polling(factory, handler, provider)  # type: ignore[arg-type]


@app.command()
def worker() -> None:
    """Run queue, Telegram-outbox, and email-delivery work."""
    settings, factory = _runtime()
    run_general_worker(factory, settings)  # type: ignore[arg-type]


@app.command()
def generation_worker() -> None:
    """Run isolated Codex generation work (Kubernetes role: generation)."""
    settings, factory = _runtime()
    run_generation_worker(factory, settings)  # type: ignore[arg-type]


@app.command()
def discover() -> None:
    """Run one scheduled discovery and recommendation pass."""
    settings, factory = _runtime()
    with factory.begin() as session:  # type: ignore[attr-defined]
        count = run_discovery(session, settings)
    typer.echo(f"queued {count} recommendation(s)")


@app.command()
def migrate() -> None:
    """Apply database migrations."""
    settings = Settings()
    service_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["JOB_ASSISTANT_DATABASE_URL"] = settings.database_url.get_secret_value()
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(service_root / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=service_root,
        env=environment,
        check=False,
    )
    if result.returncode:
        raise typer.Exit(result.returncode)
    if not settings.generation_database_url:
        raise typer.BadParameter("JOB_ASSISTANT_GENERATION_DATABASE_URL is required for migration")
    provision_generation_role(settings.database_url, settings.generation_database_url)


if __name__ == "__main__":
    app()
