import os

import pytest
from pydantic import SecretStr
from sqlalchemy import inspect, text

from tapy.config import Settings
from tapy.database import make_engine
from tapy.migrations import upgrade_database


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_forward_migration_preserves_history_and_only_binds_unambiguous_mailboxes(
    tmp_path, backend
):
    url = f"sqlite+pysqlite:///{tmp_path / 'old.db'}"
    if backend == "postgres":
        url = os.environ.get("TAPY_ONBOARDING_MIGRATION_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires a separate empty disposable PostgreSQL database")
    engine = make_engine(Settings(database_url=SecretStr(url)))
    # Minimal pre-onboarding schema including the real cascading mailbox dependency.
    with engine.begin() as connection:
        for ddl in (
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
            "CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)",
            "CREATE TABLE tapy_users (id VARCHAR(36) PRIMARY KEY, email TEXT)",
            "CREATE TABLE tapy_auth_identities (id TEXT PRIMARY KEY, user_id "
            "TEXT REFERENCES tapy_users(id), provider TEXT, subject TEXT)",
            "CREATE TABLE tapy_mailboxes (id VARCHAR(36) PRIMARY KEY, user_id "
            "TEXT REFERENCES tapy_users(id))",
            "CREATE TABLE tapy_oauth_states (state_hash TEXT PRIMARY KEY)",
            "CREATE TABLE tapy_processed_messages (id TEXT PRIMARY KEY, "
            "mailbox_id TEXT REFERENCES tapy_mailboxes(id) ON DELETE CASCADE, "
            "organization_id TEXT)",
            "CREATE TABLE booking_ingestion_sources (id TEXT PRIMARY KEY, "
            "mailbox_id TEXT, organization_id TEXT)",
            "CREATE TABLE email_booking_events (id TEXT PRIMARY KEY, "
            "mailbox_id TEXT, organization_id TEXT)",
        ):
            connection.execute(text(ddl))
        connection.execute(text("INSERT INTO alembic_version VALUES ('20260909_02')"))
        connection.execute(text("INSERT INTO organizations VALUES ('a'), ('b')"))
        connection.execute(text("INSERT INTO tapy_users VALUES ('u', 'existing@example.com')"))
        connection.execute(
            text(
                "INSERT INTO tapy_auth_identities VALUES ('identity', 'u', "
                "'google', 'stable-subject')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tapy_mailboxes VALUES ('clear', 'u'), ('ambiguous', "
                "'u'), ('empty', 'u')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tapy_processed_messages VALUES ('1', 'clear', 'a'), "
                "('2', 'ambiguous', 'a')"
            )
        )
        connection.execute(text("INSERT INTO email_booking_events VALUES ('3', 'ambiguous', 'b')"))
    upgrade_database(engine)
    upgrade_database(engine)
    with engine.connect() as connection:
        assert dict(
            connection.execute(text("SELECT id, organization_id FROM tapy_mailboxes")).all()
        ) == {"clear": "a", "ambiguous": None, "empty": None}
        assert connection.scalar(text("SELECT count(*) FROM tapy_processed_messages")) == 2
        assert (
            connection.scalar(text("SELECT subject FROM tapy_auth_identities")) == "stable-subject"
        )
        assert connection.scalar(text("SELECT email FROM tapy_users")) == "existing@example.com"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260910_03"
        assert {"organization_invitations", "organization_audit"} <= set(
            inspect(connection).get_table_names()
        )
    engine.dispose()
