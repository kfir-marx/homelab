# Homelab — GitOps Kubernetes on Proxmox

GitOps-driven Kubernetes architecture running Talos Linux on two Proxmox
hosts. The separate Ubuntu workstation supplies critical NFS storage and keeps
its NVIDIA GPU for local graphics. The optional Windows 11 gaming VM on
`largegpu` shares the RTX 3080 with a Talos worker. Ansible configures
physical hosts, Terraform/Terragrunt provisions Proxmox VMs and bootstraps
Talos, and ArgoCD owns in-cluster workloads.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Git repository (this repo)                   │
│                                                                     │
│  ansible/             Configures Proxmox + Ubuntu physical hosts    │
│  terraform/           Provisions Proxmox VMs + Talos                │
│  kubernetes/          ArgoCD-managed app manifests + storage PVs    │
│  .github/workflows/   CI: lint, validate, security scan, plan       │
└────────────┬──────────────────────────────────┬─────────────────────┘
             │                                  │
       Terragrunt apply                  ArgoCD sync (automated)
   (state: s3://kfir-homelab-tfstate     prune + selfHeal
    native S3 lockfile, role: assumed)
             │                                  │
             ▼                                  ▼
┌──────────────────────────────────┐   ┌──────────────────────────────┐
│  Proxmox VE 9 (2 hosts)          │   │  Kubernetes / Talos 1.13    │
│                                  │   │                              │
│  smallgpu  ── 10 TB bulk NFS     │   │  cp-1   2c / 4 GiB          │
│            ── gpu-3 + RTX 2060   │   │  gpu-3 10c / 12 GiB / RTX2060│
│                                  │   │                              │
│  largegpu  ── cp-1               │   │  gpu-2 14c / 52 GiB / RTX3080│
│            ── gpu-2 / Windows    │   │                              │
│               runtime mutex      │   │  NFS PVs (static, RWX):     │
│                                  │   │   bulk     → smallgpu       │
├──────────────────────────────────┤   │   critical → Ubuntu :105    │
│  Ubuntu 26.04 workstation        │   └──────────────────────────────┘
│  192.168.1.105 ── 800 GB NFS     │
│                ── GTX 1060/HDMI  │
└──────────────────────────────────┘
```

### Physical nodes

| Host | Mgmt IP | Host OS / hypervisor | CPU / RAM | GPU | Primary role |
|------|---------|----------------------|-----------|-----|--------------|
| `ubuntu-workstation` | `192.168.1.105` on `enp7s0f1` | Ubuntu 26.04 | Intel i7-8750H, 6c/12t; 15.46 GiB | Intel UHD 630 + GTX 1060 Mobile for Ubuntu | Daily workstation and critical NFS |
| `smallgpu` | `192.168.1.106` | Proxmox VE 9 | Ryzen 5 3600, 6c/12t; 15.55 GiB | RTX 2060 | Mixed GPU worker and 10 TB bulk NFS |
| `largegpu` | `192.168.1.107` | Proxmox VE 9 | Ryzen 7 5800X, 8c/16t; 62.70 GiB | RTX 3080 LHR | Single control plane; Talos GPU / Windows runtime mutex |

| Host | Fast/system disk | Additional disk | Motherboard | Virtualization |
|------|------------------|-----------------|-------------|----------------|
| `ubuntu-workstation` | 238.5 GB Intel NVMe | 931.5 GB Samsung 860 EVO; 800 GB ext4 LV | CFL Sienta_CFS | Intel VT-x / VT-d |
| `smallgpu` | 476.9 GB XPG NVMe | 9.1 TB Toshiba HDD | ASUS PRIME B450M-A | AMD-V / AMD-Vi |
| `largegpu` | 931.5 GB Samsung 980 NVMe | 1.8 TB WD HDD | ASUS TUF GAMING X570-PLUS | AMD-V / AMD-Vi |

The fleet has 20 physical CPU cores / 40 threads and 93.71 GiB of installed RAM. These are host totals, not safe VM allocations. Capacity is unevenly distributed: 62.70 GiB is in `largegpu`, while each of the other two hosts has only about 15.5 GiB. RAM and failure-domain placement, rather than aggregate CPU, are the limiting factors.

The reserved cluster VIP is `192.168.1.210` — the Talos control-plane VIP and Kubernetes API endpoint.

### Workstation and host storage

Ubuntu mounts the 800 GB ext4 LV with UUID
`07445d19-37d4-4353-af1a-9511fb9c74e9` at `/mnt/storage2-bulk` and exports it
to the LAN as NFSv4 with `fsid=10`. The GTX 1060 and HDMI-audio functions belong
to Ubuntu's local graphics stack. The workstation runs neither Proxmox nor a
Kubernetes VM.

`smallgpu` mounts the 10 TB NTFS filesystem at `/mnt/data10tb` with the kernel
`ntfs3` driver and exports it with `fsid=1`. Ansible treats an unmounted dirty
or hibernated NTFS filesystem as a hard failure; it never clears safety flags
or force-mounts the disk.

### Configuration ownership boundary

Each layer has a deliberately non-overlapping owner:

- **Ansible owns physical host configuration:** Proxmox packages/storage/backups/NFS/VFIO on the two PVE hosts and NFS on Ubuntu. It never partitions or formats disks, changes the active workstation network profile, or reboots the workstation.
- **Terraform/Terragrunt owns virtual infrastructure and Talos bootstrap:** Proxmox VMs, PCI attachment, Talos machine configuration, cluster bootstrap, and initial ArgoCD/application bootstrap.
- **ArgoCD owns in-cluster resources:** applications, system controllers, and static Kubernetes PV/StorageClass declarations. Kubernetes manifests do not configure their physical NFS servers.

Proxmox installation and cluster joining remain prerequisites only for
`smallgpu` and `largegpu`. The Ubuntu workstation uses the separate
[`configure-ubuntu-workstation.yml`](../ansible/playbooks/configure-ubuntu-workstation.yml)
entry point. No resource should be declared in both Ansible and Terraform.

### Capacity and topology

The two-host Proxmox architecture uses one **2 vCPU / 4 GiB / 50 GiB** control
plane on the more reliable `largegpu` host. This preserves the Kubernetes API
when `smallgpu` fails, but the cluster is not control-plane HA.

The placement and resource budget are:

| Host | Control plane | Workers | Availability consequence |
|------|---------------|---------|--------------------------|
| `smallgpu` | None | `gpu-3` RTX 2060 `10 vCPU / 12 GiB` | Losing this host removes its worker and bulk NFS, but the Kubernetes API remains on `largegpu` |
| `largegpu` | `cp-1` | `gpu-2` RTX 3080 `14 vCPU / 52 GiB`, mutually exclusive with the 14 vCPU / 52 GiB Windows VM | `cp-1` remains running in either GPU/Windows mode; losing this host makes the API unavailable |

A two-member control plane would not meet the requirement: etcd requires a
majority of two, so losing either member would lose quorum. A single-member
cluster has quorum while that one member is alive. It is not HA, but placing it
on `largegpu` matches the preferred failure scenario and frees 4 GiB on
`smallgpu` for applications when `gpu-2` is stopped for Windows.

The Ubuntu workstation deliberately runs no Kubernetes VM. Its constrained
memory remains available to the interactive desktop and NFS, while any
LAN-connected Kubernetes node can mount the critical export.

### Node ownership and permanence

Only **`ubuntu-workstation`** is permanently owned hardware. `smallgpu` and
`largegpu` are borrowed from a friend who may eventually ask for them back.

Practical consequences that the rest of this document depends on:

- **Critical / personal data stays on `ubuntu-workstation`.** Personal cloud, config snapshots, and anything irreplaceable bind against `storage2-bulk-pv`.
- **Bulk / non-critical / reproducible data goes on the borrowed hosts.** Media libraries (Plex/Jellyfin, *arr stack), large model caches, and anything that can be re-downloaded land on the **bulk tier** (`storage1-bulk-pv` on `smallgpu`, 10 TB).
- **`smallgpu` is the "big storage server, non-critical" role.** It is at `192.168.1.106`; its Kubernetes PV and StorageClass are named `storage1-bulk-pv` and `nfs-storage1`.
- **The workstation is storage, not cluster compute.** Critical pods may mount its NFS export from any node, but that storage is unavailable whenever Ubuntu is down.

**IP convention:**

| Range              | Use                                  |
|--------------------|--------------------------------------|
| `192.168.1.101–199` | Physical hosts                       |
| `192.168.1.200–299` | VMs (role-specific static address pools) |

**Why VMs live on the home subnet:** both Proxmox hosts use `vmbr0`, bridged to
the same LAN. The Ubuntu workstation uses its physical Ethernet interface
directly and hosts no VMs.

VM specs, IPs, and PCI/USB device IDs are defined as YAML in [`terraform/deployments/<env>/config.yml`](../terraform/deployments/) and consumed via Terragrunt's hierarchical config-merging in [`root.hcl`](../terraform/deployments/root.hcl). The production YAML implements the single-control-plane topology described above.

### Network defaults

| Setting          | Default value                   |
|------------------|---------------------------------|
| Cluster VIP      | `192.168.1.210`                 |
| K8s API endpoint | `https://192.168.1.210:6443`    |
| Gateway          | `192.168.1.1` (home router)     |
| Bridges          | Proxmox `vmbr0`                 |
| DNS              | `1.1.1.1`, `8.8.8.8`            |
| CP node IPs      | `cp-1`: `192.168.1.211/24` |
| Worker IPs       | GPU workers also accept ordinary workloads |
| GPU node IPs     | `gpu-2`: `.232/24`; `gpu-3`: `.233/24` |

