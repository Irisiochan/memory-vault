import importlib.util
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUFFIX = "…（详情用 search_vault / read_file 检索）"


def load_server(vault: Path):
    os.environ["MEMORY_VAULT_PATH"] = str(vault)
    os.environ["VAULT_GIT_SYNC"] = "off"
    spec = importlib.util.spec_from_file_location(
        "memory_vault_compact_note_test_server", ROOT / "_meta" / "mcp_server.py"
    )
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(server)
    return server


with tempfile.TemporaryDirectory(prefix="memory-vault-compact-note-") as temp:
    vault = Path(temp) / "vault"
    server = load_server(vault)

    long_note = "a" * 121
    exact_note = "b" * 120
    short_note = "short note"
    # CJK: 130 chars (>120 chars, far fewer bytes-wise ambiguous) proves char count.
    cjk_note = "中" * 130
    value_notes = "v" * 200

    for key, value, note in [
        ("long-note", "v1", long_note),
        ("exact-note", "v2", exact_note),
        ("short-note", "v3", short_note),
        ("cjk-note", "v4", cjk_note),
        # Top-level note short; over-long notes live inside value only.
        ("value-notes", {"notes": value_notes, "nested": {"note": "x" * 300}}, short_note),
    ]:
        created = server.write_fact(
            "preferences",
            key,
            value,
            ["tests/compact_note_truncation.py"],
            priority="high",
            note=note,
            source="test",
        )
        assert "已写入 fact" in created, created

    compact = server.get_core_context(source="compact")

    # 1. 超长顶层 note：截断为前 120 字符 + 提示，且全文不再出现。
    assert long_note[:120] + SUFFIX in compact
    assert long_note not in compact
    # CJK 按字符计：130 个字符同样截断到 120 字符 + 提示。
    assert cjk_note[:120] + SUFFIX in compact
    assert cjk_note not in compact

    # 2. 恰好 120 与短 note：原样输出，不带提示。
    assert f"  - note: {exact_note}\n" in compact
    assert f"  - note: {short_note}\n" in compact
    assert exact_note + SUFFIX not in compact

    # 3. value 内超长 notes 不受影响：完整出现在 compact 输出中。
    assert value_notes in compact
    assert "x" * 300 in compact

    # 4. 渲染后存储本体完整：重读 fact，顶层 note 与 value 均未被截断。
    _, stored = server._load_fact_domain("preferences")
    by_key = {fact["key"]: fact for fact in stored}
    assert by_key["long-note"]["note"] == long_note
    assert by_key["cjk-note"]["note"] == cjk_note
    assert by_key["exact-note"]["note"] == exact_note
    assert by_key["value-notes"]["value"] == {
        "notes": value_notes,
        "nested": {"note": "x" * 300},
    }

    # 5. full 路径（get_facts）不截断：超长顶层 note 全文可见。
    full = server.get_facts("preferences")
    assert long_note in full
    assert long_note + SUFFIX not in full

print("memory-vault compact note truncation: ok")
