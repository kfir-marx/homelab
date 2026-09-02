# FlightStay Match POC runbook

## What is implemented

FlightStay Match is a customer-facing Chrome extension plus a stateless API.
The user explicitly starts every scan and approves Google's read-only Gmail
scope. The extension lists a bounded number of messages, fetches inline message
text from Gmail, and sends one bounded message at a time over HTTPS. It never
requests or downloads attachments.

The API validates that the short-lived Google access token was issued to this
exact Chrome OAuth client and includes `gmail.readonly`. It sends the email to
the cluster-private Qwen/vLLM API for schema extraction only. Pydantic rejects
unstructured or extra output, and deterministic Python code scores the
extracted city/country and check-in/check-out dates against every configured
flight. A cancelled booking always scores zero.

No Gmail token, message, extraction, or result is persisted. Uvicorn access
logging is disabled, vLLM request logging is disabled, and application logs
never include email content or identifiers. The current in-memory rate limit
is suitable for a single-replica POC, not a horizontally scaled production
service.

## POC configuration

The static flights are in
`kubernetes/system/hotel-flight-matcher/config.yaml`. Each entry has a stable
ID, arrival/departure dates, destination city/country, airport codes, and
aliases. The scorer evaluates every flight, so replacing this ConfigMap with a
per-user authenticated flight store later does not require changing the LLM
schema.

The assumed public origin is:

```text
https://staymatch.547600.xyz
```

Change that exact hostname together in the extension manifest/service worker,
Cloudflare policy/runbook, and OAuth home/privacy URLs if the startup chooses a
different product domain.

## Google OAuth setup

1. Create a dedicated Google Cloud project, enable the Gmail API, configure an
   [External OAuth consent screen](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification),
   and add only POC test users while the app is in Testing.
2. Upload a bootstrap ZIP as a draft Chrome Web Store item to obtain its stable
   32-character extension ID. Do not submit it for review yet.
3. In Google Cloud Credentials, create an OAuth client of type **Chrome
   Extension** and use that store item ID.
4. Replace `REPLACE_WITH_CHROME_EXTENSION_CLIENT_ID.apps.googleusercontent.com`
   with the issued client ID in both:
   - `extensions/flightstay-match/manifest.json`
   - `kubernetes/system/hotel-flight-matcher/config.yaml`
