# Windows template refresh

Windows workstation VM `502` is an LVM-thin linked clone of template VM `101`.
Changes written to 502's copy-on-write layer cannot be merged safely into the
read-only base image. The weekly maintenance job rebuilds both objects while
preserving their IDs:

1. Gracefully shut down VM 502 if it is running; never force-stop it.
2. Create and checksum an independent Zstandard `vzdump` archive on
   `largegpu-hdd`.
3. Destroy linked clone 502, then destroy its old template 101.
4. Restore the archive to `local-lvm` as VM 101 and convert it to a template.
5. Create a new linked clone with VMID 502 from template 101.
6. Restore 502's previous MAC address, SMBIOS UUID, and VM generation ID.
7. Restart 502 only if it was running before maintenance and GPU-sharing VM
   402 is still stopped.

The backup is the transaction's recovery artifact. The job retains the two
newest VM 502 archives on `largegpu-hdd`; it does not remove the separate
monthly template backup or the daily cross-node backups. Do not delete a
staging archive until the rebuilt 502 has booted successfully.

## Schedule and installation

Ansible installs `refresh-windows-template.service` and its systemd timer on
`largegpu`. The declared schedule is Sunday at 01:00 in the Proxmox host's
local timezone, ahead of the 04:15 cross-node backup. Catch-up is deliberately
disabled: if the host is off during the window, the destructive job waits for
the next Sunday instead of starting unexpectedly after boot.

Deploy only the timer and verify its next run from `ansible/`:

```bash
ansible-playbook playbooks/configure-proxmox.yml \
  --limit largegpu --tags windows-template-refresh --check --diff
ansible-playbook playbooks/configure-proxmox.yml \
  --limit largegpu --tags windows-template-refresh --diff
ansible-playbook playbooks/verify-proxmox.yml \
  --limit largegpu --tags windows-template-refresh
```

The configuration play installs and enables the timer but does not trigger an
immediate refresh. Run the first rotation while watching it, after confirming
Windows is ready for a graceful shutdown and any BitLocker recovery key is
available:

```bash
ssh root@192.168.1.107 'systemctl start refresh-windows-template.service'
ssh root@192.168.1.107 \
  'journalctl -u refresh-windows-template.service -f'
```

Inspect the schedule and the result of the last run with:

```bash
ssh root@192.168.1.107 'systemctl list-timers refresh-windows-template.timer'
ssh root@192.168.1.107 \
  'systemctl status refresh-windows-template.service --no-pager'
```

Change `proxmox_windows_template_refresh_schedule` in
`ansible/inventory/production/host_vars/largegpu.yml` to move the window. Keep
enough separation from the daily 04:15 backup for a full read, compression,
checksum pass, restore, and linked-clone creation.

## Safety and failure recovery

The script uses a non-blocking lock and refuses to proceed if either VM is
missing at preflight, 101 is not a template, either VM is protected, Windows
cannot shut down cleanly, a disk is marked `backup=0`, or both GPU-sharing VMs
are active. It verifies the new archive before deleting anything. A failure
before 502 is destroyed tries to restart the unchanged VM when it was initially
running. A later failure leaves the verified archive in place and reports the
exact failed phase in the system journal.

If a run fails after deletion, do not run Terraform apply until the two IDs are
recovered. Find the archive recorded in the journal, then use the applicable
steps below on `largegpu`:

```bash
# If VM 101 was restored but is not yet a template:
qm template 101

# If VM 101 is absent, restore the reported archive first:
qmrestore 'largegpu-hdd:backup/vzdump-qemu-502-...vma.zst' 101 \
  --storage local-lvm --unique 1
qm template 101

# Once 101 is a template and 502 is absent:
qm clone 101 502 --full 0 --name largegpu-win11
```

Leave 502 stopped after manual recovery, confirm its hardware against
`terraform/deployments/prod/windows-workstation/config.yml`, then run a
`windows-workstation` Terraform plan. The normal Terraform resource identity
is `largegpu/502`, so the successful weekly rebuild keeps the state address and
VMID stable; the plan remains the final drift check for the VM configuration.
