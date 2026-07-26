import datetime
import importlib.util
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_server(vault: Path):
    os.environ["MEMORY_VAULT_PATH"] = str(vault)
    os.environ["VAULT_GIT_SYNC"] = "off"
    spec = importlib.util.spec_from_file_location(
        "memory_vault_fact_test_server", ROOT / "_meta" / "mcp_server.py"
    )
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(server)
    return server


with tempfile.TemporaryDirectory(prefix="memory-vault-facts-") as temp:
    vault = Path(temp) / "vault"
    server = load_server(vault)

    created = server.write_fact(
        "preferences",
        "theme",
        "dark",
        ["tests/fact_layer.py"],
        priority="high",
        source="test",
    )
    assert "已写入 fact" in created
    duplicate = server.write_fact(
        "preferences",
        "theme",
        "dark",
        ["tests/fact_layer.py"],
        priority="high",
        source="test",
    )
    assert "无需重复写入" in duplicate
    replaced = server.write_fact(
        "preferences",
        "theme",
        "light",
        ["tests/fact_layer.py"],
        priority="high",
        source="test",
    )
    assert "取代 1 个旧 active 版本" in replaced

    _, stored = server._load_fact_domain("preferences")
    active = [fact for fact in stored if fact["status"] == "active"]
    superseded = [fact for fact in stored if fact["status"] == "superseded"]
    assert len(active) == 1 and active[0]["value"] == "light"
    assert len(superseded) == 1
    assert superseded[0]["superseded_by"] == active[0]["id"]

    facts = server.get_facts("preferences")
    assert "preferences.theme" in facts and "light" in facts
    compact = server.get_core_context(source="compact")
    assert "preferences.theme" in compact and "light" in compact
    assert "dark" not in compact

    today = server._today()
    expired = server.write_fact(
        "preferences",
        "expired-example",
        "expired-value-must-not-be-active",
        ["tests/fact_layer.py"],
        priority="pinned",
        valid_from=(today - datetime.timedelta(days=2)).isoformat(),
        valid_until=(today - datetime.timedelta(days=1)).isoformat(),
        source="test",
    )
    assert "已写入 fact" in expired
    future = server.write_fact(
        "preferences",
        "future-example",
        "future-value-must-not-be-active",
        ["tests/fact_layer.py"],
        priority="pinned",
        valid_from=(today + datetime.timedelta(days=1)).isoformat(),
        source="test",
    )
    assert "已写入 fact" in future

    effective = server.get_facts("preferences")
    assert "expired-value-must-not-be-active" not in effective
    assert "future-value-must-not-be-active" not in effective
    audit = server.get_facts("preferences", status="all")
    assert "expired-value-must-not-be-active" in audit
    assert "future-value-must-not-be-active" in audit
    compact = server.get_core_context(source="compact")
    assert "expired-value-must-not-be-active" not in compact
    assert "future-value-must-not-be-active" not in compact

    search = server.search_vault("preferences theme")
    assert "memories/facts/preferences.md" in search
    blocked_update = server.update_memory(
        "memories/facts/preferences.md", "unsafe", source="test"
    )
    assert "write_fact" in blocked_update
    blocked_archive = server.archive_memory(
        "memories/facts/preferences.md", "unsafe", source="test"
    )
    assert "不允许整域归档" in blocked_archive

    malformed = vault / "memories" / "facts" / "work.md"
    malformed.write_text(
        "---\ntype: fact-domain\n---\n\n<!-- fact:begin -->\n```yaml\nbad: [\n```\n<!-- fact:end -->\n",
        encoding="utf-8",
    )
    rejected = server.write_fact(
        "work",
        "current",
        "safe",
        ["tests/fact_layer.py"],
        source="test",
    )
    assert "拒绝写入" in rejected

print("memory-vault fact layer: ok")
