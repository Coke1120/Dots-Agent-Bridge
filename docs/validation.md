# Validation evidence

Status: **implemented, awaiting connection validation**.

Local Python 3.12 checks:

```sh
.venv/bin/pytest -q
.venv/bin/python -m compileall -q bridge tests
.venv/bin/pip check
git diff --check
```

The test suite currently has **63 passing cases**, zero failures. Starlette emits one deprecation warning for its httpx TestClient integration; it does not affect the test results. The official MCP SDK test uses its own httpx ASGI transport.

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
