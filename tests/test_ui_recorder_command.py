from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from trend_analyzer.ui import MainWindow


class ViewerRecorderCommandTests(unittest.TestCase):
    def _set_frozen(self, executable: Path) -> tuple[object | None, str]:
        import sys

        prev_frozen = getattr(sys, "frozen", None)
        prev_exe = sys.executable
        sys.frozen = True
        sys.executable = str(executable)
        return prev_frozen, prev_exe

    def _restore_frozen(self, prev_frozen: object | None, prev_exe: str) -> None:
        import sys

        if prev_frozen is None and hasattr(sys, "frozen"):
            delattr(sys, "frozen")
        elif prev_frozen is not None:
            sys.frozen = prev_frozen
        sys.executable = prev_exe

    def test_external_recorder_command_uses_neighbor_trend_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            client_exe = root / "TrendClient.exe"
            recorder_exe = root / "TrendRecorder.exe"
            client_exe.write_bytes(b"")
            recorder_exe.write_bytes(b"")

            prev_frozen, prev_exe = self._set_frozen(client_exe)
            try:
                cmd = MainWindow._external_recorder_command(None)
            finally:
                self._restore_frozen(prev_frozen, prev_exe)

            self.assertEqual(cmd, [str(recorder_exe.resolve()), "--recorder"])

    def test_external_recorder_command_raises_if_recorder_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            client_exe = root / "TrendClient.exe"
            client_exe.write_bytes(b"")

            prev_frozen, prev_exe = self._set_frozen(client_exe)
            try:
                with self.assertRaises(RuntimeError):
                    MainWindow._external_recorder_command(None)
            finally:
                self._restore_frozen(prev_frozen, prev_exe)


if __name__ == "__main__":
    unittest.main()
