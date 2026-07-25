# AdGuard Home private DNS and ad-blocking runbook

AdGuard Home provides:

- private DNS for `*.home.547600.xyz`;
- DNS-level ad and tracker blocking for tailnet devices;
- the wildcard mapping from private application names to the shared Cilium
  Gateway at `192.168.1.220`.

The DNS service uses Cilium LoadBalancer VIP `192.168.1.221`. Its admin UI is
available only through the private Gateway at
`http://adguard.home.547600.xyz`.

Cilium L2 announcements use `externalTrafficPolicy: Cluster`, and Tailscale's
subnet router normally source-NATs routed clients. AdGuard Home may therefore
group several requests under a node or router address instead of showing every
tailnet device's original address. DNS filtering and private resolution are
not affected.

AdGuard Home is deliberately a single-replica StatefulSet. Its writable
configuration and query statistics cannot safely be shared by active replicas.
Until a second resolver is deployed outside Kubernetes, using it as a global
resolver makes cluster availability a dependency of tailnet DNS.

## 1. Prepare persistent storage

Create the retained NFS directory on the critical storage host before ArgoCD
syncs the application:

```bash
ssh root@192.168.1.105
mkdir -p /mnt/storage2-bulk/adguard-home
chmod 700 /mnt/storage2-bulk/adguard-home
exit
```

The pod creates its `conf` and `work` children. The PV uses the existing
`no_root_squash` NFS export and has a `Retain` reclaim policy.

## 2. Deploy through ArgoCD

Commit and push the repository changes. The root ArgoCD application should
discover `kubernetes/apps/adguard-home.yaml`. If the app-of-apps has not synced
yet, apply only the Application object:

```bash
kubectl apply -f kubernetes/apps/adguard-home.yaml
```

Wait for storage, the pod, and Cilium's LoadBalancer allocation:

```bash
kubectl -n adguard-home get pvc
kubectl -n adguard-home rollout status statefulset/adguard-home
kubectl -n adguard-home get service adguard-home-dns
```

The DNS Service must report external IP `192.168.1.221`.

## 3. Complete the one-time setup wizard

Use a port-forward for the first setup so DNS does not need to work yet:

```bash
kubectl -n adguard-home port-forward service/adguard-home-web 3000:3000
```

Open `http://127.0.0.1:3000` and configure:

- admin web interface: all interfaces, port `3000`;
- DNS server: all interfaces, port `53`;
- a unique, strong administrator password;
- DHCP server: disabled.

Keep the web interface on port `3000`. The ClusterIP Service and private
`HTTPRoute` intentionally target that port.

After the wizard finishes, stop the port-forward and verify:

```bash
kubectl -n adguard-home logs statefulset/adguard-home
dig @192.168.1.221 example.com
```

## 4. Configure private application DNS

In AdGuard Home, open **Filters → DNS rewrites** and add:

| Domain | Answer |
|--------|--------|
| `*.home.547600.xyz` | `192.168.1.220` |

This one wildcard sends all private web application names to the shared Cilium
Gateway. Each Kubernetes `HTTPRoute` still declares its exact hostname and
selects the correct backend.

Test the rewrite directly:

```bash
dig @192.168.1.221 argocd.home.547600.xyz
dig @192.168.1.221 adguard.home.547600.xyz
```

Both answers must contain `192.168.1.220`.

## 5. Enable Tailscale split DNS first

In the Tailscale admin console, open **DNS → Nameservers → Add nameserver →
Custom**:

- nameserver: `192.168.1.221`;
- enable **Restrict to domain**;
- domain: `home.547600.xyz`.

Keep MagicDNS enabled. Ensure clients use Tailscale DNS settings; on Ubuntu:

```bash
sudo tailscale set --accept-routes=true --accept-dns=true
```

Test from a tailnet client away from the home LAN:

```bash
getent hosts argocd.home.547600.xyz
curl -I http://argocd.home.547600.xyz
curl -I http://adguard.home.547600.xyz
```

This stage changes only the private zone. If Kubernetes or AdGuard Home is
unavailable, public internet DNS on the client continues to use its normal
resolver.

## 6. Enable tailnet-wide ad blocking

In AdGuard Home:

1. Confirm **Filters → DNS blocklists** has at least the default AdGuard DNS
   filter enabled.
2. Confirm upstream resolution works. Encrypted upstreams such as
   `https://dns10.quad9.net/dns-query` may be configured under
   **Settings → DNS settings**.
3. Check the query log while testing a known advertising or tracking domain.

After several days of stable private-zone operation, return to Tailscale's DNS
page and configure `192.168.1.221` as a global nameserver, then enable
**Override DNS servers**. If the console does not allow the same address as
both restricted and global, remove the restricted entry when adding the global
one; AdGuard Home still answers the private wildcard.

Do not add an unfiltered public resolver as a second global nameserver. Clients
can use it in parallel and bypass filtering.

DNS filtering cannot remove ads served from the same domains as wanted content,
including many video-platform ads. Keep browser content blocking where needed.

## 7. Optional LAN-wide filtering

Do not change the router's DHCP-provided DNS until a second resolver exists.
Once redundant DNS is available, the router can advertise the AdGuard
addresses to LAN clients as well as Tailscale clients.

The preferred second resolver is outside Kubernetes on an always-on VM,
router, or small computer. Give it a Tailscale identity or otherwise ensure it
is reachable independently of the Kubernetes subnet router, synchronize its
AdGuard configuration, and configure both filtered resolvers in Tailscale.

## Operations

```bash
# Workload and service health
kubectl -n adguard-home get pod,pvc,service
kubectl -n adguard-home logs statefulset/adguard-home

# DNS tests from the LAN
dig @192.168.1.221 argocd.home.547600.xyz
dig @192.168.1.221 example.com

# Gateway route status
kubectl -n adguard-home get httproute adguard-home
kubectl -n argocd get gateway private
```

Back up `/mnt/storage2-bulk/adguard-home` regularly. The directory contains
filter configuration, the administrator password hash, client information, and
query statistics; do not publish it in Git.

After verifying the private names, remove the obsolete Cloudflare DNS-only
record for `argocd.547600.xyz`. No Cloudflare record is required for
`*.home.547600.xyz`.
