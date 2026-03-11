from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QTableWidgetItem

from trend_analyzer.ui import (
    _install_qt_text_repair_patch,
    _repair_existing_ui_texts,
    _repair_status_text_mojibake,
)


class MojibakeRepairTests(unittest.TestCase):
    def test_repair_utf8_latin1_mojibake(self) -> None:
        source = "Статус: проверка кодировки"
        broken = source.encode("utf-8").decode("latin-1")
        self.assertEqual(_repair_status_text_mojibake(broken), source)

    def test_repair_utf8_cp1251_mojibake(self) -> None:
        source = "Ошибка: профиль отправлен"
        broken = source.encode("utf-8").decode("cp1251")
        self.assertEqual(_repair_status_text_mojibake(broken), source)

    def test_qt_set_text_is_globally_patched(self) -> None:
        app = QApplication.instance() or QApplication([])
        _install_qt_text_repair_patch()

        source = "Статус: тест"
        broken = source.encode("utf-8").decode("cp1251")

        label = QLabel()
        label.setText(broken)
        self.assertEqual(label.text(), source)

        item = QTableWidgetItem()
        item.setText(broken)
        self.assertEqual(item.text(), source)

        # Keep reference so Qt app is not optimized away in some environments.
        self.assertIsNotNone(app)

    def test_existing_widget_texts_are_normalized(self) -> None:
        from PySide6.QtWidgets import QMainWindow, QTableWidget

        app = QApplication.instance() or QApplication([])
        _install_qt_text_repair_patch()

        source = "Ошибка: источник недоступен"
        broken = source.encode("utf-8").decode("cp1251")

        win = QMainWindow()
        win.setWindowTitle(broken)
        table = QTableWidget(1, 1, win)
        item = QTableWidgetItem(broken)
        table.setItem(0, 0, item)

        _repair_existing_ui_texts(win)

        self.assertEqual(win.windowTitle(), source)
        self.assertEqual(item.text(), source)
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
