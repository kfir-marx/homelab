from __future__ import annotations

import os

from sqlalchemy import MetaData, create_engine, func, inspect, select

from .sessions import Base, SessionStore

TABLE_ORDER = (
    "assistant_sessions",
    "assistant_active_sessions",
    "assistant_messages",
    "assistant_pending_actions",
    "assistant_external_jobs",
    "assistant_audit_events",
)


def migrate(source_url: str, destination_url: str) -> dict[str, int]:
    """Copy an idle gateway database without printing either credential-bearing URL."""
    source = create_engine(source_url, pool_pre_ping=True)
    destination_store = SessionStore(destination_url)
    destination = destination_store.engine
    source_metadata = MetaData()
    source_metadata.reflect(bind=source, only=lambda name, _meta: name in TABLE_ORDER)
    available = set(inspect(source).get_table_names())
    copied: dict[str, int] = {}
    with source.connect() as source_connection, destination.begin() as destination_connection:
        for name in TABLE_ORDER:
            target_table = Base.metadata.tables[name]
            if destination_connection.scalar(select(func.count()).select_from(target_table)):
                raise RuntimeError("destination database is not empty")
        for name in TABLE_ORDER:
            if name not in available:
                copied[name] = 0
                continue
            rows = [
                dict(row._mapping)
                for row in source_connection.execute(select(source_metadata.tables[name]))
            ]
            if rows:
                destination_connection.execute(Base.metadata.tables[name].insert(), rows)
            copied[name] = len(rows)
    return copied


def main() -> None:
    source = os.environ.get("HOMELAB_ASSISTANT_MIGRATION_SOURCE_DATABASE_URL", "")
    destination = os.environ.get("HOMELAB_ASSISTANT_MIGRATION_DESTINATION_DATABASE_URL", "")
    if not source or not destination:
        raise SystemExit("source and destination migration database URLs are required")
    copied = migrate(source, destination)
    print(
        "session migration completed: "
        + ", ".join(f"{key}={value}" for key, value in copied.items())
    )


if __name__ == "__main__":
    main()
