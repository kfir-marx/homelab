# Homelab — Tasks & Current State

Snapshot taken: 2026-05-05. Verified against the live Proxmox API at `192.168.1.101:8006` and the local Terragrunt cache.

---

## What you have (working)

### Repo / IaC scaffolding
- [x] Terragrunt-based deployment layout under [terraform/deployments/](terraform/deployments/) with hierarchical YAML config merging via [merge_configs.sh](terraform/deployments/merge_configs.sh) and [root.hcl](terraform/deployments/root.hcl).
- [x] Reusable stack module [terraform/modules/stacks/homelab-cluster/](terraform/modules/stacks/homelab-cluster/) with two sub-modules:
  - [proxmox-vm](terraform/modules/stacks/homelab-cluster/modules/proxmox-vm/main.tf) — single QEMU VM with optional GPU passthrough.
  - [talos-cluster](terraform/modules/stacks/homelab-cluster/modules/talos-cluster/main.tf) — Talos secrets, machine configs, bootstrap, kubeconfig.
- [x] Wrapper script [terraform/deploy.sh](terraform/deploy.sh) that loads `.env`, builds `PROXMOX_API_TOKEN`, and dispatches to `terragrunt`.
- [x] Two environments wired up: [prod/config.yml](terraform/deployments/prod/config.yml) (3 CP + 2 W + 1 GPU on `10.0.10.0/24`) and [staging/config.yml](terraform/deployments/staging/config.yml) (1 CP + 1 W on `10.0.20.0/24`).
- [x] Local toolchain present: `terraform 1.9.2`, `terragrunt 0.63.0`, `kubectl`, `helm`.

### Proxmox cluster (verified via API)
- [x] All 7 Proxmox hosts online and reachable: `worker1`, `worker2`, `worker3`, `worker4`, `storage1`, `gpunvdgtx1060`, `largegpu`.
- [x] Talos `v1.9.5` nocloud image uploaded to `local` datastore on `worker1` and `worker2`.
- [x] Staging VMs created and **running**:
  - `cp-1` (VMID `501`) on `worker1` — 2 cores / 4 GiB / 30 GB.
  - `worker-1` (VMID `601`) on `worker2` — 4 cores / 8 GiB / 50 GB.

### Docs
- [x] [docs/architecture.md](docs/architecture.md) — full design write-up.
- [x] [docs/remote-access.md](docs/remote-access.md) — Cloudflare Tunnel + Headscale strategy.

---

## What you still need to do

### 1. Finish the staging deploy (blocked — VMs created, Talos never bootstrapped)
The staging tfstate at [terraform/deployments/staging/homelab-cluster/.terragrunt-cache/.../terraform.tfstate](terraform/deployments/staging/homelab-cluster/) contains **only 3 resources** (1 download_file × 2 nodes + 2 VMs). Zero `talos_machine_*` and zero `helm_release` resources — the apply stopped before Talos config was applied. Likely cause: the machine running `terragrunt apply` could not reach the staging VM subnet `10.0.20.0/24`, so the Talos API at `10.0.20.11` and the cluster endpoint VIP `10.0.20.100` were unreachable. From this Mac I confirmed `ping 10.0.20.11` and `ping 10.0.20.100` both fail; only `192.168.1.101` is reachable.

