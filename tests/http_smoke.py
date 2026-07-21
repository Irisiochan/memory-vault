import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def call_server(url: str) -> None:
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "get_turn_time" in {tool.name for tool in tools.tools}
            result = await session.call_tool("get_task_context", {})
            assert not result.isError


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="memory-vault-http-") as temp:
        port = free_port()
        env = os.environ.copy()
        env["MEMORY_VAULT_PATH"] = str(Path(temp) / "vault")
        env["VAULT_GIT_SYNC"] = "off"
        env.pop("VAULT_TOKEN", None)
        env.pop("VAULT_ALLOWED_HOSTS", None)

        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "_meta" / "mcp_server.py"),
                "--http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    error = process.stderr.read() if process.stderr else ""
                    raise RuntimeError(f"HTTP MCP exited early: {error}")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.1)
            else:
                raise TimeoutError("HTTP MCP did not start within 15 seconds")

            asyncio.run(call_server(f"http://127.0.0.1:{port}/mcp"))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    run()
    print("memory-vault HTTP smoke: ok")
