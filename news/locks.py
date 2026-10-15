"""Surecler arasi yazma kilidi.

SQLite tek yazici kabul eder. run_studio (ayri surec) ile gunicorn
(web) ayni anda yazinca "database is locked" hatasi olusuyor ve bulten
uretimi yarida kaliyordu. Bu kilit, haber cekme / bulten uretme /
kurulum gibi YAZMA islerini tek sirada calistirir.

Okuma isleri (sayfa gosterimi) etkilenmez; WAL modu sayesinde okuma
yazmayi bloklamaz.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


def _lock_path() -> Path:
    from django.conf import settings

    path = Path(settings.MEDIA_ROOT) / ".writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _acquire(handle, blocking: bool) -> bool:
    try:
        if os.name == "posix":
            import fcntl

            flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), flags)
            return True
        import msvcrt

        handle.seek(0)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
        return True
    except OSError:
        return False


def _release(handle) -> None:
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextmanager
def writer_lock(timeout: float = 240.0, poll: float = 0.5):
    """Yazma kilidi. True = kilit alindi, False = zaman asimi.

    Kullanim:
        with writer_lock() as ok:
            if not ok:
                return False, "baska bir yazma islemi suruyor"
            ...
    """
    handle = open(_lock_path(), "a+")
    deadline = time.monotonic() + max(0.0, timeout)
    acquired = False
    try:
        if _acquire(handle, blocking=False):
            acquired = True
        else:
            while time.monotonic() < deadline:
                time.sleep(poll)
                if _acquire(handle, blocking=False):
                    acquired = True
                    break
        yield acquired
    finally:
        if acquired:
            _release(handle)
        handle.close()
