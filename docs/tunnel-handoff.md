# Secure MCP Tunnel handoff — Mac mini Hermes test

**Active target: the user's Mac mini running Hermes.** This replaces the earlier MBA test target. Cloud work prepares code and instructions only; the separate Mac mini task owns local preflight/installation. Do not duplicate its work or use another machine without new direction.

Status: implemented, awaiting connection validation. No real tunnel, key, OAuth client/grant, subscription or local service was created by this Cloud task.

## What is known, and what is not

Checked against the [official guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and official `openai/tunnel-client` source commit `f745672195e788073ab0b104644138cdd2e4fd61` (2026-11-02):

- macOS installation is `brew install openai/tools/tunnel-client`; the supported formula includes its matching companion components. Do not bypass Gatekeeper for downloaded ZIP binaries.
- The runtime requires a tunnel ID, a restricted runtime API credential and an HTTP/stdio MCP target. We select the authenticated loopback bridge, not an embedded demo or `hermes mcp-serve`.
- Tunnel creation/management and runtime use have different permissions. Read + Manage is needed to create a tunnel; Read + Use is needed for the runtime and ChatGPT connector principal. Workspace association and custom-MCP policy also matter. An existing ChatGPT subscription does not establish these permissions, and no specific plan/entitlement is assumed here.
- OAuth metadata and Authorization headers can traverse the tunnel. Protected-resource identifiers are rewritten; the bridge therefore accepts its exact local audience plus one separately verified tunnel audience.
- A Tunnel runtime credential authenticates the tunnel transport. **It does not issue this bridge's end-user OAuth tokens or grant a Hermes/dot role.** This implementation still requires an external OAuth authorization server. An existing suitable IdP is fine; a new Auth0 tenant is not mandatory. Anonymous upstream MCP is not an allowed fallback for this project.
- MCP Events delivery is an outgoing HTTPS POST from the bridge to ChatGPT's callback, separate from tunneled inbound MCP calls. There is no inbound webhook port to open on Mac mini.

Local preflight reported Hermes `c7c7e0fa92f` / `v0.22.5+canary.20261101T064348Z-25`, Python 3.14.9 and MCP SDK 2.3.0, no MCP entries, tunnel-client absent, and clean bridge checkout at `3caf24c`. That preflight installed no dependencies. Keep bridge in its own venv; never install either bridge test lockfile into Hermes or downgrade Hermes. Cloud independently tested SDK 1.30.0 and 2.3.0 in separate Python 3.12 environments; each passed all 81 cases. The server still negotiates MCP `2025-11-25`; SDK major version is not a wire-protocol requirement. CI also exercises Python 3.14.

Unverified in this session: Platform organization/ChatGPT workspace entitlement, an existing usable tunnel, the chosen IdP and its resource/client registrations, the runtime-derived tunnel audience, callback hostname, and real dot event handling. The smallest missing **choice** is which external OAuth provider/tenant to use or reuse. Do not ask the user to send secrets in chat.

## Action-time approval bundle

The user's intent authorizes preparation and an attempt at connection. Before each persistent account/security action, show its concrete target and requested settings locally and obtain the required action-time confirmation. Prefer reusing suitable existing resources. This table is the reviewable scope, not evidence that a row has been executed.

| Stage | Exact action / target | Data and persistence | Confirmation boundary |
| --- | --- | --- | --- |
| A | Inspect Mac mini: machine identity, Hermes/Python/Homebrew/tunnel-client versions, selected profile, ports 8765/8080 and existing bridge paths | Read-only; no tokens or conversation contents in output | Authorized local preflight once access is available |
| B | Install official Homebrew tunnel-client if missing; install this reviewed Python package into an isolated venv; create disabled bridge templates in a new private directory | Software, config templates and package caches persist locally; no login or service registration | Confirm exact paths/package versions before installation in the local task |
| C | Select existing Platform tunnel or create one named `hermes-dots-bridge-test`, associated only with the verified organization and ChatGPT workspace | Persistent tunnel record and access scope; no other users/workspaces | Confirm actual IDs and reuse/create choice before mutation; do not broaden roles automatically |
| D | User creates/selects restricted runtime key with Tunnels Read + Use, stored via local protected file reference | Local key persists; OpenAI receives it for runtime authentication | User login/key creation locally; never request or print literal key; no admin key in runtime |
| E | Configure the chosen external IdP: local/tunnel API resource identifiers, four scopes, two distinct user-facing OAuth clients, verified redirects, S256 PKCE | Persistent IdP registrations; user identity/consent and tokens at provider/client | Confirm provider/tenant and exact client/resource/redirect settings before creation/change |
| F | Place bridge encryption key and enable the two explicit operator grants; pin callback hostname and exact tunnel audience | Private local key/policy/SQLite, encrypted webhook secrets; bridge retains synthetic content up to seven days | Confirm verified subject/client IDs, one test owner and file paths; no inferred owner grants |
| G | Create local tunnel profile and start bridge/tunnel in foreground; attach custom MCP to the intended dot via ChatGPT UI with OAuth | Profile persists; MCP metadata and synthetic test messages traverse OpenAI; ChatGPT OAuth connection persists | Confirm intended tunnel/issuer trust origin and target dot; browser login/consent is user-mediated |
| H | Authorize one finite `request.created` test subscription (request ttlMs 900000), submit synthetic text, read/reply, follow-up and cancellation | Subscription/signing secret, request/results persist; no unrelated conversation history or files | Confirm test-only event task and data below before subscribe/send; retain all native action confirmations |
| I | Unsubscribe test, stop foreground processes; optionally keep configured resources for next test | Stopping does not delete credentials or registrations | Confirm cleanup/retention choice; no deletion, revocation, launchd or brew-services registration by default |

No Slack/Teams, arbitrary Hermes platform messaging, `permissions_respond`, `--tools`, network/firewall changes, public unauthenticated fallback, PR merge or deployment is in this bundle.

## Verified commands and sequence

The local task must compare these with the **installed** `--help` before acting; official main and a Homebrew release can differ. Replace nonsecret identifiers locally. Secret references below are references, never literal credentials.

### 1. Read-only preflight, then approved installation

```sh
hostname
sw_vers
hermes --version
python3 --version
brew --version
command -v tunnel-client
```

If approved and missing:

```sh
brew install openai/tools/tunnel-client
tunnel-client --version
tunnel-client help quickstart
tunnel-client help oauth
tunnel-client init --help
tunnel-client run --help
```

Bridge installation/init commands are in [macos-local.md](macos-local.md). Select a supported Python >=3.12 interpreter available on Mac mini (preflight found 3.14.9), create a dedicated bridge venv, and use that venv’s executable for every bridge pip/CLI command. Do not use the interpreter/venv backing `~/.local/bin/hermes`. Preserve existing Hermes config and installations. The latest checked code adds `serve-discovery` and `check --require-tunnel`; install the latest reviewed PR head, not the older 78-test wheel.

### 2. Account UI / user login steps

1. User opens [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels) in the intended organization. Check reuse and the specific ChatGPT workspace association. If missing/denied, report the exact UI/permission failure; do not invent availability or change roles to make it work.
2. If required, an authorized org administrator reviews [Roles](https://platform.openai.com/settings/organization/people/roles). This is a separate persistent permission action, not an automatic setup fix.
3. User opens [Runtime API keys](https://platform.openai.com/settings/organization/api-keys), selects/creates the approved Restricted runtime key, and places it in the approved local protected file. Do not create an Admin API key for this test.
4. User chooses/logs into the external IdP console to review resource/client settings. Use [connection.md](connection.md) for requirements, including the proper signed client claim. Start with pre-registered clients unless the chosen provider/client combination requires and supports another method. No password/token issuer is built into the bridge.

The verified official `init` command writes a local profile; it does not create the remote tunnel or an IdP grant. The sample name `sample_mcp_with_dcr` is a metadata-capable HTTP profile, not a requirement to enable DCR or a running demo.

### 3. Safe metadata bootstrap and tunnel profile

Once the actual issuer is selected and written in the private bridge config:

```sh
hermes-dots-bridge serve-discovery --config "/ABSOLUTE/PRIVATE/PATH/config.json"
```

This provides only the two public protected-resource metadata URLs. It loads no key, policy, database or event worker; **all MCP requests, including requests carrying a token, receive 401**. It can help inspect rewritten metadata before real grants exist, but is not a successful tool/connection test. Stop it with Ctrl-C before starting the full bridge on the same port.

After the runtime/profile persistence action is approved:

```sh
tunnel-client init \
  --sample sample_mcp_with_dcr \
  --profile hermes-dots-bridge-test \
  --tunnel-id VERIFIED_TUNNEL_ID \
  --mcp-server-url http://127.0.0.1:8765/mcp \
  --control-plane-api-key-ref file:/ABSOLUTE/PRIVATE/PATH/runtime.key \
  --health-listen-addr 127.0.0.1:8080
```

Do not pass `--force`, open-Web-UI flags or demo/no-auth sample modes. Inspect the generated profile locally. Under its existing `mcp` mapping add `oauth_trusted_origins: ["https://VERIFIED-ISSUER-ORIGIN"]`, using only the exact HTTPS origin. For an organization-backed tunnel, also set the actual `control_plane.organization_id` if required by that deployment. Keep TLS checks enabled. The generated profile uses a secret reference and should be protected locally; do not paste it with resolved secrets into chat.

```sh
tunnel-client doctor --profile hermes-dots-bridge-test --explain
tunnel-client run --profile hermes-dots-bridge-test
```

Doctor/run use the runtime key and make network requests; run only at the approved stage. Foreground runtime keeps control-plane polling active. No `nohup`, `disown`, `brew services`, runtime-managed daemon or launchd registration is needed for this first test. Inspect only readiness/status locally; redact keys, callback URLs and request bodies before sharing diagnostics. A healthy tunnel does not mean bridge authorization is ready. In discovery-only mode, authenticated tool discovery is deliberately unavailable.

### 4. Resource and callback checks

While the tunnel is running, inspect the rewritten protected-resource document shown through the connection's supported OAuth discovery. Take its **exact `resource`** value, configure that API identifier at the IdP and place the same string in bridge `tunnel_resource`. No route shape is guessed. Do not confuse this with the tunnel ID, the OAuth token endpoint URL, or a `private_key_jwt` client-assertion audience.

Local Hermes uses `http://127.0.0.1:8765/mcp` as its resource. If the selected provider does not support that loopback resource or cannot bind the returned token audience correctly, stop and report the provider incompatibility; do not disable audience verification or switch transport without approval.

Before accepting a subscription, verify its destination hostname through the authorized connection/provider context and configure that exact `callback_hosts` entry. An unfamiliar hostname must fail closed. Inspect only the hostname locally, not the full callback secret or URL. Callback discovery, S256 login, resource rewriting, issuer metadata compatibility and event delivery are live validation gates, not assumptions established by unit tests.

Once keys/grants/audiences are ready, stop discovery-only mode and run:

```sh
hermes-dots-bridge check --require-tunnel --config "/ABSOLUTE/PRIVATE/PATH/config.json"
hermes-dots-bridge serve --config "/ABSOLUTE/PRIVATE/PATH/config.json"
```

Then use [ChatGPT Plugins](https://chatgpt.com/plugins): Add custom MCP server → Connection: Tunnel → approved tunnel → OAuth. Review the discovered tools/events, attach only to the intended existing dot, and complete user login/consent. Merge the output of `print-hermes` into the selected Mac mini Hermes profile and run `hermes mcp login hermes-dots-bridge`. Both paths must authenticate as different configured client roles for the same test owner.

## Synthetic end-to-end acceptance script (operator checklist)

Use a fresh nonsecret test-run label chosen locally, such as `bridge-smoke-YYYYMMDD-N`. Preserve actual returned IDs locally; do not fabricate result evidence. These are manual/agent tool steps, not an automatically executing script.

1. Before subscribing, confirm dot can see only `get_request`, `list_requests`, `list_responses`, `record_response` and event methods; Hermes sees its submit/read/cancel tools. Neither exposes approval or general execution tools.
2. Authorize a 15-minute `request.created` subscription on the test owner's queue. Dot's task is **only** to fetch each synthetic test request and record `bridge-ok:<run label>`; use no other tools or external actions. If ChatGPT cannot request a shorter lifetime, inspect the granted expiry and obtain confirmation before accepting the longer scope.
3. Require successful signed challenge verification and the returned subscription ID/expiry. A receipt or green tunnel indicator alone is insufficient.
4. In Mac mini Hermes, submit `Synthetic bridge test <run label>: reply only bridge-ok:<run label>; perform no other actions.` with a fresh stable idempotency key and request TTL 900 seconds. Capture returned request_id, conversation_id and sequence.
5. Retry exactly the same submit arguments once: it must return the same request ID, without another logical request.
6. Observe queued/delivered separately from completed. Dot calls get_request, records one response with the current revision and a stable response key, and marks completed only after recording its synthetic result. Retry that same response once; it must remain one result.
7. Hermes calls list_responses and get_request. Require the exact expected content and completed state for that request. Record this as the first actual end-to-end pass only when both are observed through the real clients.
8. Submit a second synthetic message in the same conversation with a new key; confirm sequence increment and previous context. Separately cancel a test request and verify subsequent response writes are rejected. Do not describe cancellation as stopping any external action already running.
9. Unsubscribe using the original event/arguments/callback identity through the authorized dot. Confirm no new deliveries are queued for a later retained test request. Stop bridge/tunnel foreground processes if this was a one-session test.
10. Report exact bridge commit, Hermes/tunnel versions, pass/fail for each gate, request IDs/state transitions, granted expiry, and remaining artifacts. Redact tokens, signing secrets, full callback URLs and unrelated conversations. Retain or remove resources only according to the approved cleanup choice.

Offline rehearsal: `tests/test_synthetic_acceptance.py` runs both real JWT audiences and official MCP clients with a fake callback, verifying submission/reply deduplication, no false completion, follow-up, cancellation and unsubscribe. It does not emulate the actual Tunnel control plane or prove the real dot handled anything.

## Stop conditions and exact blockers to report

Report action, target, reason and outcome as denied/pending/uncertain. Distinguish access disabled, missing Read/Use/Manage, wrong workspace association, missing IdP decision, PKCE/resource mismatch, callback host/challenge failure, and absence of dot processing after receipt. Check uncertain state before retries. Do not change device, broaden grants, accept unknown callback hosts, disable TLS/audience/authentication, or report success based only on a webhook ACK.

## Minimal nonsecret inputs for the local task

- Platform organization identifier and intended ChatGPT workspace identifier/name, verified in the user's logged-in UI; existing tunnel ID if there is one, otherwise the explicit choice to create the test tunnel in those scopes.
- External OAuth provider and tenant/issuer to reuse, or an explicit choice to set one up. This is the remaining provider decision; no secret is needed to answer it.
- The existing dot to attach and the selected Mac mini Hermes profile. Use the preflight-selected profile unless the user directs otherwise.

After those choices, the exact client IDs, verified subject, resource audience, redirect URI and callback hostname are derived through approved setup; they are not guessed upfront. Runtime keys, client secrets if applicable, signing secrets and login codes are entered/stored only through local supported interfaces. The initial foreground test does not require autostart approval.
