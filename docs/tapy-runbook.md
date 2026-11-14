# Hosting standalone Tapy

Tapy application source, tests, packaging, provider copy and operations now live in
`/home/kfir/repos/tapy` and the private `kfir-marx/tapy` GitHub repository.
The complete source path is `deploy/kubernetes/overlays/homelab`. Its
`docs/cutover.md` is the authoritative source/broker handoff and rollback procedure;
`docs/runbook.md`, `docs/kubernetes-portability.md`, `docs/production-gke.md` and
`docs/google-oauth-verification.md` own product operations and production design.

Homelab retains the cluster/Cilium/DNS/storage connectivity, NFS directory/export
Ansible declarations, `homelab-tapy` PriorityClass, Cloudflare tunnel route and
provider-owned admission in `kubernetes/system/cloudflared/tapy-admission.yaml`,
encrypted development Secret recovery snapshots, inventory and the single
`kubernetes/apps/tapy.yaml` Application. Tapy owns all namespaced workloads in `tapy`,
plus explicitly hard-bound retained `tapy-*` volumes in its homelab overlay.

## Initial manual cutover

The Application points to the private standalone repository through a read-only
deploy key in `argocd/tapy-repository` and a reviewed immutable release revision.
The three product images are independently published from source commit
`c193d3673918c8580cf05787ea43250f57bea9c4`; the homelab image-pull credential is
`tapy/tapy-ghcr-pull`. Both credentials have protected recovery inventory entries.

Keep automatic synchronization and pruning disabled during initial validation,
including the parent `root` Application so it cannot reconcile the child during
maintenance. Do not synchronize the entire parent to perform a Tapy cutover.
The Tapy Application has no deletion finalizer and retains Prune=false/Delete=false
annotations; retained storage also has deletion guards. Follow the standalone
cutover runbook, review the live diff, and stage dependencies before starting new
application writers. This configuration alone is not functional validation.

Existing PostgreSQL identity remains `tapy-postgres-tapy-pv` hard-bound to
`tapy/tapy-postgres`, 5 Gi, `nfs-storage2`, ReadWriteOnce, Retain, at
`192.168.1.105:/mnt/storage2-bulk/tapy/postgres`. Do not replace or rebind it. Preserve
the database password, OAuth encryption key and stored refresh tokens. Take and
restore-test an encrypted database-consistent backup before cutover.

An authorized Ansible NFS convergence must create these new directories only:

| Path under `/mnt/storage2-bulk/tapy/external-ai/` | UID:GID | Mode |
|---|---|---|
| `postgres` | 999:999 | 0700 |
| `codex-home` | 10001:10001 | 0700 |

Use the narrow playbook from `ansible/` during the authorized cutover:

```bash
ansible-playbook playbooks/prepare-tapy-external-ai-storage.yml --check --diff --ask-become-pass
ansible-playbook playbooks/prepare-tapy-external-ai-storage.yml --diff --ask-become-pass
```

It checks the existing critical filesystem UUID and read/write mount, uses the
declared directory ownership/modes, and creates only missing dedicated paths plus
their root-owned 0755 `external-ai` parent. It refuses symlinks and unexpected
existing ownership/modes rather than repairing them. It does not change existing
directories, exports, mounts, packages, services or the application database path.
The full workstation playbook also manages unrelated host configuration and is
not the command for this cutover.

Tapy uses its own broker (`tapy-rabbitmq`, disposable emptyDir) and provider
(`tapy-external-ai-*`), never shared homelab services. Their Secret contracts in
`secrets/inventory.tsv` are provisioned and captured for the initial cutover. Keep
the existing
`tapy-secrets` database/OAuth material and `tapy-frontend-secrets` Twilio values;
change only the broker URL at the quiesced cutover, then recapture the snapshot.
All new Secret names begin `tapy-` in namespace `tapy`. Do not decrypt or copy values
into the product repository. Never fabricate a SOPS snapshot to satisfy inventory.
Register/uncomment the optional Codex inventory entry and capture its bootstrap
snapshot only if that provider is enabled.

## Initial validation on 2026-11-14

The existing Application was cut over to immutable Tapy release
`806eee050b4500c5ff7370304b85e44f59e329db`, using the three published
`sha-c193d3673918c8580cf05787ea43250f57bea9c4` images. Registry source labels and
running image digests were verified. Public frontend/API routing, synthetic
Alibaba extraction through the dedicated broker, allowed/denied network probes,
broker permission denials, worker outbox recovery, and dedicated broker replacement
passed. Cilium flow evidence confirmed shared-service policy denials. Synthetic
markers survived replacement of the dedicated provider database/worker Pods and
were removed afterward. No customer mailbox scans or notifications were used.

An encrypted backup taken with application writers stopped restored successfully
into network-isolated disposable PostgreSQL. Organization/member/mailbox ownership
inventory, 3 users, 3 organizations, 3 mailboxes, 5 bookings, migration
`20260910_03`, and the original database PV/PVC UIDs/binding were preserved. Only
the existing application Secret's broker URL changed; database/OAuth material and
Twilio credentials were retained. All required new/changed Secrets were recaptured.

Traffic is restored. Keep both Tapy and parent `root` automatic synchronization
and pruning disabled. Argo reports Healthy with a successful sync, but OutOfSync
for twelve superseded legacy network policies intentionally retained during this
no-prune validation. Their shared-broker allowances were updated in place; live
denied-flow tests passed. Review cleanup separately rather than enabling pruning
to clear the status.

Mailbox scans, OAuth consent/token refresh and external notifications remain
operator tests after deployment. CPU HPA metrics and the unavailable internal LLM
were pre-existing cluster conditions and were not repaired by this cutover.
Rollback remains the old homelab revision
`13191e5bc6211855b1c8ca63b505c7b141d65cfd` and its protected pre-broker-change
Secret snapshot. Stop all new writers first; preserve all retained data and new
dependency state, and never restore the backup over the live database as a shortcut.

## Rollback boundary

Homelab shared `services/external-ai`, its image/workflow/Application/volumes and
RabbitMQ remain independent. Job Assistant still calls homelab external-ai;
internal-llm still uses shared homelab RabbitMQ. Their live behavior was not changed.

Retain `rabbitmq/rabbitmq-tapy-user`, its inventory/snapshot and shared broker Tapy
permission rules until successful live cutover and a rollback window are complete.
Those legacy permissions include internal/external queues for the old deployment;
they are not part of the new Tapy broker. Cleanup needs separate authorization.
Rollback uses the recorded pre-split homelab Git revision, never a current duplicate
source tree. Quiesce writers before reverting broker URLs/source. Keep all new and
old PVs, PVCs, NFS paths, Secrets and OAuth state; do not cascade-delete the Application.

## Static checks

Render the standalone homelab source with `kubectl kustomize` and run its
`scripts/check-kubernetes.sh`. Validate the provider Application and Cloudflared
source with client-side schemas. `scripts/check-tapy-boundary.py` checks provider
ownership and protects shared-service endpoints without contacting the cluster.
