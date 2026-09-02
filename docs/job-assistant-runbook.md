# Job Assistant deployment and operations

This runbook covers the Job Assistant backend. Shared bot rollout, token
rotation, smoke tests, and rollback are in
[`shared-services-telegram-runbook.md`](shared-services-telegram-runbook.md).
Never route shared traffic through the owner-only `homelab-assistant`.

## Preconditions and storage

Do not sync Argo CD until all checks pass, the NFS directories exist through an
authorized Ansible converge, real Secrets have been created and captured, and
the release PR has pinned both images.

Ansible owns these retained NFS paths:

```text
/mnt/storage2-bulk/job-assistant/postgres   999:999     0700
/mnt/storage2-bulk/job-assistant/artifacts 10001:10001 0700
/mnt/storage2-bulk/job-assistant/backups   10001:10001 0700
/mnt/storage2-bulk/job-assistant/codex-home 10001:10001 0700 (legacy, unmounted)
```

Never create them by hand as a substitute for Ansible, change an existing
filesystem, clear safety flags, or alter `Retain` PV behavior.

Each enrolled user has this private layout under the artifacts export:

```text
<opaque-uuid>/private/career-inventory.yaml
<opaque-uuid>/private/cv-template.docx       # optional
<opaque-uuid>/applications/<application-uuid>/<server-generated-file>
```

Telegram input cannot select any segment. Do not place a user's name, username,
email, or Telegram ID in a path.

## Secrets

`job-assistant/job-assistant-secrets` must contain exactly the keys inventoried
in `secrets/inventory.tsv`, including `OWNER_TELEGRAM_USER_ID`, matching
`JOB_ASSISTANT_API_TOKEN` and `JOB_ASSISTANT_NOTIFICATION_TOKEN`, database,
external-ai, SMTP, and optional IMAP values. It must not contain the shared bot
token. The two API tokens must be independently generated.

The same two client values are stored under their matching key names in the
gateway namespace Secret. The Telegram token exists only there. Never reuse the
owner-only bot token or the old `HOMELAB_ASSISTANT_PUBLIC_*` local names.

After creating or rotating real Secrets, capture them without displaying
values:

```bash
scripts/secrets.sh capture-k8s job-assistant/job-assistant-secrets
scripts/secrets.sh capture-k8s shared-services-telegram/shared-services-telegram-secrets
scripts/secrets.sh check
```

Do not fabricate encrypted snapshots before the live Secrets exist.

## Owner migration and inventory bootstrap

1. Set `OWNER_TELEGRAM_USER_ID` to the owner's immutable numeric Telegram ID in
   `job-assistant-secrets`. Do not use a username.
2. Back up PostgreSQL using the existing backup procedure and verify the backup
   file exists without inspecting application content.
3. Before sync, reproduce the deterministic prefix locally without querying
   private data (replace the placeholder only in the local shell):

   ```bash
   python -c 'import uuid; i=int(input("owner Telegram numeric ID: ")); print(uuid.uuid5(uuid.NAMESPACE_URL, f"homelab-job-assistant:storage:{i}"))'
   ```

4. Through the approved NFS-host workflow, copy the existing private runtime
   inventory from `artifacts/private/career-inventory.yaml` to
   `artifacts/<prefix>/private/career-inventory.yaml`, ownership `10001:10001`,
   modes `0700` directories and `0600` file. Copy the optional template the
   same way. Do not delete the legacy source until post-migration recovery has
   been tested. This is a live filesystem action and requires separate
   authorization.
5. Syncing Job Assistant runs migration `0003_multi_user_ownership`. A missing
   or invalid owner identity fails closed. Inspect only migration status and
   counts; do not print user/profile/application rows.
6. Validate the owner's inventory through an owner Apply smoke test. The user
   record's `inventory_valid` flag becomes true only after validation.

## Enrollment, revocation, and feature flags

Run these commands only in an authorized Job Assistant API pod or equivalent
trusted one-shot environment with the normal application database credential:

```bash
job-assistant user-enroll TELEGRAM_NUMERIC_ID --display-name "Friend"
job-assistant user-features TELEGRAM_NUMERIC_ID --generation
job-assistant user-profile TELEGRAM_NUMERIC_ID --private-search-criteria
job-assistant user-features TELEGRAM_NUMERIC_ID \
  --generation --automated-delivery \
  --review-email REVIEW_ADDRESS --smtp-from CONFIGURED_SENDER
job-assistant user-revoke TELEGRAM_NUMERIC_ID
```

Enrollment is active but generation and SMTP are off for invited users.
`--generation` is useful only after provisioning and validating that user's
inventory prefix. Automated delivery requires explicit flags and per-user
sender/review configuration. Revocation retains records, disables both
features, and makes updates and pending notifications silent. Reactivation
requires `user-enroll ... --reactivate`; re-evaluate features separately.

## Job workflow

Gateway `/help` lists shared services. `/job_help` lists the Job Assistant
adapter. Supported commands are `/job_add`, `/job_status`, `/job_final`,
`/job_contact`, `/job_approve`, `/job_manual`, `/job_submitted`, `/job_reopen`,
and `/job_help`; recommendation buttons are Apply, Skip, Snooze, Why, and Open.

Manual public URL ingestion is always available for enrolled users. If parsing
fails, the backend requests `Company | Title | Location | description`.
LinkedIn/IMAP discovery is enabled only when real IMAP credentials and the
configured folder exist. The committed Greenhouse, Lever, and Ashby entries
remain disabled placeholders. To add a source later, verify the official board
slug and terms, update `company-registry.yaml`, leave only the verified entry
enabled, run adapter tests, and review discovery egress; never invent slugs.

Apply creates a per-user application. Generation runs only when the user's flag
is enabled and their own inventory validates. Final CV is accepted only as PDF
or DOCX document up to 10 MB. Recruiter delivery remains a separate explicit
confirmation and is blocked unless the user flag, per-user sender/review
configuration, verified company-domain contact, final material, and duplicate
send protections all pass. `/job_manual` sends nothing.

## Backup and restore

Backups use the retained backup PVC. Restore PostgreSQL only into a stopped,
operator-approved recovery window. Restore the database and artifacts from the
same recovery point so artifact rows and files agree. Preserve user IDs,
storage prefixes, feature flags, and `Retain` PV bindings. Run Alembic to head,
then test owner access and cross-user denial before enabling the gateway.

If the database is lost but artifacts survive, do not create a fresh shared
deployment and assume path ownership can be reconstructed. Restore the
database first. If only one user's inventory is missing, disable their
generation flag until the correct file is reprovisioned and validates.

## Offline validation

```bash
ruff format --check services/job-assistant
ruff check services/job-assistant
mypy services/job-assistant/src services/job-assistant/tests
pytest services/job-assistant
kubectl kustomize kubernetes/system/job-assistant >/tmp/job-assistant.yaml
scripts/secrets.sh check
```

The PostgreSQL integration test upgrades from the initial schema through head,
tests queues/outbox and the restricted generation role, and requires
`TEST_POSTGRES_URL`. Container and manifest validation run in CI. No validation
command here contacts the live cluster unless the operator explicitly supplies
such access.
