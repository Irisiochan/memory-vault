import importlib.util
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier


ROOT = Path(__file__).resolve().parents[1]
WRITE_COUNT = 40


def load_server(vault: Path):
    os.environ["MEMORY_VAULT_PATH"] = str(vault)
    os.environ["VAULT_GIT_SYNC"] = "off"
    spec = importlib.util.spec_from_file_location(
        "memory_vault_concurrent_write_server",
        ROOT / "_meta" / "mcp_server.py",
    )
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(server)
    return server


with tempfile.TemporaryDirectory(prefix="memory-vault-concurrent-") as temp:
    vault = Path(temp) / "vault"
    server = load_server(vault)
    mutation_tools = (
        "write_inbox",
        "promote_to_memory",
        "write_memory",
        "update_memory",
        "archive_memory",
        "log_daily",
        "write_diary",
        "add_task",
        "update_task",
        "write_fact",
    )
    assert all(
        hasattr(getattr(server, tool), "__wrapped__")
        for tool in mutation_tools
    ), "every MCP mutation must use the shared serialized-mutation guard"

    created = server.write_memory(
        "concurrent-target",
        "Concurrent target",
        "base",
        ["test"],
        source="test",
    )
    assert "已写入" in created

    barrier = Barrier(WRITE_COUNT)

    def append(index: int) -> str:
        barrier.wait()
        return server.update_memory(
            "memories/concurrent-target.md",
            f"concurrent-marker-{index:02d}",
            source="concurrency-test",
        )

    with ThreadPoolExecutor(max_workers=WRITE_COUNT) as pool:
        results = list(pool.map(append, range(WRITE_COUNT)))

    assert all("已追加" in result for result in results), results
    stored = (vault / "memories" / "concurrent-target.md").read_text(
        encoding="utf-8"
    )
    missing = [
        marker
        for marker in (f"concurrent-marker-{index:02d}" for index in range(WRITE_COUNT))
        if stored.count(marker) != 1
    ]
    assert not missing, f"concurrent appends lost or duplicated: {missing}"

print("memory-vault concurrent writes: ok")
