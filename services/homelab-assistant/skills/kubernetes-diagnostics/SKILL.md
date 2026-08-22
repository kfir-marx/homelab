---
name: kubernetes-diagnostics
description: Diagnose live Kubernetes workload, scheduling, networking, storage, and controller issues with bounded read-only API and pod-log tools.
---

# Kubernetes diagnostics

Use the Kubernetes tools when the user asks about live cluster state. Start with
the narrowest relevant resource list, then inspect the affected object, Events,
owner/controller, endpoints, PVCs, nodes, and bounded pod logs as evidence
requires. Prefer label or field selectors over broad cluster dumps.

State which facts were observed and which conclusions are inferred. Correlate
conditions, restart counts, recent Events, rollout state, scheduling constraints,
and current or previous logs before naming a root cause. If evidence is missing,
say what remains unknown and suggest the safest declarative fix in the layer that
owns the resource.

All tools are read-only. Never claim to have changed, restarted, deleted, scaled,
or applied anything. Do not request or reproduce Secret values; they are redacted
by the gateway.
