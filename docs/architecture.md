# Homelab — GitOps Kubernetes on Proxmox (with a side of Windows gaming)

Fully automated, GitOps-driven Kubernetes cluster running Talos Linux on a 7-node Proxmox hypervisor fleet, with an optional Windows 11 gaming VM that shares the RTX 3080 with the GPU Kubernetes worker. Infrastructure is provisioned with Terraform (orchestrated by Terragrunt) using **S3 + DynamoDB remote state with IAM role assumption**; in-cluster workloads are managed by ArgoCD. No SSH, no manual `kubectl apply` — everything flows through Git.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Git repository (this repo)                   │
│                                                                     │
│  terraform/           Provisions VMs + bootstraps the cluster       │
│  kubernetes/          ArgoCD-managed app manifests + storage PVs    │
│  .github/workflows/   CI: lint, validate, security scan, plan       │
│  atlantis.yaml        PR-driven terragrunt plan/apply               │
└────────────┬──────────────────────────────────┬─────────────────────┘
             │                                  │
       Terragrunt apply                  ArgoCD sync (automated)
   (state: s3://kfir-homelab-tfstate     prune + selfHeal
    locks: DynamoDB, role: assumed)
             │                                  │
             ▼                                  ▼
┌──────────────────────────────────┐   ┌──────────────────────────────┐
│     Proxmox VE 9 cluster         │   │   Kubernetes cluster         │
│                                  │   │   (Talos Linux v1.9.5)       │
│  worker1       ── cp-1      (CP) │   │                              │
│  worker2       ── cp-2      (CP) │   │  ArgoCD ─► apps/             │
│  worker3       ── cp-3      (CP) │   │            system/storage/   │
│  worker4       ── worker-1  (W)  │   │            system/...        │
│  storage1      ── worker-2  (W)  │   │            bootstrap/        │
│                ── (NFS export of │   │                              │
│                   storage1-bulk) │   │  GPU nodes tainted with      │
│  gpunvdgtx1060 ── gpu-1     (G)  │   │  nvidia.com/gpu=NoSchedule   │
│  largegpu      ── gpu-2     (G) ─┼───┤                              │
│                ── largegpu-win11 │   │  NFS PV backed by            │
│                   (Windows)      │   │  192.168.1.106:/mnt/         │
│                ── ⚡ runtime mutex│   │  storage1-bulk               │
│                   (start-time)   │   │                              │
└──────────────────────────────────┘   └──────────────────────────────┘
```

### Physical nodes (Proxmox hosts)

| Proxmox host       | Mgmt IP          | VM name           | VM IP            | Role                                   | Key specs (prod defaults)                  |
|--------------------|------------------|-------------------|------------------|----------------------------------------|--------------------------------------------|
| `worker1`          | `192.168.1.101`  | `cp-1`            | `192.168.1.211`  | Control plane                          | 4 cores, 8 GiB RAM, 50 GB disk             |
| `worker2`          | `192.168.1.102`  | `cp-2`            | `192.168.1.212`  | Control plane                          | 4 cores, 8 GiB RAM, 50 GB disk             |
| `worker3`          | `192.168.1.103`  | `cp-3`            | `192.168.1.213`  | Control plane                          | 4 cores, 8 GiB RAM, 50 GB disk             |
| `worker4`          | `192.168.1.104`  | `worker-1`        | `192.168.1.221`  | Compute worker                         | 8 cores, 16 GiB RAM, 100 GB disk           |
| `storage1`         | `192.168.1.106`  | `worker-2`        | `192.168.1.222`  | Compute worker + NFS server            | 4 cores, 10 GiB RAM, 100 GB disk           |
| `gpunvdgtx1060`    | `192.168.1.105`  | `gpu-1`           | `192.168.1.231`  | GPU worker (GTX 1060)                  | 8 cores, 12 GiB RAM, 100 GB disk, PCIe GPU |
| `largegpu`         | `192.168.1.107`  | `gpu-2`           | `192.168.1.232`  | GPU worker (RTX 3080) — ⚡ runtime mutex | 16 cores, 60 GiB RAM, 159 GB disk, PCIe GPU |
| `largegpu`         | `192.168.1.107`  | `largegpu-win11`  | DHCP             | Windows 11 gaming VM — ⚡ runtime mutex  | 16 cores, 60 GiB RAM, 635 GB disk, PCIe GPU + USB |

The cluster VIP is `192.168.1.210` — control-plane Talos VIP, also the Kubernetes API endpoint.

**IP convention:**

| Range              | Use                                  |
|--------------------|--------------------------------------|
| `192.168.1.101–199` | Physical Proxmox hosts              |
| `192.168.1.200–299` | VMs (CPs 211-213, workers 221-222, GPU workers 231-232) |

**Why VMs live on the home subnet, not an isolated `10.x` block:** every Proxmox host's `vmbr0` is already bridged to the home LAN, so VMs on `192.168.1.x` are L2-reachable from any device on the network with zero routing config. Mac, kubectl, talosctl, and any future Proxmox host you add work out of the box. Make sure your router's DHCP pool excludes the static range you reserve for VMs.

All node specs, IPs, and PCI/USB device IDs are defined as YAML in [`terraform/deployments/<env>/config.yml`](../terraform/deployments/) and consumed via Terragrunt's hierarchical config-merging in [`root.hcl`](../terraform/deployments/root.hcl).

### Network defaults

| Setting          | Default value                   |
|------------------|---------------------------------|
| Cluster VIP      | `192.168.1.210`                 |
| K8s API endpoint | `https://192.168.1.210:6443`    |
| Gateway          | `192.168.1.1` (home router)     |
| Bridge           | `vmbr0`                         |
| DNS              | `1.1.1.1`, `8.8.8.8`            |
| CP node IPs      | `192.168.1.211-213/24`          |
| Worker IPs       | `192.168.1.221-222/24`          |
| GPU node IPs     | `192.168.1.231` (gpu-1), `192.168.1.232` (gpu-2) |

