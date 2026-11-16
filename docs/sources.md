# Sources checked on 2026-11-02

Primary protocol references used before implementation:

- [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events): authenticated event discovery/subscription lifecycle, verification challenge, Standard Webhooks, callback validation, delivery/retry semantics and finite subscription grants.
- [OpenAI authentication](https://developers.openai.com/plugins/build/auth): OAuth resource metadata, PKCE, token verification and client identification options.
- [OpenAI connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt): custom MCP/plugin connection and actual-host validation workflow.
- [MCP Streamable HTTP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports): legacy initialization-based transport contract retained for Hermes clients.
- [Hermes remote MCP](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md) and [configuration reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md): remote OAuth connection, public-client configuration and local login workflow.
- [Auth0 MCP authorization](https://auth0.com/ai/docs/mcp/get-started/authorization-for-your-mcp-server) and [access-token profiles](https://auth0.com/docs/secure/tokens/access-tokens/access-token-profiles): external provider option, resource compatibility and signed client identity claim selection. Provider setup is documented, not executed.
- [PyJWT usage](https://pyjwt.readthedocs.io/en/stable/usage.html), [dnspython resolver](https://dnspython.readthedocs.io/en/stable/resolver-class.html), [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk): verifier, bounded resolver and client interoperability support.

Two suggested examples were inspected via their README and license, not executed or copied:

- [hookdeck/mcp-events-outpost-demo](https://github.com/hookdeck/mcp-events-outpost-demo): MIT, copyright 2026 Hookdeck Technologies Inc. Its demo distinction and outbox delivery architecture are useful references; this project does not depend on Hookdeck or Outpost.
- [rohanprichard/mcp-events-starter](https://github.com/rohanprichard/mcp-events-starter): MIT, copyright 2026 Rohan Richard. Its documented limitations reinforce why a shared test bearer token is insufficient for ChatGPT OAuth. This project does not reuse its code or setup scripts.

No third-party example source was copied, so no example code license notice is embedded in runtime files. Dependency licenses remain those of their packages. This project is distributed under the [MIT License](../LICENSE), Copyright (c) 2026 Coke1120. Third-party dependencies retain their own licenses.

Local packaging references: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [official tunnel-client README](https://github.com/openai/tunnel-client), and [configuration/OAuth rewriting](https://github.com/openai/tunnel-client/blob/main/docs/configuration.md#mcp-server). The [mcp-serve evaluation](hermes-mcp-serve-evaluation.md) records the official Hermes plugin's actual direction and limits.

Tunnel command and permission handoff additionally checked the official repository at `f745672195e788073ab0b104644138cdd2e4fd61`: `cmd/client/init_command.go`, `profile_samples/sample_mcp_with_dcr.yaml.tmpl`, `help_topics/{quickstart,oauth}.txt`, and `docs/permissions.md`. Source was read only, not installed/executed. Account entitlement is not inferred from documentation.

Hermes client path additionally inspected at the local preflight revision `c7c7e0fa92f`: `tools/mcp_tool.py` and `tools/mcp_tool_transport.py` support current SDK transports and initialize-based handshake versions. SDK 2.3.0 was installed only into an isolated Cloud test venv and its public client API exercised; no Hermes environment was modified.

## Modern protocol correction (2026-11-02)

- [MCP discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover): supportedVersions, capabilities, server metadata and cache hints.
- [MCP versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning): dual-era behavior and unsupported version errors.
- [MCP HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http): required mirrored headers, encoding, validation and HTTP status codes.
- [MCP core](https://modelcontextprotocol.io/specification/2026-07-28/basic/index): required per-request metadata and resultType.
- [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events): discovery advertises events, followed by authenticated list/subscribe/unsubscribe. Existing challenge and delivery security remain unchanged.

Earlier SDK 2 tests exercised only its legacy ClientSession facade. The added `Client(mode="auto")` regression exercises modern discovery and operations without initialize fallback. SDK package compatibility alone did not establish modern wire compatibility.

## Automatic operation sources (2026-11-02)

- [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills): native SKILL.md directory and slash-command behavior. Repository ships a concise skill, not a patched gateway/CLI.
- [HTTPX environment trust](https://www.python-httpx.org/environment_variables/): process-local SSL_CERT_FILE behavior. Also inspected installed SDK 2.3 httpx2/httpcore2 code for default environment trust.
- [Tunnel trust configuration](https://github.com/openai/tunnel-client/blob/v0.0.19/docs/configuration.md): ca-bundle extends all outbound TLS trust in that process; no global plaintext exception in generated service command.
- Official Automations tools returned this bridge as an event source and the request.created schema with empty parameters. This read-only evidence does not mean a subscription was created or a dot executed.
