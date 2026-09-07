"""Shared runtime, filesystem, configuration, and Git primitives for Memory Vault."""

from __future__ import annotations

import datetime
import importlib.util
import os
import re
import shutil
import subprocess
import time
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from _meta.vault_lock import VaultLock


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

# One reentrant boundary for every vault operation, shared across the threads
# of this server and any other server process pointed at the same vault
# (e.g. a Docker HTTP instance plus per-CLI stdio instances).
operation_lock = VaultLock(lambda: VAULT)
_last_pull = 0.0
_WEEKDAY_CN = "一二三四五六日"


def serialized_mutation(function):
    """Serialize a complete read-modify-write-sync mutation across processes."""

    @wraps(function)
    def locked(*args, **kwargs):
        with operation_lock:
            return function(*args, **kwargs)

    return locked


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
        # The server owns stdin for MCP; Git must never inherit that live pipe.
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def git_error_text(
    result: subprocess.CompletedProcess,
    limit: int = 280,
) -> str:
    """Compress Git stderr/stdout into one bounded diagnostic line."""

    chunks = []
    for part in (result.stderr, result.stdout):
        if part and part.strip():
            chunks.append(part.strip())
    text = " | ".join(chunks) if chunks else f"exit {result.returncode}"
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def unpushed_commit_count(branch: str) -> int | None:
    """Return commits on HEAD not present in origin/<branch>, if knowable."""

    ahead = run_git("rev-list", "--count", f"origin/{branch}..HEAD")
    if ahead.returncode != 0:
        return None
    try:
        return int((ahead.stdout or "0").strip() or "0")
    except ValueError:
        return None


def pull_if_stale() -> None:
    """Throttle read-path pulls; failures leave the readable local snapshot intact."""

    global _last_pull
    if not git_enabled():
        return
    with operation_lock:
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
    """Commit this tool call's paths, rebase, and push with retryable failures."""

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

    with operation_lock:
        try:
            committed = False
            staged_result = run_git("diff", "--cached", "--name-only")
            if staged_result.returncode != 0:
                return (
                    "（已本地保存；无法检查 staged 状态："
                    f"{git_error_text(staged_result)}）"
                )
            staged_before = {
                line.strip()
                for line in staged_result.stdout.splitlines()
                if line.strip()
            }
            unexpected = staged_before - set(relative_paths)
            if unexpected:
                return "（已本地保存；检测到无关 staged 改动，未自动提交）"

            stage_paths = []
            for relative in relative_paths:
                if not (VAULT / relative).exists():
                    tracked = run_git(
                        "--literal-pathspecs", "ls-files", "--error-unmatch", "--", relative
                    )
                    if tracked.returncode == 1:
                        # A retried archive may have already committed its
                        # source deletion; still retry the pending push.
                        continue
                    if tracked.returncode != 0:
                        return (
                            "（已本地保存；检查文件跟踪状态失败："
                            f"{git_error_text(tracked)}；未自动提交）"
                        )
                stage_paths.append(relative)
            if stage_paths:
                staged = run_git("--literal-pathspecs", "add", "--", *stage_paths)
                if staged.returncode != 0:
                    return f"（已保存到本地 vault；暂存失败：{git_error_text(staged)}）"

            staged_diff = run_git("diff", "--cached", "--quiet")
            if staged_diff.returncode not in (0, 1):
                return (
                    "（已本地保存；无法检查 staged 差异："
                    f"{git_error_text(staged_diff)}）"
                )
            if staged_diff.returncode == 1:
                commit = run_git("commit", "-m", message)
                if commit.returncode != 0:
                    return f"（本地 commit 失败：{git_error_text(commit)}）"
                committed = True

            pull = run_git("pull", "--rebase")
            if pull.returncode != 0:
                run_git("rebase", "--abort")
                if committed:
                    return (
                        "（已本地 commit；pull --rebase 失败，未 push："
                        f"{git_error_text(pull)}；冲突需人工处理）"
                    )
                return (
                    "（文件已写盘；pull --rebase 失败，未完成补推："
                    f"{git_error_text(pull)}）"
                )

            branch_result = run_git("branch", "--show-current")
            branch = (branch_result.stdout or "").strip() or "main"
            ahead = unpushed_commit_count(branch)
            need_push = committed if ahead is None else ahead > 0
            if not need_push:
                _last_pull = time.time()
                return "（无变更需要同步）"

            push = run_git("push")
            _last_pull = time.time()
            if push.returncode != 0:
                return (
                    "（已本地 commit，push 失败："
                    f"{git_error_text(push)}；未远端同步。"
                    "下次写入会重试 pull/push，冲突需人工处理）"
                )
            if committed:
                return "（已同步到 GitHub）"
            return "（无新变更；已补推此前未推送的提交到 GitHub）"
        except subprocess.TimeoutExpired as exc:
            command = (
                " ".join(str(item) for item in exc.cmd)
                if getattr(exc, "cmd", None)
                else "git"
            )
            timeout = getattr(exc, "timeout", GIT_TIMEOUT)
            return (
                f"（git 超时：{command}（{timeout}s）；"
                "本地文件可能已写，commit/push 状态请核对后重试）"
            )
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
    """Accept vault-relative Markdown, with no traversal, hidden paths or symlinks."""
    if not path or "\\" in path or ":" in path:
        return None
    relative = Path(path)
    if relative.is_absolute() or relative.suffix != ".md":
        return None
    if any(part.startswith(".") for part in relative.parts):
        return None
    root = VAULT.resolve()
    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            return None
    try:
        target.resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    # Preserve the caller's root spelling (e.g. Windows RUNNER~1 vs runneradmin)
    # after validating its canonical target, so relative paths stay comparable.
    return VAULT / relative


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
