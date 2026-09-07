from typer.testing import CliRunner

from tapy.cli import app


def test_serve_is_an_explicit_subcommand() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
