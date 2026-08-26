# Jellyfin media stack runbook

## Scope and ownership

Argo CD owns Jellyfin, Seerr, Sonarr, Radarr, Whisparr, Prowlarr, Bazarr,
qBittorrent, FlareSolverr, Maintainerr, their routes, policies, and static
PV/PVC objects under `kubernetes/system/media/`. Terraform owns the `gpu-3` VM
data disk and Talos `media-state` user volume. Ansible owns the physical NFS
mount/export on `smallgpu`; it must never format or repair the existing NTFS
filesystem.

## Data placement

| Data | Location | Recovery expectation |
|---|---|---|
| Movies, shows, and torrents | `smallgpu:/mnt/data10tb/media` through `media-data-pv` | Replaceable; re-download if lost |
| Live application state | `gpu-3:/var/mnt/media-state` through `media-state-pv` | Restore from encrypted backup |
| Encrypted state backups | `ubuntu-workstation:/mnt/storage2-bulk/media/backups` | Retained for 30 days |
| Jellyfin transcodes | Pod-local `/cache` `emptyDir` | Disposable |

The shared container layout is deliberately stable:

```text
/data/
├── jellybridge/
├── media/
│   ├── movies/
│   ├── tv/
│   └── whisparr/
└── torrents/
    ├── incomplete/
    └── whisparr/
```

Sonarr, Radarr, Whisparr, Bazarr, and qBittorrent must all receive the single
`/data` mount. Splitting downloads and media into different mounts prevents
hardlinks and atomic imports even when both mounts ultimately refer to the same
disk. Bazarr needs this mount writable so downloaded subtitles can be stored
beside the movie or episode. Do not configure path mappings while these paths
remain identical in all applications.

Jellyfin receives the real media tree at `/data/media` read-only. Its
JellyBridge plugin receives only `/data/jellybridge` as a separate writable
mount for generated discovery-library content; Seerr does not need this
filesystem mount because JellyBridge reaches it over the internal HTTP API.

The backing NTFS filesystem exposes fixed `root:root` ownership. Ansible keeps
the declared media directories at mode `0777` so the UID-mapped media pods can
write to the shared, replaceable bulk-data tree.

## Access

Public Jellyfin and Seerr use Cloudflare Tunnel. Seerr remains available on the
private Gateway as well; every other administrative interface is private:

| Service | Public URL |
|---|---|
| Jellyfin | `https://jellyfin.547600.xyz` |
| Seerr | `https://seerr.547600.xyz` |

| Service | Private URL |
|---|---|
| Jellyfin | `https://jellyfin.home.547600.xyz` |
| Seerr | `https://seerr.home.547600.xyz` |
| Sonarr | `https://sonarr.home.547600.xyz` |
| Radarr | `https://radarr.home.547600.xyz` |
| Prowlarr | `https://prowlarr.home.547600.xyz` |
| Whisparr | `https://whisparr.home.547600.xyz` |
| Bazarr | `https://bazarr.home.547600.xyz` |
| qBittorrent | `https://qbittorrent.home.547600.xyz` |
| Maintainerr | `https://maintainerr.home.547600.xyz` |

qBittorrent peer traffic uses `192.168.1.222:51413` TCP and UDP. Forward that
port on the home router only if inbound peer reachability is desired. Never
forward the WebUI port.

## Manual completion checklist

The in-cluster deployment is automated, but these account/router actions stay
out of Git and require an operator:

1. In Cloudflare Zero Trust, open the existing tunnel and configure these
   public hostnames. No home-router forwarding is needed:

   | Public hostname | Service URL |
   |---|---|
   | `jellyfin.547600.xyz` | `http://jellyfin.media.svc.cluster.local:8096` |
   | `seerr.547600.xyz` | `http://seerr.media.svc.cluster.local:5055` |

   Do not enable Cloudflare Access because native Jellyfin and Wholphin clients
   cannot reliably complete its browser redirect. Before sharing Seerr, disable
   any unintended account-creation path, require individual Jellyfin accounts,
   give ordinary users request-only permissions, and keep Seerr administrator
   rights limited to the owner. Apply Cloudflare rate limiting/WAF rules if the
   public hostname receives abusive traffic.
