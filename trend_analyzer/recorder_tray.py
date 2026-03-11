from __future__ import annotations

import ctypes
from datetime import datetime
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
from .instance_lock import SingleInstanceLock, show_already_running_message
from .recorder_shared import (
    RECORDER_CONFIG_PATH,
    RECORDER_PID_PATH,
    RECORDER_STATUS_PATH,
    clear_recorder_pid,
    is_recorder_pid_running,
    resolve_recorder_pid,
    read_recorder_status,
    request_recorder_stop,
)
from .startup import is_windows_autostart_enabled, set_windows_autostart
from .version import APP_NAME

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


RECORDER_TRAY_AUTOSTART_APP_NAME = "TrendAnalyzerRecorder"


def _detached_process_env() -> dict[str, str]:
    env = os.environ.copy()
    # For PyInstaller one-file builds: force child process to use its own
    # extraction dir so parent can clean up _MEI on exit.
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


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


def _format_bytes(size: int | float | None) -> str:
    try:
        value = float(size or 0.0)
    except (TypeError, ValueError):
        return "n/a"
    if value < 1024.0:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.2f} {unit}"
    return f"{value:.2f} PB"


def _format_ts(ts: object) -> str:
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return "n/a"
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "n/a"


def _status_updated_age_seconds(payload: dict | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("updated_at") or "").strip()
    if not raw:
        return None
    try:
        updated_dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    try:
        age = (datetime.now() - updated_dt).total_seconds()
    except Exception:
        return None
    return max(0.0, float(age))


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
        if self._is_viewer_running():
            if self._tray_icon is not None:
                self._tray_icon.showMessage(
                    APP_NAME,
                    "Клиент уже запущен.",
                    QSystemTrayIcon.MessageIcon.Information,
                    1400,
                )
            return
        cmd = self._viewer_command()
        workdir = Path.cwd()
        if getattr(sys, "frozen", False):
            try:
                workdir = Path(sys.argv[0]).resolve().parent
            except Exception:
                workdir = Path.cwd()
        kwargs: dict[str, object] = {
            "cwd": str(workdir),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
            "env": _detached_process_env(),
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

    @staticmethod
    def _is_viewer_running() -> bool:
        if psutil is None:
            return False
        candidates = {"trendclient.exe", "trendanalyzer.exe"}
        try:
            current_pid = os.getpid()
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                info = proc.info
                pid = int(info.get("pid") or 0)
                if pid <= 0 or pid == current_pid:
                    continue
                name = str(info.get("name") or "").strip().lower()
                if name not in candidates:
                    continue
                cmdline = [str(part).lower() for part in (info.get("cmdline") or [])]
                if "--recorder" in cmdline or "--recorder-tray" in cmdline:
                    continue
                return True
        except Exception:
            return False
        return False

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
            # Single-binary recorder mode: run the same tray EXE with --recorder.
            return [sys.executable, "--recorder"]
        main_path = Path(__file__).resolve().parent.parent / "main.py"
        return [sys.executable, str(main_path), "--recorder"]

    def _viewer_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            exe_path = Path(sys.executable).resolve()
            client_candidate = exe_path.with_name("TrendClient.exe")
            if client_candidate.exists():
                return [str(client_candidate)]
            analyzer_candidate = exe_path.with_name("TrendAnalyzer.exe")
            if analyzer_candidate.exists():
                return [str(analyzer_candidate)]
            raise RuntimeError("Не найден TrendClient.exe рядом с recorder")
        main_path = Path(__file__).resolve().parent.parent / "main.py"
        return [sys.executable, str(main_path)]

    def _start_recorder(self, _checked: bool = False, silent: bool = False) -> bool:
        pid = resolve_recorder_pid()
        if pid is not None:
            self._refresh_status_ui()
            return True

        status_before = read_recorder_status() or {}
        prev_updated_at = str(status_before.get("updated_at") or "")
        # Heal stale pid-file so startup validation only accepts fresh runtime.
        clear_recorder_pid()

        cmd = self._recorder_command()
        workdir = Path.cwd()
        if getattr(sys, "frozen", False):
            try:
                workdir = Path(sys.argv[0]).resolve().parent
            except Exception:
                workdir = Path.cwd()
        kwargs: dict[str, object] = {
            "cwd": str(workdir),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
            "env": _detached_process_env(),
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

        started = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            QApplication.processEvents()
            pid = resolve_recorder_pid()
            if pid is not None:
                started = True
                break
            status_payload = read_recorder_status() or {}
            status_state = str(status_payload.get("state") or "").strip().lower()
            status_updated_at = str(status_payload.get("updated_at") or "")
            status_pid_raw = status_payload.get("pid")
            try:
                status_pid = int(status_pid_raw)
            except Exception:
                status_pid = 0
            if (
                status_state == "running"
                and status_updated_at
                and status_updated_at != prev_updated_at
                and status_pid > 0
                and is_recorder_pid_running(status_pid)
            ):
                started = True
                break
            time.sleep(0.15)

        self._refresh_status_ui()
        if started:
            return True

        status = read_recorder_status() or {}
        status_age = _status_updated_age_seconds(status)
        if status_age is not None and status_age > 30.0:
            detail = "статус регистратора устарел, процесс не подтвердил новый запуск"
        else:
            detail = str(status.get("last_error") or status.get("message") or "процесс не зарегистрировал PID")
        if not silent:
            QMessageBox.warning(None, APP_NAME, f"Регистратор не стартовал: {detail}")
        elif self._tray_icon is not None:
            self._tray_icon.showMessage(
                APP_NAME,
                f"Не удалось стартовать запись: {detail}",
                QSystemTrayIcon.MessageIcon.Warning,
                2200,
            )
        return False

    def _stop_recorder(self, _checked: bool = False) -> None:
        pid = resolve_recorder_pid()
        if pid is None:
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
            if resolve_recorder_pid() is None:
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
        live_pid = resolve_recorder_pid()
        status_age = _status_updated_age_seconds(payload)
        is_stale = status_age is None or status_age > 30.0
        state_raw = str(payload.get("state", "n/a"))
        if live_pid is None and state_raw.lower() == "running":
            state_view = "stale-running (процесс не найден)"
        else:
            state_view = state_raw

        dialog = QDialog()
        dialog.setWindowTitle("Статус внешнего регистратора")
        dialog.resize(860, 560)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        connected = bool(payload.get("connected", False)) and live_pid is not None
        summary_lines = [
            f"Состояние: {state_view}",
            f"Соединение: {'подключено' if connected else 'нет связи'}",
            f"Сообщение: {payload.get('message', '')}",
            f"PID: {payload.get('pid', 'n/a')}",
            f"PID (живой): {live_pid if live_pid is not None else 'не найден'}",
            f"Статус обновлен: {payload.get('updated_at', 'n/a')}",
            f"Свежесть статуса: {'ok' if not is_stale else 'устарел'}{'' if status_age is None else f' ({status_age:.1f} c)'}",
            f"Профиль: {payload.get('profile_name', 'n/a')} ({payload.get('profile_id', 'n/a')})",
            f"CPU: {float(payload.get('cpu_percent', 0.0) or 0.0):.1f}%",
            f"RAM: {_format_bytes(payload.get('ram_bytes', 0))}",
            f"Размер БД: {_format_bytes(payload.get('db_size_bytes', 0))}",
            f"Начало архива: {_format_ts(payload.get('archive_begin_ts'))}",
            f"Последняя запись: {_format_ts(payload.get('last_archive_ts'))}",
            f"Старт процесса: {payload.get('started_at', 'n/a')}",
            f"Путь БД: {payload.get('db_path', 'n/a')}",
        ]
        text = "\n".join(summary_lines) + "\n\nJSON:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        edit.setPlainText(text)
        layout.addWidget(edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _refresh_status_ui(self) -> None:
        pid = resolve_recorder_pid()
        running = pid is not None
        if self._action_start is not None:
            self._action_start.setEnabled(not running)
        if self._action_stop is not None:
            self._action_stop.setEnabled(running)

        if self._tray_icon is None:
            return

        if running:
            payload = read_recorder_status() or {}
            cpu = float(payload.get("cpu_percent", 0.0) or 0.0)
            ram = _format_bytes(payload.get("ram_bytes", 0))
            db_size = _format_bytes(payload.get("db_size_bytes", 0))
            connected = "подключено" if bool(payload.get("connected", False)) else "нет связи"
            self._tray_icon.setToolTip(
                f"{APP_NAME}: recorder активен (PID {pid}) | {connected} | CPU {cpu:.1f}% | RAM {ram} | БД {db_size}"
            )
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

    instance_lock = SingleInstanceLock("trend_recorder_tray")
    if not instance_lock.acquire():
        show_already_running_message(APP_NAME, "Tray-регистратор уже запущен.")
        return

    app = QApplication(sys.argv)
    app._instance_lock = instance_lock  # type: ignore[attr-defined]
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
