from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def upgrade_database(engine: Engine) -> None:
    """Apply versioned migrations with the already-normalized SQLAlchemy URL."""
    configuration = Config()
    configuration.set_main_option("script_location", str(Path(__file__).with_name("alembic")))
    configuration.attributes["connection"] = engine.connect()
    try:
        command.upgrade(configuration, "head")
    finally:
        configuration.attributes["connection"].close()
