# Tapy backend

Tapy stores multi-tenant travel bookings, people, contact points, PNRs,
segments, tickets, opportunities, recipients, send batches, and individual
deliveries in PostgreSQL. Opportunity outcome, recipient selection, delivery,
and booking lifecycles are deliberately independent.

The strict LLM schema extracts explicit facts only. Deterministic code owns
deduplication, entity resolution, the current one-ticket-per-opportunity rule,
recipient fallback, matching, and sending. Raw mail bodies are never persisted.

Alembic runs to the latest revision when the API starts. `tapy migrate` is also
available as an explicit migration command. See
[`docs/tapy-runbook.md`](../../../docs/tapy-runbook.md) for reset behavior,
metric formulas, API scope rules, and deployment verification.
