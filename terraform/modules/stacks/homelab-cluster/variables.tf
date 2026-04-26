# ──────────────────────────────────────────────────────────────────────────────
# Proxmox connection
# ──────────────────────────────────────────────────────────────────────────────

variable "proxmox_api_url" {
  description = "Proxmox VE API endpoint (e.g. https://192.168.1.101:8006)"
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
    mac_address  = string
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
    mac_address  = string
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
    mac_address  = string
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
