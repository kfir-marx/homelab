variable "proxmox_api_url" {
  description = "Proxmox API endpoint"
  type        = string
}

variable "proxmox_api_token" {
  description = "Proxmox API token retained for the shared deployment interface"
  type        = string
  sensitive   = true
}

variable "proxmox_ssh_password" {
  description = "Root password used for Proxmox operations that require PAM authentication"
  type        = string
  sensitive   = true
}

variable "network_bridge" {
  description = "Proxmox bridge used by the workstation"
  type        = string
}

variable "windows_vms" {
  description = "Windows workstation VMs, independently managed from the Talos cluster"
  type = map(object({
    proxmox_node   = string
    vm_id          = number
    cores          = number
    memory_mb      = number
    disk_size_gb   = number
    windows_iso    = string
    virtio_iso     = string
    template_vm_id = optional(number)
    full_clone     = optional(bool, true)
    pci_devices = list(object({
      id   = string
      pcie = bool
    }))
    usb_devices = list(object({
      host = string
      usb3 = bool
    }))
  }))
  default = {}
}
