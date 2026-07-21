import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def run() -> None:
    with tempfile.TemporaryDirectory(prefix="memory-vault-protocol-") as temp:
        vault = Path(temp) / "vault"
        env = os.environ.copy()
        env["MEMORY_VAULT_PATH"] = str(vault)
        env["VAULT_GIT_SYNC"] = "off"

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "_meta" / "mcp_server.py")],
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                required = {
                    "get_context",
                    "get_core_context",
                    "get_turn_time",
                    "get_task_context",
                    "search_vault",
                    "write_memory",
                }
                assert required <= names, required - names

                now = await session.call_tool("get_turn_time", {})
                assert not now.isError
                assert any("现在是" in block.text for block in now.content if hasattr(block, "text"))

                written = await session.call_tool(
                    "write_memory",
                    {
                        "slug": "protocol-smoke",
                        "title": "Protocol smoke",
                        "content": "Created through a real MCP stdio session.",
                        "tags": ["smoke"],
                        "source": "test",
                    },
                )
                assert not written.isError
                assert (vault / "memories" / "protocol-smoke.md").exists()


if __name__ == "__main__":
    asyncio.run(run())
    print("memory-vault protocol smoke: ok")
