# ELK logging runbook

The `elk` Argo CD Application installs Elastic Cloud on Kubernetes (ECK), an
Elasticsearch 9.5.3 cluster, Kibana, and a Vector DaemonSet that collects
Kubernetes container logs from every node. Kibana is private at:

```text
https://kibana.home.547600.xyz
```

The logging namespace is deliberately excluded from collection so Vector does
not ingest its own delivery errors or feed Elasticsearch and Kibana logs back
into the same cluster.

## Data-stream contract

Vector creates one data stream per detected Kubernetes service. The default
name is:

```text
logs-<namespace>.<application>[.<component>]-default
```

The application name comes from `app.kubernetes.io/name`, then `app`, and
finally the container name. The optional component comes from
`app.kubernetes.io/component` or `component`. Invalid data-stream characters
are normalized to underscores.

Set the `logging.kfir.dev/stream` Pod label when a workload needs an explicit,
stable service name. The Kubernetes namespace is still included, preventing
unrelated namespaces from colliding. All containers with the same derived
namespace/application/component value intentionally share a stream.

The `homelab-service-logs` index template applies to `logs-*-*` and fixes every
backing index at:

- one primary shard;
- one replica; and
- hot-phase rollover after 14 days or when the primary shard reaches 10 GB,
  whichever occurs first.

The delete phase begins immediately after rollover, so the completed backing
index is removed and the new write index remains. ILM runs asynchronously, so
rollover and deletion can lag their threshold by the normal ILM polling
interval.

## Elasticsearch topology and storage

Two full Elasticsearch data nodes are scheduled on different Kubernetes
hosts. A small voting-only master node provides election quorum without holding
a third shard copy. Clients use ECK's load-balanced service, and ECK manages
cluster bootstrap, transport certificates, HTTP TLS, and Kibana association.

Each data node has a separate, hard-bound 200 GiB static PV:

| PV | Critical NFS path |
|---|---|
| `elk-elasticsearch-data-0-pv` | `/mnt/storage2-bulk/elk/elasticsearch-0` |
| `elk-elasticsearch-data-1-pv` | `/mnt/storage2-bulk/elk/elasticsearch-1` |

Both PVs use `nfs-storage2` and `Retain`. ECK also preserves PVCs when the
Elasticsearch resource is deleted. The PVC sizes are logical reservations;
the static NFS export does not enforce per-directory quotas.

NFS is an explicit durability-over-performance tradeoff for Elasticsearch.
The configuration disables mmap-backed index storage, and search/index latency
will be higher than node-local SSD. Both the primary and replica ultimately
share the Ubuntu workstation's storage failure domain: the replica protects
against an Elasticsearch pod or Kubernetes worker loss, not failure of the NFS
host or its filesystem. Critical placement is not a substitute for an off-host
snapshot.

## First rollout

Create and permission the two child directories with the owning Ansible layer
before Argo CD creates the PVs. This play is a live host mutation and must be
run explicitly:

```bash
cd ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml --ask-become-pass
```

After merging, the root Application discovers `kubernetes/apps/elk.yaml`.
Watch the rollout without manually applying its generated resources:

```bash
kubectl -n argocd get application elk -w
kubectl -n logging get elasticsearch,kibana,pods,pvc
kubectl get pv elk-elasticsearch-data-0-pv elk-elasticsearch-data-1-pv
```

The sync bootstrap hook installs the ILM policy and index template before it
starts Vector. It also creates a random `vector_ingest` password and writes it
to `elk-vector-credentials`. That user can monitor cluster health and create
documents only in `logs-*-*`; the generated ECK superuser credential is not
mounted into the long-running collector.

## Access and verification

Retrieve the generated Kibana login without placing it in Git or shell history:

```bash
kubectl -n logging get secret elk-es-elastic-user \
  -o go-template='{{.data.elastic | base64decode}}{{"\n"}}'
```

The username is `elastic`. Store the credential in the normal password manager
after retrieval.

In Kibana, open Discover and create a data view named `Homelab service logs`
with the pattern `logs-*-*` and `@timestamp` as its time field. Filter on
`data_stream.dataset`, `kubernetes.pod_namespace`, or
`kubernetes.container_name` to isolate a service.

Verify the topology, data streams, and lifecycle through an authenticated local
port-forward:

```bash
kubectl -n logging port-forward service/elk-es-http 9200:9200

# In another terminal, first save the generated CA and password locally.
kubectl -n logging get secret elk-es-http-certs-public \
  -o go-template='{{index .data "ca.crt" | base64decode}}' > /tmp/elk-ca.crt
ELASTIC_PASSWORD="$(kubectl -n logging get secret elk-es-elastic-user \
  -o go-template='{{.data.elastic | base64decode}}')"

curl --cacert /tmp/elk-ca.crt -u "elastic:${ELASTIC_PASSWORD}" \
  --resolve elk-es-http.logging.svc:9200:127.0.0.1 \
  https://elk-es-http.logging.svc:9200/_cat/nodes?v
curl --cacert /tmp/elk-ca.crt -u "elastic:${ELASTIC_PASSWORD}" \
  --resolve elk-es-http.logging.svc:9200:127.0.0.1 \
  'https://elk-es-http.logging.svc:9200/_data_stream/logs-*?pretty'
curl --cacert /tmp/elk-ca.crt -u "elastic:${ELASTIC_PASSWORD}" \
  --resolve elk-es-http.logging.svc:9200:127.0.0.1 \
  'https://elk-es-http.logging.svc:9200/logs-*/_ilm/explain?pretty'
```

The `--resolve` flags preserve TLS hostname validation while directing the
cluster Service name to the local port-forward. A compact health check is:

```bash
curl --cacert /tmp/elk-ca.crt -u "elastic:${ELASTIC_PASSWORD}" \
  --resolve elk-es-http.logging.svc:9200:127.0.0.1 \
  'https://elk-es-http.logging.svc:9200/_cluster/health?pretty'
```

Remove `/tmp/elk-ca.crt` and unset `ELASTIC_PASSWORD` when verification is
complete.
