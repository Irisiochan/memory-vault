"""
从 memories/ 生成跨平台可用的 context prompt。
输出到 _meta/context_prompt.md，可以直接贴进 ChatGPT/Gemini 的 custom instructions。

用法：
    python _meta/build_context.py              # 生成 context prompt
    python _meta/build_context.py --clipboard   # 生成并复制到剪贴板
"""

import os
import re
import sys
import yaml
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = CODE_ROOT
VAULT = Path(os.environ.get("MEMORY_VAULT_PATH", DEFAULT_VAULT)).expanduser().resolve()

def _owner() -> str:
    cfg_path = VAULT / "_meta" / "vault_config.yaml"
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return cfg.get("owner", "主人")
        except yaml.YAMLError:
            pass
    return "主人"
MEMORIES_DIR = VAULT / "memories"
OUTPUT = VAULT / "_meta" / "context_prompt.md"

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from markdown."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            body = parts[2].strip()
            return meta, body
    return {}, text.strip()

def extract_h1(body: str) -> str:
    """Extract first H1 heading from body."""
    for line in body.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""

def condense_body(body: str, max_lines: int = 30) -> str:
    """Keep only the most important content, skip verbose sections."""
    lines = body.split("\n")
    result = []
    skip_sections = {"确认来源", "更新记录", "适用场景", "边界与注意事项"}
    skipping = False

    for line in lines:
        # Check if entering a section to skip
        if line.startswith("## "):
            section_name = line[3:].strip()
            skipping = section_name in skip_sections
            if skipping:
                continue

        if skipping:
            continue

        # Skip the H1 (already used as title)
        if line.startswith("# ") and not line.startswith("## "):
            continue

        result.append(line)

    # Trim trailing empty lines
    while result and not result[-1].strip():
        result.pop()

    # If still too long, truncate
    if len(result) > max_lines:
        result = result[:max_lines]
        result.append("...")

    return "\n".join(result)

def build_context() -> str:
    """Build the context prompt from all memory files."""
    memories = []

    for md_file in sorted(MEMORIES_DIR.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        title = extract_h1(body)
        tags = meta.get("tags", [])
        source = meta.get("source", "unknown")
        condensed = condense_body(body)

        memories.append({
            "title": title,
            "tags": tags,
            "source": source,
            "content": condensed,
            "filename": md_file.stem,
        })

    # Build output
    parts = []
    parts.append(f"# {_owner()} Memory Context")
    parts.append("")
    parts.append(f"以下是关于 {_owner()} 的长期记忆，由多个 AI 共同维护。")
    parts.append("⚠ source 只是记忆的写入来源，不是当前 AI 的身份。你的身份由你自己的指令和身份配置决定。")
    parts.append(f"共 {len(memories)} 条记忆，最后生成时间：{__import__('datetime').date.today().isoformat()}")
    parts.append("")

    for mem in memories:
        tag_str = ", ".join(mem["tags"]) if mem["tags"] else ""
        parts.append(f"## {mem['title']}")
        if tag_str:
            parts.append(f"[tags: {tag_str}] [source: {mem['source']}]")
        parts.append("")
        parts.append(mem["content"])
        parts.append("")
        parts.append("---")
        parts.append("")

    # 未完成任务（时间敏感事项，供无 MCP 前端参考）
    tasks_dir = VAULT / "tasks"
    if tasks_dir.exists():
        open_tasks = []
        for md_file in sorted(tasks_dir.glob("*.md")):
            meta, body = parse_frontmatter(md_file.read_text(encoding="utf-8"))
            if meta.get("status", "open") != "open":
                continue
            title = extract_h1(body) or md_file.stem
            due = meta.get("due", "none")
            open_tasks.append(f"- {title}（due: {due}）")
        if open_tasks:
            parts.append("## ⏰ 未完成的事项（对照当前日期判断是否过期，过期主动问进度）")
            parts.append("")
            parts.extend(open_tasks)
            parts.append("")

    return "\n".join(parts)

def main():
    context = build_context()

    # Write to file
    OUTPUT.write_text(context, encoding="utf-8")
    print(f"Context prompt written to: {OUTPUT}")
    print(f"Length: {len(context)} chars, {len(context.split(chr(10)))} lines")

    # Copy to clipboard if requested
    if "--clipboard" in sys.argv:
        try:
            import subprocess
            subprocess.run(["clip"], input=context.encode("utf-16-le"), check=True)
            print("Copied to clipboard!")
        except Exception as e:
            print(f"Clipboard copy failed: {e}")
            print("You can manually copy from the output file.")

if __name__ == "__main__":
    main()
