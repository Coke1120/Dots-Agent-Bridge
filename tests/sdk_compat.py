"""Test harness compatibility for official MCP client SDKs 1.x and 2.x.

This adapts Python client API names only; it does not alter the server's wire protocol.
"""
from contextlib import asynccontextmanager
from importlib.metadata import version

from mcp.client.streamable_http import streamable_http_client

if int(version('mcp').split('.')[0]) >= 2:
    import httpx2 as httpx
else:
    import httpx


@asynccontextmanager
async def sdk_transport(url, http_client):
    async with streamable_http_client(url, http_client=http_client) as streams:
        # SDK 1 returns (read, write, session_id); SDK 2 iterates (read, write).
        read, write, *_ = tuple(streams)
        yield read, write


def field(value, camel, snake):
    return getattr(value, snake, getattr(value, camel, None))
