#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: remove-stale-node.sh NODE [options]

Options:
  --dry-run                         Print mutations without running them.
  --expected-votes N                Temporarily run `pvecm expected N`.
  --archive-configs ABSOLUTE_DIR    Archive leftover VM/CT configs here.
  --remove-node-dir                 Remove the leftover /etc/pve/nodes/NODE.
  --confirm-node-dir-removal NODE   Exact confirmation for --remove-node-dir.
  -h, --help                        Show this help.

Run this only on a surviving, healthy Proxmox cluster node. The script refuses
to delete a node that is still in live Corosync membership and never deletes
guest configuration unless it has first been archived.
USAGE
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

run() {
  if [[ "$dry_run" == true ]]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

[[ $# -ge 1 ]] || { usage >&2; exit 2; }
target_node=$1
shift
dry_run=false
expected_votes=''
archive_dir=''
remove_node_dir=false
removal_confirmation=''

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=true
      shift
      ;;
    --expected-votes)
      [[ $# -ge 2 ]] || die '--expected-votes requires a value'
      expected_votes=$2
      shift 2
      ;;
    --archive-configs)
      [[ $# -ge 2 ]] || die '--archive-configs requires a path'
      archive_dir=$2
      shift 2
      ;;
    --remove-node-dir)
      remove_node_dir=true
      shift
      ;;
    --confirm-node-dir-removal)
      [[ $# -ge 2 ]] || die '--confirm-node-dir-removal requires a node name'
      removal_confirmation=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ $EUID -eq 0 ]] || die 'run as root on the surviving Proxmox node'
[[ "$target_node" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]] || die 'invalid node name'
current_node=$(hostname -s)
[[ "$target_node" != "$current_node" ]] || die 'refusing to remove the current node'
[[ -z "$expected_votes" || "$expected_votes" =~ ^[1-9][0-9]*$ ]] || die 'invalid expected-votes value'

node_dir="/etc/pve/nodes/${target_node}"
[[ "$node_dir" == "/etc/pve/nodes/${target_node}" ]] || die 'node path validation failed'
[[ "$node_dir" != /etc/pve/nodes/ && "$node_dir" != /etc/pve/nodes ]] || die 'unsafe node path'

if [[ -n "$archive_dir" ]]; then
  [[ "$archive_dir" == /* ]] || die 'archive directory must be absolute'
  [[ "$archive_dir" != / && "$archive_dir" != /etc && "$archive_dir" != /etc/pve ]] || die 'unsafe archive directory'
fi

printf '%s\n' '=== pvecm status ==='
pvecm status || true
printf '%s\n' '=== pvecm nodes ==='
pvecm nodes || true
printf '%s\n' "=== Corosync references for ${target_node} ==="
grep -n -B 4 -A 4 -E "(^|[[:space:]])name:[[:space:]]*${target_node}([[:space:]]|$)" /etc/pve/corosync.conf || true

if pvecm nodes 2>/dev/null | awk 'NR > 1 {print $NF}' | grep -Fxq "$target_node"; then
  die "${target_node} is still in live Corosync membership"
fi

if getent ahostsv4 "$target_node" >/dev/null 2>&1 && ping -c 1 -W 2 "$target_node" >/dev/null 2>&1; then
  die "${target_node} responds on the network; prove it is offline before removal"
fi

if ! pvecm status 2>/dev/null | grep -Eq '^Quorate:[[:space:]]+Yes$'; then
  [[ -n "$expected_votes" ]] || die 'cluster is not quorate; use --expected-votes only after reviewing membership'
fi

if [[ -n "$expected_votes" ]]; then
  run pvecm expected "$expected_votes"
fi

configs_present=false
if [[ -d "$node_dir" ]] && find "$node_dir" -mindepth 2 -maxdepth 2 -type f \( -path '*/qemu-server/*.conf' -o -path '*/lxc/*.conf' \) -print -quit | grep -q .; then
  configs_present=true
  [[ -n "$archive_dir" ]] || die "guest configs remain under ${node_dir}; supply --archive-configs"
  archive_name="${target_node}-guest-configs-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  run install -d -o root -g root -m 0700 "$archive_dir"
  run tar -C "$node_dir" -czf "${archive_dir}/${archive_name}" qemu-server lxc
fi

set +e
if [[ "$dry_run" == true ]]; then
  printf 'DRY-RUN: pvecm delnode %q\n' "$target_node"
  delnode_rc=0
  delnode_output=''
else
  delnode_output=$(pvecm delnode "$target_node" 2>&1)
  delnode_rc=$?
  printf '%s\n' "$delnode_output"
fi
set -e

if [[ $delnode_rc -ne 0 ]]; then
  if grep -q 'CS_ERR_NOT_EXIST' <<<"$delnode_output" && ! pvecm nodes 2>/dev/null | awk 'NR > 1 {print $NF}' | grep -Fxq "$target_node"; then
    printf 'Node is already absent from live membership; accepting CS_ERR_NOT_EXIST.\n'
  else
    die "pvecm delnode failed with exit code ${delnode_rc}"
  fi
fi

if [[ -e "$node_dir" ]]; then
  printf 'Leftover node directory: %s\n' "$node_dir"
  find "$node_dir" -mindepth 1 -maxdepth 3 -printf '%M %u:%g %p\n'
fi

if [[ "$remove_node_dir" == true ]]; then
  [[ "$removal_confirmation" == "$target_node" ]] || die 'node-directory removal confirmation does not exactly match target'
  if [[ "$configs_present" == true ]]; then
    [[ -n "$archive_dir" ]] || die 'refusing to remove unarchived guest configs'
  fi
  [[ "$node_dir" =~ ^/etc/pve/nodes/[a-zA-Z][a-zA-Z0-9_-]*$ ]] || die 'unsafe resolved node directory'
  run rm -rf --one-file-system -- "$node_dir"
fi

printf '%s\n' '=== final pvecm nodes ==='
pvecm nodes
printf '%s\n' '=== final pvecm status ==='
pvecm status

