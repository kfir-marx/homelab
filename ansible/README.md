# Physical host configuration with Ansible

Ansible owns configuration of physical Proxmox hosts after they have been
installed and joined to `HomeLab-Cluster`. It does not install Proxmox, create
or change corosync membership, own VM declarations, or reboot a host during the
normal configuration play. Backup automation never starts, stops, shuts down,
or reboots a VM. On `smallgpu` and `largegpu`, it also configures each directly
attached UPS with Network UPS Tools (NUT).

The former `gpunvdgtx1060` node is no longer a Proxmox host. The separate
`configure-ubuntu-workstation.yml` play configures its replacement Ubuntu
installation locally as the permanent NFS and Telegram-Codex host. It mounts
and exports the existing critical filesystem, removes the retired libvirt packages and VFIO
boot configuration, and returns the GTX 1060 and HDMI output to Ubuntu after
an explicit reboot.

## Prerequisites and credentials

Run from this directory with Ansible Core 2.16 or newer. SSH keys are preferred;
password-based use of Ansible's default SSH connection also requires `sshpass`
on the controller. For the existing repository password, export it without
copying it into an inventory file:

```bash
set -a
. ../.env
set +a
ansible-playbook playbooks/configure-proxmox.yml --check --diff
```

`inventory/production/group_vars/all.yml` reads `PROXMOX_SSH_PASSWORD` from the
controller environment. Alternatively, leave it unset and use SSH keys or
`--ask-pass`. Ansible Vault can be used for future per-host secrets. Never add a
literal password, API token, or vault password to the inventory.

Automatic host shutdown also needs a local-only NUT monitor account and
`NUT_MONITOR_PASSWORD`. Generate a URL-safe value (for example,
`openssl rand -hex 24`) and add it to the untracked `.env` file. Although the
NUT server only listens on `127.0.0.1`, its shutdown monitor requires this
credential. Secret-bearing templates suppress diff and task output. A host
with `nut_automatic_shutdown_enabled: false` runs the driver and local data
server without the shutdown monitor or its credential.

For the local Ubuntu workstation, install Ansible first and use the current
login user's sudo credential; the old Proxmox root password is not used:

```bash
sudo apt update
sudo apt install -y ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml --ask-become-pass
```

Ubuntu 26.04 selects `sudo-rs` by default. Its PAM prompt is not currently
compatible with Ansible's password-prompt detection, so the workstation
inventory explicitly uses the already-installed, compatible
`/usr/bin/sudo.ws` executable for Ansible become operations. This does not
change the system-wide `sudo` alternative.

The `homelab_assistant` role runs Codex App Server as the normal workstation
user and exposes it only through a group-protected Unix socket. A read-only
Podman bridge under UID 10001 receives the socket, its small critical-storage
state directory, and only the deterministic switch credentials; it never
receives the workstation home or Codex authentication. Credentials come from
controller environment variables and named local files. The
`homelab_vm_actuator` role on `largegpu` installs a management-IP-restricted,
forced-command SSH identity that can run only status and the two fixed 402/502
transitions. See the focused runbook before either first rollout.

This play is safe to run while the workstation still uses DHCP. It mounts
`UUID=07445d19-37d4-4353-af1a-9511fb9c74e9` at
`/mnt/storage2-bulk`, restores its LAN NFS export, removes the retired
virtualization packages and VFIO boot files, and rebuilds initramfs/GRUB when
required. It does not change the active network connection or reboot the
workstation.

## Ubuntu workstation recovery and network

Reboot after the Ubuntu play removes the VFIO boot configuration. The current
kernel cannot return a boot-bound display GPU to the NVIDIA driver safely in
place. After reboot, verify `01:00.0` uses `nvidia`, `01:00.1` uses
`snd_hda_intel`, and HDMI appears in Ubuntu Displays.

The Kubernetes PVs still require the workstation's reserved
`192.168.1.105` address. No bridge is needed now that the Talos VM is retired.
The dedicated static profile is `homelab-enp7s0f1`; the original
`netplan-enp7s0f1` DHCP profile is retained with autoconnect disabled as a
recovery path. The one-time configuration was:

```bash
sudo nmcli connection add \
  type ethernet ifname enp7s0f1 con-name homelab-enp7s0f1 \
  connection.autoconnect yes \
  ipv4.method manual \
  ipv4.addresses 192.168.1.105/24 \
  ipv4.gateway 192.168.1.1 \
  ipv4.dns "1.1.1.1,8.8.8.8" \
  ipv6.method auto
sudo nmcli connection modify netplan-enp7s0f1 connection.autoconnect no
sudo nmcli connection up homelab-enp7s0f1
```

Activate profiles only from the local desktop because Ethernet is interrupted.
After reconnecting, verify:

```bash
ip -br address show enp7s0f1
findmnt /mnt/storage2-bulk
systemctl is-active nfs-kernel-server
sudo exportfs -v
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
nvidia-smi
```

