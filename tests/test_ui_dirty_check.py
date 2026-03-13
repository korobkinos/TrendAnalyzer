from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trend_analyzer.ui import MainWindow


class UiDirtyCheckNormalizationTests(unittest.TestCase):
    def test_runtime_tag_values_do_not_affect_dirty_check_payload(self) -> None:
        window = MainWindow.__new__(MainWindow)
        payload_a = {
            "tags": [
                {"id": "t1", "name": "Tag 1", "value": 10.0},
                {"id": "t2", "name": "Tag 2", "value": 20.0},
            ],
            "tag_tabs": [
                {
                    "id": "tab1",
                    "name": "Tab 1",
                    "tags": [
                        {"id": "t1", "name": "Tag 1", "value": 10.0},
                        {"id": "t2", "name": "Tag 2", "value": 20.0},
                    ],
                }
            ],
            "ui_state": {
                "view": {
                    "auto_x": True,
                    "x_range": [1.0, 2.0],
                    "scale_states": [
                        {"axis_index": 1, "auto_y": True, "y_min": -5.0, "y_max": 5.0},
                    ],
                }
            },
        }
        payload_b = {
            "tags": [
                {"id": "t1", "name": "Tag 1", "value": 111.0},
                {"id": "t2", "name": "Tag 2", "value": 222.0},
            ],
            "tag_tabs": [
                {
                    "id": "tab1",
                    "name": "Tab 1",
                    "tags": [
                        {"id": "t1", "name": "Tag 1", "value": 333.0},
                        {"id": "t2", "name": "Tag 2", "value": 444.0},
                    ],
                }
            ],
            "ui_state": {
                "view": {
                    "auto_x": True,
                    "x_range": [100.0, 200.0],
                    "scale_states": [
                        {"axis_index": 1, "auto_y": True, "y_min": -100.0, "y_max": 100.0},
                    ],
                }
            },
        }

        normalized_a = MainWindow._normalize_profile_payload_for_dirty_check(window, payload_a)
        normalized_b = MainWindow._normalize_profile_payload_for_dirty_check(window, payload_b)
        self.assertEqual(normalized_a, normalized_b)

    def test_archive_size_counts_wal_and_shm_parts(self) -> None:
        window = MainWindow.__new__(MainWindow)
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "archive.db"
            db_path.write_bytes(b"a" * 10)
            Path(str(db_path) + "-wal").write_bytes(b"b" * 20)
            Path(str(db_path) + "-shm").write_bytes(b"c" * 30)

            window.current_profile = SimpleNamespace(db_path=str(db_path))
            window._archive_store = None

            size = MainWindow._archive_file_size_bytes(window)
            self.assertEqual(size, 60)

    def test_chart_auto_mode_runtime_echo_does_not_mark_config_dirty(self) -> None:
        class _DummyToggle:
            def __init__(self, checked: bool) -> None:
                self._checked = bool(checked)

            def isChecked(self) -> bool:
                return self._checked

            def blockSignals(self, _blocked: bool) -> None:
                return None

            def setChecked(self, checked: bool) -> None:
                self._checked = bool(checked)

        window = MainWindow.__new__(MainWindow)
        window._updating_ui = False
        window.action_auto_x = _DummyToggle(True)
        window.action_auto_y = _DummyToggle(True)
        window.values_auto_x_checkbox = _DummyToggle(True)
        window.values_auto_y_checkbox = _DummyToggle(True)
        window._handle_auto_x_runtime_change = lambda _enabled: self.fail("auto-x runtime handler should not run")
        window._mark_config_dirty = lambda: self.fail("dirty flag should not be set for unchanged auto mode")

        MainWindow._on_chart_auto_mode_changed(window, True, True)

        self.assertTrue(window.action_auto_x.isChecked())
        self.assertTrue(window.action_auto_y.isChecked())
        self.assertTrue(window.values_auto_x_checkbox.isChecked())
        self.assertTrue(window.values_auto_y_checkbox.isChecked())

    def test_chart_auto_mode_change_marks_config_dirty_once(self) -> None:
        class _DummyToggle:
            def __init__(self, checked: bool) -> None:
                self._checked = bool(checked)

            def isChecked(self) -> bool:
                return self._checked

            def blockSignals(self, _blocked: bool) -> None:
                return None

            def setChecked(self, checked: bool) -> None:
                self._checked = bool(checked)

        window = MainWindow.__new__(MainWindow)
        window._updating_ui = False
        window.action_auto_x = _DummyToggle(True)
        window.action_auto_y = _DummyToggle(True)
        window.values_auto_x_checkbox = _DummyToggle(True)
        window.values_auto_y_checkbox = _DummyToggle(True)
        runtime_changes: list[bool] = []
        dirty_calls: list[str] = []
        window._handle_auto_x_runtime_change = lambda enabled: runtime_changes.append(bool(enabled))
        window._mark_config_dirty = lambda: dirty_calls.append("dirty")

        MainWindow._on_chart_auto_mode_changed(window, False, True)

        self.assertEqual(runtime_changes, [False])
        self.assertEqual(dirty_calls, ["dirty"])
        self.assertFalse(window.action_auto_x.isChecked())
        self.assertTrue(window.action_auto_y.isChecked())
        self.assertFalse(window.values_auto_x_checkbox.isChecked())
        self.assertTrue(window.values_auto_y_checkbox.isChecked())


if __name__ == "__main__":
    unittest.main()
