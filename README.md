# Homelab — GitOps Kubernetes on Proxmox

<div align="center">

**A production-minded Kubernetes platform built from repurposed hardware.**

Talos Linux · Proxmox VE · Terraform/Terragrunt · Ansible · Argo CD · Cilium

![Infrastructure as Code](https://img.shields.io/badge/infrastructure-Terraform-844FBA?logo=terraform&logoColor=white)
![Configuration](https://img.shields.io/badge/configuration-Ansible-EE0000?logo=ansible&logoColor=white)
![Kubernetes](https://img.shields.io/badge/platform-Kubernetes-326CE5?logo=kubernetes&logoColor=white)
![GitOps](https://img.shields.io/badge/delivery-Argo%20CD-EF7B4D?logo=argo&logoColor=white)
![Talos Linux](https://img.shields.io/badge/node%20OS-Talos%20Linux-FF7300)

</div>

This repository is the source of truth for my homelab: physical-host
configuration, virtual machines, a Talos Kubernetes cluster, networking,
storage, GPU workloads, observability, and self-hosted applications. The goal
is not simply to run services at home, but to operate a small platform with the
same practices I would bring to a production environment: clear ownership,
reviewable infrastructure changes, immutable nodes, GitOps reconciliation,
explicit recovery procedures, and documented trade-offs.

## At a glance

| | |
|---|---|
| **Compute** | Two Proxmox VE hosts and one Ubuntu workstation |
| **Kubernetes** | Talos Linux with one control plane and two GPU-capable workers |
| **Accelerators** | NVIDIA RTX 3080 and RTX 2060 passed through to Talos VMs |
| **Storage** | 10 TB bulk tier, 800 GB critical-data tier, and disposable local GPU scratch |
| **Networking** | Cilium CNI, Gateway API, LAN load-balancer IPs, Tailscale, and Cloudflare Tunnel |
| **Delivery** | Self-hosted GitHub Actions runners, Terraform/Terragrunt, and Argo CD reconciliation |
| **Operations** | Ansible-managed hosts, Prometheus, Alertmanager, Grafana, runbooks, and PR plans |

## Architecture

```mermaid
flowchart TB
    Git["Git repository"]
    CI["GitHub Actions<br/>format · security scan · plan"]
    Ansible["Ansible<br/>physical-host configuration"]
    IaC["Terraform + Terragrunt<br/>VMs · Talos · bootstrap"]
    Argo["Argo CD<br/>automated sync · prune · self-heal"]

    subgraph Physical["Physical layer"]
        Small["smallgpu · Proxmox VE 9<br/>RTX 2060 · 10 TB bulk NFS"]
        Large["largegpu · Proxmox VE 9<br/>RTX 3080 · Windows/Talos mutex"]
        Ubuntu["Ubuntu workstation<br/>GTX 1060 desktop · 800 GB critical NFS"]
        Assistant["Private Telegram client<br/>thread selection · deterministic ops"]
        Codex["Codex App Server<br/>local Unix socket · workstation identity"]
        Actuator["Fixed VM actuator<br/>402/502 mutex"]
    end

    subgraph Cluster["Talos Kubernetes"]
        CP["cp-1<br/>control plane"]
        GPU2["gpu-2<br/>RTX 3080 worker"]
        GPU3["gpu-3<br/>RTX 2060 mixed worker"]
        Platform["Cilium · Gateway API · GPU Operator"]
        Apps["Immich · Jellyfin · Media automation · AdGuard Home · Monitoring"]
    end

    Git --> CI
    Git --> Ansible
    Git --> IaC
    Git --> Argo
    Ansible --> Small
    Ansible --> Large
    Ansible --> Ubuntu
    Ubuntu --> Assistant --> Codex
    Assistant -. fixed operations .-> Actuator --> Large
    Codex -. authorized tasks .-> Platform
    IaC --> CP
    IaC --> GPU2
    IaC --> GPU3
    Small -. hosts .-> GPU3
    Large -. hosts .-> CP
    Large -. hosts .-> GPU2
    Argo --> Platform --> Apps
    Small -. bulk storage .-> Apps
    Ubuntu -. critical storage .-> Apps
```

The platform is intentionally split into three ownership layers:

1. **Ansible owns physical hosts** — Proxmox repositories, storage, NFS,
   backups, UPS integration, and VFIO prerequisites, plus the Ubuntu NFS
   workstation.
2. **Terraform/Terragrunt owns virtual infrastructure** — Proxmox VMs, PCIe
   passthrough, Talos machine configuration, cluster bootstrap, Cilium, and the
   initial Argo CD installation.
3. **Argo CD owns in-cluster workloads** — applications, platform controllers,
   routes, and static storage resources continuously reconciled from Git.

This boundary prevents two tools from trying to manage the same resource and
makes failures easier to reason about.

## What the lab runs

| Capability | Implementation | Why it is here |
|---|---|---|
| Private photo platform | Immich with PostgreSQL and Redis | A real stateful workload whose irreplaceable data must stay on permanent hardware |
| Private AI assistants | Workstation Telegram client for the existing local Codex environment, plus job-assistant and external-ai | Reuses Codex threads, tools, skills, and configuration while keeping deterministic VM switching outside the model |
| Home streaming | Jellyfin, Seerr, Servarr, Bazarr, qBittorrent, and Maintainerr | GPU-accelerated streaming with automated subtitles, recoverable bulk media, and policy-driven retention |
| Observability | kube-prometheus-stack, Grafana, Alertmanager, and NVIDIA DCGM metrics | Cluster, workload, and GPU visibility with explicit retention limits |
| Private DNS | AdGuard Home | Split DNS for private services and optional network-wide filtering |
| Private remote access | Official Tailscale subnet router | Administrative access without exposing management interfaces publicly |
| Public application access | Cloudflare Tunnel | Outbound-only application publishing with no router port forwarding |
| GPU scheduling | Talos NVIDIA extensions and NVIDIA GPU Operator | CUDA workloads, device discovery, validation, and GPU telemetry |
| Windows gaming | Separate Terraform stack with an RTX 3080 runtime mutex | Demonstrates safe sharing of constrained physical hardware between workloads |

## Engineering highlights

- **Immutable Kubernetes nodes.** Talos has no general-purpose shell or SSH
  surface; machine configuration and upgrades are API-driven and reproducible.
- **GPU passthrough across layers.** Ansible prepares VFIO on the host,
  Terraform attaches PCIe devices to the correct VM, Talos supplies the
  version-matched NVIDIA extensions, and GPU Operator exposes the devices to
  Kubernetes and Prometheus.
- **Data placement follows failure domains.** Personal data uses the permanent
  Ubuntu workstation. Reproducible media and metrics use the larger borrowed
  host. GPU caches use disposable node-local scratch.
- **Safety is designed in.** Host playbooks validate hardware and filesystems,
  do not format disks, serialize changes, and keep reboots explicit. Static
  Kubernetes volumes use the `Retain` policy.
- **Pull requests show infrastructure impact.** CI checks formatting, runs a
  security scan, and posts Terragrunt plans. Terraform applies run manually
  from outside Kubernetes so cluster recovery never depends on an in-cluster
  runner.
- **Recovery knowledge lives beside the code.** Operational runbooks document
  enrollment, verification, failure modes, and recovery rather than relying on
  memory.

## From commit to cluster

```mermaid
flowchart LR
    Change["Pull request"] --> Checks["Format + tfsec + plan"]
    Checks --> Review["Human review"]
    Review --> Apply["Manual Terragrunt apply<br/>outside Kubernetes"]
    Apply --> Talos["Proxmox VMs + Talos bootstrap"]
    Talos --> Argo["Argo CD app-of-apps"]
    Argo --> Reconcile["Continuous sync + self-heal"]
```

Infrastructure changes and application changes have different deployment
paths. Terraform creates the platform and bootstraps Argo CD; after that, Argo
CD watches [`kubernetes/apps/`](kubernetes/apps/) and reconciles the resources
under [`kubernetes/system/`](kubernetes/system/). The self-hosted Actions
runners live inside that platform, so they are used for CI but are not the
execution boundary for Terraform apply or cluster recovery.

## Deliberate trade-offs

| Decision | Trade-off |
|---|---|
| Single control plane on `largegpu` | Keeps the API available when the less reliable `smallgpu` fails and frees its RAM for applications, but `largegpu` failure causes control-plane downtime |
| Static NFS volumes | Simple and transparent data placement, but no dynamic provisioning and the server remains a dependency |
| Borrowed GPU hosts | Adds substantial compute capacity, but only reproducible data may depend on those machines |
| RTX 3080 shared by Talos and Windows | Maximizes hardware use, but only one VM can own the GPU at a time |
| Public Git repository | Makes the architecture reviewable; plaintext credentials and generated access files stay out of Git, while recovery material is committed only as SOPS/age ciphertext |

These constraints are documented rather than hidden. Host-failure HA still
requires three control-plane nodes across three distinct physical hosts once
the hardware budget supports it.

When `gpu-2` is stopped for Windows, the 12 GiB `gpu-3` VM is the only worker;
the 4 GiB formerly assigned to `cp-1` on `smallgpu` is therefore available to
application workloads. When `smallgpu` itself is down, its worker and bulk NFS
export are unavailable even though the API on `largegpu` remains up.

## Repository tour

```text
.
├── ansible/                  Physical Proxmox and Ubuntu configuration
│   ├── inventory/production Hardware-specific desired state
│   ├── playbooks/            Configure, verify, and explicit reboot entry points
│   └── roles/                NFS, storage, backup, UPS, repositories, and VFIO
├── terraform/
│   ├── deployments/          Environment and stack composition with Terragrunt
│   └── modules/              Talos cluster, Proxmox VM, and Windows VM modules
├── kubernetes/
│   ├── apps/                 Argo CD Application manifests (app-of-apps)
│   └── system/               Workloads, networking, monitoring, and storage
├── secrets/                  SOPS-encrypted disaster-recovery material
├── scripts/secrets.sh        Capture, validate, and restore encrypted secrets
├── docs/                     Architecture notes and operational runbooks
└── .github/workflows/        Pull-request checks and infrastructure plans
```

### A five-minute technical tour

1. Start with the detailed [architecture and design decisions](docs/architecture.md).
2. See the production node model in
   [`terraform/deployments/prod/config.yml`](terraform/deployments/prod/config.yml).
3. Follow VM creation through Talos bootstrap, Cilium, and Argo CD in
   [`terraform/modules/stacks/homelab-cluster/main.tf`](terraform/modules/stacks/homelab-cluster/main.tf).
4. See the GitOps app-of-apps pattern in [`kubernetes/apps/`](kubernetes/apps/).
5. Review the guarded delivery pipeline in
   [`.github/workflows/terraform-plan.yml`](.github/workflows/terraform-plan.yml).

## Documentation

| Document | Focus |
|---|---|
| [Architecture](docs/architecture.md) | Hardware, topology, networking, storage, Terraform modules, CI/CD, and design rationale |
| [Dependency updates](docs/dependency-updates.md) | Renovate safeguards, manual review, Python lockfiles, rollback, and GitHub setup |
| [Secrets disaster recovery](docs/secrets-disaster-recovery.md) | SOPS + age setup, capture, rotation, validation, and bare-metal recovery |
| [Kubernetes network policies](docs/network-policies.md) | Default-deny application isolation, allowed-flow matrix, exceptions, and rollout verification |
| [Remote access](docs/remote-access.md) | Public Cloudflare routes, private Tailscale access, and security boundaries |
| [Tailscale runbook](docs/tailscale-runbook.md) | Subnet-router enrollment, route approval, client setup, and troubleshooting |
| [AdGuard Home runbook](docs/adguard-home-runbook.md) | Private split DNS, tailnet filtering, setup, and recovery |
| [Monitoring runbook](docs/monitoring-runbook.md) | Prometheus/Grafana deployment, storage, access, and verification |
| [Job assistant architecture](docs/job-assistant-architecture.md) | Dedicated Telegram bot target, friend data isolation, trust boundaries, and migration gates |
| [Job assistant runbook](docs/job-assistant-runbook.md) | Private inputs, broker migration, secrets, deployment, restore, and troubleshooting |
| [Homelab assistant runbook](docs/homelab-assistant-runbook.md) | Workstation Codex App Server, Telegram thread UX, Unix-socket boundary, deterministic VM switching, cutover, and rollback |
| [External AI runbook](docs/external-ai-runbook.md) | Durable broker, requester scopes, Codex authentication, rollout, and recovery |
| [GPU Operator runbook](docs/gpu-operator-runbook.md) | Talos NVIDIA prerequisites, rollout, CUDA validation, and metrics |
| [Jellyfin media stack runbook](docs/media-stack-runbook.md) | Backup restoration, storage, GPU transcoding, credential rotation, and cleanup policy |
| [Ubuntu workstation runbook](docs/ubuntu-workstation-runbook.md) | Critical NFS service and local NVIDIA/HDMI recovery |
| [Physical-host automation](ansible/README.md) | Ansible prerequisites, safety behavior, tags, backups, NFS, UPS, and VFIO |

## Security note

Secrets are supplied at runtime through a gitignored `.env`, CI secret stores,
and manually created Kubernetes Secrets. Their disaster-recovery copies are
SOPS-encrypted to an age identity whose private key is itself protected by a
human-held passphrase. Only ciphertext is committed. Kubeconfig, plaintext age
keys, Talos access files, Terraform state, and generated plans remain excluded
from version control. See the [secrets recovery runbook](docs/secrets-disaster-recovery.md).
