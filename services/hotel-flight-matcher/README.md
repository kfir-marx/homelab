# Hotel Flight Matcher

A stateless API that validates a Chrome extension's Google OAuth token, asks a
private vLLM endpoint to extract a bounded hotel-booking schema from one email,
and deterministically scores the booking against configured flights.

The service does not access Gmail itself, fetch attachments, persist messages,
or let model output determine the final match score. See
`../../docs/hotel-flight-matcher-runbook.md` for deployment and privacy gates.
