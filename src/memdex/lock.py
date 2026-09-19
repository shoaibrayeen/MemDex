"""A single-writer lock so two runs cannot interleave over the same memory."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from memdex.errors import LockedError

LOCK_NAME = "lock"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def acquire(memdex_dir: Path):
    lock_file = memdex_dir / LOCK_NAME
    memdex_dir.mkdir(parents=True, exist_ok=True)

    if lock_file.exists():
        try:
            holder = int(lock_file.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            holder = 0
        if holder and holder != os.getpid() and _process_alive(holder):
            raise LockedError(
                f"Another Memdex process (pid {holder}) is working on this project.",
                hint="Wait for it to finish, or delete .memdex/lock if it crashed.",
            )
        lock_file.unlink(missing_ok=True)  # stale

    handle = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(handle, str(os.getpid()).encode("utf-8"))
        os.close(handle)
        yield
    finally:
        lock_file.unlink(missing_ok=True)
