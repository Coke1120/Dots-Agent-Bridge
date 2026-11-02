# Moving Hermes, the bridge, or Auth0

These are manual operator procedures, not a migration command or an installer. No live
migration has been validated. Keep the current working installation until the checks below
pass. Perform one change at a time; do not combine a host move with an identity-provider
migration. See [automatic operation](automatic-operation.md) for service/trust setup and
its distinction between a verified manual reply and an automatic event round trip.

| Change | Expected effort | What must remain consistent |
| --- | --- | --- |
| Hermes computer only | Low if a secure reachable transport already exists; otherwise network setup is required | Resource URL, TLS trust, OAuth client/scopes and authorized subject |
| Bridge computer | Moderate, with a maintenance window | Database, encryption key, owner policy, resource and tunnel identity |
| Auth0 Dashboard administrator, same tenant | Usually low | Existing tenant, applications, API identifiers and app users |
| Auth0 application login user | Moderate authorization cutover | Verified new subject/client mapped deliberately to the existing owner |
| Entire Auth0 tenant | Most involved; reconnect both clients | New issuer/JWKS/clients, verified identity mappings and event subscription |

## Identity and history: what the code actually does

[JWTAuth](../bridge/auth.py) verifies one configured issuer, pinned JWKS, audience,
expiry and RS256 signature. [Policy.resolve](../bridge/auth.py) then matches the exact
verified `sub` and configured client claim (`azp` or `client_id`) against local grants.
The grant supplies a stable `owner` string; SQLite records belong to that owner, **not
directly to an email address or Auth0 subject**.

An operator can explicitly authorize a newly verified subject/client for the same owner
without rewriting SQLite. This transfers access to that owner's retained mailbox: verify
that this is intended, not merely that two accounts have the same email address. Keep the
Hermes and dot roles separate. The local runner requires exactly one enabled,
queue-authorized grant per role and a shared owner. Use an atomic policy-file replacement
with private permissions; do not temporarily enable extra identities or duplicate grants.

A different owner value makes previous records inaccessible through that grant. Never fix
this with blanket SQLite owner rewrites, disabled JWT validation or broader scopes. There
is no owner-transfer or multi-issuer migration helper. History is bounded: requests and
responses are removed seven days after request creation; requests also expire. Restoring
a backup does not reset these deadlines.

## 1. Move only Hermes; keep the bridge on Mac mini

The local runner binds to `127.0.0.1`. On a second computer,
`https://127.0.0.1:8765/mcp` points to **that second computer**, not Mac mini. Copying the
MCP YAML alone cannot connect it. OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
connects supported OpenAI surfaces; this repository has no Hermes adapter for directly
using its hosted endpoint. Do not substitute the tunnel URL and assume ordinary Hermes
OAuth will work.

