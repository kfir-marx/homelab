# ──────────────────────────────────────────────────────────────────────────────
# Proxmox connection
# ──────────────────────────────────────────────────────────────────────────────

variable "proxmox_api_url" {
  description = "Proxmox VE API endpoint (e.g. https://pve1.home.lab:8006)"
  type        = string
}

variable "proxmox_api_token" {
  description = "Proxmox API token in 'user@realm!tokenid=secret' format"
  type        = string
  sensitive   = true
}

# ──────────────────────────────────────────────────────────────────────────────
# Cluster-wide settings
# ──────────────────────────────────────────────────────────────────────────────

variable "cluster_name" {
  description = "Talos / Kubernetes cluster name"
  type        = string
  default     = "homelab"
}

variable "cluster_endpoint" {
  description = "Kubernetes API endpoint (VIP or load-balancer address)"
  type        = string
  default     = "https://10.0.10.100:6443"
}

variable "cluster_vip" {
  description = "Virtual IP for the control-plane (Talos built-in VIP)"
  type        = string
  default     = "10.0.10.100"
}

variable "talos_version" {
  description = "Talos Linux version to deploy"
  type        = string
  default     = "v1.9.5"
}

# ──────────────────────────────────────────────────────────────────────────────
# ISO / image
# ──────────────────────────────────────────────────────────────────────────────

variable "talos_iso_datastore" {
  description = "Proxmox datastore where the Talos ISO is uploaded"
  type        = string
  default     = "local"
}

variable "talos_iso_file" {
  description = "Filename of the Talos ISO on the datastore"
  type        = string
  default     = "talos-amd64.iso"
}

# ──────────────────────────────────────────────────────────────────────────────
# Network
# ──────────────────────────────────────────────────────────────────────────────

variable "network_bridge" {
  description = "Proxmox network bridge for VM NICs"
  type        = string
  default     = "vmbr0"
}

variable "network_gateway" {
  description = "Default gateway for the VM network"
  type        = string
  default     = "10.0.10.1"
}

variable "network_nameservers" {
  description = "DNS nameservers"
  type        = list(string)
  default     = ["1.1.1.1", "8.8.8.8"]
}

# ──────────────────────────────────────────────────────────────────────────────
# Node definitions — each map key is the hostname.
#
# Compute nodes: 4 pure-compute nodes (control-plane or workers).
# GPU nodes:     1 large-storage/strong-GPU, 1 mid-storage/mid-GPU.
#
# Fill in your real Proxmox node names, MAC addresses, IP addresses,
# and PCI device IDs from `lspci -nn | grep -i nvidia` on each host.
# ──────────────────────────────────────────────────────────────────────────────

variable "control_plane_nodes" {
  description = "Control-plane node specs (run on the pure-compute Proxmox hosts)"
  type = map(object({
    proxmox_node = string # Proxmox host to schedule the VM on
    vm_id        = number
    ip_address   = string # static IP in CIDR (e.g. 10.0.10.11/24)
    mac_address  = string # fixed MAC for DHCP reservations / PXE
    cores        = number
    memory_mb    = number # RAM in MiB
    disk_size_gb = number
  }))
  default = {
    "cp-1" = {
      proxmox_node = "pve1"
      vm_id        = 201
      ip_address   = "10.0.10.11/24"
      mac_address  = "BC:24:11:AA:BB:01"
      cores        = 4
      memory_mb    = 8192
      disk_size_gb = 50
    }
    "cp-2" = {
      proxmox_node = "pve2"
      vm_id        = 202
      ip_address   = "10.0.10.12/24"
      mac_address  = "BC:24:11:AA:BB:02"
      cores        = 4
      memory_mb    = 8192
      disk_size_gb = 50
    }
    "cp-3" = {
      proxmox_node = "pve3"
      vm_id        = 203
      ip_address   = "10.0.10.13/24"
      mac_address  = "BC:24:11:AA:BB:03"
      cores        = 4
      memory_mb    = 8192
      disk_size_gb = 50
    }
  }
}

variable "worker_nodes" {
  description = "Pure-compute worker node specs"
  type = map(object({
    proxmox_node = string
    vm_id        = number
    ip_address   = string
    mac_address  = string
    cores        = number
    memory_mb    = number
    disk_size_gb = number
  }))
  default = {
    "worker-1" = {
      proxmox_node = "pve4"
      vm_id        = 301
      ip_address   = "10.0.10.21/24"
      mac_address  = "BC:24:11:AA:CC:01"
      cores        = 8
      memory_mb    = 16384
      disk_size_gb = 100
    }
  }
}

variable "gpu_nodes" {
  description = <<-EOT
    GPU worker node specs. Each entry includes PCIe device IDs for passthrough.
    Run `lspci -nn | grep -i nvidia` on each Proxmox host to find the IDs.
    Format is "BUS:DEVICE.FUNCTION" (e.g. "01:00").
  EOT
  type = map(object({
    proxmox_node = string
    vm_id        = number
    ip_address   = string
    mac_address  = string
    cores        = number
    memory_mb    = number
    disk_size_gb = number
    pci_devices = list(object({
      id   = string # PCI address, e.g. "01:00" passthrough both .0 and .1
      pcie = bool   # true for PCIe passthrough (vs legacy PCI)
    }))
  }))
  default = {
    # Large-storage node with a strong GPU (e.g. RTX 4090)
    "gpu-large" = {
      proxmox_node = "pve5"
      vm_id        = 401
      ip_address   = "10.0.10.31/24"
      mac_address  = "BC:24:11:AA:DD:01"
      cores        = 16
      memory_mb    = 65536
      disk_size_gb = 500
      pci_devices = [
        { id = "01:00", pcie = true }, # GPU
        { id = "01:00", pcie = true }, # GPU audio (if separate function)
      ]
    }
    # Mid-storage node with a mid-range GPU (e.g. RTX 3060)
    "gpu-mid" = {
      proxmox_node = "pve6"
      vm_id        = 402
      ip_address   = "10.0.10.32/24"
      mac_address  = "BC:24:11:AA:DD:02"
      cores        = 8
      memory_mb    = 32768
      disk_size_gb = 250
      pci_devices = [
        { id = "41:00", pcie = true },
      ]
    }
  }
}
