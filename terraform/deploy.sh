#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOYMENTS_DIR="$SCRIPT_DIR/deployments"
ENV_FILE="${HOMELAB_ENV_FILE:-$REPO_ROOT/.env}"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
  echo -e "${CYAN}Usage:${NC}"
  echo "  $(basename "$0") <environment> <stack> <command> [args...]"
  echo "  $(basename "$0") <environment> all <command> [args...]"
  echo ""
  echo -e "${CYAN}Examples:${NC}"
  echo "  $(basename "$0") prod homelab-cluster plan"
  echo "  $(basename "$0") staging homelab-cluster apply"
  echo "  $(basename "$0") prod all plan          # run-all across every stack in prod"
  echo ""
  echo -e "${CYAN}Available environments and stacks:${NC}"
  for env_dir in "$DEPLOYMENTS_DIR"/*/; do
    [[ ! -d "$env_dir" ]] && continue
    local env
    env=$(basename "$env_dir")
    local stacks=()
    while IFS= read -r -d '' tg; do
      stacks+=("$(basename "$(dirname "$tg")")")
    done < <(find "$env_dir" -name "terragrunt.hcl" -print0 2>/dev/null)
    if [[ ${#stacks[@]} -gt 0 ]]; then
      echo -e "  ${GREEN}${env}${NC}: ${stacks[*]}"
    fi
  done
  echo ""
  echo -e "${CYAN}Commands:${NC} plan, apply, destroy, output, validate, init"
}

die() {
  echo -e "${RED}Error: $*${NC}" >&2
  exit 1
}

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "required environment variable '${name}' is not set."
}

setup_environment() {
  # Local runs load the gitignored .env file. CI systems such as Atlantis inject
  # the same variables directly and do not need a file in the checkout.
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  elif [[ -n "${HOMELAB_ENV_FILE:-}" ]]; then
    die "HOMELAB_ENV_FILE points to a missing file: ${ENV_FILE}"
  fi

  # root.hcl accepts the combined token. Construct it from the two values used
  # by the local .env and common CI secret stores when necessary.
  if [[ -z "${PROXMOX_API_TOKEN:-}" ]]; then
    require_env PROXMOX_APITOKEN_ID
    require_env PROXMOX_APITOKEN_SECRET
    export PROXMOX_API_TOKEN="${PROXMOX_APITOKEN_ID}=${PROXMOX_APITOKEN_SECRET}"
  fi

  require_env AWS_IAM_ROLE

  command -v terragrunt >/dev/null 2>&1 ||
    die "terragrunt is not installed or is not on PATH."

  # Terragrunt 1.x defaults to OpenTofu. This repository currently pins and
  # installs Terraform, while still allowing callers to explicitly select tofu.
  if [[ -z "${TG_TF_PATH:-}" ]]; then
    if command -v terraform >/dev/null 2>&1; then
      export TG_TF_PATH=terraform
    elif command -v tofu >/dev/null 2>&1; then
      export TG_TF_PATH=tofu
    else
      die "neither terraform nor tofu is installed or on PATH."
    fi
  elif ! command -v "$TG_TF_PATH" >/dev/null 2>&1; then
    die "TG_TF_PATH points to unavailable executable '${TG_TF_PATH}'."
  fi
}

# ── Argument parsing ─────────────────────────────────────────────────────────

if [[ $# -lt 3 ]]; then
  usage
  exit 1
fi

ENVIRONMENT="$1"
STACK="$2"
COMMAND="$3"
shift 3
EXTRA_ARGS=("$@")

# Validate environment exists
if [[ ! -d "$DEPLOYMENTS_DIR/$ENVIRONMENT" ]]; then
  echo -e "${RED}Error: environment '${ENVIRONMENT}' not found.${NC}" >&2
  echo "Available: $(ls -1 "$DEPLOYMENTS_DIR" | grep -v '\.' | tr '\n' ' ')" >&2
  exit 1
fi

# Validate stack exists (unless "all")
if [[ "$STACK" != "all" && ! -f "$DEPLOYMENTS_DIR/$ENVIRONMENT/$STACK/terragrunt.hcl" ]]; then
  echo -e "${RED}Error: stack '${STACK}' not found in '${ENVIRONMENT}'.${NC}" >&2
  echo "Available: $(find "$DEPLOYMENTS_DIR/$ENVIRONMENT" -name terragrunt.hcl -exec dirname {} \; | xargs -n1 basename 2>/dev/null | tr '\n' ' ')" >&2
  exit 1
fi

# ── Run ──────────────────────────────────────────────────────────────────────

setup_environment

if [[ "$STACK" == "all" ]]; then
  echo -e "${YELLOW}▸ terragrunt run --all -- ${COMMAND} [${ENVIRONMENT}]${NC}"
  cd "$DEPLOYMENTS_DIR/$ENVIRONMENT"
  terragrunt run --all -- "$COMMAND" "${EXTRA_ARGS[@]}"
else
  echo -e "${YELLOW}▸ terragrunt ${COMMAND} [${ENVIRONMENT}/${STACK}]${NC}"
  cd "$DEPLOYMENTS_DIR/$ENVIRONMENT/$STACK"
  terragrunt "$COMMAND" "${EXTRA_ARGS[@]}"
fi
