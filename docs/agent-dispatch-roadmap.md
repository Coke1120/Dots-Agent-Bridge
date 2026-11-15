# Phase 2 proposal: dot-initiated OpenBot task dispatch

**Not implemented by the OpenBot client-support PR.** Phase 1 only gives an explicitly authenticated submitter the existing private mailbox to talk to the dot; it does **not** let the dot wake, select, or command OpenBot agents.

## Desired workflow

1. A user-authorized dot creates a narrowly scoped task for an explicitly allowlisted OpenBot agent.
2. A separate dispatch service delivers the work through a **documented OpenBot host API, routine webhook or supported agent entry point**, verifying ownership and the recipient's identity before enqueueing.
3. OpenBot acknowledges receipt with a durable `task_id`, not a claim of completion, and emits progress, final result and errors correlated to that ID.
4. The dot receives signed, deduplicated events through a reviewed MCP Events publisher or explicitly reads status; it reports back with the same task ID.
5. The user can cancel pending work and inspect audit history. Cancellation is not a guarantee that external work already performed is undone.

## Required security and operational design

- **Separate roles and credentials.** Do not give the dot `bridge:submit`, change the existing `record_response` tool into arbitrary execution, or reuse the subscriber's webhook signing secret as an OpenBot host credential.
- Explicit target allowlist per owner and agent. Never infer a host or job target from untrusted prompt text; avoid cross-tenant leakage.
- Validate authenticated event signatures, timestamp freshness and replay IDs. Deliver at least once; use durable idempotency keys and monotonic task state transitions, not an in-memory “exactly once” assumption.
- Bound task size, runtime, quota, retries and concurrent jobs. Log identifiers and states, not prompts, tokens, credentials or private webhook URLs.
- Require human confirmation for privileged commands, deployment, external messages, purchases and account changes according to the ordinary OpenBot/Dots rules. Do not turn on OpenBot full-access silently.
- Support cancellation, lost acknowledgements, transient host downtime, offline Mac mini, refresh expiry, duplicate callbacks and recovery from database restart.
- Add integration tests with a **mock OpenBot endpoint** first; then run a separately approved live synthetic test against a pinned OpenBot version and known host.
- Review the current OpenBot supported trigger API before implementing; a webhook routine that triggers a fixed workflow is not the same as an API capable of arbitrary agent sessions.

## Suggested separate pull requests

- **PR A:** OpenBot target discovery and an allowlisted, read-only status probe, no new outbound execution.
- **PR B:** Authenticated and durable `dispatch_task` / `get_task` / `cancel_task` with explicit task ownership and an allowlisted host adapter.
- **PR C:** Signed OpenBot completion/progress event ingress and MCP event notification, user-visible audit and production recovery tests.

Each stage should be independent of the stable Hermes/Dots message protocol and preserve the existing installed CLI, OAuth resource/audience, SQLite state and ChatGPT subscription.
