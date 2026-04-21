# Homelab — GitOps Kubernetes on Proxmox

Fully automated, GitOps-driven Kubernetes cluster running Talos Linux on a 6-node Proxmox hypervisor fleet. Infrastructure is provisioned with Terraform; in-cluster workloads are managed by ArgoCD. No SSH, no manual `kubectl apply` — everything flows through Git.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Git repository (this repo)                   │
│                                                                     │
│  terraform/           Provisions VMs + bootstraps the cluster       │
│  kubernetes/          ArgoCD-managed app manifests                  │
│  .github/workflows/   CI: lint, validate, security scan, plan      │
│  atlantis.yaml        PR-driven terraform plan/apply                │
└────────────┬──────────────────────────────────┬─────────────────────┘
             │                                  │
     Terraform apply                   ArgoCD sync (automated)
             │                                  │
             ▼                                  ▼
┌────────────────────────┐       ┌──────────────────────────┐
│   Proxmox VE cluster   │       │   Kubernetes cluster     │
│                        │       │   (Talos Linux)          │
│  pve1  ── cp-1    (CP) │       │                          │
│  pve2  ── cp-2    (CP) │       │  ArgoCD ─► apps/         │
│  pve3  ── cp-3    (CP) │       │            system/        │
│  pve4  ── worker-1 (W) │       │            bootstrap/     │
│  pve5  ── gpu-large(G) │       │                          │
│  pve6  ── gpu-mid  (G) │       │  GPU nodes tainted with  │
│                        │       │  nvidia.com/gpu=NoSchedule│
└────────────────────────┘       └──────────────────────────┘
```

### Physical nodes (Proxmox hosts)

| Proxmox host | VM name      | Role              | Key specs (defaults)              |
|--------------|-------------|-------------------|-----------------------------------|
| `pve1`       | `cp-1`      | Control plane     | 4 cores, 8 GiB RAM, 50 GB disk   |
| `pve2`       | `cp-2`      | Control plane     | 4 cores, 8 GiB RAM, 50 GB disk   |
| `pve3`       | `cp-3`      | Control plane     | 4 cores, 8 GiB RAM, 50 GB disk   |
| `pve4`       | `worker-1`  | Compute worker    | 8 cores, 16 GiB RAM, 100 GB disk |
| `pve5`       | `gpu-large` | GPU worker (strong) | 16 cores, 64 GiB RAM, 500 GB disk, PCIe GPU |
| `pve6`       | `gpu-mid`   | GPU worker (mid)  | 8 cores, 32 GiB RAM, 250 GB disk, PCIe GPU  |

All specs, IPs, MAC addresses, and PCI device IDs are defined in `terraform/environments/prod/variables.tf` as map variables and can be overridden in `terraform.tfvars`.

### Network defaults

| Setting          | Default value       |
|------------------|---------------------|
| Cluster VIP      | `10.0.10.100`       |
| K8s API endpoint | `https://10.0.10.100:6443` |
| Gateway          | `10.0.10.1`         |
| Bridge           | `vmbr0`             |
| DNS              | `1.1.1.1`, `8.8.8.8` |
| CP node IPs      | `10.0.10.11-13/24`  |
| Worker IPs       | `10.0.10.21/24`     |
| GPU node IPs     | `10.0.10.31-32/24`  |

The control-plane VIP is managed by Talos's built-in VIP mechanism — no external load balancer needed. Each control-plane node's network interface includes a `vip` block pointing at the shared VIP.

---

## Technology stack

| Layer              | Tool                          | Purpose                                                  |
|--------------------|-------------------------------|----------------------------------------------------------|
| Hypervisor         | Proxmox VE                    | Runs all VMs across 6 physical hosts                     |
| Node OS            | Talos Linux (`v1.9.5`)        | Immutable, API-driven Linux — no SSH, no shell           |
| VM provisioning    | Terraform + `bpg/proxmox` provider (~> 0.78) | Creates QEMU VMs with optional PCIe passthrough |
| Cluster bootstrap  | Terraform + `siderolabs/talos` provider (~> 0.7) | Generates machine configs, applies them, bootstraps etcd |
| GitOps engine      | ArgoCD (Helm chart `argo-cd` v7.8.13) | Manages all in-cluster workloads from Git              |
| CI                 | GitHub Actions                | Runs `terraform fmt`, `validate`, `tfsec`, and `plan` on PRs |
| CD / Terraform PRs | Atlantis                      | Runs `terraform plan` on PR open, `apply` on PR approval |

---

## Repository structure

