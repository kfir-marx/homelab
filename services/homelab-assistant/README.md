# Homelab assistant gateway

This container is the deliberately narrow Telegram edge for the private LLM in
`kubernetes/system/homelab-assistant/`. It accepts only direct messages from
configured Telegram user IDs, keeps a small conversation history in memory,
and calls the authenticated in-cluster vLLM API. Its packaged skills let the
model diagnose live Kubernetes state with bounded read-only API and log tools,
and prepare an external-AI handoff when the current prompt explicitly requests
one.

The gateway has a dedicated service-account token and cluster-wide read-only
RBAC, but no shell tool, host mount, or infrastructure mutation capability.
Secret values are redacted before model context and handoff transmission still
requires explicit button confirmation. Add future operations as explicit typed
tools with their own authorization and audit boundary; never turn model output
into shell commands.
