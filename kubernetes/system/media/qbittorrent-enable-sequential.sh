#!/bin/sh
# Keep new torrents sequential so the NTFS-backed NFS server does not have to
# initialize large sparse gaps. Do not prioritize the last piece: writing it
# first makes ntfs3 zero-fill the file up to that offset and can stall nfsd.
set -eu

hash=$1
curl --fail --silent --show-error \
  -X POST http://localhost:8080/api/v2/torrents/toggleSequentialDownload \
  --data-urlencode "hashes=${hash}"
