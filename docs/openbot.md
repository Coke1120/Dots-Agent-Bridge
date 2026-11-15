# OpenBot and generic MCP agent integration

**Status:** implemented Python submitter role and offline tests; no live OpenBot-to-dot round trip is claimed. The existing Hermes path remains supported. This guide describes **agent → dot → agent mailbox replies**, not dot-initiated execution of arbitrary OpenBot tasks.

## What can be reused

The existing `/mcp` endpoint exposes the same legacy and modern MCP protocols, durable SQLite request/response queue, `request.created` MCP Events webhook to the dot, OAuth resource metadata and the current callback retry logic. The dot integration does **not** need a new event or authorization method.

OpenBot already supports authenticated HTTP MCP servers, an OAuth login flow with registered sign-in apps, per-agent MCP server access and an OAuth proxy that keeps the upstream token in the OpenBot host process. The compatibility path is **native MCP**, not a fake Dot provider and not the Hermes `bridge/direct.py` transport (which imports Hermes-private APIs).

If the OpenBot agent **is Hermes itself**, using OpenBot's Hermes provider (`hermes acp`), an existing correctly configured Hermes profile and the `/talk-to-dot` skill may already work; that path does not require a second grant. All other providers should use the native MCP connection below.

## 1. Deploy using a reachable, authenticated resource URL

- Keep the existing OAuth issuer, JWKS URL, dot subscription, persistent SQLite database, encryption key and `owner` mapping. Back up sensitive state through the current operator process before migration.
- The MCP server and protected-resource metadata must be reachable at a **stable URL from the OpenBot host**. Use an HTTPS endpoint with valid TLS trust, including the `/mcp` path and both `/.well-known/oauth-protected-resource*` paths.
- A local loopback bridge binds only `127.0.0.1`. **Inside OpenBot Docker, `127.0.0.1` means the container, not the Mac mini**. Do not change the bridge to `0.0.0.0`, disable TLS verification or expose SQLite to make it reachable. Use an explicitly reviewed HTTPS reverse proxy/ingress that can reach the loopback listener, or deploy an independent properly hosted bridge. The operator must verify metadata and OAuth token **audience** for the exact external resource URL.
- Do **not** assume a ChatGPT Secure MCP Tunnel URL is directly routable by OpenBot. OpenBot and ChatGPT must use an actually supported, reachable public/loopback audience. The existing local bridge supports one local audience and one explicitly pinned tunnel audience; if a third distinct audience is needed, design and review that extension separately.
- Keep the bridge single-process with a persistent, private database and private TLS/key files. No installer or account setting in this repository changes your host automatically.

## 2. Register OpenBot as a distinct OAuth client

Create a dedicated OAuth application in the same trusted issuer (Auth0 in the documented setup) for OpenBot, **not** the Hermes or ChatGPT client ID. Set a public PKCE S256 client or a confidential client only if OpenBot and the provider support the chosen method. OpenBot can store a pre-registered client ID and an optional client secret in its *Sign-in app* settings; do not paste either secret in chat or into repository files.

Register **the exact redirect URI displayed by your actual OpenBot host/sign-in UI** (including remote relay/deep-link alternatives only after verifying them). In the documented OpenBot architecture, local MCP auth uses a loopback callback on port 28417 and remote joined servers use their supported relay. Do not blindly register example redirect URIs as if all installs used one path.

Grant the OpenBot client `bridge:submit bridge:read` and enable `offline_access` if the OAuth provider and client support safe refresh. Verify that the issued access token has a signed, exact issuer, audience, subject, expiry, and the configured `client_id` or `azp` claim. The bridge maps that verified client ID and subject to the `agent` role in operator policy.

The **dot** must retain its own distinct OAuth client and `dot` role (`bridge:read bridge:respond bridge:subscribe`); the `agent` role cannot call `record_response` or any `events/*` operation. No server-side client secret or token copying is performed.

## 3. Authorize the submitter explicitly

For a fresh, OpenBot-only setup, generate placeholder files:

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

Use the real, independently verified OAuth subject and client ID, and set both boolean flags to `true` **only after** reviewing the exact account and allowing that client to exchange mailbox messages. Do not authorize from email display names, request payloads, client metadata or self-asserted role claims. Keep policy and key files owned and mode 600, with the directory mode 700. `load_config(..., ready=True)` now requires exactly one active `dot` grant and **at least one** active `hermes` or `agent` grant, all mapping to one owner. Unrecognized active roles, duplicate identities and half-enabled grants fail closed.

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

- The bridge does not insert messages into idle OpenBot chats or task queues. Dot-initiated OpenBot work requires **Phase 2**, described in [the dispatch roadmap](agent-dispatch-roadmap.md).
- A failed/expired dot subscription does not replay missed events automatically. The owner can inspect retained requests and decide whether to recover them; do not blindly resubmit.
- The bridge has no permission-approval, admin, arbitrary-command or remote execution tools. Keep it that way for Phase 1.
- Hermes `bridge/direct.py` is intentionally tied to Hermes-private MCP transport and remains a separate, supported optional client path. OpenBot uses its own MCP client and OAuth proxy.
- If the issuer, audience, TLS trust or network URL is changed, validate both the existing Hermes client and dot before considering the migration complete.

Sources: [OpenBot integration architecture](https://github.com/nightly-labs/openbot/blob/main/docs/architecture/integrations.md), [provider architecture](https://github.com/nightly-labs/openbot/blob/main/docs/architecture/providers.md) and this repository's [connection](connection.md), [contract](contract.md) and [validation](validation.md) references.
