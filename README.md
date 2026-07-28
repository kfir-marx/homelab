# Homelab — GitOps Kubernetes on Proxmox and libvirt

GitOps-driven Kubernetes cluster running Talos Linux across two Proxmox hosts
and one Ubuntu QEMU/KVM workstation. Ansible configures the physical hosts,
Terraform provisions both Proxmox and libvirt VMs and bootstraps Talos, and
ArgoCD manages in-cluster workloads.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Cluster design, node specs, network layout, Terraform modules, CI/CD pipeline, GPU passthrough |
| [Remote Access](docs/remote-access.md) | Cloudflare Tunnel for public services + official Tailscale for private admin access |
| [Tailscale Runbook](docs/tailscale-runbook.md) | Subnet-router enrollment, route approval, client setup, and troubleshooting |
| [AdGuard Home Runbook](docs/adguard-home-runbook.md) | Private split DNS, tailnet ad blocking, initial setup, and recovery |
| [Monitoring Runbook](docs/monitoring-runbook.md) | Prometheus/Grafana deployment, NFS storage, private access, and verification |
| [GPU Operator Runbook](docs/gpu-operator-runbook.md) | Talos NVIDIA prerequisites, ArgoCD rollout, GPU validation, and metrics |
| [Ubuntu Workstation Runbook](docs/ubuntu-workstation-runbook.md) | Rebuild `192.168.1.105` as the libvirt/VFIO Talos host and restore critical NFS |
| [Proxmox Ansible](ansible/README.md) | Physical-host repositories, packages, NFS, VFIO, safety checks, and reboot workflow |

## Repository Structure

```
.
├── ansible/               # Physical Proxmox + Ubuntu host configuration
├── docs/                  # Documentation
├── terraform/             # VM provisioning + cluster bootstrap
├── kubernetes/            # ArgoCD-managed app manifests
├── .github/workflows/     # CI: lint, validate, security scan, plan
└── atlantis.yaml          # PR-driven terraform plan/apply
```

## Instructions for Agent
The credentials you need are in this file ".env"
The kubeconfig to access the kubernetes cluster is in this file "kubeconfig.yaml"
