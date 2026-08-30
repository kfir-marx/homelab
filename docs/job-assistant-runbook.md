# Job assistant deployment and operations

## Current runtime boundary

This runbook describes the currently deployed service runtime. The internal
Telegram adapter is present, but no bot currently calls it. The private
homelab-assistant bot does not route job commands. The accepted target is the
dedicated, friend-shareable bot in
[`job-assistant-architecture.md`](job-assistant-architecture.md). Do not admit
friends or add a job bot token to the existing pods until its per-user ownership
and migration gates are implemented.

Job-assistant owns job discovery, truthful prompt construction, career fact
validation, workflow state, artifacts, delivery approval, SMTP delivery, and
audit events. It does not poll Telegram, call Telegram, install Codex, mount
`auth.json`, or authenticate to ChatGPT.

The authenticated internal API retains typed Telegram update, file, pending
conversation, and notification lease/ack contracts for the future dedicated
gateway. Async Telegram replies remain in the database outbox; without a
gateway they are not delivered.

The worker submits generation with a requester-scoped idempotency key to
external-ai, records `external_job_id`, waits for the durable result, validates
the JSON Schema, and rejects unknown career-inventory IDs. Retries and crashes
reuse the same broker job.

## Storage and private inputs

All job history, PostgreSQL data, CV artifacts, private career inventory, and
backups stay on retained `nfs-storage2` volumes. Create the existing runbook
directories on the NFS host with their documented UID/GID ownership; never
format, force-mount, or repurpose an existing filesystem.

The private inventory's NFS-host path is
`/mnt/storage2-bulk/job-assistant/artifacts/private/career-inventory.yaml`.
The artifacts PVC exposes it to the API and discovery workloads as
`/data/private/career-inventory.yaml`, the application default. It must be
readable by the pod security context, which runs as UID/GID `10001`. The API
loads and validates the file when Apply is pressed, so installing or replacing
it does not require a pod restart.

The legacy `/mnt/storage2-bulk/job-assistant/codex-home` PV/PVC and
`job-assistant-codex-auth-bootstrap` encrypted recovery snapshot are
intentionally retained but have no runtime consumer. Remove them only after a
verified external-ai cutover and an explicit operator decision.

## Dedicated bot acceptance workflow

This section becomes operational only after the dedicated gateway and the
per-user ownership gates in the architecture are deployed. Until then there is
no supported Telegram interface for job-assistant.

Use a private chat with the dedicated job-assistant bot from an admitted
Telegram account. Group chats and non-members are ignored. Job commands retain
the `/job_` prefix during migration; `/job_help` is the job workflow help.

### Safe first test

This test exercises inventory loading and generation but cannot send a message
to a recruiter. Apply does send the career inventory and job input through the
configured external-ai service and may queue the operator review email. Use a
real public job that you are willing to retain in job history; there is no
Telegram delete command.

1. Send `/job_help`. The bot should return the job command list. An unavailable
   response means the Telegram gateway cannot reach the job-assistant API.
2. Send `/job_add <public-job-url>`, replacing the placeholder with the real
   public HTTP(S) job URL. The URL must not require login or point to a private
   address.
3. If the page cannot be parsed, reply in the requested four-field format:
   `Company | Title | Location | Plain-text description`. Only one pending job
   conversation exists per user and it expires after 24 hours; while it is
   pending, ordinary text is treated as the requested answer.
4. Review the returned company and title, optionally press **Open job**, then
   press **Apply**. Apply creates an application, validates the private career
   inventory, and queues generation. It does not contact the employer. Keep the
   application code from the reply, for example `ABC12`.
5. Send `/job_status ABC12`. Normal progress is
   `generation_queued` -> `generating` -> `review_ready`. Completion also
   arrives asynchronously as `Application ABC12 is ready for review`; generated
   artifacts stay on NFS and a review email is queued when configured.
6. Stop here for a no-delivery smoke test. Do not run the approval confirmation
   described below.

