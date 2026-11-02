# MVP protocol and operational contract

## Transport and identity

One authenticated `/mcp` endpoint handles `initialize`, `ping`, `tools/list`, `tools/call`, `events/list`, `events/subscribe`, and `events/unsubscribe`. It negotiates MCP `2025-11-25` and returns JSON responses; GET/DELETE return 405. Sessions, SSE streams, batch requests, MCP 2.0 `server/discover`, prompts and resources are not advertised. Supported initialization/cancellation notifications are ACKed with 202. MCP transport cancellation does not cancel a queued mailbox request; use `cancel_request` for that.

The dedicated local CLI allows `http://127.0.0.1:PORT/mcp` as the resource and optionally one operator-pinned HTTPS tunnel audience. It still validates all token signatures/claims and grants. Remote HTTPS mode does not enable this exception. Local resource metadata uses the exact loopback origin; the tunnel performs its documented external discovery rewriting.

OAuth metadata is public at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp`; all MCP discovery and calls require a verified token. Missing/invalid authentication gets a 401 metadata challenge. Role/scope failures get a generic forbidden RPC error. Identity is the selected provider's verified subject + OAuth client mapped through operator policy. Token-supplied owner/role claims have no authority. Extra tool arguments are rejected.

## Conversation and request lifecycle

`submit_message(content, idempotency_key, conversation_id?, ttl_seconds?)` atomically writes the conversation sequence, request and matching subscription outbox entries. Omit conversation_id for the first message; use the returned ID for subsequent messages. Only one unfinished request per conversation is accepted, except that a new message may answer a `needs_input` request. That prior request is cancelled with `superseded_by_followup`, not falsely completed; its question remains in context.

A request starts `queued`. Successful webhook receipt may change it to `delivered` but never `completed`. Dot may write `processing`, `needs_input`, `completed` or `failed`. Hermes may cancel. Expiry produces `expired`. Terminal requests cannot accept new responses. A response's `expected_revision` must match the latest request revision; each accepted response increments it. Delivery bookkeeping does not increment that revision. Requests awaiting input close to further dot writes; continue via a new Hermes message in that conversation.

Use one stable idempotency key per logical submission (owner-wide) and per logical response (request-wide). Identical retries return the previous result; changed arguments with a reused key fail. For a stale-revision error, read again before deciding whether to write. This guarantees mailbox write idempotency, not exactly-once execution of dot's external work. Retrying after the seven-day retention horizon can create new work: idempotency records no longer exist then.

`get_request` returns the selected request, up to ten contextual requests in sequence, the latest response for each, and outbox delivery statuses. Page earlier context using `next_before_sequence`. Use `list_responses` for all progress responses in pages of ten, ordered by revision. `list_requests` pages twenty summaries and provides recovery when an event was missed. Re-scan from the beginning for new messages; pages are not a frozen snapshot.

Cancellation prevents subsequent stored replies and pending delivery. It does not stop actions already started outside this bridge. A transport disconnect is not cancellation. Expiry/retention is checked by the worker and before data operations.

## Events and delivery

`request.created` has empty subscription arguments: each subscriber sees only its mapped owner's queue. Payload data contains only `request_id`; no user content or instructions travel in the webhook. There is no event replay cursor (`cursor: null`, `truncated: false`). Requests submitted without an active subscription remain discoverable by `list_requests` but are not automatically backfilled when a subscription is later created. Persisted pending outbox entries resume after restart while their subscription remains authorized and active.

Subscription identity hashes verified subject, client, mapped owner, canonical arguments, event name and exact callback URL. Repeated creation refreshes it. The requested ttlMs is capped at 24 hours; null or omitted also receives a finite 24 hours. Minimum accepted explicit TTL is one second. Unsubscribe is idempotent and stops matching pending entries. Expiry/revocation prevents further sends.

Signing keys require `whsec_` plus valid base64 encoding of 24–64 bytes. Challenge verification is signed, single-use and limited to 15 seconds; successful verification is cached for five minutes for that subscription/principal/callback identity. Secret changes use a five-minute overlap of old/new signatures. Secrets are encrypted at rest with Fernet; encryption-key lifecycle remains an operator responsibility.

Each POST signs the exact serialized body using Standard Webhooks HMAC-SHA256 over `id.timestamp.body`. Event IDs are stable across retry; signing timestamps/signatures are regenerated. Receivers must verify signatures/timestamps and deduplicate event IDs. This service sends events; it does not accept inbound event callbacks and thus does not implement an inbound replay cache. Test callbacks are isolated to tests.

Callbacks require exact allowlisted HTTPS hosts on port 443, no credentials/fragments/control characters. Every connection resolves A/AAAA with a bounded DNS lookup, rejects any nonpublic, multicast, reserved or transition/mapped destination, then connects to a validated numeric address with the original TLS hostname/SNI. Redirects and proxy environment variables are never used. DNS has a three-second budget; a socket watchdog bounds connection/TLS/response work to ten seconds. Responses are limited to 16 KiB. Only the first validated address is attempted; another retry resolves afresh.

An event body cannot exceed 256 KiB. Retry network errors, 408, 429, 5xx and non-ACK 3xx at 5, 10, 20, 40, 80 seconds, up to six total attempts. Do not retry 410, 413 or other 4xx; 410 also deactivates the subscription. Exhaustion records a failed delivery; it does not claim that dot failed/completed the task. The request remains readable until its own expiry. No automated redelivery or operator retry tool is exposed; inspect the request before manually submitting new work with a new key.

## Persistence and limits

SQLite WAL plus immediate transactions make submissions and outbox writes atomic. A single delivery worker holds a transaction across its bounded callback POST, avoiding simultaneous sends in this deployment model. This temporarily serializes mailbox calls; it is intended for one private user's low-volume bridge. A crash after ACK but before commit may redeliver with the same event ID. Dot must inspect current state and use idempotent response keys.

Defaults: 1,000 retained requests per owner, 4 retained subscriptions per owner, 32 responses per request, 8,000 characters per content field, 64 KiB inbound JSON, request TTL 60 seconds–24 hours, request/response/outbox retention seven days from request creation. Stale subscriptions are deleted one day after expiry. Empty conversations are removed; long-running conversation context therefore has a rolling retention horizon. Active policy owners are operator-controlled; this is not a public multi-tenant service.

The database may retain previously allocated disk space. WAL files and backups can retain historical content; logical purge is not a promise of forensic erasure. Use volume encryption, restricted filesystem permissions, an explicit backup expiry policy and scheduled offline compaction as needed. Message content itself is not application-encrypted. A lost encryption key prevents subscription recovery; database recovery/backups and key recovery must be coordinated.

No horizontal scaling, multiple delivery workers, distributed leases, provider introspection/revocation webhook, telemetry dashboard, native ChatGPT thread control, automatic external-action approvals, or production OAuth account setup is included. Schema migration tooling is not yet needed for the initial schema; later changes must introduce versioned migrations. Validate these constraints before production use.
