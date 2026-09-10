from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_api import FakeExtractor, booking_payload, register, settings

from tapy.api import create_app
from tapy.database import (
    AuthIdentity,
    MailboxConnection,
    Organization,
    OrganizationAudit,
    OrganizationInvitation,
    OrganizationMembership,
    User,
    token_hash,
)
from tapy.oauth import OAuthError, OAuthService, OAuthTokens
from tapy.organizations import accept_invitation, change_member


def invitation(client, email="guest@example.com", role="agent"):
    result = client.post(
        "/v1/organizations/current/invitations", json={"email": email, "role": role}
    )
    assert result.status_code == 201, result.text
    return result.json()["id"], result.json()["invitation_path"].split("=")[1]


def test_invitation_lifecycle_and_no_membership(tmp_path):
    app = create_app(settings(tmp_path), extractor=FakeExtractor())
    with TestClient(app) as client:
        admin = register(client, "Admin", "admin@example.com")
        admin_cookie = client.cookies.get("tapy_session")
        for path, body in [
            (
                "/v1/auth/register",
                {"name": "Public", "email": "p@example.com", "password": "long-password"},
            ),
            ("/v1/agents", {}),
            ("/v1/organizations", {"name": "Forbidden"}),
            ("/v1/organizations/current/memberships", {"email": "guest@example.com"}),
        ]:
            assert client.post(path, json=body).status_code == 403
        old_id, old = invitation(client)
        _, token = invitation(client, "GUEST@example.com")
        client.cookies.clear()
        assert (
            client.post(
                "/v1/invitations/accept",
                json={"token": old, "name": "Guest", "password": "long-password"},
            ).status_code
            == 410
        )
        response = client.post(
            "/v1/invitations/accept",
            json={"token": token, "name": "Guest", "password": "long-password"},
        )
        assert response.status_code == 200, response.text
        guest = response.json()["user"]
        assert guest["active_organization_id"] == admin["active_organization_id"]
        assert guest["active_organization_role"] == "agent"
        guest_cookie = client.cookies.get("tapy_session")
        assert client.post("/v1/invitations/accept", json={"token": token}).status_code == 410
        assert client.get("/v1/organizations/current/team").status_code == 403
        assert (
            client.post(
                "/v1/organizations/current/invitations", json={"email": "no@example.com"}
            ).status_code
            == 403
        )
        own = client.post("/v1/bookings", json=booking_payload()).json()
        assert (
            client.patch(
                f"/v1/bookings/{own['id']}", json={"assigned_agent_id": admin["id"]}
            ).status_code
            == 403
        )
        client.cookies.set("tapy_session", admin_cookie)
        assert (
            client.patch(
                f"/v1/organizations/current/memberships/{admin['id']}",
                json={"role": "agent", "status": "active"},
            ).status_code
            == 409
        )
        assert (
            client.patch(
                f"/v1/organizations/current/memberships/{guest['id']}",
                json={"role": "agent", "status": "inactive"},
            ).status_code
            == 204
        )
        assert (
            client.patch(
                f"/v1/organizations/current/memberships/{guest['id']}",
                json={"role": "agent", "status": "active"},
            ).status_code
            == 409
        )
        assert client.get(f"/v1/bookings/{own['id']}").status_code == 200
        client.cookies.set("tapy_session", guest_cookie)
        me = client.get("/v1/users/me").json()
        assert me["invitation_required"] and me["active_organization_id"] is None
        for path in (
            "/v1/bookings",
            "/v1/opportunities",
            "/v1/jobs",
            "/v1/notifications",
            "/v1/metrics",
        ):
            assert client.get(path).status_code in (403, 409)
        with app.state.factory() as session:
            assert session.scalar(select(func.count()).select_from(Organization)) == 1
            assert session.get(OrganizationInvitation, old_id).revoked_at
            assert session.scalar(
                select(OrganizationInvitation).where(
                    OrganizationInvitation.token_hash == token_hash(token)
                )
            )
            assert token not in str([a.details for a in session.scalars(select(OrganizationAudit))])
        client.cookies.set("tapy_session", admin_cookie)
        _, fresh = invitation(client)
        client.cookies.set("tapy_session", guest_cookie)
        assert client.post("/v1/invitations/accept", json={"token": fresh}).status_code == 200
        assert client.get(f"/v1/bookings/{own['id']}").status_code == 200


