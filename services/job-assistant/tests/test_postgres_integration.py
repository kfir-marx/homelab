import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from job_assistant.database_roles import provision_generation_role
from job_assistant.models import (
    Application,
    Base,
    OutboxEvent,
    TelegramConversation,
    User,
)
from job_assistant.queue import claim_work, enqueue_work, put_outbox

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_url() -> str:
    value = os.environ.get("TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    return value


def test_migration_queue_claim_and_outbox(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    monkeypatch.setenv("JOB_ASSISTANT_OWNER_TELEGRAM_USER_ID", "123456789")
    engine = create_engine(postgres_url)
    command.upgrade(config, "0002_external_ai_job_id")
    now = datetime.now(UTC)
    company_id, job_id, application_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO companies
                  (id, name, normalized_name, excluded, created_at, updated_at)
                VALUES (:id, 'Legacy company', 'legacy company', false, :now, :now)
                """
            ),
            {"id": company_id, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO jobs
                  (id, company_id, title, original_url, canonical_url,
                   description_text, raw_metadata, status, first_seen_at,
                   last_seen_at, created_at, updated_at)
                VALUES (:id, :company, 'Legacy role', 'https://example.com/legacy',
                        'https://example.com/legacy', '', '{}', 'discovered',
                        :now, :now, :now, :now)
                """
            ),
            {"id": job_id, "company": company_id, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO applications
                  (id, job_id, human_code, status, outreach_status, created_at, updated_at)
                VALUES (:id, :job, 'ABC23', 'selected', 'no_contact', :now, :now)
                """
            ),
            {"id": application_id, "job": job_id, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO telegram_conversations
                  (id, chat_id, user_id, state, application_id, data, created_at, updated_at)
                VALUES (:id, 123456789, 123456789, 'awaiting_final_cv', :application,
                        '{}', :now, :now)
                """
            ),
            {"id": uuid.uuid4(), "application": application_id, "now": now},
        )
    command.upgrade(config, "head")
    with Session(engine) as session:
        owner = session.scalar(select(User))
        migrated = session.get(Application, application_id)
        conversation = session.scalar(select(TelegramConversation))
        assert owner and migrated and conversation
        assert migrated.user_id == owner.id
        assert conversation.user_id == owner.id
        assert conversation.telegram_user_id == owner.telegram_user_id
    with Session(engine) as session:
        enqueue_work(session, "test", "work", {}, "integration-work")
        put_outbox(session, "test", "notify", "recipient", {}, "integration-outbox")
        session.commit()
    with Session(engine) as first, Session(engine) as second:
        claimed = claim_work(first, "test", "worker-one", 60)
        assert claimed is not None
        first.flush()
        assert claim_work(second, "test", "worker-two", 60) is None
        first.rollback()
        second.rollback()
    with Session(engine) as session:
        assert session.scalar(select(func.count(OutboxEvent.id))) == 1
    restricted_url = make_url(postgres_url).set(
        username="job_assistant_generation",
        password="integration-restricted-password",  # noqa: S106 - disposable CI database
    )
    provision_generation_role(
        SecretStr(postgres_url),
        SecretStr(restricted_url.render_as_string(hide_password=False)),
    )
    restricted_engine = create_engine(restricted_url)
    with restricted_engine.connect() as connection:
        connection.execute(text("SELECT id FROM applications LIMIT 1"))
    with restricted_engine.connect() as connection, pytest.raises(ProgrammingError):
        connection.execute(text("SELECT id FROM contacts LIMIT 1"))
    restricted_engine.dispose()
    Base.metadata.drop_all(engine)
