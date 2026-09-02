# Shared services Telegram gateway

This stateless, typed long-polling gateway exposes only explicitly registered
friend-shareable services. Job Assistant is the first adapter. Operational and
security procedures live in `docs/shared-services-telegram-runbook.md`.

The Job Assistant adapter supports a fixed command/callback registry, bounded
PDF/DOCX uploads, and an authenticated typed document-notification pull. It has
no filesystem mount or generic file-proxy route; uncertain Telegram outcomes
are never replayed automatically.
