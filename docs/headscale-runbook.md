# Headscale operator runbook

Bootstrap order for the three Apps in `kubernetes/apps/`:

1. **cloudflared** (tunnel reaches the cluster from Cloudflare's edge)
2. **headscale** (coordination server, exposed via the tunnel)
3. **tailscale-router** (advertises 192.168.1.0/24 into the mesh)

ArgoCD will create all three Application objects as soon as you merge to `main`, but each needs a one-time secret + an out-of-band setup step before its pods become healthy.

---

## 0. Prerequisites — NFS storage on gpunvdgtx1060

Headscale's PV ([kubernetes/system/headscale/storage.yaml](../kubernetes/system/headscale/storage.yaml)) points at `storage2-bulk` on the `gpunvdgtx1060` host (192.168.1.105). The host-side setup for that export lives in [kubernetes/system/storage/storage2-bulk.yaml](../kubernetes/system/storage/storage2-bulk.yaml) — run those commands once before the StatefulSet will bind.

Then create the headscale subdirectory:

```bash
ssh root@192.168.1.105 \
  'mkdir -p /mnt/storage2-bulk/headscale && chown 1000:1000 /mnt/storage2-bulk/headscale'
```

Verify the export is reachable from a cluster node:

```bash
showmount -e 192.168.1.105
# expect: /mnt/storage2-bulk  192.168.1.0/24
```

---

## 1. Cloudflare side — DNS zone + tunnel

You need the `547600.xyz` zone in Cloudflare. If it's not there yet:

1. Cloudflare dashboard → **Add a site** → `547600.xyz` → Free plan.
2. At your registrar, change the nameservers to the two Cloudflare gave you.
3. Wait for the zone to go **Active** (DNS propagation, usually <1h).

Create the tunnel:

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**.
2. Connector type: `Cloudflared`. Name: `homelab`.
3. Copy the **tunnel token** shown on the install page (a long base64 string starting `eyJ...`). You don't run the install command — the in-cluster deployment uses the token directly.
4. **Public Hostname** tab → **Add a public hostname**:
   - Subdomain: `admin`
   - Domain: `547600.xyz`
   - Service: `HTTP` → `headscale.headscale.svc.cluster.local:8080`
5. Save. Cloudflare auto-creates a CNAME `admin.547600.xyz → <tunnel-id>.cfargotunnel.com`.

Push the token into the cluster as a Secret:

```bash
kubectl create namespace cloudflared --dry-run=client -o yaml | kubectl apply -f -
kubectl -n cloudflared create secret generic cloudflared-credentials \
  --from-literal=token='<paste-token-here>'
```

Within ~30s the two `cloudflared` pods should be `Running` and the tunnel should show **HEALTHY** in the Cloudflare dashboard.

Curl test (from anywhere):

```bash
curl -sS https://admin.547600.xyz/health
# expect: pass
```

(That endpoint won't work yet — headscale isn't running. The TLS handshake succeeding is enough confirmation that the tunnel is up.)

---

## 2. Headscale — first start + bootstrap

Once `headscale` ArgoCD app is synced and the StatefulSet pod is `Running`:

```bash
# Sanity check — health endpoint should answer 200.
curl -sS https://admin.547600.xyz/health
```

Exec into the pod to create your user and preauth keys:

```bash
POD=$(kubectl -n headscale get pod -l app=headscale -o jsonpath='{.items[0].metadata.name}')

# 1. Create your user.
kubectl -n headscale exec -it "$POD" -- headscale users create kfir

# 2. Preauth key for the in-cluster subnet router. Reusable so the pod
#    can re-auth after a restart; tagged so ACLs can target it.
kubectl -n headscale exec -it "$POD" -- headscale preauthkeys create \
  --user kfir --reusable --expiration 8760h --tags tag:router

# 3. Preauth key for your laptop. One-shot, expires in 1h.
kubectl -n headscale exec -it "$POD" -- headscale preauthkeys create \
  --user kfir --expiration 1h
```

Save both keys somewhere safe. The router key goes into the cluster:

```bash
kubectl -n tailscale-router create secret generic tailscale-auth \
  --from-literal=TS_AUTHKEY='<router-preauth-key>'
```

The router pod will pick this up on its next restart:

```bash
kubectl -n tailscale-router rollout restart deploy/tailscale-router
```

Verify the router registered and its route is auto-approved by the ACL:

```bash
kubectl -n headscale exec -it "$POD" -- headscale nodes list
kubectl -n headscale exec -it "$POD" -- headscale routes list
# expect: 192.168.1.0/24 enabled=true on the k8s-router node
```

If routes show `enabled=false`, the `autoApprovers` block in the ACL didn't match — enable manually:

```bash
kubectl -n headscale exec -it "$POD" -- headscale routes enable -r <route-id>
```

---

## 3. Client setup — your laptop

### macOS

```bash
brew install --cask tailscale

# Point the client at your Headscale instead of Tailscale's SaaS.
tailscale up \
  --login-server=https://admin.547600.xyz \
  --auth-key=<laptop-preauth-key> \
  --accept-routes
```

`--accept-routes` is the critical flag — without it the client connects but ignores the 192.168.1.0/24 advertisement from the router.

### Linux

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up \
  --login-server=https://admin.547600.xyz \
  --auth-key=<laptop-preauth-key> \
  --accept-routes
```

### iOS / Android

The official Tailscale apps support custom coordination servers, but the UI is buried. On iOS: long-press the **Sign in** button on the launch screen → you'll get a text field for the login server URL. On Android: gear icon → **Account** → **Use alternate server**.

Enter `https://admin.547600.xyz`. The app will open Safari/Chrome for OAuth — instead, you'll get a `headscale` registration page asking you to run the `headscale nodes register` command on the server:

```bash
kubectl -n headscale exec -it "$POD" -- headscale nodes register --user kfir --key <nodekey>
```

(The `<nodekey>` is shown in the browser.)

---

## 4. Test the path

From your laptop, on its home Wi-Fi or tethered to a phone — either works:

```bash
# Are you connected to the mesh?
tailscale status
# Expect 'k8s-router' listed, with 'offers routes: 192.168.1.0/24'

# Can you ping a LAN host?
ping 192.168.1.105

# Open the Proxmox UI:
open https://192.168.1.105:8006
```

Cert warning is expected (Proxmox's self-signed cert). Same one you see on the LAN today.

---

## 5. Things that go wrong, and what to look at

| Symptom | Likely cause | Where to look |
|---|---|---|
| `tailscale up` hangs at "waiting for login" | Wrong `server_url` in headscale config — must exactly match what the client uses | `kubectl -n headscale logs <pod>` — look for `Mismatched server_url` |
| Client connects but can't ping 192.168.1.105 | Routes not approved, or `--accept-routes` missing on client | `headscale routes list` + `tailscale status` |
| cloudflared healthy in dashboard but `https://admin.547600.xyz/health` returns 502 | Public Hostname target wrong | Cloudflare dashboard → tunnel → Public Hostname → service should be `http://headscale.headscale.svc.cluster.local:8080` |
| Headscale pod CrashLoopBackOff on first start | `/mnt/storage2-bulk/headscale` doesn't exist or is owned by root | SSH gpunvdgtx1060, `ls -ln /mnt/storage2-bulk/headscale` — must be 1000:1000 |
| Router pod logs `Tailscale Funnel ... is not available with your current plan` | Harmless — Funnel is a Tailscale-SaaS-only feature, Headscale ignores it |  |
| ACL changes don't take effect | ConfigMap reload doesn't restart the pod | `kubectl -n headscale rollout restart sts/headscale` |

---

## 6. Day-2 — adding more devices, more routes, more users

- **Another device:** `headscale preauthkeys create --user kfir` → use on the new device.
- **Onboard a friend:** `headscale users create alice`, then a preauth key for her. Edit `acl.hujson` in `kubernetes/system/headscale/configmap.yaml` to grant her access to specific tags.
- **Advertise a second subnet** (e.g. if you build a 10.x lab network): edit the `--advertise-routes` arg in `kubernetes/system/tailscale-router/deployment.yaml`, plus the `autoApprovers` block in the ACL.
- **MagicDNS:** any registered device is reachable at `<hostname>.homelab.ts.net` from any other device on the mesh.
