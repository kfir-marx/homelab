# Tapy portable Kubernetes deployment

## Architecture and ownership

Tapy keeps the same runtime architecture in development and
production: the frontend serves the UI and proxies `/v1/*` to the private
backend, which stores users, sessions, per-user flights, and encrypted mailbox grants in
PostgreSQL, publishes OpenAI-compatible RPC requests through RabbitMQ, and
tries the configured internal/external LLM queues in order. `external-ai`
retains its authenticated HTTP job API, durable PostgreSQL job state, RabbitMQ
RPC worker, Codex state, and Alibaba Model Studio provider.

Each service separates portable resources from deployment choices:

| Service | Portable resources | Homelab development | Cloud production |
|---|---|---|---|
| Frontend and matcher | `kubernetes/system/tapy/base` | `overlays/homelab` | `overlays/cloud/in-cluster` or `overlays/cloud/managed` |
| external-ai | `kubernetes/system/external-ai/base` | `overlays/homelab` | `overlays/cloud/in-cluster` or `overlays/cloud/managed` |
| RabbitMQ | `kubernetes/system/rabbitmq/base` | `overlays/homelab` | `overlays/cloud` when not managed |

The bases contain no homelab IP, NFS path, storage class, public domain,
Cloudflare resource, namespace-specific service DNS name, or credential. The
PostgreSQL components are included only by the in-cluster variants. Cloud
managed variants omit PostgreSQL and RabbitMQ workloads and receive endpoints
only through Secret-backed URLs. Root kustomizations remain compatibility
aliases for the homelab overlays.

Homelab Argo CD Applications select the homelab paths. Example cloud
Application sets are in `kubernetes/apps/cloud/in-cluster` and
`kubernetes/apps/cloud/managed`; install exactly one set in a cloud cluster.
They are templates and are not traversed by the homelab app-of-apps source.

## Prerequisites and deployment order

All environments require Kubernetes, Argo CD or another Kustomize-capable
delivery mechanism, immutable application images, DNS, and Secrets created by
an out-of-band secret manager or operator. Network policy requires Cilium with
`CiliumNetworkPolicy` DNS/FQDN enforcement in addition to standard Kubernetes
`NetworkPolicy`. This is cloud-provider-neutral, but it is a deliberate CNI
prerequisite because standard NetworkPolicy cannot restrict changing OAuth,
mail, and model-provider addresses by hostname.

Cloud additionally requires:

- an installed Ingress controller and namespace/pod selectors chosen for it;
- a public DNS record for the frontend hostname and a matching TLS Secret;
- a dynamically provisioning StorageClass whose reclaim policy is `Retain`
  for every in-cluster PostgreSQL or Codex PVC;
- DNS hostnames, CA trust, TLS modes, and credentials for managed PostgreSQL or
  RabbitMQ when those variants are selected;
- OAuth applications whose exact callback URIs match the final public URL.

Deploy in this order: storage and CNI prerequisites; namespaces and Secrets;
RabbitMQ (or verify the managed broker); PostgreSQL (or verify managed
databases); internal-llm if used; external-ai; matcher and frontend;
Ingress/DNS/TLS; then OAuth end-to-end tests.

## Required customization

Do not edit a portable base. Patch the selected overlay, or create a downstream
overlay that uses it as a resource.

For cloud, replace every `REPLACE_WITH_*` and `*.example.invalid` value before
deployment. Set the matcher Ingress `ingressClassName`, rule/TLS hostname, and
TLS Secret together. Label the selected controller namespace with
`networking.tapy.io/ingress=true` and its controller pods with
`networking.tapy.io/controller=true`, or patch the policy selectors. The
public DNS name must resolve to that controller.

Set `PUBLIC_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URI`, and
`MICROSOFT_OAUTH_REDIRECT_URI` to the externally visible HTTPS URL. The
redirects normally end in `/v1/oauth/gmail/callback` and
`/v1/oauth/outlook/callback`. Register those exact values at Google and
Microsoft. Set `MICROSOFT_TENANT` to `common`, an organization tenant ID, or
another tenant value appropriate for the registration.

Set `WEBHOOK_PUBLIC_BASE_URL` to the same externally reachable origin (or a
dedicated webhook origin). Set `GMAIL_PUBSUB_TOPIC` only after creating the
topic and its push subscription. Include a strong `WEBHOOK_VERIFICATION_TOKEN`
in the backend Secret and in the Gmail push URL; Outlook additionally validates
the per-subscription `clientState`.

