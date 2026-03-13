from __future__ import annotations

from types import SimpleNamespace
import unittest

from trend_analyzer.ui import MainWindow


class OnlineHistoryBackendRoutingTests(unittest.TestCase):
    def test_load_recent_online_history_from_db_skips_when_archive_disabled(self) -> None:
        window = SimpleNamespace(current_profile=SimpleNamespace(archive_to_db=False))
        self.assertFalse(MainWindow._load_recent_online_history_from_db(window))

    def test_reload_visible_history_routes_to_local_api_when_archive_disabled(self) -> None:
        class DummyWindow:
            _should_use_local_api_history_in_online_mode = lambda self: True

            def __init__(self) -> None:
                self._pending_history_x_range = (10.0, 20.0)
                self.called = None

            def _load_history_window_from_local_api(self, left, right, preserve_range=True, silent=True):
                self.called = ("api", left, right, preserve_range, silent)
                return True

            def _load_history_window_from_db(self, left, right, preserve_range=True, silent=True):
                self.called = ("db", left, right, preserve_range, silent)
                return True

        window = DummyWindow()
        MainWindow._reload_visible_history_from_db(window)

        self.assertEqual(window.called, ("api", 10.0, 20.0, True, True))
        self.assertIsNone(window._pending_history_x_range)

    def test_schedule_visible_history_reload_does_not_require_db_for_local_api_history(self) -> None:
        class DummyTimer:
            def __init__(self) -> None:
                self.started_with = None

            def isActive(self) -> bool:
                return False

            def interval(self) -> int:
                return 220

            def start(self, value: int) -> None:
                self.started_with = value

        class DummyChart:
            def follows_latest_x(self) -> bool:
                return False

        class DummyMode:
            def currentData(self):
                return "online"

        class DummyWindow:
            _should_use_local_api_history_in_online_mode = MainWindow._should_use_local_api_history_in_online_mode

            def __init__(self) -> None:
                self._history_reload_guard = False
                self.current_profile = SimpleNamespace(render_chart_enabled=True, archive_to_db=False)
                self.mode_combo = DummyMode()
                self.chart = DummyChart()
                self._history_loaded_range = None
                self._history_loaded_bucket_s = None
                self._history_view_timer = DummyTimer()
                self._pending_history_x_range = None

            def _profile_uses_local_recorder(self) -> bool:
                return True

            def _local_recorder_api_source(self):
                return object()

            def _target_history_points(self, span_s=None) -> int:
                return 1000

        window = DummyWindow()
        MainWindow._schedule_visible_history_reload(window, 100.0, 160.0, force=False)

        self.assertEqual(window._pending_history_x_range, (100.0, 160.0))
        self.assertEqual(window._history_view_timer.started_with, 220)

    def test_on_render_chart_toggled_routes_online_restore_to_local_api_when_archive_disabled(self) -> None:
        class DummyAction:
            def isChecked(self) -> bool:
                return True

        class DummyMode:
            def currentData(self):
                return "online"

        class DummyChart:
            def current_x_range(self):
                return (0.0, 120.0)

        class DummyWindow:
            _should_use_local_api_history_in_online_mode = lambda self: True

            def __init__(self) -> None:
                self.current_profile = SimpleNamespace(render_chart_enabled=False, archive_to_db=False)
                self.mode_combo = DummyMode()
                self.action_auto_x = DummyAction()
                self.chart = DummyChart()
                self._updating_ui = True
                self.local_api_called = None
                self.remote_history_called = None

            def _collect_active_signal_ids(self):
                return {"sig-1"}

            def _lightweight_live_history_span_s(self):
                return 180.0

            def _load_recent_local_api_history(self, span_s, signal_ids=None, *, adjust_x_range=True, silent=True):
                self.local_api_called = (span_s, set(signal_ids or set()), adjust_x_range, silent)
                return True

            def _load_recent_online_history_from_db(self, *args, **kwargs):
                raise AssertionError("DB history loader must not be used when archive_to_db is disabled")

            def _load_recent_remote_history_from_sources(self, span_s, signal_ids=None):
                self.remote_history_called = (span_s, set(signal_ids or set()))

            def _is_live_stream_running(self) -> bool:
                return False

            def _update_runtime_status_panel(self) -> None:
                pass

        window = DummyWindow()
        MainWindow._on_render_chart_toggled(window, True)

        self.assertEqual(window.local_api_called, (180.0, {"sig-1"}, True, True))
        self.assertEqual(window.remote_history_called, (120.0, {"sig-1"}))


if __name__ == "__main__":
    unittest.main()
