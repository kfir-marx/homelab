# Job assistant deployment and operations

This runbook prepares the declarative Argo CD deployment at
`kubernetes/system/job-assistant`. Do not run the live steps until the image,
private files, storage paths, and Secrets are ready. No public route is needed;
Telegram uses long polling and the API is a private ClusterIP.

## 1. Build and publish the image

CI verifies Python 3.12 linting, typing, tests, a disposable PostgreSQL
migration/queue integration test, and the container build. Pull requests build
without publishing. When service or release-workflow changes land on `main`, a
successful run publishes the immutable `sha-<git-sha>` tag to
`ghcr.io/kfir-marx/homelab-job-assistant` and opens a release PR that pins all
six workload references to that tag.

```bash
release_sha="$(git rev-parse HEAD)"
docker build -t "ghcr.io/kfir-marx/homelab-job-assistant:sha-${release_sha}" services/job-assistant
docker push "ghcr.io/kfir-marx/homelab-job-assistant:sha-${release_sha}"
```

The Dockerfile pins its Codex CLI version. Before an upgrade, read the current
[Codex authentication](https://learn.chatgpt.com/docs/auth),
[non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
and [managed account-auth automation](https://learn.chatgpt.com/docs/auth/ci-cd-auth)
documentation, update the pin, and rerun the fake-executable provider test.

## 2. Prepare retained storage and private inputs

On `ubuntu-workstation`, create the exact critical-NFS paths with restrictive
ownership. This creates directories only; it does not format or repartition any
disk.

```bash
sudo install -d -o 999 -g 999 -m 0700 /mnt/storage2-bulk/job-assistant/postgres
sudo install -d -o 10001 -g 10001 -m 0700 /mnt/storage2-bulk/job-assistant/artifacts
sudo install -d -o 10001 -g 10001 -m 0700 /mnt/storage2-bulk/job-assistant/artifacts/artifacts
sudo install -d -o 10001 -g 10001 -m 0700 /mnt/storage2-bulk/job-assistant/artifacts/private
sudo install -d -o 10001 -g 10001 -m 0700 /mnt/storage2-bulk/job-assistant/codex-home
sudo install -d -o 10001 -g 10001 -m 0700 /mnt/storage2-bulk/job-assistant/backups
```

Place the real career inventory at:

```text
/mnt/storage2-bulk/job-assistant/artifacts/private/career-inventory.yaml
```

Optionally place the existing ATS-friendly DOCX template beside it as
`cv-template.docx`. Neither file belongs in Git. Validate the inventory locally
before deployment:

```bash
cd services/job-assistant
python3 -c 'from pathlib import Path; from job_assistant.career import load_inventory; print(len(load_inventory(Path("/path/to/career-inventory.yaml")).fact_ids()))'
```

## 3. Configure database identities

Use a URL-safe, high-entropy database password. The main Secret contains the
database-owner URL because the migration hook owns schema changes. The Codex
worker gets a separate restricted role and Secret.

Provide a second URL whose username is exactly `job_assistant_generation` and
whose password is independently generated. During every migration sync, the
owner-backed migration job creates or rotates that role and reapplies only
these grants:

```sql
GRANT CONNECT ON DATABASE job_assistant TO job_assistant_generation;
GRANT USAGE ON SCHEMA public TO job_assistant_generation;
GRANT SELECT ON applications TO job_assistant_generation;
GRANT UPDATE (status, updated_at) ON applications TO job_assistant_generation;
GRANT SELECT, INSERT, UPDATE ON generation_runs,
  outbox_events, work_items, worker_heartbeats TO job_assistant_generation;
GRANT SELECT, INSERT ON application_events TO job_assistant_generation;
```

Do not grant access to companies, jobs, artifacts, contacts, or delivery
attempts. The generation worker's input is already sanitized by the application,
and the database role further limits blast radius. Do not create the role by
hand; the GitOps migration job establishes it after the schema exists, so first
deployment does not require a partial sync or an owner credential in the
runtime worker.

## 4. Create and capture Secrets

Create namespace and the two application Secrets from a protected temporary
environment file. Never put values in Git or paste them into this runbook.

Required `job-assistant-secrets` keys:

- `POSTGRES_PASSWORD`, `DATABASE_URL`
- `TELEGRAM_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`
- `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `REVIEW_EMAIL`
- `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`

Required `job-assistant-codex-db` key: `DATABASE_URL` for the restricted role.

```bash
kubectl create namespace job-assistant --dry-run=client -o yaml | kubectl apply -f -
umask 077
secret_env="$(mktemp)"
generation_env="$(mktemp)"
# Edit both temporary files with the exact KEY=value entries listed above.
${EDITOR:-vi} "$secret_env"
${EDITOR:-vi} "$generation_env"
kubectl -n job-assistant create secret generic job-assistant-secrets \
  --from-env-file="$secret_env"
kubectl -n job-assistant create secret generic job-assistant-codex-db \
  --from-env-file="$generation_env"
shred -u "$secret_env" "$generation_env"
```

Add these non-secret inventory rows to `secrets/inventory.tsv` only in the same
change that captures their encrypted snapshots:

```text
job-assistant/job-assistant-secrets	POSTGRES_PASSWORD|DATABASE_URL|TELEGRAM_TOKEN|TELEGRAM_ALLOWED_USER_IDS|SMTP_HOST|SMTP_USERNAME|SMTP_PASSWORD|SMTP_FROM|REVIEW_EMAIL|IMAP_HOST|IMAP_USERNAME|IMAP_PASSWORD	Job assistant database, bot, review mail, and discovery identities
job-assistant/job-assistant-codex-db	DATABASE_URL	Restricted generation-worker database identity
job-assistant/job-assistant-codex-auth-bootstrap	auth.json	Initial ChatGPT-managed Codex authentication seed
```

Then run `scripts/secrets.sh capture-k8s` for each target. Commit only the SOPS
ciphertext. The implementation did not fabricate these snapshots because real
credentials are required.

## 5. Bootstrap ChatGPT-managed Codex authentication

Official Codex documentation states that `codex exec` reuses saved CLI auth,
file-backed credentials live at `$CODEX_HOME/auth.json`, and Codex refreshes a
managed session in place. Treat that file as a password and serialize every
consumer. This deployment uses one generation replica, a PostgreSQL queue, a
process file lock, and a retained writable `CODEX_HOME`.

On a trusted machine with a browser:

```bash
codex login
codex login status
auth_file="${CODEX_HOME:-$HOME/.codex}/auth.json"
test -s "$auth_file"
kubectl -n job-assistant create secret generic job-assistant-codex-auth-bootstrap \
  --from-file=auth.json="$auth_file"
scripts/secrets.sh capture-k8s job-assistant/job-assistant-codex-auth-bootstrap
```

The init container copies the bootstrap only when persistent `auth.json` is
missing, so it never overwrites refreshed credentials. After the first healthy
generation run, deleting the bootstrap Secret from the live namespace is safe;
keep only its SOPS-encrypted recovery copy. Never print, inspect, or log the
file's tokens.

If authentication fails repeatedly:

1. scale the generation Deployment to zero;
2. run `codex login` again on the trusted machine;
3. replace the bootstrap Secret and the persistent `auth.json` while no worker
   is running;
4. recapture the SOPS snapshot;
5. restore one replica and run a fake/manual generation job.

Usage-limit and authentication failures are bounded/retryable and surfaced in
metrics and Telegram; they never retry indefinitely.

## 6. Verify and deploy through GitOps

Static validation does not contact the cluster. `kubectl apply --dry-run=client`
still performs API discovery, so CI uses kubeconform for offline OpenAPI schema
validation and ignores only schemas for CRDs that are not bundled with the
Kubernetes schema set:

```bash
kubectl kustomize kubernetes/system/job-assistant >/tmp/job-assistant.yaml
kubeconform -ignore-missing-schemas -kubernetes-version 1.33.0 -strict -summary \
  /tmp/job-assistant.yaml kubernetes/apps/job-assistant.yaml
```

In repository **Settings → Actions → General → Workflow permissions**, enable
**Allow GitHub Actions to create and approve pull requests**. The release job
uses only the scoped `GITHUB_TOKEN` with `contents: write` and
`pull-requests: write`; it needs no cluster, Tailscale, or Argo CD credential.
GitHub may require a maintainer to approve the generated PR's workflow run.

The fixed `automation/job-assistant-image` branch means a newer successful
release updates the existing open release PR instead of creating a queue of
stale PRs. Merging the reviewed PR changes the Deployment pod templates and
lets automated Argo CD reconciliation run the migration hook and perform the
rollout. A manifest-only release-PR merge does not publish another image, so
the flow cannot recursively create release PRs. Main-branch workflow
concurrency also prevents releases from being published out of order.

After reviewing, commit and push normally. The root Argo CD app discovers
`kubernetes/apps/job-assistant.yaml`. Watch, but do not manually mutate, the
declarative resources:

```bash
kubectl -n argocd get application job-assistant -w
kubectl -n job-assistant get pods,jobs,cronjobs,pvc
kubectl get pv | grep job-assistant
kubectl -n job-assistant logs job/job-assistant-migrate
```

Only if the root app is not discovering child Applications, and with explicit
live-action approval, bootstrap the child with:

```bash
kubectl apply -f kubernetes/apps/job-assistant.yaml
```

## 7. Functional verification

1. Send `/add <public URL>` from the allowlisted Telegram account.
2. If parsing fails, supply `Company | Title | Location | Description`.
3. Press `Apply`; confirm one application and one generation work item.
4. Confirm the review email has DOCX, PDF, preview, gaps, and source URL.
5. Run `/final CODE`, upload PDF/DOCX, then paste the final message.
6. Run `/contact CODE` and explicitly verify the named company-domain contact.
7. Run `/approve CODE`, review the recipient, then press `Confirm Send`.
8. For LinkedIn-only or unverified contacts, use `/manual CODE`; no automatic
   message is sent.
9. After completing the official application separately, run `/submitted CODE`.

To test discovery without changing the schedule, create a one-off Job only with
explicit live-action approval:

```bash
kubectl -n job-assistant create job --from=cronjob/job-assistant-discovery \
  job-assistant-discovery-manual
```

## Backup, restore, retention, and deletion

Back up:

- PostgreSQL logical dumps from the backups PVC;
- the complete artifacts PVC, including the private inventory/template;
- the SOPS-encrypted Secret snapshots;
- `CODEX_HOME` only in a separately encrypted credential backup.

Do not place plaintext `auth.json`, Telegram/SMTP secrets, CVs, or contacts in
ordinary backup archives. The daily dump retention action defaults to dry-run.

Restore order:

1. restore/recreate retained NFS directories;
2. restore PostgreSQL dump with `pg_restore --clean --if-exists` into an empty
   maintenance database, then verify counts and constraints;
3. restore artifact files and verify recorded SHA-256 checksums;
4. restore SOPS Secrets with `scripts/secrets.sh restore-k8s`;
5. restore or reseed persistent Codex auth;
6. let Argo CD reconcile and verify queue leases/outbox state before enabling
   workers.

After a database point-in-time restore, inspect any `sending` delivery attempt
and reconcile it manually. Never reset it to pending without proving SMTP did
not accept the original message.

Personal-data deletion is intentionally manual and reviewable. First export the
audit record, stop relevant workers, identify exact application/job/artifact
rows and storage keys, take an encrypted backup, then delete only the confirmed
records and files. Do not add a destructive retention CronJob.

## Troubleshooting

- **Migration waits:** verify PostgreSQL readiness and the owner `DATABASE_URL`.
- **PVC pending:** verify static `volumeName`, claimRef, NFS export paths, and
  `nfs-storage2`; never remove `Retain` to force binding.
- **No recommendations:** disabled placeholder ATS slugs are expected; verify
  IMAP folder, criteria threshold, maximum age, and career inventory.
- **Codex auth/limit:** inspect only redacted exit classifications and metrics;
  never log `auth.json`. Follow the reseed procedure above.
- **Generation schema failure:** retain the run metadata, not CV/prompt content,
  and fix the provider prompt/schema. The worker fails closed.
- **Outbox dead:** fix the provider, then selectively requeue review
  notifications. Never blindly requeue recruiter outreach.
- **SMTP says sent but no delivery:** Gmail SMTP acceptance is not downstream
  delivery. Check the mailbox/bounce channel manually; bounce ingestion is an
  extension point.
