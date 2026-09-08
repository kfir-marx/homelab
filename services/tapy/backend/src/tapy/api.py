from __future__ import annotations

import asyncio
import base64
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, cast

import httpx
import structlog
from fastapi import (
    BackgroundTasks,
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
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response as StarletteResponse

from .auth import bearer_token, new_session, password_hash, password_matches
from .config import Settings
from .database import (
    AuthIdentity,
    FlightIngestionRecord,
    FlightRecord,
    MailboxConnection,
    NotificationRecord,
    ProcessedMessage,
    User,
    UserSession,
    initialize,
    make_engine,
    make_factory,
    new_agent,
    token_hash,
)
from .flights import (
    DatabaseFlightRepository,
    FlightRepository,
    load_flights,
    record_to_flight,
    record_to_view,
    score_booking,
)
from .llm import BookingExtractor, ExtractionError
from .mailboxes import MailboxError, MailboxReader, readers
from .models import (
    AgentCreated,
    AgentView,
    AnalysisResponse,
    AuthorizationUrl,
    AuthProvider,
    AuthResult,
    EmailExtraction,
    EmailForAnalysis,
    FlightCreate,
    FlightMatch,
    FlightStatusUpdate,
    FlightTicket,
    FlightView,
    HotelBooking,
    LoginRequest,
    MailboxView,
    MetricsView,
    NotificationKind,
    NotificationsRead,
    NotificationView,
    Provider,
    RegisterRequest,
    ScanRequest,
    ScanResult,
    UpsellResult,
    UserUpdate,
    UserView,
)
from .oauth import OAuthError, OAuthService
from .webhooks import WebhookService, webhook_active

logger = structlog.get_logger()
SESSION_COOKIE = "tapy_session"

HOME_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Tapy</title>
<main><h1>Tapy</h1><p>Tapy identifies flight and hotel confirmations after you grant
read-only mailbox access.</p><p><a href=/privacy>Privacy policy</a> ·
<a href=/terms>Terms</a></p></main></html>"""

PRIVACY_PAGE = """<!doctype html><html lang=en><meta charset=utf-8><title>Tapy Privacy</title>
<main><h1>Privacy Policy</h1><p>Tapy requests delegated read-only Gmail or Outlook access through
the provider consent screen. Refresh tokens are encrypted. Message text is processed transiently
to identify flight and hotel confirmations and is not persisted. Users can revoke provider access
at any time. Contact: kfir.marx@gmail.com.</p></main></html>"""

TERMS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8><title>Tapy Terms</title>
<main><h1>Proof-of-concept terms</h1><p>Results are heuristic. Verify bookings directly with the
provider. Tapy does not make, change, or cancel reservations.</p></main></html>"""


class Extractor(Protocol):
    @property
    def ready(self) -> bool: ...

    async def extract(self, email: EmailForAnalysis) -> EmailExtraction | HotelBooking: ...


class EventHub:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[str]]] = {}

    def subscribe(self, user_id: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
        self._queues.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue[str]) -> None:
        queues = self._queues.get(user_id)
        if queues:
            queues.discard(queue)
            if not queues:
                self._queues.pop(user_id, None)

    def publish(self, user_id: str, event: str = "refresh") -> None:
        for queue in self._queues.get(user_id, set()):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)


