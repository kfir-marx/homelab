# Remote Access Strategy

Secure external access to the homelab using two complementary solutions: **Cloudflare Tunnel** for public-facing services and **Headscale** for private admin access.

---

## Design Principles

- **Zero inbound ports** — no port forwarding, no exposed home IP, no static IP required.
- **No client install for streaming users** — friends and family just open a URL.
- **Full mesh VPN for admin** — encrypted WireGuard tunnel for cluster management.
- **Everything managed as code** — Terraform, Helm, ArgoCD.
- **Free tier only** — $0 budget, up to 30 endpoints.

---

## Architecture Overview

```
                        ┌─────────────────────────────────────┐
                        │           Cloudflare Edge            │
                        │                                     │
  Family / Friends ────►│  jellyfin.yourdomain.com  (public)  │◄──── cloudflared pod
  (browser, apps, TV)   │  DDoS protection + TLS termination  │      (outbound tunnel)
                        └─────────────────────────────────────┘          │
                                                                         │
                        ┌─────────────────────────────────────┐          │
                        │           Headscale Server           │          │
                        │       (pod in k8s cluster)           │          │
  Admin (you) ─────────►│  WireGuard mesh VPN                 │          │
  (tailscale client)    │  ACLs, MagicDNS                     │          │
                        └─────────────────────────────────────┘          │
                                         │                               │
                                         ▼                               ▼
                        ┌─────────────────────────────────────────────────┐
                        │              Kubernetes Cluster                  │
                        │                                                 │
                        │  ┌───────────┐ ┌────────┐ ┌────────┐          │
                        │  │ Jellyfin  │ │ Sonarr │ │ Radarr │  ...     │
                        │  │ (public)  │ │ (VPN)  │ │ (VPN)  │          │
                        │  └───────────┘ └────────┘ └────────┘          │
                        │  ┌───────────┐ ┌──────────┐ ┌───────────┐    │
                        │  │ ArgoCD    │ │ Grafana  │ │ Prowlarr  │    │
                        │  │ (VPN)     │ │ (VPN)    │ │ (VPN)     │    │
                        │  └───────────┘ └──────────┘ └───────────┘    │
                        └─────────────────────────────────────────────────┘
```

---

## Cloudflare Tunnel — Public Services

### Purpose

Expose HTTPS services (primarily Jellyfin) to friends and family on any device, from anywhere in the world, without a VPN client.

### How It Works

1. A `cloudflared` pod in the cluster maintains a persistent **outbound** connection to Cloudflare's edge.
2. Cloudflare DNS points `jellyfin.yourdomain.com` to Cloudflare's edge (home IP stays hidden).
3. Incoming requests from users hit Cloudflare's edge, flow through the tunnel, and reach the target service inside the cluster.
4. No static IP, no public IP, no port forwarding required. Works behind CGNAT.

### Traffic Flow

```
User's device (anywhere)
    │
    │  HTTPS request to jellyfin.yourdomain.com
    ▼
Cloudflare Edge (104.x.x.x)
    │  DNS resolves to Cloudflare, NOT your home IP
    │
    │  Routes through existing tunnel
    ▼
cloudflared pod (in-cluster)
    │
    │  Forwards to internal service
    ▼
jellyfin.media.svc:8096
    │
    │  Jellyfin handles auth (username/password)
    ▼
Stream video back through the same path
```

### Services Exposed via Cloudflare Tunnel

| Service | Public hostname | Internal target | Auth |
|---------|----------------|-----------------|------|
| Jellyfin | `jellyfin.yourdomain.com` | `jellyfin.media.svc:8096` | Jellyfin built-in (username/password) |

Add more services by adding ingress rules to the `cloudflared` config. Only expose services that are intended for external users.

### Security Model

- **No open inbound ports** — `cloudflared` only makes outbound connections.
- **Home IP hidden** — DNS points to Cloudflare, not your residential IP.
- **DDoS protection** — Cloudflare absorbs volumetric attacks at the edge.
- **TLS termination** — Cloudflare handles HTTPS certificates automatically.
- **Jellyfin authentication** — built-in username/password login. No Cloudflare Access in front (incompatible with native TV/mobile apps).
- **WAF rules** (optional) — rate-limit login attempts on the free tier.

### Why Not Cloudflare Access?

Cloudflare Access uses a browser-based redirect flow for authentication. Native apps (Android TV, iOS, Fire Stick, Roku) cannot handle this redirect — they make API calls and expect JSON responses, not HTML login pages. Jellyfin's built-in auth works on every client.

