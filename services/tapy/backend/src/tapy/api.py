from __future__ import annotations

import asyncio
import base64
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Protocol, cast

import httpx
import structlog
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import Response as StarletteResponse

from .auth import bearer_token, new_session, password_matches
from .business import (
    booking_view,
    create_booking,
    ensure_agent,
    metric_values,
    normalized_contact,
    opportunity_view,
    stable_hash,
)
from .config import Settings
from .database import (
    AuthIdentity,
    BackgroundJob,
    BookingPerson,
    BookingPersonRole,
    ContactPoint,
    FlightReservation,
    FlightSegment,
    FlightTicket,
    MailboxConnection,
    MessageDelivery,
    NotificationRecord,
    OpportunityRecipient,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    ProcessedMessage,
    ReconciliationDecision,
    TicketSegment,
    TravelBooking,
    UpsellOpportunity,
    UpsellSendBatch,
    User,
    UserSession,
    make_engine,
    make_factory,
    token_hash,
)
from .jobs import JobFailure, JobQueue, job_view
from .llm import BookingExtractor
from .mailboxes import MailboxReader, readers
from .migrations import upgrade_database
from .models import (
    AgentCreated,
    AgentMetrics,
    AgentView,
    AuthorizationUrl,
    AuthProvider,
    AuthResult,
    BookingCreate,
    BookingUpdate,
    BookingView,
    ContactCreate,
    CurrencyMetrics,
    DeliveryView,
    EmailExtraction,
    EmailForAnalysis,
    InvitationAccept,
    JobView,
    LoginRequest,
    MailboxView,
    MembershipCreate,
    MembershipUpdate,
    MembershipView,
    MetricsView,
    NotificationsRead,
    NotificationView,
    OpportunityCreate,
    OpportunityUpdate,
    OpportunityView,
    OrganizationCreate,
    Provider,
    RecipientUpdate,
    RegisterRequest,
    RoleCreate,
    ScanRequest,
    Scope,
    SendBatchView,
    TicketCreate,
    UserUpdate,
    UserView,
)
from .oauth import OAuthError, OAuthService
from .organizations import (
    accept_invitation,
    audit,
    change_member,
    issue_invitation,
    lock_organization,
    require_member,
)
from .reconciliation import mailbox_organization, reconcile, record_email
from .webhooks import WebhookService, webhook_active

logger = structlog.get_logger()
SESSION_COOKIE = "tapy_session"

