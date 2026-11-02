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
                    empty = await session.call_tool("list_requests")
                    assert json.loads(empty.content[0].text)["requests"] == []
                    result = await session.call_tool("submit_message", {"content": "Synthetic SDK message", "idempotency_key": "sdk"})
                    assert not field(result, "isError", "is_error")
                    request = json.loads(result.content[0].text)
                    readback = await session.call_tool("get_request", {"request_id": request["id"]})
                    assert json.loads(readback.content[0].text)["request"]["state"] == "queued"
    asyncio.run(run())


def test_sdk2_modern_auto_discovery(env):
    """SDK 2 Client, not its legacy ClientSession facade, exercises real MCP2 wire."""
    from importlib.metadata import version
    if int(version('mcp').split('.')[0]) < 2:
        # SDK 1 has no modern Client API; its legacy handshake is tested above.
        import pytest
        pytest.skip("SDK 1 has no modern Client API; legacy round trip is tested separately")
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from bridge.wire import MODERN

    async def run():
        seen = []
        async def capture(request):
            seen.append(json.loads(request.content))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env['client'].app),
                                     headers={'Authorization': 'Bearer alice-hermes'},
                                     event_hooks={'request': [capture]}) as http:
            transport = streamable_http_client('https://bridge.example/mcp', http_client=http)
            async with Client(transport, mode='auto') as client:
                tools = await client.list_tools()
                assert 'submit_message' in {t.name for t in tools.tools}
                empty = await client.call_tool('list_requests')
                assert json.loads(empty.content[0].text)['requests'] == []
                result = await client.call_tool('submit_message', {'content': 'SDK2 modern synthetic', 'idempotency_key': 'modern-sdk'})
                assert not result.is_error
                rid = json.loads(result.content[0].text)['id']
                readback = await client.call_tool('get_request', {'request_id': rid})
                assert json.loads(readback.content[0].text)['request']['state'] == 'queued'
        assert seen[0]['method'] == 'server/discover'
        assert not any(m['method'] == 'initialize' for m in seen)
        assert all(m['params']['_meta']['io.modelcontextprotocol/protocolVersion'] == MODERN for m in seen)
    asyncio.run(run())
