from __future__ import annotations

import unittest
from unittest import mock

from trend_analyzer.models import RecorderSourceConfig
from trend_analyzer.ui import MainWindow


class TagsWindowSourceSelectionTests(unittest.TestCase):
    def test_effective_tags_source_prefers_explicit_selection(self) -> None:
        explicit_source = RecorderSourceConfig(id="remote-1", name="Remote", host="10.0.0.5", port=18777)
        proxy_source = RecorderSourceConfig(id="local-recorder-api", name="Local recorder", host="127.0.0.1", port=18777)

        class DummyWindow:
            def _selected_tags_source(self):
                return explicit_source

            def _local_modbus_proxy_source(self):
                return proxy_source

        result = MainWindow._effective_tags_source_for_io(DummyWindow())
        self.assertIs(result, explicit_source)

    def test_effective_tags_source_uses_local_proxy_for_local_selection(self) -> None:
        proxy_source = RecorderSourceConfig(id="local-recorder-api", name="Local recorder", host="127.0.0.1", port=18777)

        class DummyWindow:
            def _selected_tags_source(self):
                return None

            def _local_modbus_proxy_source(self):
                return proxy_source

        result = MainWindow._effective_tags_source_for_io(DummyWindow())
        self.assertIs(result, proxy_source)

    def test_read_tags_once_uses_effective_source_for_local_proxy(self) -> None:
        proxy_source = RecorderSourceConfig(id="local-recorder-api", name="Local recorder", host="127.0.0.1", port=18777)

        class _DummyTable:
            def rowCount(self):
                return 0

        class DummyWindow:
            tags_table = _DummyTable()

            def _selected_tags_source(self):
                raise AssertionError("_selected_tags_source should not be used for IO read path")

            def _effective_tags_source_for_io(self):
                return proxy_source

        ok_count, fail_count = MainWindow._read_tags_once(DummyWindow(), update_status=False)
        self.assertEqual((ok_count, fail_count), (0, 0))

    def test_maintain_active_tag_pulses_reasserts_one_while_button_is_held(self) -> None:
        class DummyButton:
            def isDown(self):
                return True

        class DummyTable:
            def rowCount(self):
                return 1

            def cellWidget(self, row, column):
                if row == 0 and column == 10:
                    return DummyButton()
                return None

        class DummyStatus:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

        timer = mock.Mock()

        class DummyWindow:
            tags_table = DummyTable()
            status_label = DummyStatus()
            _tags_pulse_active_rows = {"tag-1": 0}

            def _pulse_button_for_row(self, row):
                return DummyButton()

            def _finish_tag_pulse(self, row, pulse_tag_id):
                raise AssertionError("pulse should stay active while button is held")

            def _write_tag_row_forced_value(self, row, value):
                self.last_write = (row, value)
                return True, "ok"

            def _set_tag_row_status(self, row, text, error=False):
                self.last_status = (row, text, error)

            _tags_pulse_fail_safe_timer = timer

        window = DummyWindow()
        MainWindow._maintain_active_tag_pulses(window)
        self.assertEqual(window.last_write, (0, 1.0))
        self.assertEqual(window.last_status, (0, "Импульс=1", False))
        timer.start.assert_called_once()

    def test_maintain_active_tag_pulses_finishes_when_button_is_no_longer_down(self) -> None:
        class DummyButton:
            def isDown(self):
                return False

        class DummyTable:
            def rowCount(self):
                return 1

            def cellWidget(self, row, column):
                if row == 0 and column == 10:
                    return DummyButton()
                return None

        class DummyWindow:
            tags_table = DummyTable()
            _tags_pulse_active_rows = {"tag-1": 0}

            def _pulse_button_for_row(self, row):
                return DummyButton()

            def _finish_tag_pulse(self, row, pulse_tag_id):
                self.finished = (row, pulse_tag_id)

        window = DummyWindow()
        MainWindow._maintain_active_tag_pulses(window)
        self.assertEqual(window.finished, (0, "tag-1"))


if __name__ == "__main__":
    unittest.main()
