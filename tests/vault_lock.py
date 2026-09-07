"""The operation lock must protect read/modify/write, not only Git commands."""

from __future__ import annotations

import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _meta.vault_lock import VaultLock


def stop_process(process):
    if process.is_alive():
        process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    process.close()


def increment(root, start, count):
    root = Path(root)
    lock = VaultLock(lambda: root)
    start.wait(10)
    for _ in range(count):
        with lock:
            value = int((root / "counter").read_text())
            time.sleep(0.002)  # Force overlap if only the write itself is locked.
            with lock:
                (root / "counter").write_text(str(value + 1))


def exit_while_locked(root, entered):
    with VaultLock(lambda: Path(root)):
        entered.set()
        os._exit(23)


def acquire_once(root, acquired):
    with VaultLock(lambda: Path(root)):
        acquired.set()


class VaultLockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = VaultLock(lambda: self.root)
        self.context = multiprocessing.get_context("spawn")

    def check_process(self, process, expected=0):
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("vault operation lock did not release")
        self.assertEqual(process.exitcode, expected)

    def test_two_server_processes_cannot_lose_read_modify_write_updates(self):
        (self.root / "counter").write_text("0")
        start = self.context.Event()
        processes = [self.context.Process(target=increment, args=(str(self.root), start, 40))
                     for _ in range(2)]
        for process in processes:
            process.start()
            self.addCleanup(stop_process, process)
        start.set()
        for process in processes:
            self.check_process(process)
        self.assertEqual((self.root / "counter").read_text(), "80")

    def test_thread_waits_through_nested_lock_and_acquires_after_exception(self):
        acquired = threading.Event()

        def wait_for_lock():
            with self.lock:
                acquired.set()

        with self.assertRaisesRegex(ValueError, "write failed"):
            with self.lock:
                thread = threading.Thread(target=wait_for_lock, daemon=True)
                thread.start()
                with self.lock:
                    self.assertFalse(acquired.wait(0.05))
                self.assertFalse(acquired.is_set())
                raise ValueError("write failed")
        self.assertTrue(acquired.wait(3))
        thread.join(3)

    def test_other_process_waits_until_entire_operation_finishes(self):
        acquired = self.context.Event()
        with self.lock:
            process = self.context.Process(target=acquire_once, args=(str(self.root), acquired))
            process.start()
            self.addCleanup(stop_process, process)
            with self.lock:
                self.assertFalse(acquired.wait(0.15))
            self.assertFalse(acquired.is_set())
        self.assertTrue(acquired.wait(5))
        self.check_process(process)

    def test_process_crash_does_not_leave_a_stale_lock(self):
        entered = self.context.Event()
        process = self.context.Process(target=exit_while_locked, args=(str(self.root), entered))
        process.start()
        self.addCleanup(stop_process, process)
        self.assertTrue(entered.wait(5))
        self.check_process(process, expected=23)
        acquired = self.context.Event()
        successor = self.context.Process(target=acquire_once, args=(str(self.root), acquired))
        successor.start()
        self.addCleanup(stop_process, successor)
        self.assertTrue(acquired.wait(5))
        self.check_process(successor)

    def test_open_failure_releases_thread_lock_and_root_is_resolved_per_operation(self):
        self.root = self.root / "missing"
        with self.assertRaises(FileNotFoundError):
            with self.lock:
                self.fail("must not enter when lock acquisition failed")
        self.root.mkdir()
        acquired = threading.Event()

        def retry():
            with self.lock:
                acquired.set()

        thread = threading.Thread(target=retry, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(3))
        thread.join(3)


if __name__ == "__main__":
    unittest.main()
