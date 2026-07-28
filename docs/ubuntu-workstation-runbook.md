# Ubuntu workstation migration runbook

This runbook completes the replacement of the retired
`gpunvdgtx1060` Proxmox node with Ubuntu 26.04 on the same hardware.
Ubuntu owns the physical workstation and NFS service; Terraform owns a Talos
VM named `gpu-1` through system libvirt.

## What was wrong with the manual VM

The existing stopped domain `talos-worker` has the requested 4 vCPUs and
6 GiB RAM, and its copied qcow2 passes `qemu-img` integrity inspection. Two
domain settings prevent it from being a usable cluster node:

- virt-manager enabled Secure Boot, but the copied Proxmox disk came from the
  normal non-Secure-Boot Image Factory artifact. Selecting its UEFI disk returns
  immediately to the firmware boot picker;
- its NIC is attached to libvirt's NAT-only `default` network
  (`192.168.122.0/24`), so other Talos nodes cannot reach the required static
  LAN address.

Terraform creates a new domain instead of adopting that experiment: q35,
plain OVMF UEFI, 4 host-passthrough vCPUs, 6144 MiB RAM, 50 GiB qcow2,
Ubuntu `br0`, and both GTX 1060 PCI functions.

## 1. Configure Ubuntu packages, NFS, and VFIO

Run from a local workstation session:

```bash
cd /home/kfir/repos/homelab/ansible
sudo apt update
sudo apt install -y ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml --ask-become-pass
```

The workstation inventory uses `/usr/bin/sudo.ws` for Ansible privilege
escalation. Ubuntu 26.04 defaults to `sudo-rs`, whose PAM prompt can cause
Ansible to time out after the become password is entered. This per-host
setting leaves the workstation's system-wide `sudo` alternative unchanged.

This play:

- installs QEMU/KVM, libvirt, virt-manager, OVMF, image tools, and NFS;
- mounts UUID `07445d19-37d4-4353-af1a-9511fb9c74e9` at
  `/mnt/storage2-bulk`;
- exports it read/write to `192.168.1.0/24` as NFSv4 `fsid=10`;
- stages `intel_iommu=on iommu=pt` and VFIO ownership for `10de:1c20` and
  `10de:10f1`;
- leaves the Intel UHD 630 available to Ubuntu.

It never formats the disk, activates a network bridge, detaches the live GPU,
or reboots.

Verify the storage before continuing:

```bash
findmnt /mnt/storage2-bulk
sudo exportfs -v
systemctl is-enabled nfs-kernel-server
systemctl is-active nfs-kernel-server
```

The Kubernetes manifests intentionally keep
`192.168.1.105:/mnt/storage2-bulk`; no PV migration is required.

## 2. Create the LAN bridge

Do this only from the local desktop because Ethernet will disconnect briefly:

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

After reconnecting:

```bash
ip -br address show br0
ip route
ping -c 3 192.168.1.1
```

The bridge must own `192.168.1.105/24`; the physical Ethernet interface is only
its port. If activation fails, restore the previous profile locally:

```bash
sudo nmcli connection up netplan-enp7s0f1
```

## 3. Reboot into VFIO ownership

Close workstation applications and reboot explicitly:

```bash
sudo reboot
```

Then verify both NVIDIA functions use VFIO and the Intel display remains on
`i915`:

```bash
lspci -nnk -s 00:02.0
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
readlink /sys/bus/pci/devices/0000:01:00.0/iommu_group
ls -l /dev/vfio/2
```

Do not apply Terraform unless `01:00.0` and `01:00.1` both report
`Kernel driver in use: vfio-pci`.

## 4. Retire the manual domain without deleting its disk

The Terraform domain is named `gpu-1`, so the old definition does not collide.
Keep a rollback copy of its XML, then undefine only the domain:

```bash
virsh dumpxml talos-worker > "$HOME/talos-worker.pre-terraform.xml"
virsh destroy talos-worker 2>/dev/null || true
virsh undefine talos-worker --nvram
```

This does not delete `/home/kfir/talos-worker.qcow2`. Keep that file until
`gpu-1` has joined the cluster and passed GPU validation.

## 5. Plan and apply locally

The cluster stack must run on this workstation because `qemu:///system` always
means the machine executing Terraform. It is intentionally excluded from
Atlantis.

```bash
cd /home/kfir/repos/homelab
./terraform/run-terragrunt.sh prod homelab-cluster plan
./terraform/run-terragrunt.sh prod homelab-cluster apply
```

The apply downloads the Pascal-specific Talos Image Factory artifact, converts
it to a sparse 50 GiB qcow2, creates a small NoCloud CIDATA ISO for the initial
`192.168.1.231/24` address, defines `gpu-1`, and applies the same Talos GPU
machine configuration used by the other workers.

The GTX 1060 is Pascal, so its image uses the proprietary NVIDIA R580 LTS
extension and CUDA 12.x workloads. NVIDIA documents that open kernel modules
require Turing or newer and that R580/CUDA 12.x is the final Pascal line:
[kernel module support](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/kernel-modules.html),
[architecture matrix](https://docs.nvidia.com/datacenter/tesla/drivers/cuda-toolkit-driver-and-architecture-matrix.html).

## 6. Verify the result

```bash
virsh list --all
virsh dominfo gpu-1
virsh domiflist gpu-1
virsh nodedev-dumpxml pci_0000_01_00_0 | sed -n '1,80p'

talosctl -n 192.168.1.231 version
kubectl get nodes -o wide
kubectl describe node gpu-1
```

Verify NFS from a Talos/Kubernetes client and run the CUDA 12.9 validation in
the [GPU Operator runbook](gpu-operator-runbook.md). Only after those checks
pass should the copied manual qcow2 be considered obsolete.
