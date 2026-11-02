# Validation evidence

Status: **repository direct client and dedicated profile verified by local integration;
Hermes refresh verified; ChatGPT renewal and reboot recovery remain pending**.

## Latest live evidence (2026-11-02)

The authorized Mac mini integration reported an isolated Hermes transport submission,
signed webhook HTTP 200, genuine event-triggered intended-dot reply and exact Hermes
response retrieval at revision 1 in 24.251 seconds, with no manual chat/reply. This proves
one automatic round trip; it does not prove optional interactive skill integration, ChatGPT-managed OAuth
refresh, subscription renewal or reboot recovery. Those remain unverified; the later Hermes
refresh result is recorded below. No live IDs,
credentials or private callback URLs are included here. See the
[operational guide](automatic-operation.md) for the working architecture and remaining checks.

A subsequent local test used the unmodified repository `bridge/direct.py` at `819abe7`
with explicit `dot-bridge` profile selection. It completed in **22.246 seconds**, with one
webhook HTTP 200 and a genuine completed revision 1 response. No Hermes model initialized;
the inference import guard remained enabled and the retry receipt was retained. The daily
shell wrapper now uses that repository file. No tested-path fixes were required. This
confirms the direct-client path, not the optional agent skill or long-term renewal.

The sections below record earlier milestones and offline evidence. Their historical
connection gates are superseded only by the specific live observations above.

## Initial offline baseline

Local Python 3.12 checks:

```sh
.venv/bin/pytest -q
.venv/bin/python -m compileall -q bridge tests
.venv/bin/pip check
git diff --check
```

The test suite at the pre-modern baseline had **81 passing cases**, zero failures. Starlette emits one deprecation warning for its httpx TestClient integration; it does not affect the test results. The official MCP SDK test uses its own httpx ASGI transport.

| Area | Evidence |
| --- | --- |
| MCP | Official Python client initializes, discovers and calls tools through HTTP/ASGI; RPC discovery, notifications and error tests |
| Authentication | Real RS256 verification with ephemeral synthetic RSA keys; wrong issuer/audience/time/signature/algorithm/client, missing claims, client-claim selection |
| Authorization | Cross-owner reads/cancellation denied, dot cannot submit, Hermes cannot respond, forged owner arguments rejected, policy revocation/corruption fail closed |
| Conversation | Synthetic round-trip, needs-input follow-up, revision ordering, duplicate submission/response, atomic concurrent submission, no false completion |
| Delivery | Signed challenge, invalid challenge rejection, encrypted/rotated signing keys, stable IDs and new signatures on retry, 410/413 handling, bounded attempts, simulated crash replay |
| Recovery | Reopen same SQLite database after failed callback, resume retry; expiry/unsubscribe/cancellation/retention checks |
| Callback safety | URL/secret/payload bounds, private/mixed/transition DNS rejection, DNS rebind checks, numeric address pinning, TLS hostname/handshake and redirect behavior via mocked sockets |

**Mocked:** IdP/JWKS retrieval, HTTP authentication in mailbox integration tests, callback receiver/network/TLS sockets, clock advancement. JWT signatures and SQLite transactions are real within tests. No fake-auth environment switch or production bypass exists; injected test adapters live under `tests/` only.

**Unrun at this baseline:** actual provider login/consent/PKCE and refresh flow; deployed ingress/TLS and outbound DNS/network; actual ChatGPT dot subscription/challenge/refresh and processing; actual MBA Hermes version/configuration; live two-way synthetic conversation; backup/restore operations and load testing. No real users, account grants or event receivers were used. Passing offline tests must not be presented as proof of an existing dot connection.

At this baseline, the next step was to review the Draft PR and choose the persistent host/domain plus external OAuth provider. Then, under separate user authorization, configure the two clients/operator grants and perform the synthetic connection checks in [connection.md](connection.md).

## macOS packaging checks

Additional offline cases cover private template creation/no overwrite, disabled placeholder grants, permissions and symlink rejection, loopback-only resource validation, exact local/tunnel JWT audiences, local OAuth discovery, passive Hermes/plist rendering (including spaces/XML escaping), and one-process database locking. The launcher test mocks uvicorn, so it registers no service or network listener. Actual macOS launchctl/plutil, MBA installation, tunnel OAuth rewriting and proactive native UI delivery remain unrun.

The Python wheel was successfully built and its installed console entry point smoke-tested in the Cloud workspace. This is not a macOS installation test. CI also builds/installs the package and checks the CLI entry point.

## Tunnel preparation (Mac mini target)

Added a discovery-only bootstrap test proving that metadata remains available while every MCP request is denied and no store/key/grant is loaded. `check --require-tunnel` now rejects an unset tunnel audience. The dual-audience acceptance rehearsal uses actual RS256 verification, both official MCP client sessions, persistent mailbox operations and a fake callback; this remains offline and does not establish Tunnel/ChatGPT availability. The staged Mac mini handoff documents user login, separate action-time account approvals and exact synthetic pass criteria.

## Hermes MCP SDK 2.3 compatibility

