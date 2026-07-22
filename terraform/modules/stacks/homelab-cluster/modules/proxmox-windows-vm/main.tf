# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Module: proxmox-windows-vm                                                ║
# ║                                                                            ║
# ║  Two operating modes, picked by var.template_vm_id:                        ║
# ║                                                                            ║
# ║  1. INSTALL mode (template_vm_id == null)                                  ║
# ║     Builds an empty VM with everything Win11 needs: q35 + OVMF + Secure    ║
# ║     Boot pre-enrolled keys, vTPM 2.0, scsi0 install disk, Windows ISO on   ║
# ║     ide2. Used ONCE to install Windows + apps + drivers, after which the   ║
# ║     VM is shut down, cloned in the Proxmox UI to a new VM (e.g. 9000),     ║
# ║     and that copy is converted to a template.                              ║
# ║                                                                            ║
# ║  2. CLONE mode (template_vm_id set to the template's VMID)                 ║
# ║     Skips disk/EFI/TPM/CDROM blocks — those are inherited from the         ║
# ║     template's snapshot. Each apply produces a fresh, ready-to-use         ║
# ║     Windows VM in ~30 seconds (linked clone). GPU passthrough, USB,        ║
# ║     CPU/RAM/cores all configured per-clone.                                ║
# ║                                                                            ║
# ║  Networking is left to DHCP (Windows ignores nocloud cidata, so static     ║
# ║  IP via Terraform is not possible without an Autounattend.xml setup).     ║
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

variable "template_vm_id" {
  description = <<-EOT
    VMID of a Proxmox template to clone from. When set, the resource clones
    that template (fast, fully-installed Windows VM) instead of building an
    empty install VM. When null (default), runs INSTALL mode and attaches
    the Windows ISO so you can do a one-time install.
  EOT
  type        = number
  default     = null
}

variable "full_clone" {
  description = "Full clone (independent disk) vs linked clone (CoW from template). Full = safer, slower; linked = ~30s and shares disk pages with the template."
  type        = bool
  default     = true
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
  description = "INSTALL mode: size of the install disk. CLONE mode: ignored — disk size is inherited from the template (resize manually post-clone if needed)."
  default     = 150
}

variable "datastore_id" {
  description = "Proxmox datastore for VM disk, EFI vars and TPM state (INSTALL mode)"
  type        = string
  default     = "local-lvm"
}

variable "iso_datastore_id" {
  description = "Proxmox datastore where the Windows + virtio-win ISOs live (INSTALL mode)"
  type        = string
  default     = "local"
}

variable "windows_iso" {
  description = "Filename of the Windows installer ISO inside iso_datastore_id (INSTALL mode)"
  type        = string
  default     = ""
}

# NOTE: bpg/proxmox v0.105 only allows one `cdrom` block per VM. Attach
# virtio-win.iso manually for the install (Proxmox UI → Hardware → Add →
# CD/DVD on a SATA slot — SATA is hot-pluggable, IDE is not), then detach
# once Windows is installed.
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
# Locals — derive mode flags
# ──────────────────────────────────────────────────────────────────────────────

locals {
  is_install_mode = var.template_vm_id == null
  is_clone_mode   = var.template_vm_id != null
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

  # ── CLONE mode: source from template ───────────────────────────────────────
  # When set, all the install-only blocks below (disk, efi_disk, tpm_state,
  # cdrom) are skipped and inherited from the template instead.
  dynamic "clone" {
    for_each = local.is_clone_mode ? [1] : []
    content {
      vm_id = var.template_vm_id
      full  = var.full_clone
    }
  }

  # ── INSTALL mode: empty install disk ───────────────────────────────────────
  # scsi0 = empty install target. Windows can't see virtio-scsi without the
  # driver — at the disk-selection screen during install, click "Load driver"
  # and point at the virtio-win ISO mounted on a SATA slot.
  dynamic "disk" {
    for_each = local.is_install_mode ? [1] : []
    content {
      datastore_id = var.datastore_id
      interface    = "scsi0"
      size         = var.disk_size_gb
      file_format  = "raw"
      ssd          = true
      discard      = "on"
      iothread     = true
    }
  }

  # ── INSTALL mode: EFI + TPM (Win11 hard requirements) ──────────────────────
  dynamic "efi_disk" {
    for_each = local.is_install_mode ? [1] : []
    content {
      datastore_id      = var.datastore_id
      type              = "4m"
      pre_enrolled_keys = true # Microsoft Secure Boot keys baked in
    }
  }

  dynamic "tpm_state" {
    for_each = local.is_install_mode ? [1] : []
    content {
      datastore_id = var.datastore_id
      version      = "v2.0"
    }
  }

  # ── INSTALL mode: Windows installer ISO ────────────────────────────────────
  # bpg/proxmox v0.105 caps cdrom at 1 block. Mount virtio-win.iso manually
  # on a SATA slot (hot-pluggable) before powering on for the first time.
  dynamic "cdrom" {
    for_each = local.is_install_mode ? [1] : []
    content {
      file_id   = "${var.iso_datastore_id}:iso/${var.windows_iso}"
      interface = "ide2"
    }
  }

  # ── Network ────────────────────────────────────────────────────────────────
  # virtio = best perf, but Windows install media has no driver — load it from
  # the virtio-win ISO during OOBE if Windows doesn't see the NIC.
  network_device {
    bridge = var.bridge
    model  = "virtio"
  }

  # ── PCIe passthrough (GPU) ─────────────────────────────────────────────────
  # Configured in both modes — clones get their own GPU per VM, not the
  # template's. (Templates are shut down anyway, so they hold no live PCIe.)
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

  # INSTALL mode: boot from CD first so the Windows installer runs on first
  # power-on. CLONE mode: boot order is inherited from the template (which
  # was set up to boot from scsi0/the Windows install).
  boot_order = local.is_install_mode ? ["ide2", "scsi0"] : []

  on_boot = false # don't auto-start; user toggles between Talos and Windows manually

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