The control-plane VIP is managed by Talos's built-in VIP mechanism — no external load balancer needed. Each control-plane node's network interface includes a `vip` block pointing at the shared VIP.

---

## ⚡ The `largegpu` runtime mutex (Talos GPU worker ↔ Windows VM)

Both `gpu-2` (Talos K8s GPU worker) and `largegpu-win11` (Windows gaming VM) are defined as Terraform-managed VMs on the same Proxmox host (`largegpu`), and **both have the RTX 3080 (`0000:08:00.0`) configured for PCIe passthrough**. Terraform creates both at apply time — there is no config-time mutex.

The exclusivity is enforced at **VM start time** by Proxmox itself: the GPU can only be bound to one running VM. To switch between them:

```bash
# Talos → Windows
qm shutdown 402 && qm start 502

# Windows → Talos
qm shutdown 502 && qm start 402
```

Resource allocation reflects this mutex:

- Both VMs get the **full host CPU and RAM** (16 vCPUs, 60 GiB) — there's only ever one running, so leaving headroom on the idle one would be wasted capacity.
- The largegpu host's 794 GB local NVMe LVM-thin is split ~80/20 — Windows gets `disk_size_gb: 635` for games, gpu-2 gets `disk_size_gb: 159` for the Talos rootfs.
- `on_boot = true` for the Talos GPU worker (auto-start on Proxmox boot); `on_boot = false` for the Windows VM (manual).

### Windows VM lifecycle: install → template → clone

The Windows VM module operates in two modes, selected by `template_vm_id` in `config.yml`:

| Mode      | When                        | What happens                                                                 |
|-----------|-----------------------------|------------------------------------------------------------------------------|
| INSTALL   | `template_vm_id: null`      | Empty VM with scsi0 install disk, EFI vars (Secure Boot pre-enrolled), vTPM 2.0, and the Win11 ISO on ide2. One-time use: install Windows + apps + drivers, shut down, snapshot. |
| CLONE     | `template_vm_id: 9000`      | Clones the named template (a previously-installed-and-snapshotted Windows VM). ~30s. Full clone = independent disk; linked clone (`full_clone: false`) = CoW shared with template. |

