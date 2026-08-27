#!/usr/bin/env python3
"""Reconcile Maintainerr integrations from the media stack's retained state.

The source applications already persist the credentials that Maintainerr needs.
Reading them from the shared state volume avoids duplicating plaintext API keys
in Git or requiring a second manual configuration after disaster recovery.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol


class JsonApi(Protocol):
    def request(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> Any: ...


def read_arr_api_key(path: Path, application: str) -> str:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise RuntimeError(
            f"Could not read {application} configuration at {path}"
        ) from error

    api_key = (root.findtext("ApiKey") or "").strip()
    if not api_key:
        raise RuntimeError(f"{application} configuration at {path} has no API key")
    return api_key


def read_seerr_settings(path: Path) -> dict[str, Any]:
    try:
        settings = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"Could not read Seerr configuration at {path}") from error

    if not isinstance(settings, dict):
        raise RuntimeError(f"Seerr configuration at {path} is not a JSON object")
    return settings


def nested_secret(settings: dict[str, Any], section: str, key: str) -> str:
    section_value = settings.get(section)
    value = section_value.get(key) if isinstance(section_value, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Seerr configuration is missing {section}.{key}")
    return value.strip()


def response_ok(response: Any, operation: str) -> None:
    if not isinstance(response, dict) or response.get("status") != "OK":
        message = response.get("message") if isinstance(response, dict) else None
        suffix = f": {message}" if message else ""
        raise RuntimeError(f"Maintainerr {operation} failed{suffix}")


def normalize_url(value: Any) -> str:
    return str(value or "").rstrip("/").casefold()


def reconcile_arr(
    maintainerr: JsonApi,
    application: str,
    desired: dict[str, str],
) -> bool:
    endpoint = application.casefold()
    existing = maintainerr.request(f"/api/settings/{endpoint}")
    if not isinstance(existing, list):
        raise RuntimeError(f"Maintainerr returned invalid {application} settings")
    if len(existing) > 1:
        raise RuntimeError(
            f"Maintainerr has {len(existing)} {application} servers; expected at most one"
        )

    if existing:
        current = existing[0]
        matches = (
            current.get("serverName") == desired["serverName"]
            and normalize_url(current.get("url")) == normalize_url(desired["url"])
            and current.get("apiKey") == desired["apiKey"]
        )
        if matches:
            return False

    response_ok(
        maintainerr.request(
            f"/api/settings/test/{endpoint}", method="POST", body=desired
        ),
        f"{application} connection test",
    )
    if existing:
        setting_id = existing[0].get("id")
        if setting_id is None:
            raise RuntimeError(f"Maintainerr's {application} setting has no ID")
        response = maintainerr.request(
            f"/api/settings/{endpoint}/{setting_id}", method="PUT", body=desired
        )
    else:
        response = maintainerr.request(
            f"/api/settings/{endpoint}", method="POST", body=desired
        )
    response_ok(response, f"{application} settings update")
    return True


def reconcile_single_setting(
    maintainerr: JsonApi,
    endpoint: str,
    desired: dict[str, Any],
    operation: str,
) -> bool:
    existing = maintainerr.request(f"/api/settings/{endpoint}")
    if not isinstance(existing, dict):
        raise RuntimeError(f"Maintainerr returned invalid {operation} settings")

    matches = all(
        normalize_url(existing.get(key)) == normalize_url(value)
        if key.endswith("url") or key == "url"
        else existing.get(key) == value
        for key, value in desired.items()
    )
    if matches:
        return False

    response_ok(
        maintainerr.request(
            f"/api/settings/{endpoint}/test"
            if endpoint == "jellyfin"
            else f"/api/settings/test/{endpoint}",
            method="POST",
            body=desired,
        ),
        f"{operation} connection test",
    )
    response_ok(
        maintainerr.request(f"/api/settings/{endpoint}", method="POST", body=desired),
        f"{operation} settings update",
    )
    return True


def reconcile_integrations(maintainerr: JsonApi, state_root: Path) -> list[str]:
    """Reconcile required servers and return the names that changed."""
    seerr = read_seerr_settings(state_root / "seerr/settings.json")
    desired = {
        "Radarr": {
            "serverName": "Radarr",
            "url": "http://radarr:7878",
            "apiKey": read_arr_api_key(state_root / "radarr/config.xml", "Radarr"),
        },
        "Sonarr": {
            "serverName": "Sonarr",
            "url": "http://sonarr:8989",
            "apiKey": read_arr_api_key(state_root / "sonarr/config.xml", "Sonarr"),
        },
    }

    changed = []
    for application, setting in desired.items():
        if reconcile_arr(maintainerr, application, setting):
            changed.append(application)

    if reconcile_single_setting(
        maintainerr,
        "jellyfin",
        {
            "jellyfin_url": "http://jellyfin:8096",
            "jellyfin_api_key": nested_secret(seerr, "jellyfin", "apiKey"),
        },
        "Jellyfin",
    ):
        changed.append("Jellyfin")

    if reconcile_single_setting(
        maintainerr,
        "seerr",
        {
            "url": "http://seerr:5055",
            "api_key": nested_secret(seerr, "main", "apiKey"),
        },
        "Seerr",
    ):
        changed.append("Seerr")

    return changed
