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
| `bitwarden/server` | Gateway to TCP/8080 | PostgreSQL TCP/5432; cluster DNS; `smtp.gmail.com` TCP/587 |
| `bitwarden/backup` | None | PostgreSQL TCP/5432 and cluster DNS |
| `bitwarden/postgres` | Server and backup to TCP/5432 | None |
| `cloudflared` | None | Cluster DNS; Cloudflare tunnel domains on TCP/UDP 7844; Jellyfin on TCP/8096 and Immich on TCP/2283 |
| `immich/server` | Gateway and Cloudflared to TCP/2283 | PostgreSQL 5432, Valkey 6379, ML 3003, cluster DNS, and public-only HTTPS |
| `immich/machine-learning` | Server to TCP/3003 | Cluster DNS and public-only HTTPS for model downloads |
| `immich/postgres` | Server to TCP/5432 | None |
| `immich/redis` | Server to TCP/6379 | None |
| `tailscale-router` | Public/LAN UDP 41641 | Kubernetes API, cluster DNS, public HTTPS/UDP, and the declared `192.168.1.0/24` subnet route |
| `monitoring` | Same-namespace traffic, Gateway to Grafana, and API-server webhook traffic | Not isolated: Prometheus must discover and scrape changing cluster targets |

Return packets for admitted connections are allowed by Cilium's stateful
connection tracking; separate reverse-direction rules are not required. NFS
volume traffic is mounted by the nodes and does not originate from the pods, so
these policies do not change the critical/bulk storage paths.

## Intentional boundaries and exceptions

- The Tailscale router must reach every protocol and port in
  `192.168.1.0/24`; Tailscale grants remain the per-user authorization boundary
  for this deliberate bridge.
- AdGuard admits Cilium's `world` identity only on TCP/UDP 53. With VXLAN,
  Cilium assigns that identity to north/south LoadBalancer traffic even when
  the original client is on the LAN. The resolver remains reachable only on
  private LAN addresses and through the Tailscale subnet route; do not expose
  its LoadBalancer or node ports with public NAT.
- Monitoring has ingress isolation only. A fixed egress allow-list would
  conflict with Prometheus's cluster-wide service and pod discovery.
- Cilium may not apply ordinary pod policy to host-networked node-exporter
  endpoints. Node and host policy is a separate control-plane change.
- `argocd`, `cert-manager`, `gpu-operator`, and `kube-system` are bootstrap or
  platform namespaces. Their chart-generated API, webhook, node, and registry
  flows must be observed and staged separately before default-deny is safe.
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

```bash
kubectl get networkpolicy,ciliumnetworkpolicy -A

kubectl -n adguard-home rollout status statefulset/adguard-home
dig @192.168.1.221 example.com
curl --fail http://adguard.home.547600.xyz

kubectl -n bitwarden rollout status deployment/bitwarden
curl --fail https://bitwarden.home.547600.xyz/alive
kubectl -n bitwarden create job --from=cronjob/bitwarden-backup network-policy-test

kubectl -n immich rollout status deployment/immich-server
curl --fail https://immich.home.547600.xyz/api/server/ping

kubectl -n cloudflared logs deployment/cloudflared
kubectl -n tailscale-router logs deployment/tailscale-router
tailscale ping k8s-router

kubectl -n monitoring rollout status deployment/monitoring-grafana
curl --fail http://grafana.home.547600.xyz/api/health
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
