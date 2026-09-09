"""Replace the development flight row with the multi-tenant booking model.

Revision ID: 20260908_01
Revises: None

This application was explicitly pre-production when this baseline was created. The first
migration intentionally resets Tapy-owned tables so no ambiguous flight-row data is guessed into
the normalized model. It does not inspect or drop any non-Tapy table.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from tapy.database import Base

revision = "20260908_01"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_TABLES = (
    "tapy_flight_ingestion",
    "tapy_notifications",
    "tapy_flights",
    "tapy_processed_messages",
    "tapy_oauth_states",
    "tapy_mailboxes",
    "tapy_auth_identities",
    "tapy_user_sessions",
    "tapy_users",
)


def upgrade() -> None:
    connection = op.get_bind()
    existing = set(inspect(connection).get_table_names())
    # A legacy database has no Alembic version and contains tapy_flights. New/empty databases
    # skip this reset. CASCADE is scoped to these exact Tapy tables on PostgreSQL.
    if "tapy_flights" in existing:
        for table in LEGACY_TABLES:
            if table in existing:
                suffix = " CASCADE" if connection.dialect.name == "postgresql" else ""
                op.execute(f'DROP TABLE IF EXISTS "{table}"{suffix}')
    Base.metadata.create_all(connection)


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
