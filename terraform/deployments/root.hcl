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
  proxmox_api_token    = get_env("PROXMOX_API_TOKEN")
  proxmox_ssh_password = get_env("PROXMOX_SSH_PASSWORD", "")
})

# ──────────────────────────────────────────────────────────────────────────────
# Remote state — S3 + DynamoDB locking. Terragrunt assumes the IAM role
# defined below before any AWS call (state read/write, lock acquisition).
# Local AWS credentials must have sts:AssumeRole on the target role.
# ──────────────────────────────────────────────────────────────────────────────
iam_role = get_env("AWS_IAM_ROLE")

remote_state {
  backend = "s3"

  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }

  config = {
    bucket       = "kfir-homelab-tfstate"
    key          = "${dirname(local.relative_deployment_path)}/${local.stack}.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true

    s3_bucket_tags = {
      Owner = "DevOps"
      Name  = "Terragrunt state storage"
    }
  }
}

# Auto-detect the Terraform stack module from the deployment directory name.
# deployments/prod/homelab-cluster → modules/stacks/homelab-cluster
terraform {
  source = "${local.root_deployments_dir}/../modules/stacks/${local.stack}"
}

# Terragrunt's built-in transient-error retry policy is used here. Its defaults
# are three attempts with a five-second delay, matching the previous overrides.
