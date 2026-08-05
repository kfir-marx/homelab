# Bitwarden private password-vault runbook

This deployment runs the official Bitwarden Lite image with PostgreSQL. Lite is
Bitwarden's single-container distribution for individuals and homelabs; it is
used instead of the much larger multi-service Helm/MSSQL deployment.

The only external entry point is Tailscale's Kubernetes ingress at:

```text
https://bitwarden.ghoul-slowworm.ts.net
```

The Service is `ClusterIP`, there is no route on the LAN Cilium Gateway, and
there is no Cloudflare Tunnel rule. Tailscale terminates HTTPS with a publicly
trusted certificate, but the endpoint is not a Funnel and is reachable only by
tailnet identities allowed by policy.

Bitwarden vault records, attachments, identity material, PostgreSQL, and local
logical backups use retained static PVs on the critical NFS export at
`ubuntu-workstation:/mnt/storage2-bulk`.

Upstream references:

- [Bitwarden Lite deployment](https://bitwarden.com/help/install-and-deploy-lite/)
- [Bitwarden database options](https://bitwarden.com/help/database-options/)
- [Tailscale Kubernetes Operator installation](https://tailscale.com/docs/kubernetes-operator/install-operator)
- [Tailscale layer-7 cluster ingress](https://tailscale.com/docs/kubernetes-operator/ingress/expose-workload-to-tailnet-l7)
- [Bitwarden LastPass import](https://bitwarden.com/help/import-from-lastpass/)

## 1. Prepare Tailscale

In **DNS** in the Tailscale admin console, keep MagicDNS enabled and enable
HTTPS certificates. The operator uses this to issue the certificate for the
`bitwarden` MagicDNS name.

Merge these tag owners into the tailnet policy; do not replace existing
`tag:router` rules:

```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:bitwarden": ["tag:k8s-operator"]
}
```

Grant only the intended identities HTTPS access. For example, replace the
email below with the owner of the vault and merge this entry into the existing
`grants` array:

```json
{
  "src": ["your-email@example.com"],
  "dst": ["tag:bitwarden"],
  "ip": ["tcp:443"]
}
```

Create an OAuth client in **Trust credentials**. Give it the
`tag:k8s-operator` tag and write scope for **General/Services**,
**Devices/Core**, and **Keys/Auth Keys**. Store it out-of-band before the
ArgoCD Application syncs:

```bash
kubectl create namespace tailscale --dry-run=client -o yaml | kubectl apply -f -
read -r -p "Tailscale OAuth client ID: " tailscale_client_id
read -r -s -p "Tailscale OAuth client secret: " tailscale_client_secret
printf '\n'
kubectl -n tailscale create secret generic operator-oauth \
  --from-literal=client_id="$tailscale_client_id" \
  --from-literal=client_secret="$tailscale_client_secret"
unset tailscale_client_id tailscale_client_secret
```

The shell history contains variable names, not the credential values. Do not
run these commands with shell tracing enabled.

The existing `tailscale-router` remains responsible for the LAN subnet route.
The operator is separate and creates a dedicated HTTPS proxy for Bitwarden.

## 2. Prepare critical NFS

On `ubuntu-workstation`, first confirm the target does not contain unexpected
data, then create the three application directories:

```bash
ssh kfir@192.168.1.105
sudo ls -la /mnt/storage2-bulk/bitwarden 2>/dev/null || true
sudo install -d -o 1000 -g 1000 -m 0700 /mnt/storage2-bulk/bitwarden/app
sudo install -d -o 999 -g 999 -m 0700 /mnt/storage2-bulk/bitwarden/postgres
sudo install -d -o 1000 -g 1000 -m 0700 /mnt/storage2-bulk/bitwarden/backups
exit
```

Do not create a new filesystem or alter the NFS export. These are directories
inside the existing critical export.

PostgreSQL on NFS has a larger latency and interruption risk than a permanent
local SSD. The cluster does not currently have a permanent local-storage
failure domain, so the retained critical tier is the safer ownership choice.
Keep tested logical backups and revisit placement when permanent SSD storage is
available.

## 3. Create the Bitwarden Secret

Generate an installation ID and key at <https://bitwarden.com/host/>. Select
the same Bitwarden cloud region used by any self-hosted license.

Create the namespace and secret without committing any value to Git:

```bash
kubectl create namespace bitwarden --dry-run=client -o yaml | kubectl apply -f -
read -r -p "Bitwarden installation ID: " bitwarden_installation_id
read -r -s -p "Bitwarden installation key: " bitwarden_installation_key
printf '\n'
bitwarden_db_password="$(openssl rand -base64 48 | tr -d '\n')"
bitwarden_internal_key="$(openssl rand -hex 32)"
bitwarden_oidc_key="$(openssl rand -hex 32)"
bitwarden_duo_key="$(openssl rand -hex 32)"
bitwarden_identity_password="$(openssl rand -base64 48 | tr -d '\n')"
kubectl -n bitwarden create secret generic bitwarden-secrets \
  --from-literal=BW_INSTALLATION_ID="$bitwarden_installation_id" \
  --from-literal=BW_INSTALLATION_KEY="$bitwarden_installation_key" \
  --from-literal=BW_DB_PASSWORD="$bitwarden_db_password" \
  --from-literal=globalSettings__internalIdentityKey="$bitwarden_internal_key" \
  --from-literal=globalSettings__oidcIdentityClientKey="$bitwarden_oidc_key" \
  --from-literal=globalSettings__duo__aKey="$bitwarden_duo_key" \
  --from-literal=globalSettings__identityServer__certificatePassword="$bitwarden_identity_password"
unset bitwarden_installation_id bitwarden_installation_key \
  bitwarden_db_password bitwarden_internal_key bitwarden_oidc_key \
  bitwarden_duo_key bitwarden_identity_password
```

Back up this Secret through an encrypted, access-controlled process. It lives
in Kubernetes etcd by necessity; do not copy it as plaintext onto NFS or into
this repository.

SMTP is required for a complete self-hosted deployment, including account email
verification, new-device verification, invitations, and System Administrator
Portal login. Before onboarding, add the SMTP username and password to
`bitwarden-secrets`, and add the non-secret reply-to address, host, port, SSL,
and STARTTLS values to `bitwarden-config`. Bitwarden recommends authenticated
submission on port 587 with STARTTLS. Keep certificate validation enabled.

For a port-587 provider, add entries like these to the ConfigMap's `data` map:

```yaml
globalSettings__mail__replyToEmail: no-reply@example.com
globalSettings__mail__smtp__host: smtp.example.com
globalSettings__mail__smtp__port: "587"
globalSettings__mail__smtp__ssl: "false"
globalSettings__mail__smtp__startTls: "true"
```

Add the credentials to the Secret creation command in the same shell session:

```bash
read -r -p "SMTP username: " bitwarden_smtp_username
read -r -s -p "SMTP password: " bitwarden_smtp_password
printf '\n'
# Add these two arguments to `kubectl create secret generic` above:
# --from-literal=globalSettings__mail__smtp__username="$bitwarden_smtp_username"
# --from-literal=globalSettings__mail__smtp__password="$bitwarden_smtp_password"
```

Unset both variables with the other temporary values after creating the
Secret. Do not commit SMTP credentials.

The System Administrator Portal is disabled by default in this deployment. If
it is needed, first configure SMTP, add an explicit comma-separated
`adminSettings__admins` email allow-list, and then set `BW_ENABLE_ADMIN` to
`"true"` in `kubernetes/system/bitwarden/config.yaml`.

## 4. Deploy and verify

Commit and push the manifests. The root ArgoCD Application discovers both
child Applications. To bootstrap them manually without applying workload
resources directly:

```bash
kubectl apply -f kubernetes/apps/tailscale-operator.yaml
kubectl apply -f kubernetes/apps/bitwarden.yaml
```

Wait for the operator, storage, database, and Bitwarden:

```bash
kubectl -n tailscale rollout status deployment/operator --timeout=5m
kubectl -n bitwarden get pvc
kubectl -n bitwarden rollout status statefulset/bitwarden-postgres --timeout=10m
kubectl -n bitwarden rollout status deployment/bitwarden --timeout=10m
kubectl -n bitwarden get ingress bitwarden
```

The ingress `ADDRESS` must become
`bitwarden.ghoul-slowworm.ts.net`. From an allowed tailnet device:

```bash
curl --fail --show-error https://bitwarden.ghoul-slowworm.ts.net/alive
```

Also verify the exposure model:

```bash
kubectl -n bitwarden get service bitwarden
rg -n "bitwarden" kubernetes/system/cloudflared kubernetes/system/argocd-private-access
```

The Service must remain `ClusterIP`, and the search must find no Cloudflare or
private-LAN Gateway route.

Open the web vault, create the intended account, and record the master password
in a safe offline recovery location. Once all intended accounts exist, change
`globalSettings__disableUserRegistration` to `"true"` in
`kubernetes/system/bitwarden/config.yaml`, commit, and let ArgoCD reconcile.

Configure each browser, desktop, mobile, or CLI client to use the self-hosted
server URL before signing in:

```text
https://bitwarden.ghoul-slowworm.ts.net
```

Those clients must be connected to Tailscale when syncing. Cached vault data
remains usable offline according to the normal Bitwarden client behavior.

## 5. Import LastPass data

Prefer direct import because it avoids creating a plaintext CSV:

1. Connect the device to Tailscale and configure the Bitwarden browser
   extension or desktop app for the self-hosted URL above.
2. Sign in to the new Bitwarden account.
3. In the extension, open **Settings → Vault → Import items**. In the desktop
   app, open **Import**.
4. Select the individual vault or organization destination, choose
   **LastPass**, then choose **Import directly from LastPass**.
5. Enter the LastPass email and complete the master-password/SSO and MFA
   prompts. For Duo, the direct import supports in-app approval.

If direct import is unavailable, export from the LastPass web vault using
**Advanced Options → Export**, confirm the email, export again, and save the
CSV. In Bitwarden, use **Tools → Import**, select **LastPass (CSV)**, choose the
file, and import it exactly once. LastPass printed exports have sometimes
HTML-encoded special characters such as `&`; inspect and correct those values
before import.

Important limitations:

- Imports do not de-duplicate. Repeating an import creates duplicate items.
- LastPass file attachments and trash are not imported. Download important
  attachments securely and upload them to their corresponding Bitwarden items;
  re-create anything needed from trash.
- LastPass Sends must be re-created.
- A separate LastPass Authenticator account is not part of the password-vault
  CSV. Export it from LastPass Authenticator's **Settings → Transfer accounts**
  and import that JSON into Bitwarden Authenticator. TOTP secrets stored in
  ordinary LastPass vault records are handled by the password-vault importer.
- A free Bitwarden organization allows only two collections. Three or more
  distinct LastPass `grouping` values can make an organization import fail;
  import into the individual vault or remove/merge the grouping column first.

Before retiring LastPass:

1. Compare item and folder counts.
2. Test a varied sample of logins, secure notes, cards, identities, TOTP codes,
   and every manually copied attachment.
3. Confirm sync from at least two Bitwarden clients over Tailscale.
4. Run and verify a Bitwarden backup.
5. Delete any plaintext CSV securely, empty temporary downloads, and only then
   disable or delete the LastPass account according to its retention policy.

## 6. Backups and recovery

The nightly CronJob creates a PostgreSQL custom-format dump plus an archive of
`/etc/bitwarden`, retains them for 30 days, and writes them to:

```text
/mnt/storage2-bulk/bitwarden/backups
```

Trigger and inspect a backup after the initial import:

```bash
kubectl -n bitwarden create job --from=cronjob/bitwarden-backup bitwarden-backup-manual
kubectl -n bitwarden wait --for=condition=complete job/bitwarden-backup-manual --timeout=10m
kubectl -n bitwarden logs job/bitwarden-backup-manual
ssh kfir@192.168.1.105 'sudo find /mnt/storage2-bulk/bitwarden/backups -maxdepth 1 -type f -printf "%f %s bytes\n"'
```

These backups protect against many application and database mistakes, but they
are on the same host and filesystem as the source. They do not protect against
loss of `ubuntu-workstation` or its critical disk. Regularly copy the backup
set to an encrypted off-host destination and test a restore in an isolated
namespace. Keep the Kubernetes Secret in a separate encrypted backup as well.

For a restore, stop the Bitwarden Deployment, restore the matching app archive,
restore the PostgreSQL custom dump with `pg_restore`, verify ownerships
(`1000:1000` for app/backups and `999:999` for PostgreSQL), then start Bitwarden
and test `/alive`, login, attachment download, and client sync. Never restore
over the live database while Bitwarden is running.

## 7. Upgrades

Both application images and the Tailscale chart are pinned. Before changing a
version:

1. confirm a fresh backup exists;
2. review Bitwarden Lite and PostgreSQL release notes;
3. update the tag and digest together;
4. let ArgoCD perform the rollout;
5. verify login, sync, attachments, and a new backup.
