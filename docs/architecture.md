# Homelab — GitOps Kubernetes on Proxmox (with a side of Windows gaming)

GitOps-driven Kubernetes architecture designed to run Talos Linux on a 3-node Proxmox hypervisor fleet, with an optional Windows 11 gaming VM that shares the RTX 3080 with the GPU Kubernetes worker. Physical host configuration is applied over SSH by Ansible; infrastructure is provisioned with Terraform (orchestrated by Terragrunt) using **S3 + DynamoDB remote state with IAM role assumption**; in-cluster workloads are managed by ArgoCD. Host and cluster changes are declared in Git rather than maintained as undocumented commands.

> **Capacity transition (reviewed 2026-07-22):** the former `worker1`–`worker4` laptop hosts were returned and are no longer part of the homelab. Production is now configured with one `2 vCPU / 4 GiB` control plane and three GPU workers; the RTX 2060 VM also carries ordinary workloads. This is intentionally not control-plane HA; return to three `2 vCPU / 4 GiB` control planes only when each physical host has enough memory headroom. See [Capacity decision and target topology](#capacity-decision-and-target-topology).

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Git repository (this repo)                   │
│                                                                     │
│  ansible/             Configures physical Proxmox hosts             │
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
│     (3 physical hosts)           │   │   (Talos Linux v1.9.5)       │
│                                  │   │                              │
│  smallgpu      ── 10 TB bulk disk│   │  Start: 1 small control plane│
│                ── RTX 2060       │   │  and right-sized workers     │
│                                  │   │                              │
│  gpunvdgtx1060 ── 800 GB critical│   │  HA later: 3 control planes  │
│                   NFS            │   │  at 2 vCPU / 4 GiB each      │
│                ── VM 100 personal│   │                              │
│                   workstation    │   │  GPU nodes are tainted       │
│                ── GTX 1060       │   │  nvidia.com/gpu=NoSchedule   │
│                                  │   │  NFS PVs (static, RWX):      │
│  largegpu      ── RTX 3080       │   │   bulk     → smallgpu        │
│                ── Talos GPU VM / │   │              /mnt/data10tb   │
│                   Windows VM     │   │   critical → gpunvdgtx1060   │
│                ── ⚡ runtime mutex│   │              /mnt/storage2…  │
│                   (start-time)   │   │                              │
└──────────────────────────────────┘   └──────────────────────────────┘
```

### Physical nodes (Proxmox hosts)

| Proxmox host    | Mgmt IP         | CPU                             | Installed RAM              | GPU                          | Primary role |
|-----------------|-----------------|---------------------------------|----------------------------|------------------------------|--------------|
| `gpunvdgtx1060` | `192.168.1.105` | Intel Core i7-8750H, 6c/12t     | 15.46 GiB (2×8 GB DDR4-2667) | GeForce GTX 1060 Mobile     | Old gaming laptop: personal workstation VM, critical NFS; K8s only after capacity is freed |
| `smallgpu`      | `192.168.1.106` | AMD Ryzen 5 3600, 6c/12t        | 15.55 GiB (1×16 GB DDR4-2133) | GeForce RTX 2060            | Compute/GPU capacity and 10 TB bulk NFS |
| `largegpu`      | `192.168.1.107` | AMD Ryzen 7 5800X, 8c/16t       | 62.70 GiB (2×32 GB DDR4-2400) | GeForce RTX 3080 LHR        | GPU compute / Windows gaming runtime mutex |

| Proxmox host    | Fast/system disk                         | Additional disk                         | Motherboard                       | Virtualization |
|-----------------|------------------------------------------|-----------------------------------------|-----------------------------------|----------------|
| `gpunvdgtx1060` | 238.5 GB Intel SSDPEKKW256G8 NVMe        | 931.5 GB Samsung SSD 860 EVO SATA SSD   | CFL Sienta_CFS                    | Intel VT-x     |
| `smallgpu`      | 476.9 GB XPG SPECTRIX S40G NVMe          | 9.1 TB Toshiba MG06ACA10TE SATA HDD     | ASUS PRIME B450M-A                | AMD-V          |
| `largegpu`      | 931.5 GB Samsung SSD 980 NVMe             | 1.8 TB WD20EZBX SATA HDD                | ASUS TUF GAMING X570-PLUS         | AMD-V          |

The remaining fleet has 20 physical CPU cores / 40 threads and 93.71 GiB of installed RAM. These are host totals, not safe VM allocations. Capacity is also unevenly distributed: 62.70 GiB is in `largegpu`, while each of the other two hosts has only about 15.5 GiB. RAM and failure-domain placement, rather than aggregate CPU, are the limiting factors.

The reserved cluster VIP is `192.168.1.210` — the Talos control-plane VIP and Kubernetes API endpoint once the replacement control-plane topology is deployed.

### Live capacity snapshot (2026-07-22)

This snapshot was read from the Proxmox API at `192.168.1.105`; it is operational evidence, not Terraform desired state.

| Host | Live state | Existing VM allocation | Capacity consequence |
|------|------------|------------------------|----------------------|
| `gpunvdgtx1060` | Online; 15.46 GiB RAM | VM `100`, the personal workstation: running, 6 vCPU, fixed 10 GiB RAM, 100 GiB disk | A 3 GiB GPU worker requires workstation ballooning (8–10 GiB) or a small fixed reduction |
| `largegpu` | Online; 62.70 GiB RAM | Windows VMs `502` and `101`: stopped; each configured for 16 vCPU, 60 GiB RAM, 635 GiB disk and RTX 3080 passthrough | Main available compute while Windows is stopped; starting either Windows VM consumes essentially the whole host |
| `smallgpu` | Online; 15.55 GiB RAM, 13.53 GiB free | No VMs at review time | Hosts the `2 vCPU / 4 GiB` control plane and mixed-role `10 vCPU / 8 GiB` RTX 2060 worker |

VM `100` is not merely over-provisioned on paper. Proxmox history showed a 9.49 GiB guest-memory peak and a 14.63 GiB whole-host memory peak during the preceding week; at the follow-up review it was using 9.8 GiB and the host had already moved 0.61 GiB into swap. Inside the guest, however, applications used about 6.6 GiB with 2.6 GiB available/reclaimable, which makes an 8–10 GiB balloon range preferable to a fixed 8 GiB reduction.

The critical NFS server is confirmed to run directly on Proxmox: `nfs-kernel-server` is active and enabled, `/mnt/storage2-bulk` is an ext4 mount backed by `/dev/mapper/gpu1--extra-storage2--bulk`, and it is exported read/write to `192.168.1.0/24` with NFSv4 `fsid=10`. TCP 111 and 2049 are listening. A client mount from Talos still needs verification after deployment.

The live state changed during the 2026-07-22 review: `smallgpu` now has the UUID-based `ntfs3` fstab entry, `/mnt/data10tb` is mounted read/write without a `force` option, and the intended `fsid=1` export is active. Earlier kernel logs recorded a dirty-volume refusal, so Ansible still treats an unmounted dirty or hibernated NTFS filesystem as a hard failure and never clears the flag or force-mounts it. The export still needs an end-to-end mount test from Talos before workloads depend on it.

GPU host readiness differs by machine. The laptop GTX 1060 is already bound to `vfio-pci` and exposes `/dev/vfio/2`. The RTX 2060 is isolated in IOMMU group 18, but its GPU, audio, USB, and UCSI functions are still bound to host drivers. Before `gpu-3` can start, stage PCI IDs `10de:1e89`, `10de:10f8`, `10de:1ad8`, and `10de:1ad9` with the Ansible `vfio_passthrough` role, then explicitly approve a reboot of `smallgpu`. The RTX 3080 functions are `10de:2216` and `10de:1aef` in group 21; VMs `101` and `502` reference it and were both stopped during the read-only check.

### Configuration ownership boundary

Each layer has a deliberately non-overlapping owner:

- **Ansible owns physical Proxmox host configuration after installation and cluster joining:** APT repositories, role-specific packages, filesystem mounts, NFS exports/services, VFIO bindings, module/blacklist files, boot parameters, initramfs updates, and host preflight/verification. It never creates or changes corosync membership, formats disks, clears filesystem flags, detaches live GPUs, or reboots from the normal configuration play.
- **Terraform/Terragrunt owns virtual infrastructure and Talos bootstrap:** VM definitions and placement, PCI attachment to VMs, Talos machine configuration, cluster bootstrap, and initial ArgoCD/application bootstrap.
- **ArgoCD owns in-cluster resources:** applications, system controllers, and static Kubernetes PV/StorageClass declarations. Kubernetes manifests do not configure their physical NFS servers.

Proxmox installation and joining a node to `HomeLab-Cluster` remain explicit prerequisites. Once joined, a node is added to [`ansible/inventory/production/`](../ansible/inventory/production/) and converged with [`configure-proxmox.yml`](../ansible/playbooks/configure-proxmox.yml). No resource should be declared in both Ansible and Terraform.

### Capacity decision and target topology

**Yes, downsize the control plane.** Three control-plane VMs at 4 vCPU / 8 GiB each would reserve 24 GiB for a small homelab control plane and no longer fit the actual failure domains. Talos v1.9 lists 2 vCPU / 2 GiB as the control-plane minimum and 4 vCPU / 4 GiB as recommended; the balanced target here is **2 vCPU / 4 GiB, 40–50 GiB disk per control-plane VM**. See the [Talos v1.9 system requirements](https://docs.siderolabs.com/talos/v1.9/getting-started/system-requirements).

The staged design is:

| Stage | Control plane | Workers | Availability trade-off |
|-------|---------------|---------|------------------------|
| Constrained / configured | One `2 vCPU / 4 GiB` VM on `smallgpu` | GPU workers: GTX 1060 `2 vCPU / 3 GiB`, mixed-role RTX 2060 `10 vCPU / 8 GiB`, RTX 3080 `8 vCPU / 32 GiB` | Not control-plane HA. GTX 1060/RTX 3080 nodes are tainted; RTX 2060 accepts ordinary pods. The GTX 1060 depends on workstation ballooning; RTX 3080 stops for Windows |
| Three-host steady state | Three `2 vCPU / 4 GiB` VMs, exactly one per physical host | Right-size workers independently after observing real usage | Survives one control-plane VM or physical-host failure, but only after `gpunvdgtx1060` has additional RAM or its workstation allocation is materially reduced |

The configured `2 vCPU / 3 GiB` GTX 1060 worker on `gpunvdgtx1060` exists only to expose that PCI GPU to Kubernetes; Proxmox itself serves NFS. Like every GPU worker, it has `nvidia.com/gpu=true:NoSchedule`, so ordinary pods cannot consume its tight memory budget. Before starting it, configure workstation VM `100` with a 10 GiB maximum and 8 GiB balloon minimum (`qm set 100 --memory 10240 --balloon 8192`), or reduce the workstation to 9 GiB fixed. The balloon option preserves the workstation's ability to grow when RAM is available.

The longer-term ways to improve this are:

1. Upgrade the laptop to 32 GiB RAM, then use roughly `4 vCPU / 8 GiB` for the worker while retaining the 10 GiB workstation and at least 4 GiB for Proxmox/NFS.
2. Use the configured 8–10 GiB balloon range; guest-level measurements support reclaiming cache, but not a permanent reduction much below 9 GiB.
3. Move the workstation unchanged to `largegpu`, accepting that an owned workstation becomes dependent on borrowed hardware and competes with Windows/GPU runtime capacity.
4. Make the workstation and GTX 1060 worker mutually exclusive at runtime. This saves RAM but makes that GPU unavailable whenever the workstation is running.

A worker does not need to be on the NFS server to use the important storage. Any worker on `192.168.1.0/24` can mount `192.168.1.105:/mnt/storage2-bulk`. The laptop VM is present because PCI GPU access must be local to a Kubernetes node, not because NFS requires colocation.

### Retired Terraform topology

The old VM placement is intentionally not reproduced here because it depended on the returned laptops:

- `cp-1`, `cp-2`, and `cp-3` were hosted by `worker1`, `worker2`, and `worker3`.
- `worker-1`, `worker-3`, `worker-4`, and `worker-5` were hosted by `worker4`, `worker1`, `worker2`, and `worker3`.
- `smallgpu` is the new Proxmox hostname for the host formerly named `node6`; current Terraform and Ansible inventory use `smallgpu`.
- `terraform/deployments/prod/config.yml` has been remapped to the remaining hosts; the names and placements above are historical context only.

### Node ownership and permanence

Only **`gpunvdgtx1060`** (the GTX 1060 host) is permanently owned hardware. `smallgpu` and `largegpu` are borrowed from a friend who may eventually ask for them back. The four employer-owned laptops (`worker1`–`worker4`) have been returned and are retired from this architecture.

Practical consequences that the rest of this document depends on:

- **Critical / personal data stays on `gpunvdgtx1060`.** This is the only host where state can't simply disappear. Personal cloud (Immich), config snapshots, and anything irreplaceable should bind against the **critical tier** (`storage2-bulk-pv`, see [Kubernetes storage](#kubernetes-storage) below).
- **Bulk / non-critical / reproducible data goes on the borrowed hosts.** Media libraries (Plex/Jellyfin, *arr stack), large model caches, and anything that can be re-downloaded land on the **bulk tier** (`storage1-bulk-pv` on `smallgpu`, 10 TB).
- **`smallgpu` is the "big storage server, non-critical" role.** This is the physical host previously named `node6` (and before that `storage1`) at `192.168.1.106`. The Kubernetes PV/StorageClass names (`storage1-bulk-pv`, `nfs-storage1`) remain unchanged so existing bindings continue to resolve.
- **Failure tolerance must be revisited with the VM redesign.** The smaller three-host fleet has much less spare compute, and two hosts are borrowed. Critical pods may access `gpunvdgtx1060` storage from any LAN-connected worker; they should run locally only after the laptop has enough RAM. Storage placement and workload placement protect against different failures and are not substitutes for backups.

**IP convention:**

| Range              | Use                                  |
|--------------------|--------------------------------------|
| `192.168.1.101–199` | Physical Proxmox hosts              |
| `192.168.1.200–299` | VMs (role-specific static address pools) |

**Why VMs live on the home subnet, not an isolated `10.x` block:** every Proxmox host's `vmbr0` is already bridged to the home LAN, so VMs on `192.168.1.x` are L2-reachable from any device on the network with zero routing config. Mac, kubectl, talosctl, and any future Proxmox host you add work out of the box. Make sure your router's DHCP pool excludes the static range you reserve for VMs.

VM specs, IPs, and PCI/USB device IDs are defined as YAML in [`terraform/deployments/<env>/config.yml`](../terraform/deployments/) and consumed via Terragrunt's hierarchical config-merging in [`root.hcl`](../terraform/deployments/root.hcl). The production YAML now implements the constrained topology described above.

### Network defaults

| Setting          | Default value                   |
|------------------|---------------------------------|
| Cluster VIP      | `192.168.1.210`                 |
| K8s API endpoint | `https://192.168.1.210:6443`    |
| Gateway          | `192.168.1.1` (home router)     |
| Bridge           | `vmbr0`                         |
| DNS              | `1.1.1.1`, `8.8.8.8`            |
| CP node IPs      | `cp-1`: `192.168.1.211/24` |
| Worker IPs       | No separate general worker; `gpu-3` carries ordinary workloads |
| GPU node IPs     | `gpu-1`–`gpu-3`: `192.168.1.231-233/24` |

The control-plane VIP is managed by Talos's built-in VIP mechanism — no external load balancer is needed. The replacement control-plane nodes will each need a network-interface `vip` block pointing at the shared VIP.

---

## ⚡ The `largegpu` runtime mutex (Talos GPU worker ↔ Windows VM)

Both `gpu-2` (Talos K8s GPU worker) and `largegpu-win11` (Windows gaming VM) are defined in Terraform for the same Proxmox host (`largegpu`), and **both have the RTX 3080 (`0000:08:00.0`) configured for PCIe passthrough**. Terraform would create both at apply time — there is no config-time mutex. In the 2026-07-22 live snapshot, no Talos `gpu-2` VM existed; Windows VM `502` and its copy `101` existed but were stopped.

The exclusivity is enforced at **VM start time** by Proxmox itself: the GPU can only be bound to one running VM. To switch between them:

```bash
# Talos → Windows
qm shutdown 402 && qm start 502

# Windows → Talos
qm shutdown 502 && qm start 402
```

The configured runtime allocation is:

- `gpu-2` requests 8 vCPUs and 32 GiB RAM; the Windows VM requests 16 vCPUs and 60 GiB RAM. They remain mutually exclusive because they share the RTX 3080, so the Windows allocation can still use essentially the whole host when `gpu-2` is stopped.
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
| `largegpu`      | `local-lvm`           | LVM-thin     | 794 GB     | Windows VM (~635 GB) + Talos `gpu-2` (~159 GB), 80/20 split             |
| `largegpu`      | `largegpu-hdd`        | Directory    | 1.83 TB    | ISOs, templates, backups (slow HDD, low-churn data)                    |
| `gpunvdgtx1060` | `gpu1-extra`          | LVM-thin     | 912 GB     | Spare capacity for additional VMs + carved LV for `storage2-bulk` NFS  |
| `gpunvdgtx1060` | `storage2-bulk` (NFS) | ext4 LV on `gpu1-extra`, NFSv4 export | 800 GB | **Critical tier** — Immich, personal data (only permanent host)        |
| `smallgpu`      | `storage1-bulk` (NFS) | 10 TB NTFS via kernel `ntfs3`, NFSv4 export | 10 TB  | **Bulk tier** — active on the host; Talos client verification pending |

### Kubernetes storage

Two static NFS-backed `PersistentVolume`s are declared, one per tier (see [Node ownership and permanence](#node-ownership-and-permanence) for why two tiers exist). Both host exports are live; each still requires an end-to-end mount test from a Talos worker:

| PV name             | StorageClass     | Backed by                                       | Size   | Tier — use case                                                                            |
|---------------------|------------------|-------------------------------------------------|--------|--------------------------------------------------------------------------------------------|
| `storage1-bulk-pv`  | `nfs-storage1`   | `smallgpu:/mnt/data10tb` (NTFS via `ntfs3`)        | 9 Ti   | **Bulk** — media (Plex/Jellyfin, *arr), model caches, anything reproducible                |
| `storage2-bulk-pv`  | `nfs-storage2`   | `gpunvdgtx1060:/mnt/storage2-bulk` (ext4 on LVM-thin) | 800 Gi | **Critical** — Immich, config snapshots, personal data, anything that must survive a host loss |

Both PVs are `ReadWriteMany`, mounted with `nfsvers=4.2,hard`, and use `Retain` reclaim policy. Manifests live in [`kubernetes/system/storage/`](../kubernetes/system/storage/) (`storage1-bulk.yaml`, `storage2-bulk.yaml`). Physical mounts, exports, and `nfs-kernel-server` are owned by the Ansible `nfs_server` role, not by Kubernetes manifests.

To consume one: create a PVC in the app's namespace with the matching `storageClassName` and pin `volumeName` to the PV name. No dynamic provisioner — PVs are static, so a typo in `storageClassName` will just leave the PVC `Pending` forever rather than silently provisioning somewhere wrong.

> **Picking a tier:** if losing the data is merely inconvenient (re-download / re-rip), use `storage1-bulk-pv`. If losing it is unrecoverable (personal photos, config you don't have a backup of, etc.), use `storage2-bulk-pv`. When in doubt, critical tier — 800 GB on the permanent host is the scarce resource, but it's the one that survives a borrowed-machine return.

---

## Technology stack

| Layer              | Tool / Version                                  | Purpose                                                  |
|--------------------|-------------------------------------------------|----------------------------------------------------------|
| Hypervisor         | Proxmox VE 9 (Trixie)                           | VM capacity across 3 physical hosts                      |
| APT repo           | `pve-no-subscription` (deb822 format)           | Enabled on all 3 nodes; enterprise repo disabled         |
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
├── ansible/                              # Physical Proxmox host configuration
│   ├── inventory/production/             # Hosts, group vars, hardware-specific host vars
│   ├── playbooks/                        # Configure, verify, and explicit reboot entry points
│   └── roles/                            # Repositories, packages, NFS, VFIO, preflight
├── docs/
│   └── architecture.md                   # This file
├── kubernetes/
│   ├── apps/                             # ArgoCD Application manifests (app of apps)
│   ├── system/                           # Cluster-wide infrastructure
│   │   └── storage/
│   │       ├── storage1-bulk.yaml        # Bulk tier: NFS PV + SC, 10 TB NTFS on smallgpu
│   │       └── storage2-bulk.yaml        # Critical tier: NFS PV + SC, 800 GB ext4 on gpunvdgtx1060
│   └── bootstrap/                        # One-time bootstrap resources
└── terraform/
    ├── deploy.sh                         # Wrapper: loads .env → runs terragrunt
    ├── deployments/                      # Per-environment Terragrunt stacks
    │   ├── root.hcl                      # S3 backend + IAM role + input plumbing
    │   ├── merge_configs.sh              # Hierarchical YAML deep-merge
    │   ├── config.yml                    # Global defaults
    │   ├── prod/
    │   │   ├── config.yml                # Prod VM topology for the current 3-host fleet
    │   │   └── homelab-cluster/
    │   │       └── terragrunt.hcl        # Just `include "root"`; module auto-detected
    │   └── staging/
    │       ├── config.yml
    │       └── homelab-cluster/
    │           └── terragrunt.hcl
    └── modules/
        └── stacks/
            └── homelab-cluster/
                ├── main.tf               # VMs → Talos → ArgoCD
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
| `ansible/inventory/production/` | Physical hosts, NFS mounts/exports, PCI IDs and IOMMU groups | Adding or changing a joined Proxmox host |
| `ansible/playbooks/configure-proxmox.yml` | Serial, idempotent physical-host convergence | Repositories, packages, NFS, or VFIO host policy |
| `terraform/deployments/<env>/config.yml` | Per-environment node maps, network, cluster, Windows VM, USB/PCI devices | Adding/removing nodes, changing hardware, mapping USB devices |
| `terraform/deployments/config.yml` | Global defaults shared across all environments | Changing Talos version, default DNS, etc. |
| `terraform/deployments/root.hcl` | Terragrunt root config: S3 backend, `iam_role` from `AWS_IAM_ROLE`, input plumbing | Switching backends, changing retry policy |
| `terraform/deployments/<env>/<stack>/terragrunt.hcl` | One-line `include "root"` — stack name auto-derived from dir | Almost never |
| `terraform/modules/stacks/homelab-cluster/main.tf` | Calls sub-modules: VMs → Talos → ArgoCD bootstrap + root app | Changing orchestration logic |
| `terraform/modules/stacks/homelab-cluster/providers.tf` | Proxmox (username/password), Talos, Helm provider configs | Auth changes |
| `terraform/modules/stacks/homelab-cluster/modules/proxmox-vm/main.tf` | One Talos VM (CP/worker/GPU) with conditional PCIe passthrough | Changing Talos VM defaults |
| `terraform/modules/stacks/homelab-cluster/modules/proxmox-windows-vm/main.tf` | Windows 11 VM: INSTALL mode (build) or CLONE mode (from template) | Changing Windows VM defaults, drivers |
| `terraform/modules/stacks/homelab-cluster/modules/talos-cluster/main.tf` | Per-role machine configs, applies them, bootstraps etcd | Changing Talos config patches, cluster topology |
| `terraform/modules/stacks/homelab-cluster/modules/talos-cluster/talos-gpu-patch.yaml` | NVIDIA extensions, kernel modules, containerd config | Upgrading GPU driver version |
| `kubernetes/system/storage/storage1-bulk.yaml` | NFS-backed `PV` + `StorageClass` — bulk tier (10 TB NTFS on smallgpu) | Resizing, retargeting NFS server, host-side export setup |
| `kubernetes/system/storage/storage2-bulk.yaml` | NFS-backed `PV` + `StorageClass` — critical tier (800 GB ext4 on gpunvdgtx1060) | Resizing the carved LV, host-side export setup |
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
                             ─┘
```

**Step 1:** The Talos `nocloud-amd64.raw.zst` image is downloaded directly by each Proxmox host from the Talos Image Factory (URL embeds the `talos_schematic_id` and `talos_version`). Decompression is done by the provider with `decompression_algorithm = "zst"`.

**Step 2:** Four parallel module fan-outs:

- `control_plane_vms` / `worker_vms` / `gpu_vms` — Talos VMs via `proxmox-vm`.
- `windows_vms` — Windows VMs via `proxmox-windows-vm` (install or clone mode).

Physical host networking is intentionally absent from the Terraform DAG. Production guests use the existing LAN gateway; any future isolated-subnet forwarding or bridge address belongs in Ansible inventory/roles before its VMs are planned.

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

**Proxmox side:** Ansible owns the physical host's IOMMU parameters, VFIO
modules, PCI ID binding, driver blacklists, initramfs update, and verification.
Terraform's `proxmox-vm` / `proxmox-windows-vm` modules only attach the prepared
PCI devices to guests:
- GPU VMs use `bios = "ovmf"` and `machine = "q35"`.
- Each GPU's PCI address (`01:00` for `gpu-1`'s GTX 1060, `08:00` for `gpu-2`/Windows's RTX 3080, and `09:00` for `gpu-3`'s RTX 2060) is passed through via `hostpci` blocks.
- AMD-V (SVM) must be enabled in BIOS on AMD hosts (largegpu, smallgpu).
- Ansible stages `intel_iommu=on` or `amd_iommu=on`, verifies the complete IOMMU group, and binds the declared GPU functions to `vfio-pci` after an explicitly approved reboot.

**Talos side** (handled by `talos-gpu-patch.yaml`):
- Installs `nvidia-container-toolkit` and `nvidia-open-gpu-kernel-modules` as Talos system extensions (baked into the OS image, not Kubernetes DaemonSets).
- Loads four kernel modules at boot: `nvidia`, `nvidia_uvm`, `nvidia_drm`, `nvidia_modeset`.
- Writes a containerd config snippet to `/etc/cri/conf.d/20-customization.part` that registers the `nvidia` runtime as the default container runtime on GPU nodes.
- Sets `vm.nr_hugepages = 1024` for large-memory GPU workloads.

**Kubernetes side** (handled inline in `talos-cluster/main.tf`):
- GPU nodes get labels: `nvidia.com/gpu.present=true`, `homelab.dev/role=gpu-worker`, `homelab.dev/gpu-node=<hostname>`.
- Dedicated GPU nodes get taint `nvidia.com/gpu=true:NoSchedule`; mixed-role `gpu-3` deliberately omits it so ordinary pods have a worker.
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

1. **Proxmox hosts:** Proxmox VE 9 installed, each host already joined to `HomeLab-Cluster`, and AMD-V/SVM or Intel virtualization enabled in firmware. Run the [Ansible host configuration](../ansible/README.md) to manage repositories, NFS, IOMMU/VFIO files, and verification before Terraform.
2. **Proxmox root password** — used by the Proxmox provider for `root@pam` API authentication and provider-managed SSH operations; Terraform has no host-configuration `remote-exec` resources.
3. **AWS account** with an IAM role (`TerragruntExecutionRole`) that has `s3:*` on the state bucket and `dynamodb:*` on the lock table. Local AWS creds need `sts:AssumeRole` on that role.
4. **Tools:** Ansible Core 2.16+, `terraform >= 1.7`, `terragrunt 0.63.0`, `talosctl`, `kubectl`, `helm`.
5. **For Windows VM install:** `Win11_25H2_*.iso` + `virtio-win.iso` (v0.1.271) uploaded to the largegpu host's `local` ISO datastore.

### Deploy

```bash
# 1. Populate .env at the repo root:
#      PROXMOX_API_URL=https://192.168.1.105:8006
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

- [ ] **Reconcile retired-host Terraform state before apply.** The 2026-07-22 `-refresh=false` plan reports `4 add, 9 change, 20 destroy`, including VMs and Talos images recorded on returned `worker1`–`worker4`, the old `node6` image address, and replacement of `cp-1`. Back up state, verify those remote objects are truly gone, and deliberately remove/import or move only the stale addresses; do not run the plan as an unreviewed destroy operation.
- [ ] **Plan the single-control-plane rebuild explicitly.** `cp-1` moves from retired `worker1` to `smallgpu`, while the existing bootstrap resource is unchanged because the API endpoint stays `192.168.1.210`. Arrange the Talos bootstrap/recovery step and expected API outage before apply; a replacement VM with no etcd data will not become a working cluster merely because its IP is unchanged.
- [ ] Deploy NVIDIA device plugin DaemonSet via ArgoCD (in `kubernetes/system/`)
- [ ] Prepare `smallgpu` RTX 2060 passthrough with `ansible/playbooks/configure-proxmox.yml --limit smallgpu --tags vfio`, explicitly approve the separate reboot play, and verify `/dev/vfio/18` before starting VM `403`
- [ ] Deploy ingress controller + cert-manager for TLS termination
- [x] **Configure the bulk NTFS export on smallgpu** — UUID-based `ntfs3` mount and `fsid=1` export observed active on 2026-07-22; Ansible now owns and safety-checks this state
- [x] **Verify the critical NFS server on Proxmox** — daemon active/enabled, ext4 backing mount present, export restricted to `192.168.1.0/24`, and TCP 2049 listening on 2026-07-22
- [ ] **Verify the critical NFS export from Talos** — a worker must successfully mount `192.168.1.105:/mnt/storage2-bulk` before critical workloads are deployed
- [ ] **Verify the bulk NFS export from Talos** — a worker must successfully mount `192.168.1.106:/mnt/data10tb` before bulk workloads are deployed
- [ ] Deploy media stack PVC(s) bound to `storage1-bulk-pv` (bulk tier)
- [ ] Deploy Immich + any other personal/critical workloads with PVCs bound to `storage2-bulk-pv` (critical tier) — **never** point critical apps at `storage1-bulk-pv`, smallgpu is borrowed
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
- **Bulk media storage is NTFS+NFS, not Ceph/Longhorn.** The 10 TB drive on smallgpu has existing NTFS data worth preserving. Ansible safety-checks and mounts it with the kernel `ntfs3` driver, then manages its NFSv4 export.
- **Two storage tiers, split by host permanence — not performance.** Critical/personal data binds against `storage2-bulk-pv` on `gpunvdgtx1060` (the only permanent host). Bulk/reproducible data binds against `storage1-bulk-pv` on smallgpu (borrowed hardware). The tier names map onto *survivability* of the underlying machine, not on IOPS or media class. See [Node ownership and permanence](#node-ownership-and-permanence).
- **VM sizing follows per-host headroom, not fleet totals.** Production uses one `2 vCPU / 4 GiB` control plane and three GPU workers. The RTX 2060 VM is deliberately untainted so it also runs ordinary pods; `smallgpu` reserves 12 GiB across only two VMs. The `2 vCPU / 3 GiB` GTX 1060 worker requires VM `100` ballooning because the laptop has only 15.46 GiB total.
