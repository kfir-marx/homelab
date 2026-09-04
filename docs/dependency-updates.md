# Dependency update runbook

## Purpose and safety boundary

Renovate was selected because it understands the repository's existing
Terraform, GitHub Actions, Docker, Python, Kubernetes, and Argo CD dependency
formats and can keep `pyproject.toml` changes together with generated `uv.lock`
updates. It proposes reviewable pull requests; it does not prove that an
upgrade is compatible with this homelab.

The repository configuration disables automerge, limits concurrent work, waits
seven days after a release, and uses a weekly schedule. Digest-only pinning is
not age-delayed because it makes an already selected version immutable rather
than selecting a newer release. Renovate must never be treated as authorization
to apply Terraform, change VM state, run Ansible, sync Argo CD, or otherwise
deploy infrastructure.

## Enable hosted Renovate

Install the hosted Mend Renovate GitHub App manually and grant it access only
to `kfir-marx/homelab`:

1. Open the Mend Renovate app in the GitHub Marketplace and choose **Install**.
2. Select the `kfir-marx` account, choose **Only select repositories**, and
   select `homelab`.
3. Review the app permissions and finish installation.
4. If Renovate opens an onboarding/configuration pull request, inspect and
   merge it before expecting scheduled dependency pull requests.

This app installation is an external manual step and was not performed while
adding this configuration. The automation is not active until the app is
installed and onboarding is complete.

## Branch protection

Protect `main` in GitHub repository settings:

- Require changes to arrive through pull requests.
- Require the status checks relevant to the files changed, including the
  service lint/type/test, container-build, manifest, and Terraform checks.
- Require branches to be current with `main` before merging.
- Do not allow Renovate or other bots to bypass protection.
- Retain manual approval for all dependency pull requests.

Labels are advisory. If the `dependencies`, `manual-review`, or `high-risk`
labels do not already exist, create them in GitHub so Renovate can attach them.
Branch protection and human review are the enforcement boundary.

## Review policy

Always review these dependencies individually:

- Terraform providers and modules. Read the provider changelog and inspect a
  plan from a trusted environment.
- Talos, Kubernetes, and Cilium. Check the supported-version matrices and
  upgrade ordering; never combine them into a blind latest-version upgrade.
- Argo CD and Kubernetes or Helm application changes. Merging files reconciled
  by Argo CD can cause an automatic deployment.
- NVIDIA GPU Operator and related Talos/NVIDIA components.
- PostgreSQL, Valkey/Redis, and any other database, especially major versions
  or image-flavor changes.
- Storage and networking components, including Cilium, cert-manager,
  Tailscale, Cloudflare Tunnel, CSI drivers, and NFS provisioners.

For Terraform updates, run the appropriate read-only Terragrunt plan using the
documented workflow. Inspect every resource action, paying particular
attention to `-/+` replacement and `-` deletion. Do not apply merely because a
plan succeeds or because Renovate opened the pull request. Any unexpected
replacement, deletion, storage change, or VM power impact blocks the update.

Gateway API, Codex CLI, and kubeconform pins are deliberately outside
Renovate's custom regex coverage. Each is paired with a SHA-256 value in the
Terraform or workflow implementation, and a version-only update would fail
closed or, worse, invite an unsafe checksum workaround. Update each version and
checksum together after verifying the downloaded upstream asset.

## Python lockfiles

Each service is an independent uv project with its own lockfile:

```bash
cd services/homelab-assistant && uv lock
cd ../internal-llm && uv lock
cd ../job-assistant && uv lock
cd ../external-ai && uv lock
```

After intentionally changing dependencies, review the lockfile diff and run
`uv lock --check` in each project. CI uses `uv sync --locked --extra dev`, so it
fails if `pyproject.toml` and `uv.lock` disagree and installs locked development
tools before Ruff, mypy, and pytest. Docker uses
`uv sync --locked --no-dev --no-editable`; it fails on the same stale lock and
copies only the locked production environment into the runtime image.

Renovate uses the PEP 621 manager and uv lockfile support. A Python dependency
PR must include every resulting `pyproject.toml` and `uv.lock` change. Do not
merge a declaration-only update.

Python delivery remains deliberately two-stage:

1. Review and merge the dependency PR after its tests and container build pass.
2. The release workflow builds the tested immutable `sha-*` image and opens a
   separate deployment PR that pins that image in Kubernetes.

The internal `ghcr.io/kfir-marx/homelab-*` image references are ignored by
Renovate because those release workflows own them.

## Rollback and pausing updates

To roll back a merged dependency change, use `git revert` on the merge commit,
run the normal checks, and merge the revert. For a deployed Kubernetes or Helm
change, restore the previously pinned image tag or chart version in the revert
so Argo CD reconciles back to the known version. Avoid rewriting shared branch
history.

To pause one problematic dependency, add its Renovate package name to
`ignoreDeps` or add a narrow package rule with `"enabled": false`, then record
the reason in the rule description. Close the outstanding Renovate PR. Remove
the exclusion only after the compatibility or upstream issue is resolved. To
pause all proposals, suspend the Renovate app installation for this repository
or disable scheduling in `renovate.json5`; neither action rolls back changes
that were already merged or deployed.
