# Private homelab LLM and Telegram gateway

## Architecture and security boundary

The `homelab-assistant` Argo CD Application contains two independent roles:

```text
Owner's Telegram app
        |
        | Telegram cloud (TLS)
        v
allowlisted gateway pod -- bearer key --> ClusterIP-only vLLM
                                              |
                                              v
                               Qwen3-8B-AWQ on gpu-2 / RTX 3080
```

vLLM is pinned to `gpu-2`, requests the whole GPU, and caches the pinned model
snapshot on the existing disposable `gpu2-scratch-pv`. The model API has no
Gateway, Ingress, LoadBalancer, NodePort, Cloudflare route, or Tailscale-facing
route. Network policy admits API traffic only from the Telegram gateway and
node-originated health probes. A bearer key is an additional control, not a
substitute for that isolation.

The container root remains read-only. An ephemeral writable cache holds vLLM,
FlashInfer, and Triton compilation artifacts, while the nested Hugging Face
cache mount retains only the replaceable model snapshot across pod restarts.

The gateway accepts only direct chats where the Telegram sender ID, chat ID,
and configured allowlist agree. It has no service-account token, shell, host
mount, persistent conversation store, or model-controlled tools. `/new` clears
the bounded in-memory history and a pod restart clears all history.

Telegram itself cannot be restricted to Tailscale: the phone connects to
Telegram, while the pod long-polls `api.telegram.org`. The cluster never sees
the phone's source IP, and Telegram requires the bot to reach its public API.
The actual controls on this path are the secret bot token, exact user-ID
allowlist, private-chat requirement, Telegram account security, default-deny
network policy, and absence of execution privileges. Keep Telegram two-step
verification enabled and protect the enrolled phone. If tailnet-only transport
is a hard requirement, use a private web client over the existing Tailscale
Gateway instead of Telegram.

This first release is deliberately an LLM chat interface, not an autonomous
operator. Add future homelab actions as separately implemented, typed commands
with least-privilege credentials, confirmation for mutations, and an audit log.
Never execute model-generated shell or `kubectl` text.

The job-assistant bot remains separate. It is coupled to that application's
PostgreSQL workflow, and Telegram permits only one reliable long-poll consumer
for a bot token. Create a new BotFather bot/token for this service.

## Model sizing and availability

[`Qwen/Qwen3-8B-AWQ`](https://huggingface.co/Qwen/Qwen3-8B-AWQ) is a 4-bit 8B
model whose published snapshot is about 6.1 GB. The deployment limits context
to 8,192 tokens and two concurrent
sequences so weights and KV cache fit the RTX 3080's 10 GiB VRAM. If startup
reports CUDA out-of-memory, reduce `--max-model-len` before lowering model
quality. The model and vLLM versions are intentionally pinned.

`gpu-2` is mutually exclusive with Windows VM `502`. Draining/stopping VM `402`
makes inference unavailable; the Telegram gateway stays up and reports a
temporary failure. The cache persists on gpu-2's scratch disk, but is
replaceable and may be downloaded again. Do not move this PVC to critical NFS.
Download egress includes the exact Hugging Face/Xet hosts used by the pinned
snapshot, including the regional `us.aws.cdn.hf.co` large-file redirect.

## First image release

The workflow `.github/workflows/homelab-assistant.yml` lints, types, tests, and
builds `ghcr.io/kfir-marx/homelab-assistant:sha-<commit>`. A successful push to
`main` opens `automation/homelab-assistant-image`, replacing the bootstrap tag
in the Deployment with the tested content-addressed tag. Merge that release PR
before expecting the gateway pod to start.

## Create and capture the Secret

Create a new Telegram bot with BotFather and obtain your numeric Telegram user
ID out of band. The bot token must not be shared with the job assistant. On a
trusted controller, write values into protected temporary files so secrets do
not appear in shell history or process arguments:

```bash
umask 077
assistant_secret_dir="$(mktemp -d)"
read -rsp 'New Telegram bot token: ' telegram_token
printf '%s' "${telegram_token}" >"${assistant_secret_dir}/TELEGRAM_TOKEN"
unset telegram_token
printf '\n'
read -rp 'Allowed numeric Telegram user IDs (comma-separated): ' telegram_ids
printf '%s' "${telegram_ids}" >"${assistant_secret_dir}/TELEGRAM_ALLOWED_USER_IDS"
unset telegram_ids
openssl rand -base64 48 | tr -d '\n' >"${assistant_secret_dir}/LLM_API_KEY"

kubectl create namespace homelab-assistant --dry-run=client -o yaml | kubectl apply -f -
kubectl -n homelab-assistant create secret generic homelab-assistant-secrets \
  --from-file="${assistant_secret_dir}"
scripts/secrets.sh capture-k8s homelab-assistant/homelab-assistant-secrets

# Securely dispose of the temporary files using the controller's approved method.
```

Commit only the resulting SOPS ciphertext. Run `scripts/secrets.sh check`
afterward. Never add the plaintext token, allowlist, or LLM key to a manifest.

## Rollout and verification

After the service release PR, Secret ciphertext, and manifests are merged, let
Argo CD reconcile normally. No live apply is required:

```bash
kubectl -n argocd get application homelab-assistant -w
kubectl -n homelab-assistant get pods,pvc -o wide
kubectl -n homelab-assistant rollout status deployment/llm --timeout=30m
kubectl -n homelab-assistant rollout status deployment/telegram-gateway
kubectl -n homelab-assistant logs deployment/llm
kubectl -n homelab-assistant logs deployment/telegram-gateway
```

The first model download is several gigabytes. Test `/status`, `/new`, and a
short prompt from the allowlisted private chat. Also test from a non-allowlisted
Telegram account; it must receive no reply. Confirm there is no externally
addressable service:

```bash
kubectl -n homelab-assistant get service llm
kubectl -n homelab-assistant get httproute,ingress
kubectl -n homelab-assistant get networkpolicy,ciliumnetworkpolicy
```

Use DCGM metrics from the GPU Operator to observe VRAM, utilization,
temperature, and power. When switching `gpu-2` to Windows, follow the drain and
VM wait sequence in the GPU Operator runbook; no LLM-specific shutdown step is
needed.

## Rotation and incident response

- Rotate the internal LLM key by updating the single Secret; both pods consume
  the same key and restart through normal GitOps/operator procedure.
- If the Telegram token or account is suspected compromised, revoke the token
  immediately with BotFather, replace it in the Secret, capture the new SOPS
  ciphertext, and review Telegram active sessions.
- If an unapproved sender receives any response, scale the gateway down, revoke
  the bot token, and inspect the deployed allowlist before restoring service.
- Do not log request bodies, authorization headers, bot tokens, or model
  conversations. Current code logs only generic request failures.

The model endpoint follows [vLLM's security guidance](https://docs.vllm.ai/en/stable/usage/security/):
the bearer key protects compatible API paths, while network policy remains the
primary boundary because not every vLLM endpoint is authenticated.
