# Homelab — GitOps Kubernetes on Proxmox

GitOps-driven Kubernetes cluster running Talos Linux on a three-host Proxmox
fleet. Ansible configures the physical Proxmox hosts, Terraform provisions VMs
and bootstraps Talos, and ArgoCD manages in-cluster workloads.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Cluster design, node specs, network layout, Terraform modules, CI/CD pipeline, GPU passthrough |
| [Remote Access](docs/remote-access.md) | Cloudflare Tunnel (public services) + Headscale (private VPN) setup and design decisions |
| [Proxmox Ansible](ansible/README.md) | Physical-host repositories, packages, NFS, VFIO, safety checks, and reboot workflow |

## Repository Structure

```
.
├── ansible/               # Physical Proxmox host configuration
├── docs/                  # Documentation
├── terraform/             # VM provisioning + cluster bootstrap
├── kubernetes/            # ArgoCD-managed app manifests
├── .github/workflows/     # CI: lint, validate, security scan, plan
└── atlantis.yaml          # PR-driven terraform plan/apply
```

## Instructions for Agent
The credentials you need are in this file ".env"
The kubeconfig to access the kubernetes cluster is in this file "kubeconfig.yaml"
