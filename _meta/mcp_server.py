"""
Memory Vault MCP Server
======================
个人 AI 共享记忆库 MCP 接口。
接入 Claude Desktop / ChatGPT Desktop / RikkaHub 等前端后，AI 可以直接搜索、读取、写入记忆。
个性化配置在 _meta/vault_config.yaml（主人名、时区、核心记忆文件）。

启动方式：
    python _meta/mcp_server.py                  # stdio 模式（桌面客户端自动启动）
    python _meta/mcp_server.py --http           # HTTP 模式（绑定 Tailscale IP，供手机端连接）
    python _meta/mcp_server.py --http --port 8900 --host 100.x.x.x

HTTP 模式安全边界靠 Tailscale 组网；可选设置环境变量 VAULT_TOKEN 加一层共享密钥
（客户端需带 Authorization: Bearer <token> 或 X-Vault-Token: <token> 请求头）。

客户端配置见 _meta/client_config_example.json
"""

import datetime
import ipaddress
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from mcp.server.fastmcp import FastMCP

# ── 配置 ──────────────────────────────────────────────

VAULT = Path(__file__).resolve().parent.parent
ACTIVE_DIRS = ["memories", "tasks", "inbox", "projects", "diary"]

# ── 个性化配置：_meta/vault_config.yaml ──
_cfg_path = VAULT / "_meta" / "vault_config.yaml"
_cfg = {}
if _cfg_path.exists():
    try:
        _cfg = yaml.safe_load(_cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        _cfg = {}

OWNER = _cfg.get("owner", "主人")
CORE_FILES = _cfg.get("core_files", [
    "memories/owner-core.md",
    "memories/owner-ai-interaction-styles.md",
])
# 服务器可能是 UTC（VPS 常见），所有日期/时间显式按主人所在时区算
TZ = ZoneInfo(_cfg.get("timezone", "Asia/Shanghai"))


def _now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def _today() -> datetime.date:
    return _now().date()


_WEEKDAY_CN = "一二三四五六日"


def _time_of_day_label(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 8:
        return "早上"
    if hour < 11:
        return "上午"
    if hour < 13:
        return "中午"
    if hour < 17:
        return "下午"
    if hour < 19:
        return "傍晚"
    if hour < 23:
        return "晚上"
    return "深夜"


def _now_line() -> str:
    now = _now()
    return (
        f"🕐 现在是 {now.strftime('%Y-%m-%d')} 星期{_WEEKDAY_CN[now.weekday()]} "
        f"{now.strftime('%H:%M')}（{_time_of_day_label(now.hour)}）——回应要贴合当下时段和星期，问候语别跑偏。"
    )

GIT_TIMEOUT = 30        # 秒，写路径 git 操作超时
PULL_TIMEOUT = 10       # 秒，读路径 pull 超时
PULL_INTERVAL = 300     # 秒，读路径 pull 节流间隔

mcp = FastMCP(
    "memory-vault",
    instructions=(
        "Iris 的 AI 共享记忆库（自主模式：不需要 Iris 审批，出错她会自己改）。"
        "每次新会话第一轮回复前，先调用 get_context 获取核心记忆、时间敏感事项和最近日常。"
        "话题相关时用 search_vault 补充搜索，get_related 沿 [[链接]] 和标签联想相关记忆。"
        "写入分流："
        "确认的事实/偏好/关系变化 → write_memory；"
        "自己拿不准的推测 → write_inbox，之后验证了自己 promote_to_memory；"
        "日常发生的事、生活流水 → log_daily；写整篇日记/阶段总结 → write_diary；"
        "有截止/预期时间的事 → add_task，完成或作废时 update_task；"
        "修正已有记忆 → update_memory；整条作废 → archive_memory（软删除，git 可回滚）。"
        "写入正文时主动用 [[slug]] 链接相关记忆。"
        "核心身份文件（iris-core、iris-ai-interaction-styles）只能追加不能重写。"
        "所有写入自动 git 同步到全部设备。"
    ),
)

# ── git 同步 ──────────────────────────────────────────

_git_lock = threading.Lock()
_last_pull = 0.0


def _run_git(*args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=VAULT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _pull_if_stale() -> None:
    """读路径节流 pull：距上次 pull 超过 PULL_INTERVAL 才拉，失败静默用本地版本。"""
    global _last_pull
    with _git_lock:
        if time.time() - _last_pull < PULL_INTERVAL:
            return
        try:
            result = _run_git("pull", "--rebase", timeout=PULL_TIMEOUT)
            if result.returncode != 0:
                _run_git("rebase", "--abort", timeout=PULL_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            pass
        finally:
            _last_pull = time.time()


def _git_sync(message: str) -> str:
    """写路径同步：pull --rebase → add → commit → push。返回状态说明（拼进工具返回值）。"""
    global _last_pull
    with _git_lock:
        try:
            pull = _run_git("pull", "--rebase")
            if pull.returncode != 0:
                _run_git("rebase", "--abort")

            _run_git("add", "-A")
            if _run_git("diff", "--cached", "--quiet").returncode == 0:
                return "（无变更需要同步）"

            commit = _run_git("commit", "-m", message)
            if commit.returncode != 0:
                return f"（本地 commit 失败：{commit.stderr.strip()[:200]}）"

            push = _run_git("push")
            _last_pull = time.time()
            if push.returncode != 0:
                return "（已本地保存并 commit，推送失败——可能离线或有冲突，联网后下次写入会自动补推）"
            return "（已同步到 GitHub）"
        except subprocess.TimeoutExpired:
            return "（已本地保存，git 操作超时——网络慢或离线，下次写入自动补推）"
        except OSError as e:
            return f"（已本地保存，git 调用失败：{e}）"


# ── 工具函数 ──────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return meta, parts[2].strip()
    return {}, text.strip()


def _extract_h1(body: str) -> str:
    for line in body.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _scan_files(dirs: list[str] | None = None) -> list[dict]:
    """Scan vault directories and return file metadata."""
    results = []
    for dirname in (dirs or ACTIVE_DIRS):
        dirpath = VAULT / dirname
        if not dirpath.exists():
            continue
        for md in sorted(dirpath.glob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace")
            meta, body = _parse_frontmatter(text)
            title = _extract_h1(body) or md.stem
            results.append({
                "path": f"{dirname}/{md.name}",
                "title": title,
                "type": meta.get("type", ""),
                "source": meta.get("source", ""),
                "tags": meta.get("tags", []),
                "created": str(meta.get("created", "")),
            })
    return results


def _safe_md(path: str) -> Path | None:
    """校验 vault 内的 .md 相对路径（防穿越），非法返回 None。"""
    clean = Path(path).as_posix()
    if ".." in clean or clean.startswith("/"):
        return None
    filepath = VAULT / clean
    if filepath.suffix != ".md":
        return None
    try:
        filepath.resolve().relative_to(VAULT.resolve())
    except ValueError:
        return None
    return filepath


def _is_core(path: str) -> bool:
    return Path(path).as_posix() in CORE_FILES


def _rebuild_file(meta: dict, body: str) -> str:
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def _rebuild_context_prompt() -> None:
    """memories/ 变更后重建 _meta/context_prompt.md（供无 MCP 前端手动粘贴）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_context", Path(__file__).resolve().parent / "build_context.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUTPUT.write_text(module.build_context(), encoding="utf-8")


def _time_sensitive_lines() -> list[str]:
    """扫 tasks/ 按今天（Asia/Shanghai）计算时间敏感事项。无内容返回空列表。"""
    dirpath = VAULT / "tasks"
    if not dirpath.exists():
        return []

    today = _today()
    overdue, due_today, upcoming, no_due = [], [], [], []

    for md in sorted(dirpath.glob("*.md")):
        meta, body = _parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        if meta.get("status", "open") != "open":
            continue
        title = _extract_h1(body) or md.stem
        entry = f"**{title}** (`tasks/{md.name}`)"

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
            overdue.append(f"- ⚠ {entry} 已过期 {-delta} 天——主动问问 {OWNER} 完成了没")
        elif delta == 0:
            due_today.append(f"- 🔔 {entry} 今天到期")
        elif delta <= 7:
            upcoming.append(
                f"- {entry} 还有 {delta} 天（{due.isoformat()} 星期{_WEEKDAY_CN[due.weekday()]}）"
            )

    items = overdue + due_today + upcoming + no_due
    if not items:
        return []
    return ["## ⏰ 时间敏感事项", ""] + items + [""]


def _recent_diary_lines(days: int = 3, max_lines: int = 30) -> list[str]:
    """取最近 days 天的日常流水尾部，硬截断防膨胀。"""
    today = _today()
    collected = []
    for offset in range(days - 1, -1, -1):
        d = today - datetime.timedelta(days=offset)
        fp = VAULT / "diary" / f"{d.isoformat()}.md"
        if not fp.exists():
            continue
        _, body = _parse_frontmatter(fp.read_text(encoding="utf-8", errors="replace"))
        entries = [ln for ln in body.split("\n") if ln.startswith("- ")]
        if entries:
            label = {0: "今天", 1: "昨天", 2: "前天"}.get(offset)
            collected.append(f"### {d.isoformat()}" + (f"（{label}）" if label else ""))
            collected.extend(entries)

    if not collected:
        return []
    if len(collected) > max_lines:
        collected = ["…（更早的看 diary/ 原文）"] + collected[-max_lines:]
    return ["## 📅 最近日常", ""] + collected + [""]


# ── MCP Tools ─────────────────────────────────────────


@mcp.tool()
def get_context() -> str:
    """获取主人的核心记忆上下文。每次新会话第一轮回复前先调用这个工具，
    返回：核心记忆全文 + 时间敏感事项（过期/到期任务，主动跟进）+ 最近日常 + 其余记忆清单。
    之后可以按需用 search_vault / read_file / get_related 深挖。"""
    _pull_if_stale()

    parts = [
        f"# {OWNER} 核心记忆上下文",
        "",
        _now_line(),
        "",
        "⚠ source 只是记忆的写入来源，不是当前 AI 的身份。你的身份由你自己的指令和身份配置决定。",
        "",
    ]

    core_set = set(CORE_FILES)
    for rel in CORE_FILES:
        fp = VAULT / rel
        if fp.exists():
            parts.append(fp.read_text(encoding="utf-8", errors="replace").strip())
            parts.append("")
            parts.append("---")
            parts.append("")

    parts.extend(_time_sensitive_lines())
    parts.extend(_recent_diary_lines())

    others = [f for f in _scan_files(["memories"]) if f["path"] not in core_set]
    if others:
        parts.append("## 其余记忆清单（用 read_file 按需读取）")
        parts.append("")
        for f in others:
            tags = ", ".join(f["tags"]) if f["tags"] else ""
            tag_part = f"  [{tags}]" if tags else ""
            parts.append(f"- **{f['title']}** (`{f['path']}`){tag_part}")

    return "\n".join(parts)


@mcp.tool()
def list_memories() -> str:
    """列出所有已确认的长期记忆。返回文件名、标题和标签。"""
    _pull_if_stale()
    files = _scan_files(["memories"])
    if not files:
        return "memories/ 目录为空。"

    lines = [f"共 {len(files)} 条记忆：\n"]
    for f in files:
        tags = ", ".join(f["tags"]) if f["tags"] else ""
        tag_part = f"  [{tags}]" if tags else ""
        lines.append(f"- **{f['title']}** (`{f['path']}`){tag_part}")
    return "\n".join(lines)


@mcp.tool()
def search_vault(query: str) -> str:
    """在记忆库中搜索关键词。搜索范围：memories/ → tasks/ → inbox/ → projects/ → diary/。

    Args:
        query: 搜索关键词，支持多个词空格分隔（AND 逻辑）
    """
    keywords = query.lower().split()
    if not keywords:
        return "请提供搜索关键词。"

    _pull_if_stale()

    matches = []
    for dirname in ACTIVE_DIRS:
        dirpath = VAULT / dirname
        if not dirpath.exists():
            continue
        for md in sorted(dirpath.glob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace").lower()
            if all(kw in text for kw in keywords):
                # Extract matching context
                full_text = md.read_text(encoding="utf-8", errors="replace")
                meta, body = _parse_frontmatter(full_text)
                title = _extract_h1(body) or md.stem

                # Find first matching line for snippet
                snippet = ""
                for line in body.split("\n"):
                    if any(kw in line.lower() for kw in keywords):
                        snippet = line.strip()[:120]
                        break

                matches.append({
                    "path": f"{dirname}/{md.name}",
                    "title": title,
                    "snippet": snippet,
                })

    if not matches:
        return f"没有找到包含 '{query}' 的内容。"

    lines = [f"找到 {len(matches)} 个匹配：\n"]
    for m in matches:
        snippet_part = f"\n  > {m['snippet']}" if m["snippet"] else ""
        lines.append(f"- **{m['title']}** (`{m['path']}`){snippet_part}")
    return "\n".join(lines)


@mcp.tool()
def read_file(path: str) -> str:
    """读取记忆库中的文件。

    Args:
        path: 相对于 vault 根目录的路径，例如 "memories/owner-core.md"
    """
    # Security: prevent path traversal
    clean = Path(path).as_posix()
    if ".." in clean or clean.startswith("/"):
        return "路径不合法。"

    filepath = VAULT / clean
    if not filepath.exists():
        return f"文件不存在：{clean}"
    if not filepath.suffix == ".md":
        return "只能读取 .md 文件。"

    # Ensure file is within vault
    try:
        filepath.resolve().relative_to(VAULT.resolve())
    except ValueError:
        return "路径不合法。"

    _pull_if_stale()
    return filepath.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def write_inbox(slug: str, title: str, content: str, tags: list[str], source: str = "unknown") -> str:
    """写一条低置信度记忆到 inbox/（暂存区）：自己拿不准的推测、未验证的信息放这里，
    之后验证了自己调 promote_to_memory 升级。确定的事实直接用 write_memory，不要放这。

    Args:
        slug: 文件名后缀，英文短横线连接，例如 "likes-spicy-food"
        title: 记忆标题
        content: 记忆正文内容
        tags: 标签列表，例如 ["偏好", "饮食"]
        source: 写入来源（AI 名/渠道名），例如 "claude" 或 "gpt"
    """
    today = _today().isoformat()
    filename = f"{today}_{slug}.md"
    filepath = VAULT / "inbox" / filename
    filepath.parent.mkdir(exist_ok=True)  # 空目录不进 git，克隆/reset 后可能消失

    if filepath.exists():
        return f"文件已存在：inbox/{filename}，请换一个 slug。"

    tag_str = "\n".join(f"  - {t}" for t in tags) if tags else "  - untagged"

    md_content = f"""---
type: inbox
created: {today}
source: {source}
tags:
{tag_str}
---

# {title}

{content}
"""
    filepath.write_text(md_content.strip() + "\n", encoding="utf-8")
    sync_status = _git_sync(f"auto: 写入记忆 {slug} (source: {source})")
    return f"已写入：inbox/{filename} {sync_status}"


@mcp.tool()
def list_inbox() -> str:
    """列出 inbox/ 中所有待确认的记忆。"""
    _pull_if_stale()
    files = _scan_files(["inbox"])
    if not files:
        return "inbox/ 为空，没有待确认的记忆。"

    lines = [f"共 {len(files)} 条待确认：\n"]
    for f in files:
        tags = ", ".join(f["tags"]) if f["tags"] else ""
        tag_part = f"  [{tags}]" if tags else ""
        lines.append(f"- **{f['title']}** (`{f['path']}`){tag_part}")
    return "\n".join(lines)


@mcp.tool()
def promote_to_memory(filename: str) -> str:
    """把 inbox/ 中的文件升级为正式记忆，移动到 memories/。
    当初拿不准的内容后来被验证/反复出现时，自己调用即可，不需要主人审批。
    升级后自动重建 context_prompt.md 并 git 同步。

    Args:
        filename: inbox/ 中的文件名，例如 "2026-06-24_likes-spicy-food.md"
    """
    src = VAULT / "inbox" / filename
    if not src.exists():
        return f"文件不存在：inbox/{filename}"

    dst = VAULT / "memories" / filename
    dst.parent.mkdir(exist_ok=True)
    if dst.exists():
        return f"memories/ 中已有同名文件：{filename}"

    # Read and update frontmatter
    text = src.read_text(encoding="utf-8")
    text = text.replace("type: inbox", "type: memory", 1)
    dst.write_text(text, encoding="utf-8")
    src.unlink()

    rebuild_note = ""
    try:
        _rebuild_context_prompt()
    except Exception as e:
        rebuild_note = f"（context_prompt.md 重建失败：{e}）"

    sync_status = _git_sync(f"auto: 升级记忆 {filename}")
    return f"已升级：inbox/{filename} → memories/{filename} {sync_status}{rebuild_note}"


@mcp.tool()
def write_memory(slug: str, title: str, content: str, tags: list[str], source: str = "unknown") -> str:
    """直接写入一条确认的长期记忆到 memories/，不需要审批。
    适用：确认的事实、偏好、关系变化。拿不准的用 write_inbox。
    正文里主动用 [[slug]] 链接相关记忆（例如 [[owner-core]]），方便联想。

    Args:
        slug: 文件名，英文短横线连接，例如 "new-job"
        title: 记忆标题
        content: 记忆正文（鼓励包含 [[slug]] 链接）
        tags: 标签列表
        source: 写入来源（AI 名/渠道名），例如 "claude" / "gpt"
    """
    filepath = VAULT / "memories" / f"{slug}.md"
    filepath.parent.mkdir(exist_ok=True)
    if filepath.exists():
        return f"memories/{slug}.md 已存在。补充内容请用 update_memory，换主题请换 slug。"

    meta = {
        "type": "memory",
        "created": _today().isoformat(),
        "source": source,
        "tags": tags or ["untagged"],
    }
    filepath.write_text(_rebuild_file(meta, f"# {title}\n\n{content}"), encoding="utf-8")

    try:
        _rebuild_context_prompt()
    except Exception:
        pass
    sync_status = _git_sync(f"auto: 新记忆 {slug} (source: {source})")
    return f"已写入：memories/{slug}.md {sync_status}"


@mcp.tool()
def update_memory(path: str, content: str, mode: str = "append", source: str = "unknown") -> str:
    """修改一条已有记忆。不需要审批，git 历史可回滚。

    Args:
        path: 相对路径，例如 "memories/career.md"
        content: 新内容
        mode: "append"（默认，追加一节"## 更新 日期"）或 "replace"（保留 frontmatter 重写全部正文，正文需含 # 标题）。
              核心身份文件（vault_config 的 core_files）只允许 append。
        source: 修改来源
    """
    filepath = _safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    rel = filepath.resolve().relative_to(VAULT.resolve()).as_posix()
    if rel.split("/")[0] not in ACTIVE_DIRS:
        return f"只能修改 {'/'.join(ACTIVE_DIRS)} 下的文件。"
    if mode not in ("append", "replace"):
        return "mode 只能是 append 或 replace。"
    if mode == "replace" and _is_core(rel):
        return f"{rel} 是核心身份文件，只允许 append 追加，不允许 replace 重写。"

    meta, body = _parse_frontmatter(filepath.read_text(encoding="utf-8", errors="replace"))
    today = _today().isoformat()
    meta["updated"] = today

    if mode == "append":
        new_body = f"{body}\n\n## 更新 {today}\n\n{content.strip()}"
    else:
        new_body = content.strip()

    filepath.write_text(_rebuild_file(meta, new_body), encoding="utf-8")

    if rel.startswith("memories/"):
        try:
            _rebuild_context_prompt()
        except Exception:
            pass
    sync_status = _git_sync(f"auto: 修改记忆 {rel} [{mode}] (source: {source})")
    return f"已{'追加' if mode == 'append' else '重写'}：{rel} {sync_status}"


@mcp.tool()
def archive_memory(path: str, reason: str, source: str = "unknown") -> str:
    """把一条过时/错误的记忆软删除到 _archive/retired/。不提供硬删除，git 历史随时可翻案。

    Args:
        path: 相对路径，例如 "inbox/2026-06-10_old-note.md"
        reason: 归档原因（会写进 frontmatter）
        source: 操作来源
    """
    filepath = _safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    rel = filepath.resolve().relative_to(VAULT.resolve()).as_posix()
    if rel.split("/")[0] not in ACTIVE_DIRS:
        return f"只能归档 {'/'.join(ACTIVE_DIRS)} 下的文件。"
    if _is_core(rel):
        return f"{rel} 是核心身份文件，不允许归档。"

    meta, body = _parse_frontmatter(filepath.read_text(encoding="utf-8", errors="replace"))
    meta["archived"] = _today().isoformat()
    meta["archive_reason"] = reason

    dest_dir = VAULT / "_archive" / "retired"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_today().isoformat()}_{filepath.name}"
    if dest.exists():
        return f"归档目标已存在：{dest.relative_to(VAULT).as_posix()}，换个时间再试或手动处理。"

    dest.write_text(_rebuild_file(meta, body), encoding="utf-8")
    filepath.unlink()

    if rel.startswith("memories/"):
        try:
            _rebuild_context_prompt()
        except Exception:
            pass
    sync_status = _git_sync(f"auto: 归档 {rel}（{reason[:50]}）")
    return f"已归档：{rel} → _archive/retired/{dest.name} {sync_status}"


@mcp.tool()
def log_daily(content: str, source: str = "unknown") -> str:
    """记一条日常流水到今天的日记（diary/YYYY-MM-DD.md，自动追加时间戳）。
    聊天里冒出来的生活事件、心情、小事都记这里——这是"记住日常"的入口，写起来零负担。

    Args:
        content: 发生了什么（一两句话即可）
        source: 记录来源
    """
    now = _now()
    filepath = VAULT / "diary" / f"{now.date().isoformat()}.md"
    filepath.parent.mkdir(exist_ok=True)

    if not filepath.exists():
        meta = {
            "type": "diary",
            "created": now.date().isoformat(),
            "source": "mixed",
            "tags": ["日常"],
        }
        filepath.write_text(
            _rebuild_file(meta, f"# {now.date().isoformat()} 日常"), encoding="utf-8"
        )

    with filepath.open("a", encoding="utf-8") as f:
        f.write(f"\n- **{now.strftime('%H:%M')}** [{source}] {content.strip()}")

    sync_status = _git_sync(f"auto: 日常 {now.date().isoformat()} (source: {source})")
    return f"已记录到 diary/{filepath.name} {sync_status}"


@mcp.tool()
def write_diary(slug: str, title: str, content: str, source: str = "unknown", tags: list[str] | None = None) -> str:
    """写一篇完整的日记到 diary/（独立文件，可长文）。
    与 log_daily 的区别：log_daily 往当天流水追加一行短记录；这个写一篇有标题的完整日记——
    AI 自己视角的日记、阶段总结、纪念性记录都用这个。不会注入 get_context，但可被搜索。

    Args:
        slug: 文件名后缀，英文短横线连接，例如 "my-first-journal"
        title: 日记标题
        content: 日记正文（长度不限，鼓励 [[slug]] 链接相关记忆）
        source: 写入来源（AI 名）
        tags: 标签（可选，默认 ["日记"]）
    """
    today = _today().isoformat()
    filepath = VAULT / "diary" / f"{today}_{slug}.md"
    filepath.parent.mkdir(exist_ok=True)
    if filepath.exists():
        return f"diary/{filepath.name} 已存在，换个 slug 或用 update_memory 追加。"

    meta = {
        "type": "diary",
        "created": today,
        "source": source,
        "tags": tags or ["日记"],
    }
    filepath.write_text(_rebuild_file(meta, f"# {title}\n\n{content}"), encoding="utf-8")

    sync_status = _git_sync(f"auto: 日记 {slug} (source: {source})")
    return f"已写入：diary/{filepath.name} {sync_status}"


@mcp.tool()
def add_task(slug: str, title: str, due: str, content: str = "", tags: list[str] | None = None, source: str = "unknown") -> str:
    """记一件有时间节点的事到 tasks/。get_context 会按日期自动提醒：过期未完成会提示主动问主人进度。

    Args:
        slug: 文件名，英文短横线连接，例如 "renew-passport"
        title: 事项标题
        due: 截止/预期日期 YYYY-MM-DD；确实没有明确日期传空字符串 ""
        content: 详情（可选）
        tags: 标签（可选）
        source: 来源
    """
    if due:
        try:
            datetime.date.fromisoformat(due)
        except ValueError:
            return f"due 日期格式不对：{due}，需要 YYYY-MM-DD。"

    dirpath = VAULT / "tasks"
    dirpath.mkdir(exist_ok=True)
    filepath = dirpath / f"{slug}.md"
    if filepath.exists():
        return f"tasks/{slug}.md 已存在，换个 slug 或用 update_task 更新它。"

    meta = {
        "type": "task",
        "created": _today().isoformat(),
        "due": due or "none",
        "status": "open",
        "source": source,
        "tags": tags or ["任务"],
    }
    body = f"# {title}"
    if content.strip():
        body += f"\n\n{content.strip()}"
    filepath.write_text(_rebuild_file(meta, body), encoding="utf-8")

    sync_status = _git_sync(f"auto: 新任务 {slug} due:{due or 'none'}")
    return f"已创建：tasks/{slug}.md（due: {due or '无期限'}） {sync_status}"


@mcp.tool()
def update_task(path: str, status: str, note: str = "", source: str = "unknown") -> str:
    """更新任务状态。主人说做完了 → done；不做了/不需要了 → dropped；改期 → 保持 open 并在 note 里说明（会更新 due 需重建任务）。

    Args:
        path: 相对路径，例如 "tasks/renew-passport.md"
        status: "open" / "done" / "dropped"
        note: 备注（完成情况、原因等）
        source: 操作来源
    """
    if status not in ("open", "done", "dropped"):
        return "status 只能是 open / done / dropped。"

    filepath = _safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    rel = filepath.resolve().relative_to(VAULT.resolve()).as_posix()
    if not rel.startswith("tasks/"):
        return "update_task 只操作 tasks/ 下的文件。"

    meta, body = _parse_frontmatter(filepath.read_text(encoding="utf-8", errors="replace"))
    meta["status"] = status
    today = _today().isoformat()
    meta["updated"] = today
    if status == "done":
        meta["completed"] = today

    line = f"## 状态变更 {today} → {status}"
    if note.strip():
        line += f"\n\n{note.strip()}"
    filepath.write_text(_rebuild_file(meta, f"{body}\n\n{line}"), encoding="utf-8")

    sync_status = _git_sync(f"auto: 任务 {rel} → {status}")
    return f"已更新：{rel} → {status} {sync_status}"


@mcp.tool()
def get_related(path: str) -> str:
    """联想：找出与某条记忆相关的其他记忆——它链接了谁（[[链接]]）、谁链接了它（反向）、谁和它共享标签。

    Args:
        path: 相对路径，例如 "memories/owner-core.md"
    """
    filepath = _safe_md(path)
    if filepath is None or not filepath.exists():
        return f"路径不合法或文件不存在：{path}"
    rel = filepath.resolve().relative_to(VAULT.resolve()).as_posix()

    _pull_if_stale()
    meta, body = _parse_frontmatter(filepath.read_text(encoding="utf-8", errors="replace"))
    my_stem = filepath.stem
    my_tags = set(meta.get("tags") or [])

    # 正向：本文里的 [[链接]]
    forward = set(re.findall(r"\[\[([^\]|#]+?)\]\]", body))

    all_files = _scan_files()
    by_stem = {Path(f["path"]).stem: f for f in all_files}

    backward, tag_kin = [], []
    for f in all_files:
        if f["path"] == rel:
            continue
        text = (VAULT / f["path"]).read_text(encoding="utf-8", errors="replace")
        if f"[[{my_stem}]]" in text:
            backward.append(f)
        elif my_tags and my_tags & set(f["tags"] or []):
            tag_kin.append(f)

    lines = [f"与 `{rel}` 相关的记忆：\n"]
    if forward:
        lines.append("**它链接了：**")
        for stem in sorted(forward):
            hit = by_stem.get(stem)
            lines.append(f"- [[{stem}]] → `{hit['path']}`（{hit['title']}）" if hit else f"- [[{stem}]]（未找到对应文件）")
    if backward:
        lines.append("\n**谁链接了它：**")
        for f in backward:
            lines.append(f"- **{f['title']}** (`{f['path']}`)")
    if tag_kin:
        shared_note = ", ".join(sorted(my_tags))
        lines.append(f"\n**共享标签（{shared_note}）：**")
        for f in tag_kin[:10]:
            lines.append(f"- **{f['title']}** (`{f['path']}`)")
    if len(lines) == 1:
        lines.append("（没有找到相关记忆——写入时记得加 [[链接]] 和标签）")
    return "\n".join(lines)


# ── 启动 ──────────────────────────────────────────────


def _detect_tailscale_ip() -> str | None:
    """探测本机 Tailscale IP（CGNAT 段 100.64.0.0/10）。

    优先问 tailscale CLI（Linux 上 hostname 解析通常拿不到 100.x），
    失败再 fallback 到 hostname 地址扫描（Windows 可靠）。
    """
    tailnet = ipaddress.ip_network("100.64.0.0/10")

    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ip = result.stdout.strip().splitlines()[0].strip()
            if ip and ipaddress.ip_address(ip) in tailnet:
                return ip
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except socket.gaierror:
        return None
    for info in infos:
        ip = info[4][0]
        try:
            if ipaddress.ip_address(ip) in tailnet:
                return ip
        except ValueError:
            continue
    return None


def _run_http(host: str | None, port: int, path: str = "/mcp") -> None:
    host = host or _detect_tailscale_ip()
    if not host:
        sys.exit(
            "未检测到 Tailscale IP（100.x 段）。"
            "请先启动 Tailscale，或用 --host 手动指定绑定地址。"
            "（为安全起见不会默认绑定 0.0.0.0）"
        )

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path

    # FastMCP 构造时按默认 host(127.0.0.1) 锁了 localhost-only 的 Host 头白名单，
    # 这里按实际部署形态重建，否则请求会被 DNS rebinding 保护拒掉
    from mcp.server.transport_security import TransportSecuritySettings

    if ipaddress.ip_address(host).is_loopback:
        # 回环绑定 = 前面有本机反代（如 tailscale funnel），Host 头是外部域名，
        # 无法预知且流量只能来自本机，关掉 Host 校验，安全靠秘密路径 + 反代层
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
    else:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{host}:*", "localhost:*", "127.0.0.1:*"],
            allowed_origins=[f"http://{host}:*", "http://localhost:*", "http://127.0.0.1:*"],
        )

    token = os.environ.get("VAULT_TOKEN")
    if token:
        # 加一层共享密钥校验（Authorization: Bearer <token> 或 X-Vault-Token: <token>）
        import uvicorn
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import PlainTextResponse

        class TokenAuth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                auth_ok = (
                    request.headers.get("authorization") == f"Bearer {token}"
                    or request.headers.get("x-vault-token") == token
                )
                if not auth_ok:
                    return PlainTextResponse("unauthorized", status_code=401)
                return await call_next(request)

        app = mcp.streamable_http_app()
        app.add_middleware(TokenAuth)
        print(f"Memory Vault MCP (HTTP + token) : http://{host}:{port}/mcp")
        uvicorn.run(app, host=host, port=port)
    else:
        print(f"Memory Vault MCP (HTTP) : http://{host}:{port}/mcp")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Memory Vault MCP Server")
    parser.add_argument("--http", action="store_true", help="以 streamable-http 模式启动（默认 stdio）")
    parser.add_argument("--host", default=None, help="HTTP 绑定地址（默认自动探测 Tailscale IP）")
    parser.add_argument("--port", type=int, default=8900, help="HTTP 端口（默认 8900；别用 8769-8868，Windows 保留段）")
    parser.add_argument("--path", default="/mcp", help="MCP 端点路径（默认 /mcp；公网 funnel 部署时用带随机串的秘密路径）")
    args = parser.parse_args()

    if args.http:
        _run_http(args.host, args.port, args.path)
    else:
        mcp.run()
