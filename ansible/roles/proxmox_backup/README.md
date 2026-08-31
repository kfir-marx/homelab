# Proxmox backup role

This role manages ordinary snapshot-mode Proxmox backup jobs and the special
staged workflow for Windows VM 502. Ordinary jobs must be hook-free. VM 502 is
instead backed up by a systemd timer that stages on `largegpu-hdd`, transfers
through restricted SSH/rsync, verifies remotely, and retains one verified
copies.

The workflow intentionally separates its staging and destination directories
from legacy/emergency archive locations. It never prunes unverified archives,
never deletes the last known-good archive, and leaves a completed staging copy
in place on any downstream failure. Its only `qm` operations read guest state;
power-state mutation commands are forbidden from every installed backup-path
script.

Authentication uses a private Ed25519 key generated on the source host. Only
the public key is installed declaratively on the destination, where a forced
command and OpenSSH `restrict` option constrain its use. No credential or
private key belongs in inventory or Git.
