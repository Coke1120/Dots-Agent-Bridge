# Dots Agent Bridge

Secure, OAuth-scoped MCP bridge connecting ChatGPT Dots with OpenBot, Hermes, and other AI agents through asynchronous messaging and events.

> GitHub repository: [Coke1120/Dots-Agent-Bridge](https://github.com/Coke1120/Dots-Agent-Bridge). The Python package and CLI still use `hermes-dots-bridge` for backward compatibility. MCP server identity, local service labels, and OAuth resource identifiers remain unchanged.


**Hermes has verified live round trips; OpenBot and generic MCP agents are supported by the new submitter role but have not yet been live-tested.** This is a private, asynchronous mailbox between an explicitly authorized MCP agent client (Hermes, OpenBot, etc.) and an existing ChatGPT dot. Long-term renewal/recovery validation remains pending. No Slack/Teams, extra LLM, browser cookies or undocumented ChatGPT endpoints.

The [independent project review](docs/project-review-2026-11-02.md) records six reproduced
defects and fixes, the Python/SDK test matrix, security coverage and remaining live gates.

An authorized agent submits a message through authenticated MCP → SQLite stores the request and delivery outbox → an MCP Events webhook wakes the subscribed dot → dot reads the request and records its answer → the submitting agent retrieves the stored answer. Webhook receipt is **not** task completion. This does not control native ChatGPT threads, models or settings and does not promise streaming.

## OpenBot and generic MCP agents

The new **`agent`** OAuth role can submit, read, wait for and cancel mailbox requests, but **cannot** record dot responses or subscribe to dot-only events. Hermes continues to work unchanged via its legacy `hermes` role. Both submitter roles have the same narrow scopes; neither gets tool or account management privileges.

- [OpenBot connection / OAuth / local and Docker networking guide](docs/openbot.md), including reuse of the existing [`talk-to-dot` skill](skills/talk-to-dot/SKILL.md).
- [OpenBot-only bootstrap policy](examples/agent-policy.example.json) or `hermes-dots-bridge init --directory /PRIVATE/bridge --submitter agent`.
- [Phase 2: optional dot-initiated OpenBot task dispatch](docs/agent-dispatch-roadmap.md) (design only, **not implemented**).
- [Repository identity and naming compatibility](docs/rename-plan.md) (GitHub repository has been renamed; runtime identifiers remain stable).

An OpenBot **Hermes-provider** agent can also use the existing Hermes profile and `/talk-to-dot` skill, without granting OpenBot a separate OAuth client. For Codex, Claude, Gemini or other OpenBot providers use the native MCP connection and a dedicated `agent` grant.

## Automatic operation

Start with the [automatic-operation guide](docs/automatic-operation.md): deterministic direct MCP client, optional Hermes `/talk-to-dot` skill, bounded `wait_response`, official event automation setup, local HTTPS, disabled LaunchAgent templates, status, key rotation and recovery. A local Mac mini test completed the actual `request.created` wakeup and exact response retrieval in 24.251 seconds using an isolated Hermes transport. This is one observed run, not a latency guarantee.

## Mac local installation

The selected test target is now **Mac mini Hermes**. [The Tunnel handoff](docs/tunnel-handoff.md) lists exact account/setup gates and the synthetic end-to-end test.

The bridge can run beside Hermes on your Mac. See the [local-service walkthrough](docs/macos-local.md) for the installable CLI, private setup validation, generated Hermes configuration, foreground start/stop and optional user-controlled launchd template. No automatic installer modifies Hermes or registers a service.

Dot reaches the local server through an explicitly configured Secure MCP Tunnel or fixed authenticated HTTPS ingress. OAuth remains required. The local runner accepts only an explicit loopback resource plus an optional exact tunnel audience; the normal HTTPS server remains unchanged. Hermes polls replies; proactive injection into its native UI is not implemented. The [official mcp-serve evaluation](docs/hermes-mcp-serve-evaluation.md) explains why that plugin does not replace this flow.

## Migration

See [moving Hermes, the bridge, or Auth0](docs/migration.md) for transport requirements, stable owner mapping, protected backup/restore, identity cutover and rollback. These are manual procedures; no live migration is claimed.

## Run offline checks

Python 3.12+:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pytest -q
.venv/bin/pip check
```

The lock includes development/test dependencies. Tests use synthetic principals, ephemeral test signing keys and in-memory callbacks. The official MCP Python client is exercised through an in-process HTTP transport. Nothing connects to ChatGPT, creates an OAuth grant or sends real user data.

## Implemented

- Stateless Streamable HTTP at `/mcp`, modern MCP `2026-07-28` discovery/per-request metadata plus legacy `2025-11-25`, scoped tools and MCP Events.
- External OAuth resource server: protected resource metadata, RS256 signature/issuer/audience/time validation, operator-pinned JWKS URL, per-client roles and owner grants. No anonymous or static-token production mode.
- Owner-scoped conversations, ordered requests/responses, idempotent writes, revision conflict checks, cancellation, bounded retention and a durable SQLite outbox.
- Signed subscription challenge, deterministic subscriptions, expiry/unsubscribe, encrypted signing secrets and rotation overlap.
- Standard Webhooks signatures, stable event IDs across retries, bounded backoff, HTTPS host allowlist, DNS/IP validation on every connection, numeric-address pinning with TLS hostname validation, no redirects or environment proxies.

## Tool permissions

| Tool | Hermes (legacy) | Agent (e.g. OpenBot) | Dot | Scope |
| --- | --- | --- | --- | --- |
| `submit_message`, `cancel_request` | Yes | Yes | No | `bridge:submit` |
| `wait_response` | Yes | Yes | No | `bridge:submit` + `bridge:read` |
| `get_request`, `list_requests`, `list_responses` | Yes | Yes | Yes | `bridge:read` |
| `record_response` | No | No | Yes | `bridge:respond` |
| `events/list`, `events/subscribe`, `events/unsubscribe` | No | No | Yes | `bridge:subscribe` |

Identity comes from a verified token and local operator policy, never an `owner_id` argument. Requested scopes cannot change the role. All clients' explicitly authorized identities map to the same private owner. **Important:** the owner scope is shared by Hermes, OpenBot and any other enabled submitter; submitters for the same owner can read each other's queued messages. OpenBot's per-agent MCP access settings help restrict which agents receive the server but are not a per-agent bridge identity or authorization boundary. There are no remote grant, approve, delete or administrator tools.

## Before connecting real accounts

See [connection and external OAuth setup](docs/connection.md), [protocol and operational contract](docs/contract.md) and [verification evidence](docs/validation.md). Templates are in [examples](examples/).

Local integration has reported a real automatic reply through authenticated Hermes transport, a signed webhook and the intended dot. The repository direct client also completed a real automatic round trip in the dedicated `dot-bridge` profile without Hermes model initialization. Hermes token refresh/rotation has since passed. After the ChatGPT connection and trigger update, another real automatic round trip completed in 24.344 seconds. ChatGPT-managed token refresh, full-period subscription renewal and reboot recovery remain unverified. See the [approval bundle and acceptance test](docs/automatic-operation.md#6-automatic-acceptance-test-and-approval-bundle). No service is installed automatically by this repository.

Run a single process with a persistent writable volume only after completing those settings:

```sh
.venv/bin/uvicorn bridge.app:production_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

A TLS reverse proxy must expose `/mcp` and the protected-resource metadata paths. Configure ingress request/body deadlines, concurrency/rate limits and sensitive-log redaction. Do not put SQLite on ephemeral serverless disk; horizontal workers are outside this MVP's operating contract. Nothing in this repository deploys the service automatically.

## License

Licensed under the [MIT License](LICENSE), Copyright (c) 2026 Coke1120. You may use, modify and redistribute the original project code under the terms of this license. Dependencies retain their respective licenses.

## References

Protocol/auth sources and example license checks are recorded in [sources](docs/sources.md). Implementation is original; no example repository code or scripts were copied or run.
