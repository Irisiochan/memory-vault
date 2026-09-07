"""Task context and task mutation tools."""

from __future__ import annotations

import datetime
import shutil

from _meta import vault_runtime as rt

_WEEKDAY_CN = "一二三四五六日"
_DORMANT_AFTER_DAYS = 14


def _coerce_iso_date(value: object) -> datetime.date | None:
    """Normalize YAML date/datetime values and quoted ISO date strings."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def time_sensitive_lines() -> list[str]:
    dirpath = rt.VAULT / "tasks"
    if not dirpath.exists():
        return []
    today = rt.today()
    overdue, due_today, upcoming, no_due, dormant_titles = [], [], [], [], []
    for markdown in sorted(dirpath.glob("*.md")):
        safe = rt.safe_md(f"tasks/{markdown.name}")
        if safe is None or not safe.is_file():
            continue
        meta, body = rt.parse_frontmatter(
            safe.read_text(encoding="utf-8", errors="replace")
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
            flag = meta.get("dormant")
            touched = _coerce_iso_date(meta.get("updated")) or _coerce_iso_date(
                meta.get("created")
            )
            if flag is True:
                is_dormant = True
            elif flag is False:
                is_dormant = False
            else:
                is_dormant = (
                    touched is not None
                    and (today - touched).days > _DORMANT_AFTER_DAYS
                )
            if is_dormant:
                dormant_titles.append(title)
            else:
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
    if dormant_titles:
        items.append(
            f"- 💤 另有 {len(dormant_titles)} 条无期限任务超过 "
            f"{_DORMANT_AFTER_DAYS} 天未更新，已转冬眠层只列标题"
            "（需要详情时 search_vault / read_file）："
            + "、".join(dormant_titles)
        )
    return ["## ⏰ 时间敏感事项", "", *items, ""] if items else []


@rt.serialized_mutation
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


def _task_update_result(
    ok: bool,
    code: str,
    message: str,
    *,
    path: str = "",
    status: str = "",
    due: str | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {}
    if path:
        data["path"] = path
    if status:
        data["status"] = status
    if due is not None:
        data["due"] = due
    return {"ok": ok, "code": code, "message": message, "data": data}


@rt.serialized_mutation
def update_task_result(
    path: str,
    status: str,
    note: str = "",
    source: str = "unknown",
    due: str | None = None,
) -> dict[str, object]:
    """原子更新任务状态、可选 due 与处理说明，并返回机器可判定结果。"""
    if status not in ("open", "done", "dropped"):
        return _task_update_result(
            False,
            "invalid_status",
            "status 只能是 open / done / dropped。",
            path=path,
            status=status,
        )
    due_value: str | None = None
    if due is not None:
        candidate = due.strip()
        if candidate.lower() in ("", "none", "null", "~"):
            due_value = "none"
        else:
            try:
                datetime.date.fromisoformat(candidate)
            except ValueError:
                return _task_update_result(
                    False,
                    "invalid_due",
                    "due 日期格式不对，需要 YYYY-MM-DD；清除期限请传 none。",
                    path=path,
                    status=status,
                    due=candidate,
                )
            due_value = candidate
    filepath = rt.safe_md(path)
    if filepath is None or not filepath.relative_to(rt.VAULT).as_posix().startswith("tasks/"):
        return _task_update_result(
            False,
            "invalid_path",
            "路径不合法：只能更新 tasks/ 下的 Markdown。",
            path=path,
            status=status,
            due=due_value,
        )
    if not filepath.exists():
        return _task_update_result(
            False,
            "not_found",
            f"文件不存在：{path}",
            path=path,
            status=status,
            due=due_value,
        )
    meta, body = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="replace")
    )
    if meta.get("type") != "task":
        return _task_update_result(
            False,
            "not_task",
            "目标文件不是 task。",
            path=path,
            status=status,
            due=due_value,
        )
    relative = filepath.relative_to(rt.VAULT).as_posix()
    today = rt.today().isoformat()
    meta["status"] = status
    meta["updated"] = today
    meta["source"] = source
    if due_value is not None:
        meta["due"] = due_value
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
        message = (
            f"已更新并归档：{relative} → {status}；"
            f"{destination.relative_to(rt.VAULT).as_posix()} {sync}"
        )
        return _task_update_result(
            True,
            "task_archived",
            message,
            path=relative,
            status=status,
            due=str(meta.get("due", "none")),
        )
    filepath.write_text(updated, encoding="utf-8")
    sync = rt.git_sync(
        f"auto: task {relative} -> {status} (source: {source})",
        filepath,
    )
    message = f"已更新：{relative} → {status} {sync}"
    return _task_update_result(
        True,
        "task_updated",
        message,
        path=relative,
        status=status,
        due=str(meta.get("due", "none")),
    )


@rt.serialized_mutation
def update_task(
    path: str,
    status: str,
    note: str = "",
    source: str = "unknown",
    due: str | None = None,
) -> str:
    """兼容直接 Python 调用；MCP 工具使用结构化的 update_task_result。"""
    return str(update_task_result(path, status, note, source, due)["message"])
