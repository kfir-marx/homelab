# internal-llm

Cluster-internal, OpenAI-compatible gateway for the homelab GPU model. The API
authenticates callers and publishes generation requests to RabbitMQ; workers
consume the shared queue and are the only workloads allowed to call vLLM.

The gateway intentionally does not support streaming. Requests are buffered by
RabbitMQ and a caller waits for the corresponding RPC response. Request and
response bodies are never logged.

See `docs/local-llm-runbook.md` for deployment and client configuration.
