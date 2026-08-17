from __future__ import annotations

import psycopg
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy.engine import make_url

GENERATION_ROLE = "job_assistant_generation"


def provision_generation_role(owner_url: SecretStr, restricted_url: SecretStr) -> None:
    owner = make_url(owner_url.get_secret_value())
    restricted = make_url(restricted_url.get_secret_value())
    if restricted.username != GENERATION_ROLE or not restricted.password:
        raise ValueError(
            f"restricted database URL must contain the {GENERATION_ROLE!r} role and a password"
        )
    if not owner.database or owner.database != restricted.database:
        raise ValueError("owner and restricted database URLs must target the same database")
    dsn = owner.set(drivername="postgresql").render_as_string(hide_password=False)
    role = sql.Identifier(GENERATION_ROLE)
    password = sql.Literal(restricted.password)
    try:
        with psycopg.connect(dsn) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (GENERATION_ROLE,)
            ).fetchone()
            if not exists:
                connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role, password)
                )
            else:
                connection.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(role, password)
                )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(owner.database), role
                )
            )
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            connection.execute(sql.SQL("GRANT SELECT ON applications TO {}").format(role))
            connection.execute(
                sql.SQL("GRANT UPDATE (status, updated_at) ON applications TO {}").format(role)
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON generation_runs, outbox_events, "
                    "work_items, worker_heartbeats TO {}"
                ).format(role)
            )
            connection.execute(
                sql.SQL("GRANT SELECT, INSERT ON application_events TO {}").format(role)
            )
    except psycopg.Error as exc:
        raise RuntimeError("restricted generation database role provisioning failed") from exc