2. Store the existing `media-backup-credentials` password in a password
   manager. Retrieve it without printing it into shell history:

   ```bash
   umask 077
   media_password_file="$(mktemp)"
   kubectl --kubeconfig kubeconfig.yaml -n media get secret \
     media-backup-credentials -o jsonpath='{.data.password}' \
     | base64 -d > "${media_password_file}"
   ```

   Import `${media_password_file}` into the password manager and securely
   remove the temporary plaintext file afterward.
3. Complete the credential rotations in the order below. The restored
   application state came from a repository that contained secret-bearing
   databases.
4. Configure Whisparr, qBittorrent, and its Prowlarr application/indexer sync as
   described below. API keys and tracker credentials remain out of Git.
5. Configure Bazarr's Sonarr, Radarr, Jellyfin, subtitle-provider, and language
   integrations as described below. Provider credentials remain out of Git.
6. Configure Maintainerr and leave deletion actions disabled for at least one
   complete candidate cycle. Apply the pin and cleanup policy below only after
   reviewing its proposed collection.
7. In Jellyfin, enable NVIDIA NVENC and run one real lower-bitrate transcode.
   GPU discovery is already verified, but only playback exercises the complete
   FFmpeg path.
8. Forward WAN TCP and UDP `51413` on the home router to
   `192.168.1.222:51413` for inbound peer connectivity. Do not expose port
   `8080`.
9. On the Ubuntu workstation, run the following once with the local sudo
   password so Ansible confirms the already-created critical backup directory:

   ```bash
   cd ansible
   ansible-playbook playbooks/configure-ubuntu-workstation.yml \
     --ask-become-pass --tags nfs
   ```

## Initial restore source

The original private repository contains
`backup_jellyfin_stack_20260309_203257.tar.gz`. Its expected checksum is:

```text
96bd9a88fe100d557059e221cb8cbcbf303b9245037aeacee725377cee775e4c
```

The archive contains credentials, cookies, API keys, and password hashes. It
must not be added to this public repository, a ConfigMap, or a Kubernetes
Secret. Transfer it directly to a one-shot restore pod or the critical backup
directory and remove the temporary plaintext copy afterward.

Restore these directories while every media Deployment has zero replicas:

| Archive path | Target state path | Notes |
|---|---|---|
| `configs/jellyfin` | `jellyfin` | Preserves users, libraries, and encoding settings |
| `configs/sonarr` | `sonarr` | Preserves download client, profiles, and paths |
| `configs/radarr` | `radarr` | Preserves download client, profiles, and paths |
| `configs/prowlarr` | `prowlarr` | Preserves the manually configured indexers and proxy |
| `configs/qbittorrent` | `qbittorrent` | Preserve config/categories; omit `BT_backup` if its files are absent |
| `configs/jellyseerr` | `seerr` | Seerr performs the Jellyseerr schema migration on startup |

Do not restore the old Maintainerr directory. That database had no configured
rules and predates current Jellyfin-aware cleanup support. Bazarr and Whisparr
were not in the original archive. Start all three applications fresh. Logs,
PID files, Sentry caches, and stale lockfiles are not required.

## Backup credential and verification

The backup password is an out-of-band Secret:

```bash
kubectl --kubeconfig kubeconfig.yaml -n media create secret generic \
  media-backup-credentials \
  --from-literal=password="$(openssl rand -base64 48)"
```

Do not recreate this Secret unless existing encrypted backups can be discarded.
Capture it into SOPS and keep the optional password-manager copy; losing both
makes every archive unrecoverable:

```bash
scripts/secrets.sh capture-k8s media/media-backup-credentials
```

Trigger and verify a backup:

```bash
kubectl --kubeconfig kubeconfig.yaml -n media create job \
  --from=cronjob/media-state-backup media-state-backup-manual
kubectl --kubeconfig kubeconfig.yaml -n media logs -f job/media-state-backup-manual
kubectl --kubeconfig kubeconfig.yaml -n media delete job media-state-backup-manual
```

Test decryption without extracting:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass env:MEDIA_BACKUP_PASSWORD \
  -in media-state-YYYYMMDDTHHMMSSZ.tar.gz.enc \
  | tar -tzf -
