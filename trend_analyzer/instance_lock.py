from __future__ import annotations

import os
import sys
from pathlib import Path

from .storage import APP_DIR


class SingleInstanceLock:
    """Process lock based on an OS-level file lock."""

    def __init__(self, name: str):
        safe_name = str(name or "app").strip().replace(" ", "_")
        lock_dir = APP_DIR / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        self.path = lock_dir / f"{safe_name}.lock"
        self._fh = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        try:
            fh = open(self.path, "a+b")
        except Exception:
            return False

        try:
            if os.name == "nt":
                import msvcrt  # type: ignore

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # type: ignore

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            try:
                fh.close()
            except Exception:
                pass
            return False

        self._fh = fh
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()).encode("ascii", errors="ignore"))
            fh.flush()
        except Exception:
            pass
        return True

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # type: ignore

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.release()
        except Exception:
            pass


def show_already_running_message(title: str, text: str) -> None:
    """Show user-friendly 'already running' warning without requiring QApplication."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x00000030)
            return
        except Exception:
            pass
    try:
        print(f"{title}: {text}")
    except Exception:
        pass
