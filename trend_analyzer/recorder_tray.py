from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSystemTrayIcon,
    QVBoxLayout,
)

from .logging_utils import setup_logging
from .recorder_shared import (
    RECORDER_CONFIG_PATH,
    RECORDER_PID_PATH,
    RECORDER_STATUS_PATH,
    clear_recorder_pid,
    is_pid_running,
    read_recorder_pid,
    read_recorder_status,
    request_recorder_stop,
)
from .startup import is_windows_autostart_enabled, set_windows_autostart
from .version import APP_NAME


RECORDER_TRAY_AUTOSTART_APP_NAME = "TrendAnalyzerRecorder"


def _resolve_icon_path() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = Path(str(getattr(sys, "_MEIPASS", "")))
        if str(meipass):
            candidates.append(meipass / "assets" / "app_icon.ico")
            candidates.append(meipass / "app_icon.ico")

    project_root = Path(__file__).resolve().parent.parent
    candidates.append(project_root / "assets" / "app_icon.ico")

    for path in candidates:
        if path.exists():
            return path
    return None


class RecorderTrayController(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._tray_icon: QSystemTrayIcon | None = None
        self._action_start: QAction | None = None
        self._action_stop: QAction | None = None
        self._action_autostart: QAction | None = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self._refresh_status_ui)
        self._init_tray()
        self._status_timer.start()
        QTimer.singleShot(250, self._on_started)

    def _init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError("Системный трей недоступен")

        self._tray_icon = QSystemTrayIcon(self)
        icon = self._app.windowIcon()
        if not icon.isNull():
            self._tray_icon.setIcon(icon)

        menu = QMenu()
        self._action_start = menu.addAction("Старт записи")
        self._action_start.triggered.connect(self._start_recorder)

        self._action_stop = menu.addAction("Стоп записи")
        self._action_stop.triggered.connect(self._stop_recorder)

        action_status = menu.addAction("Статус регистратора...")
        action_status.triggered.connect(self._show_status_dialog)

        action_open_ui = menu.addAction("Открыть интерфейс настройки")
        action_open_ui.triggered.connect(self._open_viewer)

        menu.addSeparator()
        self._action_autostart = menu.addAction("Автозапуск tray-регистратора")
        self._action_autostart.setCheckable(True)
        if sys.platform == "win32":
            enabled, _error = is_windows_autostart_enabled(RECORDER_TRAY_AUTOSTART_APP_NAME)
            self._action_autostart.setChecked(enabled)
            self._action_autostart.triggered.connect(self._toggle_tray_autostart)
        else:
            self._action_autostart.setEnabled(False)

        menu.addSeparator()
        action_exit = menu.addAction("Выход")
        action_exit.triggered.connect(self._exit_tray)

        self._tray_icon.setContextMenu(menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()
        self._refresh_status_ui()

    def _on_started(self) -> None:
        started = self._start_recorder(silent=True)
        self._refresh_status_ui()
        if self._tray_icon is not None and started:
            self._tray_icon.showMessage(
                APP_NAME,
                "Tray-регистратор запущен. Запись стартует в фоне.",
                QSystemTrayIcon.MessageIcon.Information,
                1600,
            )
        elif self._tray_icon is not None and not started:
            self._tray_icon.showMessage(
                APP_NAME,
                "Tray запущен, но старт записи не подтвержден. Проверьте статус регистратора.",
                QSystemTrayIcon.MessageIcon.Warning,
                2200,
            )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_status_dialog()

    def _open_viewer(self) -> None:
        cmd = self._viewer_command()
        kwargs: dict[str, object] = {
            "cwd": str(Path.cwd()),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

        try:
            subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            QMessageBox.warning(None, APP_NAME, f"Не удалось открыть UI: {exc}")

    def _toggle_tray_autostart(self, checked: bool) -> None:
        enabled = bool(checked)
        ok, error = set_windows_autostart(
            enabled,
            app_name=RECORDER_TRAY_AUTOSTART_APP_NAME,
            extra_args=["--recorder-tray"],
        )
        if not ok:
            if self._action_autostart is not None:
                self._action_autostart.blockSignals(True)
                self._action_autostart.setChecked(False)
                self._action_autostart.blockSignals(False)
            QMessageBox.warning(None, APP_NAME, f"Ошибка автозапуска tray-регистратора: {error}")
            return

    def _recorder_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--recorder"]
        main_path = Path(__file__).resolve().parent.parent / "main.py"
        return [sys.executable, str(main_path), "--recorder"]

    def _viewer_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable]
        main_path = Path(__file__).resolve().parent.parent / "main.py"
        return [sys.executable, str(main_path)]

    def _start_recorder(self, _checked: bool = False, silent: bool = False) -> bool:
        pid = read_recorder_pid()
        if is_pid_running(pid):
            self._refresh_status_ui()
            return True

        cmd = self._recorder_command()
        kwargs: dict[str, object] = {
            "cwd": str(Path.cwd()),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

        try:
            subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            if not silent:
                QMessageBox.warning(None, APP_NAME, f"Ошибка запуска регистратора: {exc}")
            self._refresh_status_ui()
            return False

        time.sleep(0.6)
        self._refresh_status_ui()
        pid = read_recorder_pid()
        return bool(is_pid_running(pid))

    def _stop_recorder(self, _checked: bool = False) -> None:
        pid = read_recorder_pid()
        if not is_pid_running(pid):
            clear_recorder_pid()
            self._refresh_status_ui()
            return

        try:
            request_recorder_stop()
        except Exception as exc:
            QMessageBox.warning(None, APP_NAME, f"Ошибка отправки команды остановки: {exc}")
            self._refresh_status_ui()
            return

        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if not is_pid_running(pid):
                clear_recorder_pid()
                self._refresh_status_ui()
                return
            time.sleep(0.2)

        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
            else:
                os.kill(int(pid), signal.SIGTERM)
        except Exception as exc:
            QMessageBox.warning(None, APP_NAME, f"Ошибка принудительной остановки: {exc}")
            self._refresh_status_ui()
            return

        clear_recorder_pid()
        self._refresh_status_ui()

    def _show_status_dialog(self, _checked: bool = False) -> None:
        payload = read_recorder_status() or {}
        if not payload:
            payload = {
                "state": "unknown",
                "message": "статус регистратора недоступен",
                "status_path": str(RECORDER_STATUS_PATH),
                "config_path": str(RECORDER_CONFIG_PATH),
                "pid_path": str(RECORDER_PID_PATH),
            }

        dialog = QDialog()
        dialog.setWindowTitle("Статус внешнего регистратора")
        dialog.resize(860, 560)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        layout.addWidget(edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _refresh_status_ui(self) -> None:
        pid = read_recorder_pid()
        running = is_pid_running(pid)
        if self._action_start is not None:
            self._action_start.setEnabled(not running)
        if self._action_stop is not None:
            self._action_stop.setEnabled(running)

        if self._tray_icon is None:
            return

        if running:
            self._tray_icon.setToolTip(f"{APP_NAME}: recorder активен (PID {pid})")
        else:
            self._tray_icon.setToolTip(f"{APP_NAME}: recorder не запущен")

    def _exit_tray(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._app.quit()


def run_recorder_tray() -> None:
    setup_logging()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TrendAnalyzer.RecorderTray")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(f"{APP_NAME} Recorder Tray")
    app.setQuitOnLastWindowClosed(False)

    icon_path = _resolve_icon_path()
    if icon_path is not None:
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)

    try:
        controller = RecorderTrayController(app)
    except Exception as exc:
        QMessageBox.critical(None, APP_NAME, f"Не удалось запустить tray-регистратор: {exc}")
        return

    # Keep strong reference for the app lifetime.
    app._recorder_tray_controller = controller  # type: ignore[attr-defined]
    app.exec()
