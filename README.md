# Homelab — GitOps Kubernetes on Proxmox

Fully automated, GitOps-driven Kubernetes cluster running Talos Linux on a 6-node Proxmox hypervisor fleet. Infrastructure is provisioned with Terraform; in-cluster workloads are managed by ArgoCD.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Cluster design, node specs, network layout, Terraform modules, CI/CD pipeline, GPU passthrough |
| [Remote Access](docs/remote-access.md) | Cloudflare Tunnel (public services) + Headscale (private VPN) setup and design decisions |

## Repository Structure

```
.
├── docs/                  # Documentation
├── terraform/             # VM provisioning + cluster bootstrap
├── kubernetes/            # ArgoCD-managed app manifests
├── .github/workflows/     # CI: lint, validate, security scan, plan
└── atlantis.yaml          # PR-driven terraform plan/apply
```
