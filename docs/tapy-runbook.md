# Tapy

Portable homelab/cloud topology, cloud prerequisites, configuration matrices,
deployment order, and migration steps are documented in
[`tapy-kubernetes-portability.md`](tapy-kubernetes-portability.md).

## Runtime and data design

The public Next.js frontend proxies `/v1/*` to the private backend. Both run in
the `tapy` namespace. PostgreSQL is the authority for organizations and active
memberships, travel bookings, people and roles, contact points, real PNRs,
segments, passenger tickets, opportunities, covered-ticket links, recipients,
send batches, individual deliveries, notifications, and ingestion provenance.

Every business row carries an organization boundary. Composite foreign keys
prevent bookings, assigned agents, people, tickets, opportunities, recipients,
and deliveries from being connected across organizations. A request uses the
user's explicit active organization. Personal scope filters bookings and
opportunities by assigned agent; organization scope requires an active `admin`
membership and is rejected with HTTP 403 for an `agent`.

The strict `EmailExtraction` schema represents multiple people, explicit roles
and contacts, multiple PNRs, timezone-aware segments, and passenger tickets.
The LLM extracts facts only and must not infer group leadership or select a
recipient. Deterministic code resolves the graph using provider message IDs,
ticket numbers, unchanged PNRs, segment fingerprints, and stable source IDs.
The current application rule creates one opportunity per newly resolved ticket.
`opportunity_tickets` is many-to-many, so manual and future logic can attach
multiple tickets to one opportunity without a schema change.

The current recipient fallback records the ticket holder as
`legacy_ticket_holder`. If that person has no extracted contact, the recipient
is `needs_contact`; no group-leader fact is invented. Agents can manually add
multiple selected or excluded people, choose each contact, and record selection
reason and confidence.

Sending creates one `upsell_send_batch` with completion policy `all_selected`
and one immutable `message_delivery` snapshot per attempted recipient. Twilio
acceptance records `submitted`; it changes an open opportunity to `contacted`,
never `won`. A mixture of accepted and failed deliveries makes the batch
`partial`. Reusing the same idempotency key replays the batch, and a new retry
skips recipients with an earlier `submitted` or `delivered` result. Conversion
requires an explicit opportunity transition to `won`.

## Backend API

The browser uses a Secure, HttpOnly, SameSite=Lax session cookie. Passwords are
stored using salted scrypt hashes. Google and Microsoft login use the same
confidential OAuth registrations as mailbox consent, but request only identity
scopes. Login and mailbox grants remain separate operations.

- `POST /v1/auth/register`, `POST /v1/auth/login`, and `POST /v1/auth/logout`
  manage regular authentication.
- `GET /v1/auth/{google|microsoft}/authorization` starts social login.
- `GET/PATCH /v1/users/me` returns the active organization, role, memberships,
  profile, and connections; PATCH can switch to another active membership.
- `POST /v1/organizations` and `POST /v1/organizations/current/memberships`
  create an organization or let an admin add an existing user as admin/agent.
- `GET/POST /v1/bookings`, `GET/PATCH /v1/bookings/{id}`, and
  `POST /v1/bookings/{id}/tickets` expose the normalized booking graph.
  Person role/contact subresources support manual corrections without replacing
  the person or using a mutable name as identity.
- `GET/POST /v1/opportunities`, `GET/PATCH /v1/opportunities/{id}`, and
  `PUT /v1/opportunities/{id}/recipients/{person_id}` manage opportunities,
  covered tickets, outcomes, and recipient selection.
- `POST /v1/opportunities/{id}/send` sends all selected valid recipients and
  returns the batch plus individual delivery outcomes. Supply `Idempotency-Key`.
- `GET /v1/metrics?scope=personal|organization` returns opportunity metrics;
  organization scope also contains per-agent rows.
- `GET /v1/events` streams invalidations; the frontend also refreshes on focus
  and every 30 seconds as a recovery path.
- `GET /v1/notifications` lists the current user's notifications and
  `POST /v1/notifications/read` marks the current set read.
