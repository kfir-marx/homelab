# Tapy

Portable homelab/cloud topology, cloud prerequisites, configuration matrices,
deployment order, and migration steps are documented in
[`tapy-kubernetes-portability.md`](tapy-kubernetes-portability.md).

## Organization onboarding and operator rollout

This section describes operator actions for a separately authorized live rollout.
Repository implementation and tests do not provision the pilot organizations.
Use a trusted operator shell with the intended `MATCHER_DATABASE_URL` supplied through
secret management. Never paste database credentials, invitation links or sessions into
logs, tickets, commits, or shared terminal recordings.

1. Back up PostgreSQL and record the Alembic revision. For an existing `20260909_02`
   database, run the new `tapy inventory` command before migration. It is read-only,
   reflects the old mailbox schema, and prints organization IDs, member roles/status,
   mailbox user/binding IDs and historical organization evidence without credentials.
   Review this output; no existing organization or user is presumed to be a pilot.
2. Stop old API and worker writers during the separately approved release. Run
   `tapy migrate`, then `tapy inventory` again. Compare counts and ownership with the
   backup and preflight inventory. Deploy API, worker and frontend together so an old
   registration endpoint or worker cannot bypass the new policy.
3. Provision initial admin invitations with explicit real addresses and frontend origin:

   ```bash
   tapy provision-pilots "$TEST_ADMIN_EMAIL" "$LAKISH_ADMIN_EMAIL" "$PUBLIC_ORIGIN"
   ```

   `PUBLIC_ORIGIN` must be the HTTPS frontend origin. This creates ordinary Tapy-test
   and Lakish-tours organization records using deterministic UUIDv5 IDs and independent
   memberships. Names confer no privilege. It prints initial seven-day invitation links
   once, after commit. Repeating it keeps the same IDs and does not issue duplicate
   invitations, create accounts, or give developers access to the customer organization.
   It never adopts a pre-existing organization just because its name matches.
4. Deliver each link privately to its intended recipient. A new user chooses a name
   and password (at least ten characters). An existing user signs in as the exact
   normalized invited email, then accepts. Possession of the email-bound invitation
   verifies the email in addition to that existing-account authentication. Acceptance
   creates/reactivates the invited membership and selects that organization. Existing
   active memberships keep their role; role changes use Team controls.
5. If a link was lost, expired or revoked, explicitly reissue it:

   ```bash
   tapy invite "$ORGANIZATION_ID" "$ADMIN_EMAIL" "$PUBLIC_ORIGIN" --role admin
   ```

   Reissue invalidates prior outstanding invitations for that email in that organization.
   Raw invitation tokens are not stored; they cannot be retrieved later. The database
   retains hashes, expiry, inviter, acceptance/revocation timestamps, and secret-free audit
   records. Replayed or concurrent acceptance can succeed only once.
6. Review each unbound mailbox. For an unambiguous operator-reviewed resolution, use:

   ```bash
   tapy bind-mailbox "$MAILBOX_ID" "$ORGANIZATION_ID"
   ```

   This requires an active owner membership and refuses conflicting historical evidence
   or an existing different binding. Mixed-organization history stays blocked and needs
   a separately reviewed recovery migration; never choose an arbitrary organization.
   Neither membership changes nor this command move bookings or history. Then reconnect
   the same mailbox account in its bound organization and test a bounded benign scan.

Admins manage colleagues in **Settings → Team**: create/copy invitations, revoke or
reissue them, change active roles, and deactivate members. Agent access remains limited
to assigned bookings/opportunities. Only admins assign work; booking assignment carries
through to its opportunities. Deactivation keeps the account, other memberships and all
business history; reactivation requires a new invitation. Concurrent removal/demotion
cannot eliminate the last active admin. If all admins are unavailable, an operator
issues a new admin invitation for that organization's ID using the CLI.

Public signup, public development-account creation, direct member addition and API
organization creation are disabled. Accounts without active memberships see **Invitation
required** and cannot access business endpoints or jobs. Social login accepts only an
already-linked provider/subject identity; it creates neither accounts nor memberships and
never links by email. Newly invited accounts use passwords for this pilot. Linking a new
social identity is intentionally not an email-match fallback.

Mailbox connection captures the organization at consent initiation and rechecks membership
at callback. Changing the selected organization during consent cannot change that binding.
Reconnect must use the same provider account and organization. Disconnect clears stored
credentials and disables local scanning, preserving the mailbox record, binding and history.
Provider grants may also be revoked in the provider's account settings. Switching organizations
shows only that organization's connected mailboxes. Scans, callbacks, webhook ingestion and
queued jobs use the explicit mailbox binding. Unbound mailboxes and inactive memberships
cannot ingest; workers recheck membership before privileged work and retained evidence writes.

