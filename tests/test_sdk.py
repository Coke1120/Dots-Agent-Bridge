"""Official MCP client against the ASGI app; all auth/data/callbacks synthetic."""
import asyncio
import json

from mcp import ClientSession
from sdk_compat import field, httpx, sdk_transport


def test_official_client_round_trip(env):
    async def run():
        # In-process HTTP transport: no external network, listener or real account.
        transport = httpx.ASGITransport(app=env["client"].app)
        async with httpx.AsyncClient(transport=transport, headers={"Authorization": "Bearer alice-hermes"}) as http:
            async with sdk_transport("https://bridge.example/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    assert field(init, "protocolVersion", "protocol_version") == "2025-11-25"
                    tools = await session.list_tools()
                    assert "submit_message" in {t.name for t in tools.tools}
                    result = await session.call_tool("submit_message", {"content": "Synthetic SDK message", "idempotency_key": "sdk"})
                    assert not field(result, "isError", "is_error")
                    request = json.loads(result.content[0].text)
                    readback = await session.call_tool("get_request", {"request_id": request["id"]})
                    assert json.loads(readback.content[0].text)["request"]["state"] == "queued"
    asyncio.run(run())
