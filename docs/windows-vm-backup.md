# Windows gaming VM backup and recovery

Windows workstation VM `502` is a standalone guest on `largegpu`'s NVMe
`local-lvm`. It has no template parent. Nightly native Proxmox backups are the
disaster-recovery source and are stored on `backup-on-smallgpu`, the dedicated
NFS export backed by `smallgpu`'s HDD.

The dedicated `largegpu-windows-502` job runs at 04:15 in snapshot mode, uses
Zstandard compression, and retains the two newest archives. The general
`largegpu-cross-node` job runs at 07:00 and excludes `502`, avoiding duplicate
600 GiB-class archives.

## Shared-GPU backup hook

VMs `402` and `502` share the RTX 3080. Proxmox briefly starts QEMU to back up
a stopped VM with TPM state, so a stopped `502` cannot be archived while `402`
owns the passthrough device. The job's `vzdump-gpu-mutex-hook` handles this
case:

1. If `502` is running, require `402` to be stopped and let snapshot backup run.
2. If `502` is stopped and `402` is running, gracefully stop `402`.
3. Run the native backup without changing `502`'s hardware configuration.
4. Restart `402` after success or failure when the hook stopped it.

The hook never force-stops a guest. A failed graceful shutdown fails the backup
instead. Because backing up stopped `502` temporarily removes the GPU worker,
`cp-1` remains on `largegpu-hdd` and keeps the Kubernetes API available.

## Installation and verification

From `ansible/`:

```bash
ansible-playbook playbooks/configure-proxmox.yml \
  --limit largegpu --tags backup --check --diff
ansible-playbook playbooks/configure-proxmox.yml \
  --limit largegpu --tags backup --diff
ansible-playbook playbooks/verify-proxmox.yml \
  --limit largegpu --tags backup
```

Verify the installed hook and schedule without starting a backup:

```bash
ssh root@192.168.1.107 'bash -n /usr/local/sbin/vzdump-gpu-mutex-hook'
ssh root@192.168.1.107 \
  'pvesh get /cluster/backup/largegpu-windows-502 --output-format json-pretty'
```

After the first scheduled or supervised run, require a real VM 502 archive:

```bash
ssh root@192.168.1.107 'pvesm list backup-on-smallgpu --vmid 502'
```

## Disaster recovery

Keep VM `402` stopped during recovery because restored VM `502` contains the
shared GPU mapping. Select the newest archive and restore it directly to the
stable VMID on NVMe:

```bash
pvesm list backup-on-smallgpu --vmid 502
qmrestore \
  'backup-on-smallgpu:backup/vzdump-qemu-502-YYYY_MM_DD-HH_MM_SS.vma.zst' \
  502 --storage local-lvm
qm config 502
qm start 502
```

Confirm that `scsi0`, EFI vars, and TPM state are on `local-lvm`, verify the
expected PCI/USB mappings, and boot Windows before pruning older recovery
artifacts. Then run a `windows-workstation` Terraform plan to reconcile only
hardware drift. Do not apply Terraform to create a blank replacement before
restoring the native archive.
