# Homelab Telegram Codex client

This image is the narrow Telegram transport for the workstation's existing
Codex environment. It does not contain Codex, an OpenAI API key, Codex auth, a
model provider, a second transcript store, kubeconfig, or workstation SSH
configuration.

The production path is:

```text
exact private Telegram identity
  -> locked bridge container
  -> protected Unix socket
  -> Codex App Server running as kfir in /home/kfir/repos/homelab
```

The host App Server uses `HOME=/home/kfir`, so selected CLI and VS Code threads
share the normal Codex session store, configuration, skills, plugins, MCPs, and
authentication. The bridge persists only the Telegram user's selected thread
ID, opaque callback nonces, and sanitized audit metadata in SQLite.

Application commands are limited to `/tg ...` and `/ops ...`. Stable root Codex
commands are dispatched through an explicit registry; unsupported root slash
commands fail clearly and never become ordinary prompts. `/ops gaming` and
`/ops k8s` remain fixed, confirmed operations and are never exposed as Codex
tools.

Run local checks with:

```bash
uv sync --locked --extra dev
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy src tests
uv run --locked --extra dev pytest
```

See `docs/homelab-assistant-runbook.md` for provisioning, cutover, validation,
security boundaries, rollback, and the cross-client visibility limitation.
