# MVP protocol and operational contract

## Transport and identity

Invalid/expired authentication returns HTTP 401, including authentication failure during
a bounded wait. Missing operation scopes return HTTP 403 with `insufficient_scope` and
the required scopes; a post-authentication policy denial returns HTTP 403 without inviting scope
expansion. Tool calls may omit `arguments` when the input schema accepts an empty object.

One authenticated `/mcp` endpoint supports modern MCP `2026-07-28` (`server/discover` and per-request metadata), and legacy `2025-11-25` (`initialize`). Both serve `ping`, tools and the MCP Events extension. Modern requests validate required `_meta` fields and mirrored protocol/method/name headers before dispatch, including Base64-encoded names; malformed/mismatched headers return HTTP 400 / -32020, unsupported versions 400 / -32022, and unknown methods 404 / -32601. Metadata client identity never replaces verified OAuth claims. Modern results carry `resultType: complete` and server identity; discovery/list caching is private with zero TTL, and HTTP results use `Cache-Control: no-store` because grants are rechecked on every request.

JSON responses only; GET/DELETE return 405. Sessions, SSE streams, prompts/resources, sampling and elicitation are not advertised. Legacy initialization/cancellation notifications are ACKed with 202. Modern HTTP notifications are rejected. Transport cancellation does not cancel queued mailbox work; use `cancel_request`. The discovery-only bootstrap still denies every MCP request and is not the authenticated runtime server.

The dedicated local CLI allows `http://127.0.0.1:PORT/mcp` or explicitly configured native `https://127.0.0.1:PORT/mcp` as the resource and optionally one operator-pinned HTTPS tunnel audience. It still validates all token signatures/claims and grants. Remote HTTPS mode does not enable this exception. Local resource metadata uses the exact loopback origin; the tunnel performs its documented external discovery rewriting.

OAuth metadata is public at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp`; all MCP discovery and calls require a verified token. Missing/invalid authentication gets a 401 metadata challenge. Role/scope failures get a generic forbidden RPC error. Identity is the selected provider's verified subject + OAuth client mapped through operator policy. Token-supplied owner/role claims have no authority. Extra tool arguments are rejected.

## Conversation and request lifecycle

`submit_message(content, idempotency_key, conversation_id?, ttl_seconds?)` atomically writes the conversation sequence, request and matching subscription outbox entries. Omit conversation_id for the first message; use the returned ID for subsequent messages. Only one unfinished request per conversation is accepted, except that a new message may answer a `needs_input` request. That prior request is cancelled with `superseded_by_followup`, not falsely completed; its question remains in context.

A request starts `queued`. Successful webhook receipt may change it to `delivered` but never `completed`. Dot may write `processing`, `needs_input`, `completed` or `failed`. A submitter (Hermes or an authorized agent) may cancel. Expiry produces `expired`. Terminal requests cannot accept new responses. A response's `expected_revision` must match the latest request revision; each accepted response increments it. Delivery bookkeeping does not increment that revision. Requests awaiting input close to further dot writes; continue via a new Hermes message in that conversation.

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

SQLite WAL plus immediate transactions make submissions and outbox writes atomic. A single delivery worker checks for the next due event immediately after each attempt. It waits one second when idle or after an unexpected worker error. The worker holds a transaction across its bounded callback POST, which temporarily serializes mailbox calls. A crash after ACK but before commit may redeliver with the same event ID. Dot must inspect current state and use idempotent response keys.

Each owner can retain 1,000 requests and have four active, unexpired subscriptions. A replacement callback can reclaim a retired subscription's slot. Stale subscriptions are otherwise deleted one day after expiry.

Each request can accept up to 31 `processing` responses and one closing response (`completed`, `failed` or `needs_input`). A request that already had 32 progress responses before an upgrade can still record its closing response. Each content field accepts 8,000 characters and inbound JSON is capped at 64 KiB. Request TTL ranges from 60 seconds to 24 hours. Requests, responses and outbox entries are retained for seven days from request creation. Empty conversations are removed, giving long-running conversations a rolling history. The operator controls which owners can use this private bridge.

The database may retain previously allocated disk space. WAL files and backups can retain historical content; logical purge is not a promise of forensic erasure. Use volume encryption, restricted filesystem permissions, an explicit backup expiry policy and scheduled offline compaction as needed. Message content itself is not application-encrypted. A lost encryption key prevents subscription recovery; database recovery/backups and key recovery must be coordinated.

No horizontal scaling, multiple delivery workers, distributed leases, provider introspection/revocation webhook, telemetry dashboard, native ChatGPT thread control, automatic external-action approvals, or production OAuth account setup is included. Schema migration tooling is not yet needed for the initial schema; later changes must introduce versioned migrations. Validate these constraints before production use.

## Bounded submitter waits and local operations

`wait_response` is available to authorized submitters (the legacy `hermes` role and new `agent` role; both require submit and read grants). It reads one owner-scoped request, up to ten new responses and an authorized active-subscription count. It waits at most 30 seconds (default 25), polling at most once per second with four concurrent waits per process. It holds no database lock while sleeping, reauthenticates/rechecks grants every poll, stops on HTTP disconnect and returns capacity errors instead of accumulating a queue. Pending timeout never changes a request or claims completion. Terminal/needs_input states or a new response return immediately; clients page revisions and retain the request ID. Reads can still incur the bounded SQLite/callback contention of the single-worker MVP.

Optional local TLS requires a matching certificate/key pair, private owned files, and the explicit HTTPS loopback resource. Client trust, SAN/expiry verification and OAuth audience migration are operator setup steps. `status` reads only aggregate local persistence/lock information and cannot prove remote health. Generated LaunchAgents are disabled by default; keep-alive bridge templates require HTTPS. No CLI command creates tokens, certificates, subscriptions, services or trust grants.
