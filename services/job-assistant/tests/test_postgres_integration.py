import os
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
from job_assistant.models import Base, OutboxEvent
from job_assistant.queue import claim_work, enqueue_work, put_outbox

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_url() -> str:
    value = os.environ.get("TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    return value


def test_migration_queue_claim_and_outbox(postgres_url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
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
