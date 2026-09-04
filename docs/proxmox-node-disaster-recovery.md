# Proxmox node disaster recovery

Use this runbook when one Proxmox **system disk** fails. Assume the bulk disk
on that host is intact unless physical inspection proves otherwise. Never
erase, initialize, partition, format, repair, or force-mount a bulk/media disk
during node replacement.

## Recovery model

| Failed node | Address | Opposite-node storage | Physical destination |
|---|---:|---|---|
| `smallgpu` | `192.168.1.106/24` | `backup-on-largegpu` | `largegpu:/mnt/pve/largegpu-hdd/proxmox-backups/from-smallgpu` |
| `largegpu` | `192.168.1.107/24` | `backup-on-smallgpu` | `smallgpu:/mnt/data10tb/proxmox-backups/from-largegpu` |
| `tinygpu` | `192.168.1.108/24` | None declared | No native VM backup destination is configured |

All three nodes use gateway `192.168.1.1` and bridge `vmbr0`. The general snapshot
jobs run at 02:15 (`smallgpu`) and 07:00 (`largegpu`), use Zstandard, and retain
three recent plus two weekly archives. Standalone gaming VM `502` has a
separate 04:15 job with two recent archives. All jobs use snapshot mode without
power-management hooks; backup automation does not change any VM power state.
The `smallgpu` all-VM job includes `cp-2`/VM 202 after its first successful run.
`tinygpu` has no backup role or opposite-node storage declaration, so this
runbook does not claim an automated VM 203 restore path.

The one-time 2026 emergency recovery is different: it uses
`ansible/playbooks/restore-smallgpu.yml` and the accepted workstation qcow2
images. Normal recovery uses native `vzdump` archives and
`ansible/playbooks/restore-proxmox-node.yml`; do not mix the two workflows.

## Identify the failed node and survivors

On either surviving node:

```bash
pvecm status
pvecm nodes
qm list
pvesm status
```

Confirm which node is physically unreachable. Check power, link, management
ping, and the Proxmox UI; one unavailable VM is not proof of host failure.

List archives without modifying them:

```bash
pvesm list backup-on-largegpu --content backup
pvesm list backup-on-smallgpu --content backup
```

## Identify every disk before reinstalling

At the replacement-node console, record device path, size, model, serial,
partitions, filesystem signatures, and LVM membership:

```bash
lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,MODEL,SERIAL
blkid
pvs -o pv_name,pv_uuid,vg_name,pv_size
vgs
lvs -a -o vg_name,lv_name,lv_size,devices
```

Expected bulk disks at the time of writing:

- `smallgpu`: 10 TB Toshiba `MG06ACA10TE`, serial `6160A0F6FR7H`, existing
  NTFS UUID `44BA5D2BBA5D1B2E`, mounted at `/mnt/data10tb`.
- `largegpu`: 2 TB WDC `WD20EZBX-00AYRA0`, serial
  `WD-WXK2A61R9JYK`, existing ext4 UUID
  `84e5ba00-6169-4082-b453-ec93f693f167`, mounted at
  `/mnt/pve/largegpu-hdd`.
- `tinygpu`: the 1 TB WDC `WD10EZEX-60WN4A0`, serial
  `WD-WCC6Y2VY58JE`, is the Proxmox system disk and contains `local-lvm`.
  There is no separately declared bulk or application-data disk.

Stop if model, serial, partition layout, or UUID differs. Select only the
failed Proxmox **system** disk as the installer target; this is an HDD on
`tinygpu` and NVMe on the other hosts. Never select either bulk disk, and never
use `wipefs`, `mkfs`, `fsck`, a partition editor, or a forced mount against it.

## Reinstall with the original identity

| Node | Hostname | Address | Gateway | Bridge |
|---|---|---|---|---|
| small | `smallgpu` | `192.168.1.106/24` | `192.168.1.1` | `vmbr0` |
| large | `largegpu` | `192.168.1.107/24` | `192.168.1.1` | `vmbr0` |
| tiny | `tinygpu` | `192.168.1.108/24` | `192.168.1.1` | `vmbr0` |

Install a Proxmox major version compatible with the survivor. Do not restore an
old `config.db` or copy `/etc/pve` from a backup.

## Remove stale membership safely

With one failed node, the two survivors retain Proxmox quorum. Inspect exact
membership first and do not change expected votes for this normal case:

```bash
sudo scripts/proxmox/remove-stale-node.sh smallgpu --dry-run
sudo scripts/proxmox/remove-stale-node.sh smallgpu \
  --archive-configs /root/retired-node-configs \
  --remove-node-dir \
  --confirm-node-dir-removal smallgpu
```

Replace `smallgpu` with the exact failed node. The script refuses the local or
live node, displays quorum and Corosync state, requires remaining guest configs
to be archived, and accepts `CS_ERR_NOT_EXIST` only after confirming the node
is absent from live membership. If two Proxmox nodes are unavailable, stop and
establish which identities and disks survive before considering any temporary
expected-votes override. Never manually run a broad deletion under
`/etc/pve/nodes`.

## Join the replacement

Run locally on the freshly installed node. The script checks hostname/FQDN,
address, gateway, `vmbr0`, reachability, major version, and that no unexpected
guests exist. Joining replaces local `/etc/pve` state.

