# Independent project review — 2026-11-02

Six reproducible correctness, interoperability and reliability defects were fixed.
No unauthorized cross-owner access, role escalation or callback SSRF bypass was
demonstrated. Unattended operation still needs live evidence for ChatGPT-managed OAuth
refresh, full-period subscription renewal and host reboot recovery.

## Scope and method

- Repository: private `Coke1120/hermes-dots-bridge`.
- Audited base: **`7475d1da9f0cae07550c2bbad4dabab74e139157`**, fetched from `origin/main`
  in a new independent workspace. This includes `f79b08e`. Main was re-fetched and
  remained at that base before publication.
- Review branch: `review/project-reliability-20261102`; target: `main`.
- All tracked runtime modules, tests, dependency/build configuration, workflow, examples,
  skill and documentation were reviewed. No applicable `AGENTS.md` or `.agents/skills`
  directory was present.
- Call chains were traced from HTTP authentication through protocol dispatch, policy,
  SQLite and callback delivery, and from the standalone direct client through its
  transport boundary. New reproductions use synthetic data and temporary databases.
- The Codex Security `security-scan` skill was consulted, but its referenced shared
  scan prologue/core resources and scan helpers were unavailable in this host. This is
  a manual source-backed security review with regression tests and a dependency advisory
  check, **not a completed/sealed Codex Security scan**. No scanner-generated security
  report or exhaustive vulnerability-free claim is implied.

No live Mac, Auth0 tenant, Tunnel, routing, proxy, credentials, subscription, automation or
real message payload was accessed or changed. Public primary documentation and public
dependency advisory metadata were read. No merge or release is part of this review.

## Validated findings and fixes

Priorities describe product impact: P2 = normal priority; P3 = lower priority.
Source line links below identify the immutable audited base. All findings have high
confidence in the reproduced code behavior; live client consequences remain qualified.

### R1 — P2: HTTP authorization failures prevent standard client recovery signals

