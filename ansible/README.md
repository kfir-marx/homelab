# Proxmox host configuration with Ansible

Ansible owns configuration of physical Proxmox hosts after they have been
installed and joined to `HomeLab-Cluster`. It does not install Proxmox, create
or change corosync membership, manage VMs, or reboot a host during the normal
configuration play.

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
ansible-playbook playbooks/configure-proxmox.yml --limit smallgpu --tags vfio
ansible-playbook playbooks/verify-proxmox.yml --limit nfs_servers --tags nfs,verify
```

Available configuration tags are `repositories`, `packages`, `nfs`, `vfio`,
and `verify`. The always-tagged preflight checks hostname, Proxmox major
version, quorate cluster membership, filesystem UUID/type, PCI IDs, exact IOMMU
group membership, and running-VM GPU conflicts before host configuration.

## Host variables

Each host file under `inventory/production/host_vars/` declares:

- expected hostname and any special reboot safety classification;
- NFS backing mounts by filesystem UUID, type, mount point, and mount options;
- NFS client networks and export options;
- passthrough PCI addresses/IDs, the expected IOMMU group, and every member of
  that group;
- the host-specific IOMMU kernel parameters.

To add an already-joined node, add it to `proxmox_hosts` and the applicable
`nfs_servers`/`vfio_hosts` inventory groups, then add a matching host-vars file.
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

The workstation host requires a second opt-in after coordinating with its user:

```bash
ansible-playbook playbooks/reboot-proxmox.yml \
  --limit gpunvdgtx1060 \
  -e proxmox_reboot_approved=true \
  -e proxmox_workstation_reboot_approved=true
```

After a reboot, run `playbooks/verify-proxmox.yml`. Also verify both NFS exports
from a Talos worker and confirm the NVIDIA device plugin reports the GPUs inside
Kubernetes; those guest/cluster checks remain outside physical-host Ansible.
