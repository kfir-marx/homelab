# Tapy

A durable backend with password, Google, and Microsoft authentication; per-user
flights; delegated Gmail and Outlook access; renewable provider webhooks; and
strict hotel-confirmation extraction through RabbitMQ. Internal and external
LLM workers are attempted in configurable order with fallback; deterministic
code alone scores a booking and closes the matching flight for upsell.

Raw mail bodies are never persisted. User/session identities, flights,
encrypted refresh tokens, webhook state, processed-message IDs, and match
metadata are stored in PostgreSQL.
See `../../../docs/tapy-runbook.md` for the API and deployment
contract.
