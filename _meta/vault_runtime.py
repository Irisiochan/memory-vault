"""Shared runtime, filesystem, configuration, and Git primitives for Memory Vault."""

from __future__ import annotations

import datetime
import importlib.util
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


CODE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = CODE_ROOT
ACTIVE_DIRS = ["memories", "tasks", "inbox", "projects", "diary"]
FACTS_DIR = "memories/facts"
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,80}$")
MD_FILENAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}\.md$")

GIT_TIMEOUT = 30
PULL_TIMEOUT = 10
PULL_INTERVAL = 300

VAULT = DEFAULT_VAULT
TEMPLATE = CODE_ROOT / "template"
OWNER = "主人"
CORE_FILES = [
    "memories/owner-core.md",
    "memories/owner-ai-interaction-styles.md",
]
TZ = ZoneInfo("Asia/Shanghai")
GIT_SYNC_MODE = "auto"

_git_lock = threading.Lock()
_last_pull = 0.0
_WEEKDAY_CN = "一二三四五六日"


def configure(vault: str | Path | None = None, *, initialize: bool = True) -> None:
    """Reload the runtime from environment/config.

    Calling this on every ``mcp_server`` import keeps dynamic test imports and the
    console ``--vault`` entrypoint isolated even though helper modules are cached.
    """

    global VAULT, TEMPLATE, OWNER, CORE_FILES, TZ, GIT_SYNC_MODE, _last_pull
    selected = vault or os.environ.get("MEMORY_VAULT_PATH", DEFAULT_VAULT)
    VAULT = Path(selected).expanduser().resolve()
    repo_template = CODE_ROOT / "template"
    package_template = CODE_ROOT / "memory_vault_mcp" / "template"
    TEMPLATE = repo_template if repo_template.exists() else package_template
    GIT_SYNC_MODE = os.environ.get("VAULT_GIT_SYNC", "auto").strip().lower()
    _last_pull = 0.0

    if initialize:
        initialize_vault()

    cfg_path = VAULT / "_meta" / "vault_config.yaml"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            cfg = {}
    OWNER = cfg.get("owner", "主人")
    CORE_FILES = cfg.get(
        "core_files",
        [
            "memories/owner-core.md",
            "memories/owner-ai-interaction-styles.md",
        ],
    )
    TZ = ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))


def initialize_vault() -> None:
    """Initialize a new vault without injecting blank core files into an old one."""

    has_config = (VAULT / "_meta" / "vault_config.yaml").exists()
    has_user_data = any(
        directory.exists() and any(directory.iterdir())
        for directory in (VAULT / name for name in ACTIVE_DIRS)
    )
    is_new = not has_config and not has_user_data
    VAULT.mkdir(parents=True, exist_ok=True)
    if TEMPLATE.exists():
        for source in TEMPLATE.rglob("*"):
            relative = source.relative_to(TEMPLATE)
            if not is_new and relative.parts and relative.parts[0] == "memories":
                continue
            destination = VAULT / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    for dirname in [*ACTIVE_DIRS, FACTS_DIR, "_archive/retired", "_meta"]:
        (VAULT / dirname).mkdir(parents=True, exist_ok=True)


def now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def today() -> datetime.date:
    return now().date()


def time_of_day_label(hour: int) -> str:
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


def now_line() -> str:
    current = now()
    return (
        f"🕐 现在是 {current.strftime('%Y-%m-%d')} "
        f"星期{_WEEKDAY_CN[current.weekday()]} {current.strftime('%H:%M')}"
        f"（{time_of_day_label(current.hour)}，{TZ.key}）"
        "——回应要贴合当下时段和星期，问候语别跑偏。"
    )


def git_enabled() -> bool:
    """Only sync when the vault itself is a Git repository."""

    return GIT_SYNC_MODE != "off" and (VAULT / ".git").exists()


