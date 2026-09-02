import json
from pathlib import Path

from fastapi.testclient import TestClient

from hotel_flight_matcher.api import create_app
from hotel_flight_matcher.config import Settings


def test_public_pages_and_authenticated_api_boundary(tmp_path: Path) -> None:
    config = tmp_path / "flights.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "flights": [
                    {
                        "id": "test",
                        "label": "Test trip",
                        "arrival_date": "2026-10-12",
                        "departure_date": "2026-10-17",
                        "destination": {"city": "London", "country": "United Kingdom"},
                    }
                ],
            }
        )
    )
    settings = Settings(
        google_oauth_client_id="client.apps.googleusercontent.com",
        llm_api_key="x" * 32,
        flights_config_path=config,
    )
    with TestClient(create_app(settings)) as client:
        privacy = client.get("/privacy")
        assert privacy.status_code == 200
        assert "gmail" in privacy.text.casefold()
        assert privacy.headers["cache-control"] == "no-store"
        response = client.post(
            "/v1/analyze",
            json={"message_id": "m1", "body_text": "Hotel confirmation"},
        )
        assert response.status_code == 401
        assert "Google authorization" in response.json()["detail"]
