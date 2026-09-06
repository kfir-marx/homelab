# Tapy

A durable backend that creates application agents, connects Gmail and Outlook
through their official delegated OAuth APIs, reads bounded message bodies, and
extracts a strict hotel-confirmation schema through RabbitMQ. Internal and
external LLM workers are attempted in configurable order with fallback; only
deterministic code scores a booking against flights.

Raw mail bodies are never persisted. Agent identities, encrypted refresh
tokens, processed-message IDs, and match metadata are stored in PostgreSQL.
See `../../docs/tapy-runbook.md` for the API and deployment
contract.
