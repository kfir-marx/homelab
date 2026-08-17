# SOPS + age secrets and disaster recovery

## Recovery objective

The repository plus one strong master passphrase is sufficient to recover the
same controller credentials and Kubernetes Secret values after every physical
host and VM is lost. The design does not require a surviving node, Kubernetes
Secret, workstation key file, or VM backup.

SOPS encrypts each value to an age public recipient. The corresponding age
identity is itself encrypted with age's passphrase mode and committed at
`secrets/age/homelab.agekey.age`. Remember the master passphrase and store a
copy in Google Password Manager. Use a unique, high-entropy passphrase (for
example, at least six randomly selected Diceware words); because both the
encrypted identity and the public repository are available to an attacker, a
weak passphrase can be tested offline.

The private identity is never installed in Kubernetes or CI. It exists as a
mode-0600 temporary file only while `scripts/secrets.sh` is running.

## What is and is not covered

| Recovery material | Source of truth | Recovery behavior |
|---|---|---|
| Proxmox, AWS, NUT, and external-service credentials from `.env` | `secrets/infrastructure.sops.env` | Restored to a gitignored `.env`, or injected for one command |
| Manually created Kubernetes Secrets | `secrets/kubernetes/*.sops.json` | Reapplied with their original names, namespaces, types, and data |
| Talos and Kubernetes PKI | encrypted remote Terraform state | Terraform reuses `talos_machine_secrets`; do not duplicate it into SOPS |
| cert-manager TLS Secrets | cert-manager and the ACME account | Regenerated after the DNS API token is restored |
| ServiceAccount tokens and other controller-generated Secrets | Kubernetes controllers | Regenerated; never capture them |
| Application databases, Bitwarden vault contents, media configuration, AdGuard configuration, and downloaded media | application/storage backups | Outside SOPS; follow the application runbooks |
| Cloudflare routes, Tailscale ACLs/approvals, DNS registrar settings, Google account configuration, and GitHub repository settings | provider control planes | Not representable as secrets; export or document separately |

The exact Kubernetes inventory and required keys live in
`secrets/inventory.tsv`. The Tailscale state Secret is deliberately included:
it preserves the router's long-lived node identity even if the reusable auth
key later expires. Refresh its encrypted snapshot after enrollment or any
identity change.

## Install tools

The controller needs `sops`, `age`/`age-keygen`, `jq`, and `kubectl`. Install
SOPS from its [official releases](https://github.com/getsops/sops/releases),
age from its [official project](https://github.com/FiloSottile/age), and the
other tools from their distribution packages, then run:

```bash
scripts/secrets.sh doctor
```

`sops` and `age` are needed for every decrypting operation. `jq` and `kubectl`
are needed for Kubernetes capture, validation, and restore.

## One-time initialization and migration

Do this once on a trusted controller with the current `.env` and live cluster
access. Do not paste the master passphrase into a shell command, chat, issue,
or repository file; `age` prompts without echoing it.

```bash
scripts/secrets.sh init
scripts/secrets.sh capture-env
scripts/secrets.sh capture-k8s
scripts/secrets.sh check
git status --short
```

`capture-k8s` is read-only against the cluster. It removes server-owned
metadata before encrypting each Secret, and never writes plaintext Secret data
to standard output or the repository. It returns nonzero and reports names if
an inventoried Secret is missing. Create a genuinely missing Secret using its
application runbook, then capture again; do not invent a replacement for a
lost database or backup password.

Review that the only new recovery artifacts are `.sops.yaml`, the armored
`.age` identity, `infrastructure.sops.env`, and `*.sops.json`. Commit and push
all of those ciphertext files. Merely generating them locally does not protect
against loss of the controller.

The public age recipient in `.sops.yaml` is not secret. Never commit the
decrypted age identity, `.env`, `kubeconfig.yaml`, Terraform state, or decrypted
Secret JSON.

## Normal use

Run Terraform or Ansible without creating a persistent `.env`:

```bash
scripts/secrets.sh run -- terraform/run-terragrunt.sh prod homelab-cluster plan
scripts/secrets.sh run -- bash -c \
  'cd ansible && ansible-playbook playbooks/configure-proxmox.yml --syntax-check'
```

To restore a conventional local file for tools that require one:

```bash
scripts/secrets.sh restore-env
```

The command refuses to overwrite an existing `.env`; use `--yes` only after
confirming that the encrypted copy is authoritative.

Edit encrypted values with `scripts/secrets.sh edit-env` or
`scripts/secrets.sh edit-k8s namespace/name`. After rotating a credential in a
provider or Kubernetes, capture the affected source again and commit the
changed ciphertext:

```bash
scripts/secrets.sh capture-env
scripts/secrets.sh capture-k8s cert-manager/cloudflare-dns-api-token
scripts/secrets.sh check
```

SOPS uses randomized encryption, so a recapture can change ciphertext even
when a value did not change. Commit the recovery bundle together with the
change that depends on it, but do not place secret values in the commit message.

## Full bare-metal recovery order

1. On a trusted replacement controller, recover the Git repository and install
   the required tools.
2. Run `scripts/secrets.sh check`. Enter the remembered master passphrase. This
   proves that the identity, environment, every inventoried Secret, and SOPS
   integrity MACs are usable before touching infrastructure.
3. Run `scripts/secrets.sh restore-env`, or use `scripts/secrets.sh run -- ...`,
   to make the Proxmox and AWS credentials available.
4. Restore physical hosts with Ansible, following the focused Proxmox and
   Ubuntu workstation recovery runbooks. Restore storage/application backups
   separately where they exist.
5. Run Terragrunt for the homelab cluster. The remote state must still be
   accessible so Terraform reuses the original Talos PKI. If that state is
   gone, Terraform can build a new cluster, but it cannot recreate the old
   Talos identity.
6. Recreate the gitignored access files from Terraform's sensitive outputs as
   described in the architecture runbook (use `umask 077`), then wait for
   Kubernetes, Cilium, and initial Argo CD bootstrap. Argo may report
   applications unhealthy because their out-of-band Secrets are not present.
7. Inspect the target before the only mutating secrets step:

   ```bash
   kubectl config current-context
   scripts/secrets.sh restore-k8s
   ```

   The restore performs a full local decrypt/shape/key preflight first, prints
   the kubectl context, and requires typing `restore`. It creates missing
   namespaces and server-side applies only the inventoried Secrets. `--yes` is
   available for an already-reviewed automated recovery.
8. Let Argo CD reconcile applications again. cert-manager will regenerate TLS
   Secrets. Confirm the Tailscale router reuses its recovered state, then check
   Cloudflare, Grafana, Immich, Bitwarden, and encrypted media-backup access.
9. Run the application-specific restore procedures for state that SOPS does
   not own.

## Passphrase and key maintenance

To change only the human master passphrase:

```bash
scripts/secrets.sh change-passphrase
scripts/secrets.sh check
```

This re-encrypts the same age identity, so the SOPS files do not change. Commit
the updated `homelab.agekey.age` immediately and update Google Password Manager.

Do a recovery drill after initial migration and at least after important secret
rotations: clone into a temporary directory, run `check`, decrypt the environment
to a temporary destination, and verify `restore-k8s` reaches its context/confirm
prompt against a non-production or deliberately unavailable context. Do not
apply production Secrets to an unrelated cluster.

Keep a second encrypted copy of the repository or at least the entire
`secrets/` directory plus `.sops.yaml` outside the homelab. The passphrase alone
cannot recover a lost encrypted age identity or lost ciphertext. Protect the
Google account with phishing-resistant MFA/passkeys and keep its recovery codes
outside the homelab as well.
