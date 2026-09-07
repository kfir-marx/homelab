# Shared RabbitMQ runbook

The portable base, retained cloud PVC variant, managed broker option, and
cross-service configuration matrix are documented in
[`tapy-kubernetes-portability.md`](tapy-kubernetes-portability.md).

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

Do not move `/var/lib/rabbitmq` onto either homelab NFS tier. Durable homelab
RabbitMQ requires a future storage/topology project with local SSD volumes and
multiple nodes placed across failure domains. This limitation is specific to
`overlays/homelab`: the cloud overlay uses a dynamically provisioned retained
claim, and production may instead omit the Application and use a managed AMQP
endpoint from `RABBITMQ_URL`.

## Bootstrap secrets and users

Create `rabbitmq/rabbitmq-secrets` with strong generated values for `username`,
`password`, and `erlang-cookie`, then capture it with the repository's encrypted
secret workflow. The initial user exists to bootstrap administration. Other
services receive their own credentials in their own namespace. Tapy does not
use the `internal-llm` identity: it uses the dedicated `tapy` user and keeps its
URL in `tapy/tapy-secrets`.

Tapy declares the configured durable request queue, publishes
OpenAI-compatible RPC envelopes through the default exchange to
`internal-llm.requests` and `external-ai.requests` and consumes replies from
server-named exclusive callback queues. Its exact `homelab` vhost permissions
are:

```text
configure: ^(internal-llm\.requests|external-ai\.requests|amq\.gen-.*|amq_[0-9a-f]{32})$
write:     ^amq\.default$
read:      ^(amq\.gen-.*|amq_[0-9a-f]{32})$
```

RabbitMQ authorizes AMQP's nameless default exchange under the internal
resource name `amq.default`; the exact write regex grants no other exchange.
The routing key is still one of the queues Tapy can configure. The pinned
`aio-pika` client currently chooses `amq_` plus 32 lowercase hexadecimal
characters for its exclusive callback queue; `amq.gen-*` remains allowed for
broker-generated callback names. Tapy has no administrator tag and cannot
consume either request queue.

The external-ai worker identity consumes `external-ai.requests` and publishes
to callback queues through the same `^amq\.default$` write permission. Both
identities must use the same application vhost as the
internal-llm queue (currently `homelab`). Keep these permissions narrower than
the RabbitMQ bootstrap administrator.

Credentials configured through `RABBITMQ_DEFAULT_*` take effect only against a
blank node. With transient storage every recreated Pod is blank, so keep the
encrypted bootstrap values stable. A post-start reconciler in the homelab
StatefulSet recreates or updates the `tapy` and `external-ai` users from the
encrypted `rabbitmq/rabbitmq-tapy-user` and
`rabbitmq/rabbitmq-external-ai-user` Secrets and reapplies their restricted
permissions after every container start. Add equivalent broker-side recovery
and bootstrap logic before introducing another application identity; users
created only in RabbitMQ disappear with the next blank Pod replacement.

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
kubectl -n rabbitmq exec statefulset/rabbitmq -- \
  rabbitmqctl list_permissions --vhost homelab
kubectl -n rabbitmq exec statefulset/rabbitmq -- rabbitmqctl list_queues \
  name messages_ready messages_unacknowledged consumers
```

Alert on broker scrape failure and sustained ready-message backlog. Consumer
services should additionally alert on their own oldest-job age and terminal
failures because aggregate RabbitMQ metrics do not explain application state.
