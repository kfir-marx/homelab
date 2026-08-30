from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

PROXY = (
    Path(__file__).parents[3]
    / "ansible/roles/homelab_assistant/files/homelab-codex-socket-proxy.py"
)


def test_proxy_replaces_client_authorization_and_preserves_initial_frame() -> None:
    namespace = runpy.run_path(str(PROXY))
    authorize = cast(Callable[[bytes, str], bytes], namespace["authorized_handshake"])
    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Authorization: Bearer client-supplied\r\n"
        b"Upgrade: websocket\r\n\r\n"
        b"first-frame"
    )

    result = authorize(request, "unit-test-capability-token-1234567890")

    assert b"client-supplied" not in result
    assert result.count(b"Authorization:") == 1
    assert b"Bearer unit-test-capability-token-1234567890" in result
    assert result.endswith(b"\r\n\r\nfirst-frame")
