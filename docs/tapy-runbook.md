# Tapy backend

Portable homelab/cloud topology, cloud prerequisites, configuration matrices,
deployment order, and migration steps are documented in
[`staymatch-kubernetes-portability.md`](staymatch-kubernetes-portability.md).

## Runtime design

The service is a backend-only proof of concept. It does not require a browser
extension and it never accepts mailbox access tokens or email bodies from a
frontend. The flow is:

```text
frontend -> create agent -> open official provider consent page
provider -> OAuth callback -> encrypted refresh token in PostgreSQL
frontend -> start scan -> Gmail API or Microsoft Graph -> bounded email text
matcher -> RabbitMQ -> internal-llm, then external-ai on failure
matcher -> deterministic flight scoring -> function boundary on score > 0.90
```

The LLM may only classify `is_hotel_booking_confirmation` and populate the
strict `HotelBooking` schema. It does not choose flights, calculate a score, or
invoke actions. Invalid model output counts as a backend failure and triggers
the next configured LLM. The default order is `internal-llm,external-ai`.

The current flight source remains `/config/flights.json`. The application calls
it through a per-agent `FlightRepository` boundary so a future database-backed
one-to-many agent/flight model does not change mail ingestion, extraction, or
scoring. Exact location and dates score 1.0; the configured threshold is a
strict lower bound, so the default action runs only when `score > 0.90`.

## Backend API

`POST /v1/agents` creates an agent and returns its opaque bearer token once.
The future frontend must keep that token out of URLs and browser logs. All
remaining frontend calls use `Authorization: Bearer <agent token>`.

- `GET /v1/agents/me` returns the agent and connected provider names.
- `POST /v1/mailboxes/gmail/authorization` returns a Google consent URL.
- `POST /v1/mailboxes/outlook/authorization` returns a Microsoft consent URL.
- `GET /v1/oauth/{provider}/callback` consumes the one-time OAuth state and
  stores the encrypted refresh token.
- `POST /v1/scans` with `{"provider":"gmail"}` or `{"provider":"outlook"}`
  fetches and analyzes up to the configured message limit.

OAuth state is random, one-time, database-backed, and expires after ten
minutes. A mailbox account can belong to only one agent. Agent-token creation
is deliberately a minimal first-iteration identity boundary; replace it with
the eventual frontend's login/session system before a public multi-user launch.

The database retains agent identities, encrypted provider refresh tokens,
mailbox identity, processed provider message IDs, and small match summaries.
It never retains access tokens or message bodies. In the homelab overlay the
PostgreSQL PV is hard bound to the permanent critical NFS tier with `Retain`;
cloud uses either a dynamically provisioned retained PVC or managed PostgreSQL.

## OAuth registration

For Google, create a Web application OAuth client, enable the Gmail API, and
register this exact redirect URI:

```text
https://staymatch.547600.xyz/v1/oauth/gmail/callback
```

The only Google API scope is
`https://www.googleapis.com/auth/gmail.readonly`. Offline access is requested
so the backend receives a refresh token. Follow Google's official
[server-side Gmail authorization](https://developers.google.com/workspace/gmail/api/auth/web-server)
and restricted-scope verification requirements.

For Microsoft, register a confidential web application that accepts personal
Microsoft and organizational accounts, then register:

```text
https://staymatch.547600.xyz/v1/oauth/outlook/callback
```

Grant delegated `User.Read` and `Mail.Read`; `offline_access` is requested in
the authorization flow. Do not grant application-wide mailbox permission.
Follow Microsoft's official
[authorization-code flow](https://learn.microsoft.com/en-us/graph/auth-v2-user).

Update `PUBLIC_BASE_URL` and both provider redirect registrations together if
the hostname changes. Keep Cloudflare Access disabled on the callback/API
hostname because the provider and future frontend must reach it directly.

## Configuration and secrets

The ConfigMap owns `LLM_ORDER`, both queue names and model names,
`MICROSOFT_TENANT`, optional explicit redirect URIs, `MATCH_THRESHOLD`, scan
limit, Gmail query, public URL, and non-secret OAuth client IDs. The matcher
uses the `internal-llm.requests` and `external-ai.requests` durable queues
directly; it receives RabbitMQ credentials, not either LLM's HTTP credential.

Create `homelab-assistant/tapy-secrets` with:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `OAUTH_TOKEN_ENCRYPTION_KEY` (a Fernet key)
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `MICROSOFT_OAUTH_CLIENT_SECRET`

Generate the Fernet value with
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
on a trusted machine. Never rotate it without re-encrypting every stored OAuth
token. Add queue permissions for the matcher RabbitMQ identity to publish to
both request queues and use server-named reply queues. Capture the Secret with
`scripts/secrets.sh capture-k8s homelab-assistant/tapy-secrets`.

Before sync, use the Ansible workstation play to create
`/mnt/storage2-bulk/tapy/postgres`; do not create or alter the
filesystem itself. Also add `RABBITMQ_URL` and `ALIBABA_API_KEY` to
`external-ai/external-ai-secrets` and recapture that Secret.

## Rename cutover

The rename changes the Argo CD Application, Kubernetes workload and storage
object names, Secret name, container image repository, PostgreSQL database and
role, and retained NFS directory together. Before the first sync, publish the
`ghcr.io/kfir-marx/tapy` image, create and capture `tapy-secrets` while
preserving the existing OAuth encryption key, and complete an authorized
host-side PostgreSQL data migration into `/mnt/storage2-bulk/tapy/postgres`.
Keep the superseded retained volume and application resources until the Tapy
API, mailbox grants, and processed-message history have been verified.

## Verification and rollout

Static checks:

```bash
uv sync --directory services/tapy --locked --extra dev
uv run --directory services/tapy --locked --extra dev ruff format --check .
uv run --directory services/tapy --locked --extra dev ruff check .
uv run --directory services/tapy --locked --extra dev mypy src tests
uv run --directory services/tapy --locked --extra dev pytest
kubectl kustomize kubernetes/system/tapy >/tmp/tapy.yaml
kubectl kustomize kubernetes/system/tapy/overlays/cloud/in-cluster >/tmp/tapy-cloud.yaml
kubectl kustomize kubernetes/system/tapy/overlays/cloud/managed >/tmp/tapy-managed.yaml
```

An authorized rollout must converge the workstation directory first, then
RabbitMQ, internal-llm, external-ai, and finally tapy. Do not
sync the placeholder image or OAuth client IDs. After rollout, create a test
agent through the API, complete each provider's browser consent, scan benign
test mail, and verify internal failure falls back to external inference. Never
place agent, OAuth, RabbitMQ, or provider tokens in shell history.
