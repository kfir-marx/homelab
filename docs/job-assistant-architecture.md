# Job assistant architecture

## Boundaries and data flow

```text
Telegram -> homelab-assistant gateway -> authenticated job API
                                      -> local vLLM (general chat)
job API/worker -> PostgreSQL, artifacts, SMTP
restricted generation broker -> authenticated external-ai -> serialized Codex worker
job outbox <- gateway lease/send/ack
```

The gateway owns Telegram authorization, private-chat enforcement, polling,
file download, callback namespacing, and transport delivery. Job-assistant owns
the job state machines and all domain validation. External-ai owns model alias
validation, ChatGPT authentication, queueing, Codex execution, and sanitized
execution metadata.

## Reliability and trust

- Telegram updates remain deduplicated by update ID.
- Work items, outbox events, and broker submissions use unique idempotency keys.
- Generation runs persist the external job ID before workflow continuation.
- A pending job conversation takes precedence over general chat; unrelated text
  is never consumed by job-assistant.
- Files are bounded and downloaded only by the gateway, then validated and
  stored by job-assistant.
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
