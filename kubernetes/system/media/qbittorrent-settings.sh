#!/bin/sh
# Reconcile non-secret qBittorrent preferences before the daemon starts. The
# persistent configuration also contains WebUI credentials, so it is patched in
# place instead of being replaced by a ConfigMap.
set -eu

config=/config/qBittorrent/qBittorrent.conf
mkdir -p "$(dirname "$config")"
touch "$config"

set_ini() {
  section=$1
  key=$2
  value=$3
  temporary="${config}.tmp"

  awk -v section="[$section]" -v key="$key" -v value="$value" '
    BEGIN { in_section = 0; found_section = 0; wrote_value = 0 }
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
set_ini BitTorrent 'Session\MaxActiveDownloads' 4
set_ini BitTorrent 'Session\MaxUploadsPerTorrent' 10

chown 1000:1000 "$config"
chmod 0660 "$config"
