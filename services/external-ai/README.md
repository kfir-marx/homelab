# external-ai

Private, authenticated, ClusterIP-only broker for durable and serialized Codex
executions. It persists request metadata and results in PostgreSQL, while the
worker keeps ChatGPT-managed authentication in a retained `CODEX_HOME`.

The service never logs prompts, results, authorization headers, or auth data.
See `docs/external-ai-runbook.md` for deployment and recovery procedures.
