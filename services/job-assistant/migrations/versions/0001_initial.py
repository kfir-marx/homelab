"""Initial normalized job-assistant schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the baseline schema explicit. Importing the live ORM metadata here would
    # make this historical migration absorb columns owned by later revisions.
    op.create_table(
        "companies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("normalized_name", sa.String(length=300), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("excluded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name"),
    )
    op.create_table(
        "job_sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("recipient", sa.String(length=500), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_outbox_pending", "outbox_events", ["status", "available_at"], unique=False)
    op.create_table(
        "telegram_updates",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_hash", sa.LargeBinary(length=32), nullable=False),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_table(
        "work_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("queue", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','leased','completed','retry','dead')", name="ck_work_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_work_claim", "work_items", ["queue", "status", "available_at"], unique=False
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("role", sa.String(length=300), nullable=True),
        sa.Column("email", sa.String(length=500), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("confidence IN ('low','medium','high')", name="ck_contacts_confidence"),
        sa.CheckConstraint(
            "verification_status IN ('unverified','verified','rejected','stale')",
            name="ck_contacts_verification",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("workplace_type", sa.String(length=30), nullable=True),
        sa.Column("employment_type", sa.String(length=100), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("ats_job_id", sa.String(length=300), nullable=True),
        sa.Column("description_html", sa.Text(), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=False),
        sa.Column("description_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('discovered','shortlisted','skipped','snoozed','expired','reopened')",
            name="ck_jobs_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_canonical_url", "jobs", ["canonical_url"], unique=False)
    op.create_index("ix_jobs_description_hash", "jobs", ["description_hash"], unique=False)
    op.create_table(
        "source_companies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("external_slug", sa.String(length=300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["job_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_slug"),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("human_code", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("outreach_status", sa.String(length=40), nullable=False),
        sa.Column("final_message", sa.Text(), nullable=True),
        sa.Column("approved_contact_id", sa.UUID(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outreach_status IN ("
            "'no_contact','contact_candidate_found','contact_verified','drafted',"
            "'approved','queued','sent','delivered','bounced','manual_required','failed')",
            name="ck_applications_outreach_status",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'selected','generation_queued','generating','review_ready',"
            "'final_material_received','approved','submitted','manual_required','failed','withdrawn')",
            name="ck_applications_status",
        ),
        sa.ForeignKeyConstraint(["approved_contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("human_code"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_table(
        "job_duplicate_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("candidate_job_id", sa.UUID(), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("job_id <> candidate_job_id", name="ck_duplicate_distinct_jobs"),
        sa.ForeignKeyConstraint(["candidate_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "candidate_job_id"),
    )
    op.create_table(
        "job_scores",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("criteria_version", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("passed_hard_filters", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "criteria_version"),
    )
    op.create_table(
        "job_source_occurrences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("external_job_id", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["job_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_job_id"),
    )
    op.create_table(
        "application_contacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "contact_id"),
    )
    op.create_table(
        "application_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("aggregate", sa.String(length=30), nullable=False),
        sa.Column("from_state", sa.String(length=40), nullable=True),
        sa.Column("to_state", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "application_id IS NOT NULL OR job_id IS NOT NULL",
            name="ck_event_has_aggregate_id",
        ),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("user_edited", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "kind", "version"),
    )
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider_message_id", sa.String(length=500), nullable=True),
        sa.Column("response_metadata", sa.JSON(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("structured_log", sa.JSON(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "idempotency_key"),
    )
    op.create_table(
        "search_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "telegram_conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "user_id"),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_application_event_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'application_events is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER application_events_append_only
        BEFORE UPDATE OR DELETE ON application_events
        FOR EACH ROW EXECUTE FUNCTION reject_application_event_mutation()
        """
    )


def downgrade() -> None:
    op.drop_table("telegram_conversations")
    op.drop_table("search_feedback")
    op.drop_table("generation_runs")
    op.drop_table("delivery_attempts")
    op.drop_table("artifacts")
    op.drop_table("application_events")
    op.drop_table("application_contacts")
    op.drop_table("job_source_occurrences")
    op.drop_table("job_scores")
    op.drop_table("job_duplicate_candidates")
    op.drop_table("applications")
    op.drop_table("source_companies")
    op.drop_index("ix_jobs_description_hash", table_name="jobs")
    op.drop_index("ix_jobs_canonical_url", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("contacts")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_work_claim", table_name="work_items")
    op.drop_table("work_items")
    op.drop_table("telegram_updates")
    op.drop_index("ix_outbox_pending", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("job_sources")
    op.drop_table("companies")
    op.execute("DROP FUNCTION IF EXISTS reject_application_event_mutation()")
