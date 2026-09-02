# Homelab Telegram Codex client

## Purpose and ownership

`homelab-assistant` is a private Telegram frontend for the normal local Codex
environment on `ubuntu-workstation`. Ansible owns both host services. Argo CD
owns the deterministic switcher identity, the retained legacy PostgreSQL
recovery volume, and a separately operated cluster-private local LLM in the
`homelab-assistant` namespace. The Telegram client does not depend on that LLM;
its API and recovery procedure are documented in the
[local LLM runbook](local-llm-runbook.md).

The runtime path is:

```text
Telegram private chat
  -> homelab-assistant container (UID 10001, locked after every restart)
  -> /run/homelab-codex/app-server.sock
  -> authenticated loopback WebSocket proxy
  -> codex app-server (user kfir, HOME=/home/kfir)
  -> /home/kfir/repos/homelab and the user's existing ~/.codex
```

App Server binds only an authenticated WebSocket on `127.0.0.1`. A host proxy
accepts WebSocket clients on the group-protected Unix socket, injects a
host-generated capability token, and relays opaque WebSocket frames to Codex.
The token is readable only by the two host services and is never passed through
Telegram or stored in the repository. The socket directory is
`2770 kfir:homelab-assistant`, the socket is `0660 kfir:homelab-assistant`, and
the bridge receives it as a read-only bind mount. The bridge does not
mount `/home/kfir`, `~/.codex`, `.env`, kubeconfig, or workstation SSH config.
Codex accesses those files only in its host process when an authorized task
genuinely needs them.

Every Telegram-originated turn sets `approvalPolicy="never"`,
`sandboxPolicy.type="dangerFullAccess"`, and the homelab repository cwd. The
repository `AGENTS.md` safety and ownership rules still apply. Filesystem access
is not permission to disclose credentials.

## Command boundaries

The only bridge-owned root namespaces are:

- `/tg ...` for authentication and thread controls.
- `/ops ...` for deterministic VM/Kubernetes transitions.

Root Codex commands never acquire custom bridge meanings. The client maps
`/status`, `/compact`, `/fork`, `/model`, and `/review` to stable App Server
requests. Any other root slash command returns a precise unsupported-client
error and is not sent to the model as text.

Transport controls are:

- `/tg help`
- `/tg sessions`
- `/tg current`
- `/tg new [title]`
- `/tg switch`
- `/tg stop`
- `/tg rename <title>`
- `/tg unlock`
- `/tg lock`

The administrator lease is in memory, expires automatically after 15 minutes
by default, and is always cleared by a bridge restart. Listing and selecting
thread metadata is allowed while locked. New threads, turns, compaction, forks,
renames, model changes, reviews, and `/ops` previews require the lease. `/tg
stop` remains available while a long turn is running.

`/tg sessions` calls `thread/list` with the exact cwd and the `cli`, `vscode`,
and `appServer` source kinds. Buttons contain only short opaque nonces. Nonces
are bound to the exact user and chat, expire, and are deleted atomically before
their action executes. Displayed metadata is limited to thread ID when needed,
name or preview, origin, runtime state, branch, and last activity.

The bridge accepts only the configured numeric Telegram user ID and numeric
private-chat ID. Groups, channels, mismatched callback actors, and forwarded
contexts are ignored before routing.

For a direct private chat, the allowed user ID and chat ID are the same
administrator ID. They are not the bot ID returned by Telegram `getMe`.
Deployment resolves `getMe` without logging the token or IDs and refuses a
self-targeting configuration. The bridge also records only non-sensitive
rejection reason classes (actor mismatch, chat mismatch, non-private chat, or
forwarded context) and never logs the rejected IDs or message text.

The Telegram token or private identity can be rotated without supplying or
rewriting the deterministic switching credentials:

```bash
cd ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml \
  --limit ubuntu-workstation --tags telegram-identity --ask-become-pass
```

If Codex is running but the bridge reports that App Server is unavailable,
repair only the authenticated Unix-to-loopback transport with:

```bash
cd ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml \
  --limit ubuntu-workstation --tags codex-transport --ask-become-pass
```

After CI publishes and the release pin is merged, deploy only a new immutable
bridge image with:

```bash
cd ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml \
  --limit ubuntu-workstation --tags assistant-image --ask-become-pass
```

## Codex status and streaming

`/status` combines sanitized values from `thread/read`, cached
`thread/tokenUsage/updated` notifications, and `account/rateLimits/read`. It
does not request or display account identity, email, auth mode, tokens, or raw
configuration.

