terraform {
  required_version = ">= 1.7.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.105.0"
    }
  }
}

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
