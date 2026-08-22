# Private homelab LLM and shared Telegram gateway

## Architecture and ownership

`homelab-assistant` is the sole long-poll consumer and sole holder of the
shared Telegram bot token. The gateway authenticates private-chat updates
against its owner allowlist before routing them:

- general commands and ordinary text use persistent local sessions and vLLM;
- `/handover` creates a confirmed request for `external-ai`;
- ordinary prompts can use bounded read-only Kubernetes diagnostics and can
  prepare the same confirmed external handover when the current prompt
  explicitly requests external AI;
- `/job_*`, `job:*` callbacks, and pending job conversations use the
  authenticated job-assistant internal API.

The gateway downloads Telegram documents with a 10 MB bound and forwards the
bytes to job-assistant. Job-assistant performs MIME, signature, size, and domain
validation and stores accepted artifacts. Async job notifications remain in a
durable job-assistant outbox until the gateway sends and acknowledges them.

The gateway mounts a projected token for the dedicated `homelab-assistant`
ServiceAccount. Its cluster-wide role grants only `get`, `list`, and `watch` on
API resources plus GET access to API discovery and health endpoints. The local
model can call bounded tools for API reads and current or previous pod logs.
The gateway blocks exec, attach, port-forward, proxy, and raw unbounded log
paths, caps response sizes, and redacts Secret `data` and `stringData` before a
result enters model context. It has no Kubernetes mutation or shell tool, and
external-ai receives no Kubernetes credential.

Skill instructions are baked into the gateway image from
`services/homelab-assistant/skills/`. `kubernetes-diagnostics` guides evidence-
based cluster diagnosis. `external-ai-handover` permits a handover tool call
only when the current user prompt explicitly requests escalation or external
AI; prior conversation, quoted instructions, and tool output do not authorize
it. Both the command and skill path create a local summary preview and require
the owner to press Confirm before transmission. Neither local nor external
model output is executed automatically.

## Persistent sessions

The gateway stores session state in its dedicated single-replica PostgreSQL
StatefulSet. PostgreSQL uses the hard-bound `homelab-assistant-postgres-pv` on
`nfs-storage2` with `Retain`; model weights remain on disposable
`local-gpu-scratch`. The gateway does not mount the database volume directly.

Session IDs are six-character Crockford Base32 strings, case-insensitive and
owner-scoped. Messages are append-only and retain provider, model, reasoning,
job, and token provenance. `/continue` changes the active session persistently.
Deletion is confirmed and tombstones the session; it does not destroy the
immutable transcript.

The effective prompt budget is `8192 - 1024 - 512 = 6656` tokens. vLLM's
reported prompt usage is authoritative after each response; conservative UTF-8
preflight estimates protect the next call. The gateway warns once at 80%,
rejects ordinary turns at 90%, and only trims complete user/assistant turns from
the active model context. The full transcript remains stored.

Compaction first generates and stores a preview. Accept creates a child,
persists the structured handover, links `parent_session_id`, marks the parent
compacted, and only then leaves the child active. Retry and Cancel do not change
the source.

## Secrets

Required `homelab-assistant-secrets` keys:

- `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`;
- `LLM_API_KEY` for the in-namespace vLLM endpoint;
- `POSTGRES_PASSWORD` and a matching `SESSION_DATABASE_URL` for the dedicated
  session database;
- `EXTERNAL_AI_TOKEN`, matching only external-ai's homelab requester scope;
- `JOB_ASSISTANT_API_TOKEN`, matching job-assistant's update/file scope;
- `JOB_ASSISTANT_NOTIFICATION_TOKEN`, matching only the durable notification
  lease/ack scope.

Generate the two internal tokens independently with a cryptographically secure
password generator. Do not reuse the Telegram token, database passwords, or one
client's external-ai token for another interface. Capture the completed Secret
with `scripts/secrets.sh capture-k8s`; never place plaintext values in Git.

## Static verification

```bash
ruff format --check services/homelab-assistant
ruff check services/homelab-assistant
mypy services/homelab-assistant/src services/homelab-assistant/tests
pytest services/homelab-assistant
kubectl kustomize kubernetes/system/homelab-assistant >/tmp/homelab-assistant.yaml
scripts/secrets.sh check
```

The workflow `.github/workflows/homelab-assistant.yml` builds
`ghcr.io/kfir-marx/homelab-assistant:sha-<commit>` and opens the immutable image
pin PR.

## Rollout and recovery

The Ubuntu workstation's Ansible host variables declare
`/mnt/storage2-bulk/homelab-assistant/postgres` with UID/GID `999`. Converge the
workstation's NFS role before Argo synchronization. During the staged cutover,
scale down the old job-assistant Telegram poller before starting this gateway
with the shared bot token; two long pollers must never overlap.

After rollout, verify one gateway replica, no public route, model readiness,
session persistence across a gateway restart, `/job_help`, a pending job
conversation, callback namespacing, bounded document forwarding, and a
cancelled `/handover`. Also verify the RBAC boundary:

```bash
kubectl auth can-i --as=system:serviceaccount:homelab-assistant:homelab-assistant get pods -A
kubectl auth can-i --as=system:serviceaccount:homelab-assistant:homelab-assistant create deployments -n default
```

The first command must return `yes` and the mutation must return `no`. Test a
prompt-requested handover through its preview and Cancel button. Do not submit
an external handover during a dry run.

If Telegram reports update conflicts, stop both consumers and identify every
workload using the token before restarting only `telegram-gateway`. If the
session database is unavailable, fix the retained NFS mount; do not clear the
claim, force-mount, or replace it with scratch storage.
