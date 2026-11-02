> For ongoing automatic operation, use [the updated guide](automatic-operation.md). It adds native loopback HTTPS, bounded waits, status, disabled service templates and the official event-automation workflow. Older HTTP foreground examples below are not the persistent-service default. Generated LaunchAgents now include `Disabled=true`; enabling them requires explicit local approval.

# macOS local service + Hermes MCP integration

**Current live-test target: Mac mini Hermes**, superseding the earlier MBA choice. Follow [the staged Secure MCP Tunnel handoff](tunnel-handoff.md) for action-time confirmations and the synthetic acceptance test.

This is an installable Python service with a supported Hermes `mcp_servers` entry. It behaves like an extra tool integration in Hermes; it does not modify Hermes core or register a native `hermes plugins install` package. No installation, launchd registration, network change, login or tunnel creation has been performed on your Mac.

## Architecture and the UI boundary

Hermes on the selected Mac → OAuth-authenticated loopback MCP service → persistent mailbox + outbound signed event → existing ChatGPT dot → authenticated read/respond over the selected tunnel → mailbox → Hermes polls and displays the answer in its current turn.

ChatGPT cannot directly reach your Mac's `127.0.0.1`. The preferred private connectivity candidate is official **Secure MCP Tunnel**, which forwards calls through an outbound connection. The authorization server must remain reachable for browser login. Events travel separately as outbound HTTPS webhooks from the Mac to the verified ChatGPT receiver. A sleeping/offline Mac cannot deliver or answer calls; work resumes on wake while request/subscription lifetimes remain valid.

Hermes retrieves replies with `get_request`/`list_responses`. This does **not** inject a new message into an idle native Hermes CLI/Desktop conversation or automatically start a Hermes turn. Ask Hermes to check results, or have the active turn poll within a reasonable timeout. Autonomous UI notification/injection needs a separately verified Hermes interface and is not implemented.

## 1. Install the reviewed code locally (user action)

Use Python 3.12+ on the selected Mac and the reviewed PR branch/commit. These commands do not enable a service or connect accounts:

```sh
git clone --branch main https://github.com/Coke1120/hermes-dots-bridge.git
cd hermes-dots-bridge
# Use a supported Python >=3.12, in a dedicated bridge venv (not Hermes' venv).
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/hermes-dots-bridge init --directory "$HOME/Library/Application Support/hermes-dots-bridge"
```

`init` only creates disabled `config.json` and `policy.json` templates, with private permissions. It refuses an existing destination. It does not generate credentials, edit Hermes, run a shell script, install a tunnel or contact any server. Keep this checkout/venv in a permanent location if using launchd.

## 2. Fill local settings after authorization

In `~/Library/Application Support/hermes-dots-bridge/config.json`:

- Keep `resource` as `http://127.0.0.1:8765/mcp` (or another unprivileged explicit port). The local launcher always binds to **127.0.0.1**, never LAN/all interfaces. Only this explicit local launcher accepts a loopback HTTP resource; the regular production factory still requires HTTPS.
- Fill the external OAuth `issuer`, pinned `jwks_url`, and selected signed `client_claim` (`azp` or `client_id`). See [provider setup](connection.md). Loopback does not remove token verification, consent, PKCE or role separation.
- Place the persistent Fernet encryption key locally in `signing-secrets.key`, mode 600, using the chosen secret-storage workflow. Never paste it into chat, a plist, a command argument or the repository. All examples use placeholders; no real key is supplied.
- Fill the two policy grants using verified subject and client IDs. Explicitly authorize the queue before enabling them. Both roles map to one owner. Never derive these IDs or grants from message content.
- Fill `callback_hosts` with independently verified exact ChatGPT webhook hostnames; do not add wildcard/private destinations.
- Set `tunnel_resource` only to the **exact HTTPS resource URL shown by verified tunnel OAuth discovery**. It is not a tunnel ID and no URL shape is guessed here. Null is permitted for local-only validation, but then the ChatGPT tunnel path is not ready.

The provider must accept the loopback resource for Hermes and the exact rewritten tunnel resource for ChatGPT (for example, separate API resource registrations with the same bridge scopes). The verifier accepts only these explicitly configured audiences, not arbitrary tunnel URLs. If your provider rejects loopback API identifiers, use the stable HTTPS alternative below instead of weakening audience checks.

Keep config, policy, encryption key and existing database owned by your user and mode 600; their state directory must be mode 700. `check` is read-only and offline:

```sh
.venv/bin/hermes-dots-bridge check --config "$HOME/Library/Application Support/hermes-dots-bridge/config.json"
```

It validates syntax, placeholders, file permissions, encryption-key format and explicit grants. A passing check does not prove OAuth login or the tunnel works. It reports an unset tunnel audience as pending.

## 3. Start/stop in a terminal first

```sh
.venv/bin/hermes-dots-bridge serve --config "$HOME/Library/Application Support/hermes-dots-bridge/config.json"
```

Stop with Ctrl-C in that terminal. A database lock rejects a second launcher instance. SQLite survives restart; do not run the legacy uvicorn command against the same database simultaneously. The local runner disables proxy-header trust/access logs and limits concurrent connections. Its public metadata endpoint remains unauthenticated for OAuth discovery; MCP calls still require OAuth.

## 4. Configure official Secure MCP Tunnel (separate user-controlled step)

