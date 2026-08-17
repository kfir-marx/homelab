# Jellyfin media stack runbook

## Scope and ownership

Argo CD owns Jellyfin, Seerr, Sonarr, Radarr, Prowlarr, qBittorrent,
FlareSolverr, Maintainerr, their routes, policies, and static PV/PVC objects
under `kubernetes/system/media/`. Terraform owns the `gpu-3` VM data disk and
Talos `media-state` user volume. Ansible owns the physical NFS mount/export on
`smallgpu`; it must never format or repair the existing NTFS filesystem.

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
├── media/
│   ├── movies/
│   └── tv/
└── torrents/
    └── incomplete/
```

Sonarr, Radarr, and qBittorrent must all receive the single `/data` mount.
Splitting downloads and media into different mounts prevents hardlinks and
atomic imports even when both mounts ultimately refer to the same disk.

The backing NTFS filesystem exposes fixed `root:root` ownership. Ansible keeps
the declared media directories at mode `0777` so the UID-mapped media pods can
write to the shared, replaceable bulk-data tree.

## Access

Public Jellyfin uses `https://jellyfin.547600.xyz` through Cloudflare Tunnel.
Administrative and request interfaces use the private Gateway:

| Service | Private URL |
|---|---|
| Jellyfin | `https://jellyfin.home.547600.xyz` |
| Seerr | `https://seerr.home.547600.xyz` |
| Sonarr | `https://sonarr.home.547600.xyz` |
| Radarr | `https://radarr.home.547600.xyz` |
| Prowlarr | `https://prowlarr.home.547600.xyz` |
| qBittorrent | `https://qbittorrent.home.547600.xyz` |
| Maintainerr | `https://maintainerr.home.547600.xyz` |

qBittorrent peer traffic uses `192.168.1.222:6881` TCP and UDP. Forward that
port on the home router only if inbound peer reachability is desired. Never
forward the WebUI port.

## Manual completion checklist

The in-cluster deployment is automated, but these account/router actions stay
out of Git and require an operator:

1. In Cloudflare Zero Trust, open the existing tunnel and add public hostname
   `jellyfin.547600.xyz` with service URL
   `http://jellyfin.media.svc.cluster.local:8096`. The initial deployment found
   that this public hostname did not yet resolve; the private Jellyfin route
   was healthy. Do not enable Cloudflare Access because native Jellyfin clients
   cannot complete its browser redirect.
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
4. Configure Maintainerr and leave deletion actions disabled for at least one
   complete candidate cycle. Apply the pin and cleanup policy below only after
   reviewing its proposed collection.
5. In Jellyfin, enable NVIDIA NVENC and run one real lower-bitrate transcode.
   GPU discovery is already verified, but only playback exercises the complete
   FFmpeg path.
6. Optionally forward TCP and UDP `6881` on the home router to
   `192.168.1.222` for better inbound peer connectivity. Do not expose port
   `8080`.
7. On the Ubuntu workstation, run the following once with the local sudo
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
rules and predates current Jellyfin-aware cleanup support. Start Maintainerr
fresh. Logs, PID files, Sentry caches, and stale lockfiles are not required.

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

1. Change the qBittorrent WebUI password, then update Sonarr and Radarr.
2. Rotate Sonarr and Radarr API keys, then update Prowlarr, Seerr, and
   Maintainerr.
3. Rotate the Prowlarr API key after its applications have re-synced.
4. Re-authenticate Seerr to Jellyfin and update its Arr connections.
5. Reset Jellyfin user passwords and revoke obsolete devices/sessions.
6. Re-enter or rotate external tracker credentials, cookies, and passkeys in
   Prowlarr where the provider supports rotation.

## Maintainerr cleanup policy

Maintainerr should first run in observation mode. Configure integrations with
the internal service URLs (`http://jellyfin:8096`, `http://seerr:5055`,
`http://radarr:7878`, `http://sonarr:8989`, and
`http://qbittorrent:8080`). Enable qBittorrent download-data deletion only
after confirming hardlinks and the desired seed ratio/time limits.

Recommended initial rules:

- Global pin: an Arr `keep` tag, a Jellyfin favorite, or a Maintainerr manual
  exclusion always wins.
- Movie candidate: at least 30 days old and not watched in 45 days.
- Show candidate: whole show only, no recent playback, and either ended or no
  recently added episode. Do not automatically delete individual episodes or
  seasons because qBittorrent cleanup intentionally works at whole-show level.
- Disk condition: less than 15 percent free on the selected Radarr/Sonarr root
  path.
- Grace period: seven days in a visible `Leaving Soon` collection.
- Action: `Unmonitor and delete files`, clear the Seerr request, and remove the
  qBittorrent data only after its seed limit is met.

Maintainerr has no safe global dry-run button. Leave the destructive action
disabled for at least one full rule cycle and inspect every candidate before
enabling it. Native collection handling is batch-oriented and may reclaim more
than the minimum required. Add a high-water/low-water controller only if that
behavior is observed to be too aggressive.

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
6. Start qBittorrent, Sonarr/Radarr, Prowlarr, Jellyfin, Seerr, then
   Maintainerr, verifying each dependency before continuing.