The control-plane VIP is managed by Talos's built-in VIP mechanism — no external load balancer is needed. Every control-plane machine configuration includes a network-interface `vip` block pointing at the shared VIP.

---

## ⚡ The `largegpu` runtime mutex (Talos GPU worker ↔ Windows VM)

Both `gpu-2` (Talos K8s GPU worker) and `largegpu-win11` (Windows gaming VM) are defined in Terraform for the same Proxmox host (`largegpu`), and **both have the RTX 3080 (`0000:08:00.0`) configured for PCIe passthrough**. Terraform creates both VMs; there is no config-time mutex.

The exclusivity is enforced at **VM start time** by Proxmox itself: the GPU can only be bound to one running VM. Wait for the source VM to stop completely before starting the destination VM so the RTX 3080's VFIO group has been released:

```bash
# Talos → Windows
qm shutdown 402 && qm wait 402 --timeout 180 && qm start 502

# Windows → Talos
qm shutdown 502 && qm wait 502 --timeout 180 && qm start 402
```

If the graceful shutdown does not complete within three minutes, `qm wait`
fails and the chained command leaves the destination VM stopped. Investigate
the source VM instead of immediately forcing it off.

The configured runtime allocation is:

- `gpu-2` and the Windows VM each request 14 vCPUs and 52 GiB RAM. They remain mutually exclusive because they share the RTX 3080. In either mode, `cp-1` retains 2 vCPUs / 4 GiB, leaving about 6.7 GiB nominal memory headroom for Proxmox and QEMU overhead.
- The largegpu host's NVMe LVM-thin is reserved for the standalone 635 GiB Windows disk. The resident 50 GiB control-plane disk and the disposable `gpu-2` disks stay on `largegpu-hdd`. The Proxmox host's root, swap, and thin-pool metadata necessarily remain on the physical NVMe. VM 502 has no linked-clone parent, so ordinary rewrites do not retain a template's old blocks until `nospace`.
- The 159 GiB `gpu-2` system disk and a separate 400 GiB scratch disk are sparse `qcow2` volumes on `largegpu-hdd`; scratch backups remain disabled. Talos provisions the latter as the `gpu-scratch` user volume and mounts it at `/var/mnt/gpu-scratch`; Kubernetes exposes it through the static `local-gpu-scratch` StorageClass/PV.
- A retained 50 GiB local-lvm disk is attached to `gpu-3` for the media stack's SQLite/config state. Talos selects the unique non-system disk by its declared size and mounts it at `/var/mnt/media-state`; encrypted daily backups land on the permanent critical NFS tier.
- The 2 GiB hugepage pool is kept only on dedicated `gpu-2`. Mixed `gpu-3` sets `vm.nr_hugepages=0` so Jellyfin and ordinary workloads can use that memory.
- `on_boot = true` for the Talos GPU worker (auto-start on Proxmox boot); `on_boot = false` for the Windows VM (manual).

