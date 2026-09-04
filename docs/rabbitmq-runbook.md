# Shared RabbitMQ runbook

## Contract

The `rabbitmq` Argo CD Application is the cluster-wide AMQP 0-9-1 transport.
Any namespaced pod may reach `rabbitmq.rabbitmq.svc.cluster.local:5672`, but
every client must authenticate. The management port is not admitted by network
policy; Prometheus alone can reach the dedicated metrics port `15692`.

Applications own their exchanges, queues, retry/dead-letter policy, schemas,
idempotency, and durable job records. Use a separate RabbitMQ user and virtual
host, or narrowly scoped resource permissions, for each trust boundary. Never
copy the bootstrap administrator credential into application namespaces.

## Storage and availability boundary

This is one RabbitMQ node with a 10 GiB `emptyDir`. Queues and messages marked
durable survive a RabbitMQ process restart but not Pod replacement, eviction,
or node loss. That limitation is deliberate: the cluster has no suitable
declared local durable PV, and RabbitMQ assumes local filesystem `fsync`
semantics and advises against NFS for its node database. Treat RabbitMQ as a
shared backpressure and delivery mechanism, not the source of truth. A service
that cannot recreate work must keep its job state in its own retained database.

Do not move `/var/lib/rabbitmq` onto either NFS tier. Durable RabbitMQ service
requires a future storage/topology project with local SSD volumes and multiple
nodes placed across failure domains.

## Bootstrap secrets and users

Create `rabbitmq/rabbitmq-secrets` with strong generated values for `username`,
`password`, and `erlang-cookie`, then capture it with the repository's encrypted
secret workflow. The initial user exists to bootstrap administration. Create an
`internal-llm` application user with permissions for the `homelab` vhost and
store its AMQP URL as `RABBITMQ_URL` in
`homelab-assistant/homelab-assistant-secrets`. Other services receive their own
credentials in their own namespace.

Credentials configured through `RABBITMQ_DEFAULT_*` take effect only against a
blank node. With transient storage every recreated Pod is blank, so keep the
encrypted bootstrap values stable. Rotate application credentials through the
management CLI/API and update their encrypted client Secret together.

## Verification

Static checks:

```bash
kubectl kustomize kubernetes/system/rabbitmq >/tmp/rabbitmq.yaml
kubectl create --dry-run=client --validate=false \
  -f kubernetes/apps/rabbitmq.yaml -o name
kubectl create --dry-run=client --validate=false \
  -f /tmp/rabbitmq.yaml -o name
```

After an explicitly authorized sync, verify without displaying credentials:

```bash
kubectl -n rabbitmq rollout status statefulset/rabbitmq
kubectl -n rabbitmq get pod,service,networkpolicy
kubectl -n rabbitmq exec statefulset/rabbitmq -- rabbitmq-diagnostics -q check_running
kubectl -n rabbitmq exec statefulset/rabbitmq -- rabbitmqctl list_vhosts
kubectl -n rabbitmq exec statefulset/rabbitmq -- rabbitmqctl list_queues \
  name messages_ready messages_unacknowledged consumers
```

Alert on broker scrape failure and sustained ready-message backlog. Consumer
services should additionally alert on their own oldest-job age and terminal
failures because aggregate RabbitMQ metrics do not explain application state.