Sources: [OpenAI guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [official client](https://github.com/openai/tunnel-client), [OAuth forwarding behavior](https://github.com/openai/tunnel-client/blob/main/docs/configuration.md#mcp-server).

The current official macOS installation path is `brew install openai/tools/tunnel-client`. Do not run it as part of bridge setup automatically. The user must first choose/authorize the Platform organization and ChatGPT workspace, create or select a tunnel, and supply a restricted runtime credential locally. Creating a tunnel requires Tunnels Read + Manage; running/selecting it requires Tunnels Read + Use. A bridge OAuth token and tunnel runtime credential are different credentials.

After installation, use `tunnel-client help quickstart` and its guided configuration. Select the existing HTTP server `http://127.0.0.1:8765/mcp`; do not select an embedded unauthenticated demo or expose the raw `hermes mcp-serve` stdio service. Keep normal TLS validation. Configure `mcp.oauth_trusted_origins` with only the verified external issuer's HTTPS origin, because it differs from the local MCP origin. Store runtime credentials through the client's supported `file:`/`env:` references, not literals in a shared profile. Use `tunnel-client doctor --profile NAME --explain` before starting it.

The official client rewrites protected-resource `resource` and `WWW-Authenticate resource_metadata` URLs to tunnel-service URLs and forwards the incoming Authorization header. Therefore inspect the resulting metadata, register that exact resource at the IdP, and set the same `tunnel_resource` in the bridge. Do not assume a token for `http://127.0.0.1:8765/mcp` will be valid for the tunneled resource, or disable audience validation to make login work.

In ChatGPT Plugins, add a custom MCP server with Connection: Tunnel, choose the authorized tunnel/workspace and configure OAuth. Complete the existing-dot subscription workflow in [connection.md](connection.md). Both tunnel and bridge must remain running. Actual OAuth-through-tunnel, MCP Events and dot support on your account are **awaiting connection validation**, not established by these packaging tests.

## 5. Add the Hermes integration

Print, review, then manually merge this stanza into `~/.hermes/config.yaml` (do not overwrite the whole file):

```sh
.venv/bin/hermes-dots-bridge print-hermes --config "$HOME/Library/Application Support/hermes-dots-bridge/config.json"
```

Set `BRIDGE_HERMES_CLIENT_ID` locally. Register `http://localhost:27890/callback` for this public PKCE client and authorize `bridge:submit bridge:read offline_access` for client-managed renewal. The provider must allow offline access and the refresh grant; see [renewal setup](connection.md). Use the explicitly selected profile's supported login command from the automatic-operation guide, then reload MCP in that profile. The supplied entry is only the bridge; it does not expose approvals or general Hermes execution tools to dot.

First test with synthetic text. Ask Hermes to submit it, retain the request/conversation IDs and poll for a stored response. Then test a follow-up and cancellation. The dot needs its own distinct client grant with `bridge:read bridge:respond bridge:subscribe offline_access` for renewal.

## Optional: start bridge at user login

Only after foreground operation works, generate a **reviewable** plist. This command prints XML and registers nothing:

```sh
.venv/bin/hermes-dots-bridge print-launchd --config "$HOME/Library/Application Support/hermes-dots-bridge/config.json" > /tmp/com.hermes-dots-bridge.service.plist
plutil -lint /tmp/com.hermes-dots-bridge.service.plist
```

It contains absolute Python/config paths with proper XML escaping and no secrets, shell commands or tunnel startup. It runs in the logged-in user's context. `KeepAlive` is false so bad configuration does not create a restart loop. Review it, then explicitly install/enable if desired:

```sh
mkdir -p "$HOME/Library/LaunchAgents"
# Inspect an existing plist before replacing it; do not overwrite another installation.
cp -n /tmp/com.hermes-dots-bridge.service.plist "$HOME/Library/LaunchAgents/com.hermes-dots-bridge.service.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.hermes-dots-bridge.service.plist"
launchctl print "gui/$(id -u)/com.hermes-dots-bridge.service"
```

Stop/disable: `launchctl bootout "gui/$(id -u)/com.hermes-dots-bridge.service"`. To start again, use the bootstrap command after correcting any configuration problem. Foreground mode and launchd must not run simultaneously. Delete only the reviewed plist if uninstalling; keep the private state directory until its retention/backup decision is made. Bridge logs go to `service.log` in that private state directory; rotate them locally. Manage the tunnel separately with its documented lifecycle, never with a second competing supervisor.

## Stable public HTTPS alternative

If Secure MCP Tunnel is unavailable or the two-resource OAuth flow is incompatible with your provider, keep the service on the selected Mac but use an operator-controlled **fixed HTTPS domain** forwarding only to the loopback bridge. Configure the existing HTTPS production factory and [server environment](../examples/server.env.example); use the same canonical HTTPS resource for both clients and [HTTPS Hermes template](../examples/hermes.config.example.yaml). This requires an explicitly approved ingress/tunnel and external OAuth setup; the local loopback CLI is not used in this mode. No random public quick-tunnel, auth bypass, TLS bypass, port forwarding or firewall change is installed here.

Remaining decisions: external OAuth provider/tenant, approved tunnel/workspace or fixed HTTPS domain, exact resource audiences and verified callback hostnames, private key storage, and whether login startup is wanted. Mac availability determines bridge availability.
