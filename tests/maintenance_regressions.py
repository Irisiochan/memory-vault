"""Regression coverage for retrieval, backdated diary entries, and Git sync."""

from __future__ import annotations

import datetime
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _meta import vault_runtime as rt
from _meta import vault_search
from _meta import vault_tasks
from _meta import vault_writes


def completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class SearchRelevanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        rt.configure(self.vault)
        rt.GIT_SYNC_MODE = "off"
        self.original_pull = rt.pull_if_stale
        rt.pull_if_stale = lambda: None

    def tearDown(self):
        rt.pull_if_stale = self.original_pull
        self.temp.cleanup()

    def write(self, relative: str, title: str, body: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")

    @staticmethod
    def paths(result: str) -> list[str]:
        return [
            line.split("`")[1]
            for line in result.splitlines()
            if line.startswith("- **")
        ]

    def test_title_relevance_beats_dictionary_order(self):
        self.write("memories/a-first.md", "旧记录", "正文顺带提到 vault 检索。")
        self.write("tasks/z-last.md", "修 vault 检索质量", "这是当前实现任务。")

        result = vault_search.search_vault("vault 检索")

        self.assertEqual("tasks/z-last.md", self.paths(result)[0])

    def test_directory_weight_breaks_equivalent_body_matches(self):
        self.write("diary/a.md", "流水", "同样的检索词。")
        self.write("tasks/a.md", "待办", "同样的检索词。")
        self.write("memories/a.md", "记忆", "同样的检索词。")

        self.assertEqual(
            ["memories/a.md", "tasks/a.md", "diary/a.md"],
            self.paths(vault_search.search_vault("检索词")),
        )

    def test_best_snippet_prefers_context_over_title_echo(self):
        self.write(
            "tasks/search.md",
            "vault 检索",
            "背景说明。\n\n## 验收 vault 检索\n这里解释为什么命中质量更高。",
        )

        self.assertIn(
            "> ## 验收 vault 检索",
            vault_search.search_vault("vault 检索"),
        )

    def test_multi_keyword_search_remains_and_based(self):
        self.write("memories/one.md", "Alpha", "beta only here")
        self.write("memories/two.md", "Alpha", "gamma only here")

        self.assertEqual(
            ["memories/one.md"],
            self.paths(vault_search.search_vault("alpha beta")),
        )


class DirectReadPullOrderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        rt.configure(self.vault)
        self.original_pull = rt.pull_if_stale

    def tearDown(self):
        rt.pull_if_stale = self.original_pull
        self.temp.cleanup()

    def install_remote_file_on_pull(self, relative: str, content: str) -> None:
        def fake_pull() -> None:
            path = self.vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        rt.pull_if_stale = fake_pull

    def test_read_file_pulls_before_missing_check(self):
        self.install_remote_file_on_pull(
            "memories/remote-note.md",
            "# Remote note\n\nArrived from another device.\n",
        )

        result = vault_search.read_file("memories/remote-note.md")

        self.assertIn("Arrived from another device.", result)

    def test_get_related_pulls_before_missing_check(self):
        self.install_remote_file_on_pull(
            "memories/remote-related.md",
            "---\ntags: [remote]\n---\n\n# Remote related\n",
        )

        result = vault_search.get_related("memories/remote-related.md")

        self.assertIn("`memories/remote-related.md`", result)
        self.assertNotIn("文件不存在", result)


class SymlinkProtectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.outside = Path(self.temp.name) / "outside"
        self.outside.mkdir()
        self.vault = Path(self.temp.name) / "vault"
        rt.configure(self.vault)
        rt.GIT_SYNC_MODE = "off"
        self.original_pull = rt.pull_if_stale
        rt.pull_if_stale = lambda: None
        self.secret = self.outside / "secret.md"
        self.secret.write_text(
            "---\nstatus: open\ndue: 2020-01-01\n---\n\n# LEAKED-TITLE\n\nLEAKED-BODY\n",
            encoding="utf-8",
        )

    def tearDown(self):
        rt.pull_if_stale = self.original_pull
        self.temp.cleanup()

    def symlink(self, relative: str) -> None:
        link = self.vault / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(self.secret)
        except OSError:
            self.skipTest("symlinks unavailable on this platform")

    def test_read_file_rejects_symlink_installed_by_pull_on_first_read(self):
        def pull_installs_symlink() -> None:
            self.symlink("memories/leak.md")

        rt.pull_if_stale = pull_installs_symlink

        result = vault_search.read_file("memories/leak.md")

        self.assertNotIn("LEAKED-BODY", result)
        self.assertIn("路径不合法", result)

    def test_search_scan_and_task_snapshot_skip_symlinked_markdown(self):
        (self.vault / "memories").mkdir(parents=True, exist_ok=True)
        (self.vault / "memories" / "real.md").write_text(
            "# 真实记忆\n\n正常内容。\n", encoding="utf-8"
        )
        self.symlink("memories/leak.md")
        self.symlink("tasks/leak-task.md")

        self.assertIn("没有找到", vault_search.search_vault("LEAKED-BODY"))
        self.assertNotIn(
            "memories/leak.md",
            [item["path"] for item in rt.scan_files()],
        )
        self.assertNotIn(
            "LEAKED-TITLE",
            "\n".join(vault_tasks.time_sensitive_lines()),
        )

    def test_get_related_rejects_symlinked_document(self):
        self.symlink("memories/leak.md")

        result = vault_search.get_related("memories/leak.md")

        self.assertNotIn("LEAKED", result)
        self.assertIn("路径不合法或文件不存在", result)


class ArchiveRetrySyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        rt.configure(self.vault)
        rt.GIT_SYNC_MODE = "off"
        self.original_git_sync = rt.git_sync

    def tearDown(self):
        rt.git_sync = self.original_git_sync
        self.temp.cleanup()

    def test_retried_archive_confirms_result_and_finishes_sync(self):
        (self.vault / "memories").mkdir(parents=True, exist_ok=True)
        (self.vault / "memories" / "old.md").write_text(
            "# 过时记忆\n\n内容。\n", encoding="utf-8"
        )
        first = vault_writes.archive_memory("memories/old.md", "过时", "test")
        self.assertIn("已归档", first)
        self.assertFalse((self.vault / "memories" / "old.md").exists())

        synced_paths: list[tuple[Path, ...]] = []

        def fake_git_sync(message: str, *paths: Path) -> str:
            synced_paths.append(paths)
            return "（无新变更；已补推此前未推送的提交到 GitHub）"

        rt.git_sync = fake_git_sync

        retry = vault_writes.archive_memory("memories/old.md", "过时", "test")

        self.assertIn("此前已归档", retry)
        self.assertIn("已补推", retry)
        self.assertNotIn("文件不存在", retry)
        self.assertEqual(1, len(synced_paths))
        self.assertTrue(str(synced_paths[0][1]).endswith("_old.md"))

    def test_missing_file_without_archived_copy_still_reports_not_found(self):
        result = vault_writes.archive_memory("memories/never-existed.md", "x", "test")

        self.assertIn("文件不存在", result)

    def test_retry_never_claims_a_same_named_archive_from_another_directory(self):
        (self.vault / "memories").mkdir(parents=True, exist_ok=True)
        (self.vault / "memories" / "retry.md").write_text(
            "# 记忆\n\n内容。\n", encoding="utf-8"
        )
        vault_writes.archive_memory("memories/retry.md", "过时", "test")

        result = vault_writes.archive_memory("projects/retry.md", "过时", "test")

        self.assertIn("文件不存在", result)
        self.assertNotIn("此前已归档", result)

    def test_retry_rejects_archived_copy_without_recorded_origin(self):
        retired = self.vault / "_archive" / "retired"
        retired.mkdir(parents=True, exist_ok=True)
        (retired / "2026-01-01_legacy.md").write_text(
            "---\narchived: '2026-01-01'\n---\n\n# 旧副本\n", encoding="utf-8"
        )

        result = vault_writes.archive_memory("memories/legacy.md", "x", "test")

        self.assertIn("文件不存在", result)
        self.assertNotIn("此前已归档", result)


class DailyBackfillTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        rt.configure(self.vault)
        rt.GIT_SYNC_MODE = "off"

    def tearDown(self):
        self.temp.cleanup()

    def test_backfills_past_date_and_time(self):
        result = vault_writes.log_daily(
            "补记内容",
            source="test",
            date="2024-01-02",
            time="08:09",
        )

        path = self.vault / "diary" / "2024-01-02.md"
        self.assertIn("diary/2024-01-02.md", result)
        self.assertIn("- **08:09** [test] 补记内容", path.read_text(encoding="utf-8"))

    def test_rejects_future_date_and_invalid_time(self):
        future = (rt.today() + datetime.timedelta(days=1)).isoformat()

        self.assertIn("不能给未来日期", vault_writes.log_daily("x", date=future))
        self.assertIn("time 必须", vault_writes.log_daily("x", time="24:00"))


class GitSyncFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name).resolve()
        (self.vault / ".git").mkdir()
        self.original_vault = rt.VAULT
        self.original_git_enabled = rt.git_enabled
        self.original_run_git = rt.run_git
        rt.VAULT = self.vault
        rt.git_enabled = lambda: True
        self.calls: list[tuple[str, ...]] = []
        self.responses: dict[tuple[str, ...], subprocess.CompletedProcess] = {}

        def fake_run(*args: str, timeout: int = rt.GIT_TIMEOUT):
            self.calls.append(args)
            if args in self.responses:
                return self.responses[args]
            if args[:1] == ("diff",) and "--quiet" in args:
                return completed(1)
            if args[:1] == ("branch",):
                return completed(0, stdout="main\n")
            if args[:2] == ("rev-list", "--count"):
                return completed(0, stdout="1\n")
            return completed(0)

        rt.run_git = fake_run

    def tearDown(self):
        rt.VAULT = self.original_vault
        rt.git_enabled = self.original_git_enabled
        rt.run_git = self.original_run_git
        self.temp.cleanup()

    def changed_path(self) -> Path:
        return self.vault / "memories" / "changed.md"

    def test_commits_before_pull(self):
        message = rt.git_sync("auto: test", self.changed_path())

        self.assertEqual("（已同步到 GitHub）", message)
        operations = [
            operation
            for call in self.calls
            for operation in [call[1] if call[:1] == ("--literal-pathspecs",) else call[0]]
            if operation in {"add", "commit", "pull", "push"}
        ]
        self.assertEqual(["add", "commit", "pull", "push"], operations[:4])

    def test_retried_archive_with_committed_deletion_still_pushes(self):
        self.responses[
            ("--literal-pathspecs", "ls-files", "--error-unmatch", "--", "memories/changed.md")
        ] = completed(1)
        self.responses[("diff", "--cached", "--quiet")] = completed(0)

        message = rt.git_sync("auto: test", self.changed_path())

        self.assertEqual("（无新变更；已补推此前未推送的提交到 GitHub）", message)
        self.assertNotIn(("--literal-pathspecs", "add", "--", "memories/changed.md"), self.calls)
        self.assertIn(("push",), self.calls)

    def test_pull_failure_returns_real_error_and_skips_push(self):
        self.responses[("pull", "--rebase")] = completed(
            1,
            stderr="CONFLICT (content): Merge conflict in tasks/example.md\n",
        )

        message = rt.git_sync("auto: test", self.changed_path())

        self.assertIn("pull --rebase 失败", message)
        self.assertIn("CONFLICT (content)", message)
        self.assertNotIn(("push",), self.calls)

    def test_push_failure_returns_real_error(self):
        self.responses[("push",)] = completed(
            1,
            stderr="! [rejected] main -> main (non-fast-forward)\n",
        )

        message = rt.git_sync("auto: test", self.changed_path())

        self.assertIn("push 失败", message)
        self.assertIn("non-fast-forward", message)
        self.assertIn("未远端同步", message)

    def test_no_new_change_retries_unpushed_commits(self):
        self.responses[("diff", "--cached", "--quiet")] = completed(0)
        self.responses[("rev-list", "--count", "origin/main..HEAD")] = completed(
            0,
            stdout="2\n",
        )

        message = rt.git_sync("auto: test", self.changed_path())

        self.assertIn("已补推此前未推送的提交", message)
        self.assertIn(("push",), self.calls)
        self.assertFalse(any(call[:1] == ("commit",) for call in self.calls))

    def test_error_text_is_bounded_and_includes_both_streams(self):
        message = rt.git_error_text(
            completed(1, stdout="stdout detail", stderr="stderr detail"),
            limit=40,
        )

        self.assertIn("stderr detail", message)
        self.assertIn("stdout detail", message)
        self.assertLessEqual(len(message), 40)


if __name__ == "__main__":
    unittest.main()
