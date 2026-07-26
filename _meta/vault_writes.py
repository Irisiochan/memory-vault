"""Inbox, memory, archive, and diary mutation tools."""

from __future__ import annotations

from pathlib import Path

from _meta import vault_runtime as rt


@rt.serialized_mutation
def write_inbox(
    slug: str,
    title: str,
    content: str,
    tags: list[str],
    source: str = "unknown",
) -> str:
    """写一条低置信度内容到 inbox。"""
    today = rt.today().isoformat()
    filepath = rt.safe_generated_md("inbox", slug, prefix=f"{today}_")
    if filepath is None:
        return rt.invalid_slug()
    if filepath.exists():
        return f"文件已存在：inbox/{filepath.name}，请换一个 slug。"
    meta = {
        "type": "inbox",
        "created": today,
        "source": source,
        "tags": tags or ["untagged"],
    }
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        rt.rebuild_file(meta, f"# {title}\n\n{content}"), encoding="utf-8"
    )
    sync = rt.git_sync(f"auto: write inbox {slug} (source: {source})", filepath)
    return f"已写入：inbox/{filepath.name} {sync}"


def list_inbox() -> str:
    """列出 inbox 中所有待确认内容。"""
    rt.pull_if_stale()
    files = rt.scan_files(["inbox"])
    if not files:
        return "inbox/ 为空，没有待确认的记忆。"
    lines = [f"共 {len(files)} 条待确认：", ""]
    for item in files:
        tags = ", ".join(str(tag) for tag in item["tags"]) if item["tags"] else ""
        suffix = f"  [{tags}]" if tags else ""
        lines.append(f"- **{item['title']}** (`{item['path']}`){suffix}")
    return "\n".join(lines)


def inbox_source(filename: str) -> Path | None:
    """Resolve a direct inbox Markdown filename without traversal."""
    if not filename or "/" in filename or "\\" in filename or not filename.endswith(".md"):
        return None
    filepath = rt.safe_md(f"inbox/{filename}")
    if filepath is None or filepath.parent.resolve() != (rt.VAULT / "inbox").resolve():
        return None
    return filepath


def _is_hub_auto_note(filepath: Path) -> bool:
    if not filepath.exists() or filepath.suffix != ".md":
        return False
    meta, _ = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="replace")
    )
    tags = meta.get("tags") or []
    return meta.get("source") == "hub-auto" or "hub-auto" in tags


def is_hub_auto_inbox(filepath: Path) -> bool:
    try:
        relative = filepath.resolve().relative_to((rt.VAULT / "inbox").resolve())
    except ValueError:
        return False
    return len(relative.parts) == 1 and _is_hub_auto_note(filepath)


def archived_hub_auto_source(filename: str) -> Path | None:
    retired = rt.VAULT / "_archive" / "retired"
    if not retired.exists():
        return None
    for candidate in sorted(retired.glob(f"*_{filename}")):
        if _is_hub_auto_note(candidate):
            return candidate
    return None


def archive_processed_hub_auto_inbox(
    source_path: Path,
    reason: str,
    source: str,
) -> Path:
    """Archive a processed hub-auto capture; failures propagate."""
    if not is_hub_auto_inbox(source_path):
        raise ValueError(f"不是 hub-auto inbox：{source_path.name}")
    meta, body = rt.parse_frontmatter(
        source_path.read_text(encoding="utf-8", errors="replace")
    )
    meta["archived"] = rt.today().isoformat()
    meta["archive_reason"] = reason
    meta["archive_source"] = source
    retired = rt.VAULT / "_archive" / "retired"
    retired.mkdir(parents=True, exist_ok=True)
    base = f"{rt.today().isoformat()}_{source_path.name}"
    destination = retired / base
    suffix = 2
    while destination.exists():
        destination = retired / f"{Path(base).stem}-{suffix}.md"
        suffix += 1
    destination.write_text(rt.rebuild_file(meta, body), encoding="utf-8")
    source_path.unlink()
    return destination


