# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Module: proxmox-vm                                                        ║
# ║  Creates a single Proxmox QEMU VM suitable for Talos Linux.                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.78"
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
  default = 4
}

variable "memory_mb" {
  type    = number
  default = 8192
}

variable "disk_size_gb" {
  type    = number
  default = 50
}

variable "ip_address" {
  description = "Static IP in CIDR notation (e.g. 10.0.10.11/24)"
  type        = string
}

variable "gateway" {
  type = string
}

variable "bridge" {
  type    = string
  default = "vmbr0"
}

variable "image_file_id" {
  description = "Proxmox file ID of the downloaded Talos nocloud image"
  type        = string
}

variable "pci_devices" {
  description = "PCIe devices to passthrough (empty list = no passthrough)"
  type = list(object({
    id   = string
    pcie = bool
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
  tags      = ["talos", "terraform"]

  machine = length(var.pci_devices) > 0 ? "q35" : "q35"
  bios    = length(var.pci_devices) > 0 ? "ovmf" : "seabios"

  cpu {
    cores = var.cores
    type  = "host"
  }

  memory {
    dedicated = var.memory_mb
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    size         = var.disk_size_gb
    file_id      = var.image_file_id
    file_format  = "raw"
    ssd          = true
    discard      = "on"
  }

  network_device {
    bridge = var.bridge
    model  = "virtio"
  }

  dynamic "efi_disk" {
    for_each = length(var.pci_devices) > 0 ? [1] : []
    content {
      datastore_id = "local-lvm"
      type         = "4m"
    }
  }

  dynamic "hostpci" {
    for_each = var.pci_devices
    content {
      device = "hostpci${hostpci.key}"
      id     = hostpci.value.id
      pcie   = hostpci.value.pcie
      rombar = true
    }
  }

  operating_system {
    type = "l26"
  }

  on_boot = true

  lifecycle {
    ignore_changes = [disk[0].file_id]
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

output "ip_address" {
  value = var.ip_address
}
