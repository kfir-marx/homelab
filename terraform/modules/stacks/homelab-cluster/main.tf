# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  main.tf — orchestrates Proxmox VMs, Talos bootstrap, and ArgoCD install  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ──────────────────────────────────────────────────────────────────────────────
# 0. Talos nocloud image — downloaded once per Proxmox host via the API
# ──────────────────────────────────────────────────────────────────────────────

locals {
  # Mutual exclusion between Talos GPU workers and Windows VMs on the same
  # physical Proxmox host: a single GPU can only be PCIe-passed-through to
  # one VM at a time, so when a Windows VM is defined for host X, the Talos
  # GPU node on host X is filtered out of every downstream resource. Flipping
  # the toggle in config.yml destroys one VM and creates the other in the
  # same apply.
  windows_hosts = toset([for w in var.windows_vms : w.proxmox_node])

  active_gpu_nodes = {
    for name, node in var.gpu_nodes : name => node
    if !contains(local.windows_hosts, node.proxmox_node)
  }

  all_proxmox_nodes = distinct(concat(
    [for n in var.control_plane_nodes : n.proxmox_node],
    [for n in var.worker_nodes : n.proxmox_node],
    [for n in local.active_gpu_nodes : n.proxmox_node],
  ))

  talos_image_url = "https://factory.talos.dev/image/${var.talos_schematic_id}/${var.talos_version}/nocloud-amd64.raw.xz"

  # Routing: derive subnet prefix and gateway host from the first CP node
  vm_subnet_prefix     = split("/", values(var.control_plane_nodes)[0].ip_address)[1]
  gateway_proxmox_node = values(var.control_plane_nodes)[0].proxmox_node
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
  for_each = local.active_gpu_nodes

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
# 1c. Windows VMs — share Proxmox hosts (and GPUs) with Talos GPU workers.
#     Defining an entry here causes the matching gpu_nodes entry to be
#     stripped via local.active_gpu_nodes, so the Talos VM is destroyed
#     before the Windows VM is created. Remove the entry to flip back.
# ──────────────────────────────────────────────────────────────────────────────

module "windows_vms" {
  source   = "./modules/proxmox-windows-vm"
  for_each = var.windows_vms

  hostname     = each.key
  proxmox_node = each.value.proxmox_node
  vm_id        = each.value.vm_id
  cores        = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_size_gb
  bridge       = var.network_bridge

  windows_iso = each.value.windows_iso
  virtio_iso  = each.value.virtio_iso

  pci_devices = each.value.pci_devices
  usb_devices = each.value.usb_devices
}

# ──────────────────────────────────────────────────────────────────────────────
# 1b. Proxmox host routing — enables IP forwarding and assigns the VM subnet
#     gateway IP so that machines outside the Proxmox bridge can reach VMs.
#     Only runs when proxmox_node_ips is populated.
# ──────────────────────────────────────────────────────────────────────────────

resource "terraform_data" "proxmox_ip_forwarding" {
  for_each = var.proxmox_node_ips

  triggers_replace = [each.value]

  provisioner "remote-exec" {
    connection {
      type     = "ssh"
      host     = each.value
      user     = "root"
      password = var.proxmox_ssh_password
    }

    inline = [
      "echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-ip-forward.conf",
      "sysctl -w net.ipv4.ip_forward=1 > /dev/null",
    ]
  }
}

resource "terraform_data" "proxmox_subnet_gateway" {
  count = length(var.proxmox_node_ips) > 0 ? 1 : 0

  triggers_replace = [var.network_gateway, var.network_bridge]

  provisioner "remote-exec" {
    connection {
      type     = "ssh"
      host     = var.proxmox_node_ips[local.gateway_proxmox_node]
      user     = "root"
      password = var.proxmox_ssh_password
    }

    inline = [
      "ip addr show dev ${var.network_bridge} | grep -q '${var.network_gateway}/' || ip addr add ${var.network_gateway}/${local.vm_subnet_prefix} dev ${var.network_bridge}",
      "printf 'auto ${var.network_bridge}:1\\niface ${var.network_bridge}:1 inet static\\n    address ${var.network_gateway}/${local.vm_subnet_prefix}\\n' > /etc/network/interfaces.d/vm-subnet-gw",
    ]
  }
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
    for name, node in local.active_gpu_nodes : name => {
      ip_address = node.ip_address
      gpu        = true
    }
  }

  depends_on = [
    module.control_plane_vms,
    module.worker_vms,
    module.gpu_vms,
    terraform_data.proxmox_subnet_gateway,
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
