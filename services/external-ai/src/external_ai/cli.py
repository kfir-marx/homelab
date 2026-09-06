from __future__ import annotations

import asyncio

import typer
import uvicorn

from .api import create_app
from .config import Settings
from .database import initialize, make_engine, make_factory
from .rpc import run

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


@app.command()
def api() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8080, access_log=False)  # noqa: S104


@app.command()
def worker() -> None:
    settings = Settings()
    settings.require_worker_dependencies()
    engine = make_engine(settings)
    initialize(engine)
    asyncio.run(run(make_factory(engine), settings))


if __name__ == "__main__":
    app()
