# Tapy frontend

The independently deployed Next.js UI uses the same-origin `/v1/*` backend API
and its HttpOnly session cookie. Agents connect email, follow background scan
progress, and send an accommodation partner link or dismiss an opportunity.
Known traveler/flight facts appear on cards; partner outcomes are separate.

Confirmed action responses update local state immediately. Versioned refreshes
and polling reconcile server/worker changes without waiting to render mailbox
controls or remove a sent/dismissed card.

Run `npm run lint`, `npm run build`, and `npm run start -- --port 3100`.
With `playwright==1.62.0` and Chromium installed, run `python tests/browser.py`.
Tests intercept all APIs and include stalled-refresh and failed-action cases.
`TAPY_TEST_URL` and `TAPY_TEST_CHROMIUM` optionally select the local server/browser.
Deployment is owned by `kubernetes/system/tapy`.
