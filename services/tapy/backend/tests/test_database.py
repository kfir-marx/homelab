import pytest
from pydantic import SecretStr

from tapy.config import Settings
from tapy.database import make_engine


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_plain_postgresql_url_uses_installed_psycopg_driver(scheme: str) -> None:
    engine = make_engine(
        Settings(
            database_url=SecretStr(
                f"{scheme}://tapy:secret@postgres.example:5432/tapy?sslmode=require"
            )
        )
    )

    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.host == "postgres.example"
        assert engine.url.database == "tapy"
        assert engine.url.query == {"sslmode": "require"}
    finally:
        engine.dispose()


def test_explicit_database_driver_is_preserved() -> None:
    engine = make_engine(Settings(database_url=SecretStr("sqlite+pysqlite:///:memory:")))

    try:
        assert engine.url.drivername == "sqlite+pysqlite"
    finally:
        engine.dispose()
