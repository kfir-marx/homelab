"""Minimal durable state for Telegram-to-Codex thread selection and callbacks."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class BridgeState:
    """Store identifiers and sanitized audit metadata, never Codex transcript content."""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS telegram_selection (
                    user_id INTEGER PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    selected_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS callback_nonces (
                    nonce TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bridge_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    thread_id TEXT
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def selected_thread(self, user_id: int) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT thread_id FROM telegram_selection WHERE user_id = ?", (user_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def select_thread(self, user_id: int, thread_id: str) -> None:
        if not thread_id or len(thread_id) > 200:
            raise ValueError("invalid Codex thread id")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO telegram_selection(user_id, thread_id, selected_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    selected_at = excluded.selected_at
                """,
                (user_id, thread_id, int(time.time())),
            )

    def clear_selection(self, user_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM telegram_selection WHERE user_id = ?", (user_id,))

    def issue_callback(
        self,
        user_id: int,
        chat_id: int,
        action: str,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> str:
        nonce = secrets.token_urlsafe(12)
        expires_at = int(time.time()) + ttl_seconds
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM callback_nonces WHERE expires_at < ?", (int(time.time()),)
            )
            connection.execute(
                """
                INSERT INTO callback_nonces(nonce, user_id, chat_id, action, payload, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (nonce, user_id, chat_id, action[:40], encoded, expires_at),
            )
        return nonce

    def consume_callback(
        self, nonce: str, user_id: int, chat_id: int
    ) -> tuple[str, dict[str, Any]] | None:
        """Atomically consume one opaque callback nonce before its action executes."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT action, payload, expires_at, user_id, chat_id
                FROM callback_nonces WHERE nonce = ?
                """,
                (nonce,),
            ).fetchone()
            connection.execute("DELETE FROM callback_nonces WHERE nonce = ?", (nonce,))
            connection.commit()
        if not row or int(row[2]) < int(time.time()):
            return None
        if int(row[3]) != user_id or int(row[4]) != chat_id:
            return None
        payload = json.loads(str(row[1]))
        return str(row[0]), payload if isinstance(payload, dict) else {}

    def audit(
        self, user_id: int, operation: str, outcome: str, thread_id: str | None = None
    ) -> None:
        safe_thread = thread_id[:200] if thread_id else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bridge_audit_events(created_at, user_id, operation, outcome, thread_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(time.time()), user_id, operation[:40], outcome[:24], safe_thread),
            )
