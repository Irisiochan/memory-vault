import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "_meta" / "mcp_server.py"

tree = ast.parse(SERVER.read_text(encoding="utf-8"))
assert len(SERVER.read_text(encoding="utf-8").splitlines()) < 300

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

print("memory-vault module parity: ok")
