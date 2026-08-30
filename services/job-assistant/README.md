# Homelab job assistant

Personal, human-approved job discovery and application workflow service.

The service exposes a private authenticated adapter for a future dedicated
job-assistant Telegram gateway. No gateway is currently deployed; the private
homelab-assistant bot does not route job commands. The service does not poll
Telegram or hold a bot token. Typed replies preserve the existing commands,
callbacks, pending conversations, final-CV upload validation, contact
verification, approval, and submission/outreach separation.

Generation inputs are sanitized and submitted idempotently to external-ai with
the required JSON Schema. Returned claims are rejected unless every referenced
career-inventory ID exists. Job state, queues, outbox events, artifacts, and
audit history remain in PostgreSQL and retained storage.

Runtime roles:

| Role | Responsibility | Credentials |
|---|---|---|
| `api` | health, metrics, gateway update/file adapter, durable notifications | database, gateway API token |
| `worker` | domain queue and approved SMTP delivery | normal database role and SMTP |
| `broker-worker` | external-ai submission and result validation | restricted database role and requester token |
| `discover` | scheduled ATS/email discovery | database and source credentials |
| `migrate` | schema and restricted-role setup | database identities |

No role receives a Telegram bot token, Codex binary, `CODEX_HOME`, `auth.json`,
or Kubernetes service-account token. See `docs/job-assistant-runbook.md` and
`docs/job-assistant-architecture.md`.
