from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

for removed in ("app", "easy"):
    assert not (ROOT / removed).exists(), f"legacy frontend path returned: {removed}/"

legacy_workflow = ROOT / ".github" / "workflows" / "build-windows-portable.yml"
assert not legacy_workflow.exists(), "legacy frontend build workflow returned"

quality = (ROOT / ".github" / "workflows" / "quality.yml").read_text(
    encoding="utf-8"
)
for frontend_marker in ("setup-node", "npm ", "app/", "easy/"):
    assert frontend_marker not in quality, (
        f"frontend marker returned to Memory Vault CI: {frontend_marker}"
    )

readme = (ROOT / "README.md").read_text(encoding="utf-8")
first_screen = readme[:1600]
assert "通用 AI 记忆基础设施" in first_screen
assert "memory-vault-mcp" in first_screen
assert "Irisiochan/ai-hub-public" in readme
assert "docs/migration-v0.5.md" in readme

pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
assert 'version = "0.7.1"' in pyproject

package_init = (ROOT / "memory_vault_mcp" / "__init__.py").read_text(encoding="utf-8")
assert '__version__ = "0.7.1"' in package_init

print("memory-vault repository boundary: ok")
