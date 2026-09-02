import httpx
import pytest
import respx
from fastapi import HTTPException

from hotel_flight_matcher.auth import GMAIL_READONLY, GoogleTokenValidator


@pytest.mark.asyncio
@respx.mock
async def test_validates_audience_scope_and_expiry() -> None:
    route = respx.get("https://oauth2.googleapis.com/tokeninfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "aud": "client.apps.googleusercontent.com",
                "sub": "user-1",
                "email": "person@example.com",
                "scope": GMAIL_READONLY,
                "expires_in": "1200",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        validator = GoogleTokenValidator(
            client,
            "https://oauth2.googleapis.com/tokeninfo",
            "client.apps.googleusercontent.com",
        )
        user = await validator.validate("not-logged")
    assert route.called
    assert user.subject == "user-1"


@pytest.mark.asyncio
@respx.mock
async def test_rejects_token_for_another_client() -> None:
    respx.get("https://oauth2.googleapis.com/tokeninfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "aud": "attacker.apps.googleusercontent.com",
                "sub": "user-1",
                "scope": GMAIL_READONLY,
                "expires_in": "1200",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        validator = GoogleTokenValidator(
            client,
            "https://oauth2.googleapis.com/tokeninfo",
            "client.apps.googleusercontent.com",
        )
        with pytest.raises(HTTPException) as exc_info:
            await validator.validate("not-logged")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@respx.mock
async def test_accepts_access_token_user_id_shape() -> None:
    respx.get("https://oauth2.googleapis.com/tokeninfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "audience": "client.apps.googleusercontent.com",
                "user_id": "user-2",
                "scope": GMAIL_READONLY,
                "expires_in": "1200",
            },
        )
    )
    async with httpx.AsyncClient() as client:
        user = await GoogleTokenValidator(
            client,
            "https://oauth2.googleapis.com/tokeninfo",
            "client.apps.googleusercontent.com",
        ).validate("not-logged")
    assert user.subject == "user-2"
