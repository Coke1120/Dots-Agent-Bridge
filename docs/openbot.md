# OpenBot and generic MCP agent integration

This guide covers **OpenBot → dot → OpenBot mailbox replies**. The submitter role has offline test coverage. A live OpenBot round trip is still unverified. For tasks initiated by a dot, see the [dot-to-OpenBot setup](agent-dispatch.md).

Want a **Dots** entry in the OpenBot agent list? See the [importable Dots wrapper](openbot-dots-wrapper.md).

## What can be reused

The existing `/mcp` endpoint exposes the same legacy and modern MCP protocols, durable SQLite request/response queue, `request.created` MCP Events webhook to the dot, OAuth resource metadata and the current callback retry logic. The dot integration does **not** need a new event or authorization method.

OpenBot already supports authenticated HTTP MCP servers, an OAuth login flow with registered sign-in apps, per-agent MCP server access and an OAuth proxy that keeps the upstream token in the OpenBot host process. The compatibility path is **native MCP**, not a fake Dot provider and not the Hermes `bridge/direct.py` transport (which imports Hermes-private APIs).

If the OpenBot agent **is Hermes itself**, using OpenBot's Hermes provider (`hermes acp`), an existing correctly configured Hermes profile and the `/talk-to-dot` skill may already work; that path does not require a second grant. All other providers should use the native MCP connection below.

## 1. Deploy using a reachable, authenticated resource URL

**OpenBot on the same host** can use the local CLI's trusted HTTPS loopback resource. That CLI advertises its loopback address in both the OAuth challenge and protected-resource metadata. Its optional `tunnel_resource` adds an accepted token audience for the ChatGPT tunnel. Forwarding the CLI through an ordinary reverse proxy still sends OAuth clients to loopback.