- [ ] **Decide where the apply runs from.** Either:
  - (a) Run `terraform/deploy.sh staging homelab-cluster apply` from a machine that has L3 reachability to `10.0.20.0/24` (e.g. on the Proxmox host or behind the same router with a static route), **or**
  - (b) Populate `proxmox_node_ips` in [staging/config.yml](terraform/deployments/staging/config.yml) so the `terraform_data.proxmox_subnet_gateway` resource adds the gateway IP and IP-forwarding to the Proxmox host (already wired in [stacks/homelab-cluster/main.tf:98-136](terraform/modules/stacks/homelab-cluster/main.tf#L98-L136)). Today the file has the entry but with a `TODO: verify this IP` for `worker2: 192.168.1.102` — confirm and lock in.
- [ ] After fixing reachability, re-run `terraform/deploy.sh staging homelab-cluster apply`. State should then gain `talos_machine_secrets`, the per-role `talos_machine_configuration_apply` resources, `talos_machine_bootstrap`, and the two `helm_release` (`argocd`, `argocd_root_app`).
- [ ] Install `talosctl` locally (currently missing) so you can run `talosctl health`, `dashboard`, etc. once staging is up.

### 2. Deploy prod (never run)
- [ ] No tfstate exists under [terraform/deployments/prod/homelab-cluster/](terraform/deployments/prod/homelab-cluster/) — only `terragrunt.hcl`. Once staging is verified end-to-end, run `terraform/deploy.sh prod homelab-cluster plan` and review.
- [ ] The Talos image is **only present on `worker1` and `worker2`** today. The prod plan needs to download it to `worker3`, `worker4`, `storage1`, and `gpunvdgtx1060` as well — that's automatic via `proxmox_virtual_environment_download_file.talos_image` in [stacks/homelab-cluster/main.tf:23-32](terraform/modules/stacks/homelab-cluster/main.tf#L23-L32) (it iterates over `local.all_proxmox_nodes`), but expect the first plan to show 4 new image downloads.
- [ ] [prod/config.yml](terraform/deployments/prod/config.yml) does **not** set `proxmox_node_ips`. Either add it (so the host-routing resources run) or accept that you'll need to apply from a machine with direct L3 access to `10.0.10.0/24`.
- [ ] On the GPU host (`gpunvdgtx1060`), confirm IOMMU is enabled (`intel_iommu=on` / `amd_iommu=on` in kernel params) and the GTX 1060 (PCI `01:00`) is bound to `vfio-pci`. This is a host prerequisite — Terraform won't fix it.

### 3. Fix CI/Atlantis path mismatch (broken — pointing at non-existent dirs)
- [ ] [.github/workflows/terraform-plan.yml:15](.github/workflows/terraform-plan.yml#L15) sets `TF_WORK_DIR: terraform/environments/prod`, but that directory does **not** exist — current layout is `terraform/deployments/prod/homelab-cluster/` with Terragrunt. The `init`/`validate`/`plan` jobs will fail. Either rewrite the workflow to use `terragrunt` (e.g. `terragrunt run-all plan`) inside `terraform/deployments/`, or delete it if Atlantis is the source of truth.
- [ ] [atlantis.yaml:18](atlantis.yaml#L18) similarly points at `terraform/environments/prod`. Update `dir`, `when_modified` globs, and add a project for `staging` if you want both environments gated.
- [ ] Atlantis itself is **not yet deployed** anywhere — see remote-access work below.

### 4. Wire up remote Terraform state
- [ ] [root.hcl:38-54](terraform/deployments/root.hcl#L38-L54) has the S3 backend block commented out. Today every apply leaves state (including Talos PKI and Proxmox tokens) in the local `.terragrunt-cache/`. Pick a backend (S3+KMS, Terraform Cloud, or a local Minio/MinIO bucket on `storage1`) and uncomment.
- [ ] Add the backend bucket/lock-table to the prerequisite checklist before the first prod apply.

### 5. Build out [kubernetes/](kubernetes/) (currently empty)
All three subtrees only contain `.gitkeep`:
- [ ] [kubernetes/system/](kubernetes/system/) — at minimum: `cert-manager`, `ingress-nginx` (or Traefik), `nvidia-device-plugin` DaemonSet (required for `nvidia.com/gpu` resource on the GPU node — see [architecture.md:213](docs/architecture.md#L213)), `metallb` or equivalent (the ArgoCD Helm release uses `service.type=LoadBalancer`).
- [ ] [kubernetes/apps/](kubernetes/apps/) — first ArgoCD `Application` manifests; the root app already points here ([main.tf:218-252](terraform/modules/stacks/homelab-cluster/main.tf#L218-L252)).
- [ ] [kubernetes/bootstrap/](kubernetes/bootstrap/) — anything one-shot that sits outside ArgoCD's reconciliation loop.

### 6. Remote access (designed in docs, not implemented)
- [ ] **Cloudflare Tunnel** — `cloudflared` Deployment + `Tunnel`/`Ingress` config to expose `jellyfin.547600.xyz` (per [remote-access.md](docs/remote-access.md)). Needs a Cloudflare API token stored as a sealed/external secret.
- [ ] **Headscale** — server pod + ingress for admin VPN access to ArgoCD/Grafana/etc.
- [ ] **Atlantis self-hosted** — required for the PR-driven apply flow described in [atlantis.yaml](atlantis.yaml). Easiest path: deploy via a Helm chart in `kubernetes/system/` once an ingress exists.

### 7. Documentation drift
- [ ] [README.md](README.md) and [architecture.md](docs/architecture.md) describe `terraform/environments/prod/{providers,variables,main}.tf` and `terraform.tfvars.example` — that layout no longer exists. Update both to describe the Terragrunt + YAML config layout actually in use.
- [ ] [architecture.md](docs/architecture.md) lists 6 Proxmox hosts; the live cluster has **7** (`largegpu` is online but unused by any environment config). Either add `largegpu` to a node map or document why it's reserved.
- [ ] [docs/architecture.md:303-309](docs/architecture.md#L303-L309) "Post-deploy checklist" mostly overlaps with this file — once items here are tracked, prune the doc version to avoid two TODO lists going stale.

### 8. Security / hygiene
- [ ] [.env](.env) contains `PROXMOX_SSH_PASSWORD="12345678"` in plaintext at the repo root. `.env` is in [.gitignore](.gitignore), but the password itself is weak and is also used (via env var) by SSH provisioners in [main.tf:108](terraform/modules/stacks/homelab-cluster/main.tf#L108). Rotate to a strong password (or, preferably, switch the `remote-exec` provisioner to key-based auth) before exposing anything beyond the LAN.
- [ ] [.github/workflows/terraform-plan.yml:54](.github/workflows/terraform-plan.yml#L54) has `tfsec` on `soft_fail: true`. Tighten to blocking once you've triaged the existing findings.

---

## Quick verification commands

```bash
# Re-check Proxmox state any time
source .env
export PROXMOX_API_TOKEN="${PROXMOX_APITOKEN_ID}=${PROXMOX_APITOKEN_SECRET}"
curl -sk -H "Authorization: PVEAPIToken=${PROXMOX_API_TOKEN}" \
  "https://${PROXMOX_HOST}:${PROXMOX_PORT}/api2/json/cluster/resources?type=vm" | jq

# Re-plan staging
./terraform/deploy.sh staging homelab-cluster plan

# Re-plan prod
./terraform/deploy.sh prod homelab-cluster plan
```
