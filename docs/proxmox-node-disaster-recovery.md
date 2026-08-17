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

Both nodes use gateway `192.168.1.1` and bridge `vmbr0`. Daily snapshot jobs
run at 02:15 (`smallgpu`) and 04:15 (`largegpu`), use Zstandard, and retain
three recent plus two weekly archives.

The one-time 2026 emergency recovery is different: it uses
`ansible/playbooks/restore-smallgpu.yml` and the accepted workstation qcow2
images. Normal recovery uses native `vzdump` archives and
`ansible/playbooks/restore-proxmox-node.yml`; do not mix the two workflows.

## Identify the failed node and survivor

On the surviving node:

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

Stop if model, serial, partition layout, or UUID differs. Select only the
failed Proxmox **system** NVMe as the installer target. Never select either bulk
disk, and never use `wipefs`, `mkfs`, `fsck`, a partition editor, or a forced
mount against it.

## Reinstall with the original identity

| Node | Hostname | Address | Gateway | Bridge |
|---|---|---|---|---|
| small | `smallgpu` | `192.168.1.106/24` | `192.168.1.1` | `vmbr0` |
| large | `largegpu` | `192.168.1.107/24` | `192.168.1.1` | `vmbr0` |

Install a Proxmox major version compatible with the survivor. Do not restore an
old `config.db` or copy `/etc/pve` from a backup.

## Remove stale membership safely

If the survivor lost quorum, inspect exact membership first. Only use a
temporary expected-votes override for the known surviving node:

```bash
sudo scripts/proxmox/remove-stale-node.sh smallgpu --dry-run
sudo scripts/proxmox/remove-stale-node.sh smallgpu \
  --expected-votes 1 \
  --archive-configs /root/retired-node-configs \
  --remove-node-dir \
  --confirm-node-dir-removal smallgpu
```

Replace `smallgpu` with the exact failed node. The script refuses the local or
live node, displays quorum and Corosync state, requires remaining guest configs
to be archived, and accepts `CS_ERR_NOT_EXIST` only after confirming the node
is absent from live membership. Never manually run a broad deletion under
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
`192.168.1.107/24`, and confirmation `largegpu`. Authentication is interactive;
never place a password in a command or the repository.

## Reapply physical-host configuration

```bash
cd /home/kfir/repos/homelab/ansible
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/configure-proxmox.yml --limit smallgpu --check --diff
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/configure-proxmox.yml --limit smallgpu --diff
```

Use `largegpu` for that host. Review check mode before convergence. Ansible
adopts known filesystems by UUID and never formats them. Reboot only if the VFIO
role explicitly requires it, before starting a GPU VM.

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

For `smallgpu`, VM 201 is restored before VM 403. Validate 201 before relying
on 403. VM 403 must retain `hostpci` device `09:00`; the role checks this before
optional startup.

## Validate Talos and Kubernetes

Never run `talosctl bootstrap` while restoring the existing control plane.
Never reset Talos, regenerate secrets, or reapply machine configuration merely
because a VM was restored.

```bash
talosctl --talosconfig talosconfig health
talosctl --talosconfig talosconfig -n 192.168.1.211 service etcd
kubectl --kubeconfig kubeconfig.yaml get nodes -o wide
kubectl --kubeconfig kubeconfig.yaml get pods -A -o wide
kubectl --kubeconfig kubeconfig.yaml get pv,pvc -A
```

For VM 403, verify `gpu-3` is Ready, `nvidia.com/gpu` allocatable is `1`, the
`media-state` disk is visible, and GPU Operator pods are ready. If VM 201 or
etcd fails, stop and collect Talos health, service state, VM console, and
Proxmox task logs. Do not bootstrap a replacement etcd cluster.

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
9. Validate VM 201, Talos API, etcd, Kubernetes, then VM 403 and GPU.
10. Run a read-only Terraform plan. Never run `talosctl bootstrap` or
    `terraform apply` as part of this restore.

