# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  main.tf — orchestrates Proxmox VMs, Talos bootstrap, and ArgoCD install  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ──────────────────────────────────────────────────────────────────────────────
# 0. Talos Image Factory profiles and per-host image downloads
# ──────────────────────────────────────────────────────────────────────────────

locals {
  # The Talos GPU worker and separately-managed Windows workstation use the
  # same GPU on largegpu. Proxmox enforces exclusivity at start time:
  #   `qm shutdown 402 && qm start 502`   (Talos → Windows)
  #   `qm shutdown 502 && qm start 402`   (Windows → Talos)
  base_image_nodes = distinct(concat(
    [for n in var.control_plane_nodes : n.proxmox_node],
    [for n in var.worker_nodes : n.proxmox_node],
  ))
  gpu_image_nodes = distinct(
    [for n in var.gpu_nodes : n.proxmox_node],
  )

  # A host can need both profiles (for example, smallgpu runs a control-plane
  # VM and a GPU worker), so the key includes both node and profile.
  talos_image_targets = merge(
    {
      for node in local.base_image_nodes : "${node}/base" => {
        node    = node
        profile = "base"
      }
    },
    {
      for node in local.gpu_image_nodes : "${node}/gpu" => {
        node    = node
        profile = "gpu"
      }
    },
  )

  cilium_lb_start_octets = [for octet in split(".", var.cilium_load_balancer_ip_start) : parseint(octet, 10)]
  cilium_lb_stop_octets  = [for octet in split(".", var.cilium_load_balancer_ip_stop) : parseint(octet, 10)]
  cilium_lb_start_number = sum([for index, octet in local.cilium_lb_start_octets : octet * pow(256, 3 - index)])
  cilium_lb_stop_number  = sum([for index, octet in local.cilium_lb_stop_octets : octet * pow(256, 3 - index)])
  cluster_lan_prefix     = join(".", slice(split(".", var.cluster_vip), 0, 3))
  talos_node_ips = concat(
    [for n in var.control_plane_nodes : split("/", n.ip_address)[0]],
    [for n in var.worker_nodes : split("/", n.ip_address)[0]],
    [for n in var.gpu_nodes : split("/", n.ip_address)[0]],
  )
}

resource "talos_image_factory_schematic" "this" {
  for_each = fileset("${path.module}/talos-images", "*.yaml")

  schematic = file("${path.module}/talos-images/${each.value}")
}

resource "proxmox_download_file" "talos_image" {
  for_each = local.talos_image_targets

  content_type            = "iso"
  datastore_id            = var.talos_image_datastore
  node_name               = each.value.node
  url                     = "https://factory.talos.dev/image/${talos_image_factory_schematic.this["${each.value.profile}.yaml"].id}/${var.talos_version}/nocloud-amd64.raw.zst"
  file_name               = "talos-${var.talos_version}-${each.value.profile}-${substr(talos_image_factory_schematic.this["${each.value.profile}.yaml"].id, 0, 12)}-nocloud-amd64.img"
  decompression_algorithm = "zst" # required — file_name has no .zst suffix
  overwrite               = false
}

# ──────────────────────────────────────────────────────────────────────────────
# 1. Proxmox VMs — one module call per node role
# ──────────────────────────────────────────────────────────────────────────────

module "control_plane_vms" {
  source   = "./modules/proxmox-vm"
  for_each = var.control_plane_nodes

  hostname      = each.key
  proxmox_node  = each.value.proxmox_node
  vm_id         = each.value.vm_id
  cores         = each.value.cores
  memory_mb     = each.value.memory_mb
  disk_size_gb  = each.value.disk_size_gb
  ip_address    = each.value.ip_address
  gateway       = var.network_gateway
  bridge        = var.network_bridge
  image_file_id = proxmox_download_file.talos_image["${each.value.proxmox_node}/base"].id

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
  image_file_id = proxmox_download_file.talos_image["${each.value.proxmox_node}/base"].id

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
  image_file_id = proxmox_download_file.talos_image["${each.value.proxmox_node}/gpu"].id

  pci_devices = each.value.pci_devices
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. Talos cluster — generates configs, applies them, bootstraps etcd
# ──────────────────────────────────────────────────────────────────────────────

module "talos_cluster" {
  source = "./modules/talos-cluster"

  cluster_name       = var.cluster_name
  cluster_endpoint   = var.cluster_endpoint
  cluster_vip        = var.cluster_vip
  talos_version      = var.talos_version
  kubernetes_version = var.kubernetes_version
  nameservers        = var.network_nameservers

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
      dedicated  = node.dedicated
    }
  }

  depends_on = [
    module.control_plane_vms,
    module.worker_vms,
    module.gpu_vms,
  ]
}

# ──────────────────────────────────────────────────────────────────────────────
# 3. Cilium bootstrap — pod networking must exist before cluster health
# ──────────────────────────────────────────────────────────────────────────────

# Retrieving kubeconfig only proves that Talos completed bootstrap. On a fresh
# build the control plane can reboot immediately afterward, leaving the API VIP
# temporarily unroutable. Wait for the API socket before invoking the Helm
# provider so a single apply converges instead of racing that reboot.
resource "terraform_data" "kubernetes_api_ready" {
  triggers_replace = [
    sha256(module.talos_cluster.kubeconfig),
  ]

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      for attempt in $(seq 1 150); do
        if timeout 2 bash -c "</dev/tcp/${var.cluster_vip}/6443" 2>/dev/null; then
          exit 0
        fi
        sleep 2
      done

      echo "Kubernetes API ${var.cluster_vip}:6443 did not become reachable within 5 minutes" >&2
      exit 1
    EOT
  }

  depends_on = [module.talos_cluster]
}

