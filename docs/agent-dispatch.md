# Dot to OpenBot

A ChatGPT dot can send work to a specific OpenBot agent through the bridge's existing MCP connection. The bridge delivers the task to the agent's normal conversation queue and retrieves the answer from that delivery's turn. The dot checks its bridge task ID for the result. This direction can operate independently of the `request.created` subscription used for messages going the other way.

## Enable dispatch for one dot

1. In OpenBot, select the intended agent and enable **Settings → Permissions → Local scripts**. Enable it only for agents intended for local automation. OpenBot gives local scripts a host credential with additional powers, including responding to approval requests. The bridge uses the message and delivery routes. Approvals and questions the agent raises still go through the ordinary OpenBot workflow. [OpenBot Local scripts permissions](https://github.com/nightly-labs/openbot/blob/3554f248a01ad726a8dafefa0e0701a0e1bc44f4/docs/automation.md#L57-L92)
2. Run the bridge under the OpenBot host's OS user and in the same loopback network namespace. Set `BRIDGE_OPENBOT_AUTOMATION_DIR` in the bridge **service's environment** to OpenBot's private automation directory. The setting applies to both `hermes-dots-bridge serve` and `bridge.app:production_app`. On macOS the usual directory is `~/Library/Application Support/OpenBot/automation`. On Linux it is `~/.config/OpenBot/automation`. Supply the absolute path. When OpenBot runs in Docker, provide the bridge process with the same loopback namespace and access to the original private directory. Mounting those files alone leaves the two loopback interfaces separate. OpenBot rotates its port and token on restart. [OpenBot discovery files](https://github.com/nightly-labs/openbot/blob/3554f248a01ad726a8dafefa0e0701a0e1bc44f4/docs/automation.md#L78-L98)
3. Obtain the exact OpenBot agent ID. From that same OS user and network namespace, the following read-only command lists agents with Local scripts enabled. Select the target's `id` from the response.

   ```sh
   curl -fsS -H @"$BRIDGE_OPENBOT_AUTOMATION_DIR/headers" \
     "$(cat "$BRIDGE_OPENBOT_AUTOMATION_DIR/url")/v1/agents"
   ```

4. Add `dispatch_targets` to the intended dot's existing verified policy grant. For example, the field `"dispatch_targets": ["agent-1"]` allows that exact agent ID. Preserve the existing subject, OAuth client ID, owner, role and authorization flags. Grant the dot's OAuth client the additional API scope `bridge:dispatch` at the issuer, request it on the dot's connection, and obtain a new access token carrying the scope. Leave the automation-directory setting unset for deployments that only need the original mailbox.

Dedicate each native OpenBot agent to one bridge owner for that agent's lifetime. Its ordinary conversation retains prior messages. Provision a fresh OpenBot agent ID when onboarding a different owner.

## Send and read a task

| Dot tool | Use |
| --- | --- |
| `list_dispatch_targets()` | List the target IDs authorized for this dot. |
| `dispatch_task(target, content, idempotency_key)` | Submit the user-authorized task and save the returned `task_id`. Retry ambiguous submissions using the same target, content and key. |
| `get_task(task_id)` | Read the saved state, result or reason. For a pending task, check the same ID again later. |
| `cancel_task(task_id)` | Cancel while queued and before the bridge has started sending to OpenBot. |

The worker persists the task before sending it. It uses the task UUID as the OpenBot message's `clientMessageId`, retains the returned delivery ID and reads that delivery for the agent's answer. OpenBot deduplicates a message key for 24 hours. The bridge limits its dispatch window to one hour and retains task history for seven days. Task keys belong to the initiating dot's owner, subject and OAuth client. [OpenBot message and delivery contract](https://github.com/nightly-labs/openbot/blob/3554f248a01ad726a8dafefa0e0701a0e1bc44f4/docs/automation.md#L155-L177)

Report an answer to the user when `get_task` returns `state: completed` with text in `result`. If tracking ends with `unknown`, check the target agent's conversation before sending new work. A task may already have run. A task that expires before the first send can be submitted again as new work. Removing a target from the Dot's policy stops future handoffs to that target. Turning off the adapter pauses delivery; previously queued tasks still within their deadline can resume if it is enabled again. Purging bridge task records does not clear OpenBot's own conversation history.

## End-to-end acceptance check

With the intended account and agent configured, ask the dot to send harmless text such as “Reply exactly `openbot-dispatch-ok`” to the allowed target. Save the task ID, confirm the target agent received the message once, then call `get_task` until the completed result contains the requested text. A queued task or an OpenBot delivery receipt establishes acceptance only. Live account verification is pending.
