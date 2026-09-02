# Job Assistant and shared-services Telegram architecture

## Implemented boundary

The friend-shareable Telegram surface is a general, typed gateway in the
`shared-services-telegram` namespace. Job Assistant is its first registered
adapter. The gateway long-polls Telegram and creates no public ingress.

The existing Ansible-managed `homelab-assistant` on `ubuntu-workstation` is a
separate owner-only trust domain. It retains Codex/workstation and
infrastructure-control access. It has no route, token, credential, network
allowance, or fallback path for shared-service traffic and is unchanged by the
gateway implementation.

```mermaid
flowchart LR
    U[Enrolled private-chat user] <--> T[Telegram Bot API]
    T <--> G[shared-services-telegram typed router]
    G -->|scoped update/file token| A[Job Assistant API]
    A --> D[(PostgreSQL)]
    A --> F[(Retained per-user artifact prefixes)]
    W[Job workers] --> D
    W --> X[external-ai]
    W --> M[SMTP/IMAP when enabled]
    A -->|owner-scoped notification lease| G
    O[Owner-only homelab-assistant] --> C[Workstation Codex]
```

The gateway has only three secrets: its dedicated `TELEGRAM_TOKEN`, a Job
Assistant update/file token, and an independent notification token. It has no
service-account token, PVC, database credential, NFS mount, SMTP/IMAP or
external-ai secret, Codex/OpenAI material, host identity, or infrastructure
client. Adding another shared service requires a new typed adapter, command and
callback registry, scoped credentials, backend authorization, NetworkPolicy,
tests, and operator documentation. There is no generic proxy, webhook, shell,
or arbitrary dispatcher.

## Identity and ownership

`users.id` is an immutable internal UUID. `users.telegram_user_id` is a unique,
immutable positive numeric Telegram identity. Display name and username are
informational and grant no authority. Unknown, inactive, revoked, group,
channel, forwarded, sender-chat, and `from.id != chat.id` updates are silently
discarded by the gateway and checked again by the backend.

Public company, source, and job catalog rows may be shared. These records are
owner-scoped:

- applications, with uniqueness on `(user_id, job_id)` and a globally unique
  human code retained as defense in depth;
- scores, recommendations, skip/snooze/reopen feedback, and per-user job state;
- pending Telegram conversations and processed updates;
- contacts, application contacts, delivery attempts, artifacts, generation
  runs, work items, audit/application events, and notification outbox rows.

Queries for codes, UUID callbacks, pending uploads, contacts, artifacts, and
notifications include the authenticated internal `user_id`. Composite foreign
keys bind application/contact child rows to the same user. A code, UUID,
callback payload, or storage key never conveys authority by itself.

Operator enrollment creates a random opaque storage prefix. Career inventory,
optional CV template, generated output, and final files live below that prefix.
Paths are composed only from the database prefix, application UUID, fixed kind,
and server-generated version; Telegram text and filenames cannot choose a
path. Inventories are validated per user immediately before generation and
included only in that user's generation work item.

Invited users default to `generation_enabled=false` and
`automated_delivery_enabled=false`. Enabling recruiter delivery additionally
requires a per-user review address and sender identity matching the configured
SMTP sender. Manual workflow remains available without enabling SMTP.

## Telegram protocol and reliability

The gateway registers gateway `/help` plus the Job Assistant `/job_*` command
set and known callback verbs. It acknowledges authorized callbacks promptly,
enforces Telegram's 64-byte callback limit, and forwards typed updates to the
backend, which remains authoritative for workflow rules and ownership.

Before any document download, the gateway calls the authenticated pending
upload endpoint. It accepts documents only, permits PDF/DOCX, enforces the
10 MB bound before and after download, and never downloads photos. Backend
content validation remains authoritative.

Telegram update IDs are persisted for idempotency. Async notifications are
leased from PostgreSQL. A definite Telegram failure enters bounded retry; a
timeout or transport failure after a possible send becomes `uncertain` and is
not automatically replayed. This prevents blind duplicate messages after a
gateway restart. Metrics contain only outcome classes, never user identifiers
or content.

## Network and storage

The gateway namespace is default-deny. Its only flows are:

| Direction | Destination/source | Port |
|---|---|---|
| Egress | cluster DNS | UDP/TCP 53 |
| Egress | `api.telegram.org` | TCP 443 |
| Egress | exact Job Assistant API pods in `job-assistant` | TCP 8080 |
| Ingress | monitoring namespace | TCP 8080 metrics |

There is no route to the Kubernetes API, LAN/workstation, homelab-assistant,
external-ai, PostgreSQL, NFS, SMTP/IMAP, Argo CD, or VM control services. Job
Assistant's API ingress admits only the exact gateway labels and monitoring;
the obsolete homelab-assistant Telegram allowance was removed.

Job Assistant retains static `nfs-storage2` PVs with `Retain` for PostgreSQL,
artifacts, backups, and legacy unmounted Codex home. Ansible declares the four
backing directories with PostgreSQL UID/GID `999:999` or application UID/GID
`10001:10001`, mode `0700`. Per-user separation is application/database
authorization, not cryptographic isolation from the homelab operator.

## Migration compatibility

Alembic revision `0003_multi_user_ownership` requires
`JOB_ASSISTANT_OWNER_TELEGRAM_USER_ID`. It deterministically creates the owner
UUID and storage prefix, backfills all existing rows, adds ownership foreign
keys and uniqueness, then makes required ownership columns non-null. It also
works on an empty database. Missing or invalid owner identity aborts before
ownership is installed.

The committed career inventory remains in place. It is not used directly by
the multi-user runtime. Before rollout, the operator copies the private runtime
inventory into the deterministic owner prefix using the content-free procedure
in the runbook. Generation remains fail-closed until validation.

The gateway Argo CD Application is initially manual and its manifest uses the
explicit `release-required` sentinel. CI builds both containers and the first
release PR replaces that sentinel with the tested commit SHA. This avoids
pretending that an unpublished image exists and prevents Argo CD from starting
an invalid first poller.
