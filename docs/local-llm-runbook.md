# Local LLM API runbook

## Purpose and ownership

Argo CD runs one private vLLM replica on `gpu-2`, the Talos GPU VM on
`largegpu`. It restores the last known-good workload: the immutable
`Qwen/Qwen3-8B-AWQ` model revision on the pinned vLLM `v0.26.0` image. Do not
upgrade the image as part of recovery; `v0.27.1` was previously rolled back
after failing to start on this node.

vLLM is the API layer. It exposes the OpenAI-compatible `/v1/models`,
`/v1/chat/completions`, and `/v1/completions` routes at:

```text
http://llm.homelab-assistant.svc.cluster.local:8000/v1
```

The served model name is `local-llm`. The Service is `ClusterIP`; there is no
Gateway, Ingress, LoadBalancer, NodePort, Cloudflare route, or Tailscale route.
A default-deny policy admits port 8000 from namespaced Kubernetes pods and
node-originated health probes only. API requests require the existing
`LLM_API_KEY` bearer value from `homelab-assistant-secrets`.

## Storage and availability

The `llm-model-cache` claim reuses the retained `gpu2-scratch-pv`. This is a
replaceable 390 GiB cache on `gpu-2`'s separately attached scratch disk; it is
not critical storage and it is not backed up. The fixed model revision can be
downloaded again from the narrowly allowlisted Hugging Face endpoints.

The API is unavailable whenever `gpu-2` is stopped and Windows VM `502` owns
the shared RTX 3080. It is also a single replica because the model requests the
node's only GPU. The control plane remains independent of this workload.

The retired claim was deleted while the PV used `Retain`, so Kubernetes leaves
`gpu2-scratch-pv` in `Released` with the previous claim reference. Inspect it;
do not delete the PV, format the disk, change the local path, or clear any
filesystem safety flag. After confirming no PVC currently names the volume,
remove only the stale Kubernetes `claimRef`:

```bash
kubectl get pvc -A --field-selector spec.volumeName=gpu2-scratch-pv
kubectl get pv gpu2-scratch-pv -o jsonpath='{.status.phase}{"\n"}'
kubectl patch pv gpu2-scratch-pv --type=json \
  -p='[{"op":"remove","path":"/spec/claimRef"}]'
```

The first command must return no claim and the second must return `Released`
before the patch. Then let Argo recreate the same
`homelab-assistant/llm-model-cache` identity. This is retained-PV recovery, not
scratch-disk replacement.

## Client configuration

Configure in-cluster clients with a base URL and a separately delivered copy
of the bearer key. Do not grant clients read access to the whole
`homelab-assistant-secrets` Secret.

```text
OPENAI_BASE_URL=http://llm.homelab-assistant.svc.cluster.local:8000/v1
OPENAI_MODEL=local-llm
OPENAI_API_KEY=<same value as LLM_API_KEY>
```

Prefer a client-specific Secret in the consuming namespace. The API key is an
authentication boundary between pods, while the ClusterIP and network policy
are the network boundary. If only selected namespaces should consume the API,
narrow `llm-cluster-clients` to their namespace labels in the same change that
adds the first consumer.

## Static verification

These checks do not mutate the live cluster. `kubectl` may still use API
discovery for registered CRDs, so verify the active context first if even
read-only access is undesirable:

```bash
kubectl kustomize kubernetes/system/homelab-assistant \
  >/tmp/homelab-assistant.yaml
kubectl create --dry-run=client --validate=false \
  -f kubernetes/apps/homelab-assistant.yaml -o name
kubectl create --dry-run=client --validate=false \
  -f /tmp/homelab-assistant.yaml -o name
```

## Rollout and health checks

The `homelab-assistant` Application remains manual because it still owns
retained cutover resources. Review its diff, synchronize it explicitly, and do
not enable prune merely to start the model. Before syncing, confirm Talos VM
`402` is the active side of the largegpu/Windows mutex and `gpu-2` is Ready.

The active cluster currently has no `homelab-assistant-secrets` object. Restore
its existing SOPS-encrypted recovery copy before the sync; this does not print
the key and does not invent a replacement value:

```bash
scripts/secrets.sh restore-k8s \
  homelab-assistant/homelab-assistant-secrets
```

The restore is an explicit live mutation and prompts for the protected age
identity and target-cluster confirmation. Run it only after checking the
current kubectl context. Do not decrypt or copy `LLM_API_KEY` through terminal
output.

After an authorized sync, verify without displaying credentials:

```bash
kubectl -n homelab-assistant get pvc llm-model-cache
kubectl -n homelab-assistant rollout status deployment/llm --timeout=30m
kubectl -n homelab-assistant get pod,service -l app=local-llm
kubectl -n homelab-assistant logs deployment/llm --tail=100
```

The first cold start may take substantially longer while the pinned snapshot
downloads. The startup probe permits up to 30 minutes. A successful rollout
requires a `Bound` cache claim, one Ready `llm` pod on `gpu-2`, and a
`ClusterIP` `llm` Service on port 8000. Test `/v1/models` and one short chat
completion from the intended in-cluster client, using its client-scoped Secret;
do not print the bearer value in logs or shell history.

If scheduling is pending, check that `gpu-2` is Ready and advertises one free
`nvidia.com/gpu`. If startup fails, inspect the PVC state, model-download
policy drops, and vLLM logs before changing resource limits or model settings.
