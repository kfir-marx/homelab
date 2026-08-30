from __future__ import annotations

from pathlib import Path

from homelab_assistant.migrate import migrate
from homelab_assistant.sessions import SessionStore


def test_session_migration_preserves_history_provenance_and_active_session(
    tmp_path: Path,
) -> None:
    source_url = f"sqlite+pysqlite:///{tmp_path / 'source.db'}"
    destination_url = f"sqlite+pysqlite:///{tmp_path / 'destination.db'}"
    source = SessionStore(source_url)
    session = source.create(123, "Migrated")
    source.append(
        session.id,
        "assistant",
        "retained answer",
        provider="cloud-provider",
        model="cloud-model",
        prompt_tokens=10,
        completion_tokens=5,
    )
    source.audit(123, "gaming", "previewed", "secret-free state")

    counts = migrate(source_url, destination_url)
    destination = SessionStore(destination_url)
    active = destination.active(123, create=False)
    assert active and active.id == session.id
    message = destination.messages(session.id)[0]
    assert (message.content, message.provider, message.model) == (
        "retained answer",
        "cloud-provider",
        "cloud-model",
    )
    assert counts["assistant_messages"] == 1
    assert counts["assistant_audit_events"] == 1
