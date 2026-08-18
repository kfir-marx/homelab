# GitHub Actions runners

GitHub Actions Runner Controller (ARC) provides ephemeral, repository-scoped
runners for `kfir-marx/homelab`. GitHub does not charge Actions minutes for
self-hosted runners, although artifact, cache, electricity, and hardware costs
still apply.

The two Argo CD Applications pin ARC chart version `0.14.2`. The `homelab`
runner scale set has `minRunners: 0` and `maxRunners: 1`, so idle jobs consume
only the controller and listener resources. Each job gets a new runner pod and
its work directory disappears with that pod.

## Security boundary

The existing workflows use PostgreSQL service containers and Docker Buildx.
ARC therefore runs them with a Docker-in-Docker sidecar. That sidecar is
privileged, so code executed by a workflow must be treated as capable of
compromising a Kubernetes worker and anything reachable from it.

- Keep the scale set repository-scoped. Do not reuse it for public or
  less-trusted repositories.
- Do not approve fork pull-request workflows unless every changed executable,
  action reference, and build input has been reviewed.
- Keep `maxRunners` at one unless both cluster capacity and the security impact
  of additional concurrent privileged pods have been reviewed.
- Runner pods use the chart's no-permission service account. Do not grant it
  Kubernetes API permissions or mount application Secrets.
- The controller and runner namespaces intentionally have unrestricted egress.
  ARC needs the Kubernetes API, GitHub, GHCR, action download endpoints, and
  arbitrary workflow-selected registries. Treat this as a platform exception
  and do not add an incomplete default-deny policy.

## One-time GitHub authentication

This repository is owned by a personal account, so use a repository-scoped
fine-grained personal access token rather than an organization-owned GitHub
App. Create a token that:

- can access only `kfir-marx/homelab`;
- has repository `Administration: Read and write` permission;
- has the shortest practical expiration.

Create the namespace and Secret before merging the runner Applications. These
commands are create-only and never place the token in shell history or Git.
Store the token as `GITHUB_RUNNER_PERSONAL_ACCESS_TOKEN` in the gitignored
`.env`, then capture that environment into the SOPS recovery bundle first:

```bash
scripts/secrets.sh capture-env

kubectl --kubeconfig kubeconfig.yaml create namespace github-actions-runners
kubectl --kubeconfig kubeconfig.yaml label namespace github-actions-runners \
  pod-security.kubernetes.io/enforce=privileged \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted

scripts/secrets.sh run -- bash -c '
  kubectl --kubeconfig kubeconfig.yaml -n github-actions-runners \
    create secret generic github-actions-runner-auth \
    --from-literal=github_token="$GITHUB_RUNNER_PERSONAL_ACCESS_TOKEN"
'
```

Capture the Secret in the encrypted recovery bundle, then verify that only
ciphertext is tracked:

```bash
scripts/secrets.sh capture-k8s \
  github-actions-runners/github-actions-runner-auth
scripts/secrets.sh check
```

## Rollout and workflow cutover

After the Secret exists, merge and let the root Application discover both ARC
Applications. The controller must become healthy before the runner scale set:

```bash
kubectl --kubeconfig kubeconfig.yaml -n arc-systems \
  rollout status deployment/arc-gha-rs-controller
kubectl --kubeconfig kubeconfig.yaml -n github-actions-runners get pods
kubectl --kubeconfig kubeconfig.yaml -n github-actions-runners \
  get autoscalingrunnersets.actions.github.com
```

In GitHub, create the repository Actions variable `RUNNER_LABEL` with value
`homelab`. The workflows use `ubuntu-latest` only while that variable is absent,
which allows this bootstrap change to pass before ARC exists. After setting the
variable, manually dispatch or rerun a workflow and watch the ephemeral pod:

```bash
kubectl --kubeconfig kubeconfig.yaml -n github-actions-runners get pods --watch
```

Remove the variable to fall back to GitHub-hosted capacity during a cluster
outage. Setting both `minRunners` and `maxRunners` to zero drains the scale set
without changing workflow routing; jobs remain queued until capacity returns.

## Queued jobs with no runner pod

If GitHub shows jobs queued for `homelab` but no runner pod appears, verify the
listener before investigating runner scheduling:

```bash
kubectl --kubeconfig kubeconfig.yaml -n arc-systems get pods
kubectl --kubeconfig kubeconfig.yaml -n arc-systems \
  logs deployment/arc-gha-rs-controller --since=10m
```

The `arc-systems` namespace enforces the restricted Pod Security profile, so
the listener template must retain its non-root, seccomp, dropped-capabilities,
and no-privilege-escalation settings. The controller must also exclude
`argocd.argoproj.io/instance` from label propagation. Without that exclusion,
Argo CD adopts and prunes ARC's dynamically generated listener and runner
resources, causing a delete/recreate loop.

The runner container also sets numeric UID `1001`, matching the `runner` user
in GitHub's official image. Retain that value while using the upstream image:
with only `runAsNonRoot: true`, kubelet cannot verify a named image user and
leaves the pod in `CreateContainerConfigError`.

The controller uses the `immediate` update strategy. With a one-runner maximum,
this avoids an `eventual` rollout waiting indefinitely for a broken Pending
runner to finish.

Keep `runnerMaxConcurrentReconciles` at `1` while this deployment has a single
runner scale set. A higher value allowed overlapping reconciliations to create
two `EphemeralRunnerSet` resources for the same generation; ARC then marked the
newly registered runner `Outdated` and deleted it before it could claim a job.

## Rotation and upgrades

Rotate the fine-grained token before it expires: create a replacement in
GitHub, replace the Kubernetes Secret, capture it with `scripts/secrets.sh`, and
then revoke the old token. Avoid printing either token.

ARC CRDs are not upgraded or removed by Helm. Before changing the pinned chart
version, read that release's upgrade notes and follow GitHub's ARC upgrade
sequence. Do not assume an ordinary in-place Argo CD chart bump is safe.
