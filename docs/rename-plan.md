# Repository identity, description, and compatibility

The GitHub repository has already been renamed to **[Coke1120/Dots-Agent-Bridge](https://github.com/Coke1120/Dots-Agent-Bridge)**. This is the canonical GitHub location. Existing GitHub redirects are convenient for old links, but they are not a migration strategy for local files, runtime identities, or OAuth settings.

## Recommended GitHub About metadata

**Description**

> Secure, OAuth-scoped MCP bridge connecting ChatGPT Dots with OpenBot, Hermes, and other AI agents through asynchronous messaging and events.

**Topics:** `mcp`, `chatgpt-dots`, `openbot`, `hermes-agent`, `oauth2`, `agent-interop`, `webhooks`

**License:** [MIT](../LICENSE). The Python package also advertises the SPDX identifier `MIT` and includes the license file.

## Backwards-compatible names

The GitHub rename does **not** rename the Python distribution `hermes-dots-bridge`, CLI `hermes-dots-bridge`, Python import package `bridge`, MCP server identity, LaunchAgent labels, service paths, existing profile settings, or OAuth resource/audience. Keep these stable so that deployed Hermes and OpenBot profiles and the authorized ChatGPT Dot still work. Renaming the local package or CLI should only happen via a later compatibility release with aliases and a validated migration.

Before any future runtime identity change, inventory existing Mac mini installations, configured OAuth client IDs, audience/resource identifiers, event subscriptions, tunnel ingress, scripts and pinned installation paths. Back up private state and perform an end-to-end synthetic round trip after a controlled rollout. GitHub repository redirects cannot fix mismatched OAuth audiences.

The repository being public and MIT-licensed **does not make the operator's mailbox or credentials public**. Never commit real policy grants, OAuth tokens, keys, message databases or webhook secrets. Live network exposure requires HTTPS, restricted grants and the security checks already described in [connection.md](connection.md) and [openbot.md](openbot.md).
