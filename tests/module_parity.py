import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "_meta" / "mcp_server.py"
CONTEXT = ROOT / "_meta" / "vault_context.py"
WORKFLOW = ROOT / "_meta" / "cli" / "global-agent-workflow.md"
RULES = ROOT / "_meta" / "rules.md"

server_text = SERVER.read_text(encoding="utf-8")
context_text = CONTEXT.read_text(encoding="utf-8")
workflow_text = WORKFLOW.read_text(encoding="utf-8")
rules_text = RULES.read_text(encoding="utf-8")
tree = ast.parse(server_text)
assert len(server_text.splitlines()) < 300

module_names = {
    "vault_runtime",
    "vault_context",
    "vault_facts",
    "vault_search",
    "vault_writes",
    "vault_tasks",
    "vault_transport",
}
for name in module_names:
    assert (ROOT / "_meta" / f"{name}.py").exists(), name

registered = {
    node.id
    for node in ast.walk(tree)
    if isinstance(node, ast.Name)
}
required = {
    "get_context",
    "get_core_context",
    "get_turn_time",
    "get_task_context",
    "write_fact",
    "get_facts",
    "write_memory",
    "add_task",
    "search_vault",
}
assert required <= registered, required - registered

for marker in (
    "<VAULT_CORE_PRELOADED>",
    "<VAULT_CORE_PRELOAD_FALLBACK>",
    "<TURN_TIME_PRELOADED>",
):
    assert marker in server_text, f"MCP instructions missing {marker}"
    assert marker in context_text, f"context tool docs missing {marker}"
    assert marker in workflow_text, f"workflow missing {marker}"
    assert marker in rules_text, f"rules missing {marker}"

assert "write_fact" in rules_text

print("memory-vault module parity: ok")
