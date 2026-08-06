# Tailscale subnet-router runbook

The cluster runs one kernel-mode Tailscale subnet router. Tailscale's hosted
control plane coordinates the tailnet; the router advertises `192.168.1.0/24`
so enrolled clients can reach LAN hosts and Cilium LoadBalancer addresses.

Public services such as Jellyfin are intentionally separate. They remain behind
Cloudflare Tunnel and do not require Tailscale.

## 1. Configure the tailnet policy

Create the `tag:router` tag and allow it to auto-approve the LAN route in the
Tailscale admin console's Access controls page. Replace the example login with
the identity that should administer the tag:

```hujson
{
  "tagOwners": {
    "tag:router": ["autogroup:admin"],
  },
  "autoApprovers": {
    "routes": {
      "192.168.1.0/24": ["tag:router"],
    },
  },
  "grants": [
    {
      "src": ["autogroup:member"],
      "dst": ["192.168.1.0/24"],
      "ip": ["*:*"],
    },
  ],
}
```

Tighten the grant when more users join the tailnet. Route approval makes the
route available; grants independently decide who may send traffic through it.

## 2. Create the router auth Secret

In the Tailscale admin console, open **Settings → Keys → Generate auth key**.
Create a key with these properties:

- tag: `tag:router`
- reusable: enabled
- pre-approved: enabled if device approval is enabled
- ephemeral: disabled

Store it out-of-band; never commit the key:

```bash
kubectl create namespace tailscale-router --dry-run=client -o yaml \
  | kubectl apply -f -

kubectl -n tailscale-router create secret generic tailscale-auth \
  --from-literal=TS_AUTHKEY='tskey-auth-REPLACE_ME' \
  --dry-run=client -o yaml | kubectl apply -f -
```

ArgoCD ignores the Secret's data. The router stores its long-lived node identity
in the separate `tailscale-router-state-official` Secret, so the auth key is
normally used only for initial enrollment. The distinct name deliberately
prevents reuse of state created against the previous control plane.

After the Application syncs:

```bash
kubectl -n tailscale-router rollout status deploy/tailscale-router
kubectl -n tailscale-router logs deploy/tailscale-router
```

If `autoApprovers` was not configured before the router enrolled, approve
`192.168.1.0/24` manually from **Machines → k8s-router → Subnets**.

## 3. Connect clients

Ubuntu:

```bash
sudo tailscale logout
sudo tailscale up --accept-routes --accept-dns
tailscale status
```

The login URL opens the official Tailscale identity flow. Do not specify a
custom `--login-server`.

On Android/Samsung DeX, open the Tailscale app, remove or sign out of any
alternate-server account, then sign in normally to the same official tailnet.
Android accepts subnet routes automatically.

## 4. Private application DNS

AdGuard Home at `192.168.1.221` resolves `*.home.547600.xyz` to the private
Cilium Gateway at `192.168.1.220`. Configure it as Tailscale's restricted
nameserver for `home.547600.xyz`; do not create public Cloudflare records for
this zone.

Follow [the AdGuard Home runbook](adguard-home-runbook.md) for initial setup,
split DNS, verification, and the later opt-in to tailnet-wide ad blocking.

Verify from a remote client:

```bash
tailscale status
getent hosts argocd.home.547600.xyz
curl -I https://argocd.home.547600.xyz
```

## Operations

```bash
# Router health
kubectl -n tailscale-router get pod
kubectl -n tailscale-router logs deploy/tailscale-router

# Re-enroll only if the state Secret was lost or the machine was deleted
kubectl -n tailscale-router delete secret tailscale-router-state-official
kubectl -n tailscale-router rollout restart deploy/tailscale-router
```

Deleting `tailscale-router-state-official` creates a new node identity. Remove
the old `k8s-router` machine from the Tailscale admin console afterward.

The namespace intentionally enforces the privileged Pod Security level because
kernel networking requires `NET_ADMIN`, `NET_RAW`, and `/dev/net/tun`. Tailnet
grants restrict remote access; Kubernetes NetworkPolicies should separately
limit pod-to-pod traffic where applicable.