Cloud PVCs use `REPLACE_WITH_STORAGE_CLASS`. Use a class backed by storage with
the durability, zone topology, expansion, snapshot, and recovery behavior
required for production. Its reclaim policy must be `Retain`; the RabbitMQ
StatefulSet also retains claims on deletion and scale-down. Cloud never
references the homelab NFS server.

For managed variants, put the complete SQLAlchemy PostgreSQL URL in each
application's `DATABASE_URL` Secret key and the complete AMQP URL in
`RABBITMQ_URL`. Patch `managed-egress.yaml` to the exact DNS names and ports in
those URLs. IP-only managed endpoints are intentionally not admitted by the
FQDN policy. Encode the RabbitMQ virtual host in the AMQP URL and configure
least-privilege users for the selected request and server-named reply queues.

For the supplied in-cluster overlays, the private endpoints are
`tapy-postgres.tapy.svc.cluster.local:5432`,
`external-ai-postgres.external-ai.svc.cluster.local:5432`, and
`rabbitmq.rabbitmq.svc.cluster.local:5672`. Example URL shapes (with values
supplied out of band) are
`postgresql+psycopg://tapy:<password>@tapy-postgres.tapy.svc.cluster.local:5432/tapy`
and `amqp://<user>:<password>@rabbitmq.rabbitmq.svc.cluster.local:5672/<vhost>`.
These names belong in Secrets, not in the portable base.

`ALIBABA_BASE_URL` and the external model FQDN policy must describe the same
regional Model Studio endpoint. Patch both if the endpoint is not the included
international hostname. Codex/OpenAI endpoints are separately allowlisted.

## Secrets (names and keys only)

Never commit a Secret manifest with real or fabricated values.

| Namespace / Secret | Required keys |
|---|---|
| `tapy/tapy-secrets` | `DATABASE_URL`, `RABBITMQ_URL`, `OAUTH_TOKEN_ENCRYPTION_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `MICROSOFT_OAUTH_CLIENT_SECRET`; optional `WEBHOOK_VERIFICATION_TOKEN`; add `POSTGRES_PASSWORD` for in-cluster PostgreSQL |
| `tapy/tapy-frontend-secrets` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `GEMINI_API_KEY` |
| `external-ai/external-ai-secrets` | `DATABASE_URL`, `RABBITMQ_URL`, `HOMELAB_ASSISTANT_TOKEN`, `JOB_ASSISTANT_TOKEN`; `POSTGRES_PASSWORD` for in-cluster PostgreSQL; `ALIBABA_API_KEY` when Model Studio is enabled |
| `external-ai/external-ai-codex-auth-bootstrap` | `auth.json` when Codex-backed models are enabled |
| `rabbitmq/rabbitmq-secrets` | `username`, `password`, `erlang-cookie` |
| `rabbitmq/rabbitmq-tapy-user` | `password` |
| `rabbitmq/rabbitmq-external-ai-user` | `password` (broker-side recovery copy) |

The OAuth encryption key must be a Fernet key and must not be rotated without
re-encrypting stored refresh tokens. external-ai can run Model Studio-only
without `auth.json`, and Codex-only without `ALIBABA_API_KEY`; requests for an
unconfigured provider fail closed.

## Configuration matrices

The services communicate through RabbitMQ even when deployed to different
clusters or infrastructure. There is no matcher-to-LLM HTTP address to change:
all participants use the same reachable broker/vhost and matching queue names.

| Scenario | `LLM_ORDER` | Internal queue/model | External queue/model | Dependencies |
|---|---|---|---|---|
| Homelab development | `external-ai` | unused | `external-ai.requests` / `alibaba:qwen-plus` | Homelab RabbitMQ; external-ai worker only |
| Cloud, both LLMs | `internal-llm,external-ai` | operator-selected queue / cloud internal model | operator-selected queue / provider model | Reachable cloud internal-llm and external-ai consumers |
| Cloud, external only | `external-ai` | unused | operator-selected queue / `alibaba:qwen-plus` or allowlisted Codex model | external-ai worker only |

`INTERNAL_LLM_QUEUE`, `EXTERNAL_AI_QUEUE`, both model fields, request timeout,
and attempt order are matcher ConfigMap values. Configure the internal-llm
worker's `INTERNAL_LLM_RABBITMQ_URL`, `INTERNAL_LLM_REQUEST_QUEUE`, and
`INTERNAL_LLM_INFERENCE_BASE_URL` in its own deployment. Configure
external-ai's `REQUEST_QUEUE`, `RABBITMQ_URL`, and provider settings the same
way. Queue names must agree exactly.

| Data/transport choice | Matcher overlay | external-ai overlay | RabbitMQ Application |
|---|---|---|---|
| In-cluster PostgreSQL and RabbitMQ | cloud `in-cluster` | cloud `in-cluster` | `kubernetes/apps/cloud/in-cluster/rabbitmq.yaml` |
| Managed PostgreSQL and RabbitMQ | cloud `managed` | cloud `managed` | Omit it |
| Mixed | Thin downstream overlay adding only the PostgreSQL component needed, plus managed FQDN policy only for the external dependency | Same | Deploy only if RabbitMQ is in-cluster |

Managed and in-cluster choices are independent. A thin overlay may combine the
portable base/common cloud layer with one PostgreSQL component while keeping a
managed broker; no application code change is required.

## Homelab deployment

The public URL is `https://tapy.547600.xyz`. It reaches
`tapy-frontend.tapy.svc:3000`; the frontend proxies `/v1/*` to
`tapy-backend.tapy.svc:8080` so the OAuth
callbacks remain on the same origin. Update its DNS and
Cloudflare Tunnel route before switching traffic, and register the new callback
URLs with both OAuth providers. Cloudflare Tunnel access exists only in the
homelab overlay. PostgreSQL and external-ai
Codex state remain hard-bound to the same static `nfs-storage2` PV names,
workstation IP and paths, with reclaim policy `Retain`. RabbitMQ remains the
intentional 10 GiB `emptyDir` transport; it has not been moved onto NFS.

