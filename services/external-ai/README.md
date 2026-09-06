# external-ai

Private broker for durable serialized jobs and RabbitMQ RPC inference. Existing
HTTP jobs support Codex models, while both HTTP and queue requests can select
Alibaba Cloud Model Studio with an `alibaba:` model name. Durable job metadata
and results live in PostgreSQL; ChatGPT-managed authentication stays in a
retained `CODEX_HOME`.

The service never logs prompts, results, authorization headers, or auth data.
See `docs/external-ai-runbook.md` for deployment and recovery procedures.
