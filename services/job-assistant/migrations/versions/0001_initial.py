"""Initial normalized job-assistant schema."""

from alembic import op

from job_assistant.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
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
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
    op.execute("DROP FUNCTION IF EXISTS reject_application_event_mutation()")