```

## Credential rotation after restore

Treat every restored secret as compromised because the old repository tracks
the archive and application databases. Rotate in this order so integrations do
not silently break:

1. Change the qBittorrent WebUI password, then update Sonarr, Radarr, and
   Whisparr.
2. Rotate Sonarr and Radarr API keys, then update Prowlarr, Seerr, Bazarr, and
   Maintainerr.
3. Rotate the Whisparr API key, update its Prowlarr application, and force an
   application sync.
4. Rotate the Prowlarr API key after its applications have re-synced.
5. Re-authenticate Seerr to Jellyfin and update its Arr connections. Generate
   a separate Jellyfin API key for Bazarr and update its Jellyfin integration.
6. Reset Jellyfin user passwords and revoke obsolete devices/sessions.
7. Re-enter or rotate external tracker credentials, cookies, and passkeys in
   Prowlarr where the provider supports rotation.

## Whisparr and Prowlarr indexer sync

Open `https://whisparr.home.547600.xyz` and complete the setup in this order:

1. Enable Whisparr authentication before adding API keys or download-client
   credentials. Set its root folder to `/data/media/whisparr`.
2. Under **Settings > Download Clients**, add qBittorrent at
   `http://qbittorrent:8080` with category `whisparr`. In qBittorrent, make the
   matching category save to `/data/torrents/whisparr`. No remote path mapping
   is needed because both applications use the same `/data` mount.
   The reconciled torrent-added hook enables sequential downloading but must
   not prioritize the last piece. A last-piece write to a new file can force
   the NTFS3 backing filesystem to initialize the entire sparse gap, blocking
   NFS service long enough to interrupt Jellyfin playback. The hook's Web API
   bypass is restricted to localhost, and active download concurrency remains
   capped at 5 to keep NFS worker capacity available for media reads. Do not
   force-start downloads in bulk: `forcedDL` torrents bypass that queue limit.
3. Copy the Whisparr API key from **Settings > General**. In Prowlarr, open
   **Settings > Apps**, add a Whisparr application, and use Prowlarr server URL
   `http://prowlarr:9696`, application server URL `http://whisparr:6969`, and
   that API key. Use **Full Sync** so Prowlarr remains the owner of the synced
   indexer definitions.
4. Create a Prowlarr tag such as `whisparr`, apply it to the Whisparr app and
   only to indexers intended for it, and select the applicable private sync
   categories in the app's advanced settings. An indexer is synced only when
   its advertised categories overlap the application's sync categories.
5. Test each selected indexer in Prowlarr, test and save the Whisparr app, then
   run **Sync App Indexers**. Confirm the resulting `(Prowlarr)` entries appear
   and pass tests under Whisparr's **Settings > Indexers**.
6. Run an interactive search in Whisparr before enabling RSS or automatic
   searches. Keep indexer credentials, cookies, passkeys, and both application
   API keys in their retained state databases, never in manifests.

## Bazarr subtitle automation

Open `https://bazarr.home.547600.xyz` and complete the setup in this order:

1. Enable Bazarr authentication before storing API keys or provider
   credentials. The route is private, but every trusted LAN or tailnet client
   can otherwise open the UI.
2. Under **Settings > Sonarr**, enable Sonarr with address `sonarr`, port
   `8989`, no SSL or URL base, and the current Sonarr API key.
3. Under **Settings > Radarr**, enable Radarr with address `radarr`, port
   `7878`, no SSL or URL base, and the current Radarr API key.
4. Do not add path mappings. Sonarr, Radarr, and Bazarr all see movie and show
   paths under the same `/data/media/...` hierarchy.
5. In Jellyfin, create a dedicated Bazarr API key under **Dashboard > API
   Keys**. Configure Bazarr's Jellyfin integration with server URL
   `http://jellyfin:8096`, that key, and the applicable movie and TV libraries.
   This lets Bazarr refresh Jellyfin after subtitle changes.
6. Add only the subtitle providers you intend to use. Keep provider usernames,
   passwords, tokens, and cookies in Bazarr's local configuration, never in
   Git. Start with one or two providers to avoid unnecessary bans or rate
   limits, then test each provider from Bazarr.
7. Create the required language profile or profiles, enable automatic subtitle
   downloading, and assign a default profile to new movies and shows. For
   existing library entries, use Bazarr's mass editor to apply the profile;
   setting a default does not retroactively assign it.
8. Run a manual search on one movie and one episode. Confirm the `.srt` files
   appear beside the video under `/data/media`, then refresh and play both in
   Jellyfin before enabling broad automatic searches.

For a bilingual library, a practical starting profile is the preferred
language plus English as a fallback. Choose whether forced and
hearing-impaired variants are wanted explicitly; treating them as ordinary
subtitles tends to produce confusing duplicate tracks.