def test_existing_account_matching_expiry_revocation_and_tenant_scope(tmp_path):
    app = create_app(settings(tmp_path), extractor=FakeExtractor())
    with TestClient(app) as client:
        first = register(client, "First", "first@example.com")
        first_cookie = client.cookies.get("tapy_session")
        invitation_id, token = invitation(client, "second@example.com", "admin")
        client.cookies.clear()
        second = register(client, "Second", "second@example.com")
        second_cookie = client.cookies.get("tapy_session")
        assert (
            client.delete(f"/v1/organizations/current/invitations/{invitation_id}").status_code
            == 404
        )
        client.cookies.set("tapy_session", first_cookie)
        assert client.post("/v1/invitations/accept", json={"token": token}).status_code == 401
        client.cookies.clear()
        assert (
            client.post(
                "/v1/invitations/accept",
                json={"token": token, "name": "Hijack", "password": "long-password"},
            ).status_code
            == 401
        )
        client.cookies.set("tapy_session", second_cookie)
        result = client.post("/v1/invitations/accept", json={"token": token})
        assert result.status_code == 200
        assert len(result.json()["user"]["memberships"]) == 2
        assert result.json()["user"]["active_organization_id"] == first["active_organization_id"]
        assert (
            client.patch(
                "/v1/users/me", json={"active_organization_id": second["active_organization_id"]}
            ).status_code
            == 200
        )
        client.cookies.set("tapy_session", first_cookie)
        expired_id, expired = invitation(client, "expired@example.com")
        with app.state.factory.begin() as session:
            session.get(OrganizationInvitation, expired_id).expires_at = datetime.now(
                UTC
            ) - timedelta(seconds=1)
        revoked_id, revoked = invitation(client, "revoked@example.com")
        assert (
            client.delete(f"/v1/organizations/current/invitations/{revoked_id}").status_code == 204
        )
        client.cookies.clear()
        for value in (expired, revoked):
            assert (
                client.post(
                    "/v1/invitations/accept",
                    json={"token": value, "name": "No", "password": "long-password"},
                ).status_code
                == 410
            )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_concurrent_acceptance_and_last_admin(tmp_path, backend):
    import os

    from pydantic import SecretStr

    configured = settings(tmp_path)
    if backend == "postgres":
        url = os.environ.get("TAPY_ONBOARDING_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires a disposable PostgreSQL database")
        configured = configured.model_copy(update={"database_url": SecretStr(url)})
    app = create_app(configured, extractor=FakeExtractor())
    with TestClient(app) as client:
        admin = register(client, "Admin", "admin@example.com")
        _, token = invitation(client, role="admin")
        barrier = Barrier(2)

        def accept(_):
            barrier.wait()
            try:
                with app.state.factory.begin() as session:
                    return accept_invitation(session, token, None, "Guest", "long-password").id
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, range(2)))
        assert results.count(410) == 1
        guest_id = next(r for r in results if isinstance(r, str))
        barrier = Barrier(2)

        def demote(user_id):
            barrier.wait()
            try:
                with app.state.factory.begin() as session:
                    change_member(
                        session,
                        admin["active_organization_id"],
                        user_id,
                        user_id,
                        "agent",
                        "active",
                    )
                return 204
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(demote, (admin["id"], guest_id))) == [204, 409]
        with app.state.factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OrganizationMembership)
                    .where(
                        OrganizationMembership.role == "admin",
                        OrganizationMembership.status == "active",
                    )
                )
                == 1
            )


@pytest.mark.asyncio
async def test_social_login_never_creates_or_links_by_email(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path), extractor=FakeExtractor())
    with app.state.factory.begin() as session:
        user = User(email="known@example.com", name="Known")
        session.add(user)
        session.flush()
        user_id = user.id
    async with httpx.AsyncClient() as client:
        oauth = OAuthService(settings(tmp_path), app.state.factory, client)
        monkeypatch.setattr(oauth, "_consume_state", lambda *args: (None, None))

        async def exchange(*args):
            return OAuthTokens("inert", None, "openid")

        async def profile(*args):
            return ("subject", "known@example.com", "Known")

        monkeypatch.setattr(oauth, "_exchange", exchange)
        monkeypatch.setattr(oauth, "_profile", profile)
        with pytest.raises(OAuthError, match="invitation required"):
            await oauth.complete_login("google", "state", "code")
        with app.state.factory.begin() as session:
            assert session.scalar(select(func.count()).select_from(User)) == 1
            assert session.scalar(select(func.count()).select_from(AuthIdentity)) == 0
            session.add(AuthIdentity(user_id=user_id, provider="google", subject="subject"))
        completion = await oauth.complete_login("google", "state", "code")
        assert completion.user.id == user_id and not completion.created
        with app.state.factory() as session:
            assert session.scalar(select(func.count()).select_from(Organization)) == 0