Recovery: preserve the backup, current Alembic version, pilot IDs and OAuth encryption key.
The onboarding migration has no destructive downgrade. Prefer a forward fix; any backup
restore requires a separately approved outage and reconciliation of writes made since backup.
Do not roll back only the API to a version that permits public onboarding. Invitations and
member changes are recorded in `organization_audit`; a NULL actor identifies an operator
command. No API organization role grants platform operator authority.

## Future enterprise authentication boundary

`User` is the account, `AuthIdentity` authenticates it, and `OrganizationMembership` grants
organization roles. Business data stays attached to organization IDs. A future enterprise
release can add organization authentication connections (issuer, protocol and configuration)
and external identities keyed by `(connection_id, issuer, subject)`, with a user FK. One
user may have multiple connections, including multiple customer IdPs of the same provider.
Before enabling those connections, replace the current built-in-only `AuthIdentity`
`(provider, subject)` and `(user_id, provider)` uniqueness constraints: migrate each existing
identity to its explicit built-in connection without changing subjects or user IDs. Do not
reuse email as the permanent identity key or auto-link accounts by email. Account linking
must independently prove control of both identities. SSO, SCIM, domain auto-join, owner roles,
custom roles and enterprise configuration screens remain deferred. None requires replacing
memberships or moving bookings, opportunities or mailbox history.

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

Email is the sole source of upsell decisions. Alibaba Qwen extracts flight/hotel/neither
facts through the external-ai RabbitMQ RPC worker. The versioned extraction envelope
allows missing PNRs, tickets, locations and times, and retains extension metadata.
`email_booking_events` stores every changed interpretation plus provider/message/thread
identifiers, subject, sender and event time. Full message bodies remain at the provider.
Reprocessing fetches them again; it does not require a back-office integration.

`reconciliation.py` evaluates the latest interpretation of every processed email for an
agent in a tenant-locked transaction. It folds booking changes by reference and event
time, then evaluates flights against all retained accommodation evidence. It runs after
every booking email, after scans, and again before sending. Repeated processing creates
neither duplicate cards nor duplicate identical audit decisions. Flight bookings without
issued ticket numbers are supported. One booking produces at most one group opportunity.
Hotel-first evidence suppresses a later flight card; hotel-later evidence immediately
closes an existing actionable card. Cancellation and reclassification also invalidate it.

Matching requires a matching normalized traveler name or explicit contact, matching
city/airport text, and hotel dates covering the stay. A round trip uses the outbound
arrival destination and return departure as the stay window, not the final home airport.
Connections shorter than 24 hours are not separate stays. Unknown arrival dates,
multiple substantial stops and partially accommodated groups are withheld for review
and recorded in `reconciliation_decisions`. A one-way flight requires hotel coverage of
the known arrival night. Airport-to-city aliases, transliteration, fuzzy identities,
multi-room coverage and multi-city splitting are not inferred. This favors missing an
uncertain match over sending to the wrong traveler. Reference-less duplicates require
identical extracted facts; reference-less corrections cannot reliably be associated.
Provider names/references must remain consistent. A forwarded original event timestamp
is used only when explicitly extracted; otherwise the message date orders events.
A confirmation alone never reinstates a cancelled booking; explicit modification is
required. Missing facts in partial modification/cancellation messages preserve prior
facts; reprocessing a source message replaces its interpretation.

Lifecycle:

- `open`: flight evidence supports a potential accommodation upsell; send or dismiss.
- `contacted`: outreach has been queued or attempted. Job/delivery state separately
  distinguishes queued, submitted, partial, failed or uncertain delivery. It is never
  a claim that a hotel was booked.
- `declined`, or `closed` with `agent_dismissed`: agent dismissal; reconciliation never
  reopens it.
- `closed` with an evidence reason: hotel booked, flight cancelled, source reclassified,
  or insufficient evidence. It can reopen if the evidence changes and no outreach was
  attempted. Prior outreach remains `contacted` if evidence changes again.
- `expired`: known trip window has passed.

The card displays only known traveler/flight facts and send/dismiss controls. There is
no expected commission or manual success action. `partner_outcomes` is a separate,
append-only partner-event model for attributed booking values and actual commission.
There is currently no authenticated partner callback adapter; no outcomes are invented.

Sending queues a durable job and reserves the workflow in the same transaction. The
worker rechecks actionability before delivery. A send batch snapshots each recipient,
contact and rendered partner link. A partial failure remains visible in job delivery
results. An uncertain provider response or worker interruption is not automatically
resent: operators must inspect the provider to avoid duplicate customer messages.
A small external-send race remains if evidence arrives after the last eligibility check
and the provider accepts the message; already submitted messages cannot be recalled.
Configure `HOTEL_OFFER_URL` with a real partner/affiliate link before sending. The old
hard-coded destination, dates and guest count have been removed. No unsupported partner
query parameters or attribution promises are fabricated.