`/compact` calls `thread/compact/start` and waits for the
`contextCompaction` item lifecycle. It never invokes the retired application
summary/child-session behavior. `/fork` calls `thread/fork` and persists the
new ID as the Telegram selection. `/model` uses `model/list` and resumes the
thread with the chosen protocol model override. `/review` currently supports
the stable uncommitted-changes review target.

Turn notifications are reduced to bounded Telegram updates: commentary/final
agent messages, plan changes, command completion, file-change counts, tool-call
state, compaction, failures, and interruption. Reasoning items, command output,
raw environment data, and tool payloads are not forwarded. A final redaction
boundary removes common credential assignments, bearer values, private keys,
and Telegram bot URLs; final messages are split below Telegram's limit.

One bridge process owns the single App Server connection. Telegram turns use a
per-thread lock. A Telegram follow-up can use `turn/steer` only when the bridge
knows the exact active turn ID. If another Codex client owns an active turn and
the bridge cannot safely identify it, the bridge refuses to start a competing
writer. `/tg stop` explicitly resolves the selected thread's current
`inProgress` turn ID through `thread/read` and interrupts only that exact turn.

## Deterministic operations

`/ops gaming` and `/ops k8s` never enter Codex and are never registered as
model tools. Both require an unlocked lease, a fresh one-use confirmation, the
literal `gpu-2` target, and the forced-command actuator on `largegpu`.

`/ops gaming`:

1. Read VM 402/502 state and refuse unknown or simultaneous-running state.
2. Require a ready Kubernetes API.
3. Cordon `gpu-2`.
4. Evict non-DaemonSet/non-mirror pods through `policy/v1` eviction, honoring
   PDB `429` responses until the bounded timeout; refuse unmanaged pods.
5. Invoke only `switch-to-gaming` over the forced SSH identity.
6. The actuator gracefully shuts down VM 402, waits for stop, starts VM 502,
   and leaves `gpu-2` cordoned.

`/ops k8s`:

1. Read and validate VM state.
2. Invoke only `switch-to-kubernetes`; the actuator gracefully shuts down VM
   502, waits for stop, then starts VM 402.
3. Wait for the API and `gpu-2` Ready condition.
4. Uncordon `gpu-2`.

The actuator lock, App Server thread locks, callback consumption, idempotent
already-in-mode behavior, graceful-only shutdown, and unexpected-state refusal
remain mandatory.

Telegram callback queries are acknowledged before a confirmed transition starts.
Acknowledgement, audit persistence, progress editing, and final-message delivery are
secondary reporting steps: each is attempted without retrying or reclassifying an
already-completed infrastructure transition.

## Secrets and local prerequisites

As `kfir`, verify the existing Codex installation without displaying auth:

```bash
command -v codex
codex login status
test -d /home/kfir/repos/homelab
```

Do not copy `auth.json`, `config.toml`, plugins, skills, sessions, `.env`,
kubeconfig, or SSH config into `/etc/homelab-assistant` or the container.

The Ansible controller needs these untracked values:

```text
HOMELAB_ASSISTANT_TELEGRAM_TOKEN
HOMELAB_ASSISTANT_TELEGRAM_ALLOWED_USER_ID
HOMELAB_ASSISTANT_TELEGRAM_ALLOWED_CHAT_ID
HOMELAB_ASSISTANT_KUBERNETES_SWITCH_TOKEN
HOMELAB_ASSISTANT_KUBERNETES_CA_SOURCE
HOMELAB_ASSISTANT_ACTUATOR_IDENTITY_SOURCE
HOMELAB_ASSISTANT_ACTUATOR_KNOWN_HOSTS_SOURCE
HOMELAB_ASSISTANT_ACTUATOR_AUTHORIZED_KEY
```

No cloud model key or separate diagnostic token is used. The deterministic
switcher token is generated by the Kubernetes service-account token Secret;
extract it once to a protected controller file without printing it:

```bash
install -d -m 0700 /secure/path/homelab-assistant
kubectl -n homelab-assistant get secret homelab-assistant-switcher-token \
  -o jsonpath='{.data.token}' | base64 -d \
  > /secure/path/homelab-assistant/kubernetes-switch-token
kubectl -n homelab-assistant get secret homelab-assistant-switcher-token \
  -o jsonpath='{.data.ca\.crt}' | base64 -d \
  > /secure/path/homelab-assistant/kubernetes-ca.crt
chmod 0600 /secure/path/homelab-assistant/*
```

## Static verification

From `services/homelab-assistant`:

```bash
uv sync --locked --extra dev
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy src tests
uv run --locked --extra dev pytest
```

From the repository root:

```bash
kubectl kustomize kubernetes/system/homelab-assistant >/tmp/homelab-assistant.yaml
cd ansible
ansible-playbook playbooks/configure-ubuntu-workstation.yml --syntax-check
ansible-playbook playbooks/configure-proxmox.yml --syntax-check
```