@pytest.mark.asyncio
async def test_mailbox_connection_binds_original_context_and_rejects_reassignment(
    tmp_path, monkeypatch
):
    from urllib.parse import parse_qs, urlparse

    from pydantic import SecretStr

    configured = settings(tmp_path).model_copy(
        update={
            "google_oauth_client_id": "local",
            "google_oauth_client_secret": SecretStr("inert"),
            "oauth_token_encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    app = create_app(configured, extractor=FakeExtractor())
    with TestClient(app) as client:
        user = register(client, "User", "user@example.com")
        async with httpx.AsyncClient() as http:
            oauth = OAuthService(configured, app.state.factory, http)
            state = parse_qs(urlparse(oauth.authorization_url(user["id"], "gmail")).query)["state"][
                0
            ]
            with app.state.factory.begin() as session:
                other = Organization(name="Other", slug="other")
                session.add(other)
                session.flush()
                session.add(
                    OrganizationMembership(
                        organization_id=other.id, user_id=user["id"], role="admin"
                    )
                )
                session.get(User, user["id"]).active_organization_id = other.id

            async def exchange(*args):
                return OAuthTokens("inert", "refresh", "mail")

            async def profile(*args):
                return ("mail-account", "mail@example.com", "User")

            monkeypatch.setattr(oauth, "_exchange", exchange)
            monkeypatch.setattr(oauth, "_profile", profile)
            mailbox = await oauth.complete("gmail", state, "code")
            assert mailbox.organization_id == user["active_organization_id"]
            state = parse_qs(urlparse(oauth.authorization_url(user["id"], "gmail")).query)["state"][
                0
            ]
            with pytest.raises(OAuthError, match="binding"):
                await oauth.complete("gmail", state, "code")
            with app.state.factory.begin() as session:
                session.get(User, user["id"]).active_organization_id = user[
                    "active_organization_id"
                ]
            state = parse_qs(urlparse(oauth.authorization_url(user["id"], "gmail")).query)["state"][
                0
            ]
            with app.state.factory.begin() as session:
                session.get(
                    OrganizationMembership, (user["active_organization_id"], user["id"])
                ).status = "inactive"
            with pytest.raises(OAuthError, match="membership"):
                await oauth.complete("gmail", state, "code")
            with pytest.raises(OAuthError, match="inactive"):
                await oauth.access_token(mailbox)


@pytest.mark.asyncio
async def test_unbound_and_deactivated_jobs_fail_before_external_work(tmp_path):
    from tapy.database import BackgroundJob

    app = create_app(settings(tmp_path), extractor=FakeExtractor())
    with TestClient(app) as client:
        user = register(client, "User", "user@example.com")
        with app.state.factory.begin() as session:
            mailbox = MailboxConnection(
                user_id=user["id"],
                provider="gmail",
                provider_account_id="mail",
                email_address=user["email"],
                refresh_token="inert",  # noqa: S106 - disposable fixture
                scopes="mail",
            )
            session.add(mailbox)
            session.flush()
            job = BackgroundJob(
                user_id=user["id"],
                organization_id=user["active_organization_id"],
                kind="scan",
                idempotency_key="test",
                payload={"mailbox_id": mailbox.id},
            )
            session.add(job)
            session.flush()
        assert client.post("/v1/scans", json={"provider": "gmail"}).status_code == 409
        with pytest.raises(ValueError, match="disconnected"):
            await app.state.run_job(job)
        with app.state.factory.begin() as session:
            session.get(MailboxConnection, mailbox.id).organization_id = user[
                "active_organization_id"
            ]
            session.get(
                OrganizationMembership, (user["active_organization_id"], user["id"])
            ).status = "inactive"
        with pytest.raises(ValueError, match="membership"):
            await app.state.run_job(job)