## Asynchronous execution and scaling

`tapy serve` only handles HTTP; `tapy worker` consumes durable `tapy.jobs` messages.
Scans, extraction, watch registration and sends execute in workers. HTTP scan/send
requests return `202` and a persisted job view. The database outbox commits before
publisher confirmation; workers recover unpublished jobs every ten seconds. Leases,
heartbeats and bounded retry backoff cover crashes and duplicate broker deliveries.
Scan retries skip committed messages. Jobs preserve the tenant and mailbox captured
at submission even if the user changes organizations. A mailbox remains explicitly bound
to the organization captured at connection; webhook routing follows that binding. Disconnected mailboxes cannot
continue ingestion. Gmail/Outlook readers paginate (100 messages per page) through the
configured scan scope, defaulting to at most 10,000 messages. Reaching a page cap is a
visible failure, not a false completed scan. Gmail no longer filters the default scan
to the most recent year. Full rescans for webhooks remain a cost bottleneck on large
mailboxes; provider history/delta cursors are a future optimization.

Backend and frontend have separate deployments, images and CPU HPAs (1–3 replicas,
70% utilization; requires metrics-server). Argo CD respects ignored replica fields
for those two deployments so self-heal does not undo HPA scaling. Workers have their own deployment and
replica setting, with one prefetched job per consumer. Scale worker replicas from
queue depth/oldest-job age using the cluster's external metrics system; a KEDA
installation is not assumed. PostgreSQL and tenant reconciliation locks remain shared
capacity constraints. Browser polling reconciles cross-process changes; local SSE is
an optional latency optimization, not the correctness mechanism.

