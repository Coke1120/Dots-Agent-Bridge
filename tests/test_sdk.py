"""Official MCP client against the ASGI app; all auth/data/callbacks synthetic."""
import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def test_official_client_round_trip(env):
    async def run():
        # In-process HTTP transport: no external network, listener or real account.
        transport = httpx.ASGITransport(app=env["client"].app)
        async with httpx.AsyncClient(transport=transport, headers={"Authorization": "Bearer alice-hermes"}) as http:
            async with streamable_http_client("https://bridge.example/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    assert init.protocolVersion == "2025-11-25"
                    tools = await session.list_tools()
                    assert "submit_message" in {t.name for t in tools.tools}
                    result = await session.call_tool("submit_message", {"content": "Synthetic SDK message", "idempotency_key": "sdk"})
                    assert not result.isError
                    request = json.loads(result.content[0].text)
                    readback = await session.call_tool("get_request", {"request_id": request["id"]})
                    assert json.loads(readback.content[0].text)["request"]["state"] == "queued"
    asyncio.run(run())