### IaC Management

- **Terraform**: Cloudflare provider (`cloudflare/cloudflare`) manages tunnels, DNS records, and WAF rules.
- **ArgoCD**: `cloudflared` deployment manifest in `kubernetes/apps/` or `kubernetes/system/`.

### Requirements

- A domain name (managed through Cloudflare DNS).
- A Cloudflare account (free tier).
- `cloudflared` container image deployed in the cluster.

---

## Headscale — Private Admin Access

### Purpose

Secure WireGuard mesh VPN for administrative access to cluster services (ArgoCD, Proxmox, Sonarr, Radarr, Grafana, etc.) when away from the home network.

### How It Works

1. Headscale runs as a pod in the cluster — it is the self-hosted replacement for Tailscale's coordination server.
2. Your devices (laptop, phone) run the standard Tailscale client, pointed at your Headscale instance.
3. Headscale coordinates key exchange and NAT traversal. WireGuard handles the actual encrypted tunnel.
4. Once connected, your device can reach all cluster services on their internal IPs/DNS names.

### Traffic Flow

```
Your laptop (coffee shop, hotel, abroad)
    │
    │  Tailscale client → connects to Headscale coordination server
    │  WireGuard tunnel established (peer-to-peer or via DERP relay)
    ▼
Headscale pod (in-cluster)
    │
    │  Coordinates peers, distributes keys
    │  Actual traffic flows direct (WireGuard P2P) when possible
    ▼
Internal services: ArgoCD, Proxmox, Sonarr, Radarr, Grafana, kubectl API
```

### Services Accessible via Headscale

| Service | Internal address | Purpose |
|---------|-----------------|---------|
| ArgoCD | `argocd.argocd.svc` | GitOps dashboard |
| Proxmox | `10.0.10.1:8006` (or host IPs) | Hypervisor management |
| Sonarr | `sonarr.media.svc` | TV show management |
| Radarr | `radarr.media.svc` | Movie management |
| Prowlarr | `prowlarr.media.svc` | Indexer management |
| Bazarr | `bazarr.media.svc` | Subtitle management |
| Grafana | `grafana.monitoring.svc` | Observability |
| K8s API | `10.0.10.100:6443` | kubectl access |

### Security Model

- **WireGuard encryption** — state-of-the-art cryptography (Noise protocol, ChaCha20, Poly1305).
- **Self-hosted control plane** — no third party sees your peer list or network topology.
- **ACLs** — define which devices can reach which services.
- **MagicDNS** — human-friendly names for internal services.
- **Only you** — no need to onboard friends/family to the VPN.

### IaC Management

- **Helm/ArgoCD**: Headscale deployed as a Kubernetes workload via ArgoCD.
- **Configuration**: Headscale config managed as a ConfigMap or values file in Git.
- **Ansible** (optional): For bootstrapping Tailscale clients on your personal devices.

### Requirements

- Headscale server deployed in the cluster.
- Tailscale client on your personal devices (laptop, phone).
- DERP relay for NAT traversal when P2P isn't possible (can self-host or use Tailscale's public DERP servers).

---

## Decision Matrix

| Concern | Cloudflare Tunnel | Headscale |
|---------|------------------|-----------|
| **Use case** | Public-facing services | Private admin access |
| **Users** | Friends, family, you | You only |
| **Client required** | No (just a browser or Jellyfin app) | Yes (Tailscale client) |
| **Protocol** | HTTPS only (free tier) | Any TCP/UDP (full VPN) |
| **Control plane** | Cloudflare (third party) | Self-hosted (you own it) |
| **Home IP exposed** | No | No |
| **Static IP required** | No | No |
| **Port forwarding required** | No | No |
| **Cost** | Free (50 users) | Free (unlimited) |
| **Terraform provider** | Yes (official) | No (API + Helm) |
| **Talos/K8s deployment** | Pod (`cloudflared`) | Pod (Headscale server) |

---

## Domain Requirements

Cloudflare Tunnel requires a domain managed through Cloudflare DNS. Options:

- Register a cheap domain (e.g. `.uk` ~$1/yr) and transfer DNS to Cloudflare.
- Cloudflare Registrar offers at-cost domain pricing (no markup).

The domain is used only for Cloudflare Tunnel services. Headscale uses MagicDNS internally and does not need a public domain.
