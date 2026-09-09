# Tapy frontend

The Next.js UI uses the same-origin `/v1/*` backend API and its HttpOnly session
cookie. It provides personal booking/opportunity data to every user and an
organization scope only when the backend reports an active admin membership.
Organization metrics and per-agent rows come from the backend; there is no
seeded or demo aggregation.

The frontend can create a basic booking/ticket/opportunity, edit opportunity
outcomes and recipient selections, and send an opportunity to all selected
recipients. Server-sent events plus periodic refresh keep views reconciled.

Run `npm run lint` and `npm run build`. Deployment is owned by
`kubernetes/system/tapy`.
