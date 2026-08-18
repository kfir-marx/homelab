#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOPS_CONFIG="$REPO_ROOT/.sops.yaml"
IDENTITY_CIPHERTEXT="$REPO_ROOT/secrets/age/homelab.agekey.age"
ENV_CIPHERTEXT="$REPO_ROOT/secrets/infrastructure.sops.env"
INVENTORY_FILE="$REPO_ROOT/secrets/inventory.tsv"
PLAINTEXT_ENV="$REPO_ROOT/.env"
TASK_TEMP_DIR=""
IDENTITY_FILE=""

declare -a INVENTORY_TARGETS=()
declare -A EXPECTED_KEYS=()
declare -a SELECTED_TARGETS=()
declare -a REQUIRED_ENV_KEYS=(
  PROXMOX_APITOKEN_ID
  PROXMOX_APITOKEN_SECRET
  PROXMOX_SSH_PASSWORD
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_IAM_ROLE
  GITHUB_RUNNER_PERSONAL_ACCESS_TOKEN
)

usage() {
  cat <<'EOF'
Usage: scripts/secrets.sh <command> [arguments]

One-time setup:
  bootstrap                    Initialize or resume capture, then run full checks
  init                         Create an age identity and protect it by passphrase

Environment credentials:
  capture-env [path]           Encrypt .env (or path) into the recovery bundle
  restore-env [--yes] [path]   Restore the encrypted environment file
  edit-env                     Edit the encrypted environment with SOPS
  run -- command [args...]     Run a command with the encrypted environment loaded

Kubernetes Secrets:
  capture-k8s [namespace/name ...]
                               Snapshot all inventoried Secrets, or a selected set
  restore-k8s [--yes] [namespace/name ...]
                               Restore all encrypted snapshots, or a selected set
  edit-k8s namespace/name      Edit one encrypted Kubernetes Secret

Maintenance:
  list                         Show inventory and ciphertext presence (no decrypt)
  check                        Decrypt and validate all expected recovery material
  audit                        Check tracked recovery paths contain ciphertext only
  change-passphrase            Re-encrypt the age identity with a new passphrase
  doctor                       Check required local commands

The script never runs capture or restore implicitly. Kubernetes commands honor
KUBECONFIG and the current kubectl context.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*" >&2
}

cleanup() {
  if [[ -n "$TASK_TEMP_DIR" && -d "$TASK_TEMP_DIR" ]]; then
    rm -rf -- "$TASK_TEMP_DIR"
  fi
}

ensure_temp_dir() {
  if [[ -z "$TASK_TEMP_DIR" ]]; then
    TASK_TEMP_DIR="$(mktemp -d)"
    chmod 0700 "$TASK_TEMP_DIR"
    trap cleanup EXIT HUP INT TERM
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command '$1' is not installed"
}

