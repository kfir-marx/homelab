# Internal LLM runbook

## Purpose and request flow

The Argo CD Application is named `internal-llm`. It provides a private,
authenticated, OpenAI-compatible API for any namespaced Kubernetes workload:

```text
client -> internal-llm API -> RabbitMQ -> two workers -> vLLM -> worker reply -> client
```

The API supports `/v1/models`, `/v1/chat/completions`, and `/v1/completions`.
Streaming requests are rejected because responses traverse RabbitMQ RPC. The
two workers each prefetch one message, matching vLLM's `--max-num-seqs=2`;
RabbitMQ buffers excess requests instead of allowing unbounded GPU contention.
API and worker access logs are disabled and message bodies must never be logged.

New clients use:

```text
OPENAI_BASE_URL=http://internal-llm.homelab-assistant.svc.cluster.local:8080/v1
OPENAI_MODEL=local-llm
OPENAI_API_KEY=<same value as LLM_API_KEY>
```

The namespace remains `homelab-assistant` intentionally. Renaming it would
replace the retained local-GPU PVC identity and complicate the existing
workstation-assistant rollback resources. The compatibility Service
`llm.homelab-assistant.svc:8000` now targets the queued API, not vLLM directly.
The `llm-inference` Service is private to the queue workers.

## Inference and availability

Argo CD runs one vLLM replica on `gpu-2` using the immutable
`Qwen/Qwen3-8B-AWQ` revision and pinned vLLM `v0.26.0` image. Do not upgrade it
during recovery; `v0.27.1` previously failed to start on this node. The API is
unavailable whenever Talos VM `402` is stopped and Windows VM `502` owns the
shared RTX 3080.

The `llm-model-cache` claim reuses retained `gpu2-scratch-pv`. It is a
replaceable 390 GiB cache, not critical data. If the PV is `Released`, first
prove no PVC names it and then remove only the stale Kubernetes claim reference:

```bash
kubectl get pvc -A --field-selector spec.volumeName=gpu2-scratch-pv
kubectl get pv gpu2-scratch-pv -o jsonpath='{.status.phase}{"\n"}'
kubectl patch pv gpu2-scratch-pv --type=json \
  -p='[{"op":"remove","path":"/spec/claimRef"}]'
```

The first command must return no claim and the second must return `Released`.
Never delete the PV, format the disk, change its local path, or clear a
filesystem safety flag.

## Secrets

The existing `homelab-assistant/homelab-assistant-secrets` Secret supplies:

- `LLM_API_KEY`: client authentication and private vLLM authentication.
- `RABBITMQ_URL`: an `amqp://` URL for a non-administrator RabbitMQ user with
  access to the `homelab` vhost.

Deliver only `LLM_API_KEY` through a client-specific Secret in each consuming
namespace. Do not grant consumers access to the source Secret or RabbitMQ
credential. Before rollout, add `RABBITMQ_URL` to the encrypted recovery copy
with `scripts/secrets.sh edit-k8s`, then restore it without printing values.

## Static verification

```bash
uv sync --directory services/internal-llm --locked --extra dev
uv run --directory services/internal-llm --locked --extra dev ruff format --check .
uv run --directory services/internal-llm --locked --extra dev ruff check .
uv run --directory services/internal-llm --locked --extra dev mypy src tests
uv run --directory services/internal-llm --locked --extra dev pytest
kubectl kustomize kubernetes/system/homelab-assistant >/tmp/internal-llm.yaml
kubectl create --dry-run=client --validate=false \
  -f kubernetes/apps/internal-llm.yaml -o name
kubectl create --dry-run=client --validate=false \
  -f /tmp/internal-llm.yaml -o name
```

## Rollout

The rename from the deprecated `homelab-assistant` Application is complete.
`internal-llm` owns the retained compatibility resources, model storage,
gateway, workers, and private inference deployment. The Application remains
manual because it owns retained storage and the local GPU workload.

Never sync the placeholder `sha-bootstrap` image. Merge the immutable image-pin
PR created by the `internal-llm` workflow first. Restore both required Secrets,
confirm VM `402` is the active side of the GPU mutex, and sync `rabbitmq` before
`internal-llm`.

After an authorized sync:

```bash
kubectl -n rabbitmq rollout status statefulset/rabbitmq
kubectl -n homelab-assistant get pvc llm-model-cache
kubectl -n homelab-assistant rollout status deployment/llm-inference --timeout=30m
kubectl -n homelab-assistant rollout status deployment/internal-llm-api
kubectl -n homelab-assistant rollout status deployment/internal-llm-worker
kubectl -n homelab-assistant get service internal-llm llm llm-inference
```

Test `/v1/models` and one short non-streaming chat completion from an intended
in-cluster client. A cold model start can take up to 30 minutes. If requests
stall, inspect RabbitMQ queue depth, worker connectivity, the vLLM health
endpoint, `gpu-2` readiness, and its free `nvidia.com/gpu` capacity without
printing authorization headers or request bodies.
