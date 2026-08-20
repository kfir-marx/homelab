# Homelab assistant gateway

This container is the deliberately narrow Telegram edge for the private LLM in
`kubernetes/system/homelab-assistant/`. It accepts only direct messages from
configured Telegram user IDs, keeps a small conversation history in memory,
and calls the authenticated in-cluster vLLM API.

It has no Kubernetes service-account token, shell tool, host mount, or
infrastructure mutation capability. Add future commands as explicit typed
operations with their own authorization and audit boundary; never turn model
output into shell commands.

