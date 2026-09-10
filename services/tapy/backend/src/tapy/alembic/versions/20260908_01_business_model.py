"""Replace the development flight row with the multi-tenant booking model.

Revision ID: 20260908_01
Revises: None

Legacy pre-Alembic flight-row databases require a separate preservation migration.
Never reset those records implicitly during onboarding rollout.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from tapy.database import Base

revision = "20260908_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    existing = set(inspect(connection).get_table_names())
    # Fail closed rather than resetting an unversioned legacy database.
    if "tapy_flights" in existing:
        raise RuntimeError(
            "Legacy Tapy schema requires a reviewed preservation migration; refusing reset"
        )
    Base.metadata.create_all(connection)


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
