# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Module: talos-cluster                                                     ║
# ║  Generates Talos machine configs, applies them to nodes, and bootstraps.   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

terraform {
  required_providers {
    talos = {
      source  = "siderolabs/talos"
      version = "~> 0.7"
    }
  }
}

# ──────────────────────────────────────────────────────────────────────────────
# Variables
# ──────────────────────────────────────────────────────────────────────────────

variable "cluster_name" {
  type = string
}

variable "cluster_endpoint" {
  type = string
}

variable "cluster_vip" {
  type = string
}

variable "talos_version" {
  type    = string
  default = "v1.9.5"
}

variable "nameservers" {
  type    = list(string)
  default = ["1.1.1.1"]
}

variable "control_plane_nodes" {
  type = map(object({
    ip_address = string
  }))
}

variable "worker_nodes" {
  type = map(object({
    ip_address = string
    gpu        = bool
  }))
  default = {}
}

variable "gpu_worker_nodes" {
  type = map(object({
    ip_address = string
    gpu        = bool
  }))
  default = {}
}

# ──────────────────────────────────────────────────────────────────────────────
# Secrets — cluster-wide PKI material (etcd CA, Kubernetes CA, etc.)
# Generated once and stored in state. Treat your state file as secret.
# ──────────────────────────────────────────────────────────────────────────────

resource "talos_machine_secrets" "this" {}

# ──────────────────────────────────────────────────────────────────────────────
# Machine configuration data sources
# Talos generates a base config from the cluster name + endpoint + secrets,
# then we layer on patches per-role.
# ──────────────────────────────────────────────────────────────────────────────

data "talos_client_configuration" "this" {
  cluster_name         = var.cluster_name
  client_configuration = talos_machine_secrets.this.client_configuration
  endpoints            = [for n in var.control_plane_nodes : split("/", n.ip_address)[0]]
}

# --- Control-plane config (per node) ---

data "talos_machine_configuration" "controlplane" {
  for_each = var.control_plane_nodes

  cluster_name     = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_type     = "controlplane"
  machine_secrets  = talos_machine_secrets.this.machine_secrets
  talos_version    = var.talos_version

  config_patches = [
    # Hostname + network
    yamlencode({
      machine = {
        network = {
          hostname    = each.key
          nameservers = var.nameservers
          interfaces = [{
            interface = "eth0"
            dhcp      = false
            addresses = [each.value.ip_address]
            routes = [{
              network = "0.0.0.0/0"
              gateway = cidrhost(each.value.ip_address, 1)
            }]
            vip = {
              ip = var.cluster_vip
            }
          }]
        }
      }
    }),
  ]
}

# --- Worker config (non-GPU) ---

data "talos_machine_configuration" "worker" {
  for_each = var.worker_nodes

  cluster_name     = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_type     = "worker"
  machine_secrets  = talos_machine_secrets.this.machine_secrets
  talos_version    = var.talos_version

  config_patches = [
    yamlencode({
      machine = {
        network = {
          hostname    = each.key
          nameservers = var.nameservers
          interfaces = [{
            interface = "eth0"
            dhcp      = false
            addresses = [each.value.ip_address]
            routes = [{
              network = "0.0.0.0/0"
              gateway = cidrhost(each.value.ip_address, 1)
            }]
          }]
        }
      }
    }),
  ]
}

# --- GPU worker config (with NVIDIA extensions + labels/taints) ---

data "talos_machine_configuration" "gpu_worker" {
  for_each = var.gpu_worker_nodes

  cluster_name     = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_type     = "worker"
  machine_secrets  = talos_machine_secrets.this.machine_secrets
  talos_version    = var.talos_version

  config_patches = [
    # Network configuration
    yamlencode({
      machine = {
        network = {
          hostname    = each.key
          nameservers = var.nameservers
          interfaces = [{
            interface = "eth0"
            dhcp      = false
            addresses = [each.value.ip_address]
            routes = [{
              network = "0.0.0.0/0"
              gateway = cidrhost(each.value.ip_address, 1)
            }]
          }]
        }
      }
    }),

    # GPU-specific patch — loaded from the YAML file next to this module.
    file("${path.module}/talos-gpu-patch.yaml"),

    # Node labels and taints so GPU workloads land only here.
    yamlencode({
      machine = {
        nodeLabels = {
          "node.kubernetes.io/gpu"      = "true"
          "nvidia.com/gpu.present"      = "true"
          "homelab.dev/role"            = "gpu-worker"
          "homelab.dev/gpu-node"        = each.key
        }
        nodeTaints = {
          "nvidia.com/gpu" = "true:NoSchedule"
        }
      }
    }),
  ]
}

# ──────────────────────────────────────────────────────────────────────────────
# Apply configs to nodes
# ──────────────────────────────────────────────────────────────────────────────

resource "talos_machine_configuration_apply" "controlplane" {
  for_each = var.control_plane_nodes

  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.controlplane[each.key].machine_configuration
  endpoint                    = split("/", each.value.ip_address)[0]
  node                        = split("/", each.value.ip_address)[0]
}

resource "talos_machine_configuration_apply" "worker" {
  for_each = var.worker_nodes

  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.worker[each.key].machine_configuration
  endpoint                    = split("/", each.value.ip_address)[0]
  node                        = split("/", each.value.ip_address)[0]
}

resource "talos_machine_configuration_apply" "gpu_worker" {
  for_each = var.gpu_worker_nodes

  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.gpu_worker[each.key].machine_configuration
  endpoint                    = split("/", each.value.ip_address)[0]
  node                        = split("/", each.value.ip_address)[0]
}

# ──────────────────────────────────────────────────────────────────────────────
# Bootstrap — run once on the first control-plane node to initialize etcd.
# ──────────────────────────────────────────────────────────────────────────────

resource "talos_machine_bootstrap" "this" {
  client_configuration = talos_machine_secrets.this.client_configuration
  endpoint             = split("/", values(var.control_plane_nodes)[0].ip_address)[0]
  node                 = split("/", values(var.control_plane_nodes)[0].ip_address)[0]

  depends_on = [talos_machine_configuration_apply.controlplane]
}

# ──────────────────────────────────────────────────────────────────────────────
# Kubeconfig — retrieve after bootstrap completes
# ──────────────────────────────────────────────────────────────────────────────

data "talos_cluster_kubeconfig" "this" {
  client_configuration = talos_machine_secrets.this.client_configuration
  endpoint             = split("/", values(var.control_plane_nodes)[0].ip_address)[0]
  node                 = split("/", values(var.control_plane_nodes)[0].ip_address)[0]

  depends_on = [talos_machine_bootstrap.this]
}

# ──────────────────────────────────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────────────────────────────────

output "talosconfig" {
  value     = data.talos_client_configuration.this.talos_config
  sensitive = true
}

output "kubeconfig" {
  value     = data.talos_cluster_kubeconfig.this.kubeconfig_raw
  sensitive = true
}

output "kubeconfig_host" {
  value = data.talos_cluster_kubeconfig.this.kubernetes_client_configuration.host
}

output "kubeconfig_client_certificate" {
  value     = data.talos_cluster_kubeconfig.this.kubernetes_client_configuration.client_certificate
  sensitive = true
}

output "kubeconfig_client_key" {
  value     = data.talos_cluster_kubeconfig.this.kubernetes_client_configuration.client_key
  sensitive = true
}

output "kubeconfig_ca_certificate" {
  value     = data.talos_cluster_kubeconfig.this.kubernetes_client_configuration.ca_certificate
  sensitive = true
}
