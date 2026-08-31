# Windows gaming VM backup and recovery

Windows workstation VM `502` is a standalone guest on `largegpu`'s NVMe
`local-lvm`. It has no template parent. Nightly native Proxmox backups are the
disaster-recovery source and are stored on `backup-on-smallgpu`, the dedicated
NFS export backed by `smallgpu`'s HDD.

The dedicated `largegpu-windows-502` job runs at 04:15 in snapshot mode, uses
Zstandard compression, and retains the two newest archives. The general
`largegpu-cross-node` job runs at 07:00 and excludes `502`, avoiding duplicate
600 GiB-class archives.

## Shared-GPU and power-state behavior

VMs `402` and `502` share the RTX 3080, but backups do not participate in that
runtime mutex. Snapshot mode backs up a running `502` online. When `502` is
stopped, Proxmox may use a temporary backup-only QEMU process; this neither
changes the VM's configured power state nor claims its passed-through GPU.
Therefore `402` can remain in its existing state throughout the backup.

The job has no hook script. The Ansible role rejects backup-job scripts and
removes the retired `vzdump-gpu-mutex-hook`, so no managed backup job, hook, or
helper starts, stops, shuts down, or reboots either VM.

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

Verify the schedule and absence of a hook without starting a backup:

```bash
ssh root@192.168.1.107 \
  'pvesh get /cluster/backup/largegpu-windows-502 --output-format json-pretty'
ssh root@192.168.1.107 \
  'test ! -e /usr/local/sbin/vzdump-gpu-mutex-hook'
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