The workstation runbook contains the complete recovery and NFS verification
procedure.

## First execution

Start with read-only verification and check mode. Verification is expected to
fail on drift (for example, a GPU awaiting its first VFIO reboot). Configuration
is serialized with `serial: 1`, so only one cluster node is touched at a time.

```bash
ansible-playbook playbooks/verify-proxmox.yml
ansible-playbook playbooks/configure-proxmox.yml --check --diff
ansible-playbook playbooks/configure-proxmox.yml --diff
```

Useful limited runs:

```bash
ansible-playbook playbooks/configure-proxmox.yml --check --diff --tags repositories
ansible-playbook playbooks/configure-proxmox.yml --limit smallgpu --tags nfs
ansible-playbook playbooks/configure-proxmox.yml --limit largegpu --tags storage
ansible-playbook playbooks/configure-proxmox.yml --limit largegpu --tags backup
ansible-playbook playbooks/configure-proxmox.yml --limit largegpu --tags homelab-vm-actuator
ansible-playbook playbooks/configure-ubuntu-workstation.yml --tags homelab-assistant
ansible-playbook playbooks/configure-proxmox.yml --limit smallgpu --tags nut
ansible-playbook playbooks/configure-proxmox.yml --limit smallgpu --tags vfio
ansible-playbook playbooks/verify-proxmox.yml --limit nfs_servers --tags nfs
ansible-playbook playbooks/verify-proxmox.yml --limit local_storage_hosts --tags storage
ansible-playbook playbooks/verify-proxmox.yml --limit backup_hosts --tags backup
ansible-playbook playbooks/verify-proxmox.yml --limit nut_servers --tags nut
```

Available configuration tags are `repositories`, `packages`, `nfs`, `storage`,
`backup`, `homelab-vm-actuator`, `homelab-assistant`,
`nut`, and `vfio`. Common preflight checks
(hostname, Proxmox major version, and quorate cluster membership) always run.
Filesystem checks run only with NFS or directory-storage work, while PCI/IOMMU
and running-VM conflict checks run only with VFIO work. A repository-, package-,
storage-, backup-, NFS-, or NUT-only run is therefore not
blocked by a GPU legitimately assigned to a running VM.

## Local directory storage and backups

The `proxmox_storage` role owns existing node-local filesystems declared on
`local_storage_hosts`. It validates the live filesystem UUID and type, writes
the UUID-based fstab entry, mounts it read/write, and reconciles the node-scoped
Proxmox directory storage. It never partitions or formats a disk. ISO
relocations are remote copies followed by checksum verification; a source is
removed only after the destination SHA-256 matches.

The role also reconciles Proxmox's cluster-level registration of existing NFS
exports without owning the server-side export. In particular, it points
`storage1-bulk` at smallgpu's live `/mnt/data10tb` export; the previous
`/mnt/storage1-bulk` registration survived only as a stale client mount and
would fail after remount or reboot.

On `largegpu`, Windows and VirtIO ISOs move to `largegpu-hdd`. Terraform-managed
Talos images remain in each node's `local` storage because the image download
resource is per Proxmox node. The same HDD accepts VM images so Terraform can
attach a capped, disposable scratch disk to VM `402`.

The `proxmox_backup` role owns node-scoped vzdump jobs. Standalone gaming VM
`502` has a dedicated 04:15 job on `backup-on-smallgpu`, with two recent copies.
The job uses snapshot mode without a hook: a running VM is backed up online,
while a stopped VM remains stopped when Proxmox uses its temporary backup-only
QEMU process. That process does not claim the VM's passed-through GPU. The role
rejects backup-job scripts and removes the retired GPU mutex hook so no managed
backup automation can change VM power state. The general `largegpu-cross-node`
job runs at 07:00 and excludes `502` to avoid a duplicate large archive.
`smallgpu-cross-node` runs at 02:15; general jobs keep three recent and two
weekly copies. Disks declared with Proxmox `backup=0`, including disposable GPU
scratch, remain excluded by `vzdump`.

The opposite-node exports are dedicated root-owned `0700` directories. Native
restoration is handled separately by `playbooks/restore-proxmox-node.yml`; it
selects the latest archive per declared VM, refuses existing VM IDs or volumes,
and requires `proxmox_restore_confirm=true`. See
[`docs/proxmox-node-disaster-recovery.md`](../docs/proxmox-node-disaster-recovery.md).

VM 502 recovery restores its native archive directly to `local-lvm`; template
VM 101 and the former weekly template-refresh timer are not part of the active
design. See [`docs/windows-vm-backup.md`](../docs/windows-vm-backup.md).

## UPS shutdown and recovery

