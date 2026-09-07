"""Read-only listing, search, and relationship tools."""

from __future__ import annotations

import re
from pathlib import Path

from _meta import vault_runtime as rt

SEARCH_DIR_WEIGHTS = {
    "memories": 30,
    "tasks": 22,
    "inbox": 14,
    "projects": 8,
    "diary": 0,
}


def _search_line_score(line: str, keywords: list[str], index: int) -> tuple[int, int]:
    lowered = line.lower()
    hits = sum(1 for keyword in keywords if keyword in lowered)
    if not hits:
        return (-1, -index)
    heading_bonus = 24 if line.lstrip().startswith("#") else 0
    all_terms_bonus = 35 if hits == len(keywords) else 0
    occurrence_bonus = sum(min(lowered.count(keyword), 3) for keyword in keywords) * 4
    return (
        hits * 20 + heading_bonus + all_terms_bonus + occurrence_bonus,
        -index,
    )


def _best_search_snippet(body: str, title: str, keywords: list[str]) -> str:
    body_candidates = []
    title_candidates = []
    for index, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        score = _search_line_score(stripped, keywords, index)
        if score[0] < 0:
            continue
        candidate = (score, stripped)
        if stripped.lstrip("# ").strip() == title.strip():
            title_candidates.append(candidate)
        else:
            body_candidates.append(candidate)
    candidates = body_candidates or title_candidates
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1][:120]


def _search_relevance_score(
    relative_path: str,
    title: str,
    body: str,
    keywords: list[str],
) -> int:
    lowered_title = title.lower()
    lowered_body = body.lower()
    score = SEARCH_DIR_WEIGHTS.get(relative_path.split("/", 1)[0], 0)
    heading_text = "\n".join(
        line.lstrip("# ").strip().lower()
        for line in body.splitlines()
        if line.startswith("##")
    )
    for keyword in keywords:
        if keyword in lowered_title:
            score += 90 + min(lowered_title.count(keyword), 3) * 8
        if keyword in heading_text:
            score += 45 + min(heading_text.count(keyword), 3) * 5
        body_count = lowered_body.count(keyword)
        score += min(body_count, 8) * 4
        first = lowered_body.find(keyword)
        if first >= 0:
            score += max(18 - first // 240, 0)

    phrase = " ".join(keywords)
    if len(keywords) > 1 and phrase in lowered_body:
        score += 28
    score -= min(len(relative_path) // 12, 12)
    return score


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
            relative_path = markdown.relative_to(rt.VAULT).as_posix()
            safe = rt.safe_md(relative_path)
            if safe is None or not safe.is_file():
                continue
            full_text = safe.read_text(encoding="utf-8", errors="replace")
            lowered = full_text.lower()
            if not all(keyword in lowered for keyword in keywords):
                continue
            _, body = rt.parse_frontmatter(full_text)
            title = rt.extract_h1(body) or markdown.stem
            matches.append(
                {
                    "path": relative_path,
                    "title": title,
                    "snippet": _best_search_snippet(body, title, keywords),
                    "score": _search_relevance_score(
                        relative_path,
                        title,
                        body,
                        keywords,
                    ),
                }
            )
    if not matches:
        return f"没有找到包含 '{query}' 的内容。"
    matches.sort(key=lambda match: (-match["score"], match["path"].lower()))
    lines = [f"找到 {len(matches)} 个匹配：", ""]
    for match in matches:
        snippet = f"\n  > {match['snippet']}" if match["snippet"] else ""
        lines.append(f"- **{match['title']}** (`{match['path']}`){snippet}")
    return "\n".join(lines)


def read_file(path: str) -> str:
    """读取 vault 内的 Markdown 文件。"""
    # Validate AFTER the pull: a pull may materialize a symlink at this path,
    # and pre-pull validation would let the first read follow it outside.
    rt.pull_if_stale()
    filepath = rt.safe_md(path)
    if filepath is None:
        return "路径不合法。"
    if not filepath.is_file():
        return f"文件不存在：{path}"
    return filepath.read_text(encoding="utf-8", errors="replace")


def get_related(path: str) -> str:
    """沿当前文档的 [[链接]]、标签和反向链接查找相关记忆。"""
    rt.pull_if_stale()
    filepath = rt.safe_md(path)
    if filepath is None or not filepath.is_file():
        return f"路径不合法或文件不存在：{path}"
    relative = filepath.resolve().relative_to(rt.VAULT.resolve()).as_posix()
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
