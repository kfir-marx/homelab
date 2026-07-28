# Ubuntu workstation and NFS runbook

The retired `gpunvdgtx1060` Proxmox node is now an Ubuntu 26.04 workstation at
`192.168.1.105`. It is not a hypervisor or Kubernetes worker. Its homelab role
is to mount and export the existing critical `storage2-bulk` filesystem while
Ubuntu retains the GTX 1060 and its HDMI output.

The attempted Talos/libvirt design was retired because this PH315-51 routes its
only native external-display output through the NVIDIA GPU. Exclusive PCI
passthrough therefore prevents Ubuntu from using HDMI.

## 1. Converge the NFS-only workstation

Run from a local workstation session:

```bash
cd /home/kfir/repos/homelab/ansible
sudo apt update
sudo apt install -y ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml --ask-become-pass
```

The workstation inventory uses `/usr/bin/sudo.ws` for Ansible privilege
escalation. Ubuntu 26.04 defaults to `sudo-rs`, whose PAM prompt can cause
Ansible to time out after the become password is entered. This per-host setting
does not change the system-wide `sudo` alternative.

The play:

- mounts ext4 UUID `07445d19-37d4-4353-af1a-9511fb9c74e9` at
  `/mnt/storage2-bulk`;
- exports it read/write to `192.168.1.0/24` as NFSv4 `fsid=10`;
- removes the retired QEMU/libvirt/virt-manager packages;
- removes the repository-managed VFIO module, binding, blacklist, and GRUB
  files; and
- rebuilds initramfs and GRUB when those boot files change.

It never formats the storage disk, changes the active network connection, or
reboots the workstation.

## 2. Reboot and restore NVIDIA/HDMI

The GTX 1060 remains owned by `vfio-pci` until the next boot even after its
configuration files are removed. Close applications and reboot explicitly:

```bash
sudo reboot
```

After reconnecting, verify that Ubuntu owns both NVIDIA functions:

```bash
lspci -nnk -s 00:02.0
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
nvidia-smi
```

Expected drivers:

- Intel UHD 630 `00:02.0`: `i915`
- GTX 1060 `01:00.0`: `nvidia`
- NVIDIA HDMI audio `01:00.1`: `snd_hda_intel`

With an HDMI monitor connected, Ubuntu Settings → Displays should now expose
the external display. The host already has the Ubuntu `nvidia-driver-580`
packages installed.

## 3. Assign the stable NFS address

Kubernetes PVs use `192.168.1.105:/mnt/storage2-bulk`, so the workstation must
own that address. No LAN bridge is required. The dedicated
`homelab-enp7s0f1` profile owns the static address on `enp7s0f1`; the original
`netplan-enp7s0f1` DHCP profile remains available with autoconnect disabled.

The one-time configuration was:

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

Run profile activation only from the local desktop because it interrupts
Ethernet. Verify:

```bash
nmcli -t -f NAME,TYPE,DEVICE connection show --active
ip -br address show enp7s0f1
ip route
ping -c 3 192.168.1.1
```

To recover with DHCP from the local desktop:

```bash
sudo nmcli connection down homelab-enp7s0f1
sudo nmcli connection up netplan-enp7s0f1
```

## 4. Verify NFS

```bash
findmnt /mnt/storage2-bulk
systemctl is-enabled nfs-kernel-server
systemctl is-active nfs-kernel-server
sudo exportfs -v
```

The export must report `/mnt/storage2-bulk` for `192.168.1.0/24`. Verify it
from another LAN system or a Kubernetes debug pod before moving critical
applications back:

```bash
showmount -e 192.168.1.105
```

The Kubernetes manifests intentionally retain
`192.168.1.105:/mnt/storage2-bulk`; no PV migration is required.