### Windows VM lifecycle: standalone NVMe workstation

Windows is managed by the independent `windows-workstation` stack and remote
state. VM 502 is a standalone guest on `local-lvm` with no clone source. The
thin pool therefore stores only its live disk and does not pin old template
blocks whenever Windows rewrites data.

The shared component supports two modes:

| Mode      | When                        | What happens                                                                 |
|-----------|-----------------------------|------------------------------------------------------------------------------|
| STANDALONE | `template_vm_id: null`     | Independent scsi0 disk, EFI vars, and vTPM 2.0. The installer ISO is attached only when `attach_install_media: true`; normal disaster recovery restores VM 502 from a native backup. |
| CLONE     | `template_vm_id: <source>`  | Optional provisioning from another VM/template. Full clone = independent disk; linked clone (`full_clone: false`) = LVM-thin copy-on-write. |

`virtio-win.iso` (Fedora's signed driver ISO) must be attached manually on a SATA slot before a fresh install — `bpg/proxmox` v0.105 only allows one `cdrom` block per VM, and SATA is hot-pluggable (IDE is not).

VM 502 is restored directly from its nightly native archive before Terraform
is used to reconcile hardware settings. The archive preserves the VM disk and
configuration under the stable VMID. Backup power-state behavior and recovery commands are in
[`windows-vm-backup.md`](windows-vm-backup.md).

---

## Storage layout

### Physical-host storage

The Proxmox hosts retain their `local` and `local-lvm` stores. Ubuntu directly
mounts the existing ext4 LV; it is not registered as Proxmox storage.

| Host         | Storage name        | Type         | Size       | Purpose                                                         |
|--------------|---------------------|--------------|------------|-----------------------------------------------------------------|
| `largegpu`      | `local-lvm`           | LVM-thin     | 810 GB     | Independent Windows VM 502 disk (~635 GB) |
| `largegpu`      | `largegpu-hdd`        | Directory    | 1.83 TB    | Resident `cp-1`, Windows/VirtIO ISOs, then `gpu-2`'s sparse 159 GiB system disk and capped 400 GiB disposable scratch |
| `ubuntu-workstation` | `gpu1-extra`          | LVM-thin     | 912 GB     | Existing pool containing the critical-data LV |
| `ubuntu-workstation` | `storage2-bulk` (NFS) | ext4 LV on `gpu1-extra`, NFSv4 export | 800 GB | **Critical tier** — Immich and personal data |
| `smallgpu`      | `storage1-bulk` (NFS) | 10 TB NTFS via kernel `ntfs3`, NFSv4 export | 10 TB  | **Bulk tier** — active and mount-verified from Talos |

### Kubernetes storage

Two base static NFS-backed `PersistentVolume`s define the storage tiers, one
per export. Applications may declare smaller, hard-bound static PVs beneath an
export so claims have independent retention and cannot bind to another app's
path. Both exports must be mountable from the Talos nodes.

| PV name             | StorageClass     | Backed by                                       | Size   | Tier — use case                                                                            |
|---------------------|------------------|-------------------------------------------------|--------|--------------------------------------------------------------------------------------------|
| `storage1-bulk-pv`  | `nfs-storage1`   | `smallgpu:/mnt/data10tb` (NTFS via `ntfs3`)        | 9 Ti   | **Bulk** — media (Plex/Jellyfin, *arr), model caches, anything reproducible                |
| `storage2-bulk-pv`  | `nfs-storage2`   | `ubuntu-workstation:/mnt/storage2-bulk` (`192.168.1.105`) | 800 Gi | **Critical** — Immich, config snapshots, and personal data |
| `media-data-pv` | `nfs-storage1` | `smallgpu:/mnt/data10tb/media` | 7 Ti | **Bulk** — Jellyfin library, torrents, and shared hardlink tree |
| `media-state-pv` | `local-media-state` | `gpu-3:/var/mnt/media-state` | 45 Gi | **Local state** — SQLite/config; encrypted backups required |
| `media-backups-pv` | `nfs-storage2` | `ubuntu-workstation:/mnt/storage2-bulk/media/backups` | 20 Gi | **Critical** — encrypted media-stack state archives |
| `job-assistant-*-pv` | `nfs-storage2` | `ubuntu-workstation:/mnt/storage2-bulk/job-assistant/*` | 1–20 Gi each | **Critical** — personal job history, PostgreSQL, CV artifacts, backups, and encrypted Codex credential state |
| Workstation assistant bridge state | host filesystem | `ubuntu-workstation:/mnt/storage2-bulk/homelab-assistant/sessions` | bounded by critical tier | **Critical** — selected Codex thread IDs, opaque callback nonces, sanitized audit metadata, and retained legacy session migration artifacts; Codex transcript history remains in the user's normal `~/.codex` store |
| `homelab-assistant-postgres-pv` | `nfs-storage2` | `ubuntu-workstation:/mnt/storage2-bulk/homelab-assistant/postgres` | 5 Gi | **Retained recovery** — inactive legacy PostgreSQL binding preserved through migration and rollback |
| `external-ai-*-pv` | `nfs-storage2` | `ubuntu-workstation:/mnt/storage2-bulk/external-ai/*` | 1–5 Gi | **Critical** — durable external job queue and retained ChatGPT-managed Codex authentication |

Both PVs are `ReadWriteMany`, mounted with `nfsvers=4.2,hard`, and use `Retain` reclaim policy. Manifests live in [`kubernetes/system/storage/`](../kubernetes/system/storage/) (`storage1-bulk.yaml`, `storage2-bulk.yaml`). Physical mounts, exports, and `nfs-kernel-server` are owned by the Ansible `nfs_server` role, not by Kubernetes manifests. On the NTFS-backed bulk tier, Ansible also exports each PV child path explicitly with its own stable `fsid`; Talos mounts those child paths directly, and the parent NTFS export alone does not reliably serve a fresh child-path mount after an NFS restart.

Applications that need hard binding and independent retention declare smaller
static PVs beneath the same export. Bitwarden uses separate retained paths for
application/attachment state, PostgreSQL, and logical backups under
`/mnt/storage2-bulk/bitwarden`. All three use `nfs-storage2`; none may be moved
to a borrowed-host or scratch tier. The backup path shares the source failure
domain and must also be copied to an encrypted off-host destination.

The media stack follows a three-part variant of this model. Replaceable media
and torrents share one `media-data-pv` mount so Servarr imports can hardlink
and Bazarr can write subtitle files beside the media. Live SQLite databases
use a retained local disk on `gpu-3`, because Servarr and Bazarr do not support
application databases on NFS. A daily job performs consistent SQLite backups,
encrypts the complete state archive, and stores it on `media-backups-pv` on the
permanent workstation.

The separate `gpu2-scratch-pv` is node-local rather than NFS. It is a static
`390 GiB`, `ReadWriteOnce` PV backed by the capped 400 GiB virtual HDD attached
only to `gpu-2`, with `WaitForFirstConsumer` binding and node affinity. It is
appropriate only for replaceable caches and temporary work. It is unavailable
while Windows owns `largegpu`, and loss or return of that borrowed host destroys
the data. Talos VM `402` is backed up at the VM level for fast node recovery,
while its separately attached scratch disk remains excluded and disposable.

The homelab Telegram client is deliberately outside Kubernetes on the permanent
Ubuntu workstation. A host systemd unit runs Codex App Server as the normal
`kfir` user with `HOME=/home/kfir` and the homelab repository cwd. A separate,
locked bridge container reaches it only through a group-protected Unix socket;
the container cannot read Codex authentication or the workstation home. This
reuses the normal CLI/VS Code thread store, configuration, tools, MCPs, skills,
plugins, and repository instructions instead of creating a parallel provider
or transcript model. Argo CD owns only the deterministic switcher identity and
the inactive Retained PostgreSQL recovery binding. A forced-command SSH
actuator on `largegpu` exposes three hardcoded operations for the 402/502 mutex;
the switching path remains outside Codex and model text can never invoke it.

The opposite-node `backup-on-smallgpu` storage holds the two newest nightly
native archives of standalone Windows VM 502. Its dedicated job runs at 04:15.
The snapshot job has no hook and never changes the power state of VM 502 or its
GPU-sharing peer 402. A running VM is backed up online; for a stopped VM,
Proxmox's temporary backup-only QEMU process does not change configured power
state or claim the passed-through GPU. General cross-node jobs protect the
remaining guests: `smallgpu` writes to `largegpu-hdd` at 02:15, while `largegpu`
writes to `smallgpu`'s NTFS bulk disk at 07:00 and excludes 502. They retain
three recent and two weekly Zstandard archives. Proxmox registrations restrict each backup storage to its
source node, and the server exports only the dedicated destination to the
opposite host. Ansible registers `storage1-bulk` at the `/mnt/data10tb` export.
Windows and VirtIO installation ISOs remain
low-priority, replaceable assets; Terraform-managed per-node Talos images remain
on each node's `local` storage.

Prometheus, Alertmanager, and Grafana use smaller static PVs carved from the
`nfs-storage1` export under `/mnt/data10tb/monitoring`. Metrics are explicitly
non-critical. Prometheus does not officially support NFS for its local TSDB, so
corruption or loss is handled by resetting the metrics directory rather than by
treating monitoring history as durable data.

To consume one: create a PVC in the app's namespace with the matching `storageClassName` and pin `volumeName` to the PV name. No dynamic provisioner — PVs are static, so a typo in `storageClassName` will just leave the PVC `Pending` forever rather than silently provisioning somewhere wrong.

> **Picking a tier:** if losing the data is merely inconvenient (re-download / re-rip), use `storage1-bulk-pv`. If losing it is unrecoverable (personal photos, config you don't have a backup of, etc.), use `storage2-bulk-pv`. When in doubt, critical tier — 800 GB on the permanent host is the scarce resource, but it's the one that survives a borrowed-machine return.

---

## Technology stack

| Layer              | Tool / Version                                  | Purpose                                                  |
|--------------------|-------------------------------------------------|----------------------------------------------------------|
| Hypervisors        | Proxmox VE 9 on two hosts                       | Talos and Windows VM capacity                            |
| APT repo           | `pve-no-subscription` (deb822 format)           | Enabled on both Proxmox nodes; enterprise repo disabled  |
| Node OS (K8s)      | Talos Linux `v1.13.7`                           | Immutable, API-driven Linux — no SSH, no shell           |
| Node OS (gaming)   | Windows 11 25H2 + virtio drivers (0.1.271)      | One VM, GPU-passthrough'd, manual start                  |
| VM provisioning    | Terraform + `bpg/proxmox` ~> 0.105.0            | Creates Proxmox VMs                                      |
| Cluster bootstrap  | Terraform + `siderolabs/talos` ~> 0.7           | Generates machine configs, applies them, bootstraps etcd |
| Stack orchestration| Terragrunt 0.63.0                               | Hierarchical YAML config merging + per-env state isolation |
| Remote state       | S3 `kfir-homelab-tfstate` + native lockfile + IAM role assumption | Encrypted state with cross-account `sts:AssumeRole` |
| GitOps engine      | ArgoCD (Helm chart `argo-cd` v7.8.13)           | Manages all in-cluster workloads from Git                |
| CI                 | GitHub Actions                                  | Runs `terraform fmt`, `validate`, `tfsec`, and `plan` on PRs |
| Terraform apply    | Manual Terragrunt outside Kubernetes            | Keeps cluster changes and recovery independent of in-cluster runners |

### Why username/password auth instead of API token

The Proxmox provider uses `root@pam` with the SSH password rather than an API token:

> Proxmox 8.x+ refuses to let API tokens set raw `hostpci` config — even root-realm tokens with `privsep=0` hit `only root can set 'hostpci0' config for non-mapped devices`. Real-user auth bypasses that.

The current configuration uses direct PCI addresses in `hostpci`, so password
authentication is required.

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
├── ansible/                              # Physical Proxmox + Ubuntu host configuration
│   ├── inventory/production/             # Hosts, group vars, hardware-specific host vars
│   ├── playbooks/                        # Configure, verify, and explicit reboot entry points
│   └── roles/                            # Repositories, storage/backups, NFS, VFIO, preflight
├── docs/
│   └── architecture.md                   # This file
├── kubernetes/
│   ├── apps/                             # ArgoCD Application manifests (app of apps)
│   ├── system/                           # Cluster-wide infrastructure
│   │   └── storage/
│   │       ├── storage1-bulk.yaml        # Bulk tier: NFS PV + SC, 10 TB NTFS on smallgpu
│   │       └── storage2-bulk.yaml        # Critical tier: 800 GB NFS on Ubuntu
│   └── bootstrap/                        # One-time bootstrap resources
└── terraform/
    ├── run-terragrunt.sh                         # Wrapper: loads .env → runs terragrunt
    ├── deployments/                      # Per-environment Terragrunt stacks
    │   ├── root.hcl                      # S3 backend + IAM role + input plumbing
    │   ├── merge_configs.sh              # Hierarchical YAML deep-merge
    │   ├── config.yml                    # Global defaults
    │   ├── prod/
    │   │   ├── config.yml                # Prod VM topology for two Proxmox hosts
    │   │   ├── homelab-cluster/
    │   │   │   └── terragrunt.hcl        # Talos/Kubernetes state
    │   │   └── windows-workstation/
    │   │       ├── config.yml            # Template, VM, PCI and USB mapping
    │   │       └── terragrunt.hcl        # Independent Windows state
    │   └── staging/
    │       ├── config.yml
    │       └── homelab-cluster/
    │           └── terragrunt.hcl
    └── modules/
        ├── components/
        │   └── proxmox-windows-vm/
        │       └── main.tf               # Windows install + clone component
        └── stacks/
            ├── homelab-cluster/
                ├── main.tf               # VMs → Talos → ArgoCD
                ├── variables.tf
                ├── providers.tf          # Proxmox (user/pass) + Talos + Helm
                └── modules/
                    ├── proxmox-vm/                  # Talos VMs (CP/worker/GPU)
                    │   └── main.tf
                    └── talos-cluster/
                        ├── main.tf                  # Secrets, configs, bootstrap, kubeconfig
                        └── talos-gpu-patch.yaml     # NVIDIA extensions + containerd
            └── windows-workstation/
                ├── main.tf                         # Windows VM fan-out
                ├── providers.tf
                └── variables.tf
```

### Key file reference

| File | What it does | When to edit |
|------|-------------|--------------|
| `ansible/inventory/production/` | Physical hosts, NFS mounts/exports, PCI IDs and IOMMU groups | Adding or changing a joined Proxmox host |
| `ansible/playbooks/configure-proxmox.yml` | Serial, idempotent physical-host convergence | Repositories, packages, NFS, or VFIO host policy |
| `terraform/deployments/<env>/config.yml` | Per-environment cluster node maps and network settings | Adding/removing Talos nodes or changing cluster hardware |
| `terraform/deployments/prod/windows-workstation/config.yml` | Windows template, VM size, and PCI/USB mappings | Changing the workstation or its peripherals |
| `terraform/deployments/config.yml` | Global defaults shared across all environments | Changing Talos version, default DNS, etc. |
| `terraform/deployments/root.hcl` | Terragrunt root config: S3 backend, `iam_role` from `AWS_IAM_ROLE`, input plumbing | Switching backends, changing retry policy |
| `terraform/deployments/<env>/<stack>/terragrunt.hcl` | One-line `include "root"` — stack name auto-derived from dir | Almost never |
| `terraform/modules/stacks/homelab-cluster/main.tf` | Calls sub-modules: VMs → Talos → ArgoCD bootstrap + root app | Changing orchestration logic |
| `terraform/modules/stacks/homelab-cluster/providers.tf` | Proxmox (username/password), Talos, Helm provider configs | Auth changes |
| `terraform/modules/stacks/homelab-cluster/modules/proxmox-vm/main.tf` | One Talos VM (CP/worker/GPU) with conditional PCIe passthrough | Changing Talos VM defaults |
| `terraform/modules/stacks/windows-workstation/` | Independent Windows stack backed by `prod/windows-workstation.tfstate` | Changing Windows orchestration |
| `terraform/modules/components/proxmox-windows-vm/main.tf` | Windows 11 VM: standalone disk or optional clone provisioning | Changing Windows VM defaults or drivers |
| `terraform/modules/stacks/homelab-cluster/modules/talos-cluster/main.tf` | Per-role machine configs, applies them, bootstraps etcd | Changing Talos config patches, cluster topology |
| `terraform/modules/stacks/homelab-cluster/helm/cilium-l2-config/` | Cilium LB IPAM pool and LAN L2 announcement policy | Changing LoadBalancer address allocation or advertisement |
| `terraform/modules/stacks/homelab-cluster/talos-images/*.yaml` | Talos Image Factory extensions for base and GPU images | Adding or removing OS-level extensions |
| `terraform/modules/stacks/homelab-cluster/modules/talos-cluster/talos-gpu-patch.yaml` | NVIDIA kernel modules and containerd config | Changing GPU runtime behavior |
| `kubernetes/system/storage/storage1-bulk.yaml` | NFS-backed `PV` + `StorageClass` — bulk tier (10 TB NTFS on smallgpu) | Resizing, retargeting NFS server, host-side export setup |
| `kubernetes/system/storage/storage2-bulk.yaml` | NFS-backed `PV` + `StorageClass` — critical tier (800 GB ext4 on Ubuntu) | Resizing the carved LV, host-side export setup |
| `kubernetes/apps/` | ArgoCD watches this directory for Application manifests | Deploying any new workload |
| `.github/workflows/terraform-plan.yml` | CI: format check → validate → plan posted to PR | Changing CI behavior |

---

## How Terraform is organized

### Execution flow

The `homelab-cluster` apply executes a single DAG with explicit
`depends_on` ordering:

```
1. talos_image_factory_schematic.this[*]
        │
        ▼
   proxmox_download_file.talos_image[*]   (once per host/profile pair)
        │
        ▼
2. module.control_plane_vms  ─┐
   module.worker_vms          │
   module.gpu_vms             └──► 3. module.talos_cluster
                                            │
                                            ▼
                                4. Kubernetes API TCP readiness gate
                                            │
                                            ▼
                                5. helm_release.cilium
                                            │
                                            ▼
                                6. Cilium LB IPAM + L2 policy
                                            │
                                            ▼
                                7. talos_cluster_health
                                            │
                                            ▼
                                8. ArgoCD ──► 9. root Application
```

**Step 1:** Repo-owned YAML files define separate base and NVIDIA GPU Image
Factory profiles. Proxmox hosts download their base or RTX artifacts through
the provider.

**Step 2:** Talos VM module fan-outs:

- `control_plane_vms` / `worker_vms` / `gpu_vms` — Talos VMs via `proxmox-vm`.

Windows is deliberately absent from this DAG. The `windows-workstation` state
manages VM 502 independently so workstation drift cannot block cluster changes.

Physical host networking is intentionally absent from the Terraform DAG.
Proxmox `vmbr0` already exists. Ubuntu's direct Ethernet and NFS address are
documented in the workstation runbook and are not VM prerequisites.

**Step 3:** The `talos-cluster` module generates per-node machine configurations using `talos_machine_configuration` data sources. Each node gets a config patch with its hostname, static IP, and routes. The built-in CNI and kube-proxy are disabled, and Kubernetes is explicitly pinned to a version supported by both Talos and Cilium. GPU nodes additionally receive the `talos-gpu-patch.yaml` (module/runtime activation) and labels/taints; the NVIDIA extensions themselves are already in the GPU disk image. The module then applies configs via `talos_machine_configuration_apply`, bootstraps etcd on the first control-plane node, and retrieves the kubeconfig. It deliberately does not wait for Kubernetes node readiness because that cannot happen before Cilium exists.

**Step 4:** A local Terraform readiness gate waits up to five minutes for the Kubernetes API VIP's TCP socket. Kubeconfig retrieval can finish just before the freshly configured control plane reboots, so this gate prevents the Helm provider from racing that short outage.

**Step 5:** Terraform installs the pinned Cilium Helm chart in `kube-system`. Cilium uses Kubernetes host-scope IPAM, Talos's existing cgroup v2 mount, full kube-proxy replacement, and the API VIP at `192.168.1.210:6443`. L2 announcements are enabled for `eth0`.

**Step 6:** A small repo-local Helm chart creates a `CiliumLoadBalancerIPPool` from the explicitly configured DHCP-excluded LAN range and a `CiliumL2AnnouncementPolicy`. Cilium LB IPAM allocates addresses; the L2 policy advertises them with ARP. MetalLB is not installed.

**Step 7:** A repo-local readiness Job waits for every Kubernetes node, the Cilium DaemonSet and operator, and CoreDNS to become Ready, and verifies that the LB IPAM pool and L2 policy exist. This explicit Job is necessary because Talos intentionally skips NodeReady and CoreDNS assertions when its configured CNI name is `none`. Terraform then runs the remaining Talos cluster health checks.

**Step 8:** The Helm provider installs ArgoCD with normal readiness waiting
enabled. `argocd-server` remains a ClusterIP Service; an Argo-managed Cilium
Gateway requests the first address in the LB IPAM pool and owns browser ingress.

**Step 9:** A second Argo Helm release deploys the "root Application" using the `argocd-apps` chart, pointing at `kubernetes/apps/` in this repo with automated sync + prune + self-heal. From this point, ArgoCD owns application state; Terraform continues to own the bootstrap CNI and its address-advertisement policy.

### Module: `proxmox-vm` (Talos VMs)

Creates a single Proxmox QEMU VM for Talos. Key behaviors:

- **Machine type:** Always `q35` (required for PCIe passthrough).
- **BIOS:** `ovmf` (UEFI) for GPU nodes, `seabios` for others. OVMF requires the dynamic `efi_disk` block.
- **CPU type:** Always `host` — required for GPU passthrough.
- **Boot disk:** Cloned from the downloaded base or GPU Talos `nocloud` image (`file_format = "raw"`). `lifecycle.ignore_changes = [disk[0].file_id]` so a Talos upgrade doesn't fight an in-place node's config.
- **QEMU guest agent:** Enabled because both image profiles include the `siderolabs/qemu-guest-agent` extension.
- **Cloud-init `initialization` block:** Static IP via nocloud cidata — without this, Talos comes up in maintenance mode on a DHCP lease and `talos_machine_configuration_apply` can't find the node.
- **PCIe passthrough:** Dynamic `hostpci` block iterating over `var.pci_devices`. Empty list = no passthrough.
- **`on_boot = true`** — Talos VMs auto-start with the host.
- **Tags:** `["talos", "terraform"]`.

### Module: `proxmox-windows-vm`

Creates the Windows 11 gaming VM in standalone or clone-provisioned mode (see "Windows VM lifecycle" above). Key behaviors:

- **Always `q35` + `ovmf`** — Win11 requires both.
- **Secure Boot:** `efi_disk.pre_enrolled_keys = true` so Microsoft's signing keys are baked into the EFI vars at creation.
- **vTPM 2.0:** Created via `tpm_state` block (Win11 hard requirement).
- **Optional `cdrom` block (Windows ISO on `ide2`).** It is absent for the restored production VM. `virtio-win.iso` is attached manually on SATA for a fresh install.
- **`boot_order`:** standalone production boots `scsi0`; a deliberate install boots `ide2` first; clone mode inherits its source order.
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
  - *GPU workers only:* `talos-gpu-patch.yaml` + node labels (`nvidia.com/gpu.present=true`, `homelab.dev/role=gpu-worker`). The `dedicated` field controls the hugepage allocation: 1,024 hugepages on `gpu-2` and none on mixed-role `gpu-3`.
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

## Remote state (S3 native locking + IAM role assumption)

State is stored in S3 with the backend's native lockfile. Terragrunt assumes an
IAM role before every AWS call:

```hcl
# terraform/deployments/root.hcl
iam_role = get_env("AWS_IAM_ROLE")   # arn:aws:iam::<acct>:role/TerragruntExecutionRole

remote_state {
  backend = "s3"
  config = {
    bucket       = "kfir-homelab-tfstate"
    key          = "${dirname(local.relative_deployment_path)}/${local.stack}.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
```

Prerequisites:
1. Local AWS credentials with `sts:AssumeRole` on the target role.
2. The target role's trust policy allows the local principal.
3. The target role can read/write the state and `.tflock` objects in the bucket.

Terragrunt creates the bucket on first init if it does not exist.

The same role is used by GitHub Actions and local operators, so the target role
policy is shared even though each execution environment needs its own source
AWS credentials.

---

## GPU passthrough pipeline

GPU support spans three layers: physical-host VFIO, the Talos
OS-level NVIDIA stack, and Argo-managed Kubernetes GPU components:

```
Physical host                    Talos VM                           Kubernetes
─────────────                    ────────                           ──────────
IOMMU enabled          ──►  PCIe device visible       ──►   NVIDIA GPU Operator
vfio-pci driver bound         in the VM                       discovers all nodes
via hostpci/hostdev           kernel modules loaded:          and exposes
                              nvidia, nvidia_uvm,             nvidia.com/gpu
                              nvidia_drm, nvidia_modeset
                                                              DCGM Exporter sends
                              CDI supplied by the             GPU metrics to
                              container-toolkit extension     Prometheus
```

**Physical-host side:** Ansible owns IOMMU parameters, VFIO
modules, PCI ID binding, driver blacklists, initramfs update, and verification.
Terraform's Proxmox module only attaches the prepared devices:
- GPU VMs use `bios = "ovmf"` and `machine = "q35"`.
- GPU addresses are Proxmox `08:00` for `gpu-2`/Windows and Proxmox `09:00` for `gpu-3`.
- AMD-V (SVM) must be enabled in BIOS on AMD hosts (largegpu, smallgpu).
- Ansible stages `amd_iommu=on`, verifies the complete IOMMU group, and binds the declared GPU functions to `vfio-pci` after an explicitly approved reboot.

**Talos side** (handled by `talos-gpu-patch.yaml`):
- Installs NVIDIA kernel modules and the matching container toolkit as Talos
  system extensions. Both RTX nodes use the production open-driver profile.
- Loads four kernel modules at boot: `nvidia`, `nvidia_uvm`, `nvidia_drm`, `nvidia_modeset`.
- Uses Talos 1.13's CDI support and the toolkit extension to make NVIDIA devices available to the container runtime.
- Sets `vm.nr_hugepages = 1024` for large-memory GPU workloads.

**Kubernetes side** (Terraform labels plus the Argo-managed GPU Operator):
- GPU nodes get labels: `nvidia.com/gpu.present=true`, `homelab.dev/role=gpu-worker`, `homelab.dev/gpu-node=<hostname>`.
- GPU nodes are not tainted, so both can accept ordinary workloads; `gpu-3` is the mixed-role worker expected to carry them consistently.
- ArgoCD installs NVIDIA GPU Operator v26.3.1 with its driver and toolkit disabled because Talos owns those host components.
- GPU Operator deploys node-feature-discovery, the NVIDIA device plugin, validation operands, and DCGM Exporter. Prometheus discovers DCGM through a `ServiceMonitor`.

---

## CI/CD pipeline

### GitHub Actions (`.github/workflows/terraform-plan.yml`)

Triggers on PRs to `main` that touch `terraform/**`. Runs lint and security
checks and plans both Proxmox-backed Terraform stacks.

1. **Lint & Format** — `terraform fmt -check -recursive` + `terragrunt hclfmt --terragrunt-check`.
2. **Security Scan** — `tfsec` via the `aquasecurity/tfsec-action`. Currently `soft_fail: true` (non-blocking) — tighten once rules are tuned.
3. **Plan matrix** — runs `terragrunt plan` for `homelab-cluster` and
   `windows-workstation` and posts each output as a PR comment. It uses the
   Proxmox and AWS credentials supplied to CI.

The plan job depends on lint + security passing first.

All workflows select `${{ vars.RUNNER_LABEL || 'ubuntu-latest' }}`. During
bootstrap or a cluster outage, an absent `RUNNER_LABEL` uses GitHub-hosted
capacity. In normal operation the repository variable is `homelab`, which
routes jobs to the Argo-managed Actions Runner Controller scale set.

ARC `0.14.2` runs in `arc-systems`; ephemeral job pods run separately in
`github-actions-runners`. The scale set has zero idle runners and a concurrency
cap of one to protect the smaller mixed worker. Docker-in-Docker is enabled
because current workflows require service containers and Buildx, making this a
deliberate privileged workload boundary. Authentication, rollout, rotation,
and the trust restrictions are in
[`github-actions-runners-runbook.md`](github-actions-runners-runbook.md).

### Terraform apply execution boundary

Terraform apply is intentionally manual in the current architecture. After
reviewing the PR plan, run the matching stack through
`terraform/run-terragrunt.sh` from an administrative machine outside
Kubernetes. For example:

```bash
./terraform/run-terragrunt.sh prod homelab-cluster apply
```

The Actions Runner Controller and its ephemeral runners are Argo-managed
workloads inside the cluster that the `homelab-cluster` stack creates. They are
appropriate for CI and non-mutating plans while the cluster is healthy, but
using them as the only apply executor would make cluster changes and recovery
depend on the infrastructure being changed. The S3 state backend remains
external to the cluster; the dependency is the runner, not the state.

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
| `kubernetes/system/`         | Cluster-wide infrastructure (storage, private gateways, NVIDIA GPU Operator support, etc.) |
| `kubernetes/system/storage/` | NFS-backed PVs + StorageClasses (e.g. `storage1-bulk.yaml`) |
| `kubernetes/bootstrap/`      | One-time setup resources that don't fit the ArgoCD lifecycle |

The root app's `repoURL` is set in [`terraform/deployments/config.yml`](../terraform/deployments/config.yml) (`argocd_repo_url`). Per-environment `argocd_target_revision` lives in each env's `config.yml`.

## Cilium networking and LAN LoadBalancers

Talos boots with `cluster.network.cni.name = "none"` and
`cluster.proxy.disabled = true`. Terraform then installs Cilium before running
the full cluster health check or installing ArgoCD. This ordering is a bootstrap
requirement: pod networking cannot become Ready until Cilium exists, while
ArgoCD itself depends on pod networking. A pre-Cilium TCP gate also waits for
the API VIP after Talos bootstrap, because a fresh control plane may reboot
between kubeconfig retrieval and the first Helm provider request.

The globally configured versions are Talos `v1.13.7`, Kubernetes `1.34.9`, and
Cilium `1.19.6`. Talos 1.13 supports Kubernetes 1.31–1.36, but stable Cilium
1.19 only guarantees Kubernetes 1.31–1.34, so Kubernetes must not silently
follow Talos's newer 1.36 default.

Production must define `cilium_load_balancer_ip_start` and
`cilium_load_balancer_ip_stop` as a contiguous LAN range reserved outside DHCP.
Terraform rejects a reversed or single-address range and any overlap with the
API VIP or node IPs. The first address is reserved for the private Cilium
Gateway API entry point; remaining addresses are available to other
`LoadBalancer` Services. The pool is advertised on `eth0` by Cilium L2
announcements. Do not deploy MetalLB alongside this configuration.

Gateway API v1.4.1 CRDs are pinned and installed by Terraform before Cilium.
Cilium's Gateway controller owns `192.168.1.220` and routes private hostnames
such as `https://argocd.home.547600.xyz` to ClusterIP-only backends. AdGuard Home
uses Cilium LoadBalancer VIP `192.168.1.221` for TCP/UDP DNS and privately maps
`*.home.547600.xyz` to the shared Gateway. Both addresses are reached remotely
through the official Tailscale service and the cluster subnet router's
`192.168.1.0/24` route; they are not Tailscale CGNAT addresses and remain
directly reachable from the trusted LAN. Public services such as Jellyfin use a
separate Cloudflare Tunnel path and do not traverse the tailnet.

---

## Getting started

### Prerequisites

1. **Physical hosts:** Proxmox VE 9 on `smallgpu` and `largegpu`, both joined to `HomeLab-Cluster`; Ubuntu 26.04 NFS server on `ubuntu-workstation`; virtualization/IOMMU enabled on the Proxmox hosts. Run the matching [Ansible host configuration](../ansible/README.md) first.
2. **Proxmox root password** — used by the Proxmox provider for `root@pam` API authentication and provider-managed SSH operations; Terraform has no host-configuration `remote-exec` resources.
3. **AWS account** with an IAM role that can manage the state and lockfile objects. Local AWS credentials need `sts:AssumeRole` on that role.
4. **Administration tools:** Ansible Core 2.16+, `terraform >= 1.7`, `terragrunt`, `talosctl`, `kubectl`, and `helm`.
5. **For Windows VM install:** `Win11_25H2_*.iso` + `virtio-win.iso` (v0.1.271) uploaded to the largegpu host's `local` ISO datastore.

### Deploy

```bash
# 1. Populate .env at the repo root:
#      PROXMOX_HOST=192.168.1.106
#      PROXMOX_SSH_PASSWORD="..."
#      AWS_IAM_ROLE=arn:aws:iam::<acct>:role/TerragruntExecutionRole
#      AWS_ACCESS_KEY_ID=...
#      AWS_SECRET_ACCESS_KEY=...

# 2. Edit prod/config.yml for Talos nodes, or
#    prod/windows-workstation/config.yml for Windows PCI/USB settings.

# 3. Plan + apply via the wrapper script.
./terraform/run-terragrunt.sh prod homelab-cluster plan
./terraform/run-terragrunt.sh prod homelab-cluster apply

./terraform/run-terragrunt.sh prod windows-workstation plan
./terraform/run-terragrunt.sh prod windows-workstation apply

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

Both VMs are always present in state. Drain the Talos worker before switching
to Windows, and wait for each source VM to stop completely before starting the
other one:

```bash
# Run Windows for gaming
kubectl drain gpu-2 --ignore-daemonsets --delete-emptydir-data
ssh root@192.168.1.107 'qm shutdown 402 && qm wait 402 --timeout 180 && qm start 502'

# Back to Talos K8s GPU worker
ssh root@192.168.1.107 'qm shutdown 502 && qm wait 502 --timeout 180 && qm start 402'
kubectl wait --for=condition=Ready node/gpu-2 --timeout=5m
kubectl uncordon gpu-2
```

## Conventions and design decisions

- **One module call per node role** (control-plane, worker, GPU, Windows) using `for_each` over map variables. Add a node by adding a map entry — no new module blocks needed.
- **Talos config patches are layered**, not monolithic. Base network config is generated inline; GPU-specific config lives in a separate YAML file for readability.
- **Cilium is bootstrap infrastructure, not a GitOps application.** Terraform installs it after Talos bootstrap and before cluster health because ArgoCD cannot start without pod networking. Cilium replaces kube-proxy and provides LB IPAM plus L2 announcements; MetalLB must not be installed.
- **ArgoCD bootstrap is intentionally minimal.** Terraform installs ArgoCD once with a `ClusterIP` server service and `server.insecure = true`; the Argo-managed Cilium Gateway terminates TLS and provides private ingress. Helm waits for readiness because Cilium and its address pool already exist. All further ArgoCD configuration goes through GitOps.
- **State contains secrets.** `talos_machine_secrets` stores cluster PKI in Terraform state — that's why the S3 bucket has SSE enabled and the role policy is tightly scoped.
- **VM IDs are explicit.** 200s = control-plane, 300s = workers, 400s = GPU, 500s = Windows, 9000s = templates. Keeps the Proxmox UI organized and avoids collisions.
- **IP addresses use CIDR notation** (`192.168.1.232/24`) in variables. Modules use `split("/", ip)[0]` to extract the bare IP and parse the prefix for routing.
- **All VMs use `cpu.type = "host"`** — required for GPU passthrough, best performance everywhere else.
- **The `largegpu` mutex is enforced at runtime, not config time.** Two VMs sharing one GPU = one runs, the other can't start. This lets you flip between them in seconds with no Terraform churn.
- **Bulk media storage is NTFS+NFS, not Ceph/Longhorn.** The 10 TB drive on smallgpu has existing NTFS data worth preserving. Ansible safety-checks and mounts it with the kernel `ntfs3` driver, then manages its NFSv4 export.
- **Two storage tiers are split by host permanence, not performance.** Critical data binds to `storage2-bulk-pv` on Ubuntu; reproducible bulk data binds to `storage1-bulk-pv` on borrowed `smallgpu`.
- **VM sizing follows per-host headroom.** Production declares one `2 vCPU / 4 GiB` control plane on `largegpu` and two GPU workers. The 52 GiB RTX 3080/Windows allocation leaves room for `cp-1` in either runtime mode; `gpu-3` has 12 GiB on `smallgpu`. The Ubuntu workstation runs no Kubernetes VM.