## JellyBridge discovery and requests

Configure JellyBridge with Seerr URL `http://seerr:5055`, a Seerr API key, and
library directory `/data/jellybridge`. The directory is persistent but
replaceable bulk data and is deliberately isolated from the read-only movie and
TV mount.

Use JellyBridge's library setup action or create the Jellyfin discovery library
against `/data/jellybridge`. In Seerr, exclude or disable that discovery library
from availability scans so generated placeholders are not treated as already
downloaded media. Test the Seerr connection, run one manual discovery sync, and
confirm the generated entries appear in Jellyfin before enabling scheduled
syncs.

## Maintainerr cleanup policy

Configure Maintainerr integrations with the internal service URLs
(`http://jellyfin:8096`, `http://seerr:5055`, `http://radarr:7878`,
`http://sonarr:8989`, and `http://qbittorrent:8080`). There must be exactly one
Radarr and one Sonarr connection. Enable qBittorrent download-data deletion
after confirming hardlinks and the desired seed ratio/time limits; the storage
controller deliberately uses Maintainerr actions so this cleanup remains
coordinated with Arr, Seerr, and qBittorrent.

Maintainerr's native collection handler is batch-oriented and cannot stop when
a free-space target is reached. The `maintainerr-storage-cleanup` CronJob runs
hourly and supplies that high/low-water behavior:

- It starts only below `500 GB` free on the shared `media-data` filesystem and
  deletes one file at a time until the filesystem has more than `1 TB` free.
- Candidates are ordered lexicographically: watched files first, then older
  Arr import/download dates, then larger individual files. Movies and episodes
  share the same ordered list, so a 5 GiB movie sorts ahead of a 2 GiB episode
  when watch state and import date are tied.
- Movie actions unmonitor the movie and delete all its files. Episode actions
  unmonitor and delete that individual Sonarr episode file. A multi-episode
  file is considered watched only after all episodes backed by it were watched.
- An Arr `keep` tag, a Jellyfin favorite from any user, or a Maintainerr
  exclusion on the item or its parent prevents deletion.
- The controller creates one rule-less Maintainerr action collection per media
  library. These collections have no deletion delay, so Maintainerr's normal
  batch handler ignores them; the controller invokes the single-item action
  endpoint only after checking current free space.

The CronJob is active (`DRY_RUN=false`). Set `DRY_RUN=true` in
`kubernetes/system/media/maintainerr-storage-cleanup.yaml` to log the ordered
deletion plan without changing media. In dry-run mode, nominal reclaimed sizes
can overestimate real recovery because torrents may still hold hardlinks.

After every action, the controller waits for the filesystem's free space to
increase. If it does not, it preserves a safety marker under the retained media
state volume and stops before deleting another item. This most commonly means
qBittorrent retained the download because its seed goal was not met. Resolve
that torrent or seed-limit state; the next scheduled run clears the marker only
after it observes that space was actually reclaimed.

## GPU verification

Jellyfin is hard-pinned to `gpu-3` and requests one `nvidia.com/gpu`; it cannot
consume the RTX 3080. Verify allocation and an actual transcode:

```bash
kubectl --kubeconfig kubeconfig.yaml -n media get pod -l app=jellyfin -o wide
kubectl --kubeconfig kubeconfig.yaml -n media exec deploy/jellyfin -- nvidia-smi
kubectl --kubeconfig kubeconfig.yaml -n media logs deploy/jellyfin | grep -i ffmpeg
```

In Jellyfin, select NVIDIA NVENC, enable only codecs supported by the RTX 2060,
and trigger a lower-bitrate stream. Confirm a `jellyfin-ffmpeg` process appears
in `nvidia-smi` and `/cache` remains bounded.

## Rollback

Database upgrades are forward-only. Before changing image tags, trigger and
verify an encrypted backup. To roll back:

1. Scale all media Deployments to zero.
2. Decrypt a known-good archive into an empty staging directory.
3. Move the current state aside rather than deleting it.
4. Restore the archive into the matching state subdirectories.
5. Pin the exact application versions that created that archive.
6. Start qBittorrent, Sonarr/Radarr/Whisparr, Prowlarr, Bazarr, Jellyfin,
   Seerr, then Maintainerr, verifying each dependency before continuing.