def run_git(*args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=VAULT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def pull_if_stale() -> None:
    """Throttle read-path pulls; failures leave the readable local snapshot intact."""

    global _last_pull
    if not git_enabled():
        return
    with _git_lock:
        if time.time() - _last_pull < PULL_INTERVAL:
            return
        try:
            result = run_git("pull", "--rebase", timeout=PULL_TIMEOUT)
            if result.returncode != 0:
                run_git("rebase", "--abort", timeout=PULL_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            pass
        finally:
            _last_pull = time.time()


def git_sync(message: str, *changed_paths: Path) -> str:
    """Commit only this tool call's paths, then rebase and push."""

    global _last_pull
    if not git_enabled():
        return "（已保存到本地 vault；未启用独立 Git 同步）"
    relative_paths: list[str] = []
    for changed_path in changed_paths:
        try:
            relative_paths.append(
                changed_path.resolve(strict=False).relative_to(VAULT).as_posix()
            )
        except ValueError:
            return "（已保存到本地 vault；拒绝同步 vault 外路径）"
    if not relative_paths:
        return "（已保存到本地 vault；没有可同步路径）"

    with _git_lock:
        try:
            staged_before = {
                line.strip()
                for line in run_git("diff", "--cached", "--name-only").stdout.splitlines()
                if line.strip()
            }
            unexpected = staged_before - set(relative_paths)
            if unexpected:
                return "（已本地保存；检测到无关 staged 改动，未自动提交）"

            staged = run_git("add", "--", *relative_paths)
            if staged.returncode != 0:
                return f"（已保存到本地 vault；暂存失败：{staged.stderr.strip()[:200]}）"
            if run_git("diff", "--cached", "--quiet").returncode == 0:
                return "（无变更需要同步）"

            commit = run_git("commit", "-m", message)
            if commit.returncode != 0:
                return f"（本地 commit 失败：{commit.stderr.strip()[:200]}）"

            pull = run_git("pull", "--rebase")
            if pull.returncode != 0:
                run_git("rebase", "--abort")
                return "（已本地保存并 commit，拉取/rebase 失败；请检查远端、冲突或无关工作区改动）"

            push = run_git("push")
            _last_pull = time.time()
            if push.returncode != 0:
                return "（已本地保存并 commit，推送失败——可能离线或有冲突，联网后下次写入会自动补推）"
            return "（已同步到 GitHub）"
        except subprocess.TimeoutExpired:
            return "（已本地保存，git 操作超时——网络慢或离线，下次写入自动补推）"
        except OSError as exc:
            return f"（已本地保存，git 调用失败：{exc}）"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return meta, parts[2].strip()
    return {}, text.strip()


def extract_h1(body: str) -> str:
    for line in body.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def scan_files(dirs: list[str] | None = None) -> list[dict]:
    """Scan active Markdown recursively, including ``memories/facts``."""

    results = []
    for dirname in dirs or ACTIVE_DIRS:
        dirpath = VAULT / dirname
        if not dirpath.exists():
            continue
        for markdown in sorted(dirpath.rglob("*.md")):
            text = markdown.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_frontmatter(text)
            results.append(
                {
                    "path": markdown.relative_to(VAULT).as_posix(),
                    "title": extract_h1(body) or markdown.stem,
                    "type": meta.get("type", ""),
                    "source": meta.get("source", ""),
                    "tags": meta.get("tags", []),
                    "created": str(meta.get("created", "")),
                }
            )
    return results


def safe_md(path: str) -> Path | None:
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


def safe_generated_md(directory: str, slug: str, prefix: str = "") -> Path | None:
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        return None
    return safe_child_md(directory, f"{prefix}{slug}.md")


def safe_child_md(directory: str, filename: str) -> Path | None:
    if not isinstance(filename, str) or not MD_FILENAME_RE.fullmatch(filename):
        return None
    if Path(filename).name != filename:
        return None
    vault_root = VAULT.resolve()
    parent = (VAULT / directory).resolve()
    candidate = (parent / filename).resolve()
    try:
        parent.relative_to(vault_root)
        candidate.relative_to(vault_root)
        candidate.relative_to(parent)
    except ValueError:
        return None
    if candidate.suffix != ".md":
        return None
    return candidate


def invalid_slug() -> str:
    return "slug 不合法：仅允许字母、数字、下划线和短横线，必须以字母或数字开头，最长 81 个字符。"


def is_core(path: str) -> bool:
    return Path(path).as_posix() in {
        Path(str(relative)).as_posix() for relative in CORE_FILES
    }


def is_fact_domain_path(path: str) -> bool:
    rel = Path(path).as_posix()
    return rel.startswith(f"{FACTS_DIR}/") and rel.endswith(".md")


def rebuild_file(meta: dict, body: str) -> str:
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def read_core_context(max_chars_per_file: int | None = None) -> str:
    chunks = []
    for relative in CORE_FILES:
        filepath = safe_md(str(relative))
        if filepath is None or not filepath.exists():
            continue
        text = filepath.read_text(encoding="utf-8", errors="replace").strip()
        if max_chars_per_file is not None:
            text = text[:max_chars_per_file]
        chunks.append(text)
    return "\n\n---\n\n".join(chunk for chunk in chunks if chunk)


def rebuild_context_prompt() -> None:
    spec = importlib.util.spec_from_file_location(
        "memory_vault_build_context",
        Path(__file__).resolve().parent / "build_context.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load build_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUTPUT.write_text(module.build_context(), encoding="utf-8")


configure()