These checks do not contact the live cluster, restart services, sync Argo CD,
or change VM state.

## Cutover sequence

Never run the old and new Telegram pollers with the same bot token.

1. Merge and publish a tested immutable bridge image, then merge its image-pin
   release PR.
2. Sync `homelab-assistant` without prune. Confirm the retained PV/PVC and
   switcher identity are healthy. Extract the switcher token and CA as above.
3. Verify Codex is installed and authenticated as `kfir`. Do not inspect or
   export existing rollout contents.
4. Export the controller-only placeholders, then run both roles in check mode:

   ```bash
   cd ansible
   ansible-playbook playbooks/configure-proxmox.yml \
     --check --diff --limit largegpu --tags homelab-vm-actuator
   ansible-playbook playbooks/configure-ubuntu-workstation.yml \
     --check --diff --limit ubuntu-workstation --tags homelab-assistant
   ```

5. Apply the fixed-command actuator on `largegpu`. Verify only `status`,
   `switch-to-gaming`, and `switch-to-kubernetes` are accepted; do not exercise
   either switch as a deployment smoke test.

   ```bash
   ansible-playbook playbooks/configure-proxmox.yml \
     --limit largegpu --tags homelab-vm-actuator
   ```

6. Stop the legacy Kubernetes Telegram Deployment before starting the host
   bridge. Confirm it has no running pod. This is an explicit live cutover step.
7. Apply the workstation role:

   ```bash
   ansible-playbook playbooks/configure-ubuntu-workstation.yml \
     --limit ubuntu-workstation --tags homelab-assistant
   ```

8. Verify locally without printing environment or credentials:

   ```bash
   systemctl is-active homelab-codex-app-server homelab-assistant
   systemctl show homelab-codex-app-server -p User -p Group -p WorkingDirectory
   stat -c '%U %G %a %n' /run/homelab-codex /run/homelab-codex/app-server.sock
   podman inspect homelab-assistant --format '{{json .Mounts}}'
   ```

   The App Server unit must show user `kfir`, the repository cwd, and no TCP
   listener. Container mounts must not include `/home/kfir` or `~/.codex`.
9. In Telegram, confirm the initial state is locked. Exercise `/tg help`, `/tg
   sessions`, select a known VS Code thread, `/tg current`, `/status`, `/tg
   unlock`, `/tg new cutover-test`, and `/tg lock`. Cancel both `/ops` previews;
   do not confirm a VM transition merely as a smoke test.
10. Sync the Argo CD Application with prune only after the host bridge is proven.
   Prune retires the old Deployment/database/policies and the obsolete
   diagnostics identity. The `Retain` PostgreSQL PV/PVC remains inactive for
   rollback.

The legacy PostgreSQL-to-SQLite migration utility and old `sessions.db`, if
already created, are retained as migration history and rollback artifacts.
They are not imported into Codex: Codex threads already live in the normal
`~/.codex` store, and the revised bridge creates only `bridge.db` for selection,
nonces, and sanitized audit rows.

## Cross-client thread visibility

The supported direction is verified by the App Server contract: `thread/list`
can enumerate `vscode`, `cli`, and `appServer` sources, so Telegram can select
and resume VS Code and CLI threads by ID.

The reverse UI direction is not guaranteed by the documented interface. Do not
claim that every Telegram-created `appServer` thread automatically appears in
the stock VS Code Codex session list. The history remains in the shared Codex
store. Record the ID shown by `/tg current` and resume it safely on the
workstation with:

```bash
cd /home/kfir/repos/homelab
codex resume <thread-id>
```

This supported CLI workflow preserves the thread history even when the VS Code
picker does not surface the session.

## Rollback

1. Lock Telegram and stop `homelab-assistant.service`, then stop
   `homelab-codex-app-server.service`. Confirm no host poller remains.
2. Before reusing the bot token, restore the legacy Kubernetes manifests from a
   reviewed pre-cutover revision and verify the retained PostgreSQL PVC binds to
   its original `Retain` PV. Do not delete or recreate the backing directory.
3. Restore the encrypted legacy Secret material through the normal SOPS
   workflow. Never copy it into Git or command output.
4. Sync without prune, verify PostgreSQL and the old gateway, and only then
   scale the old Telegram poller to one replica.
5. Keep `bridge.db`, legacy `sessions.db`, and the retained PostgreSQL path until
   the rollback window is explicitly closed. These files contain private
   metadata/history and must not be attached to tickets or logs.

Rollback never requires deleting `~/.codex`; Telegram-created Codex histories
remain ordinary local threads and can still be resumed by ID.
