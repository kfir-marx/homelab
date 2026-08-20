# Job assistant deployment and operations

## Current runtime boundary

Job-assistant owns job discovery, truthful prompt construction, career fact
validation, workflow state, artifacts, delivery approval, SMTP delivery, and
audit events. It does not poll Telegram, call Telegram, install Codex, mount
`auth.json`, or authenticate to ChatGPT.

The shared homelab-assistant gateway calls the authenticated internal API. Its
typed replies contain text and unprefixed callback actions; the gateway adds
the `job:` namespace. Async Telegram replies remain in the database outbox
until the gateway leases, sends, and acknowledges them.

The worker submits generation with a requester-scoped idempotency key to
external-ai, records `external_job_id`, waits for the durable result, validates
the JSON Schema, and rejects unknown career-inventory IDs. Retries and crashes
reuse the same broker job.

## Storage and private inputs

All job history, PostgreSQL data, CV artifacts, private career inventory, and
backups stay on retained `nfs-storage2` volumes. Create the existing runbook
directories on the NFS host with their documented UID/GID ownership; never
format, force-mount, or repurpose an existing filesystem.

The legacy `/mnt/storage2-bulk/job-assistant/codex-home` PV/PVC and
`job-assistant-codex-auth-bootstrap` encrypted recovery snapshot are
intentionally retained but have no runtime consumer. Remove them only after a
verified external-ai cutover and an explicit operator decision.

## Secrets

Required `job-assistant-secrets` keys:

- `DATABASE_URL`;
- `GATEWAY_API_TOKEN`, matching the gateway's update/file client token;
- `GATEWAY_NOTIFICATION_TOKEN`, independently matching the notification
  lease/ack client;
- `EXTERNAL_AI_TOKEN`, unique to the job-assistant requester;
- `TELEGRAM_ALLOWED_USER_IDS` only as notification recipients and defense in
  depth; there is no `TELEGRAM_TOKEN`;
- SMTP, review-email, and IMAP keys listed in `secrets/inventory.tsv`.

`job-assistant-codex-db/DATABASE_URL` remains the restricted generation role.
The isolated `job-assistant-generation-broker` uses only this connection and
the job-assistant requester token for external-ai. The delivery worker uses the
normal connection and receives neither restricted database nor external-ai
credentials. Provisioning remains fail-closed through the database migration
command.

Capture changed Secrets with `scripts/secrets.sh capture-k8s`. Do not fabricate
SOPS snapshots or copy secrets between namespaces by hand.

## Build and static verification

```bash
ruff format --check services/job-assistant
ruff check services/job-assistant
mypy services/job-assistant/src
pytest services/job-assistant
kubectl kustomize kubernetes/system/job-assistant >/tmp/job-assistant.yaml
scripts/secrets.sh check
```

The workflow `.github/workflows/job-assistant.yml` builds and pins the same
image for API, delivery worker, generation broker, migration, discovery, and
backup roles. The runtime image must not contain a `codex` binary.

## Staged cutover

1. Deploy and authenticate external-ai without changing job-assistant.
2. Add job-assistant's external-ai token and run database migration `0002`.
3. Release the broker-backed job-assistant image and verify an idempotent test
   generation through the internal workflow.
4. Add gateway routing and verify `/job_help`, callbacks, pending conversations,
   documents, and async notification acknowledgment.
5. Stop and prune the job-assistant Telegram and Codex-generation Deployments.
6. Verify no job-assistant pod has `TELEGRAM_TOKEN`, `CODEX_HOME`, `auth.json`,
   Codex/OpenAI egress, or direct Telegram egress.
7. Revoke the old separate bot token after confirming the shared bot receives
   all intended commands.
8. Retain old Codex recovery material until the new authentication has survived
   refresh, restart, and one operator-approved recovery exercise.

## Troubleshooting

- `broker_unavailable`: verify service DNS, requester token, and NetworkPolicy;
  retries remain idempotent.
- `authentication`: recover external-ai authentication using its runbook; do
  not add auth material back to job-assistant.
- `usage_limit` or `timeout`: external-ai classifies and bounds retries; inspect
  sanitized job metadata and queue metrics, never prompt/result logs.
- missing Telegram notification: inspect the durable outbox lease and gateway
  health. Do not deliver directly from a worker.
- invalid claims/output: treat as terminal generation failure and repair the
  prompt/schema/inventory; never weaken fail-closed validation.