```bash
sudo scripts/proxmox/join-replacement-node.sh 192.168.1.107 \
  --expected-hostname smallgpu \
  --expected-address 192.168.1.106/24 \
  --expected-gateway 192.168.1.1 \
  --dry-run

sudo scripts/proxmox/join-replacement-node.sh 192.168.1.107 \
  --expected-hostname smallgpu \
  --expected-address 192.168.1.106/24 \
  --expected-gateway 192.168.1.1 \
  --confirm-join smallgpu
```

For `largegpu`, use survivor `192.168.1.106`, expected address
`192.168.1.107/24`, and confirmation `largegpu`. For `tinygpu`, use either
healthy survivor, expected address `192.168.1.108/24`, and confirmation
`tinygpu`. Authentication is interactive; never place a password in a command
or the repository.

## Reapply physical-host configuration

```bash
cd /home/kfir/repos/homelab/ansible
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/configure-proxmox.yml --limit smallgpu --check --diff
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/configure-proxmox.yml --limit smallgpu --diff
```

Use `largegpu` or `tinygpu` for those hosts. `tinygpu` receives only the common
Proxmox baseline because it has no storage, backup, NFS, UPS, or VFIO inventory
role. Review check mode before convergence. Ansible adopts known filesystems by
UUID and never formats them. Reboot only if the VFIO role explicitly requires
it, before starting a GPU VM.

## Select and restore native backups

Check mode prints the exact newest archive selected for each VM:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/restore-proxmox-node.yml --limit smallgpu --check
```

The live restore is separately confirmed and leaves VMs stopped by default:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/restore-proxmox-node.yml --limit smallgpu \
  -e proxmox_restore_confirm=true
```

The role verifies hostname, cluster/quorum, `vmbr0`, backup and target storage,
VM-ID absence, and absence of target volumes. It restores in declared order
with `qmrestore`, preserving IDs and saved configuration, and never overwrites a
VM or volume. Startup requires another explicit switch:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/restore-proxmox-node.yml --limit smallgpu \
  -e proxmox_restore_confirm=true \
  -e proxmox_native_restore_start=true
```

For `smallgpu`, restore control-plane VM 202 before VM 403. VM 403 must retain
`hostpci` device `09:00`; the role checks this before optional startup. The two
surviving control planes retain etcd quorum throughout a one-host recovery.

For `largegpu`, restore control-plane VM 201 to `largegpu-hdd`, followed by GPU
worker 402 on the HDD and standalone Windows VM 502 on NVMe. Automatic startup
starts only VM 201; choose either 402 or 502 manually after validating the
Kubernetes API. Never start VM 402 and Windows VM 502 together.

There is currently no native-backup restore mapping for `tinygpu` or VM 203.
Loss of that system disk leaves `cp-1` and `cp-2` quorate. Do not improvise a
restore, delete the stale etcd member, or remove VM 203 from Terraform state
until a reviewed Talos control-plane replacement procedure has identified the
surviving member and preserved cluster secrets. A replacement must join the
existing etcd cluster; it must never bootstrap a new one.

## Validate Talos and Kubernetes

Never run `talosctl bootstrap` while restoring the existing control plane.
Never reset Talos, regenerate secrets, or reapply machine configuration merely
because a VM was restored.

```bash
talosctl --talosconfig talosconfig health
talosctl --talosconfig talosconfig -n 192.168.1.211,192.168.1.212,192.168.1.213 service etcd
talosctl --talosconfig talosconfig -n 192.168.1.211 etcd members
kubectl --kubeconfig kubeconfig.yaml get nodes -o wide
kubectl --kubeconfig kubeconfig.yaml get pods -A -o wide
kubectl --kubeconfig kubeconfig.yaml get pv,pvc -A
```

For VM 403, verify `gpu-3` is Ready, `nvidia.com/gpu` allocatable is `1`, the
`media-state` disk is visible, and GPU Operator pods are ready. If the restored
control-plane member does not rejoin etcd, or the member list is not exactly
three healthy non-learners after recovery, stop and collect Talos health,
service state, VM console, and Proxmox task logs. Do not bootstrap a replacement
etcd cluster.

## Inspect drift without applying it

```bash
cd /home/kfir/repos/homelab
./terraform/run-terragrunt.sh prod homelab-cluster plan -input=false
```

Do not apply if the plan proposes replacing or deleting restored VMs, disks,
Talos machines, or Kubernetes resources. Record and reconcile that drift
deliberately.

## Retention and manual cleanup

```bash
pvesh get /cluster/backup
pvesm list backup-on-largegpu --content backup
pvesm list backup-on-smallgpu --content backup
journalctl -u pvescheduler --since today
```

Do not delete an archive merely to free space during recovery. Identify the
exact `volid`, confirm a newer successful backup for the same VM, and obtain
explicit authorization before using the supported Proxmox storage API. Never
delete the emergency qcow2 images.

## Emergency checklist

1. Confirm failed node, surviving quorum, and opposite-node storage.
2. Record every disk's path, size, model, serial, partitions, UUIDs, and LVM.
3. Reinstall only the failed system NVMe with the original network identity.
4. Dry-run, then use `remove-stale-node.sh` on the survivor if required.
5. Dry-run, then use `join-replacement-node.sh` on the replacement.
6. Run host Ansible in check mode, review it, then converge one node.
7. Run native restore in check mode and read the selected archives.
8. Restore with confirmation; start only when explicitly enabled.
9. Validate the restored control plane first, exactly three healthy etcd
   members, the API VIP, and Kubernetes before validating worker VM(s) and GPU.
10. Run a read-only Terraform plan. Never run `talosctl bootstrap` or
    `terraform apply` as part of this restore.
