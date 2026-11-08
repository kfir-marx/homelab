# Bitwarden password-vault runbook

This deployment runs the official Bitwarden Lite image with PostgreSQL. Lite is
Bitwarden's single-container distribution for individuals and homelabs; it is
used instead of the much larger multi-service Helm/MSSQL deployment.

The canonical client endpoint is public through the existing outbound-only
Cloudflare Tunnel:

```text
https://bitwarden.547600.xyz
```

The Service remains `ClusterIP`: Cloudflare terminates public HTTPS and reaches
`bitwarden.bitwarden.svc.cluster.local:8080` through `cloudflared`, with no
inbound home-router port forward. The existing private access route remains
`https://bitwarden.home.547600.xyz`; AdGuard resolves it to the private Gateway
VIP `192.168.1.220`, reachable on the LAN or through the existing Tailscale
subnet router. cert-manager provides that private Gateway's wildcard
certificate with a Cloudflare DNS-01 challenge.

Bitwarden vault records, attachments, identity material, PostgreSQL, and local
logical backups use retained static PVs on the critical NFS export at
`ubuntu-workstation:/mnt/storage2-bulk`.

Upstream references:

- [Bitwarden Lite deployment](https://bitwarden.com/help/install-and-deploy-lite/)
- [Bitwarden database options](https://bitwarden.com/help/database-options/)
- [cert-manager Cloudflare DNS-01](https://cert-manager.io/docs/configuration/acme/dns01/cloudflare/)
- [Cilium Gateway API](https://docs.cilium.io/en/stable/network/servicemesh/gateway-api/gateway-api/)
- [Bitwarden LastPass import](https://bitwarden.com/help/import-from-lastpass/)

## 1. Prepare HTTPS and public access

No new Tailscale tag, OAuth client, or operator is needed. The existing
`tailscale-router` continues to advertise `192.168.1.0/24`; existing tailnet
grants that allow the private Gateway apply to Bitwarden as they do to ArgoCD
and the other private services.

Replace `REPLACE_WITH_ACME_EMAIL` in
`kubernetes/system/cert-manager/clusterissuer.yaml` with the email address for
Let's Encrypt expiry and account notices.

In Cloudflare, create a scoped API token for the `547600.xyz` zone with:

- **Zone → DNS → Edit**;
- **Zone → Zone → Read**;
- zone resource restricted to `547600.xyz`.

Create the token Secret out-of-band before cert-manager syncs:

```bash
kubectl create namespace cert-manager --dry-run=client -o yaml | kubectl apply -f -
read -r -s -p "Cloudflare DNS API token: " cloudflare_dns_api_token
printf '\n'
kubectl -n cert-manager create secret generic cloudflare-dns-api-token \
  --from-literal=api-token="$cloudflare_dns_api_token"
unset cloudflare_dns_api_token
```

The shell history contains the variable name, not the token value. Do not run
the command with shell tracing enabled. This token is only for ACME TXT record
updates; do not reuse a Cloudflare Tunnel token or a global API key.

Capture it into the encrypted recovery bundle after creation:

```bash
scripts/secrets.sh capture-k8s cert-manager/cloudflare-dns-api-token
```

cert-manager issues `*.home.547600.xyz` into the
`argocd/private-home-wildcard-tls` Secret. The shared Gateway terminates HTTPS
on port 443. The existing AdGuard wildcard already maps the hostname to
`192.168.1.220`, so no public A or CNAME record is required.

After the manifests have reconciled, open the existing tunnel in Cloudflare
Zero Trust and add this public hostname:

| Public hostname | Service URL |
|---|---|
| `bitwarden.547600.xyz` | `http://bitwarden.bitwarden.svc.cluster.local:8080` |

Let the tunnel create the proxied DNS record. Do not enable Cloudflare Access:
native Bitwarden clients cannot complete its browser redirect. Require MFA on
each Bitwarden account, keep public registration disabled, and do not create a
router port forward. Cloudflare WAF or rate limiting may be applied only if it
does not challenge or block Bitwarden API and notification traffic.

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
the same Bitwarden cloud region used by any self-hosted license, then store
them in the repository-root `.env`:

```dotenv
BITWARDEN_INSTALLATION_ID="your-installation-id"
BITWARDEN_INSTALLATION_KEY="your-installation-key"
```

Create the namespace and secret without committing any value to Git:

```bash
kubectl create namespace bitwarden --dry-run=client -o yaml | kubectl apply -f -
. ./.env
bitwarden_db_password="$(openssl rand -base64 48 | tr -d '\n')"
bitwarden_internal_key="$(openssl rand -hex 32)"
bitwarden_oidc_key="$(openssl rand -hex 32)"
bitwarden_duo_key="$(openssl rand -hex 32)"
bitwarden_identity_password="$(openssl rand -base64 48 | tr -d '\n')"
printf '%s=%s\n' \
  BW_INSTALLATION_ID "$BITWARDEN_INSTALLATION_ID" \
  BW_INSTALLATION_KEY "$BITWARDEN_INSTALLATION_KEY" \
  BW_DB_PASSWORD "$bitwarden_db_password" \
  globalSettings__internalIdentityKey "$bitwarden_internal_key" \
  globalSettings__oidcIdentityClientKey "$bitwarden_oidc_key" \
  globalSettings__duo__aKey "$bitwarden_duo_key" \
  globalSettings__identityServer__certificatePassword "$bitwarden_identity_password" | \
  kubectl -n bitwarden create secret generic bitwarden-secrets \
    --from-env-file=/dev/stdin
unset BITWARDEN_INSTALLATION_ID BITWARDEN_INSTALLATION_KEY \
  bitwarden_db_password bitwarden_internal_key bitwarden_oidc_key \
  bitwarden_duo_key bitwarden_identity_password
```

This is intentionally a create-only command. If `bitwarden-secrets` already
exists, stop and investigate instead of regenerating it: rotating
`BW_DB_PASSWORD` without coordinating PostgreSQL would break database access.

Capture this Secret with:

```bash
scripts/secrets.sh capture-k8s bitwarden/bitwarden-secrets
```

It lives in Kubernetes etcd by necessity; do not copy it as plaintext onto NFS
or into this repository. Only the SOPS ciphertext belongs in Git.

SMTP is required for a complete self-hosted deployment, including account email
verification, new-device verification, invitations, and System Administrator
Portal login. Gmail's host, port, SSL, STARTTLS, and certificate-validation
settings are checked into `bitwarden-config`. The Gmail address and App
Password stay in a separate `bitwarden-smtp` Secret so they can be rotated
without replacing Bitwarden's installation and database credentials.

Populate these two variables in the repository-root `.env`:

```dotenv
GOOGLE_HOMELAB_GMAIL="your-homelab-account@gmail.com"
GOOGLE_APP_PASSWORD_FOR_SMTP_SERVER="your-google-app-password"
```

Create or update the SMTP Secret without printing either value:

```bash
set -a
. ./.env
set +a
smtp_app_password="$(printf '%s' "$GOOGLE_APP_PASSWORD_FOR_SMTP_SERVER" | tr -d '[:space:]')"
kubectl create namespace bitwarden --dry-run=client -o yaml | kubectl apply -f -
kubectl -n bitwarden create secret generic bitwarden-smtp \
  --from-literal=globalSettings__mail__replyToEmail="$GOOGLE_HOMELAB_GMAIL" \
  --from-literal=globalSettings__mail__smtp__username="$GOOGLE_HOMELAB_GMAIL" \
  --from-literal=globalSettings__mail__smtp__password="$smtp_app_password" \
  --dry-run=client -o yaml | \
  kubectl apply --server-side --field-manager=bitwarden-smtp-bootstrap -f -
unset GOOGLE_HOMELAB_GMAIL GOOGLE_APP_PASSWORD_FOR_SMTP_SERVER smtp_app_password
```

Do not commit SMTP credentials or print the generated Secret. Google displays
App Passwords in groups; the command removes display whitespace before storing
the 16-character password.

Capture the environment and SMTP Secret after creating or rotating them:

```bash
scripts/secrets.sh capture-env
scripts/secrets.sh capture-k8s bitwarden/bitwarden-smtp
```

The Bitwarden Lite Admin process must remain enabled because it owns database
initialization and migrations. Portal login is controlled separately: with no
`adminSettings__admins` value, no address is authorized to log in. To use the
System Administrator Portal, first configure SMTP and then add an explicit
comma-separated `adminSettings__admins` email allow-list to the out-of-band
`bitwarden-smtp` Secret.

## 4. Deploy and verify

Commit and push the manifests. The root ArgoCD Application discovers the
cert-manager and Bitwarden child Applications and updates the existing private
Gateway Application. To bootstrap the new child Applications manually without
applying workload resources directly:

```bash
kubectl apply -f kubernetes/apps/cert-manager.yaml
kubectl apply -f kubernetes/apps/bitwarden.yaml
```

Wait for certificate issuance, storage, database, and Bitwarden:

```bash
kubectl -n cert-manager rollout status deployment/cert-manager --timeout=5m
kubectl get clusterissuer letsencrypt-cloudflare
kubectl -n argocd get certificate private-home-wildcard
kubectl -n argocd get gateway private
kubectl -n bitwarden get pvc
kubectl -n bitwarden rollout status statefulset/bitwarden-postgres --timeout=10m
kubectl -n bitwarden rollout status deployment/bitwarden --timeout=10m
kubectl -n bitwarden get httproute
```

On initial deployment and after upgrades, confirm that the Admin process
completed the database migration before using the vault:

```bash
kubectl -n bitwarden exec deployment/bitwarden -- \
  sh -c 'grep -Ei "migrat|database" /etc/bitwarden/logs/admin-*.log | tail -50'
kubectl -n bitwarden exec statefulset/bitwarden-postgres -- \
  psql -U bitwarden -d bitwarden_vault -X \
  -c "SELECT to_regclass('\"User\"'), to_regclass('\"OrganizationDomain\"');"
```

Both table names must be returned. An empty result means the schema migration
did not complete; inspect the Admin log instead of creating tables manually.

The `ClusterIssuer` and `Certificate` must report `Ready=True`, and the Gateway
must report an accepted HTTPS listener. Test the private route from the LAN
and then from a tailnet device away from home:

```bash
curl --fail --show-error https://bitwarden.home.547600.xyz/alive
```

After creating the Cloudflare hostname, test the canonical endpoint from a
device that is not on the LAN or tailnet:

```bash
curl --fail --show-error https://bitwarden.547600.xyz/alive
```

Also verify the exposure model:

```bash
kubectl -n bitwarden get service bitwarden
rg -n "bitwarden" kubernetes/system/cloudflared
```

The Service must remain `ClusterIP`, and the search must find only the scoped
Cloudflared-to-Bitwarden TCP/8080 policy. The private `HTTPRoute` remains
attached to the shared Gateway; no public LoadBalancer or NodePort is needed.

Before adding the public Cloudflare hostname, create any intended accounts
through the private route and record the master password in a safe offline
recovery location. The checked-in configuration keeps
`globalSettings__disableUserRegistration` set to `"true"`; temporarily opening
registration requires a deliberate private-only maintenance change.

Configure each browser, desktop, mobile, or CLI client to use the self-hosted
server URL before signing in:

```text
https://bitwarden.547600.xyz
```

The public endpoint permits syncing without Tailscale. The private hostname
remains available as a separate LAN/tailnet access path.

## 5. Import LastPass data

Prefer direct import because it avoids creating a plaintext CSV:

1. Configure the Bitwarden browser extension or desktop app for the self-hosted
   public URL above.
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
3. Confirm sync from one client outside the tailnet through the public hostname
   and from a second client through the private LAN/tailnet path.
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

The application images and cert-manager chart are pinned. Before changing a
version:

1. confirm a fresh backup exists;
2. review Bitwarden Lite and PostgreSQL release notes;
3. update the tag and digest together;
4. let ArgoCD perform the rollout;
5. verify login, sync, attachments, and a new backup.
