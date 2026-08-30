# Job assistant architecture

## Boundaries and data flow

```text
Telegram -> homelab-assistant -> local Codex App Server (general Codex work)
job API/worker -> PostgreSQL, artifacts, SMTP
restricted generation broker -> authenticated external-ai -> serialized Codex worker
```

The revised homelab-assistant no longer routes `/job_*` commands, job documents,
or the job outbox: application-owned Telegram commands are restricted to `/tg`
and `/ops`. Job-assistant still owns its job state machines and domain
validation, while external-ai owns model alias validation, ChatGPT
authentication, queueing, Codex execution, and sanitized execution metadata.
A future Telegram job surface must use a separate bot or an explicitly designed
`/tg` subtree; it must not reclaim root Codex slash commands.

## Reliability and trust

- Work items, outbox events, and broker submissions use unique idempotency keys.
- Generation runs persist the external job ID before workflow continuation.
- Any future transport must deduplicate Telegram updates and bound files before
  handing them to job-assistant.
- Generated output must match the required JSON Schema and cite only known
  career-inventory identifiers.
- Recruiter delivery and application submission remain separate state machines
  with explicit human confirmation and duplicate-send protection.

No component treats model output or a job description as executable authority.
Job-assistant and external-ai have no Kubernetes service-account token.

## Storage

PostgreSQL, artifacts, private inputs, and backups are personal data on static,
hard-bound `nfs-storage2` volumes with `Retain`. External-ai's queue and Codex
authentication use separate retained volumes in its own namespace. The legacy
job-assistant Codex-home volume remains retained but unmounted during the
cutover window.
