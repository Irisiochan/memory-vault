import datetime
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _meta import vault_runtime as rt
from _meta import vault_tasks


def write_task(vault: Path, name: str, frontmatter: str, title: str) -> None:
    (vault / "tasks" / f"{name}.md").write_text(
        textwrap.dedent(frontmatter).strip() + f"\n\n# {title}\n",
        encoding="utf-8",
    )


class TaskSnapshotTierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="memory-vault-task-tiers-")
        self.vault = Path(self.temp.name)
        (self.vault / "tasks").mkdir(parents=True, exist_ok=True)
        self.original_vault = rt.VAULT
        self.original_today = rt.today
        rt.VAULT = self.vault
        rt.today = lambda: datetime.date(2026, 8, 5)

    def tearDown(self) -> None:
        rt.VAULT = self.original_vault
        rt.today = self.original_today
        self.temp.cleanup()

    def snapshot(self) -> str:
        return "\n".join(vault_tasks.time_sensitive_lines())

    def test_due_task_is_not_dormant(self) -> None:
        write_task(
            self.vault,
            "due-soon",
            """
            ---
            type: task
            created: '2026-06-01'
            due: '2026-08-08'
            status: open
            ---
            """,
            "带期限旧任务",
        )
        out = self.snapshot()
        self.assertIn("带期限旧任务", out)
        self.assertIn("还有 3 天", out)
        self.assertNotIn("冬眠", out)

    def test_fresh_no_due_task_stays_active(self) -> None:
        write_task(
            self.vault,
            "fresh",
            """
            ---
            type: task
            created: '2026-08-01'
            due: none
            status: open
            ---
            """,
            "新鲜无期限任务",
        )
        out = self.snapshot()
        self.assertIn(
            "**新鲜无期限任务** (`tasks/fresh.md`)（无期限，仍未完成）", out
        )
        self.assertNotIn("冬眠", out)

    def test_stale_no_due_tasks_collapse_to_titles(self) -> None:
        write_task(
            self.vault,
            "stale-a",
            """
            ---
            type: task
            created: '2026-05-01'
            updated: '2026-07-01'
            due: none
            status: open
            ---
            """,
            "陈旧任务甲",
        )
        write_task(
            self.vault,
            "stale-b",
            """
            ---
            type: task
            created: '2026-06-15'
            due: none
            status: open
            ---
            """,
            "陈旧任务乙",
        )
        out = self.snapshot()
        self.assertIn("💤 另有 2 条无期限任务", out)
        self.assertIn("陈旧任务甲", out)
        self.assertIn("陈旧任务乙", out)
        self.assertNotIn("tasks/stale-a.md", out)
        self.assertNotIn("tasks/stale-b.md", out)

    def test_updated_date_wins_over_created_date(self) -> None:
        write_task(
            self.vault,
            "revived",
            """
            ---
            type: task
            created: '2026-05-01'
            updated: '2026-08-04'
            due: none
            status: open
            ---
            """,
            "刚更新过的老任务",
        )
        out = self.snapshot()
        self.assertIn("tasks/revived.md", out)
        self.assertNotIn("冬眠", out)

    def test_dormant_flag_forces_both_directions(self) -> None:
        write_task(
            self.vault,
            "forced-dormant",
            """
            ---
            type: task
            created: '2026-08-04'
            due: none
            status: open
            dormant: true
            ---
            """,
            "手动冬眠的新任务",
        )
        write_task(
            self.vault,
            "forced-active",
            """
            ---
            type: task
            created: '2026-05-01'
            due: none
            status: open
            dormant: false
            ---
            """,
            "手动保活的老任务",
        )
        out = self.snapshot()
        self.assertIn("💤 另有 1 条", out)
        self.assertIn("手动冬眠的新任务", out)
        self.assertNotIn("tasks/forced-dormant.md", out)
        self.assertIn("tasks/forced-active.md", out)

    def test_unquoted_yaml_date_and_done_tasks(self) -> None:
        write_task(
            self.vault,
            "unquoted",
            """
            ---
            type: task
            created: 2026-06-01
            due: none
            status: open
            ---
            """,
            "无引号日期的陈旧任务",
        )
        write_task(
            self.vault,
            "closed",
            """
            ---
            type: task
            created: '2026-05-01'
            due: none
            status: done
            ---
            """,
            "已完成任务",
        )
        out = self.snapshot()
        self.assertIn("💤 另有 1 条", out)
        self.assertIn("无引号日期的陈旧任务", out)
        self.assertNotIn("已完成任务", out)


if __name__ == "__main__":
    unittest.main()
