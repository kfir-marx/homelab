# Tapy backend

Email-derived flight and accommodation evidence drives audited, order-independent
upsell reconciliation. Alibaba Qwen extracts facts; deterministic rules handle
matching, deduplication and send/dismiss eligibility. Message bodies are not retained.

Run `tapy migrate`, then `tapy serve` and `tapy worker` as separate processes.
Scans and sends return durable jobs; RabbitMQ workers perform provider calls.
PostgreSQL stores job progress, booking evidence, decisions and delivery receipts.
Partner-attributed outcomes are separate from the immediate opportunity workflow.

See [the runbook](../../../docs/tapy-runbook.md) for matching limitations, migration,
configuration, recovery and deployment. Run `ruff check .`, `ruff format --check .`,
`mypy src tests`, and `pytest`. The opt-in integration test additionally accepts
`TAPY_TEST_DATABASE_URL` and `TAPY_TEST_RABBITMQ_URL` for disposable local services.
