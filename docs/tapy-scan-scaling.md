# Tapy scan scaling prerequisite

The provider-owned `kubernetes/apps/keda.yaml` pins KEDA 2.21.1. Its controllers and
CRDs stay under homelab Argo ownership. Tapy's own repository owns only the
namespaced ScaledObject, TriggerAuthentication and NetworkPolicy.

The controllers request 100m CPU and 224 MiB combined. Check scheduling headroom
on the existing nodes before syncing; do not change Proxmox, Talos, node resources,
priority classes or install a node provisioner. No controller installation has
been performed as part of preparing this change.

Publish and validate the matching Tapy worker release before advancing the pinned
Tapy Application revision. Its replica-ignore rule lets KEDA own `tapy-worker`
replicas. The common application counts durable queued/retrying/running scan jobs
in PostgreSQL, uses at most one scan worker and scales to zero after idle cooldown.
The additional maintenance pod stays running for outbox recovery, delivery and
webhook renewal. Existing Tapy container resources and NFS/PV/PVC bindings are
unchanged. Production-only Spot labels and tolerations never enter this overlay.

Sync KEDA before the new Tapy revision and prove wakeup, interrupted scan recovery
and idle scale-down on the existing nodes. Follow Tapy's `docs/trial-production.md`
for release gates, migrations and rollback. Do not modify the production Argo
Application or reuse homelab tunnel admission to implement the GCP ingress.