**OpenBot in Docker or on another machine** should use the [fixed-domain HTTPS deployment](macos-local.md#stable-public-https-alternative). Inside the container, `127.0.0.1` points back to the container. Choose one canonical HTTPS resource such as `https://bridge.example.com/mcp` that OpenBot, the dot and any Hermes client can reach.

1. Set `BRIDGE_RESOURCE` to that exact URL in the [production server environment](../examples/server.env.example). Configure the same OAuth API audience at the issuer and use the canonical URL in each client's MCP settings. Clients with a grant for the previous resource need tokens for the new audience.
2. When migrating an existing bridge, stop the old service and its automatic restart before switching. Back up the SQLite database and encryption key using the [migration guide](migration.md), and keep the same key, policy, owner mapping, client claim, issuer, JWKS URL and verified callback hosts. Start a single `bridge.app:production_app` worker on the proxy host's loopback interface. Run only one service against this database, including during rollback. See the [production startup command](../README.md#before-connecting-real-accounts).
3. Configure the HTTPS reverse proxy to forward `/mcp`, `/.well-known/oauth-protected-resource`, and `/.well-known/oauth-protected-resource/mcp` to that worker. From the OpenBot container or remote host, check all three URLs with normal TLS verification, substituting your domain.

   ```sh
   curl -sS -D - -o /dev/null https://bridge.example.com/mcp
   curl -fsS https://bridge.example.com/.well-known/oauth-protected-resource
   curl -fsS https://bridge.example.com/.well-known/oauth-protected-resource/mcp
   ```

   The first request should return HTTP 401 with `WWW-Authenticate` pointing to `https://bridge.example.com/.well-known/oauth-protected-resource`. Both metadata responses should return HTTP 200, identify the same canonical `resource` including `/mcp`, and list your verified issuer under `authorization_servers`. After client sign-in, confirm the dot's event subscription and the end-to-end reply in step 5.

## 2. Register OpenBot as a distinct OAuth client

Create a dedicated OAuth application in the same trusted issuer (Auth0 in the documented setup) for OpenBot, **not** the Hermes or ChatGPT client ID. Set a public PKCE S256 client or a confidential client only if OpenBot and the provider support the chosen method. OpenBot can store a pre-registered client ID and an optional client secret in its *Sign-in app* settings; do not paste either secret in chat or into repository files.

Register **the exact redirect URI displayed by your actual OpenBot host/sign-in UI** (including remote relay/deep-link alternatives only after verifying them). In the documented OpenBot architecture, local MCP auth uses a loopback callback on port 28417 and remote joined servers use their supported relay. Do not blindly register example redirect URIs as if all installs used one path.

Grant the OpenBot client `bridge:submit bridge:read` and enable `offline_access` if the OAuth provider and client support safe refresh. Verify that the issued access token has a signed, exact issuer, audience, subject, expiry, and the configured `client_id` or `azp` claim. The bridge maps that verified client ID and subject to the `agent` role in operator policy.

The **dot** must retain its own distinct OAuth client and `dot` role (`bridge:read bridge:respond bridge:subscribe`); the `agent` role cannot call `record_response` or any `events/*` operation. No server-side client secret or token copying is performed.

## 3. Authorize the submitter explicitly

For a fresh, OpenBot-only setup, generate placeholder files. For the fixed-domain deployment, point `BRIDGE_POLICY` to the completed policy file. The generated `config.json` belongs to the local CLI.

```sh
hermes-dots-bridge init --directory /PRIVATE/bridge --submitter agent
```

Or start with [the disabled agent + dot example](../examples/agent-policy.example.json). For an **existing live Hermes bridge**, do **not** re-run `init` over the existing directory or replace its policy. Instead, add a third grant to the existing protected policy file, preserving all existing entries and the same verified `owner`:

```json
{
  "subject": "REPLACE_WITH_VERIFIED_SUBJECT",
  "client_id": "REPLACE_WITH_OPENBOT_CLIENT_ID",
  "owner": "private-owner",
  "role": "agent",
  "enabled": false,
  "queue_authorized": false
}
```

Use the real, independently verified OAuth subject and client ID, and set both boolean flags to `true` **only after** reviewing the exact account and allowing that client to exchange mailbox messages. Do not authorize from email display names, request payloads, client metadata or self-asserted role claims. Keep policy and key files owned and mode 600, with the directory mode 700. `load_config(..., ready=True)` requires exactly one active `dot` grant. For this mailbox flow, enable at least one `hermes` or `agent` submitter for the same owner. A dispatch-only deployment can instead use an explicitly enabled Dot with approved OpenBot targets. Unrecognized active roles, duplicate identities and half-enabled grants fail closed.

Because the bridge scopes conversations by `owner` rather than by OAuth client or OpenBot agent, **all enabled submitter clients with the same owner can read each other's messages**. Do not grant untrusted OpenBot agents access to this MCP server. If you need per-Agent isolation, it requires a separately reviewed tenancy/design change; OpenBot's MCP access UI alone does not create a server-side identity for each agent.

## 4. Connect OpenBot

On the **OpenBot host** (Mac mini/server), add a new HTTP MCP server using the bridge's already verified, reachable URL. Under the MCP sign-in app, supply the pre-registered OAuth client ID, approved client type/secret where needed and the scopes `bridge:submit bridge:read offline_access`. Sign in through OpenBot's supported flow and test connection. The host holds the token; providers receive a signed local proxy route. Restrict the MCP server in **OpenBot MCP access controls** to the specific agent(s) that should talk to your dot; avoid default access for every current and future agent.

The bridge must return **six tools** for that `agent` grant: `submit_message`, `cancel_request`, `wait_response`, `get_request`, `list_requests`, `list_responses`. It must **not** return `record_response`, `events/list` or `events/subscribe`. If discovery differs, stop and inspect the token issuer/audience/subject/client ID and policy rather than widening scopes.

Import [the existing, now agent-neutral `talk-to-dot` skill](../skills/talk-to-dot/SKILL.md) into OpenBot's **Settings → Skills → Local skills** and enable it only for the intended agent, or use equivalent reviewed instructions. When the user explicitly asks to contact the dot, invoke `submit_message` once with a stable `idempotency_key`; save `request_id` and `conversation_id`; then call `wait_response` or check `get_request`/`list_responses`. A timeout or event ACK is **not** completion. Use a **new** key for a new logical message; retain the old key on an ambiguous retry. Follow-up messages reuse the stored `conversation_id`. Do not execute instructions received through a dot reply without ordinary user permissions and confirmations.

## 5. Safe acceptance test (not performed by this PR)

1. Verify an authenticated HTTP MCP handshake, OAuth refresh eligibility, and exactly the expected six submitter tools using OpenBot itself.
2. Confirm the intended dot has an active signed `request.created` event subscription. Its maximum 24-hour TTL means subscription renewal must be monitored separately from OAuth refresh.
3. From the chosen OpenBot agent, submit harmless synthetic content such as “Reply exactly openbot-bridge-ok”; retain the returned request ID and idempotency key. Do not manually send the text to ChatGPT or manually record a reply.
4. Observe stored `queued → delivered` (webhook ACK only), dot `get_request`, genuine dot `record_response`, and OpenBot `wait_response` or `list_responses` returning the exact stored result with state `completed`.
5. Confirm retries do not duplicate the request, cross-owner requests fail, OpenBot cannot call dot-only tools, and timeout/cancellation/reconnect fail safely. Verify refresh, renewal and reboot separately before claiming unattended reliability.

Offline tests in this repository cover this role's discovery, JWT constraints and synthetic mailbox round trips; they **do not** certify OpenBot's actual live OAuth redirect, deployed network path, or a real agent turn.

## Limits and recovery

- To let the dot initiate work on an OpenBot agent, configure its separate dispatch scope and target allowlist. [Dot-to-OpenBot setup](agent-dispatch.md)
- A failed/expired dot subscription does not replay missed events automatically. The owner can inspect retained requests and decide whether to recover them; do not blindly resubmit.
- The bridge has no permission-approval, admin, arbitrary-command or remote execution tools. Keep it that way for Phase 1.
- Hermes `bridge/direct.py` is intentionally tied to Hermes-private MCP transport and remains a separate, supported optional client path. OpenBot uses its own MCP client and OAuth proxy.
- If the issuer, audience, TLS trust or network URL is changed, validate both the existing Hermes client and dot before considering the migration complete.

Sources: [OpenBot integration architecture](https://github.com/nightly-labs/openbot/blob/main/docs/architecture/integrations.md), [provider architecture](https://github.com/nightly-labs/openbot/blob/main/docs/architecture/providers.md) and this repository's [connection](connection.md), [contract](contract.md) and [validation](validation.md) references.