- `POST /v1/mailboxes/gmail/authorization` returns a Google consent URL.
- `POST /v1/mailboxes/outlook/authorization` returns a Microsoft consent URL.
- `GET /v1/oauth/{provider}/callback` consumes the one-time OAuth state and
  stores the encrypted refresh token and registers a renewable provider watch.
- `POST /v1/scans` with `{"provider":"gmail"}` or `{"provider":"outlook"}`
  remains available for manual recovery/testing. A connected mailbox's
  **Scan now** control in Settings exposes this bounded scan for users and
  provider reviewers.
- `POST /v1/webhooks/gmail` accepts Google Pub/Sub pushes and
  `POST /v1/webhooks/outlook` accepts Microsoft Graph notifications.

OAuth state is random, one-time, database-backed, purpose-bound, and expires
after ten minutes. A mailbox account can belong to only one user. The old
`/v1/agents` bearer-token endpoints remain only for development compatibility.

The database retains normalized business records, encrypted refresh tokens,
renewable webhook state, raw provider identifiers, derived fingerprints, and
small result summaries. It never retains access tokens or message bodies. In the homelab overlay the
PostgreSQL PV is hard bound to the permanent critical NFS tier with `Retain`;
cloud uses either a dynamically provisioned retained PVC or managed PostgreSQL.

The frontend has no seeded business data or demo aggregation. Personal and
admin-only organization views use the backend resources and metrics. Twilio is
called only by the backend; credentials are never exposed to the browser.

## Public legal pages

The frontend serves the legal pages without authentication on the same public
origin used for OAuth:

```text
https://tapy.547600.xyz/
https://tapy.547600.xyz/privacy
https://tapy.547600.xyz/terms
```

The logged-out homepage identifies Tapy and its operator, fully describes the
booking-to-upsell workflow, explains why Google user data is requested, and
links to both legal pages. It remains public without an account. The authenticated
shell links to the legal pages as well. The mailbox settings disclosure immediately
before each consent action describes the read-only scope, bounded message
processing, and Alibaba Cloud Qwen transfer. Keep the homepage, legal-page text,
provider list, retention practice, and in-product disclosure synchronized with
runtime behavior before changing a data flow or subprocessor. The homepage and
Privacy Policy URLs configured in Google Cloud must exactly match the production
URLs above.

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
`/v1/webhooks/outlook` and carry a per-mailbox `clientState`. The current
implementation periodically tries to create a replacement subscription; it
must use Graph subscription renewal and lifecycle notifications before a
production launch, as described below. `WEBHOOK_PUBLIC_BASE_URL` may override
`PUBLIC_BASE_URL` for callbacks.

## Provider promotion from development to production

These console-side steps are not managed by Kubernetes or Argo CD. Complete
them against dedicated production registrations; keep the development Google
project and Microsoft app registration for development traffic only. Changing
an OAuth client/application ID invalidates Tapy's ability to refresh grants
issued to the old client, so plan an explicit reconnect prompt for every
mailbox during cutover.

Before either provider is promoted:

1. Freeze the production HTTPS origin and callback paths. Publish working home,
   privacy-policy, terms, support, account-disconnection, and data-deletion
   pages on that origin. The privacy disclosure must describe transient email
   processing by Tapy and the configured external LLM provider.
2. Create production-only OAuth credentials and store them through the
   production secret workflow. Do not put localhost, developer, or staging
   redirect URIs in a production registration.
3. Assign at least two maintained owner/contact accounts, route provider review
   and credential-expiry mail to an attended address, and record who owns
   renewals, quota alerts, consent reviews, and incident response.
4. Keep the callback and webhook origin publicly reachable without Cloudflare
   Access or another interactive login gate. Configure a non-empty, strong
   `WEBHOOK_VERIFICATION_TOKEN`, redact it from request logs, and restrict all
   unrelated routes at the application or edge.

### Google Cloud and Gmail

1. Create a separate production Google Cloud project, enable the Gmail API and
   Pub/Sub API, attach the required billing account, and create production
   OAuth and Pub/Sub resources. Google explicitly recommends separate testing
   and production projects in its
   [OAuth production-readiness guidance](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).
