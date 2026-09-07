import typer
import uvicorn

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run the Tapy API."""


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8080) -> None:  # noqa: S104
    uvicorn.run(
        "tapy.api:create_app",
        host=host,
        port=port,
        factory=True,
        access_log=False,
        server_header=False,
    )


if __name__ == "__main__":
    app()
