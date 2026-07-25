"""Task context and task mutation tools."""

from __future__ import annotations

import datetime
import shutil

from _meta import vault_runtime as rt

_WEEKDAY_CN = "一二三四五六日"


def time_sensitive_lines() -> list[str]:
    dirpath = rt.VAULT / "tasks"
    if not dirpath.exists():
        return []
    today = rt.today()
    overdue, due_today, upcoming, no_due = [], [], [], []
    for markdown in sorted(dirpath.glob("*.md")):
        meta, body = rt.parse_frontmatter(
            markdown.read_text(encoding="utf-8", errors="replace")
        )
        if meta.get("status", "open") != "open":
            continue
        title = rt.extract_h1(body) or markdown.stem
        entry = f"**{title}** (`tasks/{markdown.name}`)"
        due = meta.get("due")
        if isinstance(due, str):
            try:
                due = datetime.date.fromisoformat(due)
            except ValueError:
                due = None
        if not isinstance(due, datetime.date):
            no_due.append(f"- {entry}（无期限，仍未完成）")
            continue
        delta = (due - today).days
        if delta < 0:
            overdue.append(
                f"- ⚠ {entry} 已过期 {-delta} 天——主动问问{rt.OWNER}完成了没"
            )
        elif delta == 0:
            due_today.append(f"- 🔔 {entry} 今天到期")
        elif delta <= 7:
            upcoming.append(
                f"- {entry} 还有 {delta} 天（{due.isoformat()} 星期{_WEEKDAY_CN[due.weekday()]}）"
            )
    items = overdue + due_today + upcoming + no_due
    return ["## ⏰ 时间敏感事项", "", *items, ""] if items else []


def add_task(
    slug: str,
    title: str,
    due: str,
    content: str = "",
    tags: list[str] | None = None,
    source: str = "unknown",
    source_inbox: str = "",
) -> str:
    """新增任务；hub-auto inbox 可通过 source_inbox 在成功后归档。"""
    from _meta import vault_writes

    filepath = rt.safe_generated_md("tasks", slug)
    if filepath is None:
        return rt.invalid_slug()
    if filepath.exists():
        if source_inbox and vault_writes.archived_hub_auto_source(source_inbox):
            return f"任务已存在且来源 inbox 已处理：tasks/{filepath.name}"
        return f"文件已存在：tasks/{filepath.name}，请换一个 slug。"
    due_value = due.strip()
    if due_value:
        try:
            datetime.date.fromisoformat(due_value)
        except ValueError:
            return "due 日期格式不对，需要 YYYY-MM-DD；无期限请传空字符串。"
    source_path = None
    if source_inbox:
        source_path = vault_writes.inbox_source(source_inbox)
        if source_path is None or not source_path.exists():
            archived = vault_writes.archived_hub_auto_source(source_inbox)
            return (
                f"来源 inbox 已处理：{archived.relative_to(rt.VAULT).as_posix()}"
                if archived
                else f"来源 inbox 不存在：{source_inbox}"
            )
        if not vault_writes.is_hub_auto_inbox(source_path):
            return "source_inbox 仅允许 type: hub-auto 的 inbox 文件。"
    today = rt.today().isoformat()
    tag_lines = "\n".join(f"  - {tag}" for tag in (tags or [])) or "  - task"
    meta = {
        "type": "task",
        "created": today,
        "due": due_value or "none",
        "status": "open",
        "source": source,
        "tags": tags or ["任务"],
    }
    body = f"# {title}"
    if content.strip():
        body += f"\n\n{content.strip()}"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(rt.rebuild_file(meta, body), encoding="utf-8")
    changed = [filepath]
    archive_note = ""
    if source_path is not None:
        try:
            archived = vault_writes.archive_processed_hub_auto_inbox(
                source_path, "converted-to-task", source
            )
        except OSError as exc:
            filepath.unlink(missing_ok=True)
            return f"任务创建失败：来源 inbox 归档失败：{exc}"
        changed.extend([source_path, archived])
        archive_note = f"，已归档来源 {archived.relative_to(rt.VAULT).as_posix()}"
    sync = rt.git_sync(f"auto: add task {slug} (source: {source})", *changed)
    return (
        f"已创建：tasks/{filepath.name}（due: {due_value or '无期限'}）"
        f"{archive_note} {sync}"
    )


def update_task(path: str, status: str, note: str = "", source: str = "unknown") -> str:
    """更新任务状态，可选追加处理说明。"""
    if status not in ("open", "done", "dropped"):
        return "status 只能是 open / done / dropped。"
    filepath = rt.safe_md(path)
    if filepath is None or not filepath.relative_to(rt.VAULT).as_posix().startswith("tasks/"):
        return "路径不合法：只能更新 tasks/ 下的 Markdown。"
    if not filepath.exists():
        return f"文件不存在：{path}"
    meta, body = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="replace")
    )
    if meta.get("type") != "task":
        return "目标文件不是 task。"
    relative = filepath.relative_to(rt.VAULT).as_posix()
    today = rt.today().isoformat()
    meta["status"] = status
    meta["updated"] = today
    meta["source"] = source
    if status == "done":
        meta["completed"] = today
    line = f"## 状态变更 {today} → {status}"
    if note.strip():
        line += f"\n\n{note.strip()}"
    updated = rt.rebuild_file(meta, f"{body}\n\n{line}")
    if status in ("done", "dropped"):
        meta["archived"] = today
        updated = rt.rebuild_file(meta, f"{body}\n\n{line}")
        retired = rt.VAULT / "_archive" / "retired"
        retired.mkdir(parents=True, exist_ok=True)
        destination = retired / filepath.name
        if destination.exists():
            suffix = today.replace("-", "")
            destination = retired / f"{filepath.stem}-{suffix}.md"
            counter = 2
            while destination.exists():
                destination = retired / f"{filepath.stem}-{suffix}-{counter}.md"
                counter += 1
        filepath.write_text(updated, encoding="utf-8")
        shutil.move(str(filepath), str(destination))
        sync = rt.git_sync(
            f"auto: task {relative} -> {status} (source: {source})",
            filepath,
            destination,
        )
        return (
            f"已更新并归档：{relative} → {status}；"
            f"{destination.relative_to(rt.VAULT).as_posix()} {sync}"
        )
    filepath.write_text(updated, encoding="utf-8")
    sync = rt.git_sync(
        f"auto: task {relative} -> {status} (source: {source})",
        filepath,
    )
    return f"已更新：{relative} → {status} {sync}"
