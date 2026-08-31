# Windows gaming VM backup and recovery

Windows workstation VM `502` is a standalone guest on `largegpu`'s NVMe
`local-lvm`. It has no template parent. Its automatic backup path deliberately
does not write through an NFS mount on `largegpu`.

## Automatic staged backup

`windows-502-backup.timer` runs daily at 04:15. The oneshot service:

1. Acquires `/run/lock/windows-502-backup.lock` without waiting.
2. Reads the states of VMs `402` and `502`. If both are already running, it
   fails without attempting to correct the invalid shared-GPU state.
3. Requires at least 751,619,276,800 bytes (700 GiB) free in
   `/mnt/pve/largegpu-hdd/windows-502-staging`. This covers a worst-case
   635 GiB archive plus 65 GiB of headroom.
4. Runs snapshot-mode `vzdump` locally with Zstandard compression and idle I/O
   priority.
5. Computes the completed archive's size and SHA-256 and transfers it at no
   more than 65,536 KiB/s (64 MiB/s) over SSH/rsync to
   `/mnt/data10tb/proxmox-backups/from-largegpu/windows-502/` on `smallgpu`.
6. On `smallgpu`, compares the exact size and SHA-256, runs
   `zstd -dc ARCHIVE | vma verify -`, and confirms
   `zstd -dc ARCHIVE | vma config -` returns configuration metadata.
7. Writes a `.verified` marker, keeps the newest verified archive, and
   only then deletes the local staging copy.

If backup, transfer, or verification fails, the service fails and preserves
the completed local archive. Unverified remote files are not retention
candidates. Retention refuses to delete an archive without its verification
marker and never deletes the last verified archive. The older emergency
archives in `largegpu-hdd/dump/` and smallgpu's
`proxmox-backups/from-largegpu/dump/` are outside the managed staging and
destination directories and are never pruned by this workflow.

The SSH private key is generated only on `largegpu` at
`/root/.ssh/windows-502-backup-ed25519` and is not stored in Git. Ansible
manages its public authorization on a locked, dedicated smallgpu account. The
key is source-address restricted, disables forwarding and PTY allocation, and
can invoke only the restricted rsync receiver, verification, or status path.
smallgpu's host key is pinned rather than accepted on first use. Because NTFS3
does not support POSIX ACLs, the root-owned `proxmox-backups` and
`from-largegpu` parents are `0711` (traversable but not listable); the dedicated
child remains `0700`.

## Shared-GPU and power-state safety

VMs `402` and `502` share the RTX 3080, but backups do not participate in that
runtime mutex. Snapshot mode backs up a running `502` online. When `502` is
stopped, Proxmox may use a temporary backup-only QEMU process; this neither
changes the VM's configured power state nor claims its passed-through GPU.

Neither installed script contains or invokes a VM start, stop, shutdown,
reboot, reset, or resume operation. There is no vzdump hook. The dedicated
Proxmox `largegpu-windows-502` job is retired, and the general 07:00
`largegpu-cross-node` job remains automatic while excluding VM `502`.

## Installation and non-triggering verification

From `ansible/`:

```bash
ansible-playbook playbooks/configure-proxmox.yml \
  --limit largegpu --tags backup --check --diff
ansible-playbook playbooks/configure-proxmox.yml \
  --limit largegpu --tags backup --diff
ansible-playbook playbooks/verify-proxmox.yml \
  --limit largegpu --tags backup
```

These commands install and verify the timer without starting the backup
service. Inspect recent failures with:

```bash
systemctl status windows-502-backup.timer windows-502-backup.service
journalctl -u windows-502-backup.service
```

Do not manually start the service merely as a configuration test: doing so
starts a full VM backup.

## Disaster recovery

On `smallgpu`, choose an archive that has a matching `.verified` marker and
confirm the marker's recorded checksum before copying it back:

```bash
cd /mnt/data10tb/proxmox-backups/from-largegpu/windows-502
archive=vzdump-qemu-502-YYYY_MM_DD-HH_MM_SS.vma.zst
test -f "${archive}.verified"
sha256sum "${archive}"
zstd -dc "${archive}" | vma verify -
zstd -dc "${archive}" | vma config -
rsync --archive --partial --bwlimit=65536 \
  "${archive}" root@192.168.1.107:/mnt/pve/largegpu-hdd/windows-502-staging/
```

Compare the SHA-256 output with `sha256=` in the marker. On `largegpu`, keep
VM `402` stopped during the restore because restored VM `502` contains the
shared GPU mapping. Confirm VMID `502` does not already exist, then restore the
copied archive to NVMe:

```bash
qm status 402
qm status 502
qmrestore \
  /mnt/pve/largegpu-hdd/windows-502-staging/vzdump-qemu-502-YYYY_MM_DD-HH_MM_SS.vma.zst \
  502 --storage local-lvm
qm config 502
```

Verify that `scsi0`, EFI vars, and TPM state are on `local-lvm`, and inspect
the expected PCI/USB mappings before explicitly choosing whether to boot the
restored guest. Then run a `windows-workstation` Terraform plan to reconcile
only hardware drift. Do not apply Terraform to create a blank replacement
before restoring the native archive.

After the staged path was deployed, `cp-1` was Ready and no blocked kernel/NFS
tasks remained. The enabled `ksmtuned` service was started again and transparent
huge pages were restored to the conservative `madvise` mode. `ksmtuned` may
dynamically leave `/sys/kernel/mm/ksm/run` at `0` until its memory thresholds
call for page merging.
