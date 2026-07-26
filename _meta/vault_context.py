"""Stable, turn-time, and task context tools."""

from __future__ import annotations

from _meta import vault_facts as facts
from _meta import vault_runtime as rt
from _meta import vault_tasks


def get_core_context(
    max_chars_per_file: int | str = 1800,
    source: str = "narrative",
) -> str:
    """读取 narrative 核心文件或 compact 结构化事实。

    宿主已注入 <VAULT_CORE_PRELOADED> 时不要重复调用本工具。
    """
    if isinstance(max_chars_per_file, str):
        source = max_chars_per_file
        max_chars_per_file = 1800
    if source not in ("narrative", "compact"):
        return "source 只能是 narrative 或 compact。"
    rt.pull_if_stale()
    if source == "compact":
        selected = [
            fact for fact in facts.all_facts()
            if fact.get("status") == "active"
            and fact.get("priority") in ("pinned", "high")
        ]
        return facts.render_compact_fact_context(selected)
    content = rt.read_core_context(max_chars_per_file)
    return content or "当前没有 narrative 核心文件。"


def get_context() -> str:
    """获取稳定核心记忆和其余长期记忆索引。

    每个新任务首次处理时先检查宿主预载标记：已有
    <VAULT_CORE_PRELOADED> 时不要重复调用 get_context 或
    get_core_context；出现 <VAULT_CORE_PRELOAD_FALLBACK> 或没有 core
    预载标记时才调用本工具。时间和任务快照按各自的预载状态单独获取。
    """
    rt.pull_if_stale()
    parts = [
        f"# {rt.OWNER} 核心记忆上下文",
        "",
        "⚠ source 只是记忆的写入来源，不是当前 AI 的身份。你的身份由你自己的指令和身份配置决定。",
        "",
    ]
    core_set = {str(path) for path in rt.CORE_FILES}
    for relative in rt.CORE_FILES:
        filepath = rt.safe_md(str(relative))
        if filepath and filepath.exists():
            parts.extend(
                [
                    filepath.read_text(encoding="utf-8", errors="replace").strip(),
                    "",
                    "---",
                    "",
                ]
            )
    others = [
        item for item in rt.scan_files(["memories"])
        if item["path"] not in core_set
    ]
    if others:
        parts.extend(["## 其余记忆清单（用 read_file 按需读取）", ""])
        for item in others:
            tags = ", ".join(str(tag) for tag in item["tags"]) if item["tags"] else ""
            suffix = f"  [{tags}]" if tags else ""
            parts.append(f"- **{item['title']}** (`{item['path']}`){suffix}")
    return "\n".join(parts)


def get_turn_time() -> str:
    """返回当前 vault 时区的一行短时间戳。

    仅当本轮没有 <TURN_TIME_PRELOADED> 时调用；宿主已注入该标记时不要
    重复调用。
    """
    return rt.now_line()


def get_task_context() -> str:
    """返回按当前 vault 日期计算的未完成任务快照。

    每个新任务首次处理时调用一次；<VAULT_CORE_PRELOADED> 不包含任务
    快照。仅当宿主明确标记任务快照也已预载时不重复调用。之后只在跨日、
    上下文恢复、任务相关话题或任务变更后刷新。
    """
    rt.pull_if_stale()
    snapshot_date = rt.today().isoformat()
    lines = vault_tasks.time_sensitive_lines()
    timezone = getattr(rt.TZ, "key", str(rt.TZ))
    if not lines:
        return f"任务快照日期：{snapshot_date}（{timezone}）\n当前没有未完成任务。"
    return "\n".join(
        [f"任务快照日期：{snapshot_date}（{timezone}）", "", *lines]
    )