`virtio-win.iso` (Fedora's signed driver ISO) must be attached manually on a SATA slot before the first install — `bpg/proxmox` v0.105 only allows one `cdrom` block per VM, and SATA is hot-pluggable (IDE is not).

The intended workflow:

```
INSTALL once  ──►  install apps + games  ──►  shutdown  ──►  clone in UI to VMID 9000
                                                            ──►  convert 9000 to template
                                                            ──►  set template_vm_id: 9000 in config.yml
                                                            ──►  terraform apply → fresh ~30s clone
```

---

## Storage layout

### Proxmox per-host storages

Beyond each host's default `local` (Directory, /var/lib/vz) and `local-lvm` (LVM-thin on the root SSD), additional storages were carved out from previously-unused disks:

| Host         | Storage name        | Type         | Size       | Purpose                                                         |
|--------------|---------------------|--------------|------------|-----------------------------------------------------------------|
| `largegpu`   | `local-lvm`         | LVM-thin     | 794 GB     | Windows VM (~635 GB) + Talos `gpu-2` (~159 GB), 80/20 split     |
| `largegpu`   | `largegpu-hdd`      | Directory    | 1.83 TB    | ISOs, templates, backups (slow HDD, low-churn data)             |
| `gpunvdgtx1060` | `gpu1-extra`     | LVM-thin     | 912 GB     | Spare capacity for additional VMs on the gpu1 host              |
| `storage1`   | `storage1-bulk` (NFS) | NTFS via `ntfs3` driver, NFSv4 export | 9.1 TB | Media/bulk storage for K8s workloads (preserves existing NTFS data) |

### Kubernetes storage

The `storage1-bulk` NFS share is exposed to the cluster as a static `PersistentVolume` + `StorageClass` in [`kubernetes/system/storage/storage1-bulk.yaml`](../kubernetes/system/storage/storage1-bulk.yaml):

- `PV` capacity: 9 Ti, `ReadWriteMany`, `nfs-storage1` storage class, `Retain` reclaim policy.
- Pods bind by creating a PVC against `storageClassName: nfs-storage1` and (optionally) pinning `volumeName: storage1-bulk-pv`.
- Mounted with `nfsvers=4.2,hard` for media workloads (Plex/Jellyfin, *arr stack).

To add a workload, drop the PVC into the consuming app's namespace alongside its ArgoCD `Application`. No dynamic provisioner — PVs are static.

---

## Technology stack

| Layer              | Tool / Version                                  | Purpose                                                  |
|--------------------|-------------------------------------------------|----------------------------------------------------------|
| Hypervisor         | Proxmox VE 9 (Trixie)                           | Runs all VMs across 7 physical hosts                     |
| APT repo           | `pve-no-subscription` (deb822 format)           | Enabled on all 7 nodes; enterprise repo disabled         |
| Node OS (K8s)      | Talos Linux `v1.9.5`                            | Immutable, API-driven Linux — no SSH, no shell           |
| Node OS (gaming)   | Windows 11 25H2 + virtio drivers (0.1.271)      | One VM, GPU-passthrough'd, manual start                  |
| VM provisioning    | Terraform + `bpg/proxmox` ~> 0.105.0            | Creates QEMU VMs with PCIe + USB passthrough             |
| Cluster bootstrap  | Terraform + `siderolabs/talos` ~> 0.7           | Generates machine configs, applies them, bootstraps etcd |
| Stack orchestration| Terragrunt 0.63.0                               | Hierarchical YAML config merging + per-env state isolation |
| Remote state       | S3 `kfir-homelab-tfstate` + DynamoDB `kfir-homelab-terragrunt-state-locks` + IAM role assumption | Server-side encrypted state with cross-account `sts:AssumeRole` plumbed via `AWS_IAM_ROLE` env var |
| GitOps engine      | ArgoCD (Helm chart `argo-cd` v7.8.13)           | Manages all in-cluster workloads from Git                |
| CI                 | GitHub Actions                                  | Runs `terraform fmt`, `validate`, `tfsec`, and `plan` on PRs |
| CD / Terraform PRs | Atlantis                                        | Runs `terragrunt plan` on PR open, `apply` on PR approval |

### Why username/password auth instead of API token

The Proxmox provider uses `root@pam` with the SSH password rather than an API token:

> Proxmox 8.x+ refuses to let API tokens set raw `hostpci` config — even root-realm tokens with `privsep=0` hit `only root can set 'hostpci0' config for non-mapped devices`. Real-user auth bypasses that.

The longer-term cleaner fix is to switch to PCI Resource Mappings (`mapping = "name"` in the `hostpci` block); for now, password auth works for the homelab.

### Why the Talos image URL ends in `.raw.zst`

`bpg/proxmox` v0.105 can only decompress `gz`, `zst`, and `bz2` — **not** `xz`. The image factory serves both; the module pulls `.raw.zst` with `decompression_algorithm = "zst"`.

---

## Repository structure

```
.
├── .env                                  # Proxmox + AWS creds (gitignored)
├── .github/
│   └── workflows/
│       └── terraform-plan.yml            # CI pipeline: lint → security → plan
├── atlantis.yaml                         # Atlantis repo-level config
├── docs/
│   └── architecture.md                   # This file
├── kubernetes/
│   ├── apps/                             # ArgoCD Application manifests (app of apps)
│   ├── system/                           # Cluster-wide infrastructure
│   │   └── storage/
│   │       └── storage1-bulk.yaml        # NFS PV + StorageClass for media
│   └── bootstrap/                        # One-time bootstrap resources
└── terraform/
    ├── deploy.sh                         # Wrapper: loads .env → runs terragrunt
    ├── deployments/                      # Per-environment Terragrunt stacks
    │   ├── root.hcl                      # S3 backend + IAM role + input plumbing
    │   ├── merge_configs.sh              # Hierarchical YAML deep-merge
    │   ├── config.yml                    # Global defaults
    │   ├── prod/
    │   │   ├── config.yml                # Prod: 3 CP + 2 W + 2 GPU + 1 Windows
    │   │   └── homelab-cluster/
    │   │       └── terragrunt.hcl        # Just `include "root"`; module auto-detected
    │   └── staging/
    │       ├── config.yml
    │       └── homelab-cluster/
    │           └── terragrunt.hcl
    └── modules/
        └── stacks/
            └── homelab-cluster/
                ├── main.tf               # VMs → host routing → Talos → ArgoCD
                ├── variables.tf
                ├── providers.tf          # Proxmox (user/pass) + Talos + Helm
                └── modules/
                    ├── proxmox-vm/                  # Talos VMs (CP/worker/GPU)
                    │   └── main.tf
                    ├── proxmox-windows-vm/          # Windows 11 VM (install + clone modes)
                    │   └── main.tf
                    └── talos-cluster/
                        ├── main.tf                  # Secrets, configs, bootstrap, kubeconfig
                        └── talos-gpu-patch.yaml     # NVIDIA extensions + containerd
```

### Key file reference

| File | What it does | When to edit |
|------|-------------|--------------|
| `terraform/deployments/<env>/config.yml` | Per-environment node maps, network, cluster, Windows VM, USB/PCI devices | Adding/removing nodes, changing hardware, mapping USB devices |
| `terraform/deployments/config.yml` | Global defaults shared across all environments | Changing Talos version, default DNS, etc. |
| `terraform/deployments/root.hcl` | Terragrunt root config: S3 backend, `iam_role` from `AWS_IAM_ROLE`, input plumbing | Switching backends, changing retry policy |
| `terraform/deployments/<env>/<stack>/terragrunt.hcl` | One-line `include "root"` — stack name auto-derived from dir | Almost never |
| `terraform/modules/stacks/homelab-cluster/main.tf` | Calls sub-modules: VMs → host routing → Talos → ArgoCD bootstrap + root app | Changing orchestration logic |
| `terraform/modules/stacks/homelab-cluster/providers.tf` | Proxmox (username/password), Talos, Helm provider configs | Auth changes |
| `terraform/modules/stacks/homelab-cluster/modules/proxmox-vm/main.tf` | One Talos VM (CP/worker/GPU) with conditional PCIe passthrough | Changing Talos VM defaults |
| `terraform/modules/stacks/homelab-cluster/modules/proxmox-windows-vm/main.tf` | Windows 11 VM: INSTALL mode (build) or CLONE mode (from template) | Changing Windows VM defaults, drivers |
| `terraform/modules/stacks/homelab-cluster/modules/talos-cluster/main.tf` | Per-role machine configs, applies them, bootstraps etcd | Changing Talos config patches, cluster topology |
| `terraform/modules/stacks/homelab-cluster/modules/talos-cluster/talos-gpu-patch.yaml` | NVIDIA extensions, kernel modules, containerd config | Upgrading GPU driver version |
| `kubernetes/system/storage/storage1-bulk.yaml` | NFS-backed `PersistentVolume` + `StorageClass` for the media share | Resizing, retargeting NFS server |
| `kubernetes/apps/` | ArgoCD watches this directory for Application manifests | Deploying any new workload |
| `.github/workflows/terraform-plan.yml` | CI: format check → validate → plan posted to PR | Changing CI behavior |
| `atlantis.yaml` | Defines which dirs Atlantis watches and apply requirements | Adding environments, changing approval rules |

---

## How Terraform is organized

### Execution flow

`terraform apply` executes a single DAG with explicit `depends_on` ordering:

```
1. proxmox_virtual_environment_download_file.talos_image[*]   (once per Proxmox host)
        │
        ▼
2. module.control_plane_vms  ─┐
   module.worker_vms          │
   module.gpu_vms             ├──► 3. module.talos_cluster ──► 4. helm_release.argocd ──► 5. helm_release.argocd_root_app
   module.windows_vms         │
                              │
   terraform_data.proxmox_subnet_gateway / proxmox_ip_forwarding (SSH-based, when `proxmox_node_ips` is populated)
                             ─┘
```

**Step 1:** The Talos `nocloud-amd64.raw.zst` image is downloaded directly by each Proxmox host from the Talos Image Factory (URL embeds the `talos_schematic_id` and `talos_version`). Decompression is done by the provider with `decompression_algorithm = "zst"`.

**Step 2:** Four parallel module fan-outs:

- `control_plane_vms` / `worker_vms` / `gpu_vms` — Talos VMs via `proxmox-vm`.
- `windows_vms` — Windows VMs via `proxmox-windows-vm` (install or clone mode).
- `terraform_data.proxmox_ip_forwarding` + `proxmox_subnet_gateway` — SSH `remote-exec` into each Proxmox host to enable `net.ipv4.ip_forward` and assign the VM subnet gateway IP to `vmbr0`. Runs only when `proxmox_node_ips` is populated; harmless to leave empty for pure-LAN setups.

**Step 3:** The `talos-cluster` module generates per-node machine configurations using `talos_machine_configuration` data sources. Each node gets a config patch with its hostname, static IP, and routes. GPU nodes additionally receive the `talos-gpu-patch.yaml` (NVIDIA extensions) and labels/taints. The module then applies configs via `talos_machine_configuration_apply`, bootstraps etcd on the first control-plane node, and retrieves the kubeconfig.

**Step 4:** The Helm provider (authenticated via the kubeconfig from step 3) installs ArgoCD into the `argocd` namespace. `wait = false` because at bootstrap time there's no LB controller — `argocd-server`'s external IP stays `<pending>` and `helm --wait` would hang for 10 min before declaring failure. ArgoCD comes up healthy without it.

**Step 5:** A second Helm release deploys the ArgoCD "root Application" using the `argocd-apps` chart, pointing at `kubernetes/apps/` in this repo with automated sync + prune + self-heal. From this point, ArgoCD owns all in-cluster state.

### Module: `proxmox-vm` (Talos VMs)

Creates a single Proxmox QEMU VM for Talos. Key behaviors:

- **Machine type:** Always `q35` (required for PCIe passthrough).
- **BIOS:** `ovmf` (UEFI) for GPU nodes, `seabios` for others. OVMF requires the dynamic `efi_disk` block.
- **CPU type:** Always `host` — required for GPU passthrough.
- **Boot disk:** Cloned from the downloaded Talos `nocloud` image (`file_id = ...talos_image[node].id`, `file_format = "raw"`). `lifecycle.ignore_changes = [disk[0].file_id]` so a Talos upgrade doesn't fight an in-place node's config.
- **Cloud-init `initialization` block:** Static IP via nocloud cidata — without this, Talos comes up in maintenance mode on a DHCP lease and `talos_machine_configuration_apply` can't find the node.
- **PCIe passthrough:** Dynamic `hostpci` block iterating over `var.pci_devices`. Empty list = no passthrough.
- **`on_boot = true`** — Talos VMs auto-start with the host.
- **Tags:** `["talos", "terraform"]`.

### Module: `proxmox-windows-vm`

Creates the Windows 11 gaming VM with two operating modes (see "Windows VM lifecycle" above). Key behaviors:

- **Always `q35` + `ovmf`** — Win11 requires both.
- **Secure Boot:** `efi_disk.pre_enrolled_keys = true` so Microsoft's signing keys are baked into the EFI vars at creation.
- **vTPM 2.0:** Created via `tpm_state` block (Win11 hard requirement).
- **Single `cdrom` block (Windows ISO on `ide2`).** `virtio-win.iso` is attached manually on SATA (provider v0.105 caps `cdrom` at 1 block).
- **`boot_order`:** `["ide2", "scsi0"]` in INSTALL mode (boot installer first); inherited from template in CLONE mode.
- **USB passthrough:** Dynamic `usb` block — accepts either `VID:PID` (replug-safe) or `bus-port` (when two devices share a VID:PID).
- **`on_boot = false`** — never auto-start; user toggles manually with `qm start/shutdown`.
- **No `initialization` block** — Windows ignores nocloud cidata. DHCP is used; static IP would require Autounattend.xml.
- **Tags:** `["windows", "terraform"]`.

### Module: `talos-cluster`

Manages the full Talos lifecycle:

- **`talos_machine_secrets`**: Generates cluster-wide PKI (etcd CA, Kubernetes CA, etc.) — stored in Terraform state (treat state as secret; that's why state is encrypted in S3).
- **`talos_machine_configuration` (data source)**: One per node. Produces the full machine config from cluster name + endpoint + secrets + config patches.
- **Config patches are layered per role:**
  - *All nodes:* hostname, static IP, routes, nameservers.
  - *Control-plane only:* VIP configuration on `eth0`.
  - *GPU workers only:* `talos-gpu-patch.yaml` + node labels (`nvidia.com/gpu.present=true`, `homelab.dev/role=gpu-worker`) + taint (`nvidia.com/gpu=true:NoSchedule`).
- **`talos_machine_configuration_apply`**: Pushes the config to each node over the Talos API.
- **`talos_machine_bootstrap`**: Runs once on the first control-plane node to initialize etcd.
- **`talos_cluster_kubeconfig`**: Retrieves the kubeconfig after bootstrap.

### Node variable schemas

Defined as `map(object)` variables — the map key is the hostname.

```hcl
# control_plane_nodes / worker_nodes:
#   proxmox_node, vm_id, ip_address, cores, memory_mb, disk_size_gb

# gpu_nodes:
#   ... same as above + pci_devices: list({ id, pcie })

# windows_vms:
#   proxmox_node, vm_id, cores, memory_mb, disk_size_gb,
#   windows_iso, virtio_iso,
#   template_vm_id (null → INSTALL, set → CLONE),
#   full_clone (default true),
#   pci_devices: list({ id, pcie }),
#   usb_devices: list({ host, usb3 })
```

The `ip_address` field uses CIDR notation (e.g. `192.168.1.232/24`). The Talos module uses `split("/", ip)` to extract the bare IP where needed.

---

## Remote state (S3 + DynamoDB + IAM role assumption)

State is stored in S3 with DynamoDB-based locking. Terragrunt assumes an IAM role before every AWS call:

```hcl
# terraform/deployments/root.hcl
iam_role = get_env("AWS_IAM_ROLE")   # arn:aws:iam::<acct>:role/TerragruntExecutionRole

remote_state {
  backend = "s3"
  config = {
    bucket         = "kfir-homelab-tfstate"
    key            = "${dirname(local.relative_deployment_path)}/${local.stack}.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "kfir-homelab-terragrunt-state-locks"
  }
}
```

Prerequisites:
1. Local AWS credentials with `sts:AssumeRole` on the target role.
2. The target role's trust policy allows the local principal.
3. The target role has `s3:*` on the state bucket and `dynamodb:*` on the lock table (or scoped equivalents).

Terragrunt auto-creates the bucket and DynamoDB table on first init if they don't exist (use `--terragrunt-non-interactive` to bypass the confirmation prompt).

The same role is used by the Atlantis runner, by GitHub Actions, and locally — no per-workstation credential duplication.

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

**Proxmox side** (handled by `proxmox-vm` / `proxmox-windows-vm`):
- GPU VMs use `bios = "ovmf"` and `machine = "q35"`.
- Each GPU's PCI address (e.g. `01:00` for `gpu-1`'s GTX 1060, `08:00` for `gpu-2`/`largegpu-win11`'s RTX 3080) is passed through via `hostpci` blocks.
- AMD-V (SVM) must be enabled in BIOS on AMD hosts (largegpu, storage1).
- IOMMU enabled on the host kernel cmdline (`intel_iommu=on` or `amd_iommu=on`); GPU bound to `vfio-pci`.

**Talos side** (handled by `talos-gpu-patch.yaml`):
- Installs `nvidia-container-toolkit` and `nvidia-open-gpu-kernel-modules` as Talos system extensions (baked into the OS image, not Kubernetes DaemonSets).
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

Triggers on PRs to `main` that touch `terraform/**`. Runs three jobs against `terraform/deployments/prod/homelab-cluster` via Terragrunt:

1. **Lint & Format** — `terraform fmt -check -recursive` + `terragrunt hclfmt --terragrunt-check`.
2. **Security Scan** — `tfsec` via the `aquasecurity/tfsec-action`. Currently `soft_fail: true` (non-blocking) — tighten once rules are tuned.
3. **Terraform Plan** — runs `terragrunt plan` and posts the output as a PR comment inside a collapsible `<details>` block. Requires GitHub secrets `PROXMOX_APITOKEN_ID`, `PROXMOX_APITOKEN_SECRET`, `PROXMOX_SSH_PASSWORD` (and AWS creds with `sts:AssumeRole` on the Terragrunt role).

The plan job depends on lint + security passing first.

### Atlantis (`atlantis.yaml`)

Atlantis provides the PR-driven plan/apply workflow:

- **Watches:** `terraform/deployments/prod/homelab-cluster/` and all `.tf`/`.yaml` files under `terraform/modules/stacks/`.
- **Auto-plan:** Enabled — opens a plan on every PR that modifies watched files.
- **Apply requirements:** `approved` + `mergeable`.
- **Parallel plan:** Enabled. **Parallel apply:** Disabled (one environment at a time).
- **Workflow:** Custom `terragrunt` workflow that runs `terragrunt plan -out=$PLANFILE` and `terragrunt apply $PLANFILE`.

GitHub Actions and Atlantis are complementary: Actions provides the fast-feedback lint/security/plan checks; Atlantis provides the gated apply workflow.

---

## ArgoCD and the app-of-apps pattern

Terraform performs a one-time bootstrap of ArgoCD (Helm chart `argo-cd` v7.8.13) and then deploys a "root Application" via the `argocd-apps` chart. This root Application points at `kubernetes/apps/` with:

```yaml
syncPolicy:
  automated:
    prune: true      # Remove resources deleted from Git
    selfHeal: true   # Revert manual in-cluster changes
```

To deploy a new workload, add an ArgoCD `Application` manifest to `kubernetes/apps/`. ArgoCD will detect and sync it automatically.

| Directory                    | Purpose |
|------------------------------|---------|
| `kubernetes/apps/`           | ArgoCD Application manifests — each one points to a Helm chart or kustomize path |
| `kubernetes/system/`         | Cluster-wide infrastructure (cert-manager, ingress-nginx, nvidia-device-plugin, etc.) |
| `kubernetes/system/storage/` | NFS-backed PVs + StorageClasses (e.g. `storage1-bulk.yaml`) |
| `kubernetes/bootstrap/`      | One-time setup resources that don't fit the ArgoCD lifecycle |

The root app's `repoURL` is set in [`terraform/deployments/config.yml`](../terraform/deployments/config.yml) (`argocd_repo_url`). Per-environment `argocd_target_revision` lives in each env's `config.yml`.

---

## Getting started

### Prerequisites

1. **Proxmox hosts:** IOMMU enabled on GPU hosts (`intel_iommu=on` or `amd_iommu=on`). GPU devices bound to `vfio-pci`. AMD-V (SVM) enabled in BIOS on AMD hosts. `pve-no-subscription` repo enabled (enterprise repo disabled).
2. **Proxmox root password** — used for both API auth (via `root@pam`) and SSH `remote-exec` for host routing.
3. **AWS account** with an IAM role (`TerragruntExecutionRole`) that has `s3:*` on the state bucket and `dynamodb:*` on the lock table. Local AWS creds need `sts:AssumeRole` on that role.
4. **Tools:** `terraform >= 1.7`, `terragrunt 0.63.0`, `talosctl`, `kubectl`, `helm`.
5. **For Windows VM install:** `Win11_25H2_*.iso` + `virtio-win.iso` (v0.1.271) uploaded to the largegpu host's `local` ISO datastore.

### Deploy

```bash
# 1. Populate .env at the repo root:
#      PROXMOX_API_URL=https://192.168.1.101:8006
#      PROXMOX_SSH_PASSWORD="..."
#      AWS_IAM_ROLE=arn:aws:iam::<acct>:role/TerragruntExecutionRole
#      AWS_ACCESS_KEY_ID=...
#      AWS_SECRET_ACCESS_KEY=...

# 2. Edit terraform/deployments/prod/config.yml — node specs, IPs, USB/PCI devices.

# 3. Plan + apply via the wrapper script.
./terraform/deploy.sh prod homelab-cluster plan
./terraform/deploy.sh prod homelab-cluster apply

# 4. Export configs (filter out merge_configs.sh log noise with sed)
cd terraform/deployments/prod/homelab-cluster
terragrunt output -raw talosconfig | sed -n '/^context:/,$p' > ~/.talos/config
terragrunt output -raw kubeconfig  | sed -n '/^apiVersion/,$p' > ~/.kube/config

# 5. Verify
talosctl --talosconfig ~/.talos/config health
kubectl get nodes
kubectl get applications -n argocd
```

### Switching between Talos GPU worker and Windows VM

Both VMs are always present in state — toggle which is *running* via Proxmox:

```bash
# Run Windows for gaming
ssh root@192.168.1.107 'qm shutdown 402 && qm start 502'

# Back to Talos K8s GPU worker
ssh root@192.168.1.107 'qm shutdown 502 && qm start 402'
```

### Post-deploy checklist

- [ ] Deploy NVIDIA device plugin DaemonSet via ArgoCD (in `kubernetes/system/`)
- [ ] Deploy ingress controller + cert-manager for TLS termination
- [ ] Deploy media stack PVC(s) bound to `storage1-bulk-pv`
- [ ] Convert the live Windows VM to a template once apps + games are installed; set `template_vm_id` in `config.yml`
- [ ] Tighten `tfsec` from `soft_fail: true` to blocking once rules are tuned
- [ ] Switch from `root@pam` password auth to PCI Resource Mappings to allow API tokens back

---

## Conventions and design decisions

- **One module call per node role** (control-plane, worker, GPU, Windows) using `for_each` over map variables. Add a node by adding a map entry — no new module blocks needed.
- **Talos config patches are layered**, not monolithic. Base network config is generated inline; GPU-specific config lives in a separate YAML file for readability.
- **ArgoCD bootstrap is intentionally minimal.** Terraform installs ArgoCD once with a `LoadBalancer` service and `server.insecure = true` (expects TLS termination at an ingress). `wait = false` because no LB controller exists at bootstrap. All further ArgoCD configuration goes through GitOps.
- **State contains secrets.** `talos_machine_secrets` stores cluster PKI in Terraform state — that's why the S3 bucket has SSE enabled and the role policy is tightly scoped.
- **VM IDs are explicit.** 200s = control-plane, 300s = workers, 400s = GPU, 500s = Windows, 9000s = templates. Keeps the Proxmox UI organized and avoids collisions.
- **IP addresses use CIDR notation** (`192.168.1.232/24`) in variables. Modules use `split("/", ip)[0]` to extract the bare IP and parse the prefix for routing.
- **All VMs use `cpu.type = "host"`** — required for GPU passthrough, best performance everywhere else.
- **The `largegpu` mutex is enforced at runtime, not config time.** Two VMs sharing one GPU = one runs, the other can't start. This lets you flip between them in seconds with no Terraform churn.
- **Bulk media storage is NTFS+NFS, not Ceph/Longhorn.** The 9 TB drive on storage1 had existing NTFS data worth preserving. Exporting it via the kernel `ntfs3` driver + NFSv4 was simpler than converting (which would require a full copy off + back).
