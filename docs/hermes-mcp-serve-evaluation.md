# Hermes official mcp-serve reuse decision

Inspected on 2026-11-02: [official plugin README](https://github.com/NousResearch/hermes-official-plugins/blob/main/mcp-serve/README.md), [catalog](https://hermes-agent.nousresearch.com/docs/plugins/mcp-serve), `plugin.yaml` and `bridge.py` at repository commit `ba4f8c9fb06c1755e8627d400c2a0faa63478535`. The catalog's reviewed pin may differ from repository main. It is MIT and requires Hermes >=0.22.0. No plugin source was copied, executed or installed.

`hermes mcp-serve` makes Hermes an MCP **server** over stdio. Our flow needs Hermes as an MCP **client** calling the private mailbox. Keeping these directions separate avoids reading unrelated existing conversations or exposing more authority than the user's bridge requires.

Verified contracts:

- `conversation_get(session_key)` reads a gateway conversation index; `messages_read(session_key, limit)` reads retained message history.
- `events_poll(after_cursor, session_key, limit)` and `events_wait(after_cursor, session_key, timeout_ms)` are callable tools over the plugin's in-memory event buffer. They are not `events/list`/`events/subscribe` or signed webhook wakeups for an idle dot. Events can include approval data as well as messages.
- `messages_send(target, message)` passes a platform target to Hermes' send-message path. It is not a native CLI prompt-injection API, and requires the relevant gateway/platform connection. No documented contract guarantees insertion into an active or idle native Hermes CLI/Desktop agent turn.
- The plugin exposes `permissions_respond` even without `--tools`. Passing the whole server through a tunnel would therefore exceed this bridge's permission boundary.

Decision: do not add mcp-serve as a dependency or proxy its tools for the current Hermes UI → dot → Hermes polling flow. Supported Hermes MCP configuration already handles submission and retrieval with fewer dependencies. This bridge stores only explicitly submitted bridge messages and does not duplicate access to Hermes' state.db. The authenticated MCP Events path remains necessary for dot wakeups.

If future user-authorized work needs existing platform conversation history, reuse the official plugin through a new narrow adapter rather than reimplementing its history parser. That adapter would need explicit conversation ownership/selection and a fixed tool allowlist (initially only `conversation_get` and `messages_read`); no permission tools, automatic `--tools`, arbitrary `messages_send`, execution capabilities or approval responses may pass through. No such adapter is implemented in this PR.

Current UI limitation: a user/active Hermes turn can fetch and display replies; proactive native UI insertion is unsupported/unverified. Changing to a connected messaging platform would change the user's requested experience and is not silently substituted. No Slack or Teams dependency is introduced.
