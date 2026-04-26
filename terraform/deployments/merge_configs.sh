#!/usr/bin/env bash
set -e

# Merges hierarchical YAML config files from ROOT_DIR down to DEST_DIR.
# Outputs the final merged JSON to stdout (for use with Terragrunt run_cmd).
# Diagnostic messages go to stderr so they don't pollute the JSON output.
#
# Merge order at each directory level:
#   config.yml → config.*.yml → config.yaml → config.*.yaml
# Deeper configs override higher configs (deep merge for objects).

log() { echo "$@" >&2; }

realpath_portable() {
  cd "$(dirname "$1")" >/dev/null && echo "$(pwd -P)/$(basename "$1")"
}

check_yq_version() {
  if ! command -v yq >/dev/null 2>&1; then
    log "Error: 'yq' is not installed."
    log "Install with: brew install yq (macOS) or snap install yq (Linux)"
    exit 1
  fi

  local yq_version_output
  yq_version_output=$(yq --version 2>&1)

  YQ_VERSION=$(echo "$yq_version_output" | grep -oE '[0-9]+\.[0-9]+' | head -n1)
  if [[ -z "$YQ_VERSION" ]]; then
    log "Error: Could not determine yq version."
    exit 1
  fi

  if [[ "$(printf '%s\n' "4.0" "$YQ_VERSION" | sort -V | head -n1)" != "4.0" ]]; then
    log "Error: yq v4+ (Go version) is required. Found v$YQ_VERSION."
    exit 1
  fi
}

MERGED="null"
FILES_USED=()

process_and_merge_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    log "  Merging: $file"
    FILES_USED+=("$file")

    local CONTENT
    CONTENT=$(yq eval -o=json '.' "$file")

    if ! echo "$CONTENT" | jq -e . >/dev/null 2>&1; then
      log "Error: yq output for $file was not valid JSON."
      exit 1
    fi

    if [[ "$MERGED" == "null" ]]; then
      MERGED="$CONTENT"
    else
      local merged_is_object=false
      local content_is_object=false
      if echo "$MERGED" | jq -e 'type == "object"' >/dev/null; then merged_is_object=true; fi
      if echo "$CONTENT" | jq -e 'type == "object"' >/dev/null; then content_is_object=true; fi

      if [[ "$merged_is_object" == true && "$content_is_object" == true ]]; then
        MERGED=$(jq -s 'reduce .[] as $item ({}; . * $item)' <(echo "$MERGED") <(echo "$CONTENT"))
      else
        MERGED="$CONTENT"
      fi
    fi
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

ROOT_DIR=$(realpath_portable "$1")
DEST_DIR=$(realpath_portable "$2")

if [[ -z "$ROOT_DIR" || -z "$DEST_DIR" ]]; then
  log "Usage: $0 <root_dir> <destination_dir>"
  exit 1
fi

check_yq_version

if [[ "$DEST_DIR" != "$ROOT_DIR"* ]]; then
  log "Error: DEST_DIR must be within ROOT_DIR"
  exit 1
fi

log "Merging configs: $ROOT_DIR → $DEST_DIR"

# Build path list from ROOT → DEST
DIRS=()
CUR="$DEST_DIR"
while [[ "$CUR" != "$ROOT_DIR" && "$CUR" != "/" ]]; do
  DIRS+=("$CUR")
  CUR=$(dirname "$CUR")
done
DIRS+=("$ROOT_DIR")

REVERSED=()
for (( i=${#DIRS[@]}-1; i>=0; i-- )); do
  REVERSED+=("${DIRS[$i]}")
done

# Merge configs at each level
for dir in "${REVERSED[@]}"; do
  process_and_merge_file "$dir/config.yml"

  while IFS= read -r -d '' f; do
    process_and_merge_file "$f"
  done < <(find "$dir" -maxdepth 1 -type f -name 'config.*.yml' ! -name 'config.yml' -print0 | sort -z)

  process_and_merge_file "$dir/config.yaml"

  while IFS= read -r -d '' f; do
    process_and_merge_file "$f"
  done < <(find "$dir" -maxdepth 1 -type f -name 'config.*.yaml' ! -name 'config.yaml' -print0 | sort -z)
done

if [[ ${#FILES_USED[@]} -eq 0 ]]; then
  log "Warning: No config files found. Returning empty object."
  echo "{}"
else
  log "Merged ${#FILES_USED[@]} config file(s)."
  echo "$MERGED"
fi
