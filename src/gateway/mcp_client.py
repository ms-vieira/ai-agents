import anyio
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_servers.tools import tool_result_payload


async def call_mcp_tool(url: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            return tool_result_payload(result)


def call_mcp_tool_sync(url: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return anyio.run(call_mcp_tool, url, tool, arguments)