def _best(matches: list[FlightMatch]) -> FlightMatch | None:
    return matches[0] if matches else None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalized(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


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


def _user_view(factory: sessionmaker[Session], user: User) -> UserView:
    with factory() as session:
        providers = session.scalars(
            select(AuthIdentity.provider).where(AuthIdentity.user_id == user.id)
        ).all()
        mailboxes = session.scalars(
            select(MailboxConnection).where(MailboxConnection.user_id == user.id)
        ).all()
    auth_providers = [str(item) for item in providers]
    if user.password_hash:
        auth_providers.insert(0, "password")
    return UserView(
        id=user.id,
        name=user.name,
        email=user.email,
        language=cast(Literal["en", "he"], user.language),
        auth_providers=auth_providers,
        mailboxes=[
            MailboxView(
                provider=cast(Provider, mailbox.provider),
                email_address=mailbox.email_address,
                webhook_active=webhook_active(mailbox),
            )
            for mailbox in mailboxes
        ],
    )


def create_app(
    settings: Settings | None = None,
    *,
    extractor: Extractor | None = None,
    mailbox_readers: Mapping[Provider, MailboxReader] | None = None,
    flight_repository: FlightRepository | None = None,
) -> FastAPI:
    resolved = settings or Settings()
    if extractor is None:
        resolved.require_rabbitmq()
    engine = make_engine(resolved)
    initialize(engine)
    factory = make_factory(engine)
    configured_extractor = extractor or BookingExtractor.from_settings(resolved)
    database_flights = DatabaseFlightRepository(factory)
    configured_flights = flight_repository or database_flights
    legacy_flights = None
    if resolved.flights_config_path.exists():
        with suppress(ValueError, OSError):
            legacy_flights = load_flights(resolved.flights_config_path)
    event_hub = EventHub()

    def add_notification(
        session: Session,
        user_id: str,
        kind: NotificationKind,
        title: str,
        message: str,
        flight_id: str | None = None,
    ) -> NotificationRecord:
        notification = NotificationRecord(
            user_id=user_id,
            flight_id=flight_id,
            kind=kind,
            title=title,
            message=message,
        )
        session.add(notification)
        return notification

    def is_duplicate_flight(
        session: Session,
        user_id: str,
        body: FlightCreate,
        departure_at: datetime | None,
    ) -> bool:
        candidates = session.scalars(
            select(FlightRecord).where(
                FlightRecord.user_id == user_id,
                FlightRecord.arrival_date == body.arrival_date,
            )
        ).all()
        for candidate in candidates:
            same_person = _normalized(candidate.passenger_name) == _normalized(body.passenger_name)
            codes_known = candidate.destination_code != "UNK" and body.destination_code != "UNK"
            same_destination = (
                candidate.destination_code.casefold() == body.destination_code.casefold()
                if codes_known
                else _normalized(candidate.destination_city) == _normalized(body.destination_city)
            )
            if not (same_person and same_destination):
                continue
            metadata = session.get(FlightIngestionRecord, candidate.id)
            if departure_at and metadata and metadata.departure_at:
                if _utc(metadata.departure_at) == _utc(departure_at):
                    return True
                continue
            return True
        return False

    def available_booking_ref(
        session: Session, user_id: str, requested: str | None, *, allow_suffix: bool
    ) -> str:
        base = (
            requested.strip()
            if requested and requested.strip()
            else f"TPY-{secrets.token_hex(4).upper()}"
        )
        candidate = base[:64]
        number = 2
        while session.scalar(
            select(FlightRecord.id).where(
                FlightRecord.user_id == user_id,
                FlightRecord.booking_ref == candidate,
            )
        ):
            if not allow_suffix:
                raise ValueError("this booking reference already exists")
            suffix = f"-{number}"
            candidate = base[: 64 - len(suffix)] + suffix
            number += 1
        return candidate

    def insert_flight(
        session: Session,
        user_id: str,
        body: FlightCreate,
        *,
        source: Literal["manual", "email"],
        mailbox_id: str | None = None,
        provider_message_id: str | None = None,
        ticket_number: str | None = None,
        departure_at: datetime | None = None,
        return_at: datetime | None = None,
        source_details: dict[str, object] | None = None,
    ) -> FlightRecord | None:
        if is_duplicate_flight(session, user_id, body, departure_at):
            return None
        record = FlightRecord(
            user_id=user_id,
            booking_ref=available_booking_ref(
                session, user_id, body.booking_ref, allow_suffix=source == "email"
            ),
            passenger_name=body.passenger_name.strip(),
            party_size=body.party_size,
            email=body.email.strip(),
            phone=body.phone.strip(),
            origin=body.origin.strip().upper(),
            origin_city=body.origin_city.strip(),
            destination_code=body.destination_code.strip().upper(),
            destination_city=body.destination_city.strip(),
            destination_country=body.destination_country.strip(),
            arrival_date=body.arrival_date,
            departure_date=body.departure_date,
            flight_cost_usd=body.flight_cost_usd,
            hotel_cost_usd=body.hotel_cost_usd,
            is_open_for_upsell=True,
        )
        session.add(record)
        session.flush()
        session.add(
            FlightIngestionRecord(
                flight_id=record.id,
                source=source,
                mailbox_id=mailbox_id,
                provider_message_id=provider_message_id,
                ticket_number=ticket_number,
                departure_at=_utc(departure_at) if departure_at else None,
                return_at=_utc(return_at) if return_at else None,
                details=source_details or {},
            )
        )
        return record

    def flight_from_ticket(ticket: FlightTicket) -> FlightCreate:
        missing: list[str] = []
        if not ticket.passenger_name:
            missing.append("passenger_name")
        if not (ticket.destination_code or ticket.destination_city):
            missing.append("destination")
        if not ticket.departure_at:
            missing.append("departure_at")
        if missing:
            raise ValueError("missing " + ", ".join(missing))
        assert ticket.passenger_name and ticket.departure_at
        departure_at = _utc(ticket.departure_at)
        return_at = _utc(ticket.return_at) if ticket.return_at else departure_at
        return FlightCreate(
            booking_ref=ticket.booking_ref or ticket.ticket_number,
            passenger_name=ticket.passenger_name,
            party_size=1,
            email=ticket.email or "",
            phone=ticket.phone or "",
            origin=ticket.origin_code or "UNK",
            origin_city=ticket.origin_city or ticket.origin_code or "Unknown",
            destination_code=ticket.destination_code or "UNK",
            destination_city=ticket.destination_city or ticket.destination_code or "Unknown",
            destination_country=ticket.destination_country or "",
            arrival_date=departure_at.date(),
            departure_date=return_at.date(),
            flight_cost_usd=ticket.flight_cost_usd or 0,
            hotel_cost_usd=0,
        )

    def notification_view(record: NotificationRecord) -> NotificationView:
        return NotificationView(
            id=record.id,
            kind=cast(NotificationKind, record.kind),
            title=record.title,
            message=record.message,
            flight_id=record.flight_id,
            created_at=record.created_at,
            read_at=record.read_at,
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
                    await app.state.webhooks.ensure(mailbox)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = httpx.AsyncClient(timeout=resolved.request_timeout_seconds)
        oauth = OAuthService(resolved, factory, client)
        app.state.http_client = client
        app.state.oauth = oauth
        app.state.webhooks = WebhookService(resolved, factory, oauth, client)
        app.state.mailbox_readers = mailbox_readers or readers(client, resolved.gmail_query)
        if extractor is None:
            assert isinstance(configured_extractor, BookingExtractor)
            await configured_extractor.connect()
        renewal_task = asyncio.create_task(renew_webhooks(app))
        yield
        renewal_task.cancel()
        with suppress(asyncio.CancelledError):
            await renewal_task
        if extractor is None:
            assert isinstance(configured_extractor, BookingExtractor)
            await configured_extractor.close()
        await client.aclose()
        engine.dispose()

    app = FastAPI(
        title="Tapy",
        version="0.3.0",
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
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
            if request.url.path.startswith("/v1/oauth/")
            else "default-src 'none'; style-src 'unsafe-inline'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def authenticate(
        authorization: Annotated[str | None, Header()] = None,
        tapy_session: Annotated[str | None, Cookie()] = None,
    ) -> User:
        supplied = tapy_session or bearer_token(authorization)
        if not supplied:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication is required")
        now = datetime.now(UTC)
        with factory() as session:
            if supplied.startswith("ses_"):
                record = session.get(UserSession, token_hash(supplied))
                user = (
                    session.get(User, record.user_id)
                    if record and _utc(record.expires_at) > now
                    else None
                )
            else:
                user = session.scalar(
                    select(User).where(User.access_token_hash == token_hash(supplied))
                )
            if not user:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session")
            return user

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.method == "POST" and request.url.path == "/v1/flights":
            try:
                user = authenticate(
                    request.headers.get("authorization"),
                    request.cookies.get(SESSION_COOKIE),
                )
            except HTTPException:
                user = None
            if user:
                with factory.begin() as session:
                    add_notification(
                        session,
                        user.id,
                        "flight_add_failed",
                        "Could not add flight",
                        "The supplied flight details were invalid. Review them and try again.",
                    )
                event_hub.publish(user.id)
        return await request_validation_exception_handler(request, exc)

    def flight_records(user_id: str) -> list[FlightRecord]:
        with factory() as session:
            return list(
                session.scalars(
                    select(FlightRecord)
                    .where(FlightRecord.user_id == user_id)
                    .order_by(FlightRecord.arrival_date, FlightRecord.created_at)
                ).all()
            )

    async def scan(user_id: str, provider: Provider, maximum: int | None = None) -> ScanResult:
        with factory() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user_id,
                    MailboxConnection.provider == provider,
                )
            )
        if not mailbox:
            raise HTTPException(status.HTTP_409_CONFLICT, "mailbox is not connected")
        limit = min(
            maximum or resolved.maximum_messages_per_scan, resolved.maximum_messages_per_scan
        )
        try:
            access_token = await app.state.oauth.access_token(mailbox)
            messages = await app.state.mailbox_readers[provider].messages(access_token, limit)
        except (OAuthError, MailboxError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

        results: list[AnalysisResponse] = []
        skipped = confirmations = matched = 0
        flight_confirmations = tickets_found = flights_added = 0
        flights = configured_flights.for_agent(user_id)
        for email in messages:
            with factory() as session:
                seen = session.scalar(
                    select(ProcessedMessage.id).where(
                        ProcessedMessage.mailbox_id == mailbox.id,
                        ProcessedMessage.provider_message_id == email.message_id,
                    )
                )
            if seen:
                skipped += 1
                continue
            try:
                extracted = await configured_extractor.extract(email)
            except ExtractionError as exc:
                logger.warning("email_extraction_failed", provider=provider, user_id=user_id)
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "all LLM backends failed") from exc
            if isinstance(extracted, HotelBooking):
                booking = extracted
                flight_booking = None
            else:
                booking = extracted.hotel_booking
                flight_booking = extracted.flight_booking

            added_ids: list[str] = []
            email_tickets = (
                flight_booking.tickets
                if flight_booking
                and flight_booking.is_flight_booking_confirmation
                and flight_booking.booking_status == "confirmed"
                else []
            )
            if email_tickets:
                flight_confirmations += 1
                tickets_found += len(email_tickets)
            for ticket in email_tickets:
                try:
                    body = flight_from_ticket(ticket)
                    with factory.begin() as session:
                        record = insert_flight(
                            session,
                            user_id,
                            body,
                            source="email",
                            mailbox_id=mailbox.id,
                            provider_message_id=email.message_id,
                            ticket_number=ticket.ticket_number,
                            departure_at=ticket.departure_at,
                            return_at=ticket.return_at,
                            source_details=ticket.model_dump(mode="json"),
                        )
                        if record:
                            add_notification(
                                session,
                                user_id,
                                "flight_added",
                                "New flight added",
                                f"{record.passenger_name}: {record.origin} → "
                                f"{record.destination_code} was extracted from email.",
                                record.id,
                            )
                            session.flush()
                    if record:
                        added_ids.append(record.id)
                        flights_added += 1
                        if configured_flights is database_flights:
                            flights = [*flights, record_to_flight(record)]
                except (IntegrityError, ValueError) as exc:
                    logger.warning(
                        "email_flight_add_failed",
                        user_id=user_id,
                        provider=provider,
                        message_id=email.message_id,
                        error=str(exc),
                    )
                    with factory.begin() as session:
                        add_notification(
                            session,
                            user_id,
                            "flight_add_failed",
                            "Could not add flight",
                            "A flight ticket in an email could not be added. Review the email "
                            "and enter the flight manually.",
                        )
                    event_hub.publish(user_id)
            if added_ids:
                event_hub.publish(user_id)
            matches = score_booking(booking, flights, resolved.match_threshold)
            best = _best(matches)
            is_confirmation = booking.is_hotel_booking_confirmation
            is_match = bool(best and best.related)
            confirmations += int(is_confirmation)
            matched += int(is_match)
            results.append(
                AnalysisResponse(
                    message_id=email.message_id,
                    booking=booking,
                    matches=matches,
                    best_flight_id=best.flight_id if best else None,
                    best_score=best.score if best else 0,
                    flight_tickets_found=len(email_tickets),
                    flights_added=added_ids,
                )
            )
            if is_match and best:
                flight_changed = False
                with factory.begin() as session:
                    record = session.get(FlightRecord, best.flight_id)
                    if record and record.user_id == user_id:
                        if record.matched_hotel:
                            logger.info(
                                "flight_already_has_hotel_match",
                                user_id=user_id,
                                flight_id=record.id,
                                message_id=email.message_id,
                            )
                        else:
                            record.matched_hotel = booking.model_dump(mode="json")
                            record.is_open_for_upsell = False
                            record.closed_reason = "hotel_match"
                            print(
                                "found hotel booking matching flight details "
                                f"user_id={user_id} flight_id={record.id} "
                                f"message_id={email.message_id}",
                                flush=True,
                            )
                            flight_changed = True
                    else:
                        flight = next(item for item in flights if item.id == best.flight_id)
                        print(
                            "found hotel booking "
                            f"{booking.model_dump(mode='json')} matching flight details "
                            f"{flight.model_dump(mode='json')}",
                            flush=True,
                        )
                if flight_changed:
                    event_hub.publish(user_id)
            with factory.begin() as session:
                session.add(
                    ProcessedMessage(
                        mailbox_id=mailbox.id,
                        provider_message_id=email.message_id,
                        outcome=(
                            "flight_added"
                            if added_ids
                            else "matched"
                            if is_match
                            else "confirmation"
                            if is_confirmation
                            else "other"
                        ),
                        best_score=str(best.score) if best else None,
                        result_summary={
                            "is_hotel_booking_confirmation": is_confirmation,
                            "is_flight_booking_confirmation": bool(
                                flight_booking and flight_booking.is_flight_booking_confirmation
                            ),
                            "flights_added": added_ids,
                            "matched_flight_id": best.flight_id if is_match and best else None,
                        },
                    )
                )
        return ScanResult(
            provider=provider,
            messages_seen=len(messages),
            messages_skipped=skipped,
            messages_analyzed=len(results),
            confirmations_found=confirmations,
            matches_found=matched,
            flight_confirmations_found=flight_confirmations,
            flight_tickets_found=tickets_found,
            flights_added=flights_added,
            results=results,
        )

    async def scan_from_webhook(user_id: str, provider: Provider) -> None:
        try:
            await scan(user_id, provider)
        except Exception as exc:
            logger.warning(
                "webhook_scan_failed", user_id=user_id, provider=provider, error=str(exc)
            )

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
            "rabbitmq": "ready" if configured_extractor.ready else "unavailable",
        }
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            dependencies["database"] = "unavailable"
        if isinstance(configured_extractor, BookingExtractor):
            dependencies["llm_backends"] = configured_extractor.readiness
        if not configured_extractor.ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready"
            if response.status_code != status.HTTP_503_SERVICE_UNAVAILABLE
            else "not-ready",
            "dependencies": dependencies,
        }

    @app.post("/v1/auth/register", response_model=AuthResult, status_code=201)
    async def register(body: RegisterRequest, response: Response) -> AuthResult:
        try:
            with factory.begin() as session:
                user = User(
                    email=str(body.email).casefold(),
                    name=body.name.strip(),
                    password_hash=password_hash(body.password),
                )
                session.add(user)
                session.flush()
                token, _ = new_session(session, user.id, resolved.session_days)
        except IntegrityError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "an account already exists for this email"
            ) from exc
        _set_session_cookie(response, token, resolved)
        return AuthResult(user=_user_view(factory, user))

    @app.post("/v1/auth/login", response_model=AuthResult)
    async def login(body: LoginRequest, response: Response) -> AuthResult:
        with factory.begin() as session:
            user = session.scalar(select(User).where(User.email == str(body.email).casefold()))
            if not user or not password_matches(body.password, user.password_hash):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "email or password is incorrect")
            token, _ = new_session(session, user.id, resolved.session_days)
        _set_session_cookie(response, token, resolved)
        return AuthResult(user=_user_view(factory, user))

    @app.post("/v1/auth/logout", status_code=204)
    async def logout(
        response: Response,
        tapy_session: Annotated[str | None, Cookie()] = None,
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
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    @app.get("/v1/users/me", response_model=UserView)
    async def get_user(user: User = Depends(authenticate)) -> UserView:  # noqa: B008
        return _user_view(factory, user)

    @app.patch("/v1/users/me", response_model=UserView)
    async def update_user(
        body: UserUpdate,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> UserView:
        with factory.begin() as session:
            current = session.get(User, user.id)
            assert current
            if body.name is not None:
                current.name = body.name.strip()
            if body.language is not None:
                current.language = body.language
            session.flush()
            user = current
        event_hub.publish(user.id)
        return _user_view(factory, user)

    @app.get("/v1/flights", response_model=list[FlightView])
    async def list_flights(user: User = Depends(authenticate)) -> list[FlightView]:  # noqa: B008
        return [record_to_view(item) for item in flight_records(user.id)]

    @app.post("/v1/flights", response_model=FlightView, status_code=201)
    async def create_flight(
        body: FlightCreate,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> FlightView:
        try:
            with factory.begin() as session:
                record = insert_flight(session, user.id, body, source="manual")
                if not record:
                    raise ValueError("this flight already exists")
                add_notification(
                    session,
                    user.id,
                    "flight_added",
                    "New flight added",
                    f"{record.passenger_name}: {record.origin} → "
                    f"{record.destination_code} was added manually.",
                    record.id,
                )
                session.flush()
        except (IntegrityError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, ValueError) else "could not save the flight"
            with factory.begin() as session:
                add_notification(
                    session,
                    user.id,
                    "flight_add_failed",
                    "Could not add flight",
                    detail,
                )
            event_hub.publish(user.id)
            raise HTTPException(status.HTTP_409_CONFLICT, detail) from exc
        event_hub.publish(user.id)
        return record_to_view(record)

    @app.patch("/v1/flights/{flight_id}/status", response_model=FlightView)
    async def update_flight_status(
        flight_id: str,
        body: FlightStatusUpdate,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> FlightView:
        with factory.begin() as session:
            record = session.get(FlightRecord, flight_id)
            if not record or record.user_id != user.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "flight was not found")
            record.is_open_for_upsell = body.status == "open"
            record.closed_reason = None if body.status == "open" else body.status
            if body.status == "open":
                record.matched_hotel = None
            session.flush()
        event_hub.publish(user.id)
        return record_to_view(record)

    @app.post("/v1/flights/{flight_id}/send-upsell", response_model=UpsellResult)
    async def send_flight_upsell(
        flight_id: str,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> UpsellResult:
        with factory() as session:
            record = session.get(FlightRecord, flight_id)
            if not record or record.user_id != user.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "flight was not found")
            if not record.is_open_for_upsell:
                raise HTTPException(status.HTTP_409_CONFLICT, "flight is already closed")

        async def fail(detail: str) -> None:
            with factory.begin() as session:
                add_notification(
                    session,
                    user.id,
                    "whatsapp_failed",
                    "WhatsApp delivery failed",
                    f"The offer for {record.booking_ref} could not be sent: {detail}",
                    record.id,
                )
            event_hub.publish(user.id)

        account_sid = resolved.twilio_account_sid.strip()
        auth_token = resolved.twilio_auth_token.get_secret_value()
        if not account_sid or not auth_token:
            detail = "Twilio is not configured"
            await fail(detail)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)
        if not record.phone.strip():
            detail = "the customer has no phone number"
            await fail(detail)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)

        first_name = record.passenger_name.split("&", 1)[0].split(",", 1)[0].strip()
        message_body = (
            f"היי {first_name}! ✈️\n\n"
            f"הנסיעה שלך עם Tapy ל-{record.destination_city} "
            f"({record.arrival_date.isoformat()} - {record.departure_date.isoformat()}) אושרה.\n\n"
            "מצאנו עבורך 3 מלונות במחירים בלעדיים לתאריכים שלך. "
            "ניתן לשריין כל אחד מהם בלחיצה אחת — ללא חיוב עד הצ'ק-אין:\n\n"
            f"👉 {resolved.hotel_offer_url}\n\nלהסרה השב STOP."
        )
        try:
            response = await app.state.http_client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                data={
                    "To": f"whatsapp:{record.phone.strip()}",
                    "From": resolved.twilio_whatsapp_from,
                    "Body": message_body,
                },
                auth=(account_sid, auth_token),
            )
            response.raise_for_status()
            message_sid = str(response.json()["sid"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "whatsapp_delivery_failed",
                user_id=user.id,
                flight_id=record.id,
                error=type(exc).__name__,
            )
            detail = (
                "Twilio rejected the message"
                if isinstance(exc, httpx.HTTPStatusError)
                else "Twilio could not be reached"
            )
            await fail(detail)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail) from exc

        with factory.begin() as session:
            current = session.get(FlightRecord, flight_id)
            if not current or current.user_id != user.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "flight was not found")
            current.is_open_for_upsell = False
            current.closed_reason = "upsold"
            add_notification(
                session,
                user.id,
                "upsell_sent",
                "Upsell sent",
                f"The WhatsApp offer for {current.booking_ref} was sent and the flight closed.",
                current.id,
            )
            session.flush()
        event_hub.publish(user.id)
        return UpsellResult(flight=record_to_view(current), message_sid=message_sid)

    @app.get("/v1/notifications", response_model=list[NotificationView])
    async def list_notifications(
        user: User = Depends(authenticate),  # noqa: B008
    ) -> list[NotificationView]:
        with factory() as session:
            records = session.scalars(
                select(NotificationRecord)
                .where(NotificationRecord.user_id == user.id)
                .order_by(NotificationRecord.created_at.desc())
                .limit(100)
            ).all()
        return [notification_view(record) for record in records]

    @app.post("/v1/notifications/read", status_code=204)
    async def mark_notifications_read(
        body: NotificationsRead,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> None:
        now = datetime.now(UTC)
        with factory.begin() as session:
            unread = session.scalars(
                select(NotificationRecord).where(
                    NotificationRecord.user_id == user.id,
                    NotificationRecord.id.in_(body.notification_ids),
                    NotificationRecord.read_at.is_(None),
                )
            ).all()
            for notification in unread:
                notification.read_at = now
        event_hub.publish(user.id)

    @app.get("/v1/metrics", response_model=MetricsView)
    async def metrics(user: User = Depends(authenticate)) -> MetricsView:  # noqa: B008
        records = flight_records(user.id)
        statuses = [record_to_view(record).status for record in records]
        upsold = statuses.count("upsold")
        opened = statuses.count("open")
        declined = statuses.count("declined")
        hotel = statuses.count("past")
        hotel_revenue = sum(
            r.hotel_cost_usd for r, s in zip(records, statuses, strict=True) if s == "upsold"
        )
        potential_value = sum(
            r.hotel_cost_usd for r, s in zip(records, statuses, strict=True) if s == "open"
        )
        handled = upsold + declined
        return MetricsView(
            total_flights=len(records),
            upsold_count=upsold,
            open_count=opened,
            declined_count=declined,
            hotel_on_file_count=hotel,
            closing_rate=upsold / handled if handled else 0,
            net_profit=round(hotel_revenue * 0.01, 2),
            potential_profit=round(potential_value * 0.01, 2),
            total_hotel_revenue=hotel_revenue,
        )

    @app.get("/v1/events")
    async def events(user: User = Depends(authenticate)) -> StreamingResponse:  # noqa: B008
        async def stream() -> AsyncIterator[str]:
            queue = event_hub.subscribe(user.id)
            try:
                yield "event: ready\ndata: connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=25)
                        yield f"event: {event}\ndata: refresh\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                event_hub.unsubscribe(user.id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # Iteration-one compatibility API. New clients use /v1/auth and /v1/users/me.
    @app.post("/v1/agents", response_model=AgentCreated, status_code=201)
    async def create_agent() -> AgentCreated:
        with factory.begin() as session:
            user, token = new_agent(session)
            if legacy_flights:
                for flight in legacy_flights.flights:
                    session.add(
                        FlightRecord(
                            user_id=user.id,
                            booking_ref=flight.id,
                            passenger_name=flight.label,
                            origin="UNK",
                            origin_city="Unknown",
                            destination_code=(flight.destination.airport_codes or ["UNK"])[0],
                            destination_city=flight.destination.city,
                            destination_country=flight.destination.country,
                            arrival_date=flight.arrival_date,
                            departure_date=flight.departure_date,
                        )
                    )
        return AgentCreated(agent_id=user.id, access_token=token)

    @app.get("/v1/agents/me", response_model=AgentView)
    async def get_agent(user: User = Depends(authenticate)) -> AgentView:  # noqa: B008
        view = _user_view(factory, user)
        return AgentView(
            agent_id=user.id,
            connected_mailboxes=[mailbox.provider for mailbox in view.mailboxes],
        )

    @app.post("/v1/mailboxes/{provider}/authorization", response_model=AuthorizationUrl)
    async def authorize_mailbox(
        provider: Provider,
        request: Request,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> AuthorizationUrl:
        try:
            return AuthorizationUrl(
                authorization_url=request.app.state.oauth.authorization_url(user.id, provider)
            )
        except OAuthError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    @app.delete("/v1/mailboxes/{provider}", status_code=204)
    async def disconnect_mailbox(
        provider: Provider,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> None:
        with factory.begin() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user.id,
                    MailboxConnection.provider == provider,
                )
            )
            if mailbox:
                session.delete(mailbox)
        event_hub.publish(user.id)

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
                auth_provider: AuthProvider = "google" if provider == "gmail" else "microsoft"
                completion = await request.app.state.oauth.complete_login(
                    auth_provider, state, code
                )
                with factory.begin() as session:
                    token, _ = new_session(session, completion.user.id, resolved.session_days)
                response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
                _set_session_cookie(response, token, resolved)
                return response
            mailbox = await request.app.state.oauth.complete(provider, state, code)
            await request.app.state.webhooks.ensure(mailbox)
            event_hub.publish(mailbox.user_id)
        except OAuthError as exc:
            logger.warning("oauth_failed", provider=provider, error=str(exc))
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return HTMLResponse(
            "<!doctype html><title>Mailbox connected</title><h1>Mailbox connected</h1>"
            f"<p>{provider.title()} access was granted. You may close this window.</p>"
            "<script>if(window.opener){window.opener.postMessage('tapy-mailbox-connected',"
            "window.location.origin);window.close()}else{window.location.replace('/')}</script>"
        )

    @app.post("/v1/scans", response_model=ScanResult)
    async def scan_mailbox(
        body: ScanRequest,
        user: User = Depends(authenticate),  # noqa: B008
    ) -> ScanResult:
        return await scan(user.id, body.provider, body.maximum_messages)

    @app.post("/v1/webhooks/gmail", status_code=202)
    async def gmail_webhook(
        payload: dict[str, object],
        background: BackgroundTasks,
        token: str = Query(default=""),
    ) -> dict[str, str]:
        expected = resolved.webhook_verification_token.get_secret_value()
        if expected and not hmac.compare_digest(token, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook token")
        try:
            message = cast(dict[str, object], payload["message"])
            raw = str(message["data"])
            notice = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            email = str(notice["emailAddress"]).casefold()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid Gmail notification") from exc
        with factory() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.provider == "gmail",
                    func.lower(MailboxConnection.email_address) == email,
                )
            )
        if mailbox:
            background.add_task(scan_from_webhook, mailbox.user_id, "gmail")
        return {"status": "accepted"}

    @app.post("/v1/webhooks/outlook")
    async def outlook_webhook(
        background: BackgroundTasks,
        payload: dict[str, object] | None = None,
        validation_token: str | None = Query(default=None, alias="validationToken"),
        token: str = Query(default=""),
    ) -> StarletteResponse:
        expected = resolved.webhook_verification_token.get_secret_value()
        if expected and not hmac.compare_digest(token, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook token")
        if validation_token is not None:
            return PlainTextResponse(validation_token)
        notifications = payload.get("value", []) if payload else []
        if not isinstance(notifications, list):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid Outlook notification")
        for notification in notifications:
            if not isinstance(notification, dict):
                continue
            subscription_id = str(notification.get("subscriptionId", ""))
            client_state = str(notification.get("clientState", ""))
            with factory() as session:
                mailbox = session.scalar(
                    select(MailboxConnection).where(
                        MailboxConnection.provider == "outlook",
                        MailboxConnection.webhook_subscription_id == subscription_id,
                        MailboxConnection.webhook_client_state == client_state,
                    )
                )
            if mailbox:
                background.add_task(scan_from_webhook, mailbox.user_id, "outlook")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    return app
