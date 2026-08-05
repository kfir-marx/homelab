# Remote access strategy

The homelab uses two independent hosted services:

- **Cloudflare Tunnel** exposes intentionally public applications such as
  Jellyfin.
- **Official Tailscale** provides private administrative access through a
  kernel-mode subnet router in Kubernetes and tailnet-only application ingress
  through the Tailscale Kubernetes Operator.

Using Tailscale's hosted control plane removes a public coordination-server
dependency from the homelab and provides managed coordination and DERP relay
infrastructure.

## Architecture

```text
Family and friends
        |
        | HTTPS: jellyfin.547600.xyz
        v
Cloudflare edge --- outbound Cloudflare Tunnel ---> Jellyfin service

Administrator devices
        |
        | Official Tailscale control plane coordinates WireGuard peers
        v
Tailscale tailnet ---> k8s-router advertises 192.168.1.0/24
                                |
                                +--> ArgoCD Gateway: 192.168.1.220
                                +--> AdGuard DNS: 192.168.1.221
                                +--> Kubernetes API: 192.168.1.210:6443
                                +--> Proxmox and other LAN services

Tailscale tailnet ---> dedicated operator ingress proxy
                                |
                                +--> Bitwarden ClusterIP: HTTPS, tailnet only
```

The two paths do not depend on one another. A Cloudflare Tunnel outage does not
change private administration, and the public Jellyfin path does not traverse
the tailnet.

## Cloudflare Tunnel: public services

The in-cluster `cloudflared` deployment maintains outbound connections to
Cloudflare. No inbound port forwarding or public home IP is required.

| Service | Public hostname | Internal target | Authentication |
|---------|-----------------|-----------------|----------------|
| Jellyfin | `jellyfin.547600.xyz` | `jellyfin.media.svc:8096` | Jellyfin account |

Add only applications intended for external users to the tunnel. Do not put
ArgoCD, Proxmox, the Kubernetes API, or other administrative endpoints on it.
Cloudflare Access is not placed in front of Jellyfin because browser redirects
are incompatible with many native TV and mobile clients.

## Official Tailscale: private administration

The `tailscale-router` Deployment joins the official tailnet as
`k8s-router` with `tag:router`. It advertises `192.168.1.0/24`, covering the
LAN and the Cilium LoadBalancer pool. The router runs in kernel mode with a real
`tailscale0` interface; encrypted traffic is normally peer-to-peer and falls
back to Tailscale's managed DERP relays when direct connectivity is unavailable.

| Service | Private address | Access path |
|---------|-----------------|-------------|
| ArgoCD | `http://argocd.home.547600.xyz` / `192.168.1.220:80` | Cilium Gateway API |
| AdGuard Home UI | `http://adguard.home.547600.xyz` / `192.168.1.220:80` | Cilium Gateway API |
| AdGuard Home DNS | `192.168.1.221:53` TCP/UDP | Cilium LoadBalancer |
| Kubernetes API | `192.168.1.210:6443` | API VIP |
| Proxmox | `https://192.168.1.106:8006` and `https://192.168.1.107:8006` | LAN subnet route |
| Other LAN services | `192.168.1.0/24` | LAN subnet route |

Bitwarden is intentionally different from the services in this table. The
Tailscale Kubernetes Operator gives it a dedicated MagicDNS name and HTTPS
proxy, so it is not reachable directly from the LAN and does not use the shared
Cilium Gateway:

| Service | Tailnet-only address | Access path |
|---------|----------------------|-------------|
| Bitwarden | `https://bitwarden.ghoul-slowworm.ts.net` | Tailscale L7 ingress; tailnet grant to `tag:bitwarden` |

AdGuard Home privately resolves `*.home.547600.xyz` to `192.168.1.220`.
Cilium Gateway API then routes each exact hostname to its backend service.
DNS does not grant access: the private address is reachable only from the LAN
or through the Tailscale route. Tailnet grants determine which enrolled users
and devices may use that route.

Tailscale's control plane sees tailnet device and coordination metadata, but
application traffic remains WireGuard encrypted between peers.

## Security boundaries

- Cloudflare Tunnel is limited to explicitly public services.
- Bitwarden has no Cloudflare rule, LAN Gateway route, LoadBalancer, NodePort,
  or Funnel. Its external boundary is the Tailscale ingress proxy.
- Tailscale device enrollment, tags, route approval, and grants are managed in
  the official admin console.
- `tag:router` owns the subnet-router identity; it should receive only the
  access needed to route the LAN.
- Route approval and access grants are separate controls. Approving
  `192.168.1.0/24` does not by itself authorize every tailnet identity.
- The router namespace uses privileged Pod Security because kernel Tailscale
  needs `NET_ADMIN`, `NET_RAW`, and `/dev/net/tun`.
- Kubernetes NetworkPolicies remain useful for pod traffic, but Tailscale
  grants are the primary policy boundary for clients entering through the
  subnet router.
- Auth keys and Cloudflare credentials are created out-of-band and never
  committed to Git.

## Private DNS and ad blocking

Tailscale MagicDNS automatically names tailnet devices but cannot host
arbitrary application records. AdGuard Home is the restricted resolver for
`home.547600.xyz` and provides the wildcard mapping:

```text
*.home.547600.xyz -> 192.168.1.220
```

The resolver itself is reached at `192.168.1.221` through the approved subnet
route. After private-zone resolution is stable, it can also become Tailscale's
global nameserver to provide DNS-level ad and tracker blocking. This is staged
because a single in-cluster global resolver makes cluster availability a
dependency of public DNS resolution.

## Operations

See [Tailscale subnet-router runbook](tailscale-runbook.md) for tailnet policy,
auth Secret creation, route approval, client enrollment, and troubleshooting.
See [AdGuard Home runbook](adguard-home-runbook.md) for storage preparation,
initial setup, split DNS, ad blocking, and resilience.
See [Bitwarden runbook](bitwarden-runbook.md) for Tailscale Operator bootstrap,
critical storage, LastPass migration, and backup handling.

ArgoCD manages:

- `kubernetes/apps/cloudflared.yaml`
- `kubernetes/apps/tailscale-router.yaml`
- `kubernetes/apps/argocd-private-access.yaml`
- `kubernetes/apps/adguard-home.yaml`
- `kubernetes/apps/tailscale-operator.yaml`
- `kubernetes/apps/bitwarden.yaml`

The official Tailscale and Cloudflare dashboards manage their respective
hosted control-plane configuration.
