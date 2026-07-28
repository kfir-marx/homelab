#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 0 ]]; then
  echo "this helper is invoked by Terraform using TALOS_* environment variables" >&2
  exit 2
fi

: "${TALOS_IMAGE_URL:?missing TALOS_IMAGE_URL}"
: "${TALOS_ASSET_DIR:?missing TALOS_ASSET_DIR}"
: "${TALOS_NODE_NAME:?missing TALOS_NODE_NAME}"
: "${TALOS_DISK_GIB:?missing TALOS_DISK_GIB}"
: "${TALOS_IP_CIDR:?missing TALOS_IP_CIDR}"
: "${TALOS_GATEWAY:?missing TALOS_GATEWAY}"
: "${TALOS_MAC:?missing TALOS_MAC}"
: "${TALOS_DNS:?missing TALOS_DNS}"

image_url=${TALOS_IMAGE_URL}
asset_dir=${TALOS_ASSET_DIR}
name=${TALOS_NODE_NAME}
disk_gib=${TALOS_DISK_GIB}
ip_cidr=${TALOS_IP_CIDR}
gateway=${TALOS_GATEWAY}
mac_address=${TALOS_MAC}
nameservers=${TALOS_DNS}

for command_name in curl genisoimage qemu-img zstd; do
  command -v "${command_name}" >/dev/null || {
    echo "missing required command: ${command_name}" >&2
    exit 1
  }
done

mkdir -p "${asset_dir}/${name}/cidata"

image_cache_key=$(printf '%s' "${image_url}" | sha256sum | cut -c1-16)
compressed_path="${asset_dir}/talos-${image_cache_key}.raw.zst"
system_image_path="${asset_dir}/${name}/system.qcow2"
system_image_stamp="${asset_dir}/${name}/system.qcow2.source"
cidata_dir="${asset_dir}/${name}/cidata"
cidata_image_path="${asset_dir}/${name}/cidata.iso"
desired_system_image_source="${image_url} ${disk_gib}G"

if [[ ! -s "${compressed_path}" ]]; then
  curl \
    --fail \
    --location \
    --retry 3 \
    --silent \
    --show-error \
    --output "${compressed_path}.partial" \
    "${image_url}"
  zstd --test "${compressed_path}.partial"
  mv "${compressed_path}.partial" "${compressed_path}"
fi

current_system_image_source=""
if [[ -r "${system_image_stamp}" ]]; then
  current_system_image_source=$(<"${system_image_stamp}")
fi

if [[ ! -s "${system_image_path}" || "${current_system_image_source}" != "${desired_system_image_source}" ]]; then
  raw_image_path="${asset_dir}/${name}/system.raw.partial"
  trap 'rm -f "${raw_image_path}" "${system_image_path}.partial"' EXIT
  zstd --decompress --stdout "${compressed_path}" >"${raw_image_path}"
  qemu-img resize -f raw "${raw_image_path}" "${disk_gib}G"
  qemu-img convert \
    -f raw \
    -O qcow2 \
    -o compat=1.1,lazy_refcounts=on \
    "${raw_image_path}" \
    "${system_image_path}.partial"
  mv "${system_image_path}.partial" "${system_image_path}"
  printf '%s\n' "${desired_system_image_source}" >"${system_image_stamp}"
  rm -f "${raw_image_path}"
  trap - EXIT
fi

ip_address=${ip_cidr%/*}
prefix=${ip_cidr#*/}
if [[ "${prefix}" != "24" ]]; then
  echo "only /24 workstation LAN addresses are currently supported" >&2
  exit 1
fi

cat >"${cidata_dir}/meta-data" <<EOF
local-hostname: ${name}
EOF

# Talos reads NoCloud network-config before its API is available. This gives
# the Terraform Talos provider a stable endpoint for the first config apply.
cat >"${cidata_dir}/network-config" <<EOF
version: 1
config:
  - type: physical
    name: eth0
    mac_address: "${mac_address}"
    subnets:
      - type: static
        address: ${ip_address}
        netmask: 255.255.255.0
        gateway: ${gateway}
        dns_nameservers: [${nameservers}]
EOF

# user-data is deliberately absent. The CIDATA disk owns only first-boot
# networking; the Talos provider remains the sole owner of machine config.
genisoimage \
  -quiet \
  -output "${cidata_image_path}.partial" \
  -volid cidata \
  -rock \
  -joliet \
  "${cidata_dir}/meta-data" \
  "${cidata_dir}/network-config"
mv "${cidata_image_path}.partial" "${cidata_image_path}"