@rt.serialized_mutation
def promote_to_memory(filename: str) -> str:
    """将 inbox 文件升级为正式 memory；hub-auto 来源改为留痕归档。"""
    source_path = inbox_source(filename)
    if source_path is None:
        return "文件名不合法：只能使用 inbox 中的 .md 文件名。"
    destination = rt.VAULT / "memories" / filename
    if not source_path.exists():
        if destination.exists():
            return f"已处理：memories/{filename} 已存在，inbox 源不存在。"
        return f"文件不存在：inbox/{filename}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if is_hub_auto_inbox(source_path):
            archived = archive_processed_hub_auto_inbox(
                source_path,
                f"hub-auto inbox 已由 promote_to_memory 处理：memories/{filename} 已存在",
                "promote_to_memory",
            )
            sync = rt.git_sync(
                f"auto: archive processed hub-auto inbox {filename}",
                source_path,
                archived,
            )
            return (
                f"memories/ 中已有同名文件：{filename}；已归档源："
                f"{archived.relative_to(rt.VAULT).as_posix()} {sync}"
            )
        return f"memories/ 中已有同名文件：{filename}"
    hub_auto = is_hub_auto_inbox(source_path)
    text = source_path.read_text(encoding="utf-8").replace(
        "type: inbox", "type: memory", 1
    )
    destination.write_text(text, encoding="utf-8")
    changed = [source_path, destination]
    archive_note = ""
    if hub_auto:
        archived = archive_processed_hub_auto_inbox(
            source_path,
            f"hub-auto inbox 已升级为 memories/{filename}",
            "promote_to_memory",
        )
        changed.append(archived)
        archive_note = f"；已归档源：{archived.relative_to(rt.VAULT).as_posix()}"
    else:
        source_path.unlink()
    rebuild_note = ""
    try:
        rt.rebuild_context_prompt()
    except Exception as exc:
        rebuild_note = f"（context_prompt.md 重建失败：{exc}）"
    sync = rt.git_sync(f"auto: promote memory {filename}", *changed)
    return (
        f"已升级：inbox/{filename} → memories/{filename}"
        f"{archive_note} {sync}{rebuild_note}"
    )


@rt.serialized_mutation
def write_memory(
    slug: str,
    title: str,
    content: str,
    tags: list[str],
    source: str = "unknown",
) -> str:
    """直接写入一条确认的长期记忆。"""
    filepath = rt.safe_generated_md("memories", slug)
    if filepath is None:
        return rt.invalid_slug()
    if filepath.exists():
        return f"memories/{slug}.md 已存在。补充内容请用 update_memory，换主题请换 slug。"
    meta = {
        "type": "memory",
        "created": rt.today().isoformat(),
        "source": source,
        "tags": tags or ["untagged"],
    }
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        rt.rebuild_file(meta, f"# {title}\n\n{content}"), encoding="utf-8"
    )
    try:
        rt.rebuild_context_prompt()
    except Exception:
        pass
    sync = rt.git_sync(f"auto: new memory {slug} (source: {source})", filepath)
    return f"已写入：memories/{slug}.md {sync}"


@rt.serialized_mutation
def update_memory(
    path: str,
    content: str,
    mode: str = "append",
    source: str = "unknown",
) -> str:
    """修改一条已有记忆；核心身份文件只允许 append。"""
    filepath = rt.safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    relative = filepath.resolve().relative_to(rt.VAULT.resolve()).as_posix()
    if relative.split("/")[0] not in rt.ACTIVE_DIRS:
        return f"只能修改 {'/'.join(rt.ACTIVE_DIRS)} 下的文件。"
    if mode not in ("append", "replace"):
        return "mode 只能是 append 或 replace。"
    if rt.is_fact_domain_path(relative):
        return f"{relative} 由 fact 层维护；事实变更请用 write_fact，note 可在 Obsidian 中直接编辑。"
    if mode == "replace" and rt.is_core(relative):
        return f"{relative} 是核心身份文件，只允许 append 追加，不允许 replace 重写。"
    meta, body = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="replace")
    )
    today = rt.today().isoformat()
    meta["updated"] = today
    new_body = (
        f"{body}\n\n## 更新 {today}\n\n{content.strip()}"
        if mode == "append"
        else content.strip()
    )
    filepath.write_text(rt.rebuild_file(meta, new_body), encoding="utf-8")
    if relative.startswith("memories/"):
        try:
            rt.rebuild_context_prompt()
        except Exception:
            pass
    sync = rt.git_sync(
        f"auto: update memory {relative} [{mode}] (source: {source})",
        filepath,
    )
    return f"已{'追加' if mode == 'append' else '重写'}：{relative} {sync}"


