# Tapy

Portable homelab/cloud topology, cloud prerequisites, configuration matrices,
deployment order, and migration steps are documented in
[`tapy-kubernetes-portability.md`](tapy-kubernetes-portability.md).

## Runtime design

Tapy is deployed as separate frontend and backend workloads. The public
`tapy` Service sends traffic to the Next.js frontend, which proxies `/v1/*`
to the private `tapy-backend` Service. This keeps the existing public hostname
and OAuth callback URIs stable. The backend never accepts mailbox access
tokens or email bodies from the frontend. The intended flow is:

```text
frontend -> password/Google/Microsoft login -> HttpOnly backend session
settings -> official mailbox consent page -> encrypted refresh token in PostgreSQL
provider webhook -> Gmail API or Microsoft Graph -> bounded email text
matcher -> RabbitMQ -> external-ai
matcher -> per-user deterministic flight scoring -> close flight on score > 0.90
```

The LLM may only classify `is_hotel_booking_confirmation` and populate the
strict `HotelBooking` schema. It does not choose flights, calculate a score, or
invoke actions. Invalid model output counts as a backend failure and triggers
the next configured LLM. The homelab development deployment uses only
`external-ai`, so Tapy startup and readiness do not depend on `internal-llm`.

Flights are stored in PostgreSQL and owned by one user. The frontend creates
them manually for now. Exact location and dates score 1.0; the configured
threshold is a strict lower bound. A winning match stores a small hotel
summary, closes the flight for upsell, and notifies connected frontends. If a
flight already has a hotel match, the backend leaves it unchanged and logs the
duplicate to standard output.

## Backend API

The browser uses a Secure, HttpOnly, SameSite=Lax session cookie. Passwords are
stored using salted scrypt hashes. Google and Microsoft login use the same
confidential OAuth registrations as mailbox consent, but request only identity
scopes. Login and mailbox grants remain separate operations.

- `POST /v1/auth/register`, `POST /v1/auth/login`, and `POST /v1/auth/logout`
  manage regular authentication.
- `GET /v1/auth/{google|microsoft}/authorization` starts social login.
- `GET/PATCH /v1/users/me` returns or updates the user profile and connections.
- `GET/POST /v1/flights` and `PATCH /v1/flights/{id}/status` own the live flight pipeline.
- `GET /v1/metrics` returns server-derived dashboard metrics.
- `GET /v1/events` streams invalidations; the frontend also refreshes on focus
  and every 30 seconds as a recovery path.
- `POST /v1/mailboxes/gmail/authorization` returns a Google consent URL.
- `POST /v1/mailboxes/outlook/authorization` returns a Microsoft consent URL.
- `GET /v1/oauth/{provider}/callback` consumes the one-time OAuth state and
  stores the encrypted refresh token and registers a renewable provider watch.
- `POST /v1/scans` with `{"provider":"gmail"}` or `{"provider":"outlook"}`
  remains available for manual recovery/testing.
- `POST /v1/webhooks/gmail` accepts Google Pub/Sub pushes and
  `POST /v1/webhooks/outlook` accepts Microsoft Graph notifications.

OAuth state is random, one-time, database-backed, purpose-bound, and expires
after ten minutes. A mailbox account can belong to only one user. The old
`/v1/agents` bearer-token endpoints remain only for development compatibility.

The database retains users, login identities and sessions, per-user flights,
encrypted provider refresh tokens, renewable webhook state, processed provider
message IDs, and small match summaries.
It never retains access tokens or message bodies. In the homelab overlay the
PostgreSQL PV is hard bound to the permanent critical NFS tier with `Retain`;
cloud uses either a dynamically provisioned retained PVC or managed PostgreSQL.

The frontend has no seeded business data. Flights, statuses, user data, and
metrics come from the backend. Its existing server actions call Twilio and Gemini directly
from the Next.js server; their credentials are never exposed as
`NEXT_PUBLIC_*` values.

## OAuth registration

For Google, create a Web application OAuth client, enable the Gmail API, and
register this exact redirect URI:

```text
https://tapy.547600.xyz/v1/oauth/gmail/callback
```

