"""Add fail-closed per-user ownership and feature controls.

Revision ID: 0003_multi_user_ownership
Revises: 0002_external_ai_job_id
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0003_multi_user_ownership"
down_revision = "0002_external_ai_job_id"
branch_labels = None
depends_on = None


def _owner_identity() -> tuple[int, uuid.UUID, str]:
    raw = os.environ.get("JOB_ASSISTANT_OWNER_TELEGRAM_USER_ID", "").strip()
    try:
        telegram_id = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "JOB_ASSISTANT_OWNER_TELEGRAM_USER_ID is required for the ownership migration"
        ) from exc
    if telegram_id <= 0:
        raise RuntimeError("JOB_ASSISTANT_OWNER_TELEGRAM_USER_ID must be a positive integer")
    owner_id = uuid.uuid5(uuid.NAMESPACE_URL, f"homelab-job-assistant:owner:{telegram_id}")
    prefix = str(uuid.uuid5(uuid.NAMESPACE_URL, f"homelab-job-assistant:storage:{telegram_id}"))
    return telegram_id, owner_id, prefix


def upgrade() -> None:
    telegram_id, owner_id, prefix = _owner_identity()
    now = datetime.now(UTC)
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("generation_enabled", sa.Boolean(), nullable=False),
        sa.Column("automated_delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("inventory_valid", sa.Boolean(), nullable=False),
        sa.Column("storage_prefix", sa.String(length=36), nullable=False),
        sa.Column("career_inventory_key", sa.String(length=200), nullable=False),
        sa.Column("cv_template_key", sa.String(length=200), nullable=True),
        sa.Column("review_email", sa.String(length=500), nullable=True),
        sa.Column("smtp_from", sa.String(length=500), nullable=True),
        sa.Column("search_criteria_key", sa.String(length=200), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("telegram_user_id > 0", name="ck_users_telegram_id_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_prefix"),
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.bulk_insert(
        sa.table(
            "users",
            sa.column("id", sa.UUID()),
            sa.column("telegram_user_id", sa.BigInteger()),
            sa.column("active", sa.Boolean()),
            sa.column("is_owner", sa.Boolean()),
            sa.column("display_name", sa.String()),
            sa.column("generation_enabled", sa.Boolean()),
            sa.column("automated_delivery_enabled", sa.Boolean()),
            sa.column("inventory_valid", sa.Boolean()),
            sa.column("storage_prefix", sa.String()),
            sa.column("career_inventory_key", sa.String()),
            sa.column("cv_template_key", sa.String()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": owner_id,
                "telegram_user_id": telegram_id,
                "active": True,
                "is_owner": True,
                "display_name": "Owner",
                "generation_enabled": True,
                "automated_delivery_enabled": False,
                "inventory_valid": False,
                "storage_prefix": prefix,
                "career_inventory_key": f"{prefix}/private/career-inventory.yaml",
                "cv_template_key": f"{prefix}/private/cv-template.docx",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.execute(
        """
        CREATE FUNCTION reject_user_identity_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.id <> OLD.id
             OR NEW.telegram_user_id <> OLD.telegram_user_id
             OR NEW.storage_prefix <> OLD.storage_prefix THEN
            RAISE EXCEPTION 'user identity and storage prefix are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER users_immutable_identity
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION reject_user_identity_mutation();
        """
    )

    required = {
        "applications": True,
        "application_events": True,
        "contacts": True,
        "application_contacts": True,
        "artifacts": True,
        "generation_runs": True,
        "delivery_attempts": True,
        "search_feedback": True,
        "job_scores": True,
        "work_items": False,
        "outbox_events": False,
        "telegram_updates": False,
    }
    for table, non_null in required.items():
        op.add_column(table, sa.Column("user_id", sa.UUID(), nullable=True))
        op.execute(
            sa.text(  # noqa: S608 - table names come only from the constant mapping above
                f"UPDATE {table} SET user_id = :owner"  # noqa: S608
            ).bindparams(owner=owner_id)
        )
        op.create_foreign_key(f"fk_{table}_user_id", table, "users", ["user_id"], ["id"])
        if non_null:
            op.alter_column(table, "user_id", nullable=False)
    op.create_check_constraint(
        "ck_generation_work_has_user",
        "work_items",
        "queue <> 'generation' OR user_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_telegram_outbox_has_user",
        "outbox_events",
        "channel <> 'telegram' OR user_id IS NOT NULL",
    )

    op.alter_column("telegram_conversations", "user_id", new_column_name="telegram_user_id")
    op.add_column("telegram_conversations", sa.Column("user_id", sa.UUID(), nullable=True))
    op.execute(
        sa.text("UPDATE telegram_conversations SET user_id = :owner").bindparams(owner=owner_id)
    )
    op.create_foreign_key(
        "fk_telegram_conversations_user_id",
        "telegram_conversations",
        "users",
        ["user_id"],
        ["id"],
    )
    op.alter_column("telegram_conversations", "user_id", nullable=False)

    op.drop_constraint("applications_job_id_key", "applications", type_="unique")
    op.create_unique_constraint("uq_applications_user_job", "applications", ["user_id", "job_id"])
    op.create_unique_constraint("uq_applications_id_user", "applications", ["id", "user_id"])
    op.create_unique_constraint("uq_contacts_id_user", "contacts", ["id", "user_id"])
    op.drop_constraint("applications_approved_contact_id_fkey", "applications", type_="foreignkey")
    op.create_foreign_key(
        "fk_applications_approved_contact_owner",
        "applications",
        "contacts",
        ["approved_contact_id", "user_id"],
        ["id", "user_id"],
    )
    op.drop_constraint("job_scores_job_id_criteria_version_key", "job_scores", type_="unique")
    op.create_unique_constraint(
        "uq_job_scores_user_job_criteria",
        "job_scores",
        ["user_id", "job_id", "criteria_version"],
    )
    op.drop_constraint(
        "telegram_conversations_chat_id_user_id_key",
        "telegram_conversations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_telegram_conversations_user", "telegram_conversations", ["user_id"]
    )
    op.create_unique_constraint(
        "uq_search_feedback_user_job_action",
        "search_feedback",
        ["user_id", "job_id", "action"],
    )
    for table in (
        "application_events",
        "application_contacts",
        "artifacts",
        "generation_runs",
        "telegram_conversations",
        "delivery_attempts",
        "search_feedback",
    ):
        op.drop_constraint(f"{table}_application_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_application_owner",
            table,
            "applications",
            ["application_id", "user_id"],
            ["id", "user_id"],
        )
    for table in ("application_contacts", "delivery_attempts"):
        op.drop_constraint(f"{table}_contact_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_contact_owner",
            table,
            "contacts",
            ["contact_id", "user_id"],
            ["id", "user_id"],
        )
    op.create_table(
        "user_job_states",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('discovered','shortlisted','skipped','snoozed','expired','reopened')",
            name="ck_user_job_states_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO user_job_states
                (id, user_id, job_id, status, snoozed_until, created_at, updated_at)
            SELECT CAST(md5(CAST(:owner AS text) || CAST(id AS text)) AS uuid),
                   :owner, id, status, snoozed_until, created_at, updated_at
            FROM jobs
            """
        ).bindparams(owner=owner_id)
    )