If Apply replies that generation is blocked by an invalid private career
inventory, validate the mounted file without printing its private contents:

```bash
kubectl -n job-assistant exec deployment/job-assistant-api -- \
  python -c 'from job_assistant.career import load_inventory; from job_assistant.config import Settings; inventory = load_inventory(Settings().career_inventory_path); print(f"inventory valid: {len(inventory.fact_ids())} facts")'
```

### Complete an application

After reviewing and editing the generated material outside Telegram:

1. Send `/job_final ABC12`. Upload the final CV as a Telegram **document**, not
   a photo, in PDF or DOCX format and no larger than 10 MB. After the bot says
   `CV stored`, paste the exact final recruiter message. Both inputs are stored
   but nothing is sent.
2. Send `/job_contact ABC12`, then paste one verified company-domain email for
   the named recruiter or job poster. Check the displayed address carefully and
   press **Verified Contact** only when that identity has actually been verified.
3. Send `/job_approve ABC12`. This only displays a final warning and the
   selected delivery target. Press **Cancel** to stop safely.
4. **Pressing Confirm Send is the delivery boundary.** It queues the final
   message and CV for SMTP delivery exactly once, but only for a verified,
   high-confidence contact whose email matches the company domain.
5. After submitting through the employer's official application channel, send
   `/job_submitted ABC12`. This records the application submission; it does not
   send another recruiter message.

Use `/job_manual ABC12` after final material is ready when outreach must be
handled outside the automated SMTP path. It marks the workflow for manual
action and sends nothing. You can then use `/job_submitted ABC12` after the
official application is submitted. Replace `ABC12` in every example with the
five-character code returned by Apply.

### Commands and recommendation buttons

| Command or button | Effect |
|---|---|
| `/job_add <public-job-url>` | Add a public job; prompts for four fields if parsing is incomplete. |
| **Apply** | Create the application and queue career-inventory-grounded generation. |
| **Skip** | Exclude the job from future recommendations until explicitly reopened. |
| **Snooze** | Hide a discovered recommendation for seven days. |
| **Why recommended?** | Show the latest scoring explanation. |
| **Open job** | Return the original public URL. |
| `/job_status <code>` | Show application, outreach, and job states. |
| `/job_final <code>` | Start the final-CV and final-message upload conversation. |
| `/job_contact <code>` | Start verified recruiter-contact entry. |
| `/job_approve <code>` | Review the target and expose the separate Confirm Send button. |
| `/job_manual <code>` | Require manual outreach; nothing is sent. |
| `/job_submitted <code>` | Record the official application submission separately from outreach. |
| `/job_reopen <code>` | Reopen the application job only when it is skipped, snoozed, or expired. |
| `/job_help` | Show the bot's current job command list. |

Application codes are case-insensitive. A new multi-message command replaces
any pending job input conversation for that private chat. Cancel buttons prevent
the displayed action but do not roll back already stored application state or
artifacts.

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

## Dedicated bot rollout

Follow the migration gates in
[`job-assistant-architecture.md`](job-assistant-architecture.md). In particular,
do not deploy the gateway or add a friend until owner-scoped applications,
profiles, files, callbacks, and tests are in place. The bot token belongs only
to the new gateway; it must not be added to the API, workers, discovery job,
external-ai, or the private homelab-assistant bridge.

## Troubleshooting

- `broker_unavailable`: verify service DNS, requester token, and NetworkPolicy;
  retries remain idempotent.
- `authentication`: recover external-ai authentication using its runbook; do
  not add auth material back to job-assistant.
- `usage_limit` or `timeout`: external-ai classifies and bounds retries; inspect
  sanitized job metadata and queue metrics, never prompt/result logs.
- missing Telegram notification after the dedicated gateway is deployed:
  inspect the durable outbox lease and gateway health. Do not deliver directly
  from a worker. Before that rollout, undelivered Telegram outbox rows are
  expected because no gateway exists.
- invalid claims/output: treat as terminal generation failure and repair the
  prompt/schema/inventory; never weaken fail-closed validation.
