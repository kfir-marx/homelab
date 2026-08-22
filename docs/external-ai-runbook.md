# external-ai deployment, authentication, and recovery

## Service boundary

external-ai is a ClusterIP-only authenticated broker. The API accepts only two
requester credentials: homelab-assistant and job-assistant. It resolves aliases,
validates the model/reasoning pair, enforces prompt and timeout bounds, persists
idempotent jobs, and exposes requester-scoped status/cancellation.

One worker claims jobs fairly between requesters and runs `codex exec`
non-interactively and ephemerally with a read-only sandbox, user configuration
ignored, web search disabled, stdin prompt input, and an argument vector. It has
no Telegram, SMTP, Kubernetes, homelab, job database, or host credentials.

The implementation follows the official OpenAI documentation for
[non-interactive Codex execution](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
[authentication storage](https://learn.chatgpt.com/docs/auth), and the
[GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

## Storage and Secrets

The Ubuntu workstation's Ansible host variables declare these NFS directories
with their manifest UID/GID. Converge the workstation's NFS role before sync:

```text
/mnt/storage2-bulk/external-ai/postgres
/mnt/storage2-bulk/external-ai/codex-home
```

Both static PVs use `nfs-storage2`, hard binding, and `Retain`.

`external-ai-secrets` requires `POSTGRES_PASSWORD`, `DATABASE_URL`,
`HOMELAB_ASSISTANT_TOKEN`, and `JOB_ASSISTANT_TOKEN`. The requester tokens must
be independent. `external-ai-codex-auth-bootstrap` requires only `auth.json`.
Create and capture these manually; no plaintext or fabricated encrypted snapshot
belongs in Git.

## ChatGPT-managed auth bootstrap

On a trusted machine, use the exact pinned Codex client version and file-backed
credential storage, complete `codex login`, and confirm login status. Treat the
resulting `auth.json` as a password. Create the bootstrap Secret from the file
without printing it, then capture the Secret using `scripts/secrets.sh`.

The init container copies the bootstrap only when retained
`/var/lib/codex/auth.json` is absent. Codex can refresh the retained file during
normal runs; restarts must not overwrite it with an older bootstrap.

For reseed or rotation, stop the worker, create fresh trusted credentials,
replace the bootstrap Secret, archive the old persistent file using the
operator's approved secret-handling process, remove only the exact persistent
`auth.json`, and restart the worker. Never inspect or log file contents.

If authentication fails, distinguish expired/revoked auth from usage limits
using the broker's sanitized classification. Do not copy auth into a client
namespace. Keep the old job-assistant recovery snapshot until the staged cutover
has passed refresh, restart, and recovery verification.

## Verification

```bash
ruff format --check services/external-ai
ruff check services/external-ai
mypy services/external-ai/src services/external-ai/tests
pytest services/external-ai
kubectl kustomize kubernetes/system/external-ai >/tmp/external-ai.yaml
scripts/secrets.sh check
```

Verify manifests contain no Ingress, Gateway, NodePort, LoadBalancer, service
account mount, client credentials in the worker, or auth mount in the API. A
fake-Codex test verifies canonical model/reasoning arguments and read-only
execution. Live bootstrap and a real execution are deliberate operator steps,
not CI actions.

Monitor `/health/ready`, `external_ai_queue_depth`, API availability, terminal
error classifications, and oldest queued-job age. Prompt/result/auth content and
authorization headers must never appear in logs or metrics.
