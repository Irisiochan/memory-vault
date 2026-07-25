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
                    "get_facts",
                    "write_fact",
                    "search_vault",
                    "write_memory",
                    "add_task",
                    "update_task",
                }
                assert required <= names, required - names

                fact = await session.call_tool(
                    "write_fact",
                    {
                        "domain": "preferences",
                        "key": "protocol-smoke",
                        "value": "works",
                        "source_refs": ["tests/protocol_smoke.py"],
                        "priority": "high",
                        "source": "test",
                    },
                )
                assert not fact.isError
                duplicate = await session.call_tool(
                    "write_fact",
                    {
                        "domain": "preferences",
                        "key": "protocol-smoke",
                        "value": "works",
                        "source_refs": ["tests/protocol_smoke.py"],
                        "priority": "high",
                        "source": "test",
                    },
                )
                duplicate_text = "\n".join(
                    block.text for block in duplicate.content if hasattr(block, "text")
                )
                assert "无需重复写入" in duplicate_text

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

                task = await session.call_tool(
                    "add_task",
                    {
                        "slug": "protocol-archive",
                        "title": "Protocol archive",
                        "due": "",
                        "source": "test",
                    },
                )
                assert not task.isError

                completed = await session.call_tool(
                    "update_task",
                    {
                        "path": "tasks/protocol-archive.md",
                        "status": "done",
                        "note": "Completed through a real MCP session.",
                        "source": "test",
                    },
                )
                assert not completed.isError
                assert not (vault / "tasks" / "protocol-archive.md").exists()
                assert (
                    vault / "_archive" / "retired" / "protocol-archive.md"
                ).exists()

                tasks = await session.call_tool("get_task_context", {})
                task_text = "\n".join(
                    block.text for block in tasks.content if hasattr(block, "text")
                )
                assert "Protocol archive" not in task_text


if __name__ == "__main__":
    asyncio.run(run())
    print("memory-vault protocol smoke: ok")
