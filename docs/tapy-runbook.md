# Hosting standalone Tapy

Tapy application source, tests, packaging, provider copy and operations now live in
`/home/kfir/repos/tapy` (remote repository creation/URL verification is pending).
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

## Static handoff and cutover prerequisites

The Application uses an explicit unavailable repository placeholder, manual sync,
and no deletion finalizer. Do not sync it until the remote exists, Argo credentials
are installed if private, all three independent images are published/pinned, and
the standalone cutover runbook is reviewed. Coordinate app-of-apps automation so
it cannot roll out this source switch prematurely. The Application itself has
Prune=false/Delete=false annotations; retained storage also has deletion guards.
No final source switch, Argo sync or infrastructure mutation was performed.

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
(`tapy-external-ai-*`), never shared homelab services. New Secret contracts in
`secrets/inventory.tsv` are pending operator creation/capture. Keep the existing
`tapy-secrets` database/OAuth material and `tapy-frontend-secrets` Twilio values;
change only the broker URL at the quiesced cutover, then recapture the snapshot.
All new Secret names begin `tapy-` in namespace `tapy`. Do not decrypt or copy values
into the product repository. Never fabricate a SOPS snapshot to satisfy inventory.
Register/uncomment the optional Codex inventory entry and capture its bootstrap
snapshot only if that provider is enabled.

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
