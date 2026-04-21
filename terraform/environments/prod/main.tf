# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  main.tf — orchestrates Proxmox VMs, Talos bootstrap, and ArgoCD install  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ──────────────────────────────────────────────────────────────────────────────
# 1. Proxmox VMs — one module call per node role
# ──────────────────────────────────────────────────────────────────────────────

module "control_plane_vms" {
  source   = "../../modules/proxmox-vm"
  for_each = var.control_plane_nodes

  hostname     = each.key
  proxmox_node = each.value.proxmox_node
  vm_id        = each.value.vm_id
  cores        = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_size_gb
  ip_address   = each.value.ip_address
  mac_address  = each.value.mac_address
  gateway      = var.network_gateway
  bridge       = var.network_bridge

  iso_datastore = var.talos_iso_datastore
  iso_file      = var.talos_iso_file

  pci_devices = [] # No GPU on control-plane nodes
}

module "worker_vms" {
  source   = "../../modules/proxmox-vm"
  for_each = var.worker_nodes

  hostname     = each.key
  proxmox_node = each.value.proxmox_node
  vm_id        = each.value.vm_id
  cores        = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_size_gb
  ip_address   = each.value.ip_address
  mac_address  = each.value.mac_address
  gateway      = var.network_gateway
  bridge       = var.network_bridge

  iso_datastore = var.talos_iso_datastore
  iso_file      = var.talos_iso_file

  pci_devices = []
}

module "gpu_vms" {
  source   = "../../modules/proxmox-vm"
  for_each = var.gpu_nodes

  hostname     = each.key
  proxmox_node = each.value.proxmox_node
  vm_id        = each.value.vm_id
  cores        = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_size_gb
  ip_address   = each.value.ip_address
  mac_address  = each.value.mac_address
  gateway      = var.network_gateway
  bridge       = var.network_bridge

  iso_datastore = var.talos_iso_datastore
  iso_file      = var.talos_iso_file

  pci_devices = each.value.pci_devices
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. Talos cluster — generates configs, applies them, bootstraps etcd
# ──────────────────────────────────────────────────────────────────────────────

module "talos_cluster" {
  source = "../../modules/talos-cluster"

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
  version          = "7.8.13" # Pin to a known-good version
  wait             = true
  timeout          = 600

  # Point ArgoCD at this repo so it can manage itself and all apps going forward.
  values = [
    yamlencode({
      configs = {
        cm = {
          "admin.enabled" = "true"
        }
        params = {
          "server.insecure" = true # Terminate TLS at ingress, not ArgoCD
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
#    This tells ArgoCD to watch kubernetes/ in this repo for all other apps.
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
            # TODO: Replace with your actual repo URL.
            repoURL        = "https://github.com/YOUR_USER/homelab.git"
            targetRevision = "main"
            path           = "kubernetes/apps"
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
