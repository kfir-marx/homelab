#!/bin/sh
set -eu

# Sonarr and Radarr already persist the credentials Bazarr needs on the shared
# media-state volume. Reconcile only the integration fields so provider,
# language-profile, Jellyfin, and authentication settings remain operator-owned.
state_root="${MEDIA_STATE_ROOT:-/state}"
bazarr_config="${state_root}/bazarr/config/config.yaml"
sonarr_config="${state_root}/sonarr/config.xml"
radarr_config="${state_root}/radarr/config.xml"

read_arr_api_key() {
  application="$1"
  config_path="$2"

  if [ ! -s "${config_path}" ]; then
    echo "${application} configuration is not ready at ${config_path}" >&2
    return 1
  fi

  api_key="$(yq --input-format xml --output-format yaml --unwrapScalar \
    '.Config.ApiKey // ""' "${config_path}")"
  if [ -z "${api_key}" ] || [ "${api_key}" = "null" ]; then
    echo "${application} configuration at ${config_path} has no API key" >&2
    return 1
  fi

  printf '%s' "${api_key}"
}

sonarr_api_key="$(read_arr_api_key Sonarr "${sonarr_config}")"
radarr_api_key="$(read_arr_api_key Radarr "${radarr_config}")"

mkdir -p "$(dirname "${bazarr_config}")"
if [ ! -s "${bazarr_config}" ]; then
  printf '{}\n' > "${bazarr_config}"
fi

export SONARR_API_KEY="${sonarr_api_key}"
export RADARR_API_KEY="${radarr_api_key}"
yq --inplace '
  .general.use_sonarr = true |
  .general.use_radarr = true |
  .general.path_mappings = [] |
  .general.path_mappings_movie = [] |
  .sonarr.ip = "sonarr" |
  .sonarr.port = 8989 |
  .sonarr.base_url = "/" |
  .sonarr.ssl = false |
  .sonarr.apikey = strenv(SONARR_API_KEY) |
  .radarr.ip = "radarr" |
  .radarr.port = 7878 |
  .radarr.base_url = "/" |
  .radarr.ssl = false |
  .radarr.apikey = strenv(RADARR_API_KEY)
' "${bazarr_config}"
unset SONARR_API_KEY RADARR_API_KEY sonarr_api_key radarr_api_key

chmod 0600 "${bazarr_config}"
echo "Bazarr Sonarr and Radarr integrations are configured"
