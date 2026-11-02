# Validation evidence

Status: **implemented, awaiting connection validation**.

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

**Unrun / still pending:** actual provider login/consent/PKCE and refresh flow; deployed ingress/TLS and outbound DNS/network; actual ChatGPT dot subscription/challenge/refresh and processing; actual MBA Hermes version/configuration; live two-way synthetic conversation; backup/restore operations and load testing. No real users, account grants or event receivers were used. Passing offline tests must not be presented as proof of an existing dot connection.

The smallest next step is to review the Draft PR and choose the persistent host/domain plus external OAuth provider. Then, under separate user authorization, configure the two clients/operator grants and perform the synthetic connection checks in [connection.md](connection.md).

## macOS packaging checks

Additional offline cases cover private template creation/no overwrite, disabled placeholder grants, permissions and symlink rejection, loopback-only resource validation, exact local/tunnel JWT audiences, local OAuth discovery, passive Hermes/plist rendering (including spaces/XML escaping), and one-process database locking. The launcher test mocks uvicorn, so it registers no service or network listener. Actual macOS launchctl/plutil, MBA installation, tunnel OAuth rewriting and proactive native UI delivery remain unrun.

The Python wheel was successfully built and its installed console entry point smoke-tested in the Cloud workspace. This is not a macOS installation test. CI also builds/installs the package and checks the CLI entry point.

## Tunnel preparation (Mac mini target)

Added a discovery-only bootstrap test proving that metadata remains available while every MCP request is denied and no store/key/grant is loaded. `check --require-tunnel` now rejects an unset tunnel audience. The dual-audience acceptance rehearsal uses actual RS256 verification, both official MCP client sessions, persistent mailbox operations and a fake callback; this remains offline and does not establish Tunnel/ChatGPT availability. The staged Mac mini handoff documents user login, separate action-time account approvals and exact synthetic pass criteria.

## Hermes MCP SDK 2.3 compatibility

Mac mini preflight reported MCP SDK 2.3.0 and Python 3.14.9. Cloud tested the full 81-case suite in independent Python 3.12 environments using SDK 1.30.0 and SDK 2.3.0: both passed. Initial SDK 2 test-harness failures (two-value transport streams and snake_case model fields) were fixed in `tests/sdk_compat.py`; at that baseline server protocol negotiation remained `2025-11-25`. The helper selects httpx2 for SDK 2, matching that SDK's client API. CI now tests both lockfiles on Python 3.12 and 3.14. `tests/requirements-mcp23.lock` is only an isolated compatibility-test environment, not a request to modify Hermes. Actual Hermes OAuth UI/login and live Tunnel interoperability remain pending.

## Modern discovery regression

The live integration reported HTTP 400 for `server/discover`, but retained logs did not include protocol headers, params or response body. The new fixtures follow the official 2026-07-28 schema; they are not represented as a replay of the unavailable live payload. Tests cover authenticated role-scoped discovery, metadata/header/version validation, tool/header encoding, per-request role/owner isolation, event subscription and response storage. SDK 2's modern `Client(mode="auto")` now verifies discovery, tools and mailbox calls with no initialize fallback; the legacy SDK sessions remain covered.

A failed modern transport validation logs only `mcp_wire_rejected code=<integer>`. No request body, client metadata, tokens, callback URLs or content are logged. If live refresh still fails, record only HTTP status and this numeric code; use the code to request a narrow sanitized schema diagnostic, never enable raw payload logging. Real dot response and webhook end-to-end validation remain local integration gates.

Local handoff: stop bridge, fetch the existing feature branch, fast-forward only after verifying a clean checkout, install the updated package with the bridge's isolated venv (`python -m pip install --no-deps .`), and restart the existing approved foreground command. Do not recreate the SQLite store, policy, keys, OAuth grants or tunnel. Refresh the existing ChatGPT plugin, verify modern discovery/tools/events, then read the queued synthetic request by its existing ID. Respect expiry; do not claim completion until a real dot writes a response and Hermes retrieves it.
