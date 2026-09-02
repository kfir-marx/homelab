# Shared-services Telegram gateway operations

## First deployment order

1. Merge code and CI changes. Wait for both tested container builds.
2. Merge the automated release PR. Confirm it replaces every Job Assistant
   image and the gateway's `release-required` sentinel with the exact
   `sha-<commit>` that passed tests. Never hand-pin an unpublished image.
3. With separate live authorization, converge the Ubuntu workstation Ansible
   play so the declared NFS directories exist. Do not create them manually.
4. Create `job-assistant-secrets` and
   `shared-services-telegram-secrets` from real local values. The shared Secret
   has only `TELEGRAM_TOKEN`, `JOB_ASSISTANT_API_TOKEN`, and
   `JOB_ASSISTANT_NOTIFICATION_TOKEN`. Capture both with `scripts/secrets.sh`.
5. Complete owner inventory bootstrap and a verified database backup as
   described in the Job Assistant runbook.
6. Sync Job Assistant first. Wait for migration, PostgreSQL, API, general
   worker, generation broker, and discovery health.
7. Verify no other poller uses the shared token. Then manually sync the
   `shared-services-telegram` Application. Keep exactly one replica.
8. Run the owner-only smoke and isolation tests below before enrollment of a
   friend. Automated Argo sync may be enabled only after the first image is
   pinned and the smoke tests pass.

These are operator/live steps. Repository validation does not perform them.

Prepare two mode-`0600` files outside the repository with the exact key names
from `secrets/inventory.tsv`. Then, only with live-change authorization, create
or rotate the Secrets without placing values in command arguments or output:

```bash
kubectl apply -f kubernetes/system/shared-services-telegram/namespace.yaml
kubectl -n job-assistant create secret generic job-assistant-secrets \
  --from-env-file=/secure/path/job-assistant-secrets.env \
  --dry-run=client -o yaml | kubectl apply --server-side -f -
kubectl -n shared-services-telegram create secret generic shared-services-telegram-secrets \
  --from-env-file=/secure/path/shared-services-telegram-secrets.env \
  --dry-run=client -o yaml | kubectl apply --server-side -f -
```

Delete the temporary files through the operator's approved secret-handling
workflow after capture and verification. Never pass these files to Git, CI, a
container build context, or the homelab-assistant host service.

## Owner-only smoke test

1. Enroll only the owner and provision the owner inventory prefix.
2. In a private chat, send `/help` and `/job_help`. Confirm the first lists the
   shared-services registry and the second lists Job Assistant commands.
3. Send `/job_add` with a real public job URL, complete manual metadata if
   requested, and press Apply. Confirm a code is returned and generation uses
   only the owner's inventory. Do not enable recruiter delivery for this test.
4. Check `/job_status CODE`, wait for the owner-scoped ready notification, then
   start `/job_final CODE`. Confirm a photo is ignored, invalid/oversized files
   are rejected, and a valid bounded PDF/DOCX is accepted only while the upload
   conversation is pending.
5. Restart the gateway during a pending notification lease. Confirm delivered
   messages are acked, definite failures retry with bounds, and an uncertain
   send raises the alert and remains `uncertain` rather than being replayed.
6. Confirm the owner-only homelab-assistant still has its original commands and
   that `/job_help` there has no route.

## Cross-user isolation and friend onboarding

1. Enroll one friend with default flags. From both users, add or discover the
   same job and press Apply. Confirm two independent application records/codes.
2. From each account, try the other account's code, application UUID callback,
   replayed callback, pending upload, and artifact key through the typed test
   client. Each must return no owned object or a generic unavailable response;
   no private content may be disclosed.
3. Confirm friend generation says disabled, SMTP confirmation says disabled,
   and notifications/recommendations go only to their owner.
4. Provision the friend's inventory at their opaque prefix, validate it, then
   explicitly enable generation. Repeat Apply and verify prompts cite only that
   inventory's fact IDs.
5. Enable automated delivery only after configuring the user's review address
   and matching sender and completing a no-send approval rehearsal. Keep it off
   when manual outreach is sufficient.

Unknown, revoked, group, channel, forwarded, sender-chat, and mismatched
`from.id`/`chat.id` cases must receive no response. Do not explain enrollment
state to an unauthorized sender.

## Monitoring

Monitor gateway readiness, `shared_services_telegram_updates_total`, sanitized
delivery outcomes, Job Assistant API readiness, outbox depth/age, and the
uncertain-delivery alert. Metrics and logs must never contain tokens, raw
updates, user IDs, names, usernames, email addresses, CV/job text, generated
output, or artifact contents. Investigate only sanitized error classes.

## Token rotation

1. Stop or scale down the single gateway poller with explicit live approval.
2. Revoke/regenerate the Telegram token in BotFather. Replace only
   `shared-services-telegram-secrets/TELEGRAM_TOKEN`.
3. Rotate update and notification tokens independently in both namespace
   Secrets, preserving which client uses which token.
4. Capture both Secrets and run `scripts/secrets.sh check` without printing
   values.
5. Start one gateway replica and run `/help`, one typed update, and one leased
   notification. Verify the old tokens fail.

Never overlap old/new pollers using the same Telegram token.

During the first rotation, remove the obsolete `JOB_ASSISTANT_API_TOKEN` and
`JOB_ASSISTANT_NOTIFICATION_TOKEN` keys from the live homelab-assistant recovery
Secret, confirm no workload consumes them, and recapture that Secret. The
currently committed ciphertext is intentionally not hand-edited; only a real
live Secret capture may replace it.

## Rollback

Rollback stops the gateway Deployment or leaves its manual Application
unsynced. Shared Telegram access is unavailable during rollback; never route it
through homelab-assistant. Roll back the gateway image to a previously published
tested SHA only when its API contract is compatible. Database migrations are
forward-only during normal rollback: keep Job Assistant at a version that
understands the current schema. Do not collapse user-owned records back to a
single-user schema.

If a faulty release sent no data mutations, revert the manifest SHA through a
reviewed Git change. If ownership or artifact integrity is in doubt, keep the
gateway stopped, restore PostgreSQL and artifacts from the same recovery point,
run migrations to head, and repeat owner plus cross-user tests.

## Disaster recovery

Restore the NFS data and SOPS-captured Secrets, sync Job Assistant and migrate
before starting the gateway, verify its image SHA exists, and ensure no other
poller is running. Restore the shared Telegram Secret only into
`shared-services-telegram`. After recovery, test unknown-user silence, owner
access, cross-user denial, notification lease/ack, and token rotation before
re-enabling friend access.