```
.
├── .github/
│   └── workflows/
│       └── terraform-plan.yml        # CI pipeline: lint → security → plan
├── atlantis.yaml                     # Atlantis repo-level config
├── terraform/
│   ├── environments/
│   │   └── prod/
│   │       ├── providers.tf          # Proxmox, Talos, Helm provider config
│   │       ├── variables.tf          # All node definitions, network, cluster settings
│   │       ├── main.tf               # Orchestration: VMs → Talos → ArgoCD
│   │       └── terraform.tfvars.example  # Template for credentials
│   └── modules/
│       ├── proxmox-vm/
│       │   └── main.tf               # Single VM resource with dynamic GPU passthrough
│       └── talos-cluster/
│           ├── main.tf               # Secrets, machine config, bootstrap, kubeconfig
│           └── talos-gpu-patch.yaml   # NVIDIA extensions + containerd runtime
└── kubernetes/
    ├── apps/                          # ArgoCD Application manifests (app of apps)
    ├── system/                        # Cluster-wide services (cert-manager, ingress, etc.)
    └── bootstrap/                     # One-time bootstrap resources
```

### Key file reference

| File | What it does | When to edit |
|------|-------------|--------------|
| `terraform/environments/prod/variables.tf` | Defines every node's specs, IPs, MACs, PCI IDs as typed maps | Adding/removing nodes, changing hardware |
| `terraform/environments/prod/main.tf` | Calls modules in order: VMs → Talos → ArgoCD bootstrap + root app | Changing orchestration logic, ArgoCD settings |
| `terraform/environments/prod/providers.tf` | Configures Proxmox API, Talos, and Helm providers; remote state backend | Changing Proxmox endpoint, enabling remote state |
| `terraform/modules/proxmox-vm/main.tf` | Creates one Proxmox QEMU VM with conditional GPU passthrough | Changing VM hardware defaults, disk config |
| `terraform/modules/talos-cluster/main.tf` | Generates Talos machine configs per role, applies them, bootstraps etcd | Changing Talos config patches, cluster topology |
| `terraform/modules/talos-cluster/talos-gpu-patch.yaml` | NVIDIA system extensions, kernel modules, containerd config | Upgrading GPU driver version, adding kernel params |
| `kubernetes/apps/` | ArgoCD watches this directory for Application manifests | Deploying any new workload |
| `.github/workflows/terraform-plan.yml` | CI: format check → validate → tfsec → plan posted to PR | Changing CI behavior |
| `atlantis.yaml` | Defines which dirs Atlantis watches and apply requirements | Adding environments, changing approval rules |

---

## How Terraform is organized

### Execution flow

`terraform apply` executes a single DAG with explicit `depends_on` ordering:

```
1. module.control_plane_vms  ─┐
2. module.worker_vms          ├──► 3. module.talos_cluster ──► 4. helm_release.argocd ──► 5. helm_release.argocd_root_app
2. module.gpu_vms            ─┘
```

**Step 1-2:** The `proxmox-vm` module creates QEMU VMs on the specified Proxmox hosts. GPU nodes get `bios = "ovmf"`, an EFI disk, and dynamic `hostpci` blocks for PCIe passthrough. All VMs boot from the Talos ISO.

**Step 3:** The `talos-cluster` module generates per-node machine configurations using `talos_machine_configuration` data sources. Each node gets a config patch with its hostname, static IP, and routes. GPU nodes additionally receive the `talos-gpu-patch.yaml` (NVIDIA extensions) and labels/taints. The module then applies configs via `talos_machine_configuration_apply`, bootstraps etcd on the first control-plane node, and retrieves the kubeconfig.

**Step 4:** The Helm provider (authenticated via the kubeconfig from step 3) installs ArgoCD into the `argocd` namespace.

**Step 5:** A second Helm release deploys the ArgoCD "root Application" using the `argocd-apps` chart, pointing at `kubernetes/apps/` in this repo with automated sync + prune + self-heal. From this point, ArgoCD owns all in-cluster state.

### Module: `proxmox-vm`

Creates a single Proxmox QEMU VM. Key behaviors:

- **Machine type:** Always `q35` (required for PCIe passthrough, fine for non-GPU too).
- **BIOS:** `ovmf` (UEFI) for GPU nodes, `seabios` for others. OVMF requires the dynamic `efi_disk` block.
- **CPU type:** Always `host` — required for GPU passthrough and gives best performance.
- **PCIe passthrough:** Uses a `dynamic "hostpci"` block that iterates over `var.pci_devices`. Empty list = no passthrough.
- **Boot:** VMs boot from the Talos ISO via a CDROM. The `lifecycle.ignore_changes` on `cdrom` prevents Terraform from fighting Talos after it installs to disk.
- **Tags:** All VMs are tagged `["talos", "terraform"]` in Proxmox for visibility.

### Module: `talos-cluster`

Manages the full Talos lifecycle:

