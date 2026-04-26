terraform {
  required_version = ">= 1.7.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.78"
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
# Create an API token in Datacenter → Permissions → API Tokens.
# The token needs PVEVMAdmin + PVEDatastoreUser on /.
# ──────────────────────────────────────────────────────────────────────────────
provider "proxmox" {
  endpoint  = var.proxmox_api_url
  api_token = var.proxmox_api_token
  insecure  = true

  ssh {
    agent = true
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