After NFS directories and out-of-band Secrets exist, let the existing Argo CD
Applications reconcile in this order: `rabbitmq`, `external-ai`, then `tapy`.
The optional `internal-llm` Application is independent of Tapy in the homelab
development configuration. No direct manifest apply is required.

## Generic cloud deployment

1. Fork or create a deployment branch and patch the chosen overlay placeholders.
   Point each cloud Application `repoURL` and `targetRevision` at that source.
2. Create namespaces and Secrets through the production secret workflow.
3. For in-cluster mode, customize StorageClass and RabbitMQ virtual host, then
   install the three Applications under `kubernetes/apps/cloud/in-cluster`.
4. For managed mode, customize both managed FQDN policies and URLs, then
   install the two Applications under `kubernetes/apps/cloud/managed`.
5. For external-ai-only inference, patch matcher `LLM_ORDER` to `external-ai`.
6. Configure Ingress selectors, DNS, TLS, provider endpoints, OAuth apps,
   webhook delivery, and any cloud internal-llm worker.
7. Verify `/health/ready`, create a test user, complete both consent flows,
   and exercise a benign mailbox scan. Readiness reports database, RabbitMQ,
   and each configured LLM queue connection without exposing credentials.

## Migration from the homelab-only layout

1. Take application-consistent PostgreSQL backups and preserve Codex auth
   state; do not delete any PVC or PV.
2. Confirm existing NFS exports and Secret identities, then provision the
   renamed Tapy Secret and retained NFS path as described in the Tapy runbook.
3. Update the Cloudflare Tunnel/DNS route and both OAuth registrations for
   `tapy.547600.xyz` before switching traffic.
4. Merge the layout and Argo path updates. The Tapy resource, storage, image,
   database, and Secret identities change as part of the rename; preserve and
   migrate the old data before allowing Argo to prune superseded resources.
5. Inspect rendered homelab overlays before allowing prune. Confirm
   `tapy-postgres-tapy-pv`, `external-ai-postgres-pv`, and
   `external-ai-codex-home-pv` still render as hard-bound `Retain` volumes.
6. Reconcile one Application at a time in the order above and test readiness
   and fallback. Keep backups until OAuth refresh, Codex refresh, restart, and
   recovery checks pass.

CI renders and client-validates every supported overlay and Argo Application.
Placeholder detection remains an operator release gate: checked-in cloud
overlays are safe templates, not production configuration.
