# Tapy

Portable homelab/cloud topology, cloud prerequisites, configuration matrices,
deployment order, and migration steps are documented in
[`tapy-kubernetes-portability.md`](tapy-kubernetes-portability.md).

## Runtime design

Tapy is deployed as separate frontend and backend workloads. The public
`tapy-frontend` Service on port 3000 sends traffic to the Next.js frontend,
which proxies `/v1/*` to the private `tapy-backend` Service on port 8080. Both
workloads and their PostgreSQL resources run only in the `tapy` namespace.
This keeps the existing public hostname
and OAuth callback URIs stable. The backend never accepts mailbox access
tokens or email bodies from the frontend. The intended flow is:

```text
frontend -> password/Google/Microsoft login -> HttpOnly backend session
settings -> official mailbox consent page -> encrypted refresh token in PostgreSQL
provider webhook -> Gmail API or Microsoft Graph -> bounded email text
matcher -> RabbitMQ -> external-ai
matcher -> flight email -> one deduplicated open flight per passenger ticket
matcher -> hotel email -> per-user deterministic flight scoring -> close flight on score > 0.90
backend -> Twilio WhatsApp -> close flight only after delivery acceptance
backend -> persisted per-user notifications -> SSE frontend refresh
```

The LLM may only classify hotel and flight confirmations and populate the
strict `EmailExtraction` schema. A confirmed flight email contains one
`FlightTicket` per passenger, so a shared multi-passenger booking creates
separate flight rows without introducing a flight-booking aggregate. The LLM
does not choose existing flights, calculate a score, deduplicate records, or
invoke actions. Invalid model output counts as a backend failure and triggers
the next configured LLM. The homelab development deployment uses only
`external-ai`, so Tapy startup and readiness do not depend on `internal-llm`.

Flights are stored in PostgreSQL and owned by one user. Manual and email
sources pass through the same insertion path. A candidate with the same
normalized passenger, destination, and departure timestamp (or date when an
exact time is unavailable) is skipped as a duplicate. Every new flight starts
open for upsell and publishes an SSE invalidation to connected browsers.
Exact hotel location and dates score 1.0; the configured
threshold is a strict lower bound. A winning match stores a small hotel
summary, closes the flight for upsell, and notifies connected frontends. If a
flight already has a hotel match, the backend leaves it unchanged and logs the
duplicate to standard output.

User notifications are persistent records, not operational logs. Tapy creates
them only when a flight is added, an upsell message is accepted and the flight
is closed, WhatsApp delivery fails, or flight insertion fails. Opening the
notification menu marks all current notifications read; later notifications
restore the unread indicator.

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
- `GET /v1/notifications` lists the current user's notifications and
  `POST /v1/notifications/read` marks the current set read.
- `POST /v1/flights/{id}/send-upsell` sends the customer WhatsApp message and
  closes the flight only when Twilio accepts it.
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
flight ingestion metadata, user notifications,
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

Create `tapy/tapy-secrets` with:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `RABBITMQ_URL`
- `OAUTH_TOKEN_ENCRYPTION_KEY` (a Fernet key)
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `MICROSOFT_OAUTH_CLIENT_SECRET`
- `WEBHOOK_VERIFICATION_TOKEN` (strong random value; recommended)

Tapy uses Psycopg 3 for PostgreSQL. The preferred `DATABASE_URL` scheme is
`postgresql+psycopg://`; plain legacy `postgresql://` and `postgres://` schemes
are normalized to the installed Psycopg driver at startup.

Create `tapy/tapy-frontend-secrets` with:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `GEMINI_API_KEY`

The backend also reads the two Twilio credentials from this Secret because it
owns the send/close/notify transaction. The frontend continues to use the
Gemini credential only from its server-side chat action.

The local source values belong in the repository-level gitignored `.env`; the
same names are documented in `.env-template`. Capture the updated encrypted
environment bundle with `scripts/secrets.sh capture-env`. After creating the
Kubernetes Secret through the trusted local workflow, capture it with
`scripts/secrets.sh capture-k8s tapy/tapy-frontend-secrets`.

Generate the Fernet value with
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
on a trusted machine. Never rotate it without re-encrypting every stored OAuth
token. Tapy's dedicated RabbitMQ user is `tapy`. Its AMQP URL uses the
`homelab` vhost and lives only in `tapy/tapy-secrets`. The broker bootstrap
reconciles that user from `rabbitmq/rabbitmq-tapy-user` after every blank-pod
start with configure permission for the two request queues and narrowly matched
`amq.gen-*` or `amq_<32 lowercase hex>` callback queues, write permission only
for the default exchange, and read permission only for those callback queues.
Capture both Secrets with
`scripts/secrets.sh capture-k8s tapy/tapy-secrets rabbitmq/rabbitmq-tapy-user`.

Before sync, use the Ansible workstation play to create
`/mnt/storage2-bulk/tapy/postgres`; do not create or alter the
filesystem itself. Also add `RABBITMQ_URL` and `ALIBABA_API_KEY` to
`external-ai/external-ai-secrets` and recapture that Secret.

## Namespace cutover

The homelab Application targets the dedicated `tapy` namespace. Its workload
names are `tapy-backend` and `tapy-frontend`; its Services have the same names.
The namespace migration reuses `/mnt/storage2-bulk/tapy/postgres` through the
new retained `tapy-postgres-tapy-pv`. Stop the old PostgreSQL writer before
binding or starting the new one, and never run both against that directory.
Keep the old namespace, PVC, and `tapy-postgres-pv` until the new API, mailbox
grants, and processed-message history have been verified. The new database URL
must target `tapy-postgres.tapy.svc.cluster.local` while preserving the existing
database password and OAuth encryption key exactly. The Cloudflare Tunnel
origin is `http://tapy-frontend.tapy.svc:3000`.

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
kubectl create --dry-run=client --validate=false -f /tmp/tapy.yaml -o name
```

An authorized rollout must converge the workstation directory first, then
RabbitMQ, external-ai, and finally tapy. The optional internal-llm can converge
independently. The release workflow
publishes both images and opens its immutable image-pin PR. Before merging that
PR, create and capture `tapy-frontend-secrets`. Do not sync the placeholder
frontend image or OAuth client IDs. After rollout, confirm `tapy-backend` and
`tapy-frontend` are Ready, check `/health/live` and `/health/ready`, submit a
benign request through `external-ai.requests`, and inspect logs for startup
exceptions or `ACCESS_REFUSED`. Then create a test agent through the API,
complete each provider's browser consent, scan benign test mail, and verify
external inference. Never place agent,
OAuth, RabbitMQ, or provider tokens in shell history.