Gmail watches include all mailbox changes, consistent with scans, per the
[watch API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/watch).
Outlook renewal uses [PATCH subscription](https://learn.microsoft.com/en-us/graph/api/subscription-update?view=graph-rest-1.0),
recreating only when the subscription no longer exists. Queue delivery follows
[RabbitMQ acknowledgement and publisher-confirmation semantics](https://www.rabbitmq.com/docs/confirms).

## Backend API

The browser uses a Secure, HttpOnly, SameSite=Lax session cookie. Passwords are
stored using salted scrypt hashes. Google and Microsoft login use the same
confidential OAuth registrations as mailbox consent, but request only identity
scopes. Login and mailbox grants remain separate operations.

- `POST /v1/auth/login` and `POST /v1/auth/logout` manage regular authentication.
  Public `POST /v1/auth/register` returns 403; use `POST /v1/invitations/accept`.
- `GET /v1/auth/{google|microsoft}/authorization` starts social login.
- `GET/PATCH /v1/users/me` returns the active organization, role, memberships,
  profile, and connections; PATCH can switch to another active membership.
- `POST /v1/organizations` and direct `POST /v1/organizations/current/memberships`
  return 403. Organization creation uses the operator CLI.
- `GET /v1/organizations/current/team`, `POST /v1/organizations/current/invitations`,
  `DELETE /v1/organizations/current/invitations/{id}`, and
  `PATCH /v1/organizations/current/memberships/{user_id}` support admin Team controls.
- `GET/POST /v1/bookings`, `GET/PATCH /v1/bookings/{id}`, and
  `POST /v1/bookings/{id}/tickets` expose the normalized booking graph.
  Person role/contact subresources support manual corrections without replacing
  the person or using a mutable name as identity.
- `GET /v1/opportunities`, `GET/PATCH /v1/opportunities/{id}`, and
  `PUT /v1/opportunities/{id}/recipients/{person_id}` list opportunities, dismiss them
  and select contacts. Manual opportunity creation returns 409.
- `GET /v1/opportunities/{id}/audit` explains derived and agent decisions.
- `POST /v1/opportunities/{id}/send` returns a queued job. Supply `Idempotency-Key`;
  replaying the same request returns that job without a second send.
- `GET /v1/jobs`, `GET /v1/jobs/{id}` expose progress, attempts, errors and delivery results.
  `POST /v1/jobs/{id}/retry` retries failed scans; uncertain sends need provider review.
- `GET /v1/metrics?scope=personal|organization` returns opportunity metrics;
  organization scope also contains per-agent rows.
- `GET /v1/events` streams local invalidations; the frontend polls every five seconds
  as a recovery path and immediately applies confirmed action responses locally.
- `GET /v1/notifications` lists the current user's notifications and
  `POST /v1/notifications/read` marks the current set read.
- `POST /v1/mailboxes/gmail/authorization` returns a Google consent URL.
- `POST /v1/mailboxes/outlook/authorization` returns a Microsoft consent URL.
- `GET /v1/oauth/{provider}/callback` consumes the one-time OAuth state and
  stores the encrypted refresh token and queues renewable provider-watch registration.
  The popup sends confirmed mailbox metadata so Scan/Disconnect render immediately.
- `POST /v1/scans` with `{"provider":"gmail"}` or `{"provider":"outlook"}`
  returns a queued job. Add `"reprocess":true` to fetch and re-extract older messages,
  including after upgrading from the previous schema. A connected mailbox's
  **Scan now** control in Settings exposes this bounded scan for users and
  provider reviewers.
- `POST /v1/webhooks/gmail` accepts Google Pub/Sub pushes and
  `POST /v1/webhooks/outlook` accepts Microsoft Graph notifications.

OAuth state is random, one-time, database-backed, purpose-bound, and expires
after ten minutes. A mailbox account can belong to only one user and one organization.
`POST /v1/agents` returns 403. Existing bearer credentials still authenticate their
accounts but confer no access without active organization membership.

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
   verify the validation-token handshake. Tapy renews existing subscriptions with `PATCH`. Before production,
   add a lifecycle-notification URL and handle
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
- Legacy API fields named `won_*` now derive only from the latest verified partner
  events; manual opportunity fields never contribute. Conversion is attributed
  opportunities divided by opportunities with outreach, not agent dismissals.
- Potential revenue/commission compatibility metrics are zero. The UI omits them.
- Without partner callbacks, actual booking and commission reporting is unavailable.

Monetary totals are grouped by ISO currency. Tapy never adds EUR to USD or
silently converts either; exchange-rate conversion is intentionally outside the
current product boundary.

## Database migration and preservation

Alembic replaces runtime `create_all()` as the deployment migration authority.
Deployment init containers run `tapy migrate` before API/worker startup; PostgreSQL
advisory locking serializes concurrent migrations. API replicas do not migrate on
request startup. Revision `20260909_02` adds evidence, audit, jobs and outcomes without
dropping existing data. It closes old actionable cards as `legacy_requires_reprocessing`,
because their hotel evidence was never retained. Run a reprocess scan after deployment
to rebuild eligible email-derived opportunities. Matching legacy email bookings are
adopted and duplicate per-ticket cards are retired, preserving prior dismissal and
outreach. Normal scans also automatically re-extract legacy processed messages.
The Settings UI provides a reprocess control. The baseline now refuses unversioned
legacy databases containing `tapy_flights`; it no longer resets them. Such databases
require a separate reviewed preservation migration. Do not clear the Alembic version
or delete a PVC to bypass this guard.

Revision `20260910_03` adds invitations, organization audit, email verification timestamps,
and nullable organization bindings on mailboxes and OAuth states. It preserves all
existing accounts, identities, organizations, memberships and business data. Mailboxes
are backfilled only when the union of processed-message, ingestion-source and retained
email-event evidence names exactly one organization. Zero or multiple organizations
leave the binding NULL and block ingestion. User selection is never backfill evidence.
Pending mailbox OAuth states created before this revision must be restarted.

## Configuration and secrets

The ConfigMap owns `LLM_ORDER`, both queue names and model names,
`MICROSOFT_TENANT`, optional explicit redirect URIs, `MATCH_THRESHOLD`, scan
limit, Gmail query and Pub/Sub topic, public/webhook URLs, secure-cookie mode,
`HOTEL_OFFER_URL`, and non-secret OAuth client IDs. The matcher
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
for the default exchange, and read/configure permission for `tapy.jobs` and those callback queues.
Capture both Secrets with
`scripts/secrets.sh capture-k8s tapy/tapy-secrets rabbitmq/rabbitmq-tapy-user`.

Before sync, use the Ansible workstation play to create
`/mnt/storage2-bulk/tapy/postgres`; do not create or alter the
filesystem itself. Also add `RABBITMQ_URL` and `ALIBABA_API_KEY` to
`external-ai/external-ai-secrets` and recapture that Secret.

## Namespace cutover

The homelab Application targets the dedicated `tapy` namespace. Its workload
names are `tapy-backend`, `tapy-worker` and `tapy-frontend`; only API/frontend have Services.
The namespace migration reuses `/mnt/storage2-bulk/tapy/postgres` through the
new retained `tapy-postgres-tapy-pv`. Stop the old PostgreSQL writer before
binding or starting the new one, and never run both against that directory.
Keep the old namespace, PVC, and `tapy-postgres-pv` until its backup has been
verified. Compare organization, identity, mailbox, and processed-message inventory
before and after migration. Unversioned legacy schemas require a preservation plan.
The new database URL
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
exceptions or `ACCESS_REFUSED`. Then accept an operator-issued Tapy-test invitation,
complete each provider's mailbox consent, scan benign test mail, and verify
external inference. Never place agent,
OAuth, RabbitMQ, or provider tokens in shell history.