@rt.serialized_mutation
def archive_memory(path: str, reason: str, source: str = "unknown") -> str:
    """软删除活跃内容到 _archive/retired。"""
    filepath = rt.safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    relative = filepath.resolve().relative_to(rt.VAULT.resolve()).as_posix()
    if relative.split("/")[0] not in rt.ACTIVE_DIRS:
        return f"只能归档 {'/'.join(rt.ACTIVE_DIRS)} 下的文件。"
    if rt.is_fact_domain_path(relative):
        return f"{relative} 由 fact 层维护，不允许整域归档；请用 write_fact 写入新版本收敛旧事实。"
    if rt.is_core(relative):
        return f"{relative} 是核心身份文件，不允许归档。"
    meta, body = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="replace")
    )
    meta["archived"] = rt.today().isoformat()
    meta["archive_reason"] = reason
    meta["archive_source"] = source
    retired = rt.VAULT / "_archive" / "retired"
    retired.mkdir(parents=True, exist_ok=True)
    destination = retired / f"{rt.today().isoformat()}_{filepath.name}"
    if destination.exists():
        return (
            f"归档目标已存在：{destination.relative_to(rt.VAULT).as_posix()}，"
            "换个时间再试或手动处理。"
        )
    destination.write_text(rt.rebuild_file(meta, body), encoding="utf-8")
    filepath.unlink()
    if relative.startswith("memories/"):
        try:
            rt.rebuild_context_prompt()
        except Exception:
            pass
    sync = rt.git_sync(
        f"auto: archive {relative} ({reason[:50]})",
        filepath,
        destination,
    )
    return (
        f"已归档：{relative} → "
        f"{destination.relative_to(rt.VAULT).as_posix()} {sync}"
    )


@rt.serialized_mutation
def log_daily(content: str, source: str = "unknown") -> str:
    """向当天流水日记追加一条带时间的记录。"""
    current = rt.now()
    filepath = rt.VAULT / "diary" / f"{current.date().isoformat()}.md"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not filepath.exists():
        meta = {
            "type": "diary",
            "created": current.date().isoformat(),
            "source": "mixed",
            "tags": ["日常"],
        }
        filepath.write_text(
            rt.rebuild_file(meta, f"# {current.date().isoformat()} 日常"),
            encoding="utf-8",
        )
    with filepath.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- **{current.strftime('%H:%M')}** [{source}] {content.strip()}")
    sync = rt.git_sync(
        f"auto: daily {current.date().isoformat()} (source: {source})",
        filepath,
    )
    return f"已记录到 diary/{filepath.name} {sync}"


@rt.serialized_mutation
def write_diary(
    slug: str,
    title: str,
    content: str,
    source: str = "unknown",
    tags: list[str] | None = None,
) -> str:
    """写一篇独立长日记。"""
    today = rt.today().isoformat()
    filepath = rt.safe_generated_md("diary", slug, prefix=f"{today}_")
    if filepath is None:
        return rt.invalid_slug()
    if filepath.exists():
        return f"diary/{filepath.name} 已存在，换个 slug 或用 update_memory 追加。"
    meta = {
        "type": "diary",
        "created": today,
        "source": source,
        "tags": tags or ["日记"],
    }
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        rt.rebuild_file(meta, f"# {title}\n\n{content}"), encoding="utf-8"
    )
    sync = rt.git_sync(f"auto: diary {slug} (source: {source})", filepath)
    return f"已写入：diary/{filepath.name} {sync}"
