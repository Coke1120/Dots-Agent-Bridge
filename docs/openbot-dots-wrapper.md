# Add Dots to the OpenBot agent list

The optional Dots wrapper gives OpenBot a normal agent chat that relays the user's messages to their existing ChatGPT Dot and displays the stored replies. It uses an OpenBot model to call the existing mailbox tools; it is not a new provider or a deterministic transport. The Dot generates the answer, but the OpenBot model still mediates what is sent and displayed. Use a private, owner-controlled OpenBot instance or chat.

## Import and connect

1. From the repository root, build an archive containing the import manifest at its root:

   ```sh
   python3 -m zipfile -c Dots-OpenBot-Agent.zip examples/openbot-dots-wrapper/openbot-import.json
   python3 -m zipfile -t Dots-OpenBot-Agent.zip
   ```

2. In OpenBot, open **Server settings → Import** and select the ZIP. Review the single **Dots** agent before importing. The archive contains instructions only, not accounts, tokens, routines or workspace files.
3. Configure the Bridge as an HTTP MCP server using the [OpenBot mailbox guide](openbot.md), including the distinct OpenBot OAuth client with `bridge:submit bridge:read`. Give this agent access to that server and do not grant unnecessary MCP access. Confirm the intended Dot has an active `request.created` subscription. The wrapper does not need Local scripts or `bridge:dispatch`.
4. Open the **Dots** agent in a private one-to-one chat. The wrapper is intended to forward only the current user message plus any text explicitly selected for forwarding. Do not paste credentials or use this model-mediated wrapper for content you do not want processed by the configured OpenBot provider.

## Verify a real round trip

- Send “Reply exactly `dots-wrapper-ok`”. On the Bridge, confirm one request and a stored final reply from the real Dot. In OpenBot, confirm the same text appears in the Dots chat. Queue or webhook acknowledgement alone does not pass.
- Send a follow-up and confirm a new request ID, the same Bridge conversation ID, and a new final reply. On timeout, enter `/status` to check the same request without submitting it again.
- Check that an expired sign-in or missing event subscription surfaces as a blocker rather than a wrapper-generated answer.

This example does not install anything, sync the native ChatGPT transcript, or push unsolicited messages into an idle OpenBot chat. [Agent Dispatch](agent-dispatch.md) targets separate OpenBot agents and is not a mechanism for injecting a Dot message into this relay. A dedicated ACP adapter and live OpenBot validation are outside this example.
