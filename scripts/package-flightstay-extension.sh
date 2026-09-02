#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXTENSION_DIR="$REPO_ROOT/extensions/flightstay-match"
allow_placeholder=false
if [[ "${1:-}" == "--bootstrap" ]]; then
  allow_placeholder=true
  shift
fi
OUTPUT="${1:-$REPO_ROOT/flightstay-match.zip}"

command -v zip >/dev/null || { echo "zip is required" >&2; exit 1; }
if [[ "$allow_placeholder" == false ]] && \
   grep -q 'REPLACE_WITH_CHROME_EXTENSION_CLIENT_ID' "$EXTENSION_DIR/manifest.json"; then
  echo "Replace the OAuth client ID in manifest.json before packaging." >&2
  exit 1
fi

rm -f -- "$OUTPUT"
(
  cd "$EXTENSION_DIR"
  zip -q -r "$OUTPUT" manifest.json service-worker.js gmail.js popup.html popup.js popup.css icons
)
echo "Created $OUTPUT"