- **`talos_machine_secrets`**: Generates cluster-wide PKI (etcd CA, Kubernetes CA, etc.) — stored in Terraform state (treat state as secret).
- **`talos_machine_configuration` (data source)**: One per node. Produces the full machine config from cluster name + endpoint + secrets + config patches.
- **Config patches are layered per role:**
  - *All nodes:* hostname, static IP, routes, nameservers.
  - *Control-plane only:* VIP configuration on `eth0`.
  - *GPU workers only:* `talos-gpu-patch.yaml` + node labels (`nvidia.com/gpu.present=true`, `homelab.dev/role=gpu-worker`) + taint (`nvidia.com/gpu=true:NoSchedule`).
- **`talos_machine_configuration_apply`**: Pushes the config to each node over the Talos API.
- **`talos_machine_bootstrap`**: Runs once on the first control-plane node to initialize etcd.
- **`talos_cluster_kubeconfig`**: Retrieves the kubeconfig after bootstrap.

### Node variables schema

Nodes are defined as `map(object)` variables — the map key is the hostname. Three separate variables for three roles:

```hcl
# control_plane_nodes — fields: proxmox_node, vm_id, ip_address, mac_address, cores, memory_mb, disk_size_gb
# worker_nodes        — same fields as control_plane_nodes
# gpu_nodes           — same fields + pci_devices: list(object({ id = string, pcie = bool }))
```

The `ip_address` field uses CIDR notation (e.g. `10.0.10.11/24`). The Talos module uses `split("/", ip)` to extract the bare IP where needed.

---

## GPU passthrough pipeline

GPU support spans two layers — Proxmox (hardware passthrough) and Talos (OS-level NVIDIA stack):

```
Proxmox host                     Talos VM                           Kubernetes
─────────────                    ────────                           ──────────
IOMMU enabled          ──►  PCIe device visible       ──►   nvidia-container-runtime
vfio-pci driver bound         in the VM                       registered in containerd
via hostpci block             kernel modules loaded:          
                              nvidia, nvidia_uvm,             Pods with toleration
                              nvidia_drm, nvidia_modeset      nvidia.com/gpu=NoSchedule
                                                              can schedule here
                              System extensions:
                              nvidia-container-toolkit
                              nvidia-open-gpu-kernel-modules
```

**Proxmox side** (handled by `proxmox-vm` module):
- GPU VMs use `bios = "ovmf"` and `machine = "q35"`.
- Each GPU's PCI address (e.g. `01:00`) is passed through via `hostpci` blocks.
- Proxmox host must have IOMMU enabled and the GPU bound to `vfio-pci` (host-level prerequisite, not managed by Terraform).

**Talos side** (handled by `talos-gpu-patch.yaml`):
- Installs `nvidia-container-toolkit` and `nvidia-open-gpu-kernel-modules` as Talos system extensions (these are baked into the OS image, not Kubernetes DaemonSets).
- Loads four kernel modules at boot: `nvidia`, `nvidia_uvm`, `nvidia_drm`, `nvidia_modeset`.
- Writes a containerd config snippet to `/etc/cri/conf.d/20-customization.part` that registers the `nvidia` runtime as the default container runtime on GPU nodes.
- Sets `vm.nr_hugepages = 1024` for large-memory GPU workloads.

