from typer.testing import CliRunner

from tapy.cli import app


def test_serve_is_an_explicit_subcommand() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"])
    assert result.exit_code == 0


def test_operator_provision_is_idempotent_and_inventory_has_no_tokens(tmp_path, monkeypatch):
    import json

    from pydantic import SecretStr

    from tapy.config import Settings
    from tapy.database import make_engine
    from tapy.migrations import upgrade_database

    url = f"sqlite+pysqlite:///{tmp_path / 'operators.db'}"
    engine = make_engine(Settings(database_url=SecretStr(url)))
    upgrade_database(engine)
    engine.dispose()
    monkeypatch.setenv("MATCHER_DATABASE_URL", url)
    runner = CliRunner()
    args = [
        "provision-pilots",
        "developer@example.com",
        "customer@example.com",
        "https://tapy.example",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert first.output.count("/#invite=") == 2
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert "/#invite=" not in second.output
    inventory = runner.invoke(app, ["inventory"])
    assert inventory.exit_code == 0, inventory.output
    data = json.loads(inventory.output)
    assert {o["name"] for o in data["organizations"]} == {"Tapy-test", "Lakish-tours"}
    assert data["memberships"] == []
    assert "token" not in inventory.output
    org = data["organizations"][0]["id"]
    assert (
        runner.invoke(
            app, ["invite", org, "explicit@example.com", "https://tapy.example"]
        ).exit_code
        == 0
    )