resource "helm_release" "cilium" {
  name       = "cilium"
  namespace  = "kube-system"
  repository = "https://helm.cilium.io"
  chart      = "cilium"
  version    = var.cilium_version

  atomic          = true
  cleanup_on_fail = true
  wait            = true
  wait_for_jobs   = true
  timeout         = 900

  values = [
    yamlencode({
      ipam = {
        mode = "kubernetes"
      }
      kubeProxyReplacement = true
      k8sServiceHost       = var.cluster_vip
      k8sServicePort       = 6443
      cgroup = {
        autoMount = {
          enabled = false
        }
        hostRoot = "/sys/fs/cgroup"
      }
      securityContext = {
        capabilities = {
          ciliumAgent = [
            "CHOWN",
            "KILL",
            "NET_ADMIN",
            "NET_RAW",
            "IPC_LOCK",
            "SYS_ADMIN",
            "SYS_RESOURCE",
            "DAC_OVERRIDE",
            "FOWNER",
            "SETGID",
            "SETUID",
          ]
          cleanCiliumState = [
            "NET_ADMIN",
            "SYS_ADMIN",
            "SYS_RESOURCE",
          ]
        }
      }
      devices = ["eth0"]
      l2announcements = {
        enabled = true
      }
      k8sClientRateLimit = {
        qps   = 10
        burst = 20
      }
      operator = {
        replicas = 2
      }
      rollOutCiliumPods = true
    })
  ]

  depends_on = [terraform_data.kubernetes_api_ready]
}

resource "helm_release" "cilium_l2_config" {
  name      = "cilium-l2-config"
  namespace = "kube-system"
  chart     = "${path.module}/helm/cilium-l2-config"

  atomic  = true
  wait    = true
  timeout = 300

  values = [
    yamlencode({
      loadBalancerPool = {
        start = var.cilium_load_balancer_ip_start
        stop  = var.cilium_load_balancer_ip_stop
      }
    })
  ]

  depends_on = [helm_release.cilium]

  lifecycle {
    precondition {
      condition = (
        join(".", slice(split(".", var.cilium_load_balancer_ip_start), 0, 3)) == local.cluster_lan_prefix &&
        join(".", slice(split(".", var.cilium_load_balancer_ip_stop), 0, 3)) == local.cluster_lan_prefix
      )
      error_message = "The Cilium LoadBalancer pool must be inside the cluster VIP's /24 LAN."
    }

    precondition {
      condition     = local.cilium_lb_start_number < local.cilium_lb_stop_number
      error_message = "The Cilium LoadBalancer pool must contain at least two addresses in ascending order."
    }

    precondition {
      condition = alltrue([
        for address in concat([var.cluster_vip], local.talos_node_ips) :
        !(
          sum([for index, octet in [for part in split(".", address) : parseint(part, 10)] : octet * pow(256, 3 - index)]) >= local.cilium_lb_start_number &&
          sum([for index, octet in [for part in split(".", address) : parseint(part, 10)] : octet * pow(256, 3 - index)]) <= local.cilium_lb_stop_number
        )
      ])
      error_message = "The Cilium LoadBalancer pool must not overlap the Kubernetes API VIP or any Talos node address."
    }
  }
}

resource "helm_release" "cluster_readiness" {
  name      = "cluster-readiness"
  namespace = "kube-system"
  chart     = "${path.module}/helm/cluster-readiness"

  atomic          = true
  cleanup_on_fail = true
  wait            = true
  wait_for_jobs   = true
  timeout         = 900

  values = [
    yamlencode({
      kubectlImage = "registry.k8s.io/kubectl:v${var.kubernetes_version}"
      generation = substr(sha256(jsonencode({
        cilium_version = var.cilium_version
        pool_start     = var.cilium_load_balancer_ip_start
        pool_stop      = var.cilium_load_balancer_ip_stop
        nodes = {
          control_plane = var.control_plane_nodes
          workers       = var.worker_nodes
          gpu_workers   = var.gpu_nodes
        }
      })), 0, 12)
    })
  ]

  depends_on = [
    helm_release.cilium,
    helm_release.cilium_l2_config,
  ]
}

# The kubeconfig is available before nodes are Kubernetes Ready when Talos has
# no built-in CNI. Only run the full Talos/Kubernetes health check after Cilium
# and an explicit Kubernetes readiness job have succeeded. Talos intentionally
# skips NodeReady and CoreDNS checks when its configured CNI name is "none".
data "talos_cluster_health" "this" {
  client_configuration = module.talos_cluster.client_configuration
  endpoints            = [for n in var.control_plane_nodes : split("/", n.ip_address)[0]]
  control_plane_nodes  = [for n in var.control_plane_nodes : split("/", n.ip_address)[0]]
  worker_nodes = concat(
    [for n in var.worker_nodes : split("/", n.ip_address)[0]],
    [for n in var.gpu_nodes : split("/", n.ip_address)[0]],
  )

  timeouts = {
    read = "20m"
  }

  depends_on = [
    helm_release.cluster_readiness,
  ]
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. ArgoCD bootstrap — one-time Helm install, then ArgoCD manages itself
# ──────────────────────────────────────────────────────────────────────────────

resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "7.8.13"
  atomic           = true
  cleanup_on_fail  = true
  wait             = true
  wait_for_jobs    = true
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
          annotations = {
            "lbipam.cilium.io/ips" = var.cilium_load_balancer_ip_start
          }
        }
      }
    })
  ]

  depends_on = [data.talos_cluster_health.this]
}

# ──────────────────────────────────────────────────────────────────────────────
# 5. ArgoCD root Application — the "app of apps" pattern
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
