"""Track external-ai jobs used by durable generation runs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_external_ai_job_id"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_runs", sa.Column("external_job_id", sa.String(40), nullable=True))
    op.create_index("ix_generation_runs_external_job_id", "generation_runs", ["external_job_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_runs_external_job_id", table_name="generation_runs")
    op.drop_column("generation_runs", "external_job_id")