Base: [app.py:137](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/app.py#L137),
[waiting.py:26](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/waiting.py#L26),
[protocol.py:68](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/protocol.py#L68).

An accepted `wait_response` reauthenticates on each poll. If authentication then fails,
the old handler returned HTTP **200** with JSON-RPC Forbidden. Missing operation scopes
also returned HTTP 200 without an OAuth scope challenge. The installed SDKs' OAuth
response handling branches on HTTP 401 and on HTTP 403 plus `insufficient_scope`, so
those responses could not initiate the corresponding standard recovery path. A later
request may still refresh proactively; this does not prove that a real client always
requires fresh consent.

Reproduction: a synthetic authenticator accepts the initial HTTP request and denies the
wait's next authentication. The new test observed HTTP 200 before the fix. Calling
`record_response` as Hermes similarly observed HTTP 200. Discovery with submit-only
credentials advertised `wait_response` even though its read scope was absent.

Fix: authentication failure during a wait now returns HTTP **401** with protected-resource
metadata. Missing scopes return HTTP **403**, `error="insufficient_scope"` and the required
scopes. Post-authentication policy denials return HTTP 403 without a scope-expansion
challenge. Discovery and dispatch both require submit **and** read for `wait_response`.
Role/owner restrictions still come from local policy; a broader OAuth request cannot
grant another role.

Regression: `test_wait_authentication_expiry_returns_http_challenge_without_losing_request`,
`test_insufficient_scope_returns_http_challenge_and_keeps_role_boundary`,
`test_wait_discovery_requires_both_scopes`, plus existing expiry/revocation/isolation cases.

### R2 — P2: fresh Hermes configuration omitted the refresh-token scope

Base: [cli.py:176](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/cli.py#L176),
[Hermes example](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/examples/hermes.config.example.yaml#L9).

`print-hermes` and the YAML example requested only `bridge:submit bridge:read`.
For the documented Auth0 setup, omitting `offline_access` prevents refresh-token issuance,
even when the provider's refresh grant is enabled. A fresh installation using those
templates can therefore work until its access token expires and then need another login.
The existing live grants described in `validation.md` already requested offline access;
this finding concerns the templates, not evidence that those live caches are broken.

Reproduction: render `hermes_snippet` with a synthetic local configuration and inspect its
requested scope; the renewed-scope assertion failed before the fix. Auth0's primary
[refresh-token instructions](https://auth0.com/docs/secure/tokens/refresh-tokens/get-refresh-tokens)
confirm the scope and separate API offline-access prerequisite.

Fix: generated and example Hermes configurations include `offline_access`. Connection,
local-operation and migration instructions now state the same prerequisite for both
clients. Stale modern-protocol/tool-count and merged-branch installation guidance was
also corrected. Templates do not change an existing profile, grant, provider policy or
token cache. An already issued non-refreshable grant may need one supported login to
obtain the newly requested scope.

Regression: the existing passive template/configuration test checks the renewal scope.
Actual token issuance and rotation were not exercised against a live provider here.

### R3 — P2: standard SDK calls with omitted empty arguments were rejected

Base: [protocol.py:70](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/protocol.py#L70).

Both official SDK generations can serialize `call_tool("list_requests")` without an
`arguments` member. The server required that member before validating the tool's own
schema, although `list_requests` accepts an empty input object. This broke an ordinary
recovery/listing call and could be mistaken for connection trouble.

Reproduction: the official legacy ClientSession and SDK 2 modern `Client(mode="auto")`
both raised `MCPError(-32602, invalid_arguments)` for that invocation before the fix.

Fix: omitted arguments default to `{}`; explicit non-objects and tools missing their
required fields still fail schema validation. Authentication and scope checks run first.
Both SDK round-trip tests now exercise the omitted-arguments invocation through HTTP.

### R4 — P2: retention cleanup blocked all mailbox work for tens of seconds

Base: [store.py:87](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/store.py#L87).

For every purged request, `maintain` loaded and parsed all remaining responses for its
owner. The whole operation held the store lock and SQLite immediate transaction, blocking
HTTP mailbox calls and event delivery. It runs in the worker and before data operations.

Reproduction: an isolated temporary database with **1,000 requests and 32,000 short
synthetic responses**, all beyond retention, took **35.674 seconds** to purge at the base.
This uses the documented per-owner request/response limits; it does not require large
message bodies. Both runs removed every record successfully.

Fix: collect purged request IDs and scan responses once for the batch, preserving one
atomic transaction and all retention deadlines. The same fixture took **0.193 seconds**
after the fix; the committed benchmark repeated at **0.174 seconds**. These are observations
in this workspace, not a performance guarantee or a full-message-capacity load test.

Reproduce the fixed measurement with `python tests/benchmark_retention.py`. Functional
regressions cover recent history across owners, conversation cleanup, and rollback of
both requests and responses when a purge is interrupted. There is no fragile timing
threshold in the unit suite.

### R5 — P3: wall-clock rollback selected an older response as latest

Base: [store.py:163](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/store.py#L163).

`get_request` chose each request's latest response after sorting by wall-clock creation
time before revision. A system clock adjustment could make an older progress response
replace a newer completion in `latest_responses`, even while the request state and paged
responses correctly showed completion.

Reproduction: store processing revision 1, move the synthetic clock back ten seconds,
then store completed revision 2. Before the fix the request was completed but its latest
response was revision 1.

Fix: select by the transactionally ordered response revision. The regression verifies
that the final response is returned despite the timestamp inversion.

### R6 — P3: malformed callback challenge escaped its categorized error

Base: [callback.py:178](https://github.com/Coke1120/hermes-dots-bridge/blob/7475d1da9f0cae07550c2bbad4dabab74e139157/bridge/callback.py#L178).

A callback echo containing a non-ASCII string caused `hmac.compare_digest` to raise
`TypeError`, which escaped verification's error handler and became an Internal error.
It still failed closed and activated no subscription, but lost the useful
`CallbackEndpointError / challenge_failed` diagnosis.

Reproduction: mock only the callback response as `{"challenge":"invalid-\u00e9"}`;
the pre-fix verifier raised TypeError. Fix: include TypeError in invalid-challenge
classification. Parameterized tests also cover arrays, null, malformed JSON and invalid
UTF-8. No signature, challenge comparison, TLS or destination check was weakened.

## Security and architecture coverage

| Area | Traced behavior and evidence | Remaining boundary |
| --- | --- | --- |
| Identity and permissions | HTTP → pinned JWKS/RS256 → exact issuer/audience/time/client claim → policy subject/client → owner/role → dispatch/store; real ephemeral RSA signatures and cross-owner/role tests | No provider introspection/revocation feed; operator policy and finite token/subscription lifetimes govern revocation |
| Protocol | Both discovery/metadata eras, mirrored headers, role-scoped tools/events, official SDK legacy sessions and modern auto discovery | Future SDK/Hermes private-API changes need new compatibility evidence |
| Callbacks and secrets | Subscribe → schema/allowlist/key checks → signed challenge → encrypted subscription → outbox → rechecked policy → signed POST; public-only DNS, pinned numeric connection, hostname TLS, no redirects/proxies, bounded bodies/timeouts | Network/DNS/TLS behavior primarily uses synthetic adapters; no live callback was contacted |
| Queue and revisions | Atomic submit/outbox, stable retry event IDs, bounded retry/permanent errors, cancellation/expiry, response-key deduplication, revision conflicts and follow-ups | At-least-once delivery; a bridge cannot deduplicate external effects performed by a handler |
| Persistence | WAL/immediate transactions, reopen/retry recovery, retention, purge rollback, encrypted signing secrets | Message bodies/backups are not application-encrypted; restore must preserve the matching Fernet key and committed WAL data |
| Direct client | Explicit profile before imports, named HTTPS OAuth transport, result aliases, private input-bound receipt before send, stable retry identity, status/wait recovery, inference import guard and cleanup | No installed Hermes runtime in this workspace; guard is not an OS sandbox and cannot certify future transitive imports |
| Packaging/operations | Owned private files, no-symlink checks, loopback listener, process lock, passive CLI/status, matching TLS pair, disabled LaunchAgents and packaging smoke test | Actual macOS launchctl, login ordering, sleep/wake, certificate trust and reboot were not exercised |
| Dependencies/workflow/docs | Both complete pinned lockfiles, package install, four matrix environments, test workflow, examples and operator guides | Locks/actions lack artifact hashes/immutable action pins; advisory checks cannot prove absence of unknown vulnerabilities |

The design remains suitable for one private owner's low-volume mailbox. Callback POSTs
still intentionally hold the database transaction; normal network contention can therefore
delay reads. Horizontal scaling, job execution leases, automatic event backfill after an
inactive subscription, and automatic account/service administration remain outside its
documented contract.

## Validation

The baseline passed **131 tests plus one expected skip with SDK 1**, and **132 with SDK 2**
on Python 3.12. New regressions were first run against the unchanged runtime and failed
with the specific behaviors recorded above; retention was measured separately.

Final local matrix:

| Python | MCP SDK | Result |
| --- | --- | --- |
| 3.12.14 | 1.30.0 | 142 passed; 1 expected modern-API skip |
| 3.12.14 | 2.3.0 | 143 passed |
| 3.14.8 | 1.30.0 | 142 passed; 1 expected modern-API skip |
| 3.14.8 | 2.3.0 | 143 passed |

All four environments passed `compileall` and dependency compatibility checks. SDK 1
retains the pre-existing Starlette/httpx TestClient deprecation warning. The package was
built/installed into the isolated Python 3.12 SDK 1 environment, and its installed console
entry point passed `--help`. `git diff --check` and Ruff fatal syntax/undefined-name rules
`E9,F63,F7,F82` passed; the repository has no broader configured linter.

`pip-audit 2.10.1` checked all **38** SDK 1 lockfile packages and **39** SDK 2 lockfile
packages with **zero known advisories and zero skipped packages** on 2026-11-02. These are
public dependency checks, not proof that dependencies contain no vulnerabilities. No
dependency version or live Hermes installation was changed.

The Draft PR and its exact-head GitHub Actions checks are the publication record. CI
results should always be associated with the PR's current head, not an earlier run.

## Remaining live acceptance work

1. Observe ChatGPT-managed refresh across access-token expiry using its supported
   connection flow. The repository's prior local evidence proves Hermes rotation and
   automatic replies, not ChatGPT refresh. The previously documented seven-day idle and
   thirty-day maximum refresh lifetimes can still require login when reached; this review
   did not inspect or change current provider settings.
2. Observe an authenticated same-identity `events/subscribe` refresh before `refreshBefore`
   and a later genuine reply. OAuth refresh and the maximum-24-hour event subscription
   are independent. Creating another handler or extending access-token TTL is not a
   substitute for this evidence. A subscription created after missed events does not
   backfill them; inspect retained request IDs instead of blindly submitting again.
3. Verify service restart and reboot with one authoritative database/key pair, private
   receipts, valid TLS trust and supported Tunnel connectivity. LaunchAgents are separate
   jobs and do not enforce a dependency ordering; KeepAlive retries do not repair expired
   credentials or guarantee upstream readiness. Perform an actual synthetic round trip
   after both are ready. Real backup/restore and host crash recovery also need operator
   rehearsal beyond the offline transaction/reopen tests.
4. The reported official Tunnel regional HTTP 403 prevents live access validation. No
   alternate routing, region evasion, audience expansion or TLS exception was attempted.
   Report and resolve it only through supported availability/account controls.

No additional user input is needed for these code fixes and the Draft PR. The live checks
above remain separate authorized operational work; this review does not claim they passed.

## Primary references checked

- [MCP authorization and HTTP error handling](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [MCP modern discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
- [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [Auth0 refresh-token prerequisites](https://auth0.com/docs/secure/tokens/refresh-tokens/get-refresh-tokens)

Installed SDK source was also inspected for optional tool arguments and OAuth 401/403
handling. Public documentation checks do not establish any account's current availability.
