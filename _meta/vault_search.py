"""Read-only listing, search, and relationship tools."""

from __future__ import annotations

import re
from pathlib import Path

from _meta import vault_runtime as rt


def list_memories() -> str:
    """列出所有活跃记忆、任务、inbox、项目和日记。"""
    rt.pull_if_stale()
    files = rt.scan_files()
    if not files:
        return "记忆库为空。"
    lines = [f"共 {len(files)} 个文件：", ""]
    for item in files:
        tags = ", ".join(str(tag) for tag in item["tags"]) if item["tags"] else ""
        suffix = f"  [{tags}]" if tags else ""
        lines.append(f"- **{item['title']}** (`{item['path']}`){suffix}")
    return "\n".join(lines)


def search_vault(query: str) -> str:
    """按空格拆分关键词，搜索活跃目录中的 Markdown 正文。"""
    keywords = [part.lower() for part in query.split() if part.strip()]
    if not keywords:
        return "请提供搜索关键词。"
    rt.pull_if_stale()
    matches = []
    for dirname in rt.ACTIVE_DIRS:
        dirpath = rt.VAULT / dirname
        if not dirpath.exists():
            continue
        for markdown in sorted(dirpath.rglob("*.md")):
            full_text = markdown.read_text(encoding="utf-8", errors="replace")
            lowered = full_text.lower()
            if not all(keyword in lowered for keyword in keywords):
                continue
            _, body = rt.parse_frontmatter(full_text)
            snippet = next(
                (
                    line.strip()[:120]
                    for line in body.splitlines()
                    if any(keyword in line.lower() for keyword in keywords)
                ),
                "",
            )
            matches.append(
                {
                    "path": markdown.relative_to(rt.VAULT).as_posix(),
                    "title": rt.extract_h1(body) or markdown.stem,
                    "snippet": snippet,
                }
            )
    if not matches:
        return f"没有找到包含 '{query}' 的内容。"
    lines = [f"找到 {len(matches)} 个匹配：", ""]
    for match in matches:
        snippet = f"\n  > {match['snippet']}" if match["snippet"] else ""
        lines.append(f"- **{match['title']}** (`{match['path']}`){snippet}")
    return "\n".join(lines)


def read_file(path: str) -> str:
    """读取 vault 内的 Markdown 文件。"""
    filepath = rt.safe_md(path)
    if filepath is None:
        return "路径不合法。"
    if not filepath.exists():
        return f"文件不存在：{path}"
    rt.pull_if_stale()
    return filepath.read_text(encoding="utf-8", errors="replace")


def get_related(path: str) -> str:
    """沿当前文档的 [[链接]]、标签和反向链接查找相关记忆。"""
    filepath = rt.safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    relative = filepath.resolve().relative_to(rt.VAULT.resolve()).as_posix()
    rt.pull_if_stale()
    meta, body = rt.parse_frontmatter(
        filepath.read_text(encoding="utf-8", errors="replace")
    )
    stem = filepath.stem
    tags = set(meta.get("tags") or [])
    forward = set(re.findall(r"\[\[([^\]|#]+?)\]\]", body))
    all_files = rt.scan_files()
    by_stem = {Path(item["path"]).stem: item for item in all_files}
    backward, tag_kin = [], []
    for item in all_files:
        if item["path"] == relative:
            continue
        text = (rt.VAULT / item["path"]).read_text(
            encoding="utf-8", errors="replace"
        )
        if f"[[{stem}]]" in text:
            backward.append(item)
        elif tags and tags & set(item["tags"] or []):
            tag_kin.append(item)
    lines = [f"与 `{relative}` 相关的记忆：", ""]
    if forward:
        lines.append("**它链接了：**")
        for linked_stem in sorted(forward):
            hit = by_stem.get(linked_stem)
            lines.append(
                f"- [[{linked_stem}]] → `{hit['path']}`（{hit['title']}）"
                if hit
                else f"- [[{linked_stem}]]（未找到对应文件）"
            )
    if backward:
        lines.append("\n**谁链接了它：**")
        for item in backward:
            lines.append(f"- **{item['title']}** (`{item['path']}`)")
    if tag_kin:
        lines.append(f"\n**共享标签（{', '.join(sorted(tags))}）：**")
        for item in tag_kin[:10]:
            lines.append(f"- **{item['title']}** (`{item['path']}`)")
    if len(lines) == 2:
        lines.append("（没有找到相关记忆——写入时记得加 [[链接]] 和标签）")
    return "\n".join(lines)