load_inventory() {
  local target keys _purpose
  [[ -f "$INVENTORY_FILE" ]] || die "missing inventory: $INVENTORY_FILE"
  while IFS=$'\t' read -r target keys _purpose; do
    [[ -z "$target" || "$target" == \#* ]] && continue
    [[ "$target" == */* && -n "$keys" ]] || die "invalid inventory entry: $target"
    INVENTORY_TARGETS+=("$target")
    EXPECTED_KEYS["$target"]="$keys"
  done < "$INVENTORY_FILE"
}

require_configured() {
  [[ -f "$IDENTITY_CIPHERTEXT" ]] || die "age identity is missing; run '$0 init' first"
  if grep -q 'AGE_RECIPIENT_PLACEHOLDER' "$SOPS_CONFIG"; then
    die ".sops.yaml is not initialized; run '$0 init' first"
  fi
}

unlock_identity() {
  if [[ -n "$IDENTITY_FILE" && -f "$IDENTITY_FILE" ]]; then
    export SOPS_AGE_KEY_FILE="$IDENTITY_FILE"
    return
  fi
  require_command age
  require_configured
  ensure_temp_dir
  IDENTITY_FILE="$TASK_TEMP_DIR/homelab.agekey"
  note "Unlocking the age identity (enter the SOPS master passphrase)..."
  age --decrypt --output "$IDENTITY_FILE" "$IDENTITY_CIPHERTEXT"
  chmod 0600 "$IDENTITY_FILE"
  export SOPS_AGE_KEY_FILE="$IDENTITY_FILE"
}

target_file() {
  local target="$1"
  printf '%s/secrets/kubernetes/%s.sops.json\n' "$REPO_ROOT" "${target//\//--}"
}

is_known_target() {
  [[ -n "${EXPECTED_KEYS[$1]+known}" ]]
}

select_targets() {
  SELECTED_TARGETS=()
  if [[ $# -eq 0 ]]; then
    SELECTED_TARGETS=("${INVENTORY_TARGETS[@]}")
    return
  fi

  local target
  for target in "$@"; do
    is_known_target "$target" || die "not in secrets/inventory.tsv: $target"
    SELECTED_TARGETS+=("$target")
  done
}

confirm_cluster_write() {
  local assume_yes="$1"
  local context
  context="$(kubectl config current-context 2>/dev/null)" || die "kubectl has no current context"
  note "Target Kubernetes context: $context"
  [[ "$assume_yes" == true ]] && return
  local answer
  read -r -p "Type 'restore' to apply encrypted Secrets to this cluster: " answer </dev/tty
  [[ "$answer" == restore ]] || die "restore cancelled"
}

show_cluster_context() {
  local context
  context="$(kubectl config current-context 2>/dev/null)" || die "kubectl has no current context"
  note "Reading Kubernetes context: $context"
}

validate_env_plaintext() {
  local file="$1"
  local line key value required_key
  [[ -s "$file" ]] || die "environment file is empty: $file"

  for required_key in "${REQUIRED_ENV_KEYS[@]}"; do
    if ! grep -q "^${required_key}=" "$file"; then
      die "environment is missing required variable: $required_key"
    fi
  done

  # Reject accidental template placeholders in any variable that is present,
  # while allowing optional credentials such as NUT in telemetry-only mode.
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    key="${line%%=*}"
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || continue
    value="${line#*=}"
    value="${value%\"}"
    value="${value#\"}"
    if [[ -z "$value" || "$value" == '...' || "$value" == *REPLACE_ME* ]]; then
      die "environment contains an empty or placeholder value: $key"
    fi
  done < "$file"
}

init_identity() {
  require_command age
  require_command age-keygen
  require_command sops
  [[ ! -e "$IDENTITY_CIPHERTEXT" ]] || die "$IDENTITY_CIPHERTEXT already exists"
  grep -q 'AGE_RECIPIENT_PLACEHOLDER' "$SOPS_CONFIG" ||
    die ".sops.yaml is already initialized"

  ensure_temp_dir
  local plain_identity="$TASK_TEMP_DIR/homelab.agekey"
  local encrypted_identity="$TASK_TEMP_DIR/homelab.agekey.age"
  local config_candidate="$TASK_TEMP_DIR/sops.yaml"
  local recipient

  umask 077
  age-keygen --output "$plain_identity"
  recipient="$(age-keygen -y "$plain_identity")"
  note "Choose the strong master passphrase that will unlock disaster recovery."
  age --passphrase --armor --output "$encrypted_identity" "$plain_identity"
  sed "s|AGE_RECIPIENT_PLACEHOLDER|$recipient|g" "$SOPS_CONFIG" > "$config_candidate"

  install -m 0600 "$encrypted_identity" "$IDENTITY_CIPHERTEXT"
  install -m 0644 "$config_candidate" "$SOPS_CONFIG"
  IDENTITY_FILE="$plain_identity"
  export SOPS_AGE_KEY_FILE="$IDENTITY_FILE"
  note "Initialized SOPS recipient: $recipient"
  note "The passphrase-encrypted identity is ready; commit only ciphertext."
}

bootstrap_recovery() {
  if [[ -f "$IDENTITY_CIPHERTEXT" ]]; then
    require_configured
    note "Resuming bootstrap with the existing encrypted age identity."
    unlock_identity
  else
    init_identity
  fi
  capture_env
  capture_k8s
  check_recovery
  note "Bootstrap complete. Review and commit the encrypted recovery artifacts."
}

capture_env() {
  require_command sops
  local source="${1:-$PLAINTEXT_ENV}"
  [[ -f "$source" ]] || die "plaintext environment file does not exist: $source"
  validate_env_plaintext "$source"
  unlock_identity
  ensure_temp_dir
  local candidate="$TASK_TEMP_DIR/infrastructure.sops.env"
  (
    cd "$REPO_ROOT"
    sops --config "$SOPS_CONFIG" --encrypt \
      --input-type dotenv --output-type dotenv \
      --filename-override "secrets/infrastructure.sops.env" \
      "$source" > "$candidate"
  )
  [[ -s "$candidate" ]] || die "SOPS produced an empty environment ciphertext"
  install -m 0644 "$candidate" "$ENV_CIPHERTEXT"
  note "Updated secrets/infrastructure.sops.env; plaintext source was not changed."
}

restore_env() {
  require_command sops
  local assume_yes=false
  if [[ "${1:-}" == --yes ]]; then
    assume_yes=true
    shift
  fi
  local destination="${1:-$PLAINTEXT_ENV}"
  [[ $# -le 1 ]] || die "restore-env accepts only [--yes] [path]"
  [[ -f "$ENV_CIPHERTEXT" ]] || die "missing $ENV_CIPHERTEXT"
  if [[ -e "$destination" && "$assume_yes" != true ]]; then
    die "$destination exists; pass --yes to replace it atomically"
  fi
  require_configured
  unlock_identity
  ensure_temp_dir
  local candidate="$TASK_TEMP_DIR/restored.env"
  sops --config "$SOPS_CONFIG" --decrypt "$ENV_CIPHERTEXT" > "$candidate"
  chmod 0600 "$candidate"
  validate_env_plaintext "$candidate"
  install -m 0600 "$candidate" "$destination"
  note "Restored plaintext environment to $destination (mode 0600, gitignored)."
}

edit_env() {
  require_command sops
  [[ -f "$ENV_CIPHERTEXT" ]] || die "capture the environment before editing it"
  unlock_identity
  sops --config "$SOPS_CONFIG" "$ENV_CIPHERTEXT"
}

run_with_env() {
  require_command sops
  [[ "${1:-}" == -- ]] && shift
  [[ $# -gt 0 ]] || die "run requires a command after '--'"
  [[ -f "$ENV_CIPHERTEXT" ]] || die "missing $ENV_CIPHERTEXT"
  unlock_identity
  ensure_temp_dir
  local runtime_env="$TASK_TEMP_DIR/runtime.env"
  sops --config "$SOPS_CONFIG" --decrypt "$ENV_CIPHERTEXT" > "$runtime_env"
  chmod 0600 "$runtime_env"
  validate_env_plaintext "$runtime_env"
  set -a
  # shellcheck disable=SC1090
  source "$runtime_env"
  set +a
  export HOMELAB_ENV_FILE="$runtime_env"
  "$@"
}

capture_k8s() {
  require_command kubectl
  require_command jq
  require_command sops
  select_targets "$@"
  show_cluster_context
  unlock_identity
  ensure_temp_dir

  local target namespace name destination candidate
  local captured=0
  local missing=0
  for target in "${SELECTED_TARGETS[@]}"; do
    namespace="${target%%/*}"
    name="${target#*/}"
    destination="$(target_file "$target")"
    candidate="$TASK_TEMP_DIR/${namespace}--${name}.sops.json"

    if ! kubectl --namespace "$namespace" get secret "$name" >/dev/null 2>&1; then
      note "Missing in current cluster: $target"
      missing=$((missing + 1))
      continue
    fi

    kubectl --namespace "$namespace" get secret "$name" --output json \
      | jq '{apiVersion, kind, metadata: {name: .metadata.name, namespace: .metadata.namespace}, type, data}' \
      | (
          cd "$REPO_ROOT"
          sops --config "$SOPS_CONFIG" --encrypt \
            --input-type json --output-type json \
            --filename-override "secrets/kubernetes/${namespace}--${name}.sops.json" \
            /dev/stdin
        ) > "$candidate"
    [[ -s "$candidate" ]] || die "SOPS produced empty ciphertext for $target"
    install -m 0644 "$candidate" "$destination"
    note "Captured: $target"
    captured=$((captured + 1))
  done

  note "Capture complete: $captured updated, $missing missing."
  [[ "$missing" -eq 0 ]] || return 1
}

validate_k8s_file() {
  local target="$1"
  local file namespace name expected plain key
  file="$(target_file "$target")"
  namespace="${target%%/*}"
  name="${target#*/}"
  expected="${EXPECTED_KEYS[$target]}"
  [[ -f "$file" ]] || die "missing ciphertext for $target"
  ensure_temp_dir
  plain="$TASK_TEMP_DIR/validate-${namespace}--${name}.json"
  sops --config "$SOPS_CONFIG" --decrypt "$file" > "$plain"
  jq -e --arg namespace "$namespace" --arg name "$name" \
    '.apiVersion == "v1" and .kind == "Secret" and
     .metadata.namespace == $namespace and .metadata.name == $name and
     (.data | type == "object")' "$plain" >/dev/null ||
    die "invalid Secret identity or data in $file"

  if [[ "$expected" == '*' ]]; then
    jq -e '.data | length > 0' "$plain" >/dev/null || die "$target has empty dynamic data"
  else
    IFS='|' read -r -a keys <<< "$expected"
    for key in "${keys[@]}"; do
      jq -e --arg key "$key" '.data[$key] | type == "string" and length > 0' \
        "$plain" >/dev/null || die "$target is missing required data key: $key"
    done
  fi
  rm -f -- "$plain"
}

restore_k8s() {
  require_command kubectl
  require_command jq
  require_command sops
  local assume_yes=false
  if [[ "${1:-}" == --yes ]]; then
    assume_yes=true
    shift
  fi
  select_targets "$@"
  unlock_identity

  local target namespace file
  for target in "${SELECTED_TARGETS[@]}"; do
    validate_k8s_file "$target"
  done
  confirm_cluster_write "$assume_yes"

  for target in "${SELECTED_TARGETS[@]}"; do
    namespace="${target%%/*}"
    file="$(target_file "$target")"
    kubectl create namespace "$namespace" --dry-run=client --output yaml \
      | kubectl apply --server-side --field-manager=homelab-sops-recovery --filename - >/dev/null
    sops --config "$SOPS_CONFIG" --decrypt "$file" \
      | kubectl apply --server-side --field-manager=homelab-sops-recovery --filename - >/dev/null
    note "Restored: $target"
  done
}

edit_k8s() {
  require_command sops
  local target="${1:-}"
  [[ -n "$target" && $# -eq 1 ]] || die "edit-k8s requires one namespace/name"
  is_known_target "$target" || die "not in secrets/inventory.tsv: $target"
  local file
  file="$(target_file "$target")"
  [[ -f "$file" ]] || die "missing ciphertext for $target; capture it first"
  unlock_identity
  sops --config "$SOPS_CONFIG" "$file"
}

list_inventory() {
  local target file status
  printf '%-58s %s\n' 'RECOVERY ITEM' 'STATUS'
  if [[ -f "$IDENTITY_CIPHERTEXT" ]]; then status=present; else status=missing; fi
  printf '%-58s %s\n' 'age identity (passphrase encrypted)' "$status"
  if [[ -f "$ENV_CIPHERTEXT" ]]; then status=present; else status=missing; fi
  printf '%-58s %s\n' 'infrastructure environment' "$status"
  for target in "${INVENTORY_TARGETS[@]}"; do
    file="$(target_file "$target")"
    if [[ -f "$file" ]]; then status=present; else status=missing; fi
    printf '%-58s %s\n' "$target" "$status"
  done
}

audit_ciphertext() {
  require_command jq
  local failures=0 file
  if ! grep -q 'AGE_RECIPIENT_PLACEHOLDER' "$SOPS_CONFIG" &&
     [[ ! -f "$IDENTITY_CIPHERTEXT" ]]; then
    note ".sops.yaml is initialized but the encrypted age identity is missing"
    failures=$((failures + 1))
  fi
  if [[ -f "$IDENTITY_CIPHERTEXT" ]]; then
    if ! grep -q '^-----BEGIN AGE ENCRYPTED FILE-----$' "$IDENTITY_CIPHERTEXT" ||
       grep -q 'AGE-SECRET-KEY-' "$IDENTITY_CIPHERTEXT"; then
      note "Not recognized as a passphrase-encrypted age identity: $IDENTITY_CIPHERTEXT"
      failures=$((failures + 1))
    fi
    if grep -q 'AGE_RECIPIENT_PLACEHOLDER' "$SOPS_CONFIG"; then
      note "Encrypted identity exists but .sops.yaml still has the placeholder recipient"
      failures=$((failures + 1))
    fi
  fi
  if [[ -f "$ENV_CIPHERTEXT" ]]; then
    if [[ ! -f "$IDENTITY_CIPHERTEXT" ]]; then
      note "Environment ciphertext exists but the encrypted age identity is missing"
      failures=$((failures + 1))
    fi
    if ! grep -q '^sops_' "$ENV_CIPHERTEXT" ||
       ! awk -F= '
          /^[[:space:]]*($|#)/ { next }
          $1 !~ /^sops_/ && $2 !~ /^ENC\[/ { bad=1 }
          END { exit bad }
        ' "$ENV_CIPHERTEXT"; then
      note "Not recognized as fully encrypted SOPS dotenv ciphertext: $ENV_CIPHERTEXT"
      failures=$((failures + 1))
    fi
  fi
  while IFS= read -r -d '' file; do
    if [[ ! -f "$IDENTITY_CIPHERTEXT" ]]; then
      note "Kubernetes ciphertext exists but the encrypted age identity is missing: $file"
      failures=$((failures + 1))
    fi
    if ! jq -e '
      .sops and (.sops.mac | type == "string") and
      (.data | type == "object") and
      all(.data[]; type == "string" and startswith("ENC["))
    ' "$file" >/dev/null; then
      note "Not recognized as SOPS JSON ciphertext: $file"
      failures=$((failures + 1))
    fi
  done < <(find "$REPO_ROOT/secrets/kubernetes" -maxdepth 1 -type f -name '*.sops.json' -print0)

  while IFS= read -r -d '' file; do
    case "$file" in
      "$REPO_ROOT/secrets/README.md"|"$INVENTORY_FILE"|"$IDENTITY_CIPHERTEXT"|*/.gitkeep|*.sops.env|*.sops.json) ;;
      *) note "Unexpected file under secrets/: $file"; failures=$((failures + 1)) ;;
    esac
  done < <(find "$REPO_ROOT/secrets" -type f -print0)

  [[ "$failures" -eq 0 ]] || die "ciphertext audit found $failures problem(s)"
  note "Ciphertext audit passed."
}

check_recovery() {
  require_command jq
  require_command sops
  [[ -f "$ENV_CIPHERTEXT" ]] || die "missing encrypted environment"
  unlock_identity
  ensure_temp_dir
  local env_plain="$TASK_TEMP_DIR/check.env"
  sops --config "$SOPS_CONFIG" --decrypt "$ENV_CIPHERTEXT" > "$env_plain"
  validate_env_plaintext "$env_plain"

  local target
  for target in "${INVENTORY_TARGETS[@]}"; do
    validate_k8s_file "$target"
  done
  audit_ciphertext
  note "Recovery check passed for the environment and ${#INVENTORY_TARGETS[@]} Kubernetes Secrets."
}

change_passphrase() {
  require_command age
  require_configured
  ensure_temp_dir
  local plain_identity="$TASK_TEMP_DIR/rekey.agekey"
  local candidate="$TASK_TEMP_DIR/rekey.agekey.age"
  note "Enter the current master passphrase."
  age --decrypt --output "$plain_identity" "$IDENTITY_CIPHERTEXT"
  note "Choose the new master passphrase."
  age --passphrase --armor --output "$candidate" "$plain_identity"
  install -m 0600 "$candidate" "$IDENTITY_CIPHERTEXT"
  note "Changed the identity passphrase. SOPS files do not need re-encryption."
}

doctor() {
  local missing=0 command
  for command in age age-keygen sops jq kubectl; do
    if command -v "$command" >/dev/null 2>&1; then
      printf '%-12s %s\n' "$command" "$(command -v "$command")"
    else
      printf '%-12s %s\n' "$command" MISSING
      missing=$((missing + 1))
    fi
  done
  [[ "$missing" -eq 0 ]] || return 1
}

main() {
  umask 077
  cd "$REPO_ROOT"
  load_inventory
  local command="${1:-}"
  [[ -n "$command" ]] || { usage; exit 1; }
  shift

  case "$command" in
    bootstrap) [[ $# -eq 0 ]] || die "bootstrap takes no arguments"; bootstrap_recovery ;;
    init) [[ $# -eq 0 ]] || die "init takes no arguments"; init_identity ;;
    capture-env) [[ $# -le 1 ]] || die "capture-env accepts at most one path"; capture_env "$@" ;;
    restore-env) restore_env "$@" ;;
    edit-env) [[ $# -eq 0 ]] || die "edit-env takes no arguments"; edit_env ;;
    run) run_with_env "$@" ;;
    capture-k8s) capture_k8s "$@" ;;
    restore-k8s) restore_k8s "$@" ;;
    edit-k8s) edit_k8s "$@" ;;
    list) [[ $# -eq 0 ]] || die "list takes no arguments"; list_inventory ;;
    check) [[ $# -eq 0 ]] || die "check takes no arguments"; check_recovery ;;
    audit) [[ $# -eq 0 ]] || die "audit takes no arguments"; audit_ciphertext ;;
    change-passphrase) [[ $# -eq 0 ]] || die "change-passphrase takes no arguments"; change_passphrase ;;
    doctor) [[ $# -eq 0 ]] || die "doctor takes no arguments"; doctor ;;
    help|-h|--help) usage ;;
    *) usage >&2; die "unknown command: $command" ;;
  esac
}

main "$@"
