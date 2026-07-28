# Physical host configuration with Ansible

Ansible owns configuration of physical Proxmox hosts after they have been
installed and joined to `HomeLab-Cluster`. It does not install Proxmox, create
or change corosync membership, manage VMs, or reboot a host during the normal
configuration play. On `smallgpu` and `largegpu`, it also configures each
directly attached UPS with Network UPS Tools (NUT).

The former `gpunvdgtx1060` node is no longer a Proxmox host. The separate
`configure-ubuntu-workstation.yml` play configures its replacement Ubuntu
installation locally: libvirt tooling, the existing critical NFS filesystem
and export, and boot-time VFIO ownership of the GTX 1060. Terraform, not
Ansible or virt-manager, owns the final `gpu-1` domain definition.

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

This play is safe to run before the network bridge exists. It mounts
`UUID=07445d19-37d4-4353-af1a-9511fb9c74e9` at
`/mnt/storage2-bulk`, restores its LAN NFS export, and stages VFIO for the next
reboot. It does not activate a bridge or reboot the workstation.

## Ubuntu workstation LAN bridge

The Talos VM must be a first-class LAN peer. Libvirt's `default` network
(`192.168.122.0/24`) is NAT-only and cannot satisfy the cluster's static
`192.168.1.231` endpoint. From a local graphical session, create and activate
the NetworkManager bridge below. This temporarily interrupts Ethernet, changes
the workstation from its current DHCP address to its reserved
`192.168.1.105`, and should not be run over SSH:

```bash
sudo nmcli connection add \
  type bridge ifname br0 con-name homelab-br0 \
  bridge.stp no \
  ipv4.method manual ipv4.addresses 192.168.1.105/24 \
  ipv4.gateway 192.168.1.1 ipv4.dns "1.1.1.1,8.8.8.8" \
  ipv6.method auto

sudo nmcli connection add \
  type ethernet ifname enp7s0f1 con-name homelab-br0-port \
  master br0 slave-type bridge

sudo nmcli connection modify netplan-enp7s0f1 connection.autoconnect no
sudo nmcli connection up homelab-br0
```

After reconnecting, verify `ip -br address show br0` reports
`192.168.1.105/24`. If activation fails, restore the previous connection
locally with `sudo nmcli connection up netplan-enp7s0f1`.

Reboot after the Ubuntu play has staged VFIO. Before applying Terraform, verify:

```bash
ip -br address show br0
findmnt /mnt/storage2-bulk
systemctl is-active nfs-kernel-server libvirtd
sudo exportfs -v
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
```

Both NVIDIA functions must report `Kernel driver in use: vfio-pci`; the Intel
UHD 630 at `00:02.0` must remain on `i915`.

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
ansible-playbook playbooks/configure-proxmox.yml --limit smallgpu --tags nut
ansible-playbook playbooks/configure-proxmox.yml --limit smallgpu --tags vfio
ansible-playbook playbooks/verify-proxmox.yml --limit nfs_servers --tags nfs
ansible-playbook playbooks/verify-proxmox.yml --limit local_storage_hosts --tags storage
ansible-playbook playbooks/verify-proxmox.yml --limit backup_hosts --tags backup
ansible-playbook playbooks/verify-proxmox.yml --limit nut_servers --tags nut
```

Available configuration tags are `repositories`, `packages`, `nfs`, `storage`,
`backup`, `nut`, and `vfio`. Common preflight checks (hostname, Proxmox major
version, and quorate cluster membership) always run. Filesystem checks run only
with NFS or directory-storage work, while PCI/IOMMU and running-VM conflict
checks run only with VFIO work. A repository-, package-, storage-, backup-,
NFS-, or NUT-only run is therefore not blocked by a GPU legitimately assigned
to a running VM.

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

The `proxmox_backup` role owns node-scoped vzdump jobs. The durable Windows
template VM `101` is backed up monthly to `largegpu-hdd` with one retained copy.
The retired workstation VM `100` backup job was removed. Linked Windows VM
`502` and all Terraform-managed Talos VMs are intentionally excluded because
they can be recreated declaratively. VM `402`'s HDD scratch disk also has
`backup = false`.

These backups primarily provide rollback from guest misconfiguration or
accidental deletion. They are not full disaster recovery: `largegpu-hdd` and
`storage1-bulk` are both on borrowed physical hosts.

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
