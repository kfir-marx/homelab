"""Add guided profiles, review state, outcomes, and discovery summaries.

Revision ID: 0004_guided_workflow
Revises: 0003_multi_user_ownership
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_guided_workflow"
down_revision = "0003_multi_user_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_search_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("notification_preferences", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "discovery_summaries",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("outcome", sa.String(length=50), nullable=False),
        sa.Column("sources_configured", sa.Integer(), nullable=False),
        sa.Column("sources_in_cooldown", sa.Integer(), nullable=False),
        sa.Column("sources_failed", sa.Integer(), nullable=False),
        sa.Column("jobs_found", sa.Integer(), nullable=False),
        sa.Column("jobs_filtered", sa.Integer(), nullable=False),
        sa.Column("recommendations_queued", sa.Integer(), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.add_column("applications", sa.Column("draft_message", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("final_subject", sa.String(length=500), nullable=True))
    op.add_column("applications", sa.Column("final_cv_artifact_id", sa.UUID(), nullable=True))
    op.add_column(
        "applications", sa.Column("cv_approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "applications", sa.Column("message_approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "applications", sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "applications",
        sa.Column("reminders_disabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_unique_constraint("uq_artifacts_id_user", "artifacts", ["id", "user_id"])
    op.create_foreign_key(
        "fk_applications_final_cv_artifact",
        "applications",
        "artifacts",
        ["final_cv_artifact_id", "user_id"],
        ["id", "user_id"],
    )
    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status",
        "applications",
        "status IN ('selected','generation_queued','generating','review_ready',"
        "'final_material_received','approved','submitted','interview','rejected','offer',"
        "'manual_required','failed','withdrawn')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status",
        "applications",
        "status IN ('selected','generation_queued','generating','review_ready',"
        "'final_material_received','approved','submitted','manual_required','failed','withdrawn')",
    )
    op.drop_constraint("fk_applications_final_cv_artifact", "applications", type_="foreignkey")
    op.drop_constraint("uq_artifacts_id_user", "artifacts", type_="unique")
    for column in (
        "reminders_disabled",
        "follow_up_at",
        "message_approved_at",
        "cv_approved_at",
        "final_cv_artifact_id",
        "final_subject",
        "draft_message",
    ):
        op.drop_column("applications", column)
    op.drop_table("discovery_summaries")
    op.drop_table("user_search_profiles")
