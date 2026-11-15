# Repository name and metadata migration plan

**Decision for the OpenBot-support PR: do not rename the GitHub repository, package, Python module, MCP server identity, launchd labels or OAuth resource.** Existing Mac mini profiles, documentation links, operator scripts, service launch definitions, OAuth audiences, GitHub actions, and the installed ChatGPT connector may already reference `hermes-dots-bridge`. GitHub redirects do not protect local imports, OAuth audiences or automation configuration.

A future name such as **`dots-agent-bridge`** better communicates multi-client support, and the suggested GitHub description is:

> Private, OAuth-scoped MCP mailbox and event bridge between ChatGPT Dots and local AI agents (OpenBot, Hermes and other MCP clients).

Suggested repository topics: `mcp`, `chatgpt-dots`, `openbot`, `hermes-agent`, `oauth2`, `agent-interop`, `webhooks`.

If the owner decides to rename later:

1. Audit clone URLs, pipeline references, self-hosted scripts, pinned paths, credential audiences and existing ChatGPT connectors; take backups and a rollback inventory.
2. Rename GitHub repository (and description/topics) in a separate approved maintenance operation, keeping code/package command stable for one deprecation window.
3. Update documentation, clone and remote URLs, release links, badges and pinned paths; verify GitHub redirects only for link navigation.
4. Rename the Python distribution, console executable or local launchd label **only** in later, independently tested migration PRs with backward-compatible aliases.
5. Never change the OAuth resource or ChatGPT event subscription merely to align a name. Audience changes require re-registration, explicit reauthorization, and live Hermes/OpenBot/Dots acceptance tests.
