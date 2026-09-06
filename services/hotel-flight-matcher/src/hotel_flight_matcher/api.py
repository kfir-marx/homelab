from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Protocol, cast

import httpx
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text
from starlette.responses import Response

from .auth import bearer_token
from .config import Settings
from .database import (
    Agent,
    MailboxConnection,
    ProcessedMessage,
    initialize,
    make_engine,
    make_factory,
    new_agent,
    token_hash,
)
from .flights import ConfigFlightRepository, FlightRepository, load_flights, score_booking
from .llm import BookingExtractor, ExtractionError
from .mailboxes import MailboxError, MailboxReader, readers
from .models import (
    AgentCreated,
    AgentView,
    AnalysisResponse,
    AuthorizationUrl,
    EmailForAnalysis,
    FlightMatch,
    HotelBooking,
    Provider,
    ScanRequest,
    ScanResult,
)
from .oauth import OAuthError, OAuthService

logger = structlog.get_logger()

HOME_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>FlightStay Match</title><main><h1>FlightStay Match</h1>
<p>A backend that finds hotel confirmations related to configured flights.</p>
<p>Users connect Gmail or Outlook directly through the provider's official consent flow.
No browser extension is required.</p><p><a href=/privacy>Privacy policy</a> ·
<a href=/terms>Terms</a></p></main></html>"""

PRIVACY_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>FlightStay Match Privacy Policy</title><main><h1>Privacy Policy</h1>
<p>FlightStay Match requests delegated read-only Gmail or Outlook mail access only after the
user starts the provider's official OAuth consent flow. Long-lived authorization is encrypted at
rest. The service reads bounded message text, but not attachments, solely to identify hotel
booking confirmations and compare them with configured flights.</p>
<p>Email bodies are processed transiently and are not persisted. The service stores agent,
mailbox, processed-message, and match metadata needed for reliable repeated scans. Email data is
not sold or used for advertising. Users can revoke the grant through Google Account or Microsoft
Account connected-app settings. Contact: kfir.marx@gmail.com.</p></main></html>"""