2. In Google Auth Platform, configure production branding, support/developer
   contacts, the final homepage, privacy policy, terms, and authorized domain.
   Verify domain ownership in Search Console. Register only the exact
   production Web redirect URI; do not copy test origins or callbacks into the
   production client.
3. Choose and document the audience:

   - Use **Internal** only when every user belongs to the same Google Workspace
     organization. It excludes consumer and other-organization accounts.
   - Otherwise use **External**, publish the app to **Production**, and submit
     it for verification. Testing mode is limited to named test users and its
     mailbox refresh-token grants expire after seven days; it is not a
     production workaround. See Google's
     [audience and publishing-status rules](https://support.google.com/cloud/answer/15549945).
4. Declare only the identity scopes used for Google login plus
   `https://www.googleapis.com/auth/gmail.readonly` for mailbox connection.
   `gmail.readonly` is a restricted scope. Prepare the scope justification,
   end-to-end consent/use demo video, reviewer instructions, and evidence that
   the published privacy policy accurately describes access, processing,
   retention, sharing, deletion, and revocation. Confirm that the external LLM
   provider's retention, training, human-access, and onward-transfer terms
   comply with the Google Workspace API
   [User Data Policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy),
   not merely that the transfer is disclosed. That policy lists automated
   travel itineraries and flight tracking as an approved Gmail use case, but
   this does not waive verification. Submit the production app using Google's
   [verification procedure](https://support.google.com/cloud/answer/13461325).
   Use the repository-local
   [Google OAuth verification packet](tapy-google-oauth-verification.md) for the
   paste-ready justification, reviewer instructions, privacy evidence matrix,
   synthetic test message, and demo-video shot list. Reconcile it with the
   deployed production revision and current Google requirements before use.
5. Unless Tapy qualifies for and documents an exception, its server-side use
   and transmission of restricted Gmail data requires a Google-approved CASA
   security assessment and annual reassessment. Budget lead time and assessor
   cost before announcing a launch. Before assessment, close the production
   security gaps required by the Workspace policy: production-grade key
   management, encryption in transit and at rest, prompt-injection protection
   for email sent to the LLM, user-data deletion, and security-incident
   handling. Google's
   [restricted-scope requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
   are the release authority.
6. Create the production Pub/Sub topic and grant
   `gmail-api-push@system.gserviceaccount.com` publisher access to that topic.
   Create the HTTPS push subscription using the production webhook URL, then
   configure retry/retention and delivery-failure monitoring. Tapy currently
   authenticates Gmail pushes with the strong query token; ensure edge and
   application logs redact it. Supporting and validating a Pub/Sub OIDC push
   token is the preferred follow-up before removing that shared token. Follow
   the Gmail
   [push notification setup](https://developers.google.com/workspace/gmail/api/guides/push).
7. Configure quota and billing alerts, watch Pub/Sub undelivered-message age,
   and alert on Gmail watch-renewal failures. After deploying the production
   client ID, secret, and topic name, reconnect a production mailbox and prove
   consent, initial scan, push delivery, token refresh, watch renewal, and
   disconnect behavior before inviting users.

Do not add a new scope directly to the production project and deploy it at the
same time. Test it in the development project, submit the production scope
change for approval, and deploy only after approval; Google may require
reverification when branding, redirect, privacy-policy, or scope configuration
changes.

### Microsoft Entra ID and Outlook

1. Create a production app registration separate from development. Choose the
   supported account type deliberately: single tenant for one organization,
   multitenant for external organizations, or multitenant plus personal
   Microsoft accounts for Outlook.com users. Set `MICROSOFT_TENANT` to the
   matching tenant ID or `common`; do not leave `common` on a deliberately
   single-tenant deployment.
2. Add the exact production callback as a **Web** redirect URI over HTTPS and
   remove localhost and development redirects. Microsoft also recommends
   separate registrations for this separation in its
   [redirect URI guidance](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url).
3. Complete production branding, support/privacy/terms URLs, verify the
   publisher domain, and set maintained owners. For a customer-facing
   multitenant application, complete
   [publisher verification](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview)
   before launch when eligible. An unverified publisher can be blocked by
   customer tenant consent policies even though `User.Read` and delegated
   `Mail.Read` do not normally require administrator consent.
4. Keep only delegated `User.Read` and `Mail.Read`; do not add application-wide
   mail permissions. Test both a normal user-consent tenant and a tenant that
   disables user consent. Provide an administrator-consent/onboarding path for
   customers whose policy blocks users, following Microsoft's
   [consent guidance](https://learn.microsoft.com/en-us/entra/identity-platform/application-consent-experience).
5. Resolve the production credential gap before launch. Microsoft recommends a
   certificate or federated credential instead of a client secret in
   production, while Tapy currently implements only `client_secret` token
   exchange. Prefer adding certificate client-assertion support. If a client
   secret is temporarily accepted as a documented exception, keep it in the
   production secret manager, use overlapping rotation, alert well before
   expiry, and test rotation without disconnecting users. See Microsoft's
   [credential guidance](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials).
6. Expose the production Graph webhook directly over valid public HTTPS and
   verify the validation-token handshake. Before production, change Tapy to
   renew the existing subscription with `PATCH /subscriptions/{id}` instead of
   attempting a duplicate `POST`, add a lifecycle-notification URL, and handle
   `reauthorizationRequired`, `subscriptionRemoved`, and `missed` events. Graph
   can delay or drop notifications from slow endpoints, so queue promptly,
   return `202`, monitor response latency and renewal failures, and retain the
   bounded scan as a recovery path. Microsoft's
   [webhook delivery](https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks)
   and
   [lifecycle notification](https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events)
   documentation defines these production requirements.
7. Deploy the production application ID and credential, then test organizational
   and personal accounts as selected. Prove login, mailbox consent, refresh,
   webhook creation and renewal, event delivery, missed-event recovery, tenant
   revocation, and disconnect behavior before retiring the development app.

Production promotion is complete only when Google verification/CASA status (or
a documented exception), Microsoft audience/publisher/tenant-consent decisions,
credential rotation, webhook renewal, provider alerts, and end-to-end mailbox
reconnection have named owners and passing evidence.

## Metrics

Metrics count each opportunity once, regardless of covered-ticket or recipient
count. Personal scope includes only opportunities assigned to the current user;
organization scope includes the active tenant and is admin-only.

- Lifecycle counts are exact counts by opportunity status.
- Delivery successes count `submitted` plus `delivered` delivery rows; failures
  count `failed` rows. These do not imply conversion.
- Conversion rate is `won / (won + declined + expired + closed)`. Open and
  contacted opportunities are not decided and are excluded from the denominator.
- Potential revenue/commission sums open and contacted opportunities.
- Won revenue/commission sums only won opportunities.
- Organization per-agent metrics use the same formula and assignment boundary.

Monetary totals are grouped by ISO currency. Tapy never adds EUR to USD or
silently converts either; exchange-rate conversion is intentionally outside the
current product boundary.

## Database migration and reset

Alembic replaces runtime `create_all()` as the deployment migration authority.
The API upgrades to the latest revision before accepting traffic; operators can
also run `tapy migrate` explicitly. Revision `20260908_01` is a pre-production
reset migration: when it detects the old `tapy_flights` table it drops only the
enumerated Tapy tables and creates the normalized schema. It does not delete the
PVC/PV or touch other schemas/workloads.

This reset irrecoverably removes Tapy users, sessions, mailbox grants, webhook
state, processed-message history, and old flight rows unless PostgreSQL was
backed up first. After it runs, create the initial admin again and reconnect
every mailbox so watches/subscriptions are recreated. A PVC deletion is not
required. If an operator nevertheless chooses to remove retained Tapy storage,
resolve the exact namespace/PVC/PV first and follow the repository's live-action
safety rules.

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

The backend reads the two Twilio credentials from this Secret because it owns
send batches and delivery records. The frontend receives no provider secret.

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
Keep the old namespace, PVC, and `tapy-postgres-pv` until its backup has been
verified. The normalized-schema migration resets legacy Tapy records, so mailbox
grants and processed-message history cannot be verified after migration without
restoring that backup. The new database URL
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
