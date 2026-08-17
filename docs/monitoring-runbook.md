# Monitoring runbook

The `monitoring` ArgoCD Application installs the pinned
`kube-prometheus-stack` chart. It provides:

- Prometheus for metric collection and 15-day retention
- Grafana with the stack's Kubernetes dashboards
- Alertmanager
- kube-state-metrics
- node-exporter on every Kubernetes node

Grafana is private at `https://grafana.home.547600.xyz`. AdGuard Home's
`*.home.547600.xyz` rewrite resolves it to the shared private Gateway at
`192.168.1.220`.

## Storage design and warning

The following static PVs use the non-critical NFS export on `smallgpu`
(`192.168.1.106:/mnt/data10tb`):

| Component | NFS directory | Capacity | Retention |
| --- | --- | ---: | --- |
| Prometheus | `/mnt/data10tb/monitoring/prometheus` | 50 GiB | 15 days, capped at 40 GiB |
| Alertmanager | `/mnt/data10tb/monitoring/alertmanager` | 2 GiB | 120 hours |
| Grafana | `/mnt/data10tb/monitoring/grafana` | 2 GiB | Until manually removed |

Prometheus upstream does not support NFS for its local TSDB and warns that
filesystem behavior can cause unrecoverable corruption. This deployment accepts
that risk because monitoring history is non-critical. If the TSDB is corrupted,
stop Prometheus, move the old directory aside on the NFS server, create a fresh
directory with the ownership below, and allow Prometheus to start with an empty
history.

## One-time preparation

Create the exact exported directories on `smallgpu`:

```bash
ssh root@192.168.1.106

mkdir -p /mnt/data10tb/monitoring/prometheus
chown -R 1000:2000 /mnt/data10tb/monitoring/prometheus/
mkdir -p /mnt/data10tb/monitoring/alertmanager
chown -R 1000:2000 /mnt/data10tb/monitoring/alertmanager/
mkdir -p /mnt/data10tb/monitoring/grafana
chown -R 472:472 /mnt/data10tb/monitoring/grafana

exportfs -rav
exit
```

Do not try to `chown` these directories. The underlying NTFS filesystem is
mounted with `uid=0,gid=0,umask=0000`, so ownership and Unix mode bits are
synthesized rather than stored per directory. The resulting writable mode lets
Prometheus (UID 1000), Alertmanager (UID 1000), and Grafana (UID 472) use their
directories. This broad access is another reason this export is restricted to
reproducible, non-critical data.

Create Grafana credentials before syncing the Application. Use an
alphanumeric password and save it in a password manager:

```bash
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

read -rsp "Paste the Grafana admin password from your password manager: " \
  GRAFANA_ADMIN_PASSWORD
echo
kubectl -n monitoring create secret generic monitoring-grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$GRAFANA_ADMIN_PASSWORD"
unset GRAFANA_ADMIN_PASSWORD
```

Capture it immediately into the encrypted recovery bundle:

```bash
scripts/secrets.sh capture-k8s monitoring/monitoring-grafana-admin
```

The plaintext Secret is deliberately not stored in this public repository;
only its SOPS ciphertext is committed. See
[SOPS + age recovery](secrets-disaster-recovery.md).

Commit and push the manifests. The root ArgoCD Application should discover
`kubernetes/apps/monitoring.yaml`. If it does not, apply the child Application:

```bash
kubectl apply -f kubernetes/apps/monitoring.yaml
```

## Verification

Watch the deployment:

```bash
kubectl -n argocd get application monitoring -w
kubectl -n monitoring get pods,pvc
kubectl get pv | grep monitoring
```

All three PVCs should be `Bound`. Verify Prometheus targets:

```bash
kubectl -n monitoring port-forward \
  svc/monitoring-kube-prometheus-prometheus 9090:9090
```

Open `http://localhost:9090/targets`. The API server, kubelets,
node-exporters, kube-state-metrics, Prometheus, Grafana, Alertmanager, and the
operator should be healthy.

The kube-proxy target is disabled because Cilium replaces kube-proxy. Talos
binds etcd, kube-controller-manager, and kube-scheduler metrics to localhost in
the current machine configuration, so those unavailable targets and their
rules are also disabled.

Finally open:

```text
https://grafana.home.547600.xyz
```

Log in with the credentials stored in the password manager. Start with the
preinstalled dashboards under **Dashboards > Kubernetes / Compute Resources**.

## Node maintenance

The core monitoring pods tolerate the intended dedicated GPU-node taint and
have no hard node selector. A planned drain can move them to another worker:

```bash
kubectl drain NODE_NAME --ignore-daemonsets --delete-emptydir-data
```

The static NFS volumes have no node affinity, so their claims remain usable
after rescheduling. node-exporter is a DaemonSet and is expected to remain on a
drained node until ignored by the drain, then disappear when that node stops.