HOME_PAGE = """<!doctype html><html lang=en><meta charset=utf-8><title>Tapy</title>
<main><h1>Tapy</h1><p>Travel-agency booking and opportunity workspace.</p>
<p><a href=/privacy>Privacy policy</a> · <a href=/terms>Terms</a></p></main></html>"""
PRIVACY_PAGE = """<!doctype html><html lang=en><meta charset=utf-8><title>Tapy Privacy</title>
<main><h1>Privacy Policy</h1><p>Tapy uses delegated read-only Gmail or Outlook access. Refresh
tokens are encrypted. Message bodies are processed transiently and are not stored;
extracted booking facts, source headers and decision history are retained.
Contact: kfir.marx@gmail.com.</p></main></html>"""
TERMS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8><title>Tapy Terms</title>
<main><h1>Proof-of-concept terms</h1><p>Verify bookings with the provider. Tapy does not make,
change, or cancel reservations.</p></main></html>"""


class Extractor(Protocol):
    @property
    def ready(self) -> bool: ...
    async def extract(self, email: EmailForAnalysis) -> EmailExtraction: ...


class EventHub:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[str]]] = {}

    def subscribe(self, user_id: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
        self._queues.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue[str]) -> None:
        self._queues.get(user_id, set()).discard(queue)

    def publish(self, user_id: str) -> None:
        for queue in self._queues.get(user_id, set()):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait("refresh")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _set_session_cookie(response: StarletteResponse, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86_400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def create_app(
    settings: Settings | None = None,
    *,
    extractor: Extractor | None = None,
    mailbox_readers: Mapping[Provider, MailboxReader] | None = None,
    flight_repository: object | None = None,
    worker: bool = False,
) -> FastAPI:
    del flight_repository
    resolved = settings or Settings()
    if extractor is None:
        resolved.require_rabbitmq()
    engine = make_engine(resolved)
    if extractor is not None:
        upgrade_database(engine)
    factory = make_factory(engine)
    configured_extractor = extractor or BookingExtractor.from_settings(resolved)
    event_hub = EventHub()
    queue = JobQueue(resolved, factory)

    def ensure_context(session: Session, user: User) -> OrganizationMembership | None:
        membership = (
            session.get(OrganizationMembership, (user.active_organization_id, user.id))
            if user.active_organization_id
            else None
        )
        if membership and membership.status == "active":
            return membership
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.status == "active",
            )
        )
        user.active_organization_id = membership.organization_id if membership else None
        session.flush()
        return membership

    def membership_for(
        session: Session, user_id: str, organization_id: str
    ) -> OrganizationMembership:
        membership = session.get(OrganizationMembership, (organization_id, user_id))
        if not membership or membership.status != "active":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "active organization membership is required"
            )
        return membership

    def authorize_scope(
        session: Session, user: User, scope: Scope
    ) -> tuple[str, OrganizationMembership]:
        organization_id = user.active_organization_id
        if not organization_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "select an active organization")
        membership = membership_for(session, user.id, organization_id)
        if scope == "organization" and membership.role != "admin":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "organization scope requires an admin role"
            )
        return organization_id, membership

    def authenticate(
        authorization: Annotated[str | None, Header()] = None,
        tapy_session: Annotated[str | None, Cookie()] = None,
    ) -> User:
        supplied = tapy_session or bearer_token(authorization)
        if not supplied:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication is required")
        with factory.begin() as session:
            if supplied.startswith("ses_"):
                record = session.get(UserSession, token_hash(supplied))
                user = (
                    session.get(User, record.user_id)
                    if record and _utc(record.expires_at) > datetime.now(UTC)
                    else None
                )
            else:
                user = session.scalar(
                    select(User).where(User.access_token_hash == token_hash(supplied))
                )
            if not user:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session")
            ensure_context(session, user)
            return user

    def active_user(user: User = Depends(authenticate)) -> User:
        with factory() as session:
            authorize_scope(session, user, "personal")
        return user

    def user_view(user: User) -> UserView:
        with factory() as session:
            memberships = session.execute(
                select(OrganizationMembership, Organization)
                .join(Organization, Organization.id == OrganizationMembership.organization_id)
                .where(
                    OrganizationMembership.user_id == user.id,
                    OrganizationMembership.status == "active",
                )
            ).all()
            active = next(
                (
                    membership
                    for membership, _ in memberships
                    if membership.organization_id == user.active_organization_id
                ),
                None,
            )
            providers = session.scalars(
                select(AuthIdentity.provider).where(AuthIdentity.user_id == user.id)
            ).all()
            mailboxes = session.scalars(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user.id,
                    MailboxConnection.organization_id == user.active_organization_id,
                    MailboxConnection.refresh_token != "",
                )
            ).all()
        auth_providers = [str(item) for item in providers]
        if user.password_hash:
            auth_providers.insert(0, "password")
        return UserView(
            id=user.id,
            name=user.name,
            email=user.email,
            language=cast(Literal["en", "he"], user.language),
            active_organization_id=active.organization_id if active else None,
            invitation_required=active is None,
            active_organization_role=cast(Literal["admin", "agent"], active.role)
            if active
            else None,
            memberships=[
                MembershipView(
                    organization_id=m.organization_id,
                    organization_name=o.name,
                    role=cast(Literal["admin", "agent"], m.role),
                )
                for m, o in memberships
            ],
            auth_providers=auth_providers,
            mailboxes=[
                MailboxView(
                    provider=cast(Provider, item.provider),
                    email_address=item.email_address,
                    webhook_active=webhook_active(item),
                )
                for item in mailboxes
            ],
        )

    def notify(
        session: Session,
        user_id: str,
        organization_id: str,
        kind: str,
        title: str,
        message: str,
        *,
        booking_id: str | None = None,
        opportunity_id: str | None = None,
    ) -> None:
        session.add(
            NotificationRecord(
                user_id=user_id,
                organization_id=organization_id,
                booking_id=booking_id,
                opportunity_id=opportunity_id,
                kind=kind,
                title=title,
                message=message,
            )
        )

    def publish_organization(organization_id: str) -> None:
        with factory() as session:
            user_ids = session.scalars(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.status == "active",
                )
            ).all()
        for member_user_id in user_ids:
            event_hub.publish(member_user_id)

    def business_filter(
        model: type[TravelBooking] | type[UpsellOpportunity],
        organization_id: str,
        user: User,
        scope: Scope,
    ) -> list[Any]:
        filters: list[Any] = [model.organization_id == organization_id]
        if scope == "personal":
            filters.append(model.assigned_agent_id == user.id)
        return filters

    def accessible_booking(session: Session, booking_id: str, user: User) -> TravelBooking:
        organization_id, membership = authorize_scope(session, user, "personal")
        booking = session.scalar(
            select(TravelBooking).where(
                TravelBooking.id == booking_id, TravelBooking.organization_id == organization_id
            )
        )
        if not booking or (booking.assigned_agent_id != user.id and membership.role != "admin"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "booking was not found")
        return booking

    def accessible_opportunity(
        session: Session, opportunity_id: str, user: User
    ) -> UpsellOpportunity:
        organization_id, membership = authorize_scope(session, user, "personal")
        opportunity = session.scalar(
            select(UpsellOpportunity).where(
                UpsellOpportunity.id == opportunity_id,
                UpsellOpportunity.organization_id == organization_id,
            )
        )
        if not opportunity or (
            opportunity.assigned_agent_id != user.id and membership.role != "admin"
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity was not found")
        return opportunity

    def delivery_view(item: MessageDelivery) -> DeliveryView:
        return DeliveryView(
            id=item.id,
            recipient_id=item.recipient_id,
            channel=item.channel,
            provider=item.provider,
            provider_message_id=item.provider_message_id,
            status=item.status,
            error_code=item.error_code,
            error_message=item.error_message,
            destination_snapshot=item.destination_snapshot,
        )

    async def renew_webhooks(app: FastAPI) -> None:
        while True:
            await asyncio.sleep(resolved.webhook_renewal_seconds)
            with factory() as session:
                mailboxes = session.scalars(select(MailboxConnection)).all()
            for mailbox in mailboxes:
                if not webhook_active(mailbox) or (
                    mailbox.webhook_expires_at
                    and (_utc(mailbox.webhook_expires_at) - datetime.now(UTC)).total_seconds()
                    < resolved.webhook_renewal_seconds * 2
                ):
                    with suppress(Exception):
                        await app.state.webhooks.ensure(mailbox)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = httpx.AsyncClient(timeout=resolved.request_timeout_seconds)
        app.state.http_client = client
        app.state.oauth = OAuthService(resolved, factory, client)
        app.state.webhooks = WebhookService(resolved, factory, app.state.oauth, client)
        app.state.mailbox_readers = mailbox_readers or readers(client, resolved.gmail_query)
        tasks = []
        if extractor is None:
            await queue.connect()
        if worker:
            if extractor is None:
                assert isinstance(configured_extractor, BookingExtractor)
                await configured_extractor.connect()
            tasks = [
                asyncio.create_task(queue.consume(run_job)),
                asyncio.create_task(renew_webhooks(app)),
            ]
        app.state.worker_tasks = tasks
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            if worker and extractor is None:
                assert isinstance(configured_extractor, BookingExtractor)
                await configured_extractor.close()
            await queue.close()
        await client.aclose()
        engine.dispose()

    app = FastAPI(
        title="Tapy",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }
        )
        return response

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home() -> str:
        return HOME_PAGE

    @app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    async def privacy() -> str:
        return PRIVACY_PAGE

    @app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
    async def terms() -> str:
        return TERMS_PAGE

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(response: Response) -> dict[str, object]:
        dependencies: dict[str, object] = {
            "database": "ready",
            "rabbitmq": "ready" if queue.ready or extractor is not None else "unavailable",
        }
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            response.status_code = 503
            dependencies["database"] = "unavailable"
        if not queue.ready and extractor is None:
            response.status_code = 503
        return {
            "status": "ready" if response.status_code != 503 else "not-ready",
            "dependencies": dependencies,
        }

    @app.post("/v1/auth/register", response_model=AuthResult, status_code=201)
    async def register(body: RegisterRequest, response: Response) -> AuthResult:
        raise HTTPException(403, "invitation required")

    @app.post("/v1/auth/login", response_model=AuthResult)
    async def login(body: LoginRequest, response: Response) -> AuthResult:
        with factory.begin() as session:
            user = session.scalar(select(User).where(User.email == str(body.email).casefold()))
            if not user or not password_matches(body.password, user.password_hash):
                raise HTTPException(401, "email or password is incorrect")
            ensure_context(session, user)
            token, _ = new_session(session, user.id, resolved.session_days)
        _set_session_cookie(response, token, resolved)
        return AuthResult(user=user_view(user))

    @app.post("/v1/auth/logout", status_code=204)
    async def logout(
        response: Response, tapy_session: Annotated[str | None, Cookie()] = None
    ) -> None:
        if tapy_session:
            with factory.begin() as session:
                record = session.get(UserSession, token_hash(tapy_session))
                if record:
                    session.delete(record)
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/v1/auth/{provider}/authorization", response_model=AuthorizationUrl)
    async def authorize_login(provider: AuthProvider, request: Request) -> AuthorizationUrl:
        try:
            return AuthorizationUrl(authorization_url=request.app.state.oauth.login_url(provider))
        except OAuthError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/v1/users/me", response_model=UserView)
    async def get_user(user: User = Depends(authenticate)) -> UserView:
        return user_view(user)  # noqa: B008

    @app.patch("/v1/users/me", response_model=UserView)
    async def update_user(body: UserUpdate, user: User = Depends(authenticate)) -> UserView:  # noqa: B008
        with factory.begin() as session:
            current = session.get(User, user.id)
            assert current
            if body.name is not None:
                current.name = body.name.strip()
            if body.language is not None:
                current.language = body.language
            if body.active_organization_id is not None:
                membership_for(session, user.id, body.active_organization_id)
                current.active_organization_id = body.active_organization_id
            session.flush()
            user = current
        event_hub.publish(user.id)
        return user_view(user)

    @app.post("/v1/organizations", response_model=MembershipView, status_code=201)
    async def create_organization(
        body: OrganizationCreate, user: User = Depends(authenticate)
    ) -> MembershipView:  # noqa: B008
        raise HTTPException(403, "organization creation requires the operator CLI")

    @app.post("/v1/organizations/current/memberships", status_code=403)
    async def add_membership(user: User = Depends(authenticate)) -> None:
        raise HTTPException(403, "use organization invitations")

    @app.get("/v1/organizations/current/team")
    async def team(user: User = Depends(authenticate)) -> dict[str, object]:
        with factory() as session:
            org, _ = authorize_scope(session, user, "organization")
            members = session.execute(
                select(OrganizationMembership, User)
                .join(User, User.id == OrganizationMembership.user_id)
                .where(OrganizationMembership.organization_id == org)
            ).all()
            invitations = session.scalars(
                select(OrganizationInvitation)
                .where(OrganizationInvitation.organization_id == org)
                .order_by(OrganizationInvitation.created_at.desc())
            ).all()
            return {
                "members": [
                    {
                        "user_id": u.id,
                        "email": u.email,
                        "name": u.name,
                        "role": m.role,
                        "status": m.status,
                    }
                    for m, u in members
                ],
                "invitations": [
                    {
                        "id": i.id,
                        "email": i.email,
                        "role": i.role,
                        "expires_at": i.expires_at,
                        "accepted_at": i.accepted_at,
                        "revoked_at": i.revoked_at,
                    }
                    for i in invitations
                ],
            }

    @app.post("/v1/organizations/current/invitations", status_code=201)
    async def invite(body: MembershipCreate, user: User = Depends(authenticate)) -> dict[str, str]:
        with factory.begin() as session:
            org, _ = authorize_scope(session, user, "organization")
            invitation, token = issue_invitation(session, org, str(body.email), body.role, user.id)
        return {"id": invitation.id, "invitation_path": "/#invite=" + token}

    @app.delete("/v1/organizations/current/invitations/{invitation_id}", status_code=204)
    async def revoke_invitation(invitation_id: str, user: User = Depends(authenticate)) -> None:
        with factory.begin() as session:
            org = user.active_organization_id
            if not org:
                raise HTTPException(403, "invitation required")
            lock_organization(session, org)
            require_member(session, org, user.id, admin=True)
            invitation = session.get(OrganizationInvitation, invitation_id)
            if not invitation or invitation.organization_id != org:
                raise HTTPException(404, "invitation not found")
            if invitation.accepted_at:
                raise HTTPException(409, "invitation already accepted")
            invitation.revoked_at = datetime.now(UTC)
            audit(session, org, user.id, "invitation.revoked", invitation.id)

    @app.patch("/v1/organizations/current/memberships/{target_id}", status_code=204)
    async def update_membership(
        target_id: str, body: MembershipUpdate, user: User = Depends(authenticate)
    ) -> None:
        with factory.begin() as session:
            if not user.active_organization_id:
                raise HTTPException(403, "invitation required")
            change_member(
                session, user.active_organization_id, user.id, target_id, body.role, body.status
            )
        event_hub.publish(target_id)

    @app.post("/v1/invitations/accept", response_model=AuthResult)
    async def accept(
        body: InvitationAccept,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
        tapy_session: Annotated[str | None, Cookie()] = None,
    ) -> AuthResult:
        actor = authenticate(authorization, tapy_session) if authorization or tapy_session else None
        try:
            with factory.begin() as session:
                user = accept_invitation(session, body.token, actor, body.name, body.password)
                token, _ = new_session(session, user.id, resolved.session_days)
        except IntegrityError as exc:
            raise HTTPException(409, "account or invitation changed; sign in and retry") from exc
        _set_session_cookie(response, token, resolved)
        return AuthResult(user=user_view(user))

    @app.get("/v1/bookings", response_model=list[BookingView])
    async def list_bookings(
        scope: Scope = "personal",
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: User = Depends(active_user),
    ) -> list[BookingView]:  # noqa: B008
        with factory() as session:
            organization_id, _ = authorize_scope(session, user, scope)
            records = session.scalars(
                select(TravelBooking)
                .where(*business_filter(TravelBooking, organization_id, user, scope))
                .order_by(TravelBooking.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [booking_view(session, row) for row in records]

    @app.get("/v1/bookings/{booking_id}", response_model=BookingView)
    async def get_booking(booking_id: str, user: User = Depends(active_user)) -> BookingView:  # noqa: B008
        with factory() as session:
            return booking_view(session, accessible_booking(session, booking_id, user))

    @app.post("/v1/bookings", response_model=BookingView, status_code=201)
    async def post_booking(body: BookingCreate, user: User = Depends(active_user)) -> BookingView:  # noqa: B008
        try:
            with factory.begin() as session:
                organization_id, membership = authorize_scope(session, user, "personal")
                if (
                    body.assigned_agent_id
                    and body.assigned_agent_id != user.id
                    and membership.role != "admin"
                ):
                    raise HTTPException(403, "agents may only assign their own bookings")
                record = create_booking(session, organization_id, user.id, body)
                session.flush()
                notify(
                    session,
                    record.assigned_agent_id,
                    organization_id,
                    "booking_added",
                    "Booking added",
                    "A travel booking was added.",
                    booking_id=record.id,
                )
                view = booking_view(session, record)
        except IntegrityError as exc:
            raise HTTPException(409, "booking conflicts with existing organization data") from exc
        publish_organization(record.organization_id)
        return view

    @app.patch("/v1/bookings/{booking_id}", response_model=BookingView)
    async def patch_booking(
        booking_id: str, body: BookingUpdate, user: User = Depends(active_user)
    ) -> BookingView:  # noqa: B008
        with factory.begin() as session:
            record = accessible_booking(session, booking_id, user)
            if body.assigned_agent_id:
                authorize_scope(session, user, "organization")
                ensure_agent(session, record.organization_id, body.assigned_agent_id)
                record.assigned_agent_id = body.assigned_agent_id
                for opportunity in session.scalars(
                    select(UpsellOpportunity).where(
                        UpsellOpportunity.booking_id == record.id,
                        UpsellOpportunity.organization_id == record.organization_id,
                    )
                ):
                    opportunity.assigned_agent_id = body.assigned_agent_id
                    opportunity.version += 1
            if body.internal_reference is not None:
                record.internal_reference = body.internal_reference
            if body.attributes is not None:
                record.attributes = body.attributes
            if body.status:
                record.status = body.status
                for reservation in session.scalars(
                    select(FlightReservation).where(FlightReservation.booking_id == record.id)
                ):
                    reservation.status = body.status
                if body.status == "cancelled":
                    for opportunity in session.scalars(
                        select(UpsellOpportunity).where(
                            UpsellOpportunity.booking_id == record.id,
                            UpsellOpportunity.status.in_(["open", "contacted"]),
                        )
                    ):
                        opportunity.status = "closed"
                        opportunity.close_reason = "booking_cancelled"
            record.updated_at = datetime.now(UTC)
            session.flush()
            view = booking_view(session, record)
        publish_organization(record.organization_id)
        return view

    @app.post("/v1/bookings/{booking_id}/tickets", response_model=BookingView, status_code=201)
    async def create_ticket(
        booking_id: str, body: TicketCreate, user: User = Depends(active_user)
    ) -> BookingView:  # noqa: B008
        try:
            with factory.begin() as session:
                booking = accessible_booking(session, booking_id, user)
                reservation = session.scalar(
                    select(FlightReservation).where(
                        FlightReservation.id == body.reservation_id,
                        FlightReservation.booking_id == booking.id,
                        FlightReservation.organization_id == booking.organization_id,
                    )
                )
                person = session.scalar(
                    select(BookingPerson).where(
                        BookingPerson.id == body.person_id,
                        BookingPerson.booking_id == booking.id,
                        BookingPerson.organization_id == booking.organization_id,
                    )
                )
                if not reservation or not person:
                    raise HTTPException(
                        422, "ticket person and reservation must belong to this booking"
                    )
                ticket = FlightTicket(
                    organization_id=booking.organization_id,
                    booking_id=booking.id,
                    reservation_id=reservation.id,
                    person_id=person.id,
                    ticket_number=body.ticket_number,
                    amount=body.amount,
                    currency=body.currency,
                )
                session.add(ticket)
                session.flush()
                for segment_id in body.segment_ids:
                    segment = session.scalar(
                        select(FlightSegment).where(
                            FlightSegment.id == segment_id,
                            FlightSegment.reservation_id == reservation.id,
                            FlightSegment.organization_id == booking.organization_id,
                        )
                    )
                    if not segment:
                        raise HTTPException(422, "ticket segment must belong to its reservation")
                    session.add(
                        TicketSegment(
                            ticket_id=ticket.id,
                            segment_id=segment.id,
                            organization_id=booking.organization_id,
                        )
                    )
                session.flush()
                return booking_view(session, booking)
        except IntegrityError as exc:
            raise HTTPException(409, "ticket number already exists in this organization") from exc

    @app.post(
        "/v1/bookings/{booking_id}/people/{person_id}/roles",
        response_model=BookingView,
        status_code=201,
    )
    async def add_person_role(
        booking_id: str,
        person_id: str,
        body: RoleCreate,
        user: User = Depends(active_user),
    ) -> BookingView:
        try:
            with factory.begin() as session:
                booking = accessible_booking(session, booking_id, user)
                person = session.scalar(
                    select(BookingPerson).where(
                        BookingPerson.id == person_id,
                        BookingPerson.booking_id == booking.id,
                        BookingPerson.organization_id == booking.organization_id,
                    )
                )
                if not person:
                    raise HTTPException(404, "person was not found")
                session.add(
                    BookingPersonRole(
                        organization_id=booking.organization_id,
                        person_id=person.id,
                        role=body.role,
                        source_method=body.source_method,
                        confidence=body.confidence,
                        reason=body.reason,
                    )
                )
                session.flush()
                return booking_view(session, booking)
        except IntegrityError as exc:
            raise HTTPException(409, "this role and source method already exist") from exc

    @app.post(
        "/v1/bookings/{booking_id}/people/{person_id}/contacts",
        response_model=BookingView,
        status_code=201,
    )
    async def add_person_contact(
        booking_id: str,
        person_id: str,
        body: ContactCreate,
        user: User = Depends(active_user),
    ) -> BookingView:
        try:
            with factory.begin() as session:
                booking = accessible_booking(session, booking_id, user)
                person = session.scalar(
                    select(BookingPerson).where(
                        BookingPerson.id == person_id,
                        BookingPerson.booking_id == booking.id,
                        BookingPerson.organization_id == booking.organization_id,
                    )
                )
                if not person:
                    raise HTTPException(404, "person was not found")
                session.add(
                    ContactPoint(
                        organization_id=booking.organization_id,
                        person_id=person.id,
                        channel=body.channel,
                        raw_value=body.value,
                        display_value=body.value.strip(),
                        normalized_value=normalized_contact(body.channel, body.value),
                        is_primary=body.is_primary,
                    )
                )
                session.flush()
                return booking_view(session, booking)
        except IntegrityError as exc:
            raise HTTPException(409, "this contact point already exists") from exc

    @app.get("/v1/opportunities", response_model=list[OpportunityView])
    async def list_opportunities(
        scope: Scope = "personal",
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: User = Depends(active_user),
    ) -> list[OpportunityView]:  # noqa: B008
        with factory() as session:
            organization_id, _ = authorize_scope(session, user, scope)
            records = session.scalars(
                select(UpsellOpportunity)
                .where(*business_filter(UpsellOpportunity, organization_id, user, scope))
                .order_by(UpsellOpportunity.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [opportunity_view(session, row) for row in records]

    @app.get("/v1/opportunities/{opportunity_id}", response_model=OpportunityView)
    async def get_opportunity(
        opportunity_id: str, user: User = Depends(active_user)
    ) -> OpportunityView:  # noqa: B008
        with factory() as session:
            return opportunity_view(session, accessible_opportunity(session, opportunity_id, user))

    @app.get("/v1/opportunities/{opportunity_id}/audit")
    async def opportunity_audit(
        opportunity_id: str, user: User = Depends(active_user)
    ) -> list[dict[str, object]]:
        with factory() as session:
            accessible_opportunity(session, opportunity_id, user)
            return [
                {"reason": row.reason, "evidence": row.evidence, "created_at": row.created_at}
                for row in session.scalars(
                    select(ReconciliationDecision)
                    .where(ReconciliationDecision.opportunity_id == opportunity_id)
                    .order_by(ReconciliationDecision.created_at)
                )
            ]

    @app.post("/v1/opportunities", response_model=OpportunityView, status_code=201)
    async def create_opportunity(
        body: OpportunityCreate, user: User = Depends(active_user)
    ) -> OpportunityView:  # noqa: B008
        raise HTTPException(
            409, "Opportunities are derived from connected email; run a mailbox scan"
        )

    @app.patch("/v1/opportunities/{opportunity_id}", response_model=OpportunityView)
    async def update_opportunity(
        opportunity_id: str, body: OpportunityUpdate, user: User = Depends(active_user)
    ) -> OpportunityView:  # noqa: B008
        with factory.begin() as session:
            session.scalar(
                select(Organization)
                .where(Organization.id == user.active_organization_id)
                .with_for_update()
            )
            record = accessible_opportunity(session, opportunity_id, user)
            if record.status not in {"open", "contacted"}:
                raise HTTPException(409, "opportunity is no longer actionable")
            if record.version != body.version:
                raise HTTPException(409, "opportunity was changed; refresh and retry")
            record.status = body.status
            record.close_reason = "agent_dismissed"
            session.add(
                ReconciliationDecision(
                    organization_id=record.organization_id,
                    booking_id=record.booking_id,
                    opportunity_id=record.id,
                    reason="agent_dismissed",
                    evidence={"actor_user_id": user.id},
                    fingerprint=stable_hash(record.id, record.version, "dismissed"),
                )
            )
            record.version += 1
            record.updated_at = datetime.now(UTC)
            session.flush()
            view = opportunity_view(session, record)
        publish_organization(record.organization_id)
        return view

    @app.put(
        "/v1/opportunities/{opportunity_id}/recipients/{person_id}", response_model=OpportunityView
    )
    async def put_recipient(
        opportunity_id: str,
        person_id: str,
        body: RecipientUpdate,
        user: User = Depends(active_user),
    ) -> OpportunityView:  # noqa: B008
        if body.person_id != person_id:
            raise HTTPException(422, "person IDs do not match")
        with factory.begin() as session:
            session.scalar(
                select(Organization)
                .where(Organization.id == user.active_organization_id)
                .with_for_update()
            )
            opportunity = accessible_opportunity(session, opportunity_id, user)
            if opportunity.status != "open":
                raise HTTPException(409, "opportunity is no longer actionable")
            person = session.scalar(
                select(BookingPerson).where(
                    BookingPerson.id == person_id,
                    BookingPerson.booking_id == opportunity.booking_id,
                    BookingPerson.organization_id == opportunity.organization_id,
                )
            )
            if not person:
                raise HTTPException(422, "recipient must belong to the opportunity booking")
            if body.contact_point_id:
                contact = session.scalar(
                    select(ContactPoint).where(
                        ContactPoint.id == body.contact_point_id,
                        ContactPoint.person_id == person_id,
                        ContactPoint.organization_id == opportunity.organization_id,
                    )
                )
                if not contact:
                    raise HTTPException(422, "contact point must belong to the recipient")
            recipient = session.scalar(
                select(OpportunityRecipient).where(
                    OpportunityRecipient.opportunity_id == opportunity.id,
                    OpportunityRecipient.person_id == person_id,
                )
            )
            if not recipient:
                recipient = OpportunityRecipient(
                    organization_id=opportunity.organization_id,
                    booking_id=opportunity.booking_id,
                    opportunity_id=opportunity.id,
                    person_id=person_id,
                    selection_status=body.selection_status,
                    selection_method=body.selection_method,
                )
                session.add(recipient)
            recipient.contact_point_id = body.contact_point_id
            recipient.selection_status = body.selection_status
            recipient.selection_method = "manual"
            recipient.selection_reason = body.selection_reason
            recipient.confidence = body.confidence
            recipient.priority = body.priority
            opportunity.version += 1
            session.flush()
            view = opportunity_view(session, opportunity)
        publish_organization(opportunity.organization_id)
        return view

    async def deliver_opportunity(
        opportunity_id: str,
        user: User = Depends(active_user),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> SendBatchView:  # noqa: B008
        key = idempotency_key or secrets.token_urlsafe(24)
        with factory() as session:
            opportunity = accessible_opportunity(session, opportunity_id, user)
            replay = session.scalar(
                select(UpsellSendBatch).where(
                    UpsellSendBatch.organization_id == opportunity.organization_id,
                    UpsellSendBatch.idempotency_key == key,
                )
            )
            if replay:
                if replay.opportunity_id != opportunity_id:
                    raise HTTPException(409, "idempotency key belongs to another opportunity")
                if replay.status == "processing":
                    raise HTTPException(
                        409, "Delivery outcome uncertain; inspect provider before retrying"
                    )
                deliveries = session.scalars(
                    select(MessageDelivery).where(MessageDelivery.batch_id == replay.id)
                ).all()
                return SendBatchView(
                    id=replay.id,
                    opportunity_id=replay.opportunity_id,
                    completion_policy="all_selected",
                    status=replay.status,
                    idempotent_replay=True,
                    deliveries=[delivery_view(item) for item in deliveries],
                )
            if opportunity.status not in {"open", "contacted"}:
                raise HTTPException(409, "opportunity is no longer actionable")
            recipients = session.scalars(
                select(OpportunityRecipient)
                .where(
                    OpportunityRecipient.opportunity_id == opportunity.id,
                    OpportunityRecipient.selection_status == "selected",
                )
                .order_by(OpportunityRecipient.priority)
            ).all()
            if not recipients:
                raise HTTPException(422, "select at least one recipient before sending")
            targets: list[tuple[OpportunityRecipient, BookingPerson, ContactPoint]] = []
            for recipient in recipients:
                person = session.get(BookingPerson, recipient.person_id)
                contact = (
                    session.get(ContactPoint, recipient.contact_point_id)
                    if recipient.contact_point_id
                    else None
                )
                if not person or not contact or contact.person_id != recipient.person_id:
                    raise HTTPException(
                        422, f"selected recipient {recipient.id} needs a valid contact point"
                    )
                targets.append((recipient, person, contact))
            org_id = opportunity.organization_id
            agent_id = opportunity.assigned_agent_id
        try:
            with factory.begin() as session:
                locked = session.scalar(
                    select(UpsellOpportunity)
                    .where(
                        UpsellOpportunity.id == opportunity_id,
                        UpsellOpportunity.organization_id == org_id,
                    )
                    .with_for_update()
                )
                assert locked
                if locked.status not in {"open", "contacted"}:
                    raise HTTPException(409, "opportunity is no longer actionable")
                processing = session.scalar(
                    select(UpsellSendBatch.id).where(
                        UpsellSendBatch.opportunity_id == opportunity_id,
                        UpsellSendBatch.status == "processing",
                    )
                )
                if processing:
                    raise HTTPException(409, "a send for this opportunity is already processing")
                batch = UpsellSendBatch(
                    organization_id=org_id,
                    opportunity_id=opportunity_id,
                    actor_user_id=user.id,
                    idempotency_key=key,
                    template_name="hotel_offer",
                    template_version="1",
                )
                session.add(batch)
                session.flush()
                batch_id = batch.id
        except IntegrityError:
            with factory() as session:
                replay = session.scalar(
                    select(UpsellSendBatch).where(
                        UpsellSendBatch.organization_id == org_id,
                        UpsellSendBatch.idempotency_key == key,
                    )
                )
                assert replay
                deliveries = session.scalars(
                    select(MessageDelivery).where(MessageDelivery.batch_id == replay.id)
                ).all()
                return SendBatchView(
                    id=replay.id,
                    opportunity_id=replay.opportunity_id,
                    completion_policy="all_selected",
                    status=replay.status,
                    idempotent_replay=True,
                    deliveries=[delivery_view(item) for item in deliveries],
                )

        account_sid = resolved.twilio_account_sid.strip()
        auth_token = resolved.twilio_auth_token.get_secret_value()
        created: list[MessageDelivery] = []
        for recipient, person, contact in targets:
            with factory() as session:
                previous = session.scalar(
                    select(MessageDelivery)
                    .join(UpsellSendBatch, UpsellSendBatch.id == MessageDelivery.batch_id)
                    .where(
                        UpsellSendBatch.opportunity_id == opportunity_id,
                        MessageDelivery.recipient_id == recipient.id,
                        MessageDelivery.status.in_(["submitted", "delivered"]),
                    )
                    .order_by(MessageDelivery.created_at.desc())
                )
            if previous:
                created.append(previous)
                continue
            with factory() as session:
                authorized_opportunity = accessible_opportunity(session, opportunity_id, user)
                if authorized_opportunity.status not in {"open", "contacted"}:
                    raise HTTPException(409, "opportunity was invalidated before delivery")
            rendered = (
                f"Hi {person.given_name or person.display_name}! "
                f"Explore accommodation for your trip: {resolved.hotel_offer_url}"
            )
            provider_id = error_code = error_message = None
            delivery_status = "failed"
            if contact.channel not in {"phone", "whatsapp"}:
                error_code, error_message = (
                    "unsupported_channel",
                    "Twilio delivery requires phone or WhatsApp",
                )
            elif not account_sid or not auth_token:
                error_code, error_message = "provider_unconfigured", "Twilio is not configured"
            else:
                try:
                    response = await app.state.http_client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                        data={
                            "To": f"whatsapp:{contact.normalized_value}",
                            "From": resolved.twilio_whatsapp_from,
                            "Body": rendered,
                        },
                        auth=(account_sid, auth_token),
                    )
                    response.raise_for_status()
                    provider_id = str(response.json()["sid"])
                    delivery_status = "submitted"
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    error_code = (
                        "provider_rejected"
                        if isinstance(exc, httpx.HTTPStatusError)
                        else "provider_unavailable"
                    )
                    error_message = "Twilio did not accept the message"
            with factory.begin() as session:
                delivery = MessageDelivery(
                    organization_id=org_id,
                    batch_id=batch_id,
                    recipient_id=recipient.id,
                    channel=contact.channel,
                    provider="twilio",
                    provider_message_id=provider_id,
                    status=delivery_status,
                    error_code=error_code,
                    error_message=error_message,
                    recipient_name_snapshot=person.display_name,
                    destination_snapshot=contact.normalized_value,
                    rendered_message=rendered,
                    idempotency_key=stable_hash(opportunity_id, recipient.id, key),
                    submitted_at=datetime.now(UTC) if delivery_status == "submitted" else None,
                )
                session.add(delivery)
                session.flush()
                created.append(delivery)
        successful_recipient_ids = {
            item.recipient_id for item in created if item.status in {"submitted", "delivered"}
        }
        batch_status = (
            "completed"
            if len(successful_recipient_ids) == len(targets)
            else "partial"
            if successful_recipient_ids
            else "failed"
        )
        with factory.begin() as session:
            completed_batch = session.get(UpsellSendBatch, batch_id)
            assert completed_batch
            completed_batch.status = batch_status
            completed_batch.completed_at = datetime.now(UTC)
            current = session.get(UpsellOpportunity, opportunity_id)
            assert current
            if successful_recipient_ids and current.status == "open":
                current.status = "contacted"
                current.version += 1
            notify(
                session,
                agent_id,
                org_id,
                "opportunity_send",
                "Opportunity outreach completed",
                f"Delivery batch finished with status {batch_status}.",
                booking_id=current.booking_id,
                opportunity_id=current.id,
            )
        publish_organization(org_id)
        return SendBatchView(
            id=batch_id,
            opportunity_id=opportunity_id,
            completion_policy="all_selected",
            status=batch_status,
            deliveries=[delivery_view(item) for item in created],
        )

    @app.get("/v1/metrics", response_model=MetricsView)
    async def metrics(scope: Scope = "personal", user: User = Depends(active_user)) -> MetricsView:  # noqa: B008
        with factory() as session:
            organization_id, _ = authorize_scope(session, user, scope)
            values = metric_values(
                session, organization_id, user.id if scope == "personal" else None
            )
            statuses = cast(dict[str, int], values["statuses"])
            per_agent: list[AgentMetrics] = []
            if scope == "organization":
                members = session.execute(
                    select(OrganizationMembership, User)
                    .join(User, User.id == OrganizationMembership.user_id)
                    .where(
                        OrganizationMembership.organization_id == organization_id,
                        OrganizationMembership.status == "active",
                    )
                ).all()
                for _, member in members:
                    own = metric_values(session, organization_id, member.id)
                    own_statuses = cast(dict[str, int], own["statuses"])
                    own_money = cast(dict[str, dict[str, Decimal]], own["monetary_totals"])
                    per_agent.append(
                        AgentMetrics(
                            agent_id=member.id,
                            agent_name=member.name,
                            total_opportunities=cast(int, own["total"]),
                            won_opportunities=own_statuses["won"],
                            conversion_rate=cast(float, own["conversion_rate"]),
                            won_commission_by_currency={
                                currency: totals["won_commission"]
                                for currency, totals in own_money.items()
                                if totals["won_commission"]
                            },
                        )
                    )
            return MetricsView(
                scope=scope,
                total_opportunities=cast(int, values["total"]),
                open_opportunities=statuses["open"],
                contacted_opportunities=statuses["contacted"],
                won_opportunities=statuses["won"],
                declined_opportunities=statuses["declined"],
                expired_opportunities=statuses["expired"],
                closed_opportunities=statuses["closed"],
                delivery_successes=cast(int, values["delivery_successes"]),
                delivery_failures=cast(int, values["delivery_failures"]),
                conversion_rate=cast(float, values["conversion_rate"]),
                monetary_totals=[
                    CurrencyMetrics(currency=currency, **totals)
                    for currency, totals in sorted(
                        cast(dict[str, dict[str, Decimal]], values["monetary_totals"]).items()
                    )
                ],
                per_agent=per_agent,
            )

    @app.get("/v1/notifications", response_model=list[NotificationView])
    async def list_notifications(user: User = Depends(active_user)) -> list[NotificationView]:  # noqa: B008
        with factory() as session:
            organization_id, _ = authorize_scope(session, user, "personal")
            records = session.scalars(
                select(NotificationRecord)
                .where(
                    NotificationRecord.user_id == user.id,
                    NotificationRecord.organization_id == organization_id,
                )
                .order_by(NotificationRecord.created_at.desc())
                .limit(100)
            ).all()
            return [NotificationView.model_validate(row, from_attributes=True) for row in records]

    @app.post("/v1/notifications/read", status_code=204)
    async def read_notifications(
        body: NotificationsRead, user: User = Depends(active_user)
    ) -> None:  # noqa: B008
        with factory.begin() as session:
            organization_id, _ = authorize_scope(session, user, "personal")
            for record in session.scalars(
                select(NotificationRecord).where(
                    NotificationRecord.user_id == user.id,
                    NotificationRecord.organization_id == organization_id,
                    NotificationRecord.id.in_(body.notification_ids),
                    NotificationRecord.read_at.is_(None),
                )
            ):
                record.read_at = datetime.now(UTC)

    @app.get("/v1/events")
    async def events(user: User = Depends(active_user)) -> StreamingResponse:  # noqa: B008
        async def stream() -> AsyncIterator[str]:
            queue = event_hub.subscribe(user.id)
            try:
                yield "event: ready\ndata: connected\n\n"
                while True:
                    try:
                        value = await asyncio.wait_for(queue.get(), timeout=25)
                        yield f"event: refresh\ndata: {value}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                event_hub.unsubscribe(user.id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/v1/agents", response_model=AgentCreated, status_code=201)
    async def create_agent() -> AgentCreated:
        raise HTTPException(403, "invitation required")

    @app.get("/v1/agents/me", response_model=AgentView)
    async def get_agent(user: User = Depends(active_user)) -> AgentView:  # noqa: B008
        view = user_view(user)
        return AgentView(
            agent_id=user.id, connected_mailboxes=[item.provider for item in view.mailboxes]
        )

    @app.post("/v1/mailboxes/{provider}/authorization", response_model=AuthorizationUrl)
    async def authorize_mailbox(
        provider: Provider, request: Request, user: User = Depends(active_user)
    ) -> AuthorizationUrl:  # noqa: B008
        try:
            return AuthorizationUrl(
                authorization_url=request.app.state.oauth.authorization_url(user.id, provider)
            )
        except OAuthError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.delete("/v1/mailboxes/{provider}", status_code=204)
    async def disconnect_mailbox(provider: Provider, user: User = Depends(active_user)) -> None:  # noqa: B008
        with factory.begin() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user.id, MailboxConnection.provider == provider
                )
            )
            if mailbox:
                if mailbox.organization_id != user.active_organization_id:
                    raise HTTPException(403, "mailbox belongs to another organization")
                # Preserve binding and history on disconnect; reconnect restores credentials.
                mailbox.refresh_token = ""
                mailbox.webhook_expires_at = None

    async def enqueue_job(
        user: User, kind: str, payload: dict[str, object], key: str | None = None
    ) -> JobView:
        with factory() as session:
            authorize_scope(session, user, "personal")
        try:
            job = queue.enqueue(
                user_id=user.id,
                organization_id=cast(str, user.active_organization_id),
                kind=kind,
                payload=payload,
                key=key or secrets.token_urlsafe(24),
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        with suppress(Exception):
            await queue.publish(job.id)
        return job

    @app.get("/v1/jobs", response_model=list[JobView])
    async def list_jobs(user: User = Depends(active_user)) -> list[JobView]:
        with factory() as session:
            return [
                job_view(j)
                for j in session.scalars(
                    select(BackgroundJob)
                    .where(
                        BackgroundJob.user_id == user.id,
                        BackgroundJob.organization_id == user.active_organization_id,
                    )
                    .order_by(BackgroundJob.created_at.desc())
                    .limit(30)
                )
            ]

    @app.get("/v1/jobs/{job_id}", response_model=JobView)
    async def get_job(job_id: str, user: User = Depends(active_user)) -> JobView:
        with factory() as session:
            job = session.get(BackgroundJob, job_id)
            if (
                not job
                or job.user_id != user.id
                or job.organization_id != user.active_organization_id
            ):
                raise HTTPException(404, "job not found")
            return job_view(job)

    @app.post("/v1/jobs/{job_id}/retry", response_model=JobView, status_code=202)
    async def retry_job(job_id: str, user: User = Depends(active_user)) -> JobView:
        await get_job(job_id, user)
        with factory.begin() as session:
            job = session.scalar(
                select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
            )
            assert job
            if job.status != "failed" or job.kind == "send":
                raise HTTPException(
                    409,
                    "Only failed scan jobs can be retried; "
                    "inspect uncertain sends with the provider",
                )
            job.status, job.attempts, job.error = "queued", 0, None
            job.available_at = datetime.now(UTC)
            session.flush()
            view = job_view(job)
        with suppress(Exception):
            await queue.publish(job_id)
        return view

    @app.post("/v1/opportunities/{opportunity_id}/send", response_model=JobView, status_code=202)
    async def send_opportunity(
        opportunity_id: str,
        user: User = Depends(active_user),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JobView:
        with factory.begin() as session:
            session.scalar(
                select(Organization)
                .where(Organization.id == user.active_organization_id)
                .with_for_update()
            )
            opportunity = accessible_opportunity(session, opportunity_id, user)
            if idempotency_key:
                previous = session.scalar(
                    select(BackgroundJob).where(
                        BackgroundJob.user_id == user.id,
                        BackgroundJob.organization_id == user.active_organization_id,
                        BackgroundJob.idempotency_key == idempotency_key,
                    )
                )
                if previous:
                    if previous.kind != "send" or previous.payload != {
                        "opportunity_id": opportunity_id
                    }:
                        raise HTTPException(409, "Idempotency key belongs to another request")
                    return job_view(previous)
            reconcile(session, opportunity.organization_id, opportunity.assigned_agent_id)
            if opportunity.status != "open":
                raise HTTPException(409, "opportunity is no longer actionable")
            if not resolved.hotel_offer_url:
                raise HTTPException(409, "A partner booking link must be configured before sending")
            selected = session.scalar(
                select(OpportunityRecipient.id).where(
                    OpportunityRecipient.opportunity_id == opportunity_id,
                    OpportunityRecipient.selection_status == "selected",
                    OpportunityRecipient.contact_point_id.is_not(None),
                )
            )
            if not selected:
                raise HTTPException(
                    422, "Select a traveler with a phone or WhatsApp contact before sending"
                )
            # Enqueue and reserve the workflow in one transaction; no gap can lose a send.
            key = idempotency_key or secrets.token_urlsafe(24)
            job = BackgroundJob(
                user_id=user.id,
                organization_id=cast(str, user.active_organization_id),
                kind="send",
                payload={"opportunity_id": opportunity_id},
                idempotency_key=key,
            )
            session.add(job)
            opportunity.status = "contacted"
            opportunity.attributes = {**opportunity.attributes, "outreach_started": True}
            opportunity.version += 1
            session.add(
                ReconciliationDecision(
                    organization_id=opportunity.organization_id,
                    booking_id=opportunity.booking_id,
                    opportunity_id=opportunity.id,
                    reason="outreach_queued",
                    evidence={"actor_user_id": user.id, "job_key": key},
                    fingerprint=stable_hash(opportunity.id, key),
                )
            )
            session.flush()
            view = job_view(job)
        with suppress(Exception):
            await queue.publish(view.id)
        return view

    @app.post("/v1/scans", response_model=JobView, status_code=202)
    async def scan_mailbox(
        body: ScanRequest,
        user: User = Depends(active_user),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JobView:
        with factory() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user.id,
                    MailboxConnection.provider == body.provider,
                )
            )
            if not mailbox or not mailbox.refresh_token:
                raise HTTPException(409, "mailbox is not connected")
            bound = mailbox_organization(session, mailbox.id)
            if not bound or bound != user.active_organization_id:
                raise HTTPException(
                    409, "Switch to the organization where this mailbox was first scanned"
                )
        return await enqueue_job(
            user, "scan", {**body.model_dump(), "mailbox_id": mailbox.id}, idempotency_key
        )

    async def run_job(job: BackgroundJob) -> dict[str, object]:
        with factory() as session:
            user = session.get(User, job.user_id)
            membership = session.get(OrganizationMembership, (job.organization_id, job.user_id))
            if not user or not membership or membership.status != "active":
                raise ValueError("agent membership is no longer active")
            # Jobs stay in the tenant selected at submission, including after UI tenant switches.
            user.active_organization_id = job.organization_id
        if job.kind == "send":
            result = await deliver_opportunity(str(job.payload["opportunity_id"]), user, job.id)
            if result.status != "completed":
                raise JobFailure(
                    "Delivery failed or partial; inspect delivery details before retrying",
                    result.model_dump(mode="json"),
                )
            return result.model_dump(mode="json")
        with factory() as session:
            mailbox = session.get(MailboxConnection, job.payload["mailbox_id"])
            if (
                not mailbox
                or mailbox.user_id != user.id
                or mailbox.organization_id != job.organization_id
            ):
                raise ValueError("mailbox disconnected")
        if job.kind == "watch":
            registered = await app.state.webhooks.ensure(mailbox)
            if not registered and (mailbox.provider != "gmail" or resolved.gmail_pubsub_topic):
                raise ValueError("webhook registration failed")
            with factory() as session:
                current_mailbox = session.get(MailboxConnection, mailbox.id)
                return {"webhook_active": bool(current_mailbox and webhook_active(current_mailbox))}
        access_token = await app.state.oauth.access_token(mailbox)
        maximum = int(
            cast(int, job.payload.get("maximum_messages") or resolved.maximum_messages_per_scan)
        )
        counts = {
            "messages_seen": 0,
            "messages_analyzed": 0,
            "messages_skipped": 0,
            "opportunities_created": 0,
        }
        # Readers paginate; extraction is performed page by page in this RabbitMQ worker.
        reader = app.state.mailbox_readers[mailbox.provider]
        async for messages in reader.pages(access_token, maximum):
            for email in messages:
                counts["messages_seen"] += 1
                with factory() as session:
                    require_member(session, job.organization_id, user.id)
                    seen = session.scalar(
                        select(ProcessedMessage).where(
                            ProcessedMessage.mailbox_id == mailbox.id,
                            ProcessedMessage.provider_message_id == email.message_id,
                        )
                    )
                if (
                    seen
                    and seen.result_summary.get("schema_version") == 1
                    and not job.payload.get("reprocess")
                ):
                    counts["messages_skipped"] += 1
                else:
                    extracted = await configured_extractor.extract(email)
                    with factory.begin() as session:
                        if not session.get(MailboxConnection, mailbox.id):
                            raise ValueError("mailbox disconnected during scan")
                        created = record_email(
                            session,
                            organization_id=job.organization_id,
                            agent_id=user.id,
                            mailbox_id=mailbox.id,
                            provider=mailbox.provider,
                            email=email,
                            extraction=extracted,
                        )
                        processed = session.scalar(
                            select(ProcessedMessage).where(
                                ProcessedMessage.mailbox_id == mailbox.id,
                                ProcessedMessage.provider_message_id == email.message_id,
                            )
                        )
                        if not processed:
                            processed = ProcessedMessage(
                                mailbox_id=mailbox.id,
                                organization_id=job.organization_id,
                                provider_message_id=email.message_id,
                                outcome="analyzed",
                            )
                            session.add(processed)
                        processed.result_summary = {"opportunity_ids": created, "schema_version": 1}
                    counts["messages_analyzed"] += 1
                    counts["opportunities_created"] += len(created)
                with factory.begin() as session:
                    current = session.get(BackgroundJob, job.id)
                    assert current
                    current.progress = dict(counts)
        with factory.begin() as session:
            lock_organization(session, job.organization_id)
            require_member(session, job.organization_id, user.id)
            reconcile(session, job.organization_id, user.id)
        return dict(counts)

    app.state.run_job = run_job
    app.state.job_queue = queue
    app.state.factory = factory

    @app.get("/v1/oauth/{provider}/callback", response_class=HTMLResponse)
    async def oauth_callback(
        provider: Provider,
        request: Request,
        state: str = Query(min_length=20, max_length=200),
        code: str = Query(min_length=1, max_length=4096),
    ) -> StarletteResponse:
        try:
            purpose = request.app.state.oauth.state_purpose(provider, state)
            if purpose == "login":
                completion = await request.app.state.oauth.complete_login(
                    "google" if provider == "gmail" else "microsoft", state, code
                )
                with factory.begin() as session:
                    current = session.get(User, completion.user.id)
                    assert current
                    ensure_context(session, current)
                    token, _ = new_session(session, current.id, resolved.session_days)
                response = RedirectResponse(url="/", status_code=303)
                _set_session_cookie(response, token, resolved)
                return response
            mailbox = await request.app.state.oauth.complete(provider, state, code)
            with factory() as session:
                owner = session.get(User, mailbox.user_id)
            assert owner
            owner.active_organization_id = mailbox.organization_id
            await enqueue_job(owner, "watch", {"mailbox_id": mailbox.id})
            event_hub.publish(mailbox.user_id)
        except OAuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        payload = json.dumps(
            {
                "type": "tapy-mailbox-connected",
                "mailbox": {
                    "provider": mailbox.provider,
                    "email_address": mailbox.email_address,
                    "webhook_active": webhook_active(mailbox),
                },
            }
        ).replace("<", "\\u003c")
        return HTMLResponse(
            "<!doctype html><title>Mailbox connected</title><h1>Mailbox connected</h1>"
            f"<script>if(window.opener){{window.opener.postMessage({payload},"
            "window.location.origin);window.close()}else{window.location.replace('/')}</script>"
        )

    async def scan_from_webhook(user_id: str, provider: Provider) -> None:
        with factory() as session:
            user = session.get(User, user_id)
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user_id, MailboxConnection.provider == provider
                )
            )
            if user and mailbox:
                if not mailbox.refresh_token:
                    return
                user.active_organization_id = mailbox.organization_id
                if not mailbox.organization_id:
                    return
                member = session.get(OrganizationMembership, (mailbox.organization_id, user.id))
                if not member or member.status != "active":
                    return
        if user:
            await scan_mailbox(ScanRequest(provider=provider), user, None)

    @app.post("/v1/webhooks/gmail", status_code=202)
    async def gmail_webhook(
        payload: dict[str, object], token: str = Query(default="")
    ) -> dict[str, str]:
        expected = resolved.webhook_verification_token.get_secret_value()
        if not expected or not hmac.compare_digest(token, expected):
            raise HTTPException(401, "invalid webhook token")
        try:
            message = cast(dict[str, object], payload["message"])
            raw = str(message["data"])
            notice = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            email = str(notice["emailAddress"]).casefold()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "invalid Gmail notification") from exc
        with factory() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.provider == "gmail",
                    MailboxConnection.email_address.ilike(email),
                )
            )
        if mailbox:
            await scan_from_webhook(mailbox.user_id, "gmail")
        return {"status": "accepted"}

    @app.post("/v1/webhooks/outlook")
    async def outlook_webhook(
        payload: dict[str, object] | None = None,
        validation_token: str | None = Query(default=None, alias="validationToken"),
        token: str = Query(default=""),
    ) -> StarletteResponse:
        expected = resolved.webhook_verification_token.get_secret_value()
        if not expected or not hmac.compare_digest(token, expected):
            raise HTTPException(401, "invalid webhook token")
        if validation_token is not None:
            return PlainTextResponse(validation_token)
        notifications = payload.get("value", []) if payload else []
        if not isinstance(notifications, list):
            raise HTTPException(400, "invalid Outlook notification")
        for item in notifications:
            if not isinstance(item, dict):
                continue
            with factory() as session:
                mailbox = session.scalar(
                    select(MailboxConnection).where(
                        MailboxConnection.provider == "outlook",
                        MailboxConnection.webhook_subscription_id
                        == str(item.get("subscriptionId", "")),
                        MailboxConnection.webhook_client_state == str(item.get("clientState", "")),
                    )
                )
            if mailbox:
                await scan_from_webhook(mailbox.user_id, "outlook")
        return Response(status_code=202)

    return app
