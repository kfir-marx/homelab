terraform {
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.9.8"
    }
  }
}

variable "hostname" {
  type = string
}

variable "cores" {
  type = number
}

variable "memory_mb" {
  type = number
}

variable "disk_size_gb" {
  type = number
}

variable "pool" {
  type = string
}

variable "bridge" {
  type = string
}

variable "mac_address" {
  type = string
}

variable "system_image_path" {
  type = string
}

variable "cidata_image_path" {
  type = string
}

variable "ovmf_code_path" {
  type = string
}

variable "ovmf_vars_path" {
  type = string
}

variable "pci_devices" {
  type = list(object({
    domain   = number
    bus      = number
    slot     = number
    function = number
  }))
}

resource "libvirt_volume" "system" {
  name          = "${var.hostname}.qcow2"
  pool          = var.pool
  capacity      = var.disk_size_gb * 1024 * 1024 * 1024
  capacity_unit = "bytes"

  target = {
    format = {
      type = "qcow2"
    }
  }

  create = {
    content = {
      url = var.system_image_path
    }
  }
}

resource "libvirt_volume" "cidata" {
  name = "${var.hostname}-cidata.iso"
  pool = var.pool

  target = {
    format = {
      type = "raw"
    }
  }

  create = {
    content = {
      url = var.cidata_image_path
    }
  }
}

resource "libvirt_domain" "this" {
  name        = var.hostname
  description = "Terraform-managed Talos GPU worker"
  type        = "kvm"
  memory      = var.memory_mb
  memory_unit = "MiB"
  vcpu        = var.cores
  autostart   = true
  running     = true

  cpu = {
    mode       = "host-passthrough"
    check      = "none"
    migratable = false
    topology = {
      sockets = 1
      dies    = 1
      cores   = var.cores
      threads = 1
    }
  }

  # The normal Talos Image Factory raw image is UEFI-capable but is not the
  # separate SecureBoot artifact. Pin the non-Microsoft OVMF firmware so
  # libvirt cannot silently select Secure Boot as virt-manager did.
  os = {
    type            = "hvm"
    type_arch       = "x86_64"
    type_machine    = "q35"
    firmware        = "efi"
    loader          = var.ovmf_code_path
    loader_format   = "raw"
    loader_readonly = "yes"
    loader_secure   = "no"
    loader_type     = "pflash"
    nv_ram = {
      nv_ram          = "/var/lib/libvirt/qemu/nvram/${var.hostname}_VARS.fd"
      format          = "raw"
      template        = var.ovmf_vars_path
      template_format = "raw"
    }
    boot_devices = [{
      dev = "hd"
    }]
  }

  features = {
    acpi = true
    apic = {}
  }

  devices = {
    disks = [
      {
        device = "disk"
        driver = {
          name    = "qemu"
          type    = "qcow2"
          discard = "unmap"
        }
        source = {
          volume = {
            pool   = var.pool
            volume = libvirt_volume.system.name
          }
        }
        target = {
          dev = "vda"
          bus = "virtio"
        }
        boot = {
          order = 1
        }
      },
      {
        device    = "cdrom"
        read_only = true
        driver = {
          name = "qemu"
          type = "raw"
        }
        source = {
          volume = {
            pool   = var.pool
            volume = libvirt_volume.cidata.name
          }
        }
        target = {
          dev = "sda"
          bus = "sata"
        }
      },
    ]

    interfaces = [{
      mac = {
        address = var.mac_address
      }
      model = {
        type = "virtio"
      }
      source = {
        bridge = {
          bridge = var.bridge
        }
      }
    }]

    hostdevs = [
      for device in var.pci_devices : {
        managed = true
        subsys_pci = {
          source = {
            address = {
              domain   = device.domain
              bus      = device.bus
              slot     = device.slot
              function = device.function
            }
          }
        }
      }
    ]
  }

  depends_on = [
    libvirt_volume.system,
    libvirt_volume.cidata,
  ]
}

output "name" {
  value = libvirt_domain.this.name
}
