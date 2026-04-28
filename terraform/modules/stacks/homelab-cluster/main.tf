# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  main.tf — orchestrates Proxmox VMs, Talos bootstrap, and ArgoCD install  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ──────────────────────────────────────────────────────────────────────────────
# 0. Talos nocloud image — downloaded once per Proxmox host via the API
# ──────────────────────────────────────────────────────────────────────────────

locals {
  all_proxmox_nodes = distinct(concat(
    [for n in var.control_plane_nodes : n.proxmox_node],
    [for n in var.worker_nodes : n.proxmox_node],
    [for n in var.gpu_nodes : n.proxmox_node],
  ))

  talos_image_url = "https://factory.talos.dev/image/${var.talos_schematic_id}/${var.talos_version}/nocloud-amd64.raw.xz"
}

resource "proxmox_virtual_environment_download_file" "talos_image" {
  for_each = toset(local.all_proxmox_nodes)

  content_type = "iso"
  datastore_id = var.talos_image_datastore
  node_name    = each.value
  url          = local.talos_image_url
  file_name    = "talos-${var.talos_version}-nocloud-amd64.img"
  overwrite    = false
}

# ──────────────────────────────────────────────────────────────────────────────
# 1. Proxmox VMs — one module call per node role
# ──────────────────────────────────────────────────────────────────────────────

module "control_plane_vms" {
  source   = "./modules/proxmox-vm"
  for_each = var.control_plane_nodes

  hostname     = each.key
  proxmox_node = each.value.proxmox_node
  vm_id        = each.value.vm_id
  cores        = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_size_gb
  ip_address    = each.value.ip_address
  gateway       = var.network_gateway
  bridge        = var.network_bridge
  image_file_id = proxmox_virtual_environment_download_file.talos_image[each.value.proxmox_node].id

  pci_devices = []
}

module "worker_vms" {
  source   = "./modules/proxmox-vm"
  for_each = var.worker_nodes

  hostname      = each.key
  proxmox_node  = each.value.proxmox_node
  vm_id         = each.value.vm_id
  cores         = each.value.cores
  memory_mb     = each.value.memory_mb
  disk_size_gb  = each.value.disk_size_gb
  ip_address    = each.value.ip_address
  gateway       = var.network_gateway
  bridge        = var.network_bridge
  image_file_id = proxmox_virtual_environment_download_file.talos_image[each.value.proxmox_node].id

  pci_devices = []
}

module "gpu_vms" {
  source   = "./modules/proxmox-vm"
  for_each = var.gpu_nodes

  hostname      = each.key
  proxmox_node  = each.value.proxmox_node
  vm_id         = each.value.vm_id
  cores         = each.value.cores
  memory_mb     = each.value.memory_mb
  disk_size_gb  = each.value.disk_size_gb
  ip_address    = each.value.ip_address
  gateway       = var.network_gateway
  bridge        = var.network_bridge
  image_file_id = proxmox_virtual_environment_download_file.talos_image[each.value.proxmox_node].id

  pci_devices = each.value.pci_devices
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. Talos cluster — generates configs, applies them, bootstraps etcd
# ──────────────────────────────────────────────────────────────────────────────

module "talos_cluster" {
  source = "./modules/talos-cluster"

  cluster_name     = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  cluster_vip      = var.cluster_vip
  talos_version    = var.talos_version
  nameservers      = var.network_nameservers

  control_plane_nodes = {
    for name, node in var.control_plane_nodes : name => {
      ip_address = node.ip_address
    }
  }

  worker_nodes = {
    for name, node in var.worker_nodes : name => {
      ip_address = node.ip_address
      gpu        = false
    }
  }

  gpu_worker_nodes = {
    for name, node in var.gpu_nodes : name => {
      ip_address = node.ip_address
      gpu        = true
    }
  }

  depends_on = [
    module.control_plane_vms,
    module.worker_vms,
    module.gpu_vms,
  ]
}

# ──────────────────────────────────────────────────────────────────────────────
# 3. ArgoCD bootstrap — one-time Helm install, then ArgoCD manages itself
# ──────────────────────────────────────────────────────────────────────────────

resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "7.8.13"
  wait             = true
  timeout          = 600

  values = [
    yamlencode({
      configs = {
        cm = {
          "admin.enabled" = "true"
        }
        params = {
          "server.insecure" = true
        }
      }
      server = {
        service = {
          type = "LoadBalancer"
        }
      }
    })
  ]

  depends_on = [module.talos_cluster]
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. ArgoCD root Application — the "app of apps" pattern
# ──────────────────────────────────────────────────────────────────────────────

resource "helm_release" "argocd_root_app" {
  name       = "root-app"
  namespace  = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "2.0.2"

  values = [
    yamlencode({
      applications = {
        root = {
          namespace = "argocd"
          project   = "default"
          source = {
            repoURL        = var.argocd_repo_url
            targetRevision = var.argocd_target_revision
            path           = var.argocd_app_path
          }
          destination = {
            server    = "https://kubernetes.default.svc"
            namespace = "argocd"
          }
          syncPolicy = {
            automated = {
              prune    = true
              selfHeal = true
            }
          }
        }
      }
    })
  ]

  depends_on = [helm_release.argocd]
}

# ──────────────────────────────────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────────────────────────────────

output "talosconfig" {
  description = "Talos client config (write to ~/.talos/config)"
  value       = module.talos_cluster.talosconfig
  sensitive   = true
}

output "kubeconfig" {
  description = "Kubeconfig for the cluster"
  value       = module.talos_cluster.kubeconfig
  sensitive   = true
}
