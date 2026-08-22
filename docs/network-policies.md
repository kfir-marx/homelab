# Kubernetes network-policy model

Application namespaces use a default-deny model enforced by Cilium. Standard
Kubernetes `NetworkPolicy` resources express portable pod-to-pod rules;
`CiliumNetworkPolicy` is used only where Kubernetes selectors are insufficient:

- the Gateway API data plane has Cilium's reserved `ingress` identity;
- DNS-derived public destinations must follow changing IP addresses;
- the Kubernetes API, nodes, and hosts have reserved Cilium identities;
- public CIDR rules must exclude private and cluster address space.

Argo CD discovers each `network-policy.yaml` in the application's existing
source directory. The default-deny resources use sync wave `1`, so the allow
rules are established first during the initial rollout. No imperative policy
apply step is required.

## Allowed flows

| Namespace/workload | Ingress | Egress |
|---|---|---|
| `adguard-home` | Gateway to TCP/3000; external/LAN, Tailscale router, and forwarding nodes to TCP/UDP 53 through the private DNS VIP | Cluster DNS; named public fallback resolvers on 53; public-only TCP 80/443 for DoH and filter lists |
| `bitwarden/server` | Gateway to TCP/8080 | PostgreSQL TCP/5432; cluster DNS; public-only TCP 80/443 for website icons; `smtp.gmail.com` TCP/587 |
| `bitwarden/backup` | None | PostgreSQL TCP/5432 and cluster DNS |
| `bitwarden/postgres` | Server and backup to TCP/5432 | None |
| `cloudflared` | None | Cluster DNS; Cloudflare tunnel domains on TCP/UDP 7844; Jellyfin on TCP/8096, Seerr on TCP/5055, and Immich on TCP/2283 |
| `immich/server` | Gateway and Cloudflared to TCP/2283 | PostgreSQL 5432, Valkey 6379, ML 3003, cluster DNS, and public-only HTTPS |
| `immich/machine-learning` | Server to TCP/3003 | Cluster DNS and public-only HTTPS for model downloads |
| `immich/postgres` | Server to TCP/5432 | None |
| `job-assistant/api` | Homelab Telegram gateway and Prometheus to TCP/8080 | PostgreSQL, cluster DNS, and SSRF-filtered public HTTP(S) job pages |
| `job-assistant/worker` | None | PostgreSQL, cluster DNS, and Gmail SMTP; no Telegram, external-ai, or Codex egress |
| `job-assistant/generation-broker` | None | Restricted PostgreSQL role, cluster DNS, and external-ai TCP/8080 only |
| `job-assistant/discovery` | None | PostgreSQL, cluster DNS, Gmail IMAP, and configured public ATS APIs |
| `job-assistant/postgres` | Application roles and backup to TCP/5432 | None |
| `homelab-assistant/telegram` | None | Cluster DNS, Kubernetes API HTTPS for read-only diagnostics, Telegram API HTTPS, local LLM TCP/8000, job API TCP/8080, external-ai TCP/8080, and PostgreSQL TCP/5432 |
| `homelab-assistant/inference` | Telegram gateway and node health probes to TCP/8000 | Cluster DNS and pinned-model download hosts on HTTPS |
| `homelab-assistant/postgres` | Telegram gateway to TCP/5432 | Cluster DNS only |
| `external-ai/api` | Homelab gateway, job generation broker, and Prometheus to TCP/8080 | PostgreSQL and cluster DNS only |
| `external-ai/worker` | None | PostgreSQL, cluster DNS, and allowlisted OpenAI/ChatGPT HTTPS only |
| `external-ai/postgres` | external-ai API and worker to TCP/5432 | None |
| `immich/redis` | Server to TCP/6379 | None |
| `media` | Same-namespace application traffic; Gateway to each web UI; Cloudflared to Jellyfin TCP/8096 and Seerr TCP/5055; LAN/world peers to qBittorrent TCP/UDP 51413 | Cluster DNS; public-only TCP 80/443 for metadata, indexers, subtitle providers, and trackers; qBittorrent additionally reaches public TCP/UDP peers |
| `tailscale-router` | Public/LAN UDP 41641 (pinned in the Deployment) | Not isolated; see the Cilium Gateway hairpin exception below |
| `monitoring` | Same-namespace traffic, Gateway to Grafana, and API-server webhook traffic | Not isolated: Prometheus must discover and scrape changing cluster targets |

Return packets for admitted connections are allowed by Cilium's stateful
connection tracking; separate reverse-direction rules are not required. NFS
volume traffic is mounted by the nodes and does not originate from the pods, so
these policies do not change the critical/bulk storage paths.

