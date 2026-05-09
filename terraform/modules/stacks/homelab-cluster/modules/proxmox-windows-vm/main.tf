# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Module: proxmox-windows-vm                                                ║
# ║                                                                            ║
# ║  Creates a single Proxmox QEMU VM tailored for a Windows 11 install:       ║
# ║    • UEFI (OVMF) + Secure Boot pre-enrolled keys      → Win11 requirement  ║
# ║    • vTPM 2.0 (tpm_state)                             → Win11 requirement  ║
# ║    • q35 chipset                                      → required for       ║
# ║                                                         clean PCIe         ║
# ║                                                         passthrough        ║
# ║    • Two CD-ROM devices: Windows installer + virtio-win drivers ISO        ║
# ║    • Empty SCSI disk for Windows to install onto                           ║
# ║    • Optional GPU passthrough (the same physical card the Talos GPU        ║
# ║      worker normally uses — the parent stack enforces that only one of     ║
# ║      the two VMs exists at a time, see homelab-cluster/main.tf).           ║
# ║    • Optional USB passthrough — typically mouse, keyboard, and game        ║
# ║      controllers so the VM is usable as a TV/console PC.                   ║
# ║                                                                            ║
# ║  Networking is left to DHCP (Windows ignores nocloud cidata, so static     ║
# ║  IP via Terraform is not possible without an Autounattend.xml setup —      ║
# ║  out of scope here). DHCP-reserve at the router for stable addressing.    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.105.0"
    }
  }
}

# ──────────────────────────────────────────────────────────────────────────────
# Variables
# ──────────────────────────────────────────────────────────────────────────────

variable "hostname" {
  type = string
}

variable "proxmox_node" {
  description = "Proxmox host to place this VM on"
  type        = string
}

variable "vm_id" {
  type = number
}

variable "cores" {
  type    = number
  default = 8
}

variable "memory_mb" {
  type    = number
  default = 16384
}

variable "disk_size_gb" {
  type        = number
  description = "Size of the empty install disk; Windows 11 needs ~64 GB minimum"
  default     = 150
}

variable "datastore_id" {
  description = "Proxmox datastore for the VM disk, EFI vars and TPM state"
  type        = string
  default     = "local-lvm"
}

variable "iso_datastore_id" {
  description = "Proxmox datastore where the Windows + virtio-win ISOs live"
  type        = string
  default     = "local"
}

variable "windows_iso" {
  description = "Filename of the Windows installer ISO inside iso_datastore_id"
  type        = string
}

# NOTE: The virtio-win drivers ISO is *not* attached by Terraform — bpg/proxmox
# v0.105 only allows one `cdrom` block per VM. Attach virtio-win.iso manually
# for the install (Proxmox UI → VM → Hardware → Add → CD/DVD → IDE3 →
# local:iso/virtio-win.iso), then detach once Windows boots.
variable "virtio_iso" {
  description = "Reserved for future use (currently mounted manually — see module header)"
  type        = string
  default     = "virtio-win.iso"
}

variable "bridge" {
  description = "Proxmox network bridge for the virtio NIC"
  type        = string
  default     = "vmbr0"
}

variable "pci_devices" {
  description = "PCIe devices to passthrough (leave empty for no GPU)"
  type = list(object({
    id   = string
    pcie = bool
  }))
  default = []
}

variable "usb_devices" {
  description = <<-EOT
    USB devices to passthrough. Each entry is either:
      • host = "vendor:product"  (e.g. "046d:c52b") — survives replug, but
        ambiguous if two devices share the same VID:PID
      • host = "bus-port"        (e.g. "1-2.4")     — physical port, but
        moves if you replug into a different socket
    Get values from `lsusb` on the Proxmox host.
  EOT
  type = list(object({
    host = string
    usb3 = bool
  }))
  default = []
}

# ──────────────────────────────────────────────────────────────────────────────
# VM resource
# ──────────────────────────────────────────────────────────────────────────────

resource "proxmox_virtual_environment_vm" "this" {
  name      = var.hostname
  node_name = var.proxmox_node
  vm_id     = var.vm_id
  tags      = ["windows", "terraform"]

  # q35 + OVMF is required for both the GPU passthrough path AND for Windows 11
  # (Secure Boot, TPM). Use it unconditionally — there's no SeaBIOS branch here.
  machine = "q35"
  bios    = "ovmf"

  cpu {
    cores = var.cores
    type  = "host"
  }

  memory {
    dedicated = var.memory_mb
  }

  # ── Disks ──────────────────────────────────────────────────────────────────
  # scsi0 = empty install target. Windows can't see virtio-scsi without the
  # driver — at the disk-selection screen during install, click "Load driver"
  # and point at the virtio-win ISO mounted on ide3 below.
  disk {
    datastore_id = var.datastore_id
    interface    = "scsi0"
    size         = var.disk_size_gb
    file_format  = "raw"
    ssd          = true
    discard      = "on"
    iothread     = true
  }

  # ── EFI + TPM (Win11 hard requirements) ────────────────────────────────────
  efi_disk {
    datastore_id      = var.datastore_id
    type              = "4m"
    pre_enrolled_keys = true # Microsoft Secure Boot keys baked in
  }

  tpm_state {
    datastore_id = var.datastore_id
    version      = "v2.0"
  }

  # ── Installer ISO ─────────────────────────────────────────────────────────
  # Only one cdrom block is permitted by bpg/proxmox 0.105, so this is the
  # Windows installer. Mount virtio-win.iso on a second IDE slot manually in
  # the Proxmox UI before powering on (needed to load vioscsi during install
  # and NetKVM during OOBE — see module header).
  cdrom {
    file_id   = "${var.iso_datastore_id}:iso/${var.windows_iso}"
    interface = "ide2"
  }

  # ── Network ────────────────────────────────────────────────────────────────
  # virtio = best perf, but Windows install media has no driver — load it from
  # the virtio-win ISO during OOBE if Windows doesn't see the NIC.
  network_device {
    bridge = var.bridge
    model  = "virtio"
  }

  # ── PCIe passthrough (GPU) ─────────────────────────────────────────────────
  dynamic "hostpci" {
    for_each = var.pci_devices
    content {
      device = "hostpci${hostpci.key}"
      id     = hostpci.value.id
      pcie   = hostpci.value.pcie
      rombar = true
    }
  }

  # ── USB passthrough (HID + controllers) ────────────────────────────────────
  dynamic "usb" {
    for_each = var.usb_devices
    content {
      host = usb.value.host
      usb3 = usb.value.usb3
    }
  }

  operating_system {
    type = "win11"
  }

  # Try the installer CD first; once Windows writes its bootloader to scsi0,
  # OVMF's NVRAM remembers the Windows EFI entry and boots that automatically.
  boot_order = ["ide2", "scsi0"]

  on_boot = false # don't auto-start; user picks talos vs windows manually

  # Don't have Terraform bounce the VM on every drift in these mutable fields
  # (the user will tweak USB lists, memory, etc. in the Proxmox UI sometimes).
  lifecycle {
    ignore_changes = [
      started,
    ]
  }
}

# ──────────────────────────────────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────────────────────────────────

output "vm_id" {
  value = proxmox_virtual_environment_vm.this.vm_id
}

output "name" {
  value = proxmox_virtual_environment_vm.this.name
}
