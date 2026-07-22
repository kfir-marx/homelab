# ──────────────────────────────────────────────────────────────────────────────
# Proxmox connection
# ──────────────────────────────────────────────────────────────────────────────

variable "proxmox_api_url" {
  description = "Proxmox VE API endpoint (e.g. https://192.168.1.105:8006)"
  type        = string
}

variable "proxmox_api_token" {
  description = "Proxmox API token in 'user@realm!tokenid=secret' format"
  type        = string
  sensitive   = true
}

variable "proxmox_ssh_password" {
  description = "Proxmox root SSH password for disk import operations"
  type        = string
  sensitive   = true
  default     = ""
}

# ──────────────────────────────────────────────────────────────────────────────
# Proxmox node management IPs (for host-level configuration via SSH)
# ──────────────────────────────────────────────────────────────────────────────

variable "proxmox_node_ips" {
  description = "Map of Proxmox node names to their management IPs (enables routing setup)"
  type        = map(string)
  default     = {}
}

# ──────────────────────────────────────────────────────────────────────────────
# Cluster-wide settings
# ──────────────────────────────────────────────────────────────────────────────

variable "cluster_name" {
  description = "Talos / Kubernetes cluster name"
  type        = string
}

variable "cluster_endpoint" {
  description = "Kubernetes API endpoint (VIP or load-balancer address)"
  type        = string
}

variable "cluster_vip" {
  description = "Virtual IP for the control-plane (Talos built-in VIP)"
  type        = string
}

variable "talos_version" {
  description = "Talos Linux version to deploy"
  type        = string
  default     = "v1.9.5"
}

# ──────────────────────────────────────────────────────────────────────────────
# Talos image
# ──────────────────────────────────────────────────────────────────────────────

variable "talos_schematic_id" {
  description = "Talos Image Factory schematic ID (default = vanilla, no extensions)"
  type        = string
  default     = "376567988ad370138ad8b2698212367b8edcb69b5fd68c80be1f2ec7d603b4ba"
}

variable "talos_image_datastore" {
  description = "Proxmox datastore to download the Talos image to"
  type        = string
  default     = "local"
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
}

variable "network_nameservers" {
  description = "DNS nameservers"
  type        = list(string)
  default     = ["1.1.1.1", "8.8.8.8"]
}

# ──────────────────────────────────────────────────────────────────────────────
# ArgoCD / GitOps
# ──────────────────────────────────────────────────────────────────────────────

variable "argocd_repo_url" {
  description = "Git repository URL for ArgoCD to watch"
  type        = string
}

variable "argocd_target_revision" {
  description = "Git branch/tag for ArgoCD to track"
  type        = string
  default     = "main"
}

variable "argocd_app_path" {
  description = "Path within the repo where Kubernetes manifests live"
  type        = string
  default     = "kubernetes/apps"
}

# ──────────────────────────────────────────────────────────────────────────────
# Node definitions
# ──────────────────────────────────────────────────────────────────────────────

variable "control_plane_nodes" {
  description = "Control-plane node specs"
  type = map(object({
    proxmox_node = string
    vm_id        = number
    ip_address   = string
    cores        = number
    memory_mb    = number
    disk_size_gb = number
  }))
}

variable "worker_nodes" {
  description = "Pure-compute worker node specs"
  type = map(object({
    proxmox_node = string
    vm_id        = number
    ip_address   = string
    cores        = number
    memory_mb    = number
    disk_size_gb = number
  }))
  default = {}
}

variable "gpu_nodes" {
  description = "GPU worker node specs with PCIe passthrough devices"
  type = map(object({
    proxmox_node = string
    vm_id        = number
    ip_address   = string
    cores        = number
    memory_mb    = number
    disk_size_gb = number
    pci_devices = list(object({
      id   = string
      pcie = bool
    }))
  }))
  default = {}
}

# ──────────────────────────────────────────────────────────────────────────────
# Windows VMs — coexist with gpu_nodes on the same Proxmox host. Both have
# the GPU configured for passthrough; Proxmox enforces start-time exclusivity.
# Toggle which is running with `qm start/shutdown` (manual). Talos auto-starts
# on boot, Windows VMs do not (on_boot=false in the module).
#
# Two install modes per VM, picked by template_vm_id:
#   • template_vm_id = null → INSTALL mode: empty VM with Win11 ISO attached
#                              for a one-time install + apps + drivers.
#   • template_vm_id = NNNN → CLONE mode: clone a prebuilt template VM
#                              (fast — ~30s, all your apps already installed).
# ──────────────────────────────────────────────────────────────────────────────

variable "windows_vms" {
  description = "Windows VM specs. Both gpu_nodes (Talos) and windows_vms can target the same proxmox_node + GPU; only one runs at a time (Proxmox enforces at start)."
  type = map(object({
    proxmox_node   = string
    vm_id          = number
    cores          = number
    memory_mb      = number
    disk_size_gb   = number
    windows_iso    = string
    virtio_iso     = string
    template_vm_id = optional(number) # null = install from ISO, set = clone from template
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
