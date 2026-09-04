# AI agent context map

This is an **on-demand repository index**, not the automatically loaded agent
prompt. Codex reads the root [`AGENTS.md`](AGENTS.md) before working. Read this
file when a task is cross-cutting or when you need to locate the authoritative
context; for a focused task, use the routing table and open only the relevant
files.

## Repository mission

This repository declaratively manages a homelab across three layers:

1. Ansible configures the physical Proxmox and Ubuntu hosts.
2. Terraform/Terragrunt provisions Proxmox VMs and bootstraps Talos,
   Kubernetes, Cilium, and Argo CD.
3. Argo CD reconciles applications and cluster resources from
   `kubernetes/apps/` and `kubernetes/system/`.

The production model defines three Talos control-plane VMs, one on each of
`largegpu`, `smallgpu`, and `tinygpu`, plus GPU-capable workers on `largegpu`
and `smallgpu`. Etcd therefore retains quorum after one Proxmox failure-domain
loss. The separate Ubuntu workstation is an NFS server and desktop; it is not
a Proxmox host or Kubernetes worker.

## Source-of-truth order

When documentation and implementation disagree, determine intent from this
order and report the mismatch:

1. Environment configuration and executable manifests
2. Terraform/Ansible implementation
3. Focused runbooks
4. `docs/architecture.md`
5. Human-facing `README.md`

Do not silently edit code to match prose. Establish which side is stale first.

## Context routing

| Task | Read first | Then inspect if needed |
|---|---|---|
| Production topology, IPs, VM sizing | `terraform/deployments/prod/config.yml` | `terraform/deployments/config.yml`, relevant stack variables |
| Talos, Kubernetes, Cilium, or Argo CD bootstrap | `terraform/modules/stacks/homelab-cluster/main.tf` | `modules/talos-cluster/`, `helm/`, `talos-images/` |
| Windows VM or RTX 3080 sharing | `terraform/deployments/prod/windows-workstation/config.yml` | `terraform/modules/stacks/windows-workstation/`, `terraform/modules/components/proxmox-windows-vm/`, `docs/windows-vm-backup.md` for backup and recovery |
| Physical Proxmox hosts | `ansible/README.md` and the relevant playbook | matching inventory host vars and role |
| Ubuntu workstation or critical NFS | `docs/ubuntu-workstation-runbook.md` | `ansible/playbooks/configure-ubuntu-workstation.yml`, Ubuntu host vars, `nfs_server` role |
| Kubernetes application | matching file in `kubernetes/apps/` | its directory under `kubernetes/system/` and matching runbook |
| Kubernetes network policy | `docs/network-policies.md` | matching workload manifests and Cilium bootstrap configuration |
| Storage placement or PV changes | storage section in `docs/architecture.md` | `kubernetes/system/storage/`, app storage manifest, NFS host vars |
| Remote access or DNS | `docs/remote-access.md` | Tailscale/AdGuard runbook and matching manifests |
| Secrets or bare-metal credential recovery | `docs/secrets-disaster-recovery.md` | `scripts/secrets.sh`, `secrets/inventory.tsv`, then the consuming layer |
| Monitoring or GPUs | matching runbook | Argo CD application and system manifests |
| CI or delivery | `.github/workflows/terraform-plan.yml`, `docs/github-actions-runners-runbook.md` | `kubernetes/apps/github-actions-*.yaml`, `terraform/run-terragrunt.sh` |
| Broad architecture change | relevant config and manifests first | only the affected sections of `docs/architecture.md` |

Use `rg` to locate symbols and read narrow sections. Do not load every runbook
for a single-layer change.

## Critical invariants

- Ansible owns physical-host state; Terraform owns VMs and cluster bootstrap;
  Argo CD owns in-cluster resources. Do not introduce overlapping ownership.
- `ubuntu-workstation` holds the critical 800 GB NFS tier and must retain its
  local GTX 1060/HDMI. It must not be converted back into a hypervisor.
- `smallgpu` and `largegpu` are borrowed hardware. Only reproducible or
  disposable application data may depend on them. `tinygpu` has no declared
  storage, UPS, NFS, or VFIO role.
- The RTX 3080 on `largegpu` is shared by Talos VM `402` and Windows VM `502`.
  They must never be started at the same time.
- Preserve the three-control-plane placement: `cp-1` on `largegpu`, `cp-2` on
  `smallgpu`, and `cp-3` on `tinygpu`, each at 4 GiB. Preserve the existing
  Talos machine secrets and etcd data and never run `talosctl bootstrap` again.
  A single host loss retains etcd quorum; two control-plane host losses do not.
- Keep `tinygpu` dedicated to `cp-3` unless a fresh live capacity review
  justifies a worker. Its current 4-core / 11.63 GiB budget favors control-plane
  stability.
- Existing filesystems are adopted, validated, and mounted. Automation must not
  partition, format, force-mount, or clear filesystem safety flags.
- Static persistent volumes use explicit storage classes and `Retain`. Critical
  data belongs on `nfs-storage2`; reproducible bulk data belongs on
  `nfs-storage1`; GPU scratch is disposable.
- Secrets and access artifacts must stay out of Git and command output.

## Safe change workflow

1. Check `git status` and preserve unrelated user changes.
2. Read the smallest relevant source set from the routing table.
3. State any assumption that affects live infrastructure, storage, networking,
   or ownership.
4. Make the declarative change at the owning layer.
5. Update only the documentation whose behavior actually changed.
6. Run the narrowest applicable static checks.
7. Summarize changed files, validation performed, and any live action still
   required.

Plans, syntax checks, and client-side dry runs are acceptable verification.
Do not apply Terraform, run a mutating Ansible play, sync Argo CD, apply
Kubernetes manifests, reboot hosts, or start/stop VMs unless the user explicitly
requests that live action.

## Local secrets and live access

- `.env` and `kubeconfig.yaml` are gitignored local files.
- Their presence means access may be available; it does not authorize a live
  change.
- Do not read them for documentation, refactoring, formatting, or static
  validation tasks.
- If live access is explicitly required, source or pass credentials without
  printing their values. Never include secret-bearing output in the response.
- Never add literal credentials to YAML, Terraform, Ansible inventory,
  documentation, examples, or tests. Use `.env-template` only for variable
  names and placeholders.

## Validation menu

Choose only checks relevant to the files changed:

```bash
# Terraform and Terragrunt formatting (same checks used by CI)
terraform fmt -check -recursive terraform/
terragrunt hclfmt --terragrunt-check --terragrunt-working-dir terraform/deployments

# Shell scripts
bash -n terraform/run-terragrunt.sh terraform/deployments/merge_configs.sh

# Ansible structure; run from ansible/
ansible-playbook playbooks/configure-proxmox.yml --syntax-check
ansible-playbook playbooks/verify-proxmox.yml --syntax-check

# Read-only infrastructure plan when explicitly relevant and dependencies exist
./terraform/run-terragrunt.sh prod homelab-cluster plan -input=false
./terraform/run-terragrunt.sh prod windows-workstation plan -input=false

# Read-only live host verification when explicitly requested
cd ansible && ansible-playbook playbooks/verify-proxmox.yml
```

For Kubernetes YAML, inspect the Argo CD `Application` and all resources at its
source path together. Prefer a client-side schema/dry-run check when the needed
CRDs and tooling are available; do not contact the live cluster unless the task
requires it.
