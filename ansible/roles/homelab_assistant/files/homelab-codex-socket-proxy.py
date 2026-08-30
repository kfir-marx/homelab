"""Relay a group-protected Unix WebSocket to authenticated loopback Codex."""

from __future__ import annotations

import argparse
import grp
import os
import re
import select
import signal
import socket
import sys
import threading
from pathlib import Path

HEADER_END = b"\r\n\r\n"
MAX_HANDSHAKE_BYTES = 65536
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~-]{32,512}")


def authorized_handshake(request: bytes, token: str) -> bytes:
    """Replace any client authorization with the host-held capability token."""
    header, separator, remainder = request.partition(HEADER_END)
    if not separator:
        raise ValueError("incomplete WebSocket handshake")
    lines = header.split(b"\r\n")
    if not lines or not lines[0].startswith(b"GET "):
        raise ValueError("invalid WebSocket request line")
    filtered = [
        line for line in lines if not line.lower().startswith(b"authorization:")
    ]
    filtered.append(b"Authorization: Bearer " + token.encode("ascii"))
    return b"\r\n".join(filtered) + HEADER_END + remainder


def read_handshake(connection: socket.socket) -> bytes:
    request = bytearray()
    while HEADER_END not in request:
        chunk = connection.recv(8192)
        if not chunk:
            raise ConnectionError("client closed before WebSocket handshake")
        request.extend(chunk)
        if len(request) > MAX_HANDSHAKE_BYTES:
            raise ValueError("WebSocket handshake is too large")
    return bytes(request)


def relay(left: socket.socket, right: socket.socket) -> None:
    while True:
        readable, _, _ = select.select((left, right), (), ())
        for source in readable:
            destination = right if source is left else left
            data = source.recv(65536)
            if not data:
                return
            destination.sendall(data)


def handle_client(
    client: socket.socket, upstream_host: str, upstream_port: int, token: str
) -> None:
    try:
        client.settimeout(10)
        request = authorized_handshake(read_handshake(client), token)
        with socket.create_connection(
            (upstream_host, upstream_port), timeout=10
        ) as upstream:
            upstream.sendall(request)
            client.settimeout(None)
            upstream.settimeout(None)
            relay(client, upstream)
    except OSError, ValueError:
        print("Codex socket proxy connection failed", file=sys.stderr)
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-socket", required=True)
    parser.add_argument("--upstream-host", required=True)
    parser.add_argument("--upstream-port", required=True, type=int)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--socket-group", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = Path(args.token_file).read_text().strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise SystemExit("Codex WebSocket capability token is invalid")

    socket_path = Path(args.listen_socket)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def stop(_signum: int, _frame: object) -> None:
        listener.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        listener.bind(str(socket_path))
        os.chown(socket_path, -1, grp.getgrnam(args.socket_group).gr_gid)
        os.chmod(socket_path, 0o660)
        listener.listen(16)
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                break
            threading.Thread(
                target=handle_client,
                args=(client, args.upstream_host, args.upstream_port, token),
                daemon=True,
            ).start()
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