`smallgpu` initially uses a telemetry-only standalone NUT instance because its
USB data connection is unstable; `nut-monitor.service` remains disabled, so
NUT cannot shut down the host. After the connection is stable, add
`NUT_MONITOR_PASSWORD`, set `nut_automatic_shutdown_enabled: true`, converge
again, and perform the staged outage tests below. `largegpu` remains outside the
`nut_servers` group until its UPS arrives; add it only after the USB data cable
is connected and its identity is declared. Each UPS data cable must be
connected directly to a host-controlled USB port on the computer it protects.
Identify the UPS by unplugging/reconnecting its data cable and comparing
`lsusb`, then set its four-digit `nut_ups_usb_vendorid` and
`nut_ups_usb_productid` in the matching host-vars file. Confirm the model is supported by the NUT
`usbhid-ups` driver, or override `nut_ups_driver` and
`nut_ups_driver_options` according to the NUT hardware compatibility list. The
role makes no changes unless exactly one attached USB device matches the
declared IDs; use `nut_ups_usb_serial` as an additional selector if identical
devices are connected to one host.

The outage flow is:

1. NUT reports `ONBATT` and starts a 30-second timer.
2. An `ONLINE` event during those 30 seconds cancels the timer.
3. If the timer expires, `upsmon` enters forced-shutdown mode. A low-battery
   condition can trigger this immediately rather than waiting for the timer.
4. Systemd powers off Proxmox. The enabled `pve-guests.service` runs first and
   asks all running QEMU VMs and containers on that host to shut down cleanly.
5. Late in shutdown, NUT requests that a capable UPS turn off its load. When
   utility power returns and the UPS restores output, the BIOS restore-on-AC
   setting boots the host.

Talos VMs already have Terraform `on_boot = true`, so Proxmox starts them after
the host boots. Windows VMs deliberately remain `on_boot = false` because the
Windows and Talos GPU VMs on `largegpu` share the same passed-through GPU and
cannot start together. This setup does not remember which side of that runtime
mutex was active before the outage.

First deployment must be staged one host at a time:

```bash
ansible-playbook playbooks/configure-proxmox.yml \
  --check --diff --limit smallgpu --tags nut
ansible-playbook playbooks/configure-proxmox.yml \
  --diff --limit smallgpu --tags nut
ansible-playbook playbooks/verify-proxmox.yml \
  --limit smallgpu --tags nut
```

Repeat for `largegpu` only after its UPS arrives and the host is added to
`nut_servers`. Verify `upsc ups@localhost ups.status` returns `OL`, and
inspect `upscmd -l ups@localhost` without invoking a command. Automatic BIOS
recovery requires the UPS/driver to support turning its outlets off during the
final shutdown and restoring them when mains returns. If it cannot power-cycle
the load, the PSU never loses input and a restore-on-AC BIOS setting alone will
not reboot the already powered-off host; use a supported UPS shutdown command
or a separate wake-on-LAN controller in that case.

Test cancellation first by removing utility input for less than 30 seconds and
confirming the timer is cancelled in the journal. Schedule a maintenance window
for the full unplugged test: it intentionally stops guests and powers off the
host. After restoring utility power, verify host boot, UPS status, and expected
guest autostart before deploying the second node.

## Host variables

Each host file under `inventory/production/host_vars/` declares:

- expected hostname and any special reboot safety classification;
- NFS backing mounts by filesystem UUID, type, mount point, and mount options;
- NFS client networks and export options;
- UPS USB identity, driver overrides, and any additional driver options;
- passthrough PCI addresses/IDs, the expected IOMMU group, and every member of
  that group;
- the host-specific IOMMU kernel parameters.

To add an already-joined Proxmox node, add it to `proxmox_hosts` and the applicable
`nfs_servers`/`nut_servers`/`vfio_hosts` inventory groups, then add a matching
host-vars file.
Record hardware facts from the live node; do not copy PCI IDs, UUIDs, or IOMMU
groups from another machine.

The NFS role never formats disks, clears dirty flags, or uses a force mount. An
unmounted NTFS filesystem must pass `ntfsfix --no-action` and a normal temporary
read/write mount before Ansible writes fstab or exports. A dirty or hibernated
volume must be repaired in Windows with `chkdsk /f`, Fast Startup disabled, and
a full shutdown. The `ntfs-3g` package on `smallgpu` supplies diagnostics only;
the persistent mount is `ntfs3`.

## Reboots and verification

VFIO files and initramfs are prepared idempotently, but devices are never
detached from the running host and the configuration play never reboots. After
shutting down affected workloads, reboot only explicitly:

```bash
ansible-playbook playbooks/reboot-proxmox.yml \
  --limit smallgpu \
  -e proxmox_reboot_approved=true
```

The Ubuntu workstation is not targeted by `reboot-proxmox.yml`; reboot it
locally only after closing active work and confirming the Intel GPU drives the
display.

After a reboot, run `playbooks/verify-proxmox.yml`. Also verify both NFS exports
from a Talos worker and confirm the NVIDIA device plugin reports the GPUs inside
Kubernetes; those guest/cluster checks remain outside physical-host Ansible.
