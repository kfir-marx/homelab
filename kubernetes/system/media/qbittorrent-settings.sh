#!/bin/sh
# Reconcile non-secret qBittorrent preferences before the daemon starts. The
# persistent configuration also contains WebUI credentials, so it is patched in
# place instead of being replaced by a ConfigMap.
set -eu

config=${QBITTORRENT_CONFIG:-/config/qBittorrent/qBittorrent.conf}
mkdir -p "$(dirname "$config")"
touch "$config"

set_ini() {
  section=$1
  key=$2
  value=$3
  temporary="${config}.tmp"

  # awk -v processes backslash escapes, while qBittorrent preference keys use
  # literal backslashes. Pass them through the environment so matching remains
  # byte-for-byte exact.
  INI_SECTION="[$section]" INI_KEY="$key" INI_VALUE="$value" awk '
    BEGIN {
      section = ENVIRON["INI_SECTION"]
      key = ENVIRON["INI_KEY"]
      value = ENVIRON["INI_VALUE"]
      in_section = 0
      found_section = 0
      wrote_value = 0
    }
    /^\[/ {
      if (in_section && !wrote_value) {
        print key "=" value
        wrote_value = 1
      }
      in_section = ($0 == section)
      if (in_section) found_section = 1
      print
      next
    }
    {
      if (in_section && index($0, key "=") == 1) {
        if (!wrote_value) print key "=" value
        wrote_value = 1
        next
      }
      print
    }
    END {
      if (!found_section) {
        print ""
        print section
        print key "=" value
      }
      else if (in_section && !wrote_value) print key "=" value
    }
  ' "$config" > "$temporary"
  mv "$temporary" "$config"
}

set_ini BitTorrent 'Session\Port' 51413
set_ini BitTorrent 'Session\UseRandomPort' false
# qBittorrent uses one setting for both UPnP and NAT-PMP.
set_ini BitTorrent 'Session\EnableUPnP' false
set_ini BitTorrent 'Session\Encryption' 1
set_ini BitTorrent 'Session\GlobalMaxConnections' 1000
set_ini BitTorrent 'Session\MaxConnectionsPerTorrent' 200
set_ini BitTorrent 'Session\GlobalUploadSpeedLimit' 10000
set_ini BitTorrent 'Session\GlobalDownloadSpeedLimit' 0
set_ini BitTorrent 'Session\QueueingSystemEnabled' true
# The backing NTFS3 filesystem serializes file extension work. Keep download
# concurrency at one so qBittorrent cannot occupy every NFS worker while a
# file is being extended.
set_ini BitTorrent 'Session\MaxActiveDownloads' 10
set_ini BitTorrent 'Session\MaxUploadsPerTorrent' 10
# The torrent-added hook calls the Web API from inside this same container.
# Bypass authentication only for localhost; routed WebUI clients still require
# the configured credentials.
set_ini Preferences 'WebUI\LocalHostAuth' false

chown 1000:1000 "$config"
chmod 0660 "$config"