For an existing authorized SSH connection to Mac mini, a manual loopback forward can
preserve the exact MCP URL. This is a standard network arrangement, not a tested repository
migration helper. Confirm the remote SSH host key and that local port 8765 is unused:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 '<ssh-user>@<bridge-host>'
```

Run this on the new Hermes computer. It forwards TCP only: HTTPS certificate verification
and bridge OAuth remain required. Do not enable SSH access/firewall rules implicitly;
if approved SSH access does not exist, stop and provision an authorized transport first.
Do not expose the local bridge on the LAN or disable TLS to make the example work.

1. Install Hermes independently using its supported instructions. Copy only the reviewed
   bridge MCP entry and `talk-to-dot` skill, not the entire old Hermes profile/token cache.
   The bridge Python venv and database stay on Mac mini.
2. Supply the same pre-registered Hermes OAuth client ID, `bridge:submit bridge:read offline_access`,
   timeout 60 and exact HTTPS resource. Keep the forwarding process running during use.
   Follow [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md).
3. Transfer only the approved public CA certificate needed to validate the bridge leaf;
   build process-local trust with public roots as described in the automatic guide. Do not
   transfer the server private key, CA signing key or webhook encryption key to Hermes.
4. Perform a fresh OAuth login on the new computer. The browser and local OAuth redirect
   listener must be on that computer; retain the registered callback host/port (the example
   uses `localhost:27890`). Do not copy refresh tokens or paste secrets into chat.
5. Confirm a harmless authenticated `list_requests` and a known retained request ID. If the
   verified subject/client is unchanged, no bridge policy change is needed. If it changed,
   follow section 3. Test one synthetic message and its actual stored reply before retiring
   the old Hermes installation. Do not submit the same task twice from both computers.

The bridge host still needs to be awake, running its bridge and tunnel. An SSH forward
adds another process whose failure disconnects Hermes; persistence for it is not supplied.

## 2. Move the bridge host

Record the code commit, Python/dependency versions, configuration paths and service labels
privately. Keep the same code revision during migration; recreate the isolated venv rather
than copying it between machines. Do not install bridge dependencies into Hermes.

### Quiesce and back up

Pause the event handler through its supported controls, stop new Hermes submissions and
allow in-flight work to settle. Stop/unload the old bridge and tunnel LaunchAgents (or stop
foreground processes); ensure KeepAlive cannot restart them. Confirm no bridge process
holds the database lock. This is a maintenance window, not a zero-downtime failover.

Back up the configured database using SQLite's [backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup),
which includes committed WAL data. Do not copy only a live `.sqlite` file. The following
manual example requires an existing mode-700 backup directory and refuses to overwrite a
backup. Replace paths locally; it prints no records or credentials:

```sh
python3 - /PRIVATE/bridge/bridge.sqlite /PRIVATE/backup/bridge.sqlite <<'PY'
import os
from pathlib import Path
import sqlite3
import sys
from contextlib import closing

source, destination = map(Path, sys.argv[1:])
source = source.resolve(strict=True)
if destination.parent.stat().st_mode & 0o077:
    raise SystemExit("Backup directory must be private (mode 700)")
fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.close(fd)
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise SystemExit("Backup integrity check failed; do not restore")
print("Backup integrity check passed")
PY
```

Also preserve `config.json`, the referenced policy and **original Fernet encryption key**,
TLS leaf key/certificate and public trust chain, and the reviewed tunnel profile. Relative
config paths resolve beside the config file. Secrets may also reside outside this folder;
inventory referenced locations without printing values. Without the Fernet key, existing
subscription signing secrets cannot be decrypted. SQLite message content itself is not
encrypted by that key: use encrypted backup storage and an authenticated encrypted transfer.
Do not commit state/secrets or copy a CA signing key to a runtime host.

### Restore and cut over

1. On the new host, create an owned private state directory (700). Restore the verified
   backup to a **new database path**, not over an active database or stale WAL/SHM files.
   Restore the matched config/policy/encryption key; set owned regular private files to 600.
   Retain the original database snapshot untouched for rollback.
2. Adjust absolute file paths and service executable paths. Keep the exact resource URL,
   stable owner and tunnel identity where supported. Loopback TLS needs IP SAN `127.0.0.1`
   and a valid trusted chain; provision or securely transfer the approved leaf key. Validate
   process-local trust on both Hermes and tunnel-client. Never restore a plaintext exception
   or disable certificate checks. Changes to resource/audience require client reauthorization.
3. Install the same reviewed revision and locked dependencies into a new isolated venv.
   Run these existing commands (no migration helper is invoked):

   ```sh
   /path/to/bridge-venv/bin/hermes-dots-bridge check --require-tunnel --config /PRIVATE/bridge/config.json
   /path/to/bridge-venv/bin/hermes-dots-bridge status --config /PRIVATE/bridge/config.json
   ```

4. Start exactly one bridge and one tunnel runner. Keep the old runners stopped; database
   copies on two hosts cannot enforce a shared process lock. Configure the existing tunnel
   through supported controls, with an approved unexpired runtime credential; don't create
   another tunnel merely because the machine changed. If its identity must change, update
   the pinned tunnel audience and ChatGPT connection through the supported setup flow.
5. Regenerate and inspect the disabled LaunchAgent templates with the commands in the
   automatic guide. Do not copy old absolute paths/UIDs or assume copying a plist activates
   it. Registration and credential transfer/rotation remain explicit operator actions.
6. Verify TLS, public resource metadata, unauthenticated MCP rejection and authenticated
   owner reads. A successful `check` is local validation, not proof of network/OAuth access.
   Check known retained IDs, subscription expiry and delivery state; resume the intended
   handler and confirm subscription refresh, then run the synthetic automatic acceptance
   test. Existing subscriptions are time-limited and may require a supported refresh or
   recreation; restored outbox entries must not be interpreted as completed replies.

### Rollback

Stop the destination bridge/tunnel and pause the handler before starting the source again.
If the destination accepted writes, the original snapshot is stale: first back up the stopped
destination and reconcile the cutover explicitly. Do not silently discard new messages or
merge SQLite files. A stale restore can replay deliveries and lose response/idempotency
records. Keep one authoritative database. Recheck OAuth grants, tunnel ownership and TLS
trust when reversing configuration changes; revoked credentials do not revive on restore.
After verified cutover, retire old credentials/services and protected backups under the
operator's retention policy. Do not delete the only rollback copy prematurely.

## 3. Change Auth0 account or tenant

### Different Dashboard administrator, same tenant

Auth0 [Dashboard membership](https://auth0.com/docs/get-started/manage-dashboard-access)
is distinct from an application user's OAuth login. Transfer management access using
supported tenant-member controls, verify the new administrator can manage the existing
tenant and preserve recovery access before removing the former administrator. If tenant,
clients, API identifiers and application users stay unchanged, bridge issuer/policy and
mailbox ownership need no change. This guide performs no account or billing transfer.

### Different application login user, same tenant

After an authorized fresh OAuth login, establish the exact verified subject and client ID
through the provider's supported administration/authentication flow. Do not infer `sub`
from an email or an unverified token. Back up the private policy, then replace the relevant
grant with the newly authorized subject/client while retaining the intended owner, role
and narrow scopes. Disable the former grant; validate the local configuration before use.
This is an explicit access transfer to retained history, not automatic account linking.

If only Hermes changes identity, the dot subscription identity can remain unchanged.
If the dot subject/client changes, recreate or refresh its event handler through the
supported platform workflow and retire the former subscription. Subscription IDs bind
subject, client, owner and callback parameters; delivery rechecks the original grant.
Keeping the owner alone does not rebind old subscriptions or pending deliveries. Inspect
pending requests manually; new subscriptions do not backfill old requests. Preserve request
IDs and do not resubmit tasks blindly to manufacture a wakeup.

### Entire new tenant

Auth0 [tenants](https://auth0.com/docs/get-started/auth0-overview/create-tenants) contain
separate applications and user profiles. Prepare new Hermes and dot clients, exact callback
registrations, PKCE S256, required scopes and provider settings from
[connection setup](connection.md). Then perform a planned outage and change:

- `issuer`, `jwks_url` and, if necessary, `client_claim`;
- both OAuth client registrations/configurations and verified policy subject/client pairs;
- ChatGPT's supported OAuth connection configuration and fresh Hermes/dot authorizations;
- event subscription/refresh setup, using the verified callback allowlist.

The API resource/audience **does not inherently have to change** when the tenant changes:
register the same exact resource identifier in the new tenant if it remains the intended
resource. Keep the separate tunnel audience only if still valid. Never accept the old HTTP
audience as a workaround for an HTTPS resource. If identifiers do change, update all
corresponding resource metadata/client registrations and reauthorize consistently.

Keep the original owner for an explicitly approved transfer of retained history. The
server supports one issuer at a time, not simultaneous old/new tenant trust. Replace the
complete identity policy at cutover; do not leave old-issuer mappings to accidentally match
new-issuer subjects. Old tokens/refresh tokens are not portable. A UI connection that
silently restores cached credentials is not proof of reauthorization: verify an actual
read under the new issuer/client and use supported connection controls or report the
platform blocker. Never bypass audience checks to preserve an old connection.

Finish with the same harmless retained-ID read and synthetic automatic round trip. Only
then revoke/retire old credentials and tenant resources as separately authorized. There is
no automated tenant, user-data, subscription or credential migration in this repository.
