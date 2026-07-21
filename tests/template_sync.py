from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELATIVE_FILES = [
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("_meta/cli/global-agent-workflow.md"),
    Path("_meta/rules.md"),
    Path("_meta/vault_config.yaml"),
    Path("memories/owner-core.md"),
    Path("memories/owner-ai-interaction-styles.md"),
]


for relative in RELATIVE_FILES:
    root_text = (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
    template_text = (ROOT / "template" / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
    package_text = (
        ROOT / "memory_vault_mcp" / "template" / relative
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert root_text == template_text, f"root/template drift: {relative}"
    assert template_text == package_text, f"template/package drift: {relative}"

print("memory-vault templates: in sync")