The only Google API scope is
`https://www.googleapis.com/auth/gmail.readonly`. Offline access is requested
so the backend receives a refresh token. Follow Google's official
[server-side Gmail authorization](https://developers.google.com/workspace/gmail/api/auth/web-server)
and restricted-scope verification requirements.

For Microsoft, register a confidential web application that accepts personal
Microsoft and organizational accounts, then register:

```text
https://tapy.547600.xyz/v1/oauth/outlook/callback
```

Grant delegated `User.Read` and `Mail.Read`; `offline_access` is requested in
the authorization flow. Do not grant application-wide mailbox permission.
Follow Microsoft's official
[authorization-code flow](https://learn.microsoft.com/en-us/graph/auth-v2-user).

Update `PUBLIC_BASE_URL` and both provider redirect registrations together if
the hostname changes. Keep Cloudflare Access disabled on the callback/API
hostname because the provider and frontend must reach it directly.

For Gmail push, create a Google Cloud Pub/Sub topic, grant Gmail permission to
publish to it, set `GMAIL_PUBSUB_TOPIC` to its full
`projects/<project>/topics/<topic>` name, and configure a push subscription to
`/v1/webhooks/gmail?token=<WEBHOOK_VERIFICATION_TOKEN>`. Gmail watches are
renewed by Tapy. Microsoft Graph subscriptions point directly to
`/v1/webhooks/outlook`, carry a per-mailbox `clientState`, and are renewed by
Tapy. `WEBHOOK_PUBLIC_BASE_URL` may override `PUBLIC_BASE_URL` for callbacks.

## Configuration and secrets

The ConfigMap owns `LLM_ORDER`, both queue names and model names,
`MICROSOFT_TENANT`, optional explicit redirect URIs, `MATCH_THRESHOLD`, scan
limit, Gmail query and Pub/Sub topic, public/webhook URLs, secure-cookie mode,
and non-secret OAuth client IDs. The matcher
uses only the durable queues selected by `LLM_ORDER`; it receives RabbitMQ
credentials, not either LLM's HTTP credential. The homelab overlay selects
only `external-ai.requests`.

Create `homelab-assistant/tapy-secrets` with:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `OAUTH_TOKEN_ENCRYPTION_KEY` (a Fernet key)
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `MICROSOFT_OAUTH_CLIENT_SECRET`
- `WEBHOOK_VERIFICATION_TOKEN` (strong random value; recommended)

Tapy uses Psycopg 3 for PostgreSQL. The preferred `DATABASE_URL` scheme is
`postgresql+psycopg://`; plain legacy `postgresql://` and `postgres://` schemes
are normalized to the installed Psycopg driver at startup.

Create `homelab-assistant/tapy-frontend-secrets` with:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `AGENT_PHONE_NUMBER`
- `GEMINI_API_KEY`

The local source values belong in the repository-level gitignored `.env`; the
same names are documented in `.env-template`. Capture the updated encrypted
environment bundle with `scripts/secrets.sh capture-env`. After creating the
Kubernetes Secret through the trusted local workflow, capture it with
`scripts/secrets.sh capture-k8s homelab-assistant/tapy-frontend-secrets`.

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
role, retained NFS directory, public hostname, cloud namespace, and cloud
network-policy labels together. Before the first sync, publish the
`ghcr.io/kfir-marx/tapy` and `ghcr.io/kfir-marx/tapy-frontend` images, create
and capture both Tapy Secrets while
preserving the existing OAuth encryption key, and complete an authorized
host-side PostgreSQL data migration into `/mnt/storage2-bulk/tapy/postgres`.
Provision `tapy.547600.xyz` in DNS and the Cloudflare Tunnel, and update the
Google and Microsoft OAuth registrations to use the Tapy callback URLs.
Keep the superseded retained volume and application resources until the Tapy
API, mailbox grants, and processed-message history have been verified.

## Verification and rollout

Static checks:

```bash
uv sync --directory services/tapy/backend --locked --extra dev
uv run --directory services/tapy/backend --locked --extra dev ruff format --check .
uv run --directory services/tapy/backend --locked --extra dev ruff check .
uv run --directory services/tapy/backend --locked --extra dev mypy src tests
uv run --directory services/tapy/backend --locked --extra dev pytest
docker build services/tapy/frontend
kubectl kustomize kubernetes/system/tapy >/tmp/tapy.yaml
kubectl kustomize kubernetes/system/tapy/overlays/cloud/in-cluster >/tmp/tapy-cloud.yaml
kubectl kustomize kubernetes/system/tapy/overlays/cloud/managed >/tmp/tapy-managed.yaml
```

An authorized rollout must converge the workstation directory first, then
RabbitMQ, external-ai, and finally tapy. The optional internal-llm can converge
independently. The release workflow
publishes both images and opens its immutable image-pin PR. Before merging that
PR, create and capture `tapy-frontend-secrets`. Do not sync the placeholder
frontend image or OAuth client IDs. After rollout, create a test agent through
the API, complete each provider's browser consent, scan benign test mail, and
verify external inference. Never place agent,
OAuth, RabbitMQ, or provider tokens in shell history.