**Kubernetes side** (handled inline in `talos-cluster/main.tf`):
- GPU nodes get labels: `nvidia.com/gpu.present=true`, `homelab.dev/role=gpu-worker`, `homelab.dev/gpu-node=<hostname>`.
- GPU nodes get taint: `nvidia.com/gpu=true:NoSchedule` — only pods with the matching toleration will schedule here.
- You still need to deploy the [NVIDIA device plugin DaemonSet](https://github.com/NVIDIA/k8s-device-plugin) via ArgoCD to expose `nvidia.com/gpu` as a schedulable resource. Place that manifest in `kubernetes/system/`.

---

## CI/CD pipeline

### GitHub Actions (`.github/workflows/terraform-plan.yml`)

Triggers on PRs to `main` that touch `terraform/**`. Runs three jobs:

1. **Lint & Format** — `terraform fmt -check -recursive` + `terraform validate` (init with `-backend=false`).
2. **Security Scan** — `tfsec` via the `aquasecurity/tfsec-action`. Currently `soft_fail: true` (non-blocking) — tighten once rules are tuned.
3. **Terraform Plan** — runs `terraform plan` and posts the output as a PR comment inside a collapsible `<details>` block. Requires GitHub secrets `PROXMOX_API_URL` and `PROXMOX_API_TOKEN`.

The plan job depends on lint + security passing first.

### Atlantis (`atlantis.yaml`)

Atlantis provides the PR-driven plan/apply workflow:

- **Watches:** `terraform/environments/prod/` and all files under `terraform/modules/`.
- **Auto-plan:** Enabled — opens a plan on every PR that modifies watched files.
- **Apply requirements:** Requires both `approved` (at least one PR approval) and `mergeable` (branch protection checks pass).
- **Parallel plan:** Enabled. **Parallel apply:** Disabled (one environment at a time for safety).
- **Terraform version:** Pinned to `v1.11.4`.

GitHub Actions and Atlantis are complementary: Actions provides the fast-feedback lint/security/plan checks; Atlantis provides the gated apply workflow.

---

## ArgoCD and the app-of-apps pattern

Terraform performs a one-time bootstrap of ArgoCD (Helm chart `argo-cd` v7.8.13) and then deploys a "root Application" via the `argocd-apps` chart. This root Application points at `kubernetes/apps/` in this repo with:

```yaml
syncPolicy:
  automated:
    prune: true      # Remove resources deleted from Git
    selfHeal: true   # Revert manual in-cluster changes
```

To deploy a new workload, add an ArgoCD `Application` manifest to `kubernetes/apps/`. ArgoCD will detect and sync it automatically. The directory structure under `kubernetes/`:

| Directory | Purpose |
|-----------|---------|
| `apps/`   | ArgoCD Application manifests — each one points to a Helm chart or kustomize path |
| `system/` | Cluster-wide infrastructure (cert-manager, ingress-nginx, nvidia-device-plugin, etc.) |
| `bootstrap/` | One-time setup resources that don't fit the ArgoCD lifecycle |

The root app's `repoURL` is set to `https://github.com/YOUR_USER/homelab.git` — update this in `terraform/environments/prod/main.tf` (line ~167).

---

## Getting started

### Prerequisites

1. **Proxmox hosts:** IOMMU enabled on GPU hosts (`intel_iommu=on` or `amd_iommu=on` in kernel params). GPU devices bound to `vfio-pci`.
2. **Proxmox API token:** Create at Datacenter > Permissions > API Tokens. Needs `PVEVMAdmin` + `PVEDatastoreUser` on `/`.
3. **Talos ISO:** Download from the [Talos releases page](https://github.com/siderolabs/talos/releases) and upload to the `local` datastore on each Proxmox host (or a shared datastore).
4. **Tools:** `terraform >= 1.7`, `talosctl`, `kubectl`, `helm`.

### Deploy

```bash
cd terraform/environments/prod

# 1. Create your tfvars (gitignored)
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your Proxmox URL and API token

# 2. Edit variables.tf with your real node specs, IPs, MACs, and PCI IDs

# 3. Initialize and apply
terraform init
terraform plan          # Review the plan
terraform apply         # Creates VMs, bootstraps Talos, installs ArgoCD

# 4. Export configs
terraform output -raw talosconfig > ~/.talos/config
terraform output -raw kubeconfig > ~/.kube/config

# 5. Verify
talosctl health
kubectl get nodes
kubectl get applications -n argocd
```

### Post-deploy checklist

- [ ] Update the ArgoCD root app's `repoURL` to your actual GitHub repo URL
- [ ] Deploy NVIDIA device plugin DaemonSet via ArgoCD (in `kubernetes/system/`)
- [ ] Configure remote Terraform state backend (uncomment in `providers.tf`)
- [ ] Deploy Atlantis (self-hosted or use a managed service) and point it at this repo
- [ ] Set GitHub secrets: `PROXMOX_API_URL`, `PROXMOX_API_TOKEN`
- [ ] Tighten `tfsec` from `soft_fail: true` to blocking once rules are tuned
- [ ] Deploy ingress controller + cert-manager for TLS termination in front of ArgoCD

---

## Conventions and design decisions

- **One module call per node role** (control-plane, worker, GPU) using `for_each` over map variables. Add a node by adding a map entry — no new module blocks needed.
- **Talos config patches are layered**, not monolithic. Base network config is generated inline; GPU-specific config lives in a separate YAML file for readability.
- **ArgoCD bootstrap is intentionally minimal.** Terraform installs ArgoCD once with a `LoadBalancer` service and `server.insecure = true` (expects TLS termination at an ingress). All further ArgoCD configuration should be done via GitOps (ArgoCD managing its own Helm values).
- **State contains secrets.** `talos_machine_secrets` stores cluster PKI in Terraform state. Use encrypted remote state (S3 + KMS, Terraform Cloud, etc.) in production. The `terraform.tfvars` file with Proxmox tokens is gitignored.
- **VM IDs are explicit** (200s for control-plane, 300s for workers, 400s for GPU) to keep the Proxmox UI organized and avoid ID collisions.
- **IP addresses use CIDR notation** (`10.0.10.11/24`) in variables. Modules use `split("/", ip)[0]` to extract the bare IP and `cidrhost(ip, 1)` to derive the gateway where needed.
- **All VMs use `cpu.type = "host"`** — required for GPU passthrough and provides best performance for all nodes.
