# hermes-dots-bridge

**Implemented, awaiting connection validation.** A private, asynchronous mailbox between Hermes Agent on a MacBook Air and an existing ChatGPT dot. No Slack/Teams, extra LLM, browser cookies or undocumented ChatGPT endpoints.

Hermes submits a message through authenticated MCP → SQLite stores the request and delivery outbox → an MCP Events webhook wakes the subscribed dot → dot reads the request and records its answer → Hermes retrieves the stored answer. Webhook receipt is **not** task completion. This does not control native ChatGPT threads, models or settings and does not promise streaming.

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

- Stateless Streamable HTTP at `/mcp`, negotiated MCP `2025-11-25`, scoped tool discovery and MCP Events. No MCP 2.0 claim.
- External OAuth resource server: protected resource metadata, RS256 signature/issuer/audience/time validation, operator-pinned JWKS URL, per-client roles and owner grants. No anonymous or static-token production mode.
- Owner-scoped conversations, ordered requests/responses, idempotent writes, revision conflict checks, cancellation, bounded retention and a durable SQLite outbox.
- Signed subscription challenge, deterministic subscriptions, expiry/unsubscribe, encrypted signing secrets and rotation overlap.
- Standard Webhooks signatures, stable event IDs across retries, bounded backoff, HTTPS host allowlist, DNS/IP validation on every connection, numeric-address pinning with TLS hostname validation, no redirects or environment proxies.

## Tool permissions

| Tool | Hermes | dot | Scope |
| --- | --- | --- | --- |
| `submit_message`, `cancel_request` | Yes | No | `bridge:submit` |
| `get_request`, `list_requests`, `list_responses` | Yes | Yes | `bridge:read` |
| `record_response` | No | Yes | `bridge:respond` |
| `events/list`, `events/subscribe`, `events/unsubscribe` | No | Yes | `bridge:subscribe` |

Identity comes from a verified token and local operator policy, never an `owner_id` argument. Requested scopes cannot change the role. Both clients' explicitly authorized identities map to the same private owner. There are no remote grant, approve, delete or administrator tools.

## Before connecting real accounts

See [connection and external OAuth setup](docs/connection.md), [protocol and operational contract](docs/contract.md) and [verification evidence](docs/validation.md). Templates are in [examples](examples/).

Decisions still required: persistent hosting/HTTPS domain, external OAuth tenant/client registrations, verified subject/client grants, callback hostname allowlist, secret-manager key, and explicit dot queue authorization. Real OAuth login, ChatGPT subscription and MBA Hermes round-trip are **unrun**. Do not treat this draft as a deployed or connected service.

Run a single process with a persistent writable volume only after completing those settings:

```sh
.venv/bin/uvicorn bridge.app:production_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

A TLS reverse proxy must expose `/mcp` and the protected-resource metadata paths. Configure ingress request/body deadlines, concurrency/rate limits and sensitive-log redaction. Do not put SQLite on ephemeral serverless disk; horizontal workers are outside this MVP's operating contract. Nothing in this repository deploys the service automatically.

## References

Protocol/auth sources and example license checks are recorded in [sources](docs/sources.md). Implementation is original; no example repository code or scripts were copied or run.
