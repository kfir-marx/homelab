import asyncio

import typer
import uvicorn

from .config import Settings
from .database import make_engine
from .migrations import upgrade_database

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run the Tapy API."""


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080) -> None:  # noqa: S104
    uvicorn.run(
        "tapy.api:create_app",
        host=host,
        port=port,
        factory=True,
        access_log=False,
        server_header=False,
    )


@app.command()
def migrate() -> None:
    """Upgrade the configured database to the latest schema revision."""
    engine = make_engine(Settings())
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()


@app.command()
def worker() -> None:
    """Run RabbitMQ jobs independently of the HTTP server."""
    from .api import create_app

    async def run() -> None:
        application = create_app(worker=True)
        async with application.router.lifespan_context(application):
            await asyncio.gather(*application.state.worker_tasks)

    asyncio.run(run())


if __name__ == "__main__":
    app()


@app.command()
def inventory() -> None:
    """Read-only organization/member/mailbox ownership inventory (no credentials)."""
    import json

    from sqlalchemy import MetaData, Table, select

    from .database import (
        EmailBookingEvent,
        IngestionSource,
        Organization,
        OrganizationMembership,
        ProcessedMessage,
        make_factory,
    )

    engine = make_engine(Settings())
    try:
        with make_factory(engine)() as session:
            organizations = [
                {"id": o.id, "name": o.name, "slug": o.slug}
                for o in session.scalars(select(Organization))
            ]
            memberships = [
                {
                    "organization_id": m.organization_id,
                    "user_id": m.user_id,
                    "role": m.role,
                    "status": m.status,
                }
                for m in session.scalars(select(OrganizationMembership))
            ]
            mailboxes = []
            # Reflection keeps this read-only command usable before the ownership migration.
            mailbox_table = Table("tapy_mailboxes", MetaData(), autoload_with=session.connection())
            for row in session.execute(select(mailbox_table)).mappings():
                from types import SimpleNamespace

                m = SimpleNamespace(
                    id=row["id"], user_id=row["user_id"], organization_id=row.get("organization_id")
                )
                evidence: set[str] = set()
                for model in (ProcessedMessage, IngestionSource, EmailBookingEvent):
                    evidence.update(
                        session.scalars(
                            select(model.organization_id).where(model.mailbox_id == m.id)
                        )
                    )
                mailboxes.append(
                    {
                        "id": m.id,
                        "user_id": m.user_id,
                        "organization_id": m.organization_id,
                        "evidence_organizations": sorted(evidence),
                        "review_required": not m.organization_id
                        or bool(evidence - {m.organization_id}),
                    }
                )
            typer.echo(
                json.dumps(
                    {
                        "organizations": organizations,
                        "memberships": memberships,
                        "mailboxes": mailboxes,
                    },
                    indent=2,
                )
            )
    finally:
        engine.dispose()


@app.command()
def provision_pilots(test_admin_email: str, lakish_admin_email: str, public_url: str) -> None:
    """Create stable pilot organizations; issue initial admin invitations once."""
    import uuid

    from pydantic import EmailStr, TypeAdapter
    from sqlalchemy import select

    from .database import Organization, OrganizationInvitation, OrganizationMembership, make_factory
    from .organizations import issue_invitation, lock_organization

    emails = [
        str(TypeAdapter(EmailStr).validate_python(e)).casefold()
        for e in (test_admin_email, lakish_admin_email)
    ]
    if not public_url.startswith("https://"):
        raise typer.BadParameter("public-url must be the HTTPS frontend origin")
    engine = make_engine(Settings())
    output = []
    try:
        with make_factory(engine).begin() as session:
            for name, email in zip(("Tapy-test", "Lakish-tours"), emails, strict=True):
                org_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://tapy.local/pilot/" + name))
                org = session.get(Organization, org_id)
                if not org:
                    org = Organization(id=org_id, name=name, slug="pilot-" + org_id)
                    session.add(org)
                    session.flush()
                lock_organization(session, org.id)
                admin = session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.organization_id == org.id,
                        OrganizationMembership.role == "admin",
                        OrganizationMembership.status == "active",
                    )
                )
                previous = session.scalar(
                    select(OrganizationInvitation).where(
                        OrganizationInvitation.organization_id == org.id
                    )
                )
                if admin or previous:
                    output.append(
                        f"{name} {org.id}: already provisioned; use invite to reissue if needed"
                    )
                else:
                    _, token = issue_invitation(session, org.id, email, "admin", None)
                    output.append(f"{name} {org.id}: {public_url.rstrip('/')}/#invite={token}")
        for line in output:
            typer.echo(line)
    finally:
        engine.dispose()


@app.command("invite")
def operator_invite(organization_id: str, email: str, public_url: str, role: str = "admin") -> None:
    """Issue/reissue an invitation for an existing organization; prints secret link once."""
    from pydantic import EmailStr, TypeAdapter

    from .database import Organization, make_factory
    from .organizations import issue_invitation

    normalized = str(TypeAdapter(EmailStr).validate_python(email))
    if role not in ("admin", "agent") or not public_url.startswith("https://"):
        raise typer.BadParameter("role must be admin/agent and public-url must use HTTPS")
    engine = make_engine(Settings())
    try:
        with make_factory(engine).begin() as session:
            if not session.get(Organization, organization_id):
                raise typer.BadParameter("organization does not exist")
            _, token = issue_invitation(session, organization_id, normalized, role, None)
        typer.echo(f"{public_url.rstrip('/')}/#invite={token}")
    finally:
        engine.dispose()


@app.command()
def bind_mailbox(mailbox_id: str, organization_id: str) -> None:
    """Resolve an unbound mailbox after inventory review; never move conflicting history."""
    from sqlalchemy import select

    from .database import (
        EmailBookingEvent,
        IngestionSource,
        MailboxConnection,
        ProcessedMessage,
        make_factory,
    )
    from .organizations import audit, lock_organization, require_member

    engine = make_engine(Settings())
    try:
        with make_factory(engine).begin() as session:
            lock_organization(session, organization_id)
            mailbox = session.scalar(
                select(MailboxConnection)
                .where(MailboxConnection.id == mailbox_id)
                .with_for_update()
            )
            if not mailbox:
                raise typer.BadParameter("mailbox not found")
            if mailbox.organization_id and mailbox.organization_id != organization_id:
                raise typer.BadParameter("existing mailbox binding cannot be reassigned")
            require_member(session, organization_id, mailbox.user_id)
            evidence: set[str] = set()
            for model in (ProcessedMessage, IngestionSource, EmailBookingEvent):
                evidence.update(
                    session.scalars(
                        select(model.organization_id).where(model.mailbox_id == mailbox_id)
                    )
                )
            if evidence - {organization_id}:
                raise typer.BadParameter(
                    "conflicting history requires a separately reviewed recovery migration"
                )
            mailbox.organization_id = organization_id
            audit(session, organization_id, None, "mailbox.bound", mailbox_id)
        typer.echo("Mailbox binding recorded; history unchanged")
    finally:
        engine.dispose()