## Intentional boundaries and exceptions

- The Tailscale router has ingress isolation but no pod egress policy. Cilium
  1.19.6's Gateway L7 load-balancer returns HTTP 403 for pod-to-Gateway hairpin
  traffic from an egress-policy-selected endpoint, including when an exact
  destination identity or `0.0.0.0/0` is allowed. An unconstrained egress rule
  succeeds, so a restrictive egress allow-list cannot currently coexist with
  this kernel-mode subnet-router path. Re-test this exception after Cilium
  upgrades before restoring egress isolation.
- Tailscale grants and the advertised `192.168.1.0/24` route remain the
  authorization boundary for traffic forwarded from tailnet clients. They do
  not constrain a compromised router pod, so keep its image, capabilities,
  service account, and out-of-band auth/state Secrets tightly scoped.
- The router pins tailscaled's WireGuard listener to UDP 41641. Keep that
  setting and the router ingress policy synchronized; an unpinned container
  selects a random listen port that the default-deny policy will block.
- AdGuard admits Cilium's `world` identity only on TCP/UDP 53. With VXLAN,
  Cilium assigns that identity to north/south LoadBalancer traffic even when
  the original client is on the LAN. The resolver remains reachable only on
  private LAN addresses and through the Tailscale subnet route; do not expose
  its LoadBalancer or node ports with public NAT.
- Bitwarden's website-icon service fetches icons from arbitrary vault entry
  domains. Its TCP 80/443 egress excludes private, link-local, loopback, and
  CGNAT ranges so icon fetching cannot be used to reach internal services.
- Monitoring has ingress isolation only. A fixed egress allow-list would
  conflict with Prometheus's cluster-wide service and pod discovery.
- Cilium may not apply ordinary pod policy to host-networked node-exporter
  endpoints. Node and host policy is a separate control-plane change.
- `argocd`, `cert-manager`, `gpu-operator`, and `kube-system` are bootstrap or
  platform namespaces. Their chart-generated API, webhook, node, and registry
  flows must be observed and staged separately before default-deny is safe.
- `arc-systems` and `github-actions-runners` are platform exceptions. ARC needs
  the Kubernetes API and GitHub endpoints, while runner jobs may fetch actions,
  packages, tools, and workflow-selected registries. The privileged runner
  namespace is repository-scoped and documented in the runner runbook.
- Public CIDR rules are IPv4-only because this cluster is currently IPv4-only.
  Add equivalent IPv6 policy before enabling IPv6 pod or service networking.

Cloudflare dashboard configuration is not the source of truth for network
authorization. When a tunnel origin is added or changed, update
`kubernetes/system/cloudflared/network-policy.yaml` in the same change. A
dashboard-only origin will fail closed.

## Rollout and verification

Argo CD will reconcile these policies automatically after merge. Watch one
application at a time and test its positive and negative paths before moving to
the next:

Run the private-application checks from a tailnet client outside
`192.168.1.0/24` as well as from the LAN. A LAN client reaches the Gateway VIP
directly and therefore cannot verify the tailnet-routed Gateway path.

```bash
kubectl get networkpolicy,ciliumnetworkpolicy -A

kubectl -n adguard-home rollout status statefulset/adguard-home
dig @192.168.1.221 example.com
curl --fail https://adguard.home.547600.xyz

kubectl -n bitwarden rollout status deployment/bitwarden
curl --fail https://bitwarden.home.547600.xyz/alive
kubectl -n bitwarden create job --from=cronjob/bitwarden-backup network-policy-test

kubectl -n immich rollout status deployment/immich-server
curl --fail https://immich.home.547600.xyz/api/server/ping

kubectl -n media rollout status deployment/jellyfin
curl --fail https://jellyfin.home.547600.xyz/health
curl --fail https://seerr.home.547600.xyz/api/v1/status

kubectl -n homelab-assistant rollout status deployment/llm --timeout=30m
kubectl -n homelab-assistant rollout status deployment/telegram-gateway

kubectl -n cloudflared logs deployment/cloudflared
kubectl -n tailscale-router logs deployment/tailscale-router
tailscale ping k8s-router

kubectl -n monitoring rollout status deployment/monitoring-grafana
curl --fail https://grafana.home.547600.xyz/api/health
```

Delete the one-off `network-policy-test` Job after checking its result. If an
expected flow is denied, use Hubble to capture the source identity, destination,
and port before broadening a rule:

```bash
hubble observe --verdict DROPPED --since 10m
```

Prefer adding one specific identity/selector and destination port. Do not
replace a failed rule with namespace-wide access, arbitrary destination ports,
or unrestricted private-network egress.