5. Keep only
   [`https://www.googleapis.com/auth/gmail.readonly`](https://developers.google.com/workspace/gmail/api/auth/scopes).
   Do not add
   modify, compose, send, broad mail, profile, or future-feature scopes.

For a developer-mode-only test, load the unpacked extension first, copy its ID
from `chrome://extensions`, create a Chrome Extension OAuth client for that ID,
then replace the two placeholders and reload. That client works only for the
same extension ID; use the Web Store item ID before distributing to customers.

## Local Chrome installation

After replacing the OAuth client ID:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select `/home/kfir/repos/homelab/extensions/flightstay-match`.
5. Pin **FlightStay Match** from Chrome's Extensions menu.
6. Click the icon. It opens a full extension tab so the authorization and model
   scan are not interrupted when a toolbar popup loses focus.
7. Review the disclosure, leave the initial Gmail query bounded, click
   **Connect Gmail & scan**, choose the test Google account, and approve
   read-only access.

The default POC query is `newer_than:365d` and the UI caps a scan at 20
messages. Narrow the query during testing, for example:

```text
newer_than:365d (hotel OR reservation OR booking OR confirmation)
```

**Disconnect Gmail** clears the extension's cached Chrome authorization
session. It does not revoke Google's server-side grant; users revoke that grant
from their Google Account's connected apps page.

## Package and Chrome Web Store

Create the initial draft package with the placeholder allowed only to obtain an
item ID:

```bash
scripts/package-flightstay-extension.sh --bootstrap
```

After installing the real OAuth client ID, create the review package:

```bash
scripts/package-flightstay-extension.sh
```

Follow the [Chrome Web Store publishing
workflow](https://developer.chrome.com/docs/webstore/publish/) and upload
`flightstay-match.zip` in the Chrome Developer Dashboard, then complete:

- Store Listing: clearly state the single purpose—finding flight-related hotel
  confirmations—and disclose that bounded email text is transmitted for
  private server-side inference.
- Privacy: declare authentication information, personal communications, and
  personally identifiable information; provide
  `https://staymatch.547600.xyz/privacy`; copy the Limited Use disclosure from
  that page without broadening the actual behavior.
- Distribution: start with **Unlisted** and only your POC countries/users, then
  move to Public after OAuth verification.
- Test instructions: provide a dedicated test Gmail account containing benign
  sample booking and non-booking emails plus a configured matching flight.

The package is Manifest V3, bundles all executable JavaScript, has no content
script, requests only `identity`, `storage`, Gmail API, and the single API
origin, and performs Gmail/API requests in its service worker.

## Mandatory public-launch gates

`gmail.readonly` is a Google **restricted** scope. Because this design
transmits restricted email data to a server for inference, a general customer
launch requires [Google OAuth restricted-scope
verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
and an annual security assessment by a Google-approved assessor. The Chrome
Web Store review is separate and additionally requires accurate [user-data
privacy declarations](https://developer.chrome.com/docs/webstore/user_data).

For a small POC, keep Google OAuth in Testing and explicitly add testers. Do
not describe the extension as generally available or work around the unverified
user cap. Before verification, publish a public product home page and privacy
policy on the same verified domain, keep support/contact details current, and
prepare a screen recording showing the consent and exact data use.

## Cloudflare Tunnel route

The tunnel is remotely managed, so the public hostname is an account-side
operation. In Cloudflare Zero Trust, add this route to the existing named
[tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/):

| Public hostname | Origin service |
|---|---|
| `staymatch.547600.xyz` | `http://hotel-flight-matcher.homelab-assistant.svc.cluster.local:8080` |

Keep Cloudflare Access disabled for this hostname because the extension already
authenticates requests with a Google token and cannot reliably follow an Access
browser redirect during background fetches. Enable Cloudflare WAF/rate limiting
for `/v1/*`, cap request bodies, and do not cache `/v1/*` responses. The
declarative cloudflared egress policy admits only this exact in-cluster pod and
port.

## Build, deploy, and verify

Static checks:

```bash
uv sync --directory services/hotel-flight-matcher --locked --extra dev
uv run --directory services/hotel-flight-matcher --locked --extra dev ruff format --check .
uv run --directory services/hotel-flight-matcher --locked --extra dev ruff check .
uv run --directory services/hotel-flight-matcher --locked --extra dev mypy src tests
uv run --directory services/hotel-flight-matcher --locked --extra dev pytest
node --test extensions/flightstay-match/tests/*.test.js
kubectl kustomize kubernetes/system/hotel-flight-matcher >/tmp/hotel-flight-matcher.yaml
```

The release workflow builds `ghcr.io/kfir-marx/hotel-flight-matcher` from
`main` and opens an image-pin PR. Merge that PR before expecting Argo CD to make
the Deployment healthy. The workload reuses only the `LLM_API_KEY` key from
`homelab-assistant-secrets`; it receives no Kubernetes token and no Gmail
credential Secret.

After the image pin, OAuth client ID, Secret restore, and Cloudflare route are
ready:

```bash
kubectl -n homelab-assistant rollout status deployment/hotel-flight-matcher
kubectl -n homelab-assistant get service hotel-flight-matcher
curl --fail https://staymatch.547600.xyz/health/ready
curl --fail https://staymatch.547600.xyz/privacy >/dev/null
```

An unauthenticated `/v1/analyze` request must return `401`. Complete the final
test through the extension so no Gmail token is printed or placed in shell
history. Confirm one real hotel confirmation matches the configured flight, a
newsletter does not, a cancellation scores zero, and neither backend nor vLLM
logs contain message text.
