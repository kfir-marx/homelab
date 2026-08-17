#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: join-replacement-node.sh SURVIVOR_IP options

Required options:
  --expected-hostname NAME
  --expected-address CIDR
  --expected-gateway IP

Safety options:
  --confirm-join NAME   Must exactly match --expected-hostname for a live join.
  --dry-run             Run all read-only checks and print the join command.
  -h, --help            Show this help.

Joining replaces this node's local /etc/pve state. Authentication to the
surviving node is interactive and no credential is stored by this script.
USAGE
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $# -ge 1 ]] || { usage >&2; exit 2; }
survivor_ip=$1
shift
expected_hostname=''
expected_address=''
expected_gateway=''
confirm_join=''
dry_run=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expected-hostname)
      [[ $# -ge 2 ]] || die '--expected-hostname requires a value'
      expected_hostname=$2
      shift 2
      ;;
    --expected-address)
      [[ $# -ge 2 ]] || die '--expected-address requires CIDR'
      expected_address=$2
      shift 2
      ;;
    --expected-gateway)
      [[ $# -ge 2 ]] || die '--expected-gateway requires an IP'
      expected_gateway=$2
      shift 2
      ;;
    --confirm-join)
      [[ $# -ge 2 ]] || die '--confirm-join requires a hostname'
      confirm_join=$2
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
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

[[ $EUID -eq 0 ]] || die 'run as root on the replacement Proxmox node'
[[ "$survivor_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die 'invalid survivor IP'
[[ "$expected_gateway" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die 'invalid gateway IP'
[[ "$expected_address" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || die 'invalid expected CIDR'
[[ "$expected_hostname" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]] || die 'invalid expected hostname'

current_hostname=$(hostname -s)
current_fqdn=$(hostname -f)
[[ "$current_hostname" == "$expected_hostname" ]] || die "hostname is ${current_hostname}, expected ${expected_hostname}"
[[ "$current_fqdn" == "$expected_hostname" || "$current_fqdn" == "$expected_hostname".* ]] || die "unexpected FQDN: ${current_fqdn}"

ip link show vmbr0 >/dev/null 2>&1 || die 'vmbr0 is missing'
ip -4 -o addr show dev vmbr0 | awk '{print $4}' | grep -Fxq "$expected_address" || die "vmbr0 does not own ${expected_address}"
ip -4 route show default | awk '{print $3}' | grep -Fxq "$expected_gateway" || die "default gateway is not ${expected_gateway}"
ping -c 2 -W 2 "$survivor_ip" >/dev/null || die 'surviving node is unreachable'

local_major=$(pveversion | sed -nE 's#^pve-manager/([0-9]+)\..*#\1#p')
[[ -n "$local_major" ]] || die 'cannot determine local Proxmox major version'

printf 'Authenticating to surviving node for version comparison...\n'
remote_version=$(ssh -o StrictHostKeyChecking=accept-new "root@${survivor_ip}" pveversion)
remote_major=$(sed -nE 's#^pve-manager/([0-9]+)\..*#\1#p' <<<"$remote_version")
[[ "$local_major" == "$remote_major" ]] || die "Proxmox major mismatch: local ${local_major}, survivor ${remote_major}"

qemu_count=$(qm list | awk 'NR > 1 {count++} END {print count+0}')
ct_count=$(pct list | awk 'NR > 1 {count++} END {print count+0}')
[[ "$qemu_count" -eq 0 && "$ct_count" -eq 0 ]] || die 'replacement node has unexpected local VMs or containers'

printf 'Local node: %s (%s), address %s, gateway %s\n' "$current_hostname" "$current_fqdn" "$expected_address" "$expected_gateway"
printf 'Survivor: %s, compatible Proxmox major %s\n' "$survivor_ip" "$local_major"
printf '%s\n' 'WARNING: pvecm add replaces local /etc/pve cluster state.'

if [[ "$dry_run" == true ]]; then
  printf 'DRY-RUN: pvecm add %q\n' "$survivor_ip"
  exit 0
fi

[[ "$confirm_join" == "$expected_hostname" ]] || die '--confirm-join must exactly match the replacement hostname'
pvecm add "$survivor_ip"

printf '%s\n' '=== final pvecm nodes ==='
pvecm nodes
printf '%s\n' '=== final pvecm status ==='
pvecm status
pvecm status | grep -Eq '^Quorate:[[:space:]]+Yes$' || die 'joined cluster is not quorate'

