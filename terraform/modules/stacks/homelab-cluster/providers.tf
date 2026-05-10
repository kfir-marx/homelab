terraform {
  required_version = ">= 1.7.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.105.0"
    }
    talos = {
      source  = "siderolabs/talos"
      version = "~> 0.7"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
  }
}

# ──────────────────────────────────────────────────────────────────────────────
# Proxmox provider — talks to the Proxmox VE API.
#
# Auth: username + password (root@pam), NOT API token. Reason:
# Proxmox 8.x refuses to let API tokens set raw `hostpci` config — even
# root-realm tokens with privsep=0 hit "only root can set 'hostpci0' config
# for non-mapped devices". Real-user auth bypasses that. The alternative
# (PCI Resource Mappings, then `mapping = "name"` in the hostpci block) is
# the longer-term cleaner fix; switch when ready.
# ──────────────────────────────────────────────────────────────────────────────
provider "proxmox" {
  endpoint = var.proxmox_api_url
  username = "root@pam"
  password = var.proxmox_ssh_password
  insecure = true

  ssh {
    agent    = true
    username = "root"
    password = var.proxmox_ssh_password
  }
}

# ──────────────────────────────────────────────────────────────────────────────
# Talos provider — generates machine configs and bootstraps the cluster.
# No explicit credentials needed; it uses the generated client config.
# ──────────────────────────────────────────────────────────────────────────────
provider "talos" {}

# ──────────────────────────────────────────────────────────────────────────────
# Helm provider — used only for the initial ArgoCD bootstrap.
# After ArgoCD is running, it manages all further Helm releases via GitOps.
# ──────────────────────────────────────────────────────────────────────────────
provider "helm" {
  kubernetes {
    host                   = module.talos_cluster.kubeconfig_host
    client_certificate     = base64decode(module.talos_cluster.kubeconfig_client_certificate)
    client_key             = base64decode(module.talos_cluster.kubeconfig_client_key)
    cluster_ca_certificate = base64decode(module.talos_cluster.kubeconfig_ca_certificate)
  }
}
