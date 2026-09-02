from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import typer
import uvicorn
from sqlalchemy import select

from .api import create_app
from .config import Settings
from .database import make_engine, make_session_factory
from .database_roles import provision_generation_role
from .discovery import run_discovery
from .logging import configure_logging
from .models import User
from .reminders import queue_due_reminders
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
def worker() -> None:
    """Run domain queue and approved-delivery work."""
    settings, factory = _runtime()
    run_general_worker(factory, settings)  # type: ignore[arg-type]


@app.command()
def broker_worker() -> None:
    """Run the restricted external-ai generation queue consumer."""
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
def reminders() -> None:
    """Queue one idempotent pass of user-owned application reminders."""
    settings, factory = _runtime()
    with factory.begin() as session:  # type: ignore[attr-defined]
        count = queue_due_reminders(session)
    typer.echo(f"queued {count} reminder(s)")


@app.command()
def migrate() -> None:
    """Apply database migrations."""
    settings = Settings()
    configured_root = os.environ.get("JOB_ASSISTANT_MIGRATION_ROOT")
    service_root = Path(configured_root) if configured_root else Path(__file__).resolve().parents[2]
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


@app.command("user-enroll")
def user_enroll(
    telegram_user_id: int,
    display_name: str = "",
    username: str = "",
    owner: bool = False,
    reactivate: bool = False,
) -> None:
    """Enroll one immutable Telegram numeric identity; invited features default off."""
    if telegram_user_id <= 0:
        raise typer.BadParameter("telegram_user_id must be positive")
    _, factory = _runtime()
    with factory.begin() as session:  # type: ignore[attr-defined]
        user = session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is not None:
            if not reactivate or user.active:
                raise typer.BadParameter("identity already exists; use --reactivate when revoked")
            user.active = True
            user.revoked_at = None
            user.display_name = display_name or user.display_name
            user.username = username or user.username
        else:
            prefix = str(uuid.uuid4())
            session.add(
                User(
                    telegram_user_id=telegram_user_id,
                    active=True,
                    is_owner=owner,
                    display_name=display_name or None,
                    username=username or None,
                    generation_enabled=owner,
                    automated_delivery_enabled=False,
                    inventory_valid=False,
                    storage_prefix=prefix,
                    career_inventory_key=f"{prefix}/private/career-inventory.yaml",
                    cv_template_key=f"{prefix}/private/cv-template.docx",
                )
            )
    typer.echo("user enrollment stored")


@app.command("user-revoke")
def user_revoke(telegram_user_id: int) -> None:
    """Revoke an enrolled identity without deleting its retained data."""
    _, factory = _runtime()
    with factory.begin() as session:  # type: ignore[attr-defined]
        user = session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            raise typer.BadParameter("identity is not enrolled")
        user.active = False
        user.generation_enabled = False
        user.automated_delivery_enabled = False
        user.revoked_at = datetime.now(UTC)
    typer.echo("user revoked")


@app.command("user-features")
def user_features(
    telegram_user_id: int,
    generation: bool = False,
    automated_delivery: bool = False,
    review_email: str = "",
    smtp_from: str = "",
) -> None:
    """Set explicit per-user generation, review, and recruiter-delivery controls."""
    _, factory = _runtime()
    with factory.begin() as session:  # type: ignore[attr-defined]
        user = session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id, User.active.is_(True))
        )
        if user is None:
            raise typer.BadParameter("active identity is not enrolled")
        if automated_delivery and (not review_email or not smtp_from):
            raise typer.BadParameter(
                "automated delivery requires per-user review_email and smtp_from"
            )
        user.generation_enabled = generation
        user.automated_delivery_enabled = automated_delivery
        user.review_email = review_email or None
        user.smtp_from = smtp_from or None
    typer.echo("user feature controls updated")


@app.command("user-profile")
def user_profile(telegram_user_id: int, private_search_criteria: bool = False) -> None:
    """Select shared defaults or the user's fixed private search-criteria path."""
    _, factory = _runtime()
    with factory.begin() as session:  # type: ignore[attr-defined]
        user = session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id, User.active.is_(True))
        )
        if user is None:
            raise typer.BadParameter("active identity is not enrolled")
        user.search_criteria_key = (
            f"{user.storage_prefix}/private/search-criteria.yaml"
            if private_search_criteria
            else None
        )
    typer.echo("user profile selection updated")


if __name__ == "__main__":
    app()