def downgrade() -> None:
    op.drop_table("user_job_states")
    op.drop_constraint("fk_applications_approved_contact_owner", "applications", type_="foreignkey")
    op.create_foreign_key(
        "applications_approved_contact_id_fkey",
        "applications",
        "contacts",
        ["approved_contact_id"],
        ["id"],
    )
    op.drop_constraint("ck_telegram_outbox_has_user", "outbox_events", type_="check")
    op.drop_constraint("ck_generation_work_has_user", "work_items", type_="check")
    for table in ("application_contacts", "delivery_attempts"):
        op.drop_constraint(f"fk_{table}_contact_owner", table, type_="foreignkey")
        op.create_foreign_key(f"{table}_contact_id_fkey", table, "contacts", ["contact_id"], ["id"])
    for table in (
        "application_events",
        "application_contacts",
        "artifacts",
        "generation_runs",
        "telegram_conversations",
        "delivery_attempts",
        "search_feedback",
    ):
        op.drop_constraint(f"fk_{table}_application_owner", table, type_="foreignkey")
        op.create_foreign_key(
            f"{table}_application_id_fkey",
            table,
            "applications",
            ["application_id"],
            ["id"],
        )
    op.drop_constraint("uq_search_feedback_user_job_action", "search_feedback", type_="unique")
    op.drop_constraint("uq_telegram_conversations_user", "telegram_conversations", type_="unique")
    op.create_unique_constraint(
        "telegram_conversations_chat_id_user_id_key",
        "telegram_conversations",
        ["chat_id", "telegram_user_id"],
    )
    op.drop_constraint("uq_job_scores_user_job_criteria", "job_scores", type_="unique")
    op.create_unique_constraint(
        "job_scores_job_id_criteria_version_key", "job_scores", ["job_id", "criteria_version"]
    )
    op.drop_constraint("uq_applications_id_user", "applications", type_="unique")
    op.drop_constraint("uq_contacts_id_user", "contacts", type_="unique")
    op.drop_constraint("uq_applications_user_job", "applications", type_="unique")
    op.create_unique_constraint("applications_job_id_key", "applications", ["job_id"])
    op.drop_constraint(
        "fk_telegram_conversations_user_id", "telegram_conversations", type_="foreignkey"
    )
    op.drop_column("telegram_conversations", "user_id")
    op.alter_column("telegram_conversations", "telegram_user_id", new_column_name="user_id")
    for table in (
        "telegram_updates",
        "outbox_events",
        "work_items",
        "job_scores",
        "search_feedback",
        "delivery_attempts",
        "generation_runs",
        "artifacts",
        "application_contacts",
        "contacts",
        "application_events",
        "applications",
    ):
        op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
        op.drop_column(table, "user_id")
    op.execute("DROP TRIGGER users_immutable_identity ON users")
    op.execute("DROP FUNCTION reject_user_identity_mutation()")
    op.drop_table("users")
