from __future__ import annotations

import logging

import pytest

from homelab_assistant import main


def test_main_suppresses_token_bearing_httpx_request_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging, "basicConfig", lambda **_kwargs: None)
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)
    try:
        main.configure_logging()
        assert not httpx_logger.isEnabledFor(logging.INFO)
    finally:
        httpx_logger.setLevel(original_level)
