from typer.testing import CliRunner

from hotel_flight_matcher.cli import app


def test_serve_is_an_explicit_subcommand() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.stdout
