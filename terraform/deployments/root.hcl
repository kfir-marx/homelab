# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  root.hcl — central Terragrunt configuration for all deployments          ║
# ║                                                                           ║
# ║  Every stack's terragrunt.hcl includes this via:                          ║
# ║    include "root" { path = find_in_parent_folders("root.hcl") }           ║
# ║                                                                           ║
# ║  Config hierarchy (deep-merged, later overrides earlier):                 ║
# ║    deployments/config.yml          → global defaults                      ║
# ║    deployments/<env>/config.yml    → environment overrides                ║
# ║    deployments/<env>/<stack>/config.yml → stack overrides (optional)      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

locals {
  root_deployments_dir       = get_parent_terragrunt_dir()
  relative_deployment_path   = path_relative_to_include()
  deployment_path_components = compact(split("/", local.relative_deployment_path))

  # Auto-detect stack name from the deepest directory in the deployment path
  stack = reverse(local.deployment_path_components)[0]

  # Merge hierarchical YAML configs (global → environment → stack)
  merged_config = jsondecode(
    run_cmd("bash", "${local.root_deployments_dir}/merge_configs.sh",
      local.root_deployments_dir,
      "${local.root_deployments_dir}/${local.relative_deployment_path}"
    )
  )
}

# Pass merged YAML config as Terraform input variables.
# Secrets are injected from environment variables — never stored in YAML.
inputs = merge(local.merged_config, {
  proxmox_api_token = get_env("PROXMOX_API_TOKEN")
})

# ──────────────────────────────────────────────────────────────────────────────
# Remote state (uncomment when ready)
#
# remote_state {
#   backend = "s3"
#   generate = {
#     path      = "backend.tf"
#     if_exists = "overwrite_terragrunt"
#   }
#   config = {
#     bucket         = "homelab-tfstate"
#     key            = "${dirname(local.relative_deployment_path)}/${local.stack}.tfstate"
#     region         = "us-east-1"
#     encrypt        = true
#     dynamodb_table = "homelab-tflock"
#   }
# }
# ──────────────────────────────────────────────────────────────────────────────

# Auto-detect the Terraform stack module from the deployment directory name.
# deployments/prod/homelab-cluster → modules/stacks/homelab-cluster
terraform {
  source = "${local.root_deployments_dir}/../modules/stacks/${local.stack}"
}

# Retry transient Proxmox / network errors automatically.
retry_max_attempts       = 3
retry_sleep_interval_sec = 5
