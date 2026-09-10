"""Invitation onboarding and explicit mailbox ownership; preserve existing records."""

from typing import cast

import sqlalchemy as sa
from alembic import op

from tapy.database import OrganizationAudit, OrganizationInvitation

revision = "20260910_03"
down_revision = "20260909_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    for table, column in (
        ("tapy_users", "email_verified_at"),
        ("tapy_mailboxes", "organization_id"),
        ("tapy_oauth_states", "organization_id"),
    ):
        if column not in {c["name"] for c in sa.inspect(connection).get_columns(table)}:
            if column == "email_verified_at":
                op.add_column(table, sa.Column(column, sa.DateTime(timezone=True), nullable=True))
            else:
                # ADD COLUMN with an inline nullable FK avoids SQLite table recreation/cascades.
                op.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN organization_id VARCHAR(36) '
                    "REFERENCES organizations(id)"
                )
                op.create_index(f"ix_{table}_organization_id", table, [column])
    cast(sa.Table, OrganizationInvitation.__table__).create(connection, checkfirst=True)
    cast(sa.Table, OrganizationAudit.__table__).create(connection, checkfirst=True)
    # Include all retained evidence, not just the first scan. Never infer from user selection.
    connection.execute(
        sa.text("""
        UPDATE tapy_mailboxes SET organization_id = (
            SELECT MIN(organization_id) FROM (
                SELECT mailbox_id, organization_id FROM tapy_processed_messages
                UNION SELECT mailbox_id, organization_id FROM booking_ingestion_sources
                UNION SELECT mailbox_id, organization_id FROM email_booking_events
            ) evidence WHERE evidence.mailbox_id = tapy_mailboxes.id
            HAVING COUNT(DISTINCT organization_id) = 1
        ) WHERE organization_id IS NULL
    """)
    )


def downgrade() -> None:
    raise RuntimeError("Onboarding downgrade is unsupported; restore a reviewed backup instead")