TERMS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>FlightStay Match Terms</title><main><h1>Proof-of-concept terms</h1>
<p>Results are heuristic and may be incomplete or incorrect. Verify every booking and flight
directly with the provider. This service does not make, change, or cancel reservations.</p>
</main></html>"""


class Extractor(Protocol):
    @property
    def ready(self) -> bool: ...

    async def extract(self, email: EmailForAnalysis) -> HotelBooking: ...


def _best(matches: list[FlightMatch]) -> FlightMatch | None:
    return matches[0] if matches else None


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
    configured_flights = flight_repository or ConfigFlightRepository(
        load_flights(resolved.flights_config_path)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = httpx.AsyncClient(timeout=resolved.request_timeout_seconds)
        app.state.http_client = client
        app.state.oauth = OAuthService(resolved, factory, client)
        app.state.mailbox_readers = mailbox_readers or readers(client, resolved.gmail_query)
        if extractor is None:
            assert isinstance(configured_extractor, BookingExtractor)
            await configured_extractor.connect()
        yield
        if extractor is None:
            assert isinstance(configured_extractor, BookingExtractor)
            await configured_extractor.close()
        await client.aclose()
        engine.dispose()

    app = FastAPI(
        title="FlightStay Match",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> Agent:
        supplied = bearer_token(authorization)
        if not supplied:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "agent bearer token is required")
        with factory() as session:
            agent = session.scalar(
                select(Agent).where(Agent.access_token_hash == token_hash(supplied))
            )
            if not agent:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid agent bearer token")
            return agent

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
        ready_status = response.status_code != status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready" if ready_status else "not-ready",
            "dependencies": dependencies,
        }

    @app.post("/v1/agents", response_model=AgentCreated, status_code=201)
    async def create_agent() -> AgentCreated:
        with factory.begin() as session:
            agent, token = new_agent(session)
            return AgentCreated(agent_id=agent.id, access_token=token)

    @app.get("/v1/agents/me", response_model=AgentView)
    async def get_agent(agent: Agent = Depends(authenticate)) -> AgentView:  # noqa: B008
        with factory() as session:
            providers = session.scalars(
                select(MailboxConnection.provider).where(MailboxConnection.agent_id == agent.id)
            ).all()
        return AgentView(
            agent_id=agent.id,
            connected_mailboxes=[cast(Provider, provider) for provider in providers],
        )

    @app.post(
        "/v1/mailboxes/{provider}/authorization",
        response_model=AuthorizationUrl,
    )
    async def authorize_mailbox(
        provider: Provider,
        request: Request,
        agent: Agent = Depends(authenticate),  # noqa: B008
    ) -> AuthorizationUrl:
        try:
            url = request.app.state.oauth.authorization_url(agent.id, provider)
        except OAuthError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        return AuthorizationUrl(authorization_url=url)

    @app.get("/v1/oauth/{provider}/callback", response_class=HTMLResponse)
    async def oauth_callback(
        provider: Provider,
        request: Request,
        state: str = Query(min_length=20, max_length=200),
        code: str = Query(min_length=1, max_length=4096),
    ) -> str:
        try:
            mailbox = await request.app.state.oauth.complete(provider, state, code)
        except OAuthError as exc:
            logger.warning("mailbox_oauth_failed", provider=provider)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return (
            "<!doctype html><title>Mailbox connected</title><h1>Mailbox connected</h1>"
            f"<p>{mailbox.provider.title()} access was granted. You may close this window.</p>"
        )

    @app.post("/v1/scans", response_model=ScanResult)
    async def scan_mailbox(
        body: ScanRequest,
        request: Request,
        agent: Agent = Depends(authenticate),  # noqa: B008
    ) -> ScanResult:
        with factory() as session:
            mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.agent_id == agent.id,
                    MailboxConnection.provider == body.provider,
                )
            )
        if not mailbox:
            raise HTTPException(status.HTTP_409_CONFLICT, "mailbox is not connected")
        limit = min(
            body.maximum_messages or resolved.maximum_messages_per_scan,
            resolved.maximum_messages_per_scan,
        )
        try:
            access_token = await request.app.state.oauth.access_token(mailbox)
            messages = await request.app.state.mailbox_readers[body.provider].messages(
                access_token, limit
            )
        except (OAuthError, MailboxError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

        results: list[AnalysisResponse] = []
        skipped = 0
        confirmations = 0
        matched = 0
        flights = configured_flights.for_agent(agent.id)
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
                booking = await configured_extractor.extract(email)
            except ExtractionError as exc:
                logger.warning("hotel_extraction_failed", provider=body.provider)
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "all LLM backends failed") from exc
            matches = score_booking(booking, flights, resolved.match_threshold)
            best = _best(matches)
            analysis = AnalysisResponse(
                message_id=email.message_id,
                booking=booking,
                matches=matches,
                best_flight_id=best.flight_id if best else None,
                best_score=best.score if best else 0,
            )
            results.append(analysis)
            is_confirmation = booking.is_hotel_booking_confirmation
            confirmations += int(is_confirmation)
            is_match = bool(best and best.related)
            matched += int(is_match)
            if is_match and best:
                flight = next(item for item in flights if item.id == best.flight_id)
                print(  # noqa: T201 - this is the iteration-one function-call boundary
                    "found hotel booking "
                    f"{booking.model_dump(mode='json')} matching flight details "
                    f"{flight.model_dump(mode='json')}",
                    flush=True,
                )
            with factory.begin() as session:
                session.add(
                    ProcessedMessage(
                        mailbox_id=mailbox.id,
                        provider_message_id=email.message_id,
                        outcome="matched"
                        if is_match
                        else "confirmation"
                        if is_confirmation
                        else "other",
                        best_score=str(best.score) if best else None,
                        result_summary={
                            "is_hotel_booking_confirmation": is_confirmation,
                            "matched_flight_id": best.flight_id if is_match and best else None,
                        },
                    )
                )
        return ScanResult(
            provider=body.provider,
            messages_seen=len(messages),
            messages_skipped=skipped,
            messages_analyzed=len(results),
            confirmations_found=confirmations,
            matches_found=matched,
            results=results,
        )

    return app
