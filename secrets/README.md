# Encrypted recovery material

This directory is the recovery source for credentials that are otherwise held
only on a controller or in Kubernetes. The intended tracked files are:

- `age/homelab.agekey.age`: the age identity encrypted with the human-held
  master passphrase;
- `infrastructure.sops.env`: the SOPS-encrypted replacement for local `.env`;
- `kubernetes/*.sops.json`: sanitized, SOPS-encrypted snapshots of the
  Kubernetes Secrets listed in `inventory.tsv`.

No plaintext secret belongs here. Use `scripts/secrets.sh`; do not decrypt a
file into this directory. The complete initialization, rotation, restore, and
disaster-recovery procedure is in
[`docs/secrets-disaster-recovery.md`](../docs/secrets-disaster-recovery.md).
