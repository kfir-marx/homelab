# Homelab job assistant

Personal, human-approved job discovery and application workflow service.

The service exposes a private authenticated adapter for the separate
shared-services Telegram gateway. The private homelab-assistant bot does not
route job commands. Job Assistant does not poll Telegram or hold a bot token.
Typed replies preserve the existing commands,
callbacks, pending conversations, final-CV upload validation, contact
verification, approval, and submission/outreach separation.

Generation inputs are sanitized and submitted idempotently to external-ai with
the required JSON Schema. Returned claims are rejected unless every referenced
career-inventory ID exists. Public catalog data may be shared; applications,
recommendations, profiles, conversations, contacts, artifacts, generation,
delivery, events, and notifications are scoped to an immutable enrolled user.
State remains in PostgreSQL and retained storage.

Runtime roles:

| Role | Responsibility | Credentials |
|---|---|---|
| `api` | health, metrics, gateway update/file adapter, durable notifications | database, gateway API token |
| `worker` | domain queue and approved SMTP delivery | normal database role and SMTP |
| `broker-worker` | external-ai submission and result validation | restricted database role and requester token |
| `discover` | scheduled ATS/email discovery | database and source credentials |
| `reminders` | scheduled, idempotent owner notifications | database |
| `migrate` | schema and restricted-role setup | database identities |

No role receives a Telegram bot token, Codex binary, `CODEX_HOME`, `auth.json`,
or Kubernetes service-account token. See `docs/job-assistant-runbook.md` and
`docs/job-assistant-architecture.md`.

The primary Telegram flow is `/job_setup` → `/job_today` →
`/job_applications`. Existing code-based `/job_*` commands remain available.
Career-inventory onboarding from CV is not implemented; uploads can only revise
an application CV and never replace the confirmed inventory.
