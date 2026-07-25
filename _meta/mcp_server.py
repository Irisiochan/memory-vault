"""Memory Vault MCP server and compatibility entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from _meta import vault_context
from _meta import vault_facts
from _meta import vault_runtime as rt
from _meta import vault_search
from _meta import vault_tasks
from _meta import vault_transport
from _meta import vault_writes


rt.configure()

mcp = FastMCP(
    "memory-vault",
    instructions=(
        "你已连接到用户拥有的 Memory Vault。新会话先调用 get_context、"
        "get_turn_time、get_task_context；后续每轮只调用 get_turn_time，"
        "任务相关或跨日时再刷新 get_task_context。确认且结构化的事实优先用 "
        "write_fact；叙事记忆用 write_memory；不确定内容用 write_inbox。"
    ),
)


get_core_context = vault_context.get_core_context
get_context = vault_context.get_context
get_turn_time = vault_context.get_turn_time
get_task_context = vault_context.get_task_context
get_facts = vault_facts.get_facts
write_fact = vault_facts.write_fact
list_memories = vault_search.list_memories
search_vault = vault_search.search_vault
read_file = vault_search.read_file
get_related = vault_search.get_related
write_inbox = vault_writes.write_inbox
list_inbox = vault_writes.list_inbox
promote_to_memory = vault_writes.promote_to_memory
write_memory = vault_writes.write_memory
update_memory = vault_writes.update_memory
archive_memory = vault_writes.archive_memory
log_daily = vault_writes.log_daily
write_diary = vault_writes.write_diary
add_task = vault_tasks.add_task
update_task = vault_tasks.update_task

for tool in (
    get_core_context,
    get_context,
    get_turn_time,
    get_task_context,
    get_facts,
    write_fact,
    list_memories,
    search_vault,
    read_file,
    get_related,
    write_inbox,
    list_inbox,
    promote_to_memory,
    write_memory,
    update_memory,
    archive_memory,
    log_daily,
    write_diary,
    add_task,
    update_task,
):
    mcp.tool()(tool)


# Compatibility exports for existing direct-import clients and smoke tests.
VAULT = rt.VAULT
DEFAULT_VAULT = rt.DEFAULT_VAULT
OWNER = rt.OWNER
CORE_FILES = rt.CORE_FILES
ACTIVE_DIRS = rt.ACTIVE_DIRS
FACTS_DIR = rt.FACTS_DIR
FACT_DOMAINS = vault_facts.FACT_DOMAINS
_git_enabled = rt.git_enabled
_today = rt.today
_now = rt.now
_load_fact_domain = vault_facts.load_fact_domain
_all_facts = vault_facts.all_facts


def main(argv: list[str] | None = None) -> None:
    vault_transport.main(mcp, argv)


if __name__ == "__main__":
    main()
