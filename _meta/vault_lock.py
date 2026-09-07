"""One reentrant operation boundary for threads and processes sharing a vault."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class VaultLock:
    """Hold this across the complete read/modify/write/Git operation.

    The OS releases the file lock if a server dies. Keep the lock file in place:
    deleting it while another process is waiting would create two lock domains.
    """

    def __init__(self, root: Callable[[], Path]):
        self._root = root
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._file = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                stream = (Path(self._root()) / ".vault-operation.lock").open("a+b")
                try:
                    if os.name == "nt":
                        # Windows byte-range locks need one persistent byte.
                        stream.seek(0, os.SEEK_END)
                        if stream.tell() == 0:
                            stream.write(b"\0")
                            stream.flush()
                        while True:
                            stream.seek(0)
                            try:
                                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                                break
                            except OSError as error:
                                if error.errno not in (13, 36):
                                    raise
                                time.sleep(0.05)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                except BaseException:
                    stream.close()
                    raise
                self._file = stream
            self._depth += 1
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self._depth -= 1
            if self._depth == 0:
                stream, self._file = self._file, None
                try:
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
        finally:
            self._thread_lock.release()
