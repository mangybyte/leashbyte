"""Check MCP authentication, tool listing and one fetch call against Compose."""

import asyncio
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    key = os.environ["LEASHBYTE_API_KEY"]
    port = os.environ.get("LEASHBYTE_MCP_PORT", "8081")
    url = f"http://127.0.0.1:{port}/mcp"
    async with httpx.AsyncClient() as http:
        for headers in ({}, {"X-API-Key": "wrong-key"}):
            response = await http.post(url, json={}, headers=headers)
            assert response.status_code == 401, response.status_code
    async with streamablehttp_client(url, headers={"X-API-Key": key}) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"search", "fetch", "create_run", "browser_action"} <= names
            assert not any("vault" in name or "cdp" in name for name in names)
            result = await session.call_tool(
                "fetch",
                {"url": "http://host.docker.internal:18765/html", "format": "markdown"},
            )
            assert not result.isError
            content = result.structuredContent
            assert content is not None and content["status"] == 200
            assert "LeashByte controlled page" in content["content"]
            print(f"MCP round trip passed with {len(names)} tools and one fetch.")


if __name__ == "__main__":
    asyncio.run(main())
