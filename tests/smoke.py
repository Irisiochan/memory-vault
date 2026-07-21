import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


with tempfile.TemporaryDirectory(prefix="memory-vault-smoke-") as temp:
    vault = Path(temp) / "vault"

    os.environ["MEMORY_VAULT_PATH"] = str(vault)
    os.environ["VAULT_GIT_SYNC"] = "auto"

    spec = importlib.util.spec_from_file_location(
        "memory_vault_smoke_server", ROOT / "_meta" / "mcp_server.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load memory-vault server")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)

    assert server.VAULT == vault.resolve()
    assert server.DEFAULT_VAULT == ROOT.resolve()
    assert (vault / "_meta" / "vault_config.yaml").exists()
    assert (vault / "memories" / "owner-core.md").exists()
    assert not server._git_enabled(), "a parent source repo must never enable vault git sync"

    result = server.write_inbox(
        slug="smoke-note",
        title="Smoke note",
        content="The bundled vault stores private data outside the source tree.",
        tags=["smoke"],
        source="smoke-test",
    )
    notes = list((vault / "inbox").glob("*_smoke-note.md"))
    assert len(notes) == 1
    assert "已保存到本地 vault" in result

    promoted = server.promote_to_memory(notes[0].name)
    assert "已升级" in promoted
    assert (vault / "memories" / notes[0].name).exists()
    assert not notes[0].exists()

    context = server.get_context()
    assert "核心记忆" in context
    core_context = server.get_core_context()
    assert "核心记忆" in core_context
    assert "现在是" in server.get_turn_time()
    assert "任务快照日期" in server.get_task_context()

    outside = vault.parent / "escape-proof.md"
    rejected = [
        server.write_memory("../../escape-proof", "x", "x", []),
        server.write_inbox("../../escape-proof", "x", "x", []),
        server.write_diary("../../escape-proof", "x", "x"),
        server.add_task("../../escape-proof", "x", ""),
        server.promote_to_memory("../../escape-proof.md"),
    ]
    assert all("不合法" in result for result in rejected)
    assert not outside.exists(), "path traversal escaped the vault"

    for invalid in [".hidden", "has.dot", "slash/name", r"back\slash", "a" * 82]:
        assert "不合法" in server.write_memory(invalid, "x", "x", [])

    legacy = Path(temp) / "legacy-vault"
    (legacy / "_meta").mkdir(parents=True)
    (legacy / "memories").mkdir()
    (legacy / "_meta" / "vault_config.yaml").write_text(
        "owner: Legacy User\n"
        "timezone: UTC\n"
        "core_files:\n"
        "  - memories/User-core.md\n"
        "  - memories/User-ai-interaction-styles.md\n",
        encoding="utf-8",
    )
    (legacy / "memories" / "User-core.md").write_text("# Legacy core\n", encoding="utf-8")
    (legacy / "memories" / "User-ai-interaction-styles.md").write_text(
        "# Legacy interaction styles\n", encoding="utf-8"
    )

    os.environ["MEMORY_VAULT_PATH"] = str(legacy)
    legacy_spec = importlib.util.spec_from_file_location(
        "memory_vault_legacy_smoke_server", ROOT / "_meta" / "mcp_server.py"
    )
    if legacy_spec is None or legacy_spec.loader is None:
        raise RuntimeError("failed to load legacy memory-vault server")
    legacy_server = importlib.util.module_from_spec(legacy_spec)
    legacy_spec.loader.exec_module(legacy_server)

    assert not (legacy / "memories" / "owner-core.md").exists()
    assert not (legacy / "memories" / "owner-ai-interaction-styles.md").exists()
    legacy_core = legacy_server.get_core_context()
    assert "Legacy core" in legacy_core
    assert "Legacy interaction styles" in legacy_core

    git_vault = Path(temp) / "git-vault"
    os.environ["MEMORY_VAULT_PATH"] = str(git_vault)
    git_spec = importlib.util.spec_from_file_location(
        "memory_vault_git_smoke_server", ROOT / "_meta" / "mcp_server.py"
    )
    if git_spec is None or git_spec.loader is None:
        raise RuntimeError("failed to load git memory-vault server")
    git_server = importlib.util.module_from_spec(git_spec)
    git_spec.loader.exec_module(git_server)

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=git_vault,
            text=True,
            capture_output=True,
            check=True,
        )

    git("init")
    git("config", "user.name", "Memory Vault Smoke")
    git("config", "user.email", "memory-vault-smoke@example.invalid")
    git("add", "-A")
    git("commit", "-m", "test: initialize vault")
    (git_vault / "unrelated.txt").write_text("must remain untracked\n", encoding="utf-8")

    git_server.write_memory(
        "git-isolation",
        "Git isolation",
        "Only the generated memory may enter the automatic commit.",
        ["smoke"],
        "smoke-test",
    )
    committed = set(git("show", "--pretty=", "--name-only", "HEAD").stdout.splitlines())
    assert committed == {"memories/git-isolation.md"}, committed
    assert "unrelated.txt" not in git("ls-files").stdout.splitlines()

print("memory-vault smoke: ok")