Mac mini preflight reported MCP SDK 2.3.0 and Python 3.14.9. Cloud tested the full 81-case suite in independent Python 3.12 environments using SDK 1.30.0 and SDK 2.3.0: both passed. Initial SDK 2 test-harness failures (two-value transport streams and snake_case model fields) were fixed in `tests/sdk_compat.py`; at that baseline server protocol negotiation remained `2025-11-25`. The helper selects httpx2 for SDK 2, matching that SDK's client API. CI now tests both lockfiles on Python 3.12 and 3.14. `tests/requirements-mcp23.lock` is only an isolated compatibility-test environment, not a request to modify Hermes. Actual Hermes OAuth UI/login and live Tunnel interoperability were still pending at this milestone.

## Modern discovery regression

The live integration reported HTTP 400 for `server/discover`, but retained logs did not include protocol headers, params or response body. The new fixtures follow the official 2026-07-28 schema; they are not represented as a replay of the unavailable live payload. Tests cover authenticated role-scoped discovery, metadata/header/version validation, tool/header encoding, per-request role/owner isolation, event subscription and response storage. SDK 2's modern `Client(mode="auto")` now verifies discovery, tools and mailbox calls with no initialize fallback; the legacy SDK sessions remain covered.

A failed modern transport validation logs only `mcp_wire_rejected code=<integer>`. No request body, client metadata, tokens, callback URLs or content are logged. If live refresh still fails, record only HTTP status and this numeric code; use the code to request a narrow sanitized schema diagnostic, never enable raw payload logging. Real dot response and webhook end-to-end validation were local integration gates at this milestone.

Local handoff: stop bridge, fetch the existing feature branch, fast-forward only after verifying a clean checkout, install the updated package with the bridge's isolated venv (`python -m pip install --no-deps .`), and restart the existing approved foreground command. Do not recreate the SQLite store, policy, keys, OAuth grants or tunnel. Refresh the existing ChatGPT plugin, verify modern discovery/tools/events, then read the queued synthetic request by its existing ID. Respect expiry; do not claim completion until a real dot writes a response and Hermes retrieves it.

## Automatic-operation preparation

Separate local integration reports a successful manual real round trip. Cloud added bounded wait tests for response arrival, timeout, cancellation, expiry, token expiry, policy revocation, disconnect, capacity and cross-owner/role isolation; the dual-client real-JWT synthetic rehearsal also retrieves through wait_response. TLS tests use ephemeral synthetic certificates and a real local socket-pair TLS handshake; no CA is installed. Service templates are checked for Disabled=true and no plaintext exception. Local status is checked for no database creation. Existing durable outbox/retry/restart and false-completion tests remain in the suite.

Real event subscription/challenge/refresh, platform-triggered intended-dot execution, launchd persistence and the macOS HTTPS trust/audience migration were **unrun** by Cloud at this milestone. No new real credentials, grants, services or automation were created. See automatic-operation.md for a staged acceptance test that does not count a manually invoked dot reply as autonomous success.

## Subscription failure diagnostics

Failed authenticated `events/subscribe` callback validation now logs only
`mcp_subscription_rejected hostname=<normalized-host-or-unavailable> reason=<fixed-code>`.
It never logs URL path/query/fragment/userinfo, tokens, signing secrets, headers, payloads
or owner identity. `host_not_allowed` distinguishes a syntactically acceptable HTTPS URL
from other `invalid_url` failures; the RPC error and fail-closed allowlist are unchanged.
The hostname is untrusted diagnostic input, not permission to allow it: correlate the
attempt with the authorized platform subscription flow and independently verify the host
before any allowlist change. Malformed/control-character hosts use `unavailable`.

## Deterministic direct client

`bridge/direct.py` follows the locally verified Hermes MCP transport call path without
starting chat, an agent or provider selection. SDK result normalization uses JSON aliases
and handles both error spellings. Fourteen direct-client cases cover actual installed SDK
result models, offline transport mocks, profile-before-import order, blocked inference
imports, private retry receipts, input-bound idempotency, status-only reads, bounded waits
and session cleanup on failure. The later local run described above separately verifies
the packaged file in the dedicated profile. Neither test set certifies transitive imports
of future Hermes releases. The initial missing-refresh-token observation is superseded
by the approved renewal update below.


## Approved renewal update

Local verification reports HTTPS API offline access enabled and both existing clients
configured for rotating refresh tokens, seven-day idle expiry, thirty-day maximum expiry
and zero overlap. Hermes reauthorized for submit/read/offline_access, actually rotated
tokens through refresh and then completed a harmless authenticated read. This is live
refresh evidence, not merely successful login or a configured grant checkbox.

The new ChatGPT connection retains the same client and HTTPS audience, uses
client_secret_post with read/respond/subscribe/offline_access and existing openid, and
passes an authenticated read. The same automation was updated to its new source. Its
post-change automatic round trip is pending; ChatGPT-managed refresh, subscription renewal
and reboot recovery have not been observed. Earlier automatic successes remain evidence
for the earlier connection. See the operational guide for domain roles and separate token
and subscription lifecycles. No Cloud account changes or live calls were made for this
documentation update.
