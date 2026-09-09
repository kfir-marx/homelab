from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine


def upgrade_database(engine: Engine) -> None:
    """Apply versioned migrations with the already-normalized SQLAlchemy URL."""
    configuration = Config()
    configuration.set_main_option("script_location", str(Path(__file__).with_name("alembic")))
    configuration.attributes["connection"] = engine.connect()
    connection = configuration.attributes["connection"]
    if engine.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_lock(8472910)"))
        connection.commit()
    try:
        command.upgrade(configuration, "head")
    finally:
        if engine.dialect.name == "postgresql":
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(8472910)"))
            connection.commit()
        connection.close()
