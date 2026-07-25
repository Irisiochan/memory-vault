import importlib.util
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_server(vault: Path):
    os.environ["MEMORY_VAULT_PATH"] = str(vault)
    os.environ["VAULT_GIT_SYNC"] = "off"
    spec = importlib.util.spec_from_file_location(
        "memory_vault_hub_auto_test_server", ROOT / "_meta" / "mcp_server.py"
    )
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(server)
    return server


def hub_note(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
type: inbox
created: 2026-07-25
source: hub-auto
tags:
  - hub-auto
---

# {title}

Captured by AI Hub.
""",
        encoding="utf-8",
    )


with tempfile.TemporaryDirectory(prefix="memory-vault-hub-auto-") as temp:
    vault = Path(temp) / "vault"
    server = load_server(vault)

    task_source = vault / "inbox" / "2026-07-25_follow-up.md"
    hub_note(task_source, "Follow up")
    created = server.add_task(
        "follow-up",
        "Follow up",
        "",
        source="test",
        source_inbox=task_source.name,
    )
    assert "已归档来源" in created
    assert not task_source.exists()
    assert (vault / "tasks" / "follow-up.md").exists()
    archives = list((vault / "_archive" / "retired").glob(f"*_{task_source.name}"))
    assert len(archives) == 1
    repeated = server.add_task(
        "follow-up",
        "Follow up",
        "",
        source="test",
        source_inbox=task_source.name,
    )
    assert "已处理" in repeated

    memory_source = vault / "inbox" / "2026-07-25_confirmed.md"
    hub_note(memory_source, "Confirmed")
    promoted = server.promote_to_memory(memory_source.name)
    assert "已归档源" in promoted
    assert not memory_source.exists()
    assert (vault / "memories" / memory_source.name).exists()

    normal = vault / "inbox" / "2026-07-25_manual.md"
    normal.write_text(
        "---\ntype: inbox\ncreated: 2026-07-25\nsource: test\ntags: []\n---\n\n# Manual\n",
        encoding="utf-8",
    )
    rejected = server.add_task(
        "manual",
        "Manual",
        "",
        source="test",
        source_inbox=normal.name,
    )
    assert "仅允许" in rejected
    assert normal.exists()

print("memory-vault hub-auto lifecycle: ok")
