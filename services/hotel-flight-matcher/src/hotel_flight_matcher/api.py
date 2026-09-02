from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from .auth import AuthenticatedUser, GoogleTokenValidator, PerUserRateLimiter
from .config import Settings
from .flights import load_flights, score_booking
from .llm import BookingExtractor, ExtractionError
from .models import AnalysisResponse, EmailForAnalysis, FlightConfiguration

logger = structlog.get_logger()

HOME_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>FlightStay Match</title><main><h1>FlightStay Match</h1>
<p>A proof of concept that finds hotel confirmations related to your flights.</p>
<p>The Chrome extension reads Gmail only after your consent, sends bounded message text
over HTTPS for private inference, never sends attachments, and does not retain email content.</p>
<p><a href=/privacy>Privacy policy</a> · <a href=/terms>Terms</a></p></main></html>"""

PRIVACY_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>FlightStay Match Privacy Policy</title><main><h1>Privacy Policy</h1>
<p>FlightStay Match requests read-only Gmail access only when a user starts a scan.</p>
<p>The extension reads message headers and bounded message text, but not attachments. It sends
that data and a short-lived Google access token over HTTPS to the FlightStay Match service solely
to identify hotel bookings and compare them with configured flights.</p>
<p>Email content, Google tokens, and extracted bookings are processed transiently and are not
persisted by the service. They are not used for advertising, sold, or read by humans except with
the user's explicit consent for support or when legally/security required.</p>
<p>Use of Google user data complies with the Chrome Web Store User Data Policy, including its
Limited Use requirements. Disconnecting in the extension clears its cached Chrome session;
users can revoke the app's grant completely in their Google Account. Contact:
kfir.marx@gmail.com.</p></main></html>"""

TERMS_PAGE = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>FlightStay Match Terms</title><main><h1>Proof-of-concept terms</h1>
<p>Results are heuristic and may be incomplete or incorrect. Verify every booking and flight
directly with the provider. This service does not make, change, or cancel reservations.</p>
</main></html>"""


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google authorization is required")
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not value.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid authorization header")
    return value.strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = httpx.AsyncClient(timeout=resolved.request_timeout_seconds)
        app.state.http_client = client
        app.state.flights = load_flights(resolved.flights_config_path)
        app.state.validator = GoogleTokenValidator(
            client, str(resolved.google_tokeninfo_url), resolved.google_oauth_client_id
        )
        app.state.extractor = BookingExtractor(
            client, str(resolved.llm_base_url), resolved.llm_api_key, resolved.llm_model
        )
        app.state.rate_limiter = PerUserRateLimiter(resolved.per_user_requests_per_minute)
        yield
        await client.aclose()

    app = FastAPI(
        title="FlightStay Match",
        version="0.1.0",
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

    async def authenticate(
        request: Request, authorization: str | None = Header(default=None)
    ) -> AuthenticatedUser:
        validator: GoogleTokenValidator = request.app.state.validator
        user = await validator.validate(_bearer_token(authorization))
        request.app.state.rate_limiter.check(user.subject)
        return user

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
    async def ready(request: Request) -> dict[str, str]:
        flights: FlightConfiguration = request.app.state.flights
        return {"status": "ready", "flight_config_version": str(flights.version)}

    @app.post("/v1/analyze", response_model=AnalysisResponse)
    async def analyze(
        email: EmailForAnalysis,
        request: Request,
        user: AuthenticatedUser = Depends(authenticate),  # noqa: B008
    ) -> AnalysisResponse:
        del user
        try:
            booking = await request.app.state.extractor.extract(email)
        except ExtractionError as exc:
            logger.warning("hotel_extraction_failed", message_id_length=len(email.message_id))
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "private model unavailable") from exc
        flights: FlightConfiguration = request.app.state.flights
        matches = score_booking(booking, flights, resolved.related_threshold)
        best = matches[0] if matches else None
        return AnalysisResponse(
            message_id=email.message_id,
            booking=booking,
            matches=matches,
            best_flight_id=best.flight_id if best else None,
            best_probability=best.probability if best else 0,
        )

    return app
