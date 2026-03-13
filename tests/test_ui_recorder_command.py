from __future__ import annotations

import concurrent.futures
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

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

            self.assertEqual(cmd, [str(recorder_exe.resolve()), "--recorder-tray"])

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

    def test_external_recorder_start_confirmed_accepts_fresh_running_status(self) -> None:
        window = MainWindow.__new__(MainWindow)
        with mock.patch("trend_analyzer.ui.resolve_recorder_pid", return_value=None), mock.patch(
            "trend_analyzer.ui.read_recorder_status",
            return_value={
                "state": "running",
                "updated_at": "2026-03-13T14:00:05",
                "pid": 4321,
            },
        ):
            ok = MainWindow._external_recorder_start_confirmed(window, "2026-03-13T14:00:00")
        self.assertTrue(ok)

    def test_external_recorder_start_confirmed_rejects_stale_status(self) -> None:
        window = MainWindow.__new__(MainWindow)
        with mock.patch("trend_analyzer.ui.resolve_recorder_pid", return_value=None), mock.patch(
            "trend_analyzer.ui.read_recorder_status",
            return_value={
                "state": "running",
                "updated_at": "2026-03-13T14:00:00",
                "pid": 4321,
            },
        ):
            ok = MainWindow._external_recorder_start_confirmed(window, "2026-03-13T14:00:00")
        self.assertFalse(ok)

    def test_wait_for_local_recorder_api_bootstrap_retries_until_ready(self) -> None:
        class DummyWindow:
            def __init__(self) -> None:
                self.bootstrap_calls = 0

            def _local_recorder_api_source(self):
                return object()

            def _bootstrap_local_api_live_cursor(self):
                self.bootstrap_calls += 1
                return self.bootstrap_calls >= 3

            def _is_external_recorder_running(self):
                return True

        monotonic_values = iter([0.0, 0.1, 0.2, 0.3, 0.4])
        dummy_app = mock.Mock()
        with mock.patch("trend_analyzer.ui.QApplication.instance", return_value=dummy_app), mock.patch(
            "trend_analyzer.ui.time.monotonic",
            side_effect=lambda: next(monotonic_values),
        ), mock.patch("trend_analyzer.ui.time.sleep") as sleep_mock:
            ok = MainWindow._wait_for_local_recorder_api_bootstrap(DummyWindow(), timeout_s=0.5)

        self.assertTrue(ok)
        self.assertEqual(dummy_app.processEvents.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_local_live_fetch_db_payload_reads_incremental_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE samples (id INTEGER PRIMARY KEY, profile_id TEXT, signal_id TEXT, ts REAL, value REAL)"
                )
                conn.execute(
                    "CREATE TABLE connection_events (id INTEGER PRIMARY KEY, profile_id TEXT, ts REAL, is_connected INTEGER)"
                )
                conn.executemany(
                    "INSERT INTO samples (id, profile_id, signal_id, ts, value) VALUES (?, ?, ?, ?, ?)",
                    [
                        (1, "profile-a", "sig-1", 1.0, 10.0),
                        (2, "profile-a", "sig-1", 2.0, 11.0),
                        (3, "profile-b", "sig-x", 3.0, 99.0),
                    ],
                )
                conn.executemany(
                    "INSERT INTO connection_events (id, profile_id, ts, is_connected) VALUES (?, ?, ?, ?)",
                    [
                        (1, "profile-a", 1.5, 1),
                        (2, "profile-a", 2.5, 0),
                        (3, "profile-b", 3.5, 1),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            payload = MainWindow._local_live_fetch_db_payload(str(db_path), "profile-a", 1, 0)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "db")
        self.assertEqual(payload["sample_rows"], [(2, "sig-1", 2.0, 11.0)])
        self.assertEqual(payload["connection_rows"], [(1, 1.5, 1), (2, 2.5, 0)])

    def test_poll_local_live_stream_async_consumes_api_future(self) -> None:
        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_result(
            {
                "ok": True,
                "kind": "api",
                "payload": {
                    "ok": True,
                    "connected": True,
                    "samples": [{"tag_id": "sig-1", "ts": 10.0, "value": 42.5}],
                    "connection_events": [{"ts": 10.0, "is_connected": 1}],
                    "next_sample_id": 12,
                    "next_event_id": 4,
                },
            }
        )

        class DummyTimer:
            def interval(self) -> int:
                return 300

        class DummyWindow:
            _cancel_local_live_future = MainWindow._cancel_local_live_future
            _reset_local_live_async_state = MainWindow._reset_local_live_async_state
            _local_live_connected_stable = MainWindow._local_live_connected_stable

            def __init__(self) -> None:
                self._local_live_transport = "api"
                self._local_live_future = future
                self._local_live_backoff_until_mono = 0.0
                self._local_live_fail_count = 0
                self._local_last_ok_mono = None
                self._local_api_live_cursor = {"sample_id": 0, "event_id": 0}
                self._remote_live_executor = None
                self._db_live_timer = DummyTimer()
                self.current_profile = SimpleNamespace(archive_to_db=True)
                self.samples_rows = None
                self.connection_rows = None

            def _local_recorder_api_source(self):
                return object()

            def _append_local_api_samples_rows(self, rows):
                self.samples_rows = rows

            def _append_live_connection_event_payload(self, rows):
                self.connection_rows = rows

        window = DummyWindow()
        with mock.patch("trend_analyzer.ui.time.monotonic", return_value=100.0):
            connected = MainWindow._poll_local_live_stream_async(window)

        self.assertTrue(connected)
        self.assertEqual(window.samples_rows, [{"tag_id": "sig-1", "ts": 10.0, "value": 42.5}])
        self.assertEqual(window.connection_rows, [{"ts": 10.0, "is_connected": 1}])
        self.assertEqual(window._local_api_live_cursor, {"sample_id": 12, "event_id": 4})
        self.assertIsNone(window._local_live_future)

    def test_poll_local_live_stream_async_consumes_db_future(self) -> None:
        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_result(
            {
                "ok": True,
                "kind": "db",
                "sample_rows": [(5, "sig-1", 11.0, 55.0)],
                "connection_rows": [(3, 11.0, 1)],
            }
        )

        class DummyTimer:
            def interval(self) -> int:
                return 300

        class DummyWindow:
            _cancel_local_live_future = MainWindow._cancel_local_live_future
            _reset_local_live_async_state = MainWindow._reset_local_live_async_state
            _local_live_connected_stable = MainWindow._local_live_connected_stable
            _local_recorder_connected_from_status = MainWindow._local_recorder_connected_from_status

            def __init__(self) -> None:
                self._local_live_transport = "db"
                self._local_live_future = future
                self._local_live_backoff_until_mono = 0.0
                self._local_live_fail_count = 0
                self._local_last_ok_mono = None
                self._db_live_last_sample_row_id = 0
                self._db_live_last_connection_event_row_id = 0
                self._remote_live_executor = None
                self._db_live_timer = DummyTimer()
                self.current_profile = SimpleNamespace(id="profile-a", archive_to_db=True)
                self.sample_rows = None
                self.connection_rows = None

            def _append_db_live_sample_rows(self, rows):
                self.sample_rows = rows

            def _append_db_live_connection_rows(self, rows):
                self.connection_rows = rows

            def _is_external_recorder_running(self):
                return True

        window = DummyWindow()
        with mock.patch("trend_analyzer.ui.read_recorder_status", return_value={"profile_id": "profile-a", "connected": True}), mock.patch(
            "trend_analyzer.ui.time.monotonic", return_value=200.0
        ):
            connected = MainWindow._poll_local_live_stream_async(window)

        self.assertTrue(connected)
        self.assertEqual(window.sample_rows, [(5, "sig-1", 11.0, 55.0)])
        self.assertEqual(window.connection_rows, [(3, 11.0, 1)])
        self.assertEqual(window._db_live_last_sample_row_id, 5)
        self.assertEqual(window._db_live_last_connection_event_row_id, 3)
        self.assertIsNone(window._local_live_future)


if __name__ == "__main__":
    unittest.main()
