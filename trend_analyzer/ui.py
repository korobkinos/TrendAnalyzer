from __future__ import annotations

import copy
import concurrent.futures
from contextlib import contextmanager
import csv
from datetime import datetime
import bisect
import ctypes
import ipaddress
import math
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
import uuid
import zipfile

from PySide6.QtCore import QDateTime, QEvent, QMimeData, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDrag, QFont, QIcon, QPainter, QPageLayout, QPageSize, QPen, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QMenu,
    QPlainTextEdit,
    QSystemTrayIcon,
    QToolButton,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg
from pymodbus.client import ModbusTcpClient
try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None

from .models import ProfileConfig, RecorderSourceConfig, SignalConfig, TagConfig, TagTabConfig
from .modbus_worker import ModbusWorker
from .storage import ArchiveStore, ConfigStore, DEFAULT_DB_PATH, atomic_write_text
from .archive_bundle import (
    ARCHIVE_BUNDLE_CONNECTION_CONFIG,
    ARCHIVE_BUNDLE_DIR,
    ARCHIVE_BUNDLE_FORMAT,
    ARCHIVE_BUNDLE_MAGIC,
    ARCHIVE_BUNDLE_MANIFEST,
    CONNECTION_CONFIG_FORMAT,
)
from .startup import set_windows_autostart
from .logging_utils import setup_logging
from .history_restore import compute_live_history_span_s
from .instance_lock import SingleInstanceLock, show_already_running_message
from .stability_policy import should_force_auto_x_on_start, should_preload_history_on_profile_load
from .recorder_shared import (
    RECORDER_CONFIG_FORMAT,
    RECORDER_CONFIG_PATH,
    RECORDER_PID_PATH,
    RECORDER_STATUS_PATH,
    clear_recorder_pid,
    resolve_recorder_pid,
    read_recorder_status,
    request_recorder_stop,
    write_recorder_config,
)
from .version import APP_NAME, app_title


DEFAULT_COLORS = [
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#8c564b",
]

ROLE_SIGNAL_ID = int(Qt.ItemDataRole.UserRole)
ROLE_SIGNAL_SOURCE_ID = ROLE_SIGNAL_ID + 1
ROLE_SIGNAL_REMOTE_TAG_ID = ROLE_SIGNAL_ID + 2

from .chart import MultiAxisChart, SIGNAL_IDS_MIME_TYPE, format_ts_ms


def _detached_process_env() -> dict[str, str]:
    env = os.environ.copy()
    # For PyInstaller one-file builds: force child process to use its own
    # extraction dir so parent can clean up _MEI on exit.
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _repair_status_text_mojibake(text: str) -> str:
    value = str(text)
    if not value:
        return value

    repaired = value
    # Typical UTF-8->Latin-1 mojibake fragments: Ð¡Ñ‚Ð°Ñ‚ÑƒÑ, ÐžÑˆÐ¸Ð±ÐºÐ°
    if "Ð" in repaired or "Ñ" in repaired:
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
            if candidate:
                repaired = candidate
        except Exception:
            pass

    # Typical UTF-8->CP1251 mojibake fragments: РЎС‚Р°С‚СѓСЃ, РћС€РёР±РєР°
    has_cp1251_markers = any(ch in repaired for ch in ("Ѓ", "ѓ", "‚", "€", "Љ", "Њ", "Ћ", "Џ", "љ", "њ", "ћ", "џ"))
    if has_cp1251_markers or "РЎ" in repaired or "Рћ" in repaired:
        try:
            candidate = repaired.encode("cp1251").decode("utf-8")
            if candidate:
                repaired = candidate
        except Exception:
            pass

    return repaired


_QT_TEXT_REPAIR_PATCHED = False


def _install_qt_text_repair_patch() -> None:
    """Install global Qt text setters patch to auto-repair mojibake at source."""
    global _QT_TEXT_REPAIR_PATCHED
    if _QT_TEXT_REPAIR_PATCHED:
        return
    _QT_TEXT_REPAIR_PATCHED = True

    def wrap_one_text_arg(cls: type, method_name: str) -> None:
        original = getattr(cls, method_name, None)
        if original is None or getattr(original, "__ta_text_repair_wrapped__", False):
            return

        def patched(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
            value = _repair_status_text_mojibake(str(text)) if isinstance(text, str) else text
            return original(self, value, *args, **kwargs)

        setattr(patched, "__ta_text_repair_wrapped__", True)
        setattr(cls, method_name, patched)

    def wrap_index_text_arg(cls: type, method_name: str) -> None:
        original = getattr(cls, method_name, None)
        if original is None or getattr(original, "__ta_text_repair_wrapped__", False):
            return

        def patched(self, index, text, *args, **kwargs):  # type: ignore[no-untyped-def]
            value = _repair_status_text_mojibake(str(text)) if isinstance(text, str) else text
            return original(self, index, value, *args, **kwargs)

        setattr(patched, "__ta_text_repair_wrapped__", True)
        setattr(cls, method_name, patched)

    def wrap_qmessagebox_method(method_name: str) -> None:
        original = getattr(QMessageBox, method_name, None)
        if original is None or getattr(original, "__ta_text_repair_wrapped__", False):
            return

        def patched(*args, **kwargs):  # type: ignore[no-untyped-def]
            fixed_args = list(args)
            # Signature is typically: parent, title, text, buttons, defaultButton
            if len(fixed_args) >= 2 and isinstance(fixed_args[1], str):
                fixed_args[1] = _repair_status_text_mojibake(fixed_args[1])
            if len(fixed_args) >= 3 and isinstance(fixed_args[2], str):
                fixed_args[2] = _repair_status_text_mojibake(fixed_args[2])
            for key in ("title", "text", "informativeText", "detailedText"):
                value = kwargs.get(key)
                if isinstance(value, str):
                    kwargs[key] = _repair_status_text_mojibake(value)
            return original(*fixed_args, **kwargs)

        setattr(patched, "__ta_text_repair_wrapped__", True)
        setattr(QMessageBox, method_name, staticmethod(patched))

    wrap_one_text_arg(QLabel, "setText")
    wrap_one_text_arg(QLineEdit, "setPlaceholderText")
    wrap_one_text_arg(QWidget, "setWindowTitle")
    wrap_one_text_arg(QAction, "setText")
    wrap_one_text_arg(QAction, "setStatusTip")
    wrap_one_text_arg(QAction, "setToolTip")
    wrap_one_text_arg(QMenu, "setTitle")
    wrap_one_text_arg(QTableWidgetItem, "setText")
    wrap_index_text_arg(QComboBox, "setItemText")
    wrap_index_text_arg(QTabBar, "setTabText")
    wrap_qmessagebox_method("critical")
    wrap_qmessagebox_method("warning")
    wrap_qmessagebox_method("information")
    wrap_qmessagebox_method("question")


def _repair_existing_ui_texts(root: QWidget) -> None:
    """One-shot normalization for texts set via constructors before patches."""
    widgets: list[QWidget] = [root] + root.findChildren(QWidget)
    for widget in widgets:
        try:
            title = widget.windowTitle()
            fixed_title = _repair_status_text_mojibake(str(title))
            if fixed_title != title:
                widget.setWindowTitle(fixed_title)
        except Exception:
            pass
        try:
            if hasattr(widget, "text") and hasattr(widget, "setText"):
                text = widget.text()
                if isinstance(text, str):
                    fixed_text = _repair_status_text_mojibake(text)
                    if fixed_text != text:
                        widget.setText(fixed_text)
        except Exception:
            pass
        if isinstance(widget, QComboBox):
            try:
                for idx in range(widget.count()):
                    item_text = widget.itemText(idx)
                    fixed_item_text = _repair_status_text_mojibake(item_text)
                    if fixed_item_text != item_text:
                        widget.setItemText(idx, fixed_item_text)
            except Exception:
                pass
        if isinstance(widget, QTabBar):
            try:
                for idx in range(widget.count()):
                    tab_text = widget.tabText(idx)
                    fixed_tab_text = _repair_status_text_mojibake(tab_text)
                    if fixed_tab_text != tab_text:
                        widget.setTabText(idx, fixed_tab_text)
            except Exception:
                pass
        if isinstance(widget, QTableWidget):
            try:
                for col in range(widget.columnCount()):
                    h_item = widget.horizontalHeaderItem(col)
                    if h_item is not None:
                        text = h_item.text()
                        fixed = _repair_status_text_mojibake(text)
                        if fixed != text:
                            h_item.setText(fixed)
            except Exception:
                pass
            try:
                for row in range(widget.rowCount()):
                    for col in range(widget.columnCount()):
                        item = widget.item(row, col)
                        if item is None:
                            continue
                        text = item.text()
                        fixed = _repair_status_text_mojibake(text)
                        if fixed != text:
                            item.setText(fixed)
            except Exception:
                pass

    try:
        menu_bar = root.menuBar() if isinstance(root, QMainWindow) else None
        if menu_bar is not None:
            for action in menu_bar.actions():
                stack = [action]
                while stack:
                    current = stack.pop()
                    try:
                        text = current.text()
                        fixed = _repair_status_text_mojibake(text)
                        if fixed != text:
                            current.setText(fixed)
                    except Exception:
                        pass
                    try:
                        status_tip = current.statusTip()
                        fixed_tip = _repair_status_text_mojibake(status_tip)
                        if fixed_tip != status_tip:
                            current.setStatusTip(fixed_tip)
                    except Exception:
                        pass
                    try:
                        tool_tip = current.toolTip()
                        fixed_tool_tip = _repair_status_text_mojibake(tool_tip)
                        if fixed_tool_tip != tool_tip:
                            current.setToolTip(fixed_tool_tip)
                    except Exception:
                        pass
                    try:
                        sub_menu = current.menu()
                        if sub_menu is not None:
                            title = sub_menu.title()
                            fixed_title = _repair_status_text_mojibake(title)
                            if fixed_title != title:
                                sub_menu.setTitle(fixed_title)
                            stack.extend(sub_menu.actions())
                    except Exception:
                        pass
    except Exception:
        pass


class AutoClearStatusLabel(QLabel):
    def __init__(self, idle_text: str = 'Статус: ожидание', clear_after_ms: int = 5000, parent: QWidget | None = None):
        super().__init__(idle_text, parent)
        self._idle_text = str(idle_text)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(max(500, int(clear_after_ms)))
        self._clear_timer.timeout.connect(self._restore_idle_text)

    def setText(self, text: str) -> None:  # type: ignore[override]
        value = _repair_status_text_mojibake(str(text))
        super().setText(value)
        if value.startswith('Статус:') and value != self._idle_text:
            self._clear_timer.start()
        else:
            self._clear_timer.stop()

    def _restore_idle_text(self) -> None:
        super().setText(self._idle_text)


class SignalValuesTable(QTableWidget):
    def __init__(self, rows: int = 0, columns: int = 0, parent: QWidget | None = None):
        super().__init__(rows, columns, parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDragDropOverwriteMode(False)

    def _selected_signal_ids(self) -> list[str]:
        signal_ids: list[str] = []
        seen: set[str] = set()
        selected_rows = sorted({idx.row() for idx in self.selectedIndexes() if idx.row() >= 0})
        for row in selected_rows:
            checkbox = self.cellWidget(row, 0)
            if not isinstance(checkbox, QCheckBox):
                continue
            signal_id = str(checkbox.property("signal_id") or "").strip()
            if not signal_id or signal_id in seen:
                continue
            seen.add(signal_id)
            signal_ids.append(signal_id)
        return signal_ids

    def startDrag(self, _supportedActions):  # type: ignore[override]
        signal_ids = self._selected_signal_ids()
        if not signal_ids:
            return
        mime = QMimeData()
        mime.setData(SIGNAL_IDS_MIME_TYPE, "\n".join(signal_ids).encode("utf-8"))
        mime.setText(", ".join(signal_ids))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class SignalLibraryTable(QTableWidget):
    def __init__(self, rows: int = 0, columns: int = 0, parent: QWidget | None = None):
        super().__init__(rows, columns, parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDragDropOverwriteMode(False)

    def _selected_signal_ids(self) -> list[str]:
        signal_ids: list[str] = []
        seen: set[str] = set()
        selected_rows = sorted({idx.row() for idx in self.selectedIndexes() if idx.row() >= 0})
        for row in selected_rows:
            signal_id = ""
            name_item = self.item(row, 1)
            if isinstance(name_item, QTableWidgetItem):
                signal_id = str(name_item.data(ROLE_SIGNAL_ID) or "").strip()
            if not signal_id:
                for col in range(self.columnCount()):
                    item = self.item(row, col)
                    if not isinstance(item, QTableWidgetItem):
                        continue
                    signal_id = str(item.data(ROLE_SIGNAL_ID) or "").strip()
                    if signal_id:
                        break
            if not signal_id or signal_id in seen:
                continue
            seen.add(signal_id)
            signal_ids.append(signal_id)
        return signal_ids

    def startDrag(self, _supportedActions):  # type: ignore[override]
        signal_ids = self._selected_signal_ids()
        if not signal_ids:
            return
        mime = QMimeData()
        mime.setData(SIGNAL_IDS_MIME_TYPE, "\n".join(signal_ids).encode("utf-8"))
        mime.setText(", ".join(signal_ids))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        _install_qt_text_repair_patch()
        self._base_window_title = app_title()
        self._last_window_title_text = ""
        self.setWindowTitle(self._base_window_title)
        self.resize(1400, 850)

        # Antialiasing noticeably slows down realtime redraw with many signals.
        pg.setConfigOptions(antialias=False)

        self._updating_ui = False
        self._updating_scales_table = False
        self._updating_values_table = False
        self._updating_tags_table = False
        self._updating_stats_ui = False
        self._scales_table_last_rowcount = -1
        self._stats_table_fitted_once = False
        self._values_header_sort_column: int | None = None
        self._values_header_sort_desc: bool = False
        self._last_values_rows: list[dict] = []
        self._force_close = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._worker: ModbusWorker | None = None
        self._archive_store: ArchiveStore | None = None
        self._db_live_conn: sqlite3.Connection | None = None
        self._db_live_running = False
        self._local_live_enabled = True
        self._db_live_last_sample_row_id = 0
        self._db_live_last_connection_event_row_id = 0
        self._remote_live_cursors: dict[str, dict[str, int]] = {}
        self._remote_live_executor: concurrent.futures.ThreadPoolExecutor | None = concurrent.futures.ThreadPoolExecutor(
            max_workers=8,
            thread_name_prefix="trend-remote-live",
        )
        self._remote_live_futures: dict[str, concurrent.futures.Future] = {}
        self._remote_live_backoff_until_mono: dict[str, float] = {}
        self._remote_live_fail_count: dict[str, int] = {}
        self._remote_last_ok_mono: dict[str, float] = {}
        self._last_live_values: dict[str, tuple[str, float]] = {}
        self._live_cycle_has_new_samples = False
        self._last_ui_heartbeat_ts = 0.0
        self._last_archive_ts = 0.0
        self._last_retention_cleanup_ts = 0.0
        self._archive_last_values: dict[str, float] = {}
        self._archive_last_written_ts: dict[str, float] = {}
        self._signal_types_by_id: dict[str, str] = {}
        self._connection_events: list[list[float]] = []
        self._last_connection_state: bool | None = None
        self._stopping_worker = False
        self._tags_poll_timer = QTimer(self)
        self._tags_poll_timer.setSingleShot(False)
        self._tags_poll_timer.timeout.connect(self._on_tags_poll_timer)
        self._tags_tabs: list[TagTabConfig] = []
        self._active_tags_tab_index = -1
        self._updating_tags_tabs = False
        self._updating_signal_source_tabs = False
        self._active_signal_source_id = "local"
        self._config_dirty = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1200)
        self._autosave_timer.timeout.connect(self._autosave_config_if_dirty)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(False)
        self._render_timer.timeout.connect(self._flush_pending_render_samples)
        self._db_live_timer = QTimer(self)
        self._db_live_timer.setSingleShot(False)
        self._db_live_timer.setInterval(300)
        self._db_live_timer.timeout.connect(self._poll_db_live_stream)
        self._history_view_timer = QTimer(self)
        self._history_view_timer.setSingleShot(True)
        self._history_view_timer.setInterval(220)
        self._history_view_timer.timeout.connect(self._reload_visible_history_from_db)
        self._pending_render_samples: list[tuple[float, dict[str, tuple[str, float]]]] = []
        # Backpressure cap for UI queue to avoid high RAM usage when UI redraw
        # cannot keep up with very fast polling / many signals.
        self._max_pending_render_batches = 1200
        self._pending_history_x_range: tuple[float, float] | None = None
        self._history_loaded_range: tuple[float, float] | None = None
        self._history_loaded_bucket_s: float | None = None
        self._history_reload_guard = False
        self._last_applied_work_mode: str | None = None
        self._runtime_connected = False
        self._cpu_count = max(1, int(os.cpu_count() or 1))
        self._last_proc_cpu_time = time.process_time()
        self._last_wall_cpu_time = time.monotonic()
        self._process_handle = ctypes.windll.kernel32.GetCurrentProcess() if sys.platform == "win32" else None
        self._last_mem_bytes: int | None = None
        self._psutil_process = None
        if psutil is not None:
            try:
                self._psutil_process = psutil.Process(os.getpid())
                self._psutil_process.cpu_percent(interval=None)
            except Exception:
                self._psutil_process = None
        self._runtime_stats_timer = QTimer(self)
        self._runtime_stats_timer.setSingleShot(False)
        self._runtime_stats_timer.setInterval(1000)
        self._runtime_stats_timer.timeout.connect(self._update_runtime_status_panel)
        self._runtime_stats_timer.timeout.connect(self._update_recorder_dependent_ui_state)
        self._recorder_controls_enabled: bool | None = None
        self._busy_depth = 0
        self._busy_cursor_active = False

        self.config_store = ConfigStore()
        self.app_config = self.config_store.load()
        self.current_profile = self._find_profile(self.app_config.active_profile_id)

        self._build_ui()
        _repair_existing_ui_texts(self)
        self._populate_profiles()
        self._load_profile_to_ui(self.current_profile)
        QTimer.singleShot(0, self._auto_connect_startup_if_needed)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        self._build_menu_bar()

        # Internal mode controls (state carrier); user interacts via menu.
        self.mode_combo = QComboBox()
        self.mode_combo.addItem('Онлайн', "online")
        self.mode_combo.addItem('Офлайн', "offline")
        self.archive_to_db_checkbox = QCheckBox('Писать в БД')
        self.archive_to_db_checkbox.setChecked(True)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)

        self.status_label = AutoClearStatusLabel(
            'Статус: ожидание',
            5000,
            self,
        )

        self.chart = MultiAxisChart()
        self._values_collapsed = False
        self._values_closed = False

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.setHandleWidth(5)
        self.main_splitter.splitterMoved.connect(lambda _pos, _index: self._mark_config_dirty())
        self.main_splitter.setStyleSheet(
            "QSplitter::handle:vertical {"
            "background-color: rgba(150, 170, 190, 45);"
            "border-top: 1px solid rgba(180, 200, 220, 55);"
            "border-bottom: 1px solid rgba(0, 0, 0, 80);"
            "}"
            "QSplitter::handle:vertical:hover {"
            "background-color: rgba(170, 210, 245, 80);"
            "}"
        )
        main_layout.addWidget(self.main_splitter, 1)

        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.addWidget(self.chart)
        self.main_splitter.addWidget(chart_panel)

        self.values_panel = QWidget()
        values_layout = QVBoxLayout(self.values_panel)
        values_layout.setContentsMargins(0, 0, 0, 0)
        values_layout.setSpacing(0)

        values_header = QWidget()
        values_header_layout = QHBoxLayout(values_header)
        values_header_layout.setContentsMargins(6, 0, 6, 0)
        values_header_layout.setSpacing(3)
        self.values_auto_x_checkbox = QCheckBox('Авто X')
        self.values_auto_x_checkbox.setChecked(True)
        self.values_auto_x_checkbox.toggled.connect(self._on_values_auto_x_toggled)
        values_header_layout.addWidget(self.values_auto_x_checkbox)
        self.values_cursor_checkbox = QCheckBox('Курсор')
        self.values_cursor_checkbox.setChecked(False)
        self.values_cursor_checkbox.toggled.connect(self._on_values_cursor_toggled)
        values_header_layout.addWidget(self.values_cursor_checkbox)
        self.values_reset_zoom_btn = QPushButton('Сброс масштаба')
        self.values_reset_zoom_btn.clicked.connect(lambda _checked=False: self.chart.reset_view())
        values_header_layout.addWidget(self.values_reset_zoom_btn)
        self.values_statistics_btn = QPushButton('Статистика')
        self.values_statistics_btn.clicked.connect(self._show_statistics_window)
        values_header_layout.addWidget(self.values_statistics_btn)
        values_header_layout.addStretch(1)

        self.values_collapse_btn = QToolButton()
        self.values_collapse_btn.setIcon(self._make_panel_control_icon("chevron_down"))
        self.values_collapse_btn.setIconSize(QSize(14, 14))
        self.values_collapse_btn.setFixedSize(24, 22)
        self.values_collapse_btn.setStyleSheet(
            "QToolButton {"
            "background-color: rgba(33, 37, 43, 160);"
            "border: 1px solid rgba(130, 140, 155, 150);"
            "border-radius: 6px;"
            "padding: 0px;"
            "}"
            "QToolButton:hover {"
            "background-color: rgba(58, 65, 76, 200);"
            "border-color: rgba(160, 170, 186, 190);"
            "}"
            "QToolButton:pressed {"
            "background-color: rgba(24, 28, 34, 220);"
            "}"
        )
        self.values_collapse_btn.setToolTip('Свернуть/развернуть')
        self.values_collapse_btn.clicked.connect(self._toggle_values_panel)
        values_header_layout.addWidget(self.values_collapse_btn)

        self.values_expand_btn = QToolButton()
        self.values_expand_btn.setIcon(self._make_panel_control_icon("expand"))
        self.values_expand_btn.setIconSize(QSize(14, 14))
        self.values_expand_btn.setFixedSize(24, 22)
        self.values_expand_btn.setStyleSheet(self.values_collapse_btn.styleSheet())
        self.values_expand_btn.setToolTip('Развернуть панель')
        self.values_expand_btn.clicked.connect(self._expand_values_panel)
        values_header_layout.addWidget(self.values_expand_btn)

        self.values_close_btn = QToolButton()
        self.values_close_btn.setIcon(self._make_panel_control_icon("close"))
        self.values_close_btn.setIconSize(QSize(14, 14))
        self.values_close_btn.setFixedSize(24, 22)
        self.values_close_btn.setStyleSheet(self.values_collapse_btn.styleSheet())
        self.values_close_btn.setToolTip('Скрыть таблицу')
        self.values_close_btn.clicked.connect(self._close_values_panel)
        values_header_layout.addWidget(self.values_close_btn)

        values_layout.addWidget(values_header)

        self.values_table = SignalValuesTable(0, 7)
        self.values_table.setHorizontalHeaderLabels(['Вид', 'Сигнал', 'Шкала', 'Значение', 'Время', 'Источник', 'Цвет'])
        header = self.values_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(self.values_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_values_header_clicked)
        header.sectionResized.connect(lambda *_args: self._on_table_column_resized("values_table"))
        self.values_table.verticalHeader().setVisible(False)
        self.values_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.values_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.values_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.values_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.values_table.customContextMenuRequested.connect(self._on_values_table_context_menu)
        self.values_table.setColumnWidth(4, 170)
        self.values_table.setColumnWidth(5, 120)
        values_layout.addWidget(self.values_table, 1)
        self.main_splitter.addWidget(self.values_panel)
        self.main_splitter.setStretchFactor(0, 6)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([780, 180])

        self.chart.auto_mode_changed.connect(self._on_chart_auto_mode_changed)
        self.chart.cursor_enabled_changed.connect(self._on_chart_cursor_enabled_changed)
        self.chart.stats_range_changed.connect(self._on_chart_stats_range_changed)
        self.chart.x_range_changed.connect(self._on_chart_x_range_changed)
        self.chart.display_updated.connect(self._update_values_table)
        self.chart.scales_changed.connect(self._update_scales_table)
        self.chart.signals_dropped.connect(self._on_chart_signals_dropped)
        self.chart.export_image_requested.connect(self._export_chart_image)
        self.chart.export_csv_requested.connect(self._export_chart_csv)
        self.chart.print_requested.connect(self._print_chart)

        self._build_connection_window()
        self._build_signals_window()
        self._build_sources_window()
        self._build_tags_window()
        self._build_scales_window()
        self._build_graph_settings_window()
        self._build_statistics_window()

        self._init_tray()
        self._sync_mode_actions()
        self._sync_close_behavior_actions()
        self._sync_startup_actions()
        self._apply_windows_autostart(silent=True)
        status_bar = self.statusBar()
        status_bar.setSizeGripEnabled(False)
        status_bar.setContentsMargins(0, 0, 0, 4)
        status_bar.setStyleSheet("QStatusBar::item { border: none; }")
        self._status_left_spacer = QLabel("")
        self._status_left_spacer.setFixedWidth(10)
        status_bar.addWidget(self._status_left_spacer, 0)
        status_bar.addWidget(self.status_label, 1)
        self.status_label.setContentsMargins(0, 0, 0, 0)
        self.busy_progress = QProgressBar()
        self.busy_progress.setTextVisible(False)
        self.busy_progress.setRange(0, 0)
        self.busy_progress.setFixedWidth(110)
        self.busy_progress.setMaximumHeight(10)
        self.busy_progress.setVisible(False)
        self.busy_progress.setStyleSheet(
            "QProgressBar {"
            "background-color: rgba(90, 90, 90, 80);"
            "border: 1px solid rgba(160, 160, 160, 90);"
            "border-radius: 5px;"
            "}"
            "QProgressBar::chunk {"
            "background-color: #59a5f5;"
            "border-radius: 5px;"
            "}"
        )
        status_bar.addPermanentWidget(self.busy_progress, 0)
        self.runtime_indicator_label = QLabel('●')
        self.runtime_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.runtime_indicator_label.setStyleSheet("font-size: 12px; color: #ef5350;")
        self.runtime_indicator_label.setFixedWidth(14)
        status_bar.addPermanentWidget(self.runtime_indicator_label, 0)
        self.runtime_status_label = QLabel("")
        self.runtime_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.runtime_status_label.setStyleSheet("font-size: 11px; color: #9aa0a6;")
        self.runtime_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.runtime_status_label.setContentsMargins(0, 0, 0, 0)
        self.runtime_status_label.setMinimumWidth(420)
        status_bar.addPermanentWidget(self.runtime_status_label, 0)
        self._status_right_spacer = QLabel("")
        self._status_right_spacer.setFixedWidth(10)
        status_bar.addPermanentWidget(self._status_right_spacer, 0)
        self._update_runtime_status_panel()
        self._runtime_stats_timer.start()
        self._update_recorder_dependent_ui_state()

    def _set_busy_state(self, busy: bool, status_text: str | None = None) -> None:
        if busy:
            self._busy_depth += 1
            if status_text:
                self.status_label.setText(str(status_text))
            if self._busy_depth == 1:
                if hasattr(self, "busy_progress"):
                    self.busy_progress.setRange(0, 0)
                    self.busy_progress.setVisible(True)
                if not self._busy_cursor_active:
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    self._busy_cursor_active = True
            QApplication.processEvents()
            return

        if self._busy_depth > 0:
            self._busy_depth -= 1
        if self._busy_depth == 0:
            if hasattr(self, "busy_progress"):
                self.busy_progress.setVisible(False)
            if self._busy_cursor_active:
                try:
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass
                self._busy_cursor_active = False
            QApplication.processEvents()

    @contextmanager
    def _busy(self, status_text: str | None = None):
        self._set_busy_state(True, status_text)
        try:
            yield
        finally:
            self._set_busy_state(False)

    def _make_panel_control_icon(self, kind: str, size: int = 14) -> QIcon:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#e6edf7"))
        pen.setWidthF(1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if kind == "chevron_up":
            painter.drawLine(4, 9, 7, 6)
            painter.drawLine(7, 6, 10, 9)
        elif kind == "chevron_down":
            painter.drawLine(4, 5, 7, 8)
            painter.drawLine(7, 8, 10, 5)
        elif kind == "expand":
            painter.drawRect(3, 3, max(1, size - 7), max(1, size - 7))
        else:  # close
            painter.drawLine(4, 4, size - 5, size - 5)
            painter.drawLine(size - 5, 4, 4, size - 5)

        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _format_bytes_human(size_bytes: int | float) -> str:
        value = max(0.0, float(size_bytes))
        units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
        idx = 0
        while value >= 1024.0 and idx < len(units) - 1:
            value /= 1024.0
            idx += 1
        if idx == 0:
            return f"{int(value)} {units[idx]}"
        return f"{value:.2f} {units[idx]}"

    def _estimate_cpu_percent(self) -> float:
        if self._psutil_process is not None:
            try:
                cpu = float(self._psutil_process.cpu_percent(interval=None))
                return max(0.0, cpu / float(self._cpu_count))
            except Exception:
                pass

        now_proc = time.process_time()
        now_wall = time.monotonic()
        delta_proc = max(0.0, now_proc - self._last_proc_cpu_time)
        delta_wall = max(1e-6, now_wall - self._last_wall_cpu_time)
        self._last_proc_cpu_time = now_proc
        self._last_wall_cpu_time = now_wall
        return max(0.0, min(999.0, (delta_proc / delta_wall) * 100.0 / float(self._cpu_count)))

    def _get_memory_usage_bytes(self) -> int | None:
        if self._psutil_process is not None:
            try:
                return int(self._psutil_process.memory_info().rss)
            except Exception:
                pass

        if sys.platform != "win32" or self._process_handle is None:
            return None
        try:
            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_uint32),
                    ("PageFaultCount", ctypes.c_uint32),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("psapi")
            get_mem = psapi.GetProcessMemoryInfo
            get_mem.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            get_mem.restype = ctypes.c_int

            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
            ok = get_mem(
                self._process_handle,
                ctypes.byref(counters),
                counters.cb,
            )
            if ok:
                return int(counters.WorkingSetSize)
        except Exception:
            return None
        return None

    def _archive_file_size_bytes(self) -> int:
        db_path = Path(str(self.current_profile.db_path or str(DEFAULT_DB_PATH)))
        try:
            if db_path.exists():
                return int(db_path.stat().st_size)
        except Exception:
            return 0
        return 0

    def _remote_live_ttl_s(self) -> float:
        try:
            return max(2.0, float(self._db_live_timer.interval()) / 1000.0 * 4.0)
        except Exception:
            return 2.0

    def _build_connection_title_suffix(self) -> str:
        mode = str(getattr(self.current_profile, "work_mode", "online") or "online")
        if mode != "online":
            return "режим: офлайн"

        live_running = self._is_live_stream_running()
        parts: list[str] = []
        if not live_running:
            parts.append("режим: онлайн (ожидание старта)")

        if bool(self._local_live_enabled):
            if live_running:
                pid = resolve_recorder_pid()
                if pid is not None:
                    parts.append(f"локальный recorder PID {int(pid)}")
                else:
                    parts.append("локальный recorder: нет связи")
            else:
                parts.append("локальный recorder: настроен")

        mapping = self._remote_signal_mapping()
        if mapping:
            by_id = {
                str(source.id): source
                for source in getattr(self.current_profile, "recorder_sources", [])
                if bool(getattr(source, "enabled", False))
            }
            source_ids = [sid for sid in mapping.keys() if sid in by_id]
            if source_ids:
                if live_running:
                    now_mono = time.monotonic()
                    ttl_s = self._remote_live_ttl_s()
                    connected_count = 0
                    for source_id in source_ids:
                        last_ok = self._remote_last_ok_mono.get(str(source_id))
                        if last_ok is not None and (now_mono - float(last_ok)) <= ttl_s:
                            connected_count += 1
                    if len(source_ids) == 1:
                        source = by_id[source_ids[0]]
                        state_text = "online" if connected_count == 1 else "offline"
                        parts.append(f"удалённый {source.name} ({source.host}:{source.port}, {state_text})")
                    else:
                        parts.append(f"удалённые recorder: {connected_count}/{len(source_ids)} online")
                else:
                    if len(source_ids) == 1:
                        source = by_id[source_ids[0]]
                        parts.append(f"удалённый {source.name} ({source.host}:{source.port}, настроен)")
                    else:
                        parts.append(f"удалённые recorder: настроено {len(source_ids)}")

        if not parts:
            return "источник: не определён"
        return " | ".join(parts)

    def _update_window_title_connection(self) -> None:
        suffix = self._build_connection_title_suffix()
        title = self._base_window_title if not suffix else f"{suffix} — {self._base_window_title}"
        if title != self._last_window_title_text:
            self._last_window_title_text = title
            self.setWindowTitle(title)

    def _update_runtime_status_panel(self) -> None:
        try:
            connected = bool(self._runtime_connected and self._is_live_stream_running())
            indicator_color = "#70d26f" if connected else "#ef5350"
            indicator_text = 'подключено' if connected else 'не подключено'
            cpu_percent = self._estimate_cpu_percent()
            mem_bytes = self._get_memory_usage_bytes()
            if mem_bytes is not None:
                self._last_mem_bytes = mem_bytes
            elif self._last_mem_bytes is not None:
                mem_bytes = self._last_mem_bytes
            archive_bytes = self._archive_file_size_bytes()
            mem_text = self._format_bytes_human(mem_bytes) if mem_bytes is not None else "-"
            archive_text = self._format_bytes_human(archive_bytes)
            mode = str(getattr(self.current_profile, "work_mode", "online") or "online")
            write_enabled = bool(getattr(self.current_profile, "archive_to_db", False)) and mode == "online"
            if not write_enabled:
                archive_mode_text = 'архив выкл'
            elif bool(getattr(self.current_profile, "archive_on_change_only", False)):
                archive_mode_text = 'архив: изменения'
            else:
                archive_mode_text = 'архив: все точки'
            render_mode_text = (
                'график: вкл' if bool(getattr(self.current_profile, "render_chart_enabled", True)) else 'график: выкл'
            )
            if hasattr(self, "runtime_indicator_label"):
                self.runtime_indicator_label.setStyleSheet(f"font-size: 12px; color: {indicator_color};")
            if hasattr(self, "runtime_status_label"):
                self.runtime_status_label.setText(
                    f"{indicator_text}, CPU {cpu_percent:.1f}%, RAM {mem_text}, Архив {archive_text}, {archive_mode_text}, {render_mode_text}"
                )
        except Exception:
            if hasattr(self, "runtime_indicator_label"):
                self.runtime_indicator_label.setStyleSheet("font-size: 12px; color: #ef5350;")
            if hasattr(self, "runtime_status_label"):
                self.runtime_status_label.setText('не подключено, CPU -, RAM -, Архив -, график -')
        self._update_window_title_connection()

    def _on_archive_filter_settings_changed(self, *_args) -> None:
        if self._updating_ui:
            return
        self.current_profile.archive_on_change_only = bool(self.archive_on_change_checkbox.isChecked())
        self.current_profile.archive_deadband = max(0.0, float(self.archive_deadband_spin.value()))
        self.current_profile.archive_keepalive_s = max(0, int(self.archive_keepalive_spin.value()))
        self._update_runtime_status_panel()
        self._mark_config_dirty()

    def _build_connection_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.connection_window = QDialog(self, flags)
        self.connection_window.setWindowTitle('Настройки подключения')
        self.connection_window.resize(520, 520)
        self.connection_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.connection_window)

        top_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        top_row.addWidget(QLabel('Профиль'))
        top_row.addWidget(self.profile_combo, 1)
        layout.addLayout(top_row)

        button_row = QHBoxLayout()
        self.new_profile_btn = QPushButton('Новый')
        self.clone_profile_btn = QPushButton('Клон')
        self.delete_profile_btn = QPushButton('Удалить')
        self.save_profiles_btn = QPushButton('Сохранить')
        button_row.addWidget(self.new_profile_btn)
        button_row.addWidget(self.clone_profile_btn)
        button_row.addWidget(self.delete_profile_btn)
        button_row.addWidget(self.save_profiles_btn)
        layout.addLayout(button_row)

        self.new_profile_btn.clicked.connect(self._new_profile)
        self.clone_profile_btn.clicked.connect(self._clone_profile)
        self.delete_profile_btn.clicked.connect(self._delete_profile)
        self.save_profiles_btn.clicked.connect(self._save_config)

        form = QFormLayout()
        self.profile_name_edit = QLineEdit()
        self.ip_edit = QLineEdit()

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.unit_id_spin = QSpinBox()
        self.unit_id_spin.setRange(1, 255)
        self.poll_interval_spin = QSpinBox()
        self.poll_interval_spin.setRange(50, 60000)
        self.poll_interval_spin.setSuffix(" ms")
        self.render_interval_spin = QSpinBox()
        self.render_interval_spin.setRange(50, 5000)
        self.render_interval_spin.setSingleStep(50)
        self.render_interval_spin.setSuffix(" ms")
        self.render_interval_spin.valueChanged.connect(self._on_render_interval_changed)
        self.render_chart_checkbox = QCheckBox('Включена')
        self.render_chart_checkbox.setChecked(True)
        self.render_chart_checkbox.toggled.connect(self._on_render_chart_toggled)
        self.archive_interval_spin = QSpinBox()
        self.archive_interval_spin.setRange(50, 600000)
        self.archive_interval_spin.setSuffix(" ms")
        self.archive_on_change_checkbox = QCheckBox('Только при изменении')
        self.archive_on_change_checkbox.toggled.connect(self._on_archive_filter_settings_changed)
        self.archive_deadband_spin = QDoubleSpinBox()
        self.archive_deadband_spin.setRange(0.0, 1_000_000_000.0)
        self.archive_deadband_spin.setDecimals(6)
        self.archive_deadband_spin.setSingleStep(0.001)
        self.archive_deadband_spin.valueChanged.connect(self._on_archive_filter_settings_changed)
        self.archive_keepalive_spin = QSpinBox()
        self.archive_keepalive_spin.setRange(0, 86400)
        self.archive_keepalive_spin.setSuffix(" c")
        self.archive_keepalive_spin.setSpecialValueText('Отключен')
        self.archive_keepalive_spin.valueChanged.connect(self._on_archive_filter_settings_changed)
        self.archive_retention_days_spin = QSpinBox()
        self.archive_retention_days_spin.setRange(0, 3650)
        self.archive_retention_days_spin.setSuffix(' дн')
        self.archive_retention_days_spin.setSpecialValueText('Без ограничения')
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30.0)
        self.timeout_spin.setSingleStep(0.1)
        self.timeout_spin.setSuffix(" s")
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.address_offset_spin = QSpinBox()
        self.address_offset_spin.setRange(-10, 10)
        self.recorder_api_enabled_checkbox = QCheckBox('Включено')
        self.recorder_api_enabled_checkbox.setChecked(True)
        self.recorder_api_host_edit = QLineEdit()
        self.recorder_api_host_edit.setText("0.0.0.0")
        self.recorder_api_port_spin = QSpinBox()
        self.recorder_api_port_spin.setRange(1, 65535)
        self.recorder_api_port_spin.setValue(18777)
        self.recorder_api_token_edit = QLineEdit()
        self.recorder_api_token_edit.setPlaceholderText('необязательно')

        form.addRow('Имя профиля', self.profile_name_edit)
        form.addRow("IP", self.ip_edit)
        form.addRow('Порт', self.port_spin)
        form.addRow('ID устройства (Unit ID)', self.unit_id_spin)
        form.addRow('Частота опроса', self.poll_interval_spin)
        form.addRow('Интервал отрисовки', self.render_interval_spin)
        form.addRow('Отрисовка графика', self.render_chart_checkbox)
        form.addRow('Частота архивации', self.archive_interval_spin)
        form.addRow('Архив: только изменения', self.archive_on_change_checkbox)
        form.addRow('Архив: deadband', self.archive_deadband_spin)
        form.addRow('Архив: keepalive', self.archive_keepalive_spin)
        form.addRow('Глубина архива', self.archive_retention_days_spin)
        form.addRow('Таймаут', self.timeout_spin)
        form.addRow('Повторы', self.retries_spin)
        form.addRow('Смещение адреса', self.address_offset_spin)
        form.addRow('API регистратора', self.recorder_api_enabled_checkbox)
        form.addRow("API host", self.recorder_api_host_edit)
        form.addRow("API port", self.recorder_api_port_spin)
        form.addRow("API token", self.recorder_api_token_edit)
        layout.addLayout(form)

        self.apply_btn = QPushButton('Применить')
        self.apply_btn.clicked.connect(self._apply_current_profile)
        self.clear_archive_db_btn = QPushButton('Очистить архив БД')
        self.clear_archive_db_btn.clicked.connect(self._on_clear_archive_db_clicked)
        action_row = QHBoxLayout()
        action_row.addWidget(self.apply_btn)
        action_row.addWidget(self.clear_archive_db_btn)
        layout.addLayout(action_row)

    def _build_signals_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.signals_window = QDialog(self, flags)
        self.signals_window.setWindowTitle('Сигналы графика')
        self.signals_window.resize(800, 520)
        self.signals_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.signals_window)

        bulk_row = QHBoxLayout()
        self.signals_start_addr_spin = QSpinBox()
        self.signals_start_addr_spin.setRange(0, 65535)
        self.signals_count_spin = QSpinBox()
        self.signals_count_spin.setRange(1, 1000)
        self.signals_count_spin.setValue(10)
        self.signals_step_spin = QSpinBox()
        self.signals_step_spin.setRange(1, 1000)
        self.signals_step_spin.setValue(1)
        self.signals_reg_combo = QComboBox()
        self.signals_reg_combo.addItem('Holding (хран.)', "holding")
        self.signals_reg_combo.addItem('Input (вход.)', "input")
        self.signals_type_combo = QComboBox()
        self.signals_type_combo.addItem('INT16 (знак.)', "int16")
        self.signals_type_combo.addItem('UINT16 (без знака)', "uint16")
        self.signals_type_combo.addItem("REAL / FLOAT32", "float32")
        self.signals_type_combo.addItem('BOOL (бит)', "bool")
        self.signals_float_order_combo = QComboBox()
        self.signals_float_order_combo.addItem("ABCD", "ABCD")
        self.signals_float_order_combo.addItem("BADC", "BADC")
        self.signals_float_order_combo.addItem("CDAB", "CDAB")
        self.signals_float_order_combo.addItem("DCBA", "DCBA")
        self.signals_type_combo.currentIndexChanged.connect(self._on_signals_bulk_type_changed)
        self.signals_axis_spin = QSpinBox()
        self.signals_axis_spin.setRange(1, 64)
        self.signals_axis_spin.setValue(1)
        self.signals_add_range_btn = QPushButton('Добавить диапазон')
        self.signals_add_range_btn.clicked.connect(self._on_add_signal_range_clicked)

        bulk_row.addWidget(QLabel('Старт'))
        bulk_row.addWidget(self.signals_start_addr_spin)
        bulk_row.addWidget(QLabel('Кол-во'))
        bulk_row.addWidget(self.signals_count_spin)
        bulk_row.addWidget(QLabel('Шаг'))
        bulk_row.addWidget(self.signals_step_spin)
        bulk_row.addWidget(self.signals_reg_combo)
        bulk_row.addWidget(self.signals_type_combo)
        bulk_row.addWidget(self.signals_float_order_combo)
        bulk_row.addWidget(QLabel('Шкала'))
        bulk_row.addWidget(self.signals_axis_spin)
        bulk_row.addWidget(self.signals_add_range_btn)
        bulk_row.addStretch(1)
        layout.addLayout(bulk_row)
        self._on_signals_bulk_type_changed(self.signals_type_combo.currentIndex())

        self.signal_source_tabs = QTabBar(self.signals_window)
        self.signal_source_tabs.setExpanding(False)
        self.signal_source_tabs.setDrawBase(False)
        self.signal_source_tabs.currentChanged.connect(self._on_signal_source_tab_changed)
        layout.addWidget(self.signal_source_tabs)

        self.signal_table = SignalLibraryTable(0, 11, self.signals_window)
        self.signal_table.setHorizontalHeaderLabels(
            ['Вкл', 'Имя', 'Адрес', 'Регистр', 'Тип', 'Бит', 'Шкала', 'Порядок REAL', 'Коэфф.', 'Цвет', 'Источник']
        )
        self.signal_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.signal_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        signal_header = self.signal_table.horizontalHeader()
        signal_header.setStretchLastSection(False)
        signal_header.sectionResized.connect(lambda *_args: self._on_table_column_resized("signal_table"))
        layout.addWidget(self.signal_table, 1)

        signal_buttons = QHBoxLayout()
        self.add_signal_btn = QPushButton('+ Сигнал')
        self.copy_signal_btn = QPushButton('Копировать')
        self.remove_signal_btn = QPushButton('- Сигнал')
        self.clear_signals_btn = QPushButton('Удалить все')
        self.save_signals_config_btn = QPushButton('Сохранить конфигурацию')
        self.signal_columns_btn = QPushButton('Колонки...')
        signal_buttons.addWidget(self.add_signal_btn)
        signal_buttons.addWidget(self.copy_signal_btn)
        signal_buttons.addWidget(self.remove_signal_btn)
        signal_buttons.addWidget(self.clear_signals_btn)
        signal_buttons.addWidget(self.signal_columns_btn)
        signal_buttons.addStretch(1)
        signal_buttons.addWidget(self.save_signals_config_btn)
        layout.addLayout(signal_buttons)

        self.add_signal_btn.clicked.connect(self._on_add_signal_clicked)
        self.copy_signal_btn.clicked.connect(self._on_copy_signal_clicked)
        self.remove_signal_btn.clicked.connect(self._on_remove_signal_rows_clicked)
        self.clear_signals_btn.clicked.connect(self._on_clear_signals_clicked)
        self.signal_columns_btn.clicked.connect(self._show_signal_columns_menu)
        self.save_signals_config_btn.clicked.connect(self._save_from_signals_window)
        self.signal_table.itemChanged.connect(self._on_signal_table_item_changed)

        self._signal_column_actions: dict[int, QAction] = {}

    def _build_sources_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.sources_window = QDialog(self, flags)
        self.sources_window.setWindowTitle('Источники данных (регистраторы)')
        self.sources_window.resize(900, 460)
        self.sources_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.sources_window)

        scan_row = QHBoxLayout()
        self.sources_subnet_edit = QLineEdit()
        self.sources_subnet_edit.setPlaceholderText('Подсеть, например 192.168.1.0/24')
        self.sources_subnet_edit.setText(self._guess_local_subnet_for_scan())
        self.sources_scan_port_spin = QSpinBox()
        self.sources_scan_port_spin.setRange(1, 65535)
        self.sources_scan_port_spin.setValue(18777)
        self.sources_scan_btn = QPushButton('Сканировать сеть')
        self.sources_scan_btn.clicked.connect(self._on_scan_sources_clicked)
        scan_row.addWidget(QLabel('Подсеть'))
        scan_row.addWidget(self.sources_subnet_edit, 1)
        scan_row.addWidget(QLabel('Порт API'))
        scan_row.addWidget(self.sources_scan_port_spin)
        scan_row.addWidget(self.sources_scan_btn)
        layout.addLayout(scan_row)

        self.sources_table = QTableWidget(0, 7)
        self.sources_table.setHorizontalHeaderLabels(['Вкл', 'Имя', "Host", 'Порт', "Token", "Recorder ID", 'Статус'])
        header = self.sources_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(self.sources_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(lambda *_args: self._on_table_column_resized("sources_table"))
        self.sources_table.verticalHeader().setVisible(False)
        self.sources_table.itemChanged.connect(self._on_sources_table_item_changed)
        layout.addWidget(self.sources_table, 1)

        btn_row = QHBoxLayout()
        self.sources_add_btn = QPushButton('+ Источник')
        self.sources_add_btn.clicked.connect(self._on_add_source_clicked)
        self.sources_remove_btn = QPushButton('- Источник')
        self.sources_remove_btn.clicked.connect(self._on_remove_source_clicked)
        self.sources_connect_btn = QPushButton('Подключить выбранный')
        self.sources_connect_btn.clicked.connect(self._on_connect_source_clicked)
        self.sources_refresh_btn = QPushButton('Проверить статус')
        self.sources_refresh_btn.clicked.connect(self._on_refresh_sources_status_clicked)
        self.sources_import_selected_tags_btn = QPushButton('Импортировать выбранные теги...')
        self.sources_import_selected_tags_btn.clicked.connect(self._on_import_selected_source_tags_clicked)
        self.sources_import_tags_btn = QPushButton('Импортировать все теги')
        self.sources_import_tags_btn.clicked.connect(self._on_import_source_tags_clicked)
        self.sources_apply_profile_btn = QPushButton('Применить профиль на источник')
        self.sources_apply_profile_btn.clicked.connect(self._on_apply_profile_to_source_clicked)
        btn_row.addWidget(self.sources_add_btn)
        btn_row.addWidget(self.sources_remove_btn)
        btn_row.addWidget(self.sources_connect_btn)
        btn_row.addWidget(self.sources_refresh_btn)
        btn_row.addWidget(self.sources_import_selected_tags_btn)
        btn_row.addWidget(self.sources_import_tags_btn)
        btn_row.addWidget(self.sources_apply_profile_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def _guess_local_subnet_for_scan(self) -> str:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
            finally:
                sock.close()
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            return str(net)
        except Exception:
            return "192.168.1.0/24"

    def _add_source_row(self, source: RecorderSourceConfig | None = None, status_text: str = "-") -> None:
        if not isinstance(source, RecorderSourceConfig):
            source = RecorderSourceConfig(name=f"Recorder {self.sources_table.rowCount() + 1}")
        row = self.sources_table.rowCount()
        self.sources_table.insertRow(row)

        enabled_item = QTableWidgetItem()
        enabled_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        enabled_item.setCheckState(Qt.CheckState.Checked if source.enabled else Qt.CheckState.Unchecked)
        self.sources_table.setItem(row, 0, enabled_item)

        name_item = QTableWidgetItem(str(source.name))
        name_item.setData(Qt.ItemDataRole.UserRole, str(source.id))
        self.sources_table.setItem(row, 1, name_item)
        self.sources_table.setItem(row, 2, QTableWidgetItem(str(source.host)))
        self.sources_table.setItem(row, 3, QTableWidgetItem(str(int(source.port))))
        self.sources_table.setItem(row, 4, QTableWidgetItem(str(source.token)))
        self.sources_table.setItem(row, 5, QTableWidgetItem(str(source.recorder_id)))
        status_item = QTableWidgetItem(str(status_text or "-"))
        status_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.sources_table.setItem(row, 6, status_item)

    def _fill_sources_table(self, sources: list[RecorderSourceConfig]) -> None:
        self.sources_table.blockSignals(True)
        try:
            self.sources_table.setRowCount(0)
            for source in sources:
                self._add_source_row(source, status_text="-")
        finally:
            self.sources_table.blockSignals(False)
        self.sources_table.resizeColumnsToContents()
        for col in range(self.sources_table.columnCount()):
            width = self.sources_table.columnWidth(col)
            self.sources_table.setColumnWidth(col, min(max(56, width + 12), 360))
        self._refresh_tags_sources_combo()
        self._refresh_signal_source_tabs()
        self._refresh_signal_source_column_labels()

    def _on_sources_table_item_changed(self, _item: QTableWidgetItem) -> None:
        if self._updating_ui:
            return
        if _item is not None and int(_item.column()) == 6:
            return
        self._refresh_tags_sources_combo()
        self._refresh_signal_source_tabs()
        self._refresh_signal_source_column_labels()
        self._mark_config_dirty()

    def _refresh_tags_sources_combo(self) -> None:
        if not hasattr(self, "tags_source_combo"):
            return
        selected = str(self.tags_source_combo.currentData() or "local")
        self.tags_source_combo.blockSignals(True)
        self.tags_source_combo.clear()
        self.tags_source_combo.addItem('Локальный (прямой Modbus)', "local")
        for source in self._collect_sources_table():
            if not source.enabled:
                continue
            label = f"{source.name} ({source.host}:{source.port})"
            self.tags_source_combo.addItem(label, str(source.id))
        idx = self.tags_source_combo.findData(selected)
        self.tags_source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.tags_source_combo.blockSignals(False)

    def _source_by_id(self, source_id: str) -> RecorderSourceConfig | None:
        sid = str(source_id or "").strip()
        for source in self._collect_sources_table():
            if str(source.id) == sid:
                return source
        return None

    def _source_label_for_source_id(
        self,
        source_id: str,
        source_map: dict[str, RecorderSourceConfig] | None = None,
    ) -> str:
        sid = str(source_id or "local").strip() or "local"
        if sid == "local":
            profile = getattr(self, "current_profile", None)
            host = str(getattr(profile, "ip", "127.0.0.1") or "127.0.0.1")
            try:
                port = int(getattr(profile, "port", 502) or 502)
            except Exception:
                port = 502
            return f"{host}:{port}"
        if source_map and sid in source_map:
            source = source_map[sid]
            return f"{source.host}:{source.port}"
        source = self._source_by_id(sid)
        if source is None:
            for item in getattr(self.current_profile, "recorder_sources", []):
                if str(getattr(item, "id", "")) == sid:
                    source = item
                    break
        if source is None:
            return f"Удаленный recorder ({sid[:8]})"
        return f"{source.host}:{source.port}"

    def _get_signal_source_tab_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = [("local", "Локальный")]
        seen: set[str] = {"local"}
        for source in self._collect_sources_table():
            sid = str(source.id or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            items.append((sid, f"{source.host}:{source.port}"))
        return items

    @staticmethod
    def _normalize_profile_signal_sources(profile: ProfileConfig) -> int:
        valid_sources = {str(item.id or "").strip() for item in getattr(profile, "recorder_sources", []) if str(item.id or "").strip()}
        changed = 0
        for signal in getattr(profile, "signals", []):
            sid = str(getattr(signal, "source_id", "local") or "local").strip() or "local"
            remote_tag_id = str(getattr(signal, "remote_tag_id", "") or "").strip()

            if sid != "local" and sid not in valid_sources:
                sid = "local"
                remote_tag_id = ""

            if sid == "local":
                if remote_tag_id:
                    remote_tag_id = ""
            else:
                if not remote_tag_id:
                    remote_tag_id = str(getattr(signal, "id", "") or "").strip()

            if str(getattr(signal, "source_id", "local") or "local") != sid:
                signal.source_id = sid
                changed += 1
            if str(getattr(signal, "remote_tag_id", "") or "") != remote_tag_id:
                signal.remote_tag_id = remote_tag_id
                changed += 1
        return changed

    def _current_signal_source_id(self) -> str:
        if hasattr(self, "signal_source_tabs") and self.signal_source_tabs.count() > 0:
            idx = int(self.signal_source_tabs.currentIndex())
            if idx >= 0:
                sid = str(self.signal_source_tabs.tabData(idx) or "local")
                if sid:
                    return sid
        return "local"

    def _refresh_signal_source_tabs(self) -> None:
        if not hasattr(self, "signal_source_tabs"):
            return
        current_sid = self._active_signal_source_id or self._current_signal_source_id()
        items = self._get_signal_source_tab_items()
        self._updating_signal_source_tabs = True
        try:
            self.signal_source_tabs.blockSignals(True)
            while self.signal_source_tabs.count() > 0:
                self.signal_source_tabs.removeTab(self.signal_source_tabs.count() - 1)
            target_index = 0
            for idx, (sid, title) in enumerate(items):
                self.signal_source_tabs.addTab(title)
                self.signal_source_tabs.setTabData(idx, sid)
                if sid == current_sid:
                    target_index = idx
            if self.signal_source_tabs.count() > 0:
                self.signal_source_tabs.setCurrentIndex(target_index)
                self._active_signal_source_id = str(self.signal_source_tabs.tabData(target_index) or "local")
            else:
                self._active_signal_source_id = "local"
        finally:
            self.signal_source_tabs.blockSignals(False)
            self._updating_signal_source_tabs = False

    def _store_signal_table_to_profile(
        self,
        profile: ProfileConfig,
        ensure_signal_minimum: bool = False,
        source_id: str | None = None,
    ) -> None:
        selected_source_id = str(source_id or self._current_signal_source_id() or "local").strip() or "local"
        only_local_minimum = bool(ensure_signal_minimum and selected_source_id == "local")
        selected_signals = self._collect_signal_table(ensure_signal_minimum=only_local_minimum)
        kept = [
            signal
            for signal in profile.signals
            if str(getattr(signal, "source_id", "local") or "local") != selected_source_id
        ]
        profile.signals = kept + selected_signals

    def _on_signal_source_tab_changed(self, index: int) -> None:
        if self._updating_ui or self._updating_signal_source_tabs:
            return
        if index < 0:
            return
        previous_source_id = str(self._active_signal_source_id or "local").strip() or "local"
        self._store_signal_table_to_profile(self.current_profile, source_id=previous_source_id)
        source_id = str(self.signal_source_tabs.tabData(index) or "local")
        self._active_signal_source_id = source_id
        self._fill_signal_table(self.current_profile.signals, source_id=source_id)

    def _refresh_signal_source_column_labels(self) -> None:
        if not hasattr(self, "signal_table"):
            return
        sources_by_id = {str(item.id): item for item in self._collect_sources_table()}
        for row in range(self.signal_table.rowCount()):
            name_item = self.signal_table.item(row, 1)
            if name_item is None:
                continue
            source_id = str(name_item.data(ROLE_SIGNAL_SOURCE_ID) or "local")
            label = self._source_label_for_source_id(source_id, sources_by_id)
            source_item = self.signal_table.item(row, 10)
            if source_item is None:
                source_item = QTableWidgetItem()
                source_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self.signal_table.setItem(row, 10, source_item)
            if source_item.text() != label:
                source_item.setText(label)

    def _selected_tags_source(self) -> RecorderSourceConfig | None:
        if not hasattr(self, "tags_source_combo"):
            return None
        source_id = str(self.tags_source_combo.currentData() or "local")
        if source_id == "local":
            return None
        return self._source_by_id(source_id)

    def _collect_sources_table(self) -> list[RecorderSourceConfig]:
        rows: list[RecorderSourceConfig] = []
        seen_ids: set[str] = set()
        for row in range(self.sources_table.rowCount()):
            enabled_item = self.sources_table.item(row, 0)
            name_item = self.sources_table.item(row, 1)
            host_item = self.sources_table.item(row, 2)
            port_item = self.sources_table.item(row, 3)
            token_item = self.sources_table.item(row, 4)
            recorder_id_item = self.sources_table.item(row, 5)
            source_id = str(name_item.data(Qt.ItemDataRole.UserRole) or uuid.uuid4()) if name_item else str(uuid.uuid4())
            if source_id in seen_ids:
                source_id = str(uuid.uuid4())
                if name_item is not None:
                    name_item.setData(Qt.ItemDataRole.UserRole, source_id)
            seen_ids.add(source_id)
            name = (name_item.text().strip() if name_item else "") or f"Recorder {row + 1}"
            host = (host_item.text().strip() if host_item else "") or "127.0.0.1"
            try:
                port = int(port_item.text().strip() if port_item else "18777")
            except ValueError:
                port = 18777
            token = token_item.text().strip() if token_item else ""
            recorder_id = recorder_id_item.text().strip() if recorder_id_item else ""
            enabled = bool(enabled_item and enabled_item.checkState() == Qt.CheckState.Checked)
            rows.append(
                RecorderSourceConfig(
                    id=source_id,
                    name=name,
                    host=host,
                    port=max(1, min(65535, int(port))),
                    token=token,
                    enabled=enabled,
                    recorder_id=recorder_id,
                )
            )
        return rows

    @staticmethod
    def _source_base_url(source: RecorderSourceConfig) -> str:
        return f"http://{source.host}:{int(source.port)}"

    def _source_request_json(
        self,
        source: RecorderSourceConfig,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict[str, object] | None = None,
        timeout_s: float = 1.2,
    ) -> dict:
        base = self._source_base_url(source)
        url = f"{base}{path}"
        if query:
            q = urlparse.urlencode({k: v for k, v in query.items() if v is not None})
            if q:
                url = f"{url}?{q}"
        body = b""
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        token = str(source.token or "").strip()
        if token:
            headers["X-Recorder-Token"] = token
        request = urlrequest.Request(url=url, data=body if method.upper() != "GET" else None, method=method.upper())
        for k, v in headers.items():
            request.add_header(k, v)
        with urlrequest.urlopen(request, timeout=max(0.2, float(timeout_s))) as response:
            raw = response.read()
        payload_obj = json.loads(raw.decode("utf-8"))
        if not isinstance(payload_obj, dict):
            raise RuntimeError("invalid_response")
        return payload_obj

    def _probe_source_health(self, source: RecorderSourceConfig, timeout_s: float = 0.5) -> tuple[bool, dict]:
        try:
            payload = self._source_request_json(source, "GET", "/v1/health", timeout_s=timeout_s)
        except urlerror.HTTPError as exc:
            return False, {"error": f"HTTP {exc.code}"}
        except Exception as exc:
            return False, {"error": str(exc)}
        ok = bool(payload.get("ok", False))
        return ok, payload

    def _selected_source_rows(self) -> list[int]:
        rows = sorted({idx.row() for idx in self.sources_table.selectedIndexes() if idx.row() >= 0})
        if rows:
            return rows
        current_row = int(self.sources_table.currentRow())
        return [current_row] if current_row >= 0 else []

    def _source_from_row(self, row: int, sources: list[RecorderSourceConfig]) -> RecorderSourceConfig | None:
        if row < 0 or row >= len(sources):
            return None
        return sources[row]

    def _set_source_status_cell(self, row: int, text: str) -> None:
        status_item = self.sources_table.item(row, 6)
        if status_item is None:
            status_item = QTableWidgetItem("-")
            status_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.sources_table.setItem(row, 6, status_item)
        status_item.setText(str(text or "-"))

    def _connect_source_row(
        self,
        row: int,
        source: RecorderSourceConfig,
        timeout_s: float = 1.0,
        ensure_enabled: bool = True,
    ) -> tuple[bool, str]:
        ok, payload = self._probe_source_health(source, timeout_s=timeout_s)
        if not ok:
            error_text = str(payload.get("error", "n/a"))
            self._set_source_status_cell(row, f"Ошибка: {error_text}")
            return False, error_text

        profile_name = str(payload.get("profile_name") or source.name)
        recorder_id = str(payload.get("recorder_id") or "")
        if recorder_id:
            rec_item = self.sources_table.item(row, 5)
            if rec_item is None:
                rec_item = QTableWidgetItem(recorder_id)
                self.sources_table.setItem(row, 5, rec_item)
            else:
                rec_item.setText(recorder_id)

        if ensure_enabled:
            enabled_item = self.sources_table.item(row, 0)
            if enabled_item is None:
                enabled_item = QTableWidgetItem()
                enabled_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                self.sources_table.setItem(row, 0, enabled_item)
            enabled_item.setCheckState(Qt.CheckState.Checked)

        self._set_source_status_cell(row, f"Подключен: {profile_name}")
        self._refresh_tags_sources_combo()
        source_id = str(source.id)
        idx = self.tags_source_combo.findData(source_id) if hasattr(self, "tags_source_combo") else -1
        if idx >= 0:
            self.tags_source_combo.setCurrentIndex(idx)
        return True, profile_name

    def _fetch_source_tags(self, source: RecorderSourceConfig, timeout_s: float = 2.0) -> list[dict]:
        payload = self._source_request_json(source, "GET", "/v1/tags", timeout_s=timeout_s)
        if not bool(payload.get("ok", False)):
            raise RuntimeError(str(payload.get("error") or "tags_failed"))
        raw_tags = payload.get("tags")
        if not isinstance(raw_tags, list):
            return []
        return [item for item in raw_tags if isinstance(item, dict)]

    def _import_source_tags_into_signals(
        self,
        source: RecorderSourceConfig,
        tag_items: list[dict],
        existing_keys: set[tuple[str, str]],
    ) -> int:
        added = 0
        for item in tag_items:
            remote_tag_id = str(item.get("id") or "").strip()
            if not remote_tag_id:
                continue
            key = (str(source.id), remote_tag_id)
            if key in existing_keys:
                continue
            signal_name = str(item.get("name") or remote_tag_id)
            signal = SignalConfig(
                id=str(uuid.uuid4()),
                name=f"{source.name}: {signal_name}",
                address=int(item.get("address") or 0),
                register_type=str(item.get("register_type") or "holding"),
                data_type=str(item.get("data_type") or "int16"),
                bit_index=max(0, min(15, int(item.get("bit_index") or 0))),
                axis_index=max(1, int(item.get("axis_index") or 1)),
                float_order=str(item.get("float_order") or "ABCD"),
                scale=float(item.get("scale") or 1.0),
                color=str(item.get("color") or DEFAULT_COLORS[(len(self.current_profile.signals) + added) % len(DEFAULT_COLORS)]),
                enabled=False,
                source_id=str(source.id),
                remote_tag_id=remote_tag_id,
            )
            self.current_profile.signals.append(signal)
            existing_keys.add(key)
            added += 1
        return added

    def _pick_tags_for_import_dialog(
        self,
        source: RecorderSourceConfig,
        tags: list[dict],
        already_imported_ids: set[str],
    ) -> list[dict] | None:
        tags_sorted = sorted(
            tags,
            key=lambda item: (
                int(item.get("address") or 0),
                str(item.get("name") or ""),
                str(item.get("id") or ""),
            ),
        )

        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        dialog = QDialog(self, flags)
        dialog.setWindowTitle(f"Выбор тегов: {source.name} ({source.host}:{source.port})")
        dialog.resize(900, 560)
        dialog.setSizeGripEnabled(True)
        layout = QVBoxLayout(dialog)

        summary_label = QLabel(
            f"Найдено тегов: {len(tags_sorted)} | Уже импортировано: {len(already_imported_ids)}. "
            "Отметьте нужные теги и нажмите Импорт. Уже импортированные будут пропущены."
        )
        layout.addWidget(summary_label)

        table = QTableWidget(0, 7, dialog)
        table.setHorizontalHeaderLabels(["Импорт", "Имя", "Адрес", "Регистр", "Тип", "Бит", "ID"])
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(False)

        for item in tags_sorted:
            remote_tag_id = str(item.get("id") or "").strip()
            if not remote_tag_id:
                continue
            row = table.rowCount()
            table.insertRow(row)

            checked_item = QTableWidgetItem()
            checked_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            checked_item.setCheckState(Qt.CheckState.Unchecked)
            checked_item.setData(Qt.ItemDataRole.UserRole, dict(item))
            table.setItem(row, 0, checked_item)

            table.setItem(row, 1, QTableWidgetItem(str(item.get("name") or remote_tag_id)))
            table.setItem(row, 2, QTableWidgetItem(str(int(item.get("address") or 0))))
            table.setItem(row, 3, QTableWidgetItem(str(item.get("register_type") or "holding")))
            table.setItem(row, 4, QTableWidgetItem(str(item.get("data_type") or "int16")))
            table.setItem(row, 5, QTableWidgetItem(str(int(item.get("bit_index") or 0))))
            table.setItem(row, 6, QTableWidgetItem(remote_tag_id))

        table.resizeColumnsToContents()
        table.setColumnWidth(1, min(420, max(180, table.columnWidth(1) + 24)))
        table.setColumnWidth(6, min(340, max(160, table.columnWidth(6) + 20)))
        layout.addWidget(table, 1)

        controls_row = QHBoxLayout()
        select_all_btn = QPushButton("Отметить все")
        clear_all_btn = QPushButton("Снять все")
        controls_row.addWidget(select_all_btn)
        controls_row.addWidget(clear_all_btn)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        def _set_checks(state: Qt.CheckState) -> None:
            for row in range(table.rowCount()):
                cell = table.item(row, 0)
                if cell is None:
                    continue
                if bool(cell.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    cell.setCheckState(state)

        select_all_btn.clicked.connect(lambda _checked=False: _set_checks(Qt.CheckState.Checked))
        clear_all_btn.clicked.connect(lambda _checked=False: _set_checks(Qt.CheckState.Unchecked))

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Импорт")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return None

        selected_items: list[dict] = []
        for row in range(table.rowCount()):
            cell = table.item(row, 0)
            if cell is None:
                continue
            if cell.checkState() != Qt.CheckState.Checked:
                continue
            payload = cell.data(Qt.ItemDataRole.UserRole)
            if isinstance(payload, dict):
                selected_items.append(payload)
        return selected_items

    def _on_add_source_clicked(self, _checked: bool = False) -> None:
        self._store_signal_table_to_profile(self.current_profile)
        self.sources_table.blockSignals(True)
        try:
            self._add_source_row()
        finally:
            self.sources_table.blockSignals(False)
        self.sources_table.resizeColumnsToContents()
        self._refresh_tags_sources_combo()
        self._refresh_signal_source_tabs()
        self._refresh_signal_source_column_labels()
        self._mark_config_dirty()

    def _on_remove_source_clicked(self, _checked: bool = False) -> None:
        self._store_signal_table_to_profile(self.current_profile)
        selected = sorted(self._selected_source_rows(), reverse=True)
        for row in selected:
            self.sources_table.removeRow(row)
        self.current_profile.recorder_sources = self._collect_sources_table()
        self._normalize_profile_signal_sources(self.current_profile)
        self._refresh_tags_sources_combo()
        self._refresh_signal_source_tabs()
        self._refresh_signal_source_column_labels()
        self._fill_signal_table(self.current_profile.signals, source_id=self._current_signal_source_id())
        self._mark_config_dirty()

    def _on_connect_source_clicked(self, _checked: bool = False) -> None:
        selected_rows = self._selected_source_rows()
        if not selected_rows:
            self.status_label.setText('Статус: выберите источник в таблице')
            return
        sources = self._collect_sources_table()
        ok_count = 0
        with self._busy('Статус: подключение к выбранным источникам...'):
            for row in selected_rows:
                source = self._source_from_row(row, sources)
                if source is None:
                    continue
                ok, _message = self._connect_source_row(row, source, timeout_s=1.2, ensure_enabled=True)
                if ok:
                    ok_count += 1
        if ok_count > 0:
            self.status_label.setText(f"Статус: подключено источников: {ok_count}")
        else:
            self.status_label.setText('Ошибка: не удалось подключиться к выбранным источникам')
        self._mark_config_dirty()

    def _on_refresh_sources_status_clicked(self, _checked: bool = False) -> None:
        sources = self._collect_sources_table()
        ok_count = 0
        with self._busy('Статус: проверка источников...'):
            for row, source in enumerate(sources):
                ok, _message = self._connect_source_row(row, source, timeout_s=0.8, ensure_enabled=False)
                if ok:
                    ok_count += 1
        self.status_label.setText(f"Статус: проверка источников завершена (OK {ok_count}/{len(sources)})")
        self._mark_config_dirty()

    def _scan_subnet_for_sources(self, subnet_text: str, port: int) -> list[RecorderSourceConfig]:
        try:
            network = ipaddress.ip_network(str(subnet_text).strip(), strict=False)
        except Exception:
            raise RuntimeError('Неверный формат подсети. Пример: 192.168.1.0/24')
        port = max(1, min(65535, int(port)))
        discovered: list[RecorderSourceConfig] = []

        hosts = [str(ip) for ip in network.hosts()]
        if not hosts:
            return discovered
        if len(hosts) > 4096:
            raise RuntimeError('Слишком большая подсеть для быстрого сканирования. Используйте, например, /24')

        def probe(host: str) -> RecorderSourceConfig | None:
            source = RecorderSourceConfig(name=host, host=host, port=port, enabled=True)
            ok, payload = self._probe_source_health(source, timeout_s=0.35)
            if not ok:
                return None
            source.name = str(payload.get("profile_name") or host)
            source.recorder_id = str(payload.get("recorder_id") or "")
            return source

        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
            futures = [executor.submit(probe, host) for host in hosts]
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    result = None
                if isinstance(result, RecorderSourceConfig):
                    discovered.append(result)
        discovered.sort(key=lambda item: (item.host, int(item.port)))
        return discovered

    def _on_scan_sources_clicked(self, _checked: bool = False) -> None:
        subnet = self.sources_subnet_edit.text().strip() or self._guess_local_subnet_for_scan()
        port = int(self.sources_scan_port_spin.value())
        try:
            with self._busy(f"Статус: сканирование подсети {subnet}..."):
                found = self._scan_subnet_for_sources(subnet, port)

                existing = self._collect_sources_table()
                by_key = {(item.host, int(item.port)): item for item in existing}
                added = 0
                for source in found:
                    key = (source.host, int(source.port))
                    if key in by_key:
                        existing_item = by_key[key]
                        if source.recorder_id:
                            existing_item.recorder_id = source.recorder_id
                        if source.name and existing_item.name.startswith("Recorder"):
                            existing_item.name = source.name
                    else:
                        existing.append(source)
                        by_key[key] = source
                        added += 1
                self._fill_sources_table(existing)
        except Exception as exc:
            self.status_label.setText(f"Ошибка сканирования: {exc}")
            return

        self.status_label.setText(f"Статус: сканирование завершено, найдено {len(found)}, добавлено {added}")
        self._mark_config_dirty()

    def _on_import_source_tags_clicked(self, _checked: bool = False) -> None:
        selected_rows = self._selected_source_rows()
        if not selected_rows:
            self.status_label.setText('Статус: выберите источник(и) в таблице')
            return
        sources = self._collect_sources_table()
        self._store_signal_table_to_profile(self.current_profile)
        signals = list(self.current_profile.signals)
        existing_keys = {(str(sig.source_id), str(sig.remote_tag_id)) for sig in signals if str(sig.source_id) != "local"}
        added = 0
        with self._busy('Статус: импорт тегов из источников...'):
            for row in selected_rows:
                source = self._source_from_row(row, sources)
                if source is None:
                    continue
                connected_ok, _message = self._connect_source_row(row, source, timeout_s=1.2, ensure_enabled=True)
                if not connected_ok:
                    continue
                try:
                    raw_tags = self._fetch_source_tags(source, timeout_s=2.5)
                except Exception as exc:
                    self.status_label.setText(f"Ошибка чтения тегов из {source.host}:{source.port}: {exc}")
                    continue
                added += self._import_source_tags_into_signals(source, raw_tags, existing_keys)

        if added > 0:
            self._refresh_signal_source_tabs()
            self._fill_signal_table(self.current_profile.signals, source_id=self._current_signal_source_id())
            self._fit_signal_table_columns(initial=False)
            self._apply_current_profile()
            self.status_label.setText(
                f"Статус: импортировано тегов: {added}. Они в таблице; перетащите нужные строки на график."
            )
        else:
            self.status_label.setText('Статус: новых тегов для импорта не найдено')

    def _on_import_selected_source_tags_clicked(self, _checked: bool = False) -> None:
        selected_rows = self._selected_source_rows()
        if not selected_rows:
            self.status_label.setText('Статус: выберите источник в таблице')
            return
        if len(selected_rows) != 1:
            self.status_label.setText('Статус: выберите один источник для выборочного импорта')
            return
        row = int(selected_rows[0])
        sources = self._collect_sources_table()
        source = self._source_from_row(row, sources)
        if source is None:
            self.status_label.setText('Статус: выбранный источник не найден')
            return

        connected_ok = False
        raw_tags: list[dict] = []
        try:
            with self._busy(f"Статус: чтение тегов из {source.host}:{source.port}..."):
                connected_ok, _message = self._connect_source_row(row, source, timeout_s=1.2, ensure_enabled=True)
                if connected_ok:
                    raw_tags = self._fetch_source_tags(source, timeout_s=3.0)
        except Exception as exc:
            self.status_label.setText(f"Ошибка чтения тегов из {source.host}:{source.port}: {exc}")
            return
        if not connected_ok:
            self.status_label.setText(f"Ошибка: источник {source.host}:{source.port} недоступен")
            return
        if not raw_tags:
            self.status_label.setText(f"Статус: у источника {source.name} нет тегов для импорта")
            return

        self._store_signal_table_to_profile(self.current_profile)
        signals = list(self.current_profile.signals)
        existing_keys = {(str(sig.source_id), str(sig.remote_tag_id)) for sig in signals if str(sig.source_id) != "local"}
        existing_ids_for_source = {remote_id for src_id, remote_id in existing_keys if src_id == str(source.id)}
        selected_tags = self._pick_tags_for_import_dialog(source, raw_tags, existing_ids_for_source)
        if selected_tags is None:
            self.status_label.setText('Статус: импорт тегов отменен')
            return
        if not selected_tags:
            self.status_label.setText('Статус: не выбраны теги для импорта')
            return

        with self._busy('Статус: импорт выбранных тегов...'):
            added = self._import_source_tags_into_signals(source, selected_tags, existing_keys)
            if added > 0:
                self._refresh_signal_source_tabs()
                self._fill_signal_table(self.current_profile.signals, source_id=self._current_signal_source_id())
                self._fit_signal_table_columns(initial=False)
                self._apply_current_profile()
        if added > 0:
            self.status_label.setText(
                f"Статус: импортировано выбранных тегов: {added}. Они в таблице; перетащите нужные строки на график."
            )
        else:
            self.status_label.setText('Статус: выбранные теги уже были импортированы')

    def _build_remote_profile_payload_for_source(self, source_id: str) -> dict:
        source_sid = str(source_id or "").strip()
        profile_payload = self.current_profile.to_dict()
        all_signals = list(self.current_profile.signals)
        source_signals = [s for s in all_signals if str(getattr(s, "source_id", "local") or "local") == source_sid]
        local_signals = [s for s in all_signals if str(getattr(s, "source_id", "local") or "local") == "local"]
        selected_signals = source_signals if source_signals else local_signals

        normalized: list[dict] = []
        for signal in selected_signals:
            payload = signal.to_dict()
            payload["source_id"] = "local"
            payload["remote_tag_id"] = ""
            normalized.append(payload)

        profile_payload["signals"] = normalized
        profile_payload["recorder_sources"] = []
        profile_payload["work_mode"] = "online"
        profile_payload["ui_state"] = {}
        return profile_payload

    def _apply_profile_to_source_runtime(
        self,
        source: RecorderSourceConfig,
        row: int | None = None,
        timeout_s: float = 2.5,
        update_status: bool = True,
    ) -> tuple[bool, str]:
        payload_profile = self._build_remote_profile_payload_for_source(source.id)
        try:
            response = self._source_request_json(
                source,
                "PUT",
                "/v1/config",
                payload={"profile": payload_profile},
                timeout_s=float(max(0.5, timeout_s)),
            )
        except Exception as exc:
            return False, f"apply_failed: {exc}"

        if not bool(response.get("ok", False)):
            message = str(response.get("message") or response.get("error") or "n/a")
            return False, f"apply_failed: {message}"

        source_sid = str(source.id or "").strip()
        bindings_updated = 0
        for signal in self.current_profile.signals:
            sid = str(getattr(signal, "source_id", "local") or "local").strip() or "local"
            if sid != source_sid:
                continue
            remote_tag_id = str(getattr(signal, "remote_tag_id", "") or "").strip()
            if remote_tag_id:
                continue
            signal.remote_tag_id = str(getattr(signal, "id", "") or "").strip()
            bindings_updated += 1

        if bindings_updated > 0:
            if self._current_signal_source_id() == source_sid:
                self._fill_signal_table(self.current_profile.signals, source_id=source_sid)
                self._fit_signal_table_columns(initial=False)
            self._mark_config_dirty()

        if row is None:
            sources_rows = self._collect_sources_table()
            for idx, item in enumerate(sources_rows):
                if str(getattr(item, "id", "") or "") == source_sid:
                    row = idx
                    break
        if row is not None and row >= 0:
            try:
                self._connect_source_row(int(row), source, timeout_s=1.2, ensure_enabled=True)
            except Exception:
                pass

        message = (
            f"Профиль отправлен на {source.name} ({source.host}:{source.port}), "
            f"сигналов: {len(payload_profile.get('signals') or [])}"
        )
        if update_status:
            self.status_label.setText(f"Статус: {message}")
        return True, message

    def _auto_sync_remote_profiles_for_live(self) -> tuple[int, int]:
        source_ids_with_signals = {
            str(getattr(signal, "source_id", "local") or "local").strip()
            for signal in list(getattr(self.current_profile, "signals", []) or [])
            if str(getattr(signal, "source_id", "local") or "local").strip() not in {"", "local"}
        }
        if not source_ids_with_signals:
            return 0, 0

        source_rows = self._collect_sources_table()
        row_by_source_id = {str(item.id): idx for idx, item in enumerate(source_rows)}
        sources_by_id = {
            str(source.id): source
            for source in list(getattr(self.current_profile, "recorder_sources", []) or [])
            if bool(getattr(source, "enabled", False))
        }

        applied = 0
        failed = 0
        for source_id in sorted(source_ids_with_signals):
            source = sources_by_id.get(source_id)
            if source is None:
                continue

            source_signals = [
                signal
                for signal in self.current_profile.signals
                if str(getattr(signal, "source_id", "local") or "local").strip() == source_id
            ]
            if not source_signals:
                continue

            need_apply = False
            try:
                tags_payload = self._source_request_json(
                    source,
                    "GET",
                    "/v1/tags",
                    timeout_s=max(0.6, self._remote_live_timeout_s()),
                )
                if bool(tags_payload.get("ok", False)):
                    tags_raw = tags_payload.get("tags")
                    tag_ids = set()
                    if isinstance(tags_raw, list):
                        for item in tags_raw:
                            if not isinstance(item, dict):
                                continue
                            rid = str(item.get("id") or "").strip()
                            if rid:
                                tag_ids.add(rid)
                    matched = 0
                    for signal in source_signals:
                        remote_tag_id = str(getattr(signal, "remote_tag_id", "") or "").strip()
                        if remote_tag_id and remote_tag_id in tag_ids:
                            matched += 1
                    # If none of configured signal bindings exist on source,
                    # source profile is likely stale for this viewer config.
                    if matched <= 0:
                        need_apply = True
                else:
                    need_apply = True
            except Exception:
                # Unreachable source: do not force apply, let regular live polling
                # and reconnect logic handle the source.
                continue

            if not need_apply:
                continue

            ok, _msg = self._apply_profile_to_source_runtime(
                source,
                row=row_by_source_id.get(source_id),
                timeout_s=max(1.2, self._remote_live_timeout_s() * 3.0),
                update_status=False,
            )
            if ok:
                applied += 1
            else:
                failed += 1

        return applied, failed

    def _on_apply_profile_to_source_clicked(self, _checked: bool = False) -> None:
        self._store_signal_table_to_profile(self.current_profile)
        selected_rows = sorted({idx.row() for idx in self.sources_table.selectedIndexes()})
        if not selected_rows:
            self.status_label.setText('Статус: выберите источник в таблице')
            return
        row = int(selected_rows[0])
        sources = self._collect_sources_table()
        if row < 0 or row >= len(sources):
            self.status_label.setText('Статус: выбранный источник не найден')
            return
        source = sources[row]
        try:
            with self._busy(f"Статус: отправка профиля на {source.host}:{source.port}..."):
                ok, message = self._apply_profile_to_source_runtime(
                    source,
                    row=row,
                    timeout_s=2.5,
                    update_status=False,
                )
        except Exception as exc:
            self.status_label.setText(
                f"Ошибка: не удалось применить профиль на {source.host}:{source.port}: {exc}"
            )
            return
        if ok:
            self.status_label.setText(f"Статус: {message}")
        else:
            self.status_label.setText(
                f"Ошибка применения профиля на {source.name}: {message}"
            )

    def _build_tags_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.tags_window = QDialog(self, flags)
        self.tags_window.setWindowTitle('Регистры Modbus')
        self.tags_window.resize(980, 620)
        self.tags_window.setSizeGripEnabled(True)
        self.tags_window.finished.connect(lambda _code: self._stop_tags_polling(silent=True))
        layout = QVBoxLayout(self.tags_window)

        tabs_row = QHBoxLayout()
        tabs_row.addWidget(QLabel('Вкладки'))
        self.tags_tabbar = QTabBar()
        self.tags_tabbar.setMovable(True)
        self.tags_tabbar.setExpanding(False)
        self.tags_tabbar.setTabsClosable(True)
        self.tags_tabbar.currentChanged.connect(self._on_tags_tab_changed)
        self.tags_tabbar.tabMoved.connect(self._on_tags_tab_moved)
        self.tags_tabbar.tabCloseRequested.connect(self._on_tags_tab_close_requested)
        tabs_row.addWidget(self.tags_tabbar, 1)
        self.tags_add_tab_btn = QPushButton('+ Вкладка')
        self.tags_add_tab_btn.clicked.connect(self._on_add_tags_tab_clicked)
        tabs_row.addWidget(self.tags_add_tab_btn)
        self.tags_rename_tab_btn = QPushButton('Переименовать')
        self.tags_rename_tab_btn.clicked.connect(self._on_rename_tags_tab_clicked)
        tabs_row.addWidget(self.tags_rename_tab_btn)
        layout.addLayout(tabs_row)

        bulk_row = QHBoxLayout()
        self.tags_source_combo = QComboBox()
        self.tags_source_combo.addItem('Локальный (прямой Modbus)', "local")
        bulk_row.addWidget(QLabel('Источник'))
        bulk_row.addWidget(self.tags_source_combo)
        self.tags_start_addr_spin = QSpinBox()
        self.tags_start_addr_spin.setRange(0, 65535)
        self.tags_count_spin = QSpinBox()
        self.tags_count_spin.setRange(1, 1000)
        self.tags_step_spin = QSpinBox()
        self.tags_step_spin.setRange(1, 1000)
        self.tags_reg_combo = QComboBox()
        self.tags_reg_combo.addItem('Holding (хран.)', "holding")
        self.tags_reg_combo.addItem('Input (вход.)', "input")
        self.tags_type_combo = QComboBox()
        self.tags_type_combo.addItem('INT16 (знак.)', "int16")
        self.tags_type_combo.addItem('UINT16 (без знака)', "uint16")
        self.tags_type_combo.addItem("REAL / FLOAT32", "float32")
        self.tags_type_combo.addItem('BOOL (бит)', "bool")
        self.tags_float_order_combo = QComboBox()
        self.tags_float_order_combo.addItem('ABCD (обычный)', "ABCD")
        self.tags_float_order_combo.addItem("BADC (swap bytes)", "BADC")
        self.tags_float_order_combo.addItem("CDAB (swap words)", "CDAB")
        self.tags_float_order_combo.addItem("DCBA (reverse)", "DCBA")
        self.tags_add_range_btn = QPushButton('Добавить диапазон')
        self.tags_add_range_btn.clicked.connect(self._on_add_tag_range_clicked)

        bulk_row.addWidget(QLabel('Старт'))
        bulk_row.addWidget(self.tags_start_addr_spin)
        bulk_row.addWidget(QLabel('Кол-во'))
        bulk_row.addWidget(self.tags_count_spin)
        bulk_row.addWidget(QLabel('Шаг'))
        bulk_row.addWidget(self.tags_step_spin)
        bulk_row.addWidget(self.tags_reg_combo)
        bulk_row.addWidget(self.tags_type_combo)
        bulk_row.addWidget(self.tags_float_order_combo)
        bulk_row.addWidget(self.tags_add_range_btn)
        bulk_row.addStretch(1)
        layout.addLayout(bulk_row)

        poll_row = QHBoxLayout()
        self.tags_poll_interval_spin = QSpinBox()
        self.tags_poll_interval_spin.setRange(100, 600000)
        self.tags_poll_interval_spin.setSingleStep(100)
        self.tags_poll_interval_spin.setSuffix(" ms")
        self.tags_poll_start_btn = QPushButton('Старт')
        self.tags_poll_stop_btn = QPushButton('Стоп')
        self.tags_poll_start_btn.clicked.connect(self._start_tags_polling)
        self.tags_poll_stop_btn.clicked.connect(self._stop_tags_polling)
        poll_row.addWidget(QLabel('Интервал чтения'))
        poll_row.addWidget(self.tags_poll_interval_spin)
        poll_row.addWidget(self.tags_poll_start_btn)
        poll_row.addWidget(self.tags_poll_stop_btn)
        poll_row.addStretch(1)
        layout.addLayout(poll_row)

        self.tags_table = QTableWidget(0, 10)
        self.tags_table.setHorizontalHeaderLabels(
            ['Чтение', 'Имя', 'Адрес', 'Регистр', 'Тип', 'Бит', 'Порядок REAL', 'Значение', 'Запись', 'Статус']
        )
        header = self.tags_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(self.tags_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(lambda *_args: self._on_table_column_resized("tags_table"))
        self.tags_table.verticalHeader().setVisible(False)
        layout.addWidget(self.tags_table, 1)

        buttons_row = QHBoxLayout()
        self.add_tag_btn = QPushButton('+ Тег')
        self.remove_tag_btn = QPushButton('- Тег')
        self.clear_tags_btn = QPushButton('Удалить все')
        self.read_tags_btn = QPushButton('Прочитать отмеченные')
        self.write_tags_btn = QPushButton('Записать отмеченные')
        self.save_tags_config_btn = QPushButton('Сохранить конфигурацию')

        self.add_tag_btn.clicked.connect(self._on_add_tag_clicked)
        self.remove_tag_btn.clicked.connect(self._on_remove_tag_rows_clicked)
        self.clear_tags_btn.clicked.connect(self._on_clear_tags_clicked)
        self.read_tags_btn.clicked.connect(self._on_read_tags_clicked)
        self.write_tags_btn.clicked.connect(self._on_write_tags_clicked)
        self.save_tags_config_btn.clicked.connect(self._save_from_tags_window)
        # Do not trigger actions on Enter while editing cells/spinboxes in this dialog.
        for button in (
            self.tags_add_tab_btn,
            self.tags_rename_tab_btn,
            self.tags_add_range_btn,
            self.tags_poll_start_btn,
            self.tags_poll_stop_btn,
            self.add_tag_btn,
            self.remove_tag_btn,
            self.clear_tags_btn,
            self.read_tags_btn,
            self.write_tags_btn,
            self.save_tags_config_btn,
        ):
            button.setAutoDefault(False)
            button.setDefault(False)

        buttons_row.addWidget(self.add_tag_btn)
        buttons_row.addWidget(self.remove_tag_btn)
        buttons_row.addWidget(self.clear_tags_btn)
        buttons_row.addWidget(self.read_tags_btn)
        buttons_row.addWidget(self.write_tags_btn)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.save_tags_config_btn)
        layout.addLayout(buttons_row)
        self._sync_tags_poll_buttons()

    @staticmethod
    def _table_row_for_widget(table: QTableWidget, widget: QWidget, column: int) -> int:
        for row in range(table.rowCount()):
            if table.cellWidget(row, column) is widget:
                return row
        return -1

    @staticmethod
    def _clone_tag_list(items: list[TagConfig]) -> list[TagConfig]:
        return [TagConfig.from_dict(item.to_dict()) for item in items]

    def _capture_active_tags_tab(self) -> None:
        if self._active_tags_tab_index < 0 or self._active_tags_tab_index >= len(self._tags_tabs):
            return
        self._tags_tabs[self._active_tags_tab_index].tags = self._collect_tags_table()

    def _refresh_tags_tabbar(self, target_index: int = 0) -> None:
        self._updating_tags_tabs = True
        while self.tags_tabbar.count() > 0:
            self.tags_tabbar.removeTab(0)
        for idx, tab in enumerate(self._tags_tabs):
            title = _repair_status_text_mojibake(str(tab.name or f"Вкладка {idx + 1}")).strip() or f"Вкладка {idx + 1}"
            self.tags_tabbar.addTab(title)
        if self._tags_tabs:
            bounded = max(0, min(int(target_index), len(self._tags_tabs) - 1))
            self.tags_tabbar.setCurrentIndex(bounded)
        self._updating_tags_tabs = False

    def _load_active_tags_tab_to_table(self) -> None:
        self.tags_table.setRowCount(0)
        if self._active_tags_tab_index < 0 or self._active_tags_tab_index >= len(self._tags_tabs):
            self._fit_tags_table_columns(initial=True)
            return
        tab = self._tags_tabs[self._active_tags_tab_index]
        for tag in tab.tags:
            self._add_tag_row(TagConfig.from_dict(tag.to_dict()))
        self._fit_tags_table_columns(initial=True)

    def _on_tags_tab_changed(self, index: int) -> None:
        if self._updating_tags_tabs:
            self._active_tags_tab_index = int(index)
            return
        self._capture_active_tags_tab()
        self._active_tags_tab_index = int(index)
        self._load_active_tags_tab_to_table()

    def _on_tags_tab_moved(self, from_index: int, to_index: int) -> None:
        if from_index == to_index:
            return
        self._capture_active_tags_tab()
        if from_index < 0 or to_index < 0:
            return
        if from_index >= len(self._tags_tabs) or to_index >= len(self._tags_tabs):
            return
        tab = self._tags_tabs.pop(from_index)
        self._tags_tabs.insert(to_index, tab)
        self._active_tags_tab_index = int(self.tags_tabbar.currentIndex())
        self._load_active_tags_tab_to_table()

    def _on_add_tags_tab_clicked(self, _checked: bool = False) -> None:
        self._capture_active_tags_tab()
        next_index = len(self._tags_tabs) + 1
        self._tags_tabs.append(TagTabConfig(name=f"Вкладка {next_index}", tags=[]))
        self._refresh_tags_tabbar(target_index=len(self._tags_tabs) - 1)
        self._active_tags_tab_index = self.tags_tabbar.currentIndex()
        self._load_active_tags_tab_to_table()

    def _on_rename_tags_tab_clicked(self, _checked: bool = False) -> None:
        idx = int(self.tags_tabbar.currentIndex())
        if idx < 0 or idx >= len(self._tags_tabs):
            return
        current_name = _repair_status_text_mojibake(str(self._tags_tabs[idx].name or f"Вкладка {idx + 1}")).strip() or f"Вкладка {idx + 1}"
        new_name, ok = QInputDialog.getText(self, 'Переименовать вкладку', 'Название вкладки:', text=current_name)
        if not ok:
            return
        cleaned = str(new_name).strip()
        if not cleaned:
            return
        self._tags_tabs[idx].name = cleaned
        self.tags_tabbar.setTabText(idx, cleaned)

    def _on_tags_tab_close_requested(self, index: int) -> None:
        if len(self._tags_tabs) <= 1:
            QMessageBox.information(self, 'Регистры Modbus', 'Должна оставаться минимум одна вкладка.')
            return
        if index < 0 or index >= len(self._tags_tabs):
            return
        self._capture_active_tags_tab()
        removed_name = str(self._tags_tabs[index].name or "")
        self._tags_tabs.pop(index)
        target = min(index, len(self._tags_tabs) - 1)
        self._refresh_tags_tabbar(target_index=target)
        self._active_tags_tab_index = self.tags_tabbar.currentIndex()
        self._load_active_tags_tab_to_table()
        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: РІРєР»Р°РґРєР° '{removed_name}' СѓРґР°Р»РµРЅР°")

    def _sync_tags_poll_buttons(self) -> None:
        running = bool(self._tags_poll_timer.isActive())
        self.tags_poll_start_btn.setEnabled(not running)
        self.tags_poll_stop_btn.setEnabled(running)

    def _start_tags_polling(self, _checked: bool = False) -> None:
        interval_ms = max(100, int(self.tags_poll_interval_spin.value()))
        self._tags_poll_timer.start(interval_ms)
        self._sync_tags_poll_buttons()
        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: Р°РІС‚РѕС‡С‚РµРЅРёРµ СЂРµРіРёСЃС‚СЂРѕРІ Р·Р°РїСѓС‰РµРЅРѕ, РёРЅС‚РµСЂРІР°Р» {interval_ms} ms")
        self._read_tags_once(update_status=True)

    def _stop_tags_polling(self, _checked: bool = False, silent: bool = False) -> None:
        if self._tags_poll_timer.isActive():
            self._tags_poll_timer.stop()
        self._sync_tags_poll_buttons()
        if not silent:
            self.status_label.setText('Статус: авточтение регистров остановлено')

    def _on_tags_poll_timer(self) -> None:
        self._read_tags_once(update_status=False)

    def _set_tag_row_status(self, row: int, text: str, error: bool = False) -> None:
        if row < 0 or row >= self.tags_table.rowCount():
            return
        item = self.tags_table.item(row, 9)
        if item is None:
            item = QTableWidgetItem()
            self.tags_table.setItem(row, 9, item)
        item.setText(text)
        item.setForeground(QColor("#e57373" if error else "#8bc34a"))

    def _configure_tag_value_editor(self, editor: QDoubleSpinBox, data_type: str) -> None:
        if data_type == "int16":
            editor.setDecimals(0)
            editor.setRange(-32768.0, 32767.0)
            editor.setSingleStep(1.0)
            return
        if data_type == "uint16":
            editor.setDecimals(0)
            editor.setRange(0.0, 65535.0)
            editor.setSingleStep(1.0)
            return
        if data_type == "bool":
            editor.setDecimals(0)
            editor.setRange(0.0, 1.0)
            editor.setSingleStep(1.0)
            return
        editor.setDecimals(6)
        editor.setRange(-1e9, 1e9)
        editor.setSingleStep(0.1)

    def _on_tag_type_combo_changed(self, _index: int) -> None:
        combo = self.sender()
        if not isinstance(combo, QComboBox):
            return
        row = self._table_row_for_widget(self.tags_table, combo, 4)
        if row < 0:
            return
        data_type = str(combo.currentData() or "int16")
        bit_spin = self.tags_table.cellWidget(row, 5)
        order_combo = self.tags_table.cellWidget(row, 6)
        value_spin = self.tags_table.cellWidget(row, 7)
        if isinstance(bit_spin, QSpinBox):
            bit_spin.setEnabled(data_type == "bool")
            if data_type != "bool":
                bit_spin.setValue(0)
        if isinstance(order_combo, QComboBox):
            order_combo.setEnabled(data_type == "float32")
            if data_type != "float32":
                order_combo.setCurrentIndex(order_combo.findData("ABCD"))
        if isinstance(value_spin, QDoubleSpinBox):
            self._configure_tag_value_editor(value_spin, data_type)

    def _add_tag_row(self, tag: TagConfig | None = None) -> None:
        if tag is None:
            tag = TagConfig(name=f"Tag {self.tags_table.rowCount() + 1}")
        row = self.tags_table.rowCount()
        self.tags_table.insertRow(row)

        read_item = QTableWidgetItem()
        read_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        read_item.setCheckState(Qt.CheckState.Checked if tag.read_enabled else Qt.CheckState.Unchecked)
        self.tags_table.setItem(row, 0, read_item)

        name_item = QTableWidgetItem(str(tag.name or f"Tag {row + 1}"))
        name_item.setData(Qt.ItemDataRole.UserRole, tag.id)
        self.tags_table.setItem(row, 1, name_item)

        addr_spin = QSpinBox()
        addr_spin.setRange(0, 65535)
        addr_spin.setValue(max(0, int(tag.address)))
        self.tags_table.setCellWidget(row, 2, addr_spin)

        reg_combo = QComboBox()
        reg_combo.addItem('Holding (хран.)', "holding")
        reg_combo.addItem('Input (вход.)', "input")
        reg_index = reg_combo.findData(str(tag.register_type or "holding"))
        reg_combo.setCurrentIndex(reg_index if reg_index >= 0 else 0)
        self.tags_table.setCellWidget(row, 3, reg_combo)

        type_combo = QComboBox()
        type_combo.addItem('INT16 (знак.)', "int16")
        type_combo.addItem('UINT16 (без знака)', "uint16")
        type_combo.addItem("REAL / FLOAT32", "float32")
        type_combo.addItem('BOOL (бит)', "bool")
        type_index = type_combo.findData(str(tag.data_type or "int16"))
        type_combo.setCurrentIndex(type_index if type_index >= 0 else 0)
        type_combo.currentIndexChanged.connect(self._on_tag_type_combo_changed)
        self.tags_table.setCellWidget(row, 4, type_combo)

        bit_spin = QSpinBox()
        bit_spin.setRange(0, 15)
        bit_spin.setValue(max(0, min(15, int(tag.bit_index))))
        self.tags_table.setCellWidget(row, 5, bit_spin)

        order_combo = QComboBox()
        order_combo.addItem("ABCD", "ABCD")
        order_combo.addItem("BADC", "BADC")
        order_combo.addItem("CDAB", "CDAB")
        order_combo.addItem("DCBA", "DCBA")
        ord_idx = order_combo.findData(str(tag.float_order or "ABCD"))
        order_combo.setCurrentIndex(ord_idx if ord_idx >= 0 else 0)
        self.tags_table.setCellWidget(row, 6, order_combo)

        value_spin = QDoubleSpinBox()
        value_spin.setKeyboardTracking(False)
        value_spin.setRange(-1e9, 1e9)
        value_spin.setDecimals(6)
        value_spin.setValue(float(tag.value))
        self.tags_table.setCellWidget(row, 7, value_spin)

        write_btn = QPushButton('Запись')
        write_btn.clicked.connect(self._on_write_single_tag_row_clicked)
        self.tags_table.setCellWidget(row, 8, write_btn)

        status_item = QTableWidgetItem("")
        status_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.tags_table.setItem(row, 9, status_item)

        data_type = str(type_combo.currentData() or "int16")
        bit_spin.setEnabled(data_type == "bool")
        if data_type != "bool":
            bit_spin.setValue(0)
        order_combo.setEnabled(data_type == "float32")
        if data_type != "float32":
            order_combo.setCurrentIndex(order_combo.findData("ABCD"))
        self._configure_tag_value_editor(value_spin, data_type)

    def _fit_tags_table_columns(self, initial: bool = False) -> None:
        header = self.tags_table.horizontalHeader()
        header.setStretchLastSection(False)
        self.tags_table.resizeColumnsToContents()
        for col in range(self.tags_table.columnCount()):
            width = self.tags_table.columnWidth(col)
            if col == 9:
                min_w, max_w = 180, 520
            else:
                min_w, max_w = 56, 360
            self.tags_table.setColumnWidth(col, min(max(min_w, width + 12), max_w))
        for col in range(self.tags_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        if isinstance(getattr(self, "current_profile", None), ProfileConfig):
            self._apply_saved_table_column_widths(self.current_profile, "tags_table", self.tags_table)

    def _load_tags_to_ui(self, profile: ProfileConfig) -> None:
        self.tags_start_addr_spin.setValue(max(0, int(profile.tags_bulk_start_address)))
        self.tags_count_spin.setValue(max(1, int(profile.tags_bulk_count)))
        self.tags_step_spin.setValue(max(1, int(profile.tags_bulk_step)))
        self.tags_poll_interval_spin.setValue(max(100, int(profile.tags_poll_interval_ms)))
        reg_index = self.tags_reg_combo.findData(str(profile.tags_bulk_register_type or "holding"))
        self.tags_reg_combo.setCurrentIndex(reg_index if reg_index >= 0 else 0)
        type_index = self.tags_type_combo.findData(str(profile.tags_bulk_data_type or "int16"))
        self.tags_type_combo.setCurrentIndex(type_index if type_index >= 0 else 0)
        ord_index = self.tags_float_order_combo.findData(str(profile.tags_bulk_float_order or "ABCD"))
        self.tags_float_order_combo.setCurrentIndex(ord_index if ord_index >= 0 else 0)
        tabs_src = profile.tag_tabs
        if not tabs_src:
            if profile.tags:
                tabs_src = [TagTabConfig(name='Вкладка 1', tags=self._clone_tag_list(profile.tags))]
            else:
                tabs_src = [TagTabConfig(name='Вкладка 1', tags=[])]
        self._tags_tabs = [
            TagTabConfig(
                id=str(tab.id),
                name=_repair_status_text_mojibake(str(tab.name or f"Вкладка {idx + 1}")).strip() or f"Вкладка {idx + 1}",
                tags=self._clone_tag_list(tab.tags),
            )
            for idx, tab in enumerate(tabs_src)
        ]
        if not self._tags_tabs:
            self._tags_tabs = [TagTabConfig(name='Вкладка 1', tags=[])]
        self._active_tags_tab_index = 0
        self._refresh_tags_tabbar(target_index=0)
        self._load_active_tags_tab_to_table()

    def _collect_tag_row(self, row: int) -> TagConfig | None:
        read_item = self.tags_table.item(row, 0)
        name_item = self.tags_table.item(row, 1)
        addr_spin = self.tags_table.cellWidget(row, 2)
        reg_combo = self.tags_table.cellWidget(row, 3)
        type_combo = self.tags_table.cellWidget(row, 4)
        bit_spin = self.tags_table.cellWidget(row, 5)
        order_combo = self.tags_table.cellWidget(row, 6)
        value_spin = self.tags_table.cellWidget(row, 7)
        if (
            name_item is None
            or not isinstance(addr_spin, QSpinBox)
            or not isinstance(reg_combo, QComboBox)
            or not isinstance(type_combo, QComboBox)
            or not isinstance(bit_spin, QSpinBox)
            or not isinstance(order_combo, QComboBox)
            or not isinstance(value_spin, QDoubleSpinBox)
        ):
            return None
        tag_id = str(name_item.data(Qt.ItemDataRole.UserRole) or uuid.uuid4())
        name_item.setData(Qt.ItemDataRole.UserRole, tag_id)
        data_type = str(type_combo.currentData() or "int16")
        bit_index = int(bit_spin.value())
        if data_type != "bool":
            bit_index = 0
        float_order = str(order_combo.currentData() or "ABCD")
        if data_type != "float32":
            float_order = "ABCD"

        return TagConfig(
            id=tag_id,
            name=str(name_item.text().strip() or f"Tag {row + 1}"),
            address=max(0, int(addr_spin.value())),
            register_type=str(reg_combo.currentData() or "holding"),
            data_type=data_type,
            bit_index=max(0, min(15, bit_index)),
            float_order=float_order,
            read_enabled=bool(read_item is not None and read_item.checkState() == Qt.CheckState.Checked),
            value=float(value_spin.value()),
        )

    def _collect_tags_table(self) -> list[TagConfig]:
        tags: list[TagConfig] = []
        seen_ids: set[str] = set()
        for row in range(self.tags_table.rowCount()):
            tag = self._collect_tag_row(row)
            if tag is None:
                continue
            if tag.id in seen_ids:
                tag.id = str(uuid.uuid4())
                name_item = self.tags_table.item(row, 1)
                if name_item is not None:
                    name_item.setData(Qt.ItemDataRole.UserRole, tag.id)
            seen_ids.add(tag.id)
            tags.append(tag)
        return tags

    def _collect_tags_tabs(self) -> list[TagTabConfig]:
        self._capture_active_tags_tab()
        result: list[TagTabConfig] = []
        for idx, tab in enumerate(self._tags_tabs):
            name = _repair_status_text_mojibake(str(tab.name or f"Вкладка {idx + 1}")).strip() or f"Вкладка {idx + 1}"
            result.append(
                TagTabConfig(
                    id=str(tab.id or uuid.uuid4()),
                    name=name,
                    tags=self._clone_tag_list(tab.tags),
                )
            )
        if not result:
            result.append(TagTabConfig(name='Вкладка 1', tags=[]))
        return result

    def _write_tag_row_with_client(
        self,
        client: ModbusTcpClient,
        row: int,
        skip_old_value_read: bool = False,
    ) -> tuple[bool, str]:
        tag = self._collect_tag_row(row)
        if tag is None:
            return False, 'Строка не распознана'
        value_spin = self.tags_table.cellWidget(row, 7)
        if not isinstance(value_spin, QDoubleSpinBox):
            return False, 'Нет поля значения'
        value = float(value_spin.value())
        old_value: float | None = None
        if not bool(skip_old_value_read):
            try:
                old_value = self._read_single_tag(client, tag)
            except Exception:
                old_value = None
        self._write_single_tag(client, tag, value)
        if old_value is None:
            return True, f"{tag.name} MW{tag.address}: Р·Р°РїРёСЃР°РЅРѕ {value:.6g}"
        return True, f"{tag.name} MW{tag.address}: {old_value:.6g} -> {value:.6g}"

    def _on_write_single_tag_row_clicked(self, _checked: bool = False) -> None:
        button = self.sender()
        if not isinstance(button, QPushButton):
            return
        row = self._table_row_for_widget(self.tags_table, button, 8)
        if row < 0:
            return
        source = self._selected_tags_source()
        if source is None:
            client = self._open_tags_client()
            if client is None:
                self._set_tag_row_status(row, 'Нет связи', error=True)
                return
            try:
                try:
                    _ok, message = self._write_tag_row_with_client(client, row)
                    self._set_tag_row_status(row, 'Записано', error=False)
                    self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: {message}")
                except Exception as exc:
                    self._set_tag_row_status(row, 'Ошибка записи', error=True)
                    self.status_label.setText(f"РћС€РёР±РєР° Р·Р°РїРёСЃРё СЃС‚СЂРѕРєРё {row + 1}: {exc}")
            finally:
                try:
                    client.close()
                except Exception:
                    pass
            return

        try:
            _ok, message = self._write_tag_row_remote(source, row)
            self._set_tag_row_status(row, 'Записано', error=False)
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: {message}")
        except Exception as exc:
            self._set_tag_row_status(row, 'Ошибка записи', error=True)
            self.status_label.setText(f"РћС€РёР±РєР° Р·Р°РїРёСЃРё СЃС‚СЂРѕРєРё {row + 1}: {exc}")

    def _on_add_tag_clicked(self, _checked: bool = False) -> None:
        self._add_tag_row()
        self._fit_tags_table_columns(initial=False)

    def _on_remove_tag_rows_clicked(self, _checked: bool = False) -> None:
        selected = sorted({idx.row() for idx in self.tags_table.selectedIndexes()}, reverse=True)
        for row in selected:
            self.tags_table.removeRow(row)
        self._fit_tags_table_columns(initial=False)

    def _on_clear_tags_clicked(self, _checked: bool = False) -> None:
        if self.tags_table.rowCount() <= 0:
            return
        answer = QMessageBox.question(
            self,
            'Регистры Modbus',
            'Удалить все теги из таблицы?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.tags_table.setRowCount(0)
        self._fit_tags_table_columns(initial=False)
        self.status_label.setText('Статус: все регистры удалены из таблицы')

    def _on_add_tag_range_clicked(self, _checked: bool = False) -> None:
        start_address = int(self.tags_start_addr_spin.value())
        count = int(self.tags_count_spin.value())
        step = int(self.tags_step_spin.value())
        register_type = str(self.tags_reg_combo.currentData() or "holding")
        data_type = str(self.tags_type_combo.currentData() or "int16")
        float_order = str(self.tags_float_order_combo.currentData() or "ABCD")

        for i in range(count):
            address = start_address + i * step
            tag = TagConfig(
                name=f"Tag {self.tags_table.rowCount() + 1}",
                address=address,
                register_type=register_type,
                data_type=data_type,
                bit_index=0,
                float_order=float_order,
                read_enabled=True,
                value=0.0,
            )
            self._add_tag_row(tag)
        self._fit_tags_table_columns(initial=False)
        self.status_label.setText(
            f"РЎС‚Р°С‚СѓСЃ: РґРѕР±Р°РІР»РµРЅРѕ {count} СЂРµРіРёСЃС‚СЂРѕРІ (СЃС‚Р°СЂС‚={start_address}, С€Р°Рі={step}, С‚РёРї={data_type})"
        )

    def _open_tags_client(self) -> ModbusTcpClient | None:
        client = ModbusTcpClient(
            host=self.current_profile.ip,
            port=self.current_profile.port,
            timeout=self.current_profile.timeout_s,
        )
        try:
            ok = bool(client.connect())
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР°: РїРѕРґРєР»СЋС‡РµРЅРёРµ Рє {self.current_profile.ip}:{self.current_profile.port} РЅРµ СѓРґР°Р»РѕСЃСЊ")
            return None
        if not ok:
            self.status_label.setText(f"РћС€РёР±РєР°: РїРѕРґРєР»СЋС‡РµРЅРёРµ Рє {self.current_profile.ip}:{self.current_profile.port} РЅРµ СѓРґР°Р»РѕСЃСЊ")
            return None
        return client

    def _read_single_tag(self, client: ModbusTcpClient, tag: TagConfig) -> float:
        address = max(0, int(tag.address) + int(self.current_profile.address_offset))
        count = 2 if tag.data_type == "float32" else 1
        if tag.register_type == "input":
            response = ModbusWorker._read_input_registers(client, address, count, self.current_profile.unit_id)
        else:
            response = ModbusWorker._read_holding_registers(client, address, count, self.current_profile.unit_id)
        if response.isError():
            raise RuntimeError(str(response))
        registers = list(response.registers)
        return ModbusWorker._decode_registers(tag.data_type, registers, tag.float_order, tag.bit_index)

    def _write_single_tag(self, client: ModbusTcpClient, tag: TagConfig, write_value: float) -> None:
        if tag.register_type == "input":
            raise RuntimeError('Input-регистры доступны только для чтения')
        address = max(0, int(tag.address) + int(self.current_profile.address_offset))
        if tag.data_type == "int16":
            raw = int(round(write_value))
            if raw < -32768 or raw > 32767:
                raise RuntimeError('INT16 вне диапазона -32768..32767')
            response = ModbusWorker._write_single_register(client, address, raw & 0xFFFF, self.current_profile.unit_id)
        elif tag.data_type == "uint16":
            raw = int(round(write_value))
            if raw < 0 or raw > 65535:
                raise RuntimeError('UINT16 вне диапазона 0..65535')
            response = ModbusWorker._write_single_register(client, address, raw, self.current_profile.unit_id)
        elif tag.data_type == "bool":
            bit = max(0, min(15, int(tag.bit_index)))
            desired = 1 if int(round(write_value)) != 0 else 0
            current = ModbusWorker._read_holding_registers(client, address, 1, self.current_profile.unit_id)
            if current.isError():
                raise RuntimeError(f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ СЂРµРіРёСЃС‚СЂР° РїРµСЂРµРґ Р·Р°РїРёСЃСЊСЋ BOOL: {current}")
            current_reg = int(current.registers[0]) if current.registers else 0
            if desired:
                raw = current_reg | (1 << bit)
            else:
                raw = current_reg & ~(1 << bit)
            response = ModbusWorker._write_single_register(client, address, raw & 0xFFFF, self.current_profile.unit_id)
        else:
            if not math.isfinite(float(write_value)):
                raise RuntimeError('FLOAT32 должен быть конечным числом')
            reg0, reg1 = ModbusWorker._encode_float32_words(float(write_value), tag.float_order)
            response = ModbusWorker._write_multiple_registers(client, address, [reg0, reg1], self.current_profile.unit_id)
        if response.isError():
            raise RuntimeError(str(response))

    def _read_single_tag_remote(self, source: RecorderSourceConfig, tag: TagConfig) -> float:
        payload = self._source_request_json(
            source,
            "POST",
            "/v1/modbus/read",
            payload={
                "address": int(tag.address),
                "register_type": str(tag.register_type),
                "data_type": str(tag.data_type),
                "bit_index": int(tag.bit_index),
                "float_order": str(tag.float_order),
            },
            timeout_s=1.5,
        )
        if not bool(payload.get("ok", False)):
            raise RuntimeError(str(payload.get("error") or "read_failed"))
        return float(payload.get("value", 0.0))

    def _write_single_tag_remote(self, source: RecorderSourceConfig, tag: TagConfig, write_value: float) -> None:
        payload = self._source_request_json(
            source,
            "POST",
            "/v1/modbus/write",
            payload={
                "address": int(tag.address),
                "register_type": str(tag.register_type),
                "data_type": str(tag.data_type),
                "bit_index": int(tag.bit_index),
                "float_order": str(tag.float_order),
                "value": float(write_value),
            },
            timeout_s=1.5,
        )
        if not bool(payload.get("ok", False)):
            raise RuntimeError(str(payload.get("error") or "write_failed"))

    def _write_tag_row_remote(
        self,
        source: RecorderSourceConfig,
        row: int,
        skip_old_value_read: bool = False,
    ) -> tuple[bool, str]:
        tag = self._collect_tag_row(row)
        if tag is None:
            return False, 'Строка не распознана'
        value_spin = self.tags_table.cellWidget(row, 7)
        if not isinstance(value_spin, QDoubleSpinBox):
            return False, 'Нет поля значения'
        value = float(value_spin.value())
        old_value: float | None = None
        if not bool(skip_old_value_read):
            try:
                old_value = self._read_single_tag_remote(source, tag)
            except Exception:
                old_value = None
        self._write_single_tag_remote(source, tag, value)
        if old_value is None:
            return True, f"{source.name}/{tag.name} MW{tag.address}: Р·Р°РїРёСЃР°РЅРѕ {value:.6g}"
        return True, f"{source.name}/{tag.name} MW{tag.address}: {old_value:.6g} -> {value:.6g}"

    @staticmethod
    def _row_batch_key(row: int) -> str:
        return f"row:{int(row)}"

    @staticmethod
    def _row_from_batch_key(raw: str) -> int | None:
        text = str(raw or "")
        if not text.startswith("row:"):
            return None
        try:
            return int(text.split(":", 1)[1])
        except Exception:
            return None

    def _read_tags_many_with_client(
        self,
        client: ModbusTcpClient,
        tags_by_row: list[tuple[int, TagConfig]],
    ) -> tuple[dict[int, float], dict[int, str], str | None]:
        if not tags_by_row:
            return {}, {}, None
        temp_items: list[object] = []
        for row, tag in tags_by_row:
            item = type("TagReadItem", (), {})()
            item.id = self._row_batch_key(row)
            item.name = str(tag.name)
            item.address = int(tag.address)
            item.register_type = str(tag.register_type)
            item.data_type = str(tag.data_type)
            item.bit_index = int(tag.bit_index)
            item.float_order = str(tag.float_order)
            item.scale = 1.0
            temp_items.append(item)

        specs = ModbusWorker._build_read_specs(
            temp_items,
            address_offset=int(self.current_profile.address_offset),
            default_scale=1.0,
        )
        values, errors, comm_error = ModbusWorker._read_specs_grouped(
            client,
            specs,
            int(self.current_profile.unit_id),
            read_attempts=max(1, int(self.current_profile.retries) + 1),
        )
        values_by_row: dict[int, float] = {}
        for key, entry in values.items():
            row = self._row_from_batch_key(str(key))
            if row is None:
                continue
            _name, value = entry
            values_by_row[row] = float(value)

        errors_by_row: dict[int, str] = {}
        for spec, exc in errors:
            row = self._row_from_batch_key(str(spec.get("id", "")))
            if row is None:
                continue
            errors_by_row[row] = str(exc)

        comm_error_text: str | None = None
        if comm_error is not None:
            spec, exc = comm_error
            row = self._row_from_batch_key(str(spec.get("id", "")))
            if row is not None and row not in errors_by_row:
                errors_by_row[row] = str(exc)
            comm_error_text = str(exc)
        return values_by_row, errors_by_row, comm_error_text

    def _read_tags_many_remote_batch(
        self,
        source: RecorderSourceConfig,
        tags_by_row: list[tuple[int, TagConfig]],
    ) -> tuple[dict[int, float], dict[int, str], str | None]:
        payload_items = [
            {
                "id": self._row_batch_key(row),
                "name": str(tag.name),
                "address": int(tag.address),
                "register_type": str(tag.register_type),
                "data_type": str(tag.data_type),
                "bit_index": int(tag.bit_index),
                "float_order": str(tag.float_order),
            }
            for row, tag in tags_by_row
        ]
        try:
            payload = self._source_request_json(
                source,
                "POST",
                "/v1/modbus/read_many",
                payload={"items": payload_items},
                timeout_s=min(6.0, max(1.2, 0.02 * len(payload_items) + 1.0)),
            )
        except urlerror.HTTPError as exc:
            raise RuntimeError(f"HTTP {int(getattr(exc, 'code', 0))}: read_many_failed") from exc
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        if not bool(payload.get("ok", False)):
            raise RuntimeError(str(payload.get("error") or "read_many_failed"))

        values_by_row: dict[int, float] = {}
        for item in payload.get("values") or []:
            if not isinstance(item, dict):
                continue
            row = self._row_from_batch_key(str(item.get("id", "")))
            if row is None:
                continue
            try:
                values_by_row[row] = float(item.get("value", 0.0))
            except Exception:
                continue

        errors_by_row: dict[int, str] = {}
        for item in payload.get("errors") or []:
            if not isinstance(item, dict):
                continue
            row = self._row_from_batch_key(str(item.get("id", "")))
            if row is None:
                continue
            errors_by_row[row] = str(item.get("error") or "read_failed")

        comm_error_text: str | None = None
        comm_raw = payload.get("connection_error")
        if isinstance(comm_raw, dict):
            row = self._row_from_batch_key(str(comm_raw.get("id", "")))
            text = str(comm_raw.get("error") or "connection_error")
            if row is not None and row not in errors_by_row:
                errors_by_row[row] = text
            comm_error_text = text
        return values_by_row, errors_by_row, comm_error_text

    def _read_tags_once(self, update_status: bool = True) -> tuple[int, int]:
        source = self._selected_tags_source()
        rows_to_read: list[tuple[int, TagConfig, QDoubleSpinBox]] = []
        for row in range(self.tags_table.rowCount()):
            tag = self._collect_tag_row(row)
            if tag is None or not tag.read_enabled:
                continue
            value_spin = self.tags_table.cellWidget(row, 7)
            if not isinstance(value_spin, QDoubleSpinBox):
                continue
            rows_to_read.append((row, tag, value_spin))

        if not rows_to_read:
            if update_status:
                src_text = 'локальный Modbus' if source is None else f"{source.name} ({source.host}:{source.port})"
                self.status_label.setText(
                    f"Статус: чтение регистров завершено [{src_text}] (успешно 0, ошибок 0)"
                )
            return 0, 0

        values_by_row: dict[int, float] = {}
        errors_by_row: dict[int, str] = {}
        comm_error_text: str | None = None
        client: ModbusTcpClient | None = None
        if source is None:
            client = self._open_tags_client()
            if client is None:
                return 0, 0
            try:
                values_by_row, errors_by_row, comm_error_text = self._read_tags_many_with_client(
                    client,
                    [(row, tag) for row, tag, _spin in rows_to_read],
                )
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        else:
            try:
                values_by_row, errors_by_row, comm_error_text = self._read_tags_many_remote_batch(
                    source,
                    [(row, tag) for row, tag, _spin in rows_to_read],
                )
            except Exception as exc:
                error_text = str(exc)
                for row, _tag, _spin in rows_to_read:
                    errors_by_row[row] = error_text
                comm_error_text = error_text

        ok_count = 0
        fail_count = 0
        for row, tag, value_spin in rows_to_read:
            if row in values_by_row:
                value_spin.blockSignals(True)
                value_spin.setValue(float(values_by_row[row]))
                value_spin.blockSignals(False)
                self._set_tag_row_status(row, 'Прочитано', error=False)
                ok_count += 1
                continue
            self._set_tag_row_status(row, 'Ошибка чтения', error=True)
            fail_count += 1
            if row not in errors_by_row and comm_error_text:
                errors_by_row[row] = comm_error_text

        if update_status:
            src_text = 'локальный Modbus' if source is None else f"{source.name} ({source.host}:{source.port})"
            self.status_label.setText(
                f"РЎС‚Р°С‚СѓСЃ: С‡С‚РµРЅРёРµ СЂРµРіРёСЃС‚СЂРѕРІ Р·Р°РІРµСЂС€РµРЅРѕ [{src_text}] (СѓСЃРїРµС€РЅРѕ {ok_count}, РѕС€РёР±РѕРє {fail_count})"
            )
        return ok_count, fail_count

    def _on_read_tags_clicked(self, _checked: bool = False) -> None:
        with self._busy('Статус: чтение регистров...'):
            self._read_tags_once(update_status=True)

    def _on_write_tags_clicked(self, _checked: bool = False) -> None:
        source = self._selected_tags_source()
        client: ModbusTcpClient | None = None
        if source is None:
            client = self._open_tags_client()
            if client is None:
                return
        ok_count = 0
        fail_count = 0
        try:
            with self._busy('Статус: запись регистров...'):
                for row in range(self.tags_table.rowCount()):
                    tag = self._collect_tag_row(row)
                    if tag is None or not tag.read_enabled:
                        continue
                    try:
                        if source is None:
                            _ok, _message = self._write_tag_row_with_client(
                                client,
                                row,
                                skip_old_value_read=True,
                            )  # type: ignore[arg-type]
                        else:
                            _ok, _message = self._write_tag_row_remote(
                                source,
                                row,
                                skip_old_value_read=True,
                            )
                        self._set_tag_row_status(row, 'Записано', error=False)
                        ok_count += 1
                    except Exception as exc:
                        self._set_tag_row_status(row, 'Ошибка записи', error=True)
                        fail_count += 1
                        self.status_label.setText(f"РћС€РёР±РєР° Р·Р°РїРёСЃРё {tag.name}: {exc}")
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        src_text = 'локальный Modbus' if source is None else f"{source.name} ({source.host}:{source.port})"
        self.status_label.setText(
            f"РЎС‚Р°С‚СѓСЃ: Р·Р°РїРёСЃСЊ СЂРµРіРёСЃС‚СЂРѕРІ Р·Р°РІРµСЂС€РµРЅР° [{src_text}] (СѓСЃРїРµС€РЅРѕ {ok_count}, РѕС€РёР±РѕРє {fail_count})"
        )

    def _save_from_tags_window(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        self.app_config.active_profile_id = self.current_profile.id
        self.config_store.save(self.app_config)
        self._save_recorder_config_snapshot(silent=True)
        self.status_label.setText('Статус: конфигурация (включая теги) сохранена')

    def _build_scales_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.scales_window = QDialog(self, flags)
        self.scales_window.setWindowTitle('Настройка шкал')
        self.scales_window.resize(700, 380)
        self.scales_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.scales_window)

        self.scales_table = QTableWidget(0, 5)
        self.scales_table.setHorizontalHeaderLabels(['Шкала', 'Авто Y', 'Мин', 'Макс', 'Сигналы'])
        scales_header = self.scales_table.horizontalHeader()
        scales_header.setStretchLastSection(False)
        for col in range(self.scales_table.columnCount()):
            scales_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        scales_header.sectionResized.connect(lambda *_args: self._on_table_column_resized("scales_table"))
        self.scales_table.verticalHeader().setVisible(False)
        layout.addWidget(self.scales_table, 1)
        self.scales_table.itemChanged.connect(self._on_scale_table_item_changed)

    def _build_graph_settings_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.graph_settings_window = QDialog(self, flags)
        self.graph_settings_window.setWindowTitle('Настройки графика')
        self.graph_settings_window.resize(420, 260)
        self.graph_settings_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.graph_settings_window)

        form = QFormLayout()
        self.graph_bg_btn = QPushButton()
        self.graph_bg_btn.clicked.connect(lambda _checked=False: self._pick_graph_color("background"))
        self.graph_grid_color_btn = QPushButton()
        self.graph_grid_color_btn.clicked.connect(lambda _checked=False: self._pick_graph_color("grid"))
        self.graph_grid_alpha_spin = QSpinBox()
        self.graph_grid_alpha_spin.setRange(0, 100)
        self.graph_grid_alpha_spin.setSuffix(" %")
        self.graph_grid_x_checkbox = QCheckBox('Вертикальная (X)')
        self.graph_grid_y_checkbox = QCheckBox('Горизонтальная (Y)')

        form.addRow('Цвет фона', self.graph_bg_btn)
        form.addRow('Цвет сетки', self.graph_grid_color_btn)
        form.addRow('Прозрачность сетки', self.graph_grid_alpha_spin)
        form.addRow('Сетка X', self.graph_grid_x_checkbox)
        form.addRow('Сетка Y', self.graph_grid_y_checkbox)
        layout.addLayout(form)

        apply_row = QHBoxLayout()
        self.graph_apply_btn = QPushButton('Применить')
        self.graph_apply_btn.clicked.connect(self._apply_graph_settings_from_ui)
        self.graph_reset_btn = QPushButton('Сброс')
        self.graph_reset_btn.clicked.connect(self._reset_graph_settings_ui)
        apply_row.addWidget(self.graph_apply_btn)
        apply_row.addWidget(self.graph_reset_btn)
        apply_row.addStretch(1)
        layout.addLayout(apply_row)

    def _build_statistics_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.statistics_window = QDialog(self, flags)
        self.statistics_window.setWindowTitle('Анализ участка графика')
        self.statistics_window.resize(920, 480)
        self.statistics_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.statistics_window)

        controls_row = QHBoxLayout()
        self.stats_markers_checkbox = QCheckBox('Статистика по 2-м точкам')
        self.stats_markers_checkbox.toggled.connect(self._on_stats_markers_toggled)
        controls_row.addWidget(self.stats_markers_checkbox)
        self.stats_from_markers_btn = QPushButton('Применить 2 точки')
        self.stats_from_markers_btn.clicked.connect(self._on_stats_from_markers_clicked)
        controls_row.addWidget(self.stats_from_markers_btn)
        self.stats_from_view_btn = QPushButton('Период из видимой области')
        self.stats_from_view_btn.clicked.connect(self._on_stats_from_view_clicked)
        controls_row.addWidget(self.stats_from_view_btn)
        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        period_form = QFormLayout()
        self.stats_start_edit = QDateTimeEdit()
        self.stats_start_edit.setCalendarPopup(True)
        self.stats_start_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss.zzz")
        self.stats_end_edit = QDateTimeEdit()
        self.stats_end_edit.setCalendarPopup(True)
        self.stats_end_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss.zzz")
        self.stats_start_edit.dateTimeChanged.connect(self._on_stats_period_changed)
        self.stats_end_edit.dateTimeChanged.connect(self._on_stats_period_changed)
        period_form.addRow('Начало периода', self.stats_start_edit)
        period_form.addRow('Конец периода', self.stats_end_edit)
        layout.addLayout(period_form)

        self.stats_interval_label = QLabel('Интервал: -')
        layout.addWidget(self.stats_interval_label)

        calc_row = QHBoxLayout()
        self.stats_calc_btn = QPushButton('Рассчитать')
        self.stats_calc_btn.clicked.connect(self._calculate_statistics)
        calc_row.addWidget(self.stats_calc_btn)
        calc_row.addStretch(1)
        layout.addLayout(calc_row)

        self.stats_table = QTableWidget(0, 7)
        self.stats_table.setHorizontalHeaderLabels(
            ['Сигнал', 'Мин', 'Макс', 'Среднее', 'Скорость, ед/с', 'Интервал, с', 'Точек']
        )
        header = self.stats_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(self.stats_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(lambda *_args: self._on_table_column_resized("stats_table"))
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.stats_table, 1)

        self._on_stats_from_view_clicked()

    def _show_statistics_window(self) -> None:
        self._disable_auto_x_for_stats()
        self._show_tool_window(self.statistics_window)
        self._calculate_statistics()

    def _set_stats_period(self, start_ts: float, end_ts: float) -> None:
        start = float(start_ts)
        end = float(end_ts)
        if end < start:
            start, end = end, start
        self._updating_stats_ui = True
        self.stats_start_edit.setDateTime(QDateTime.fromMSecsSinceEpoch(int(start * 1000)))
        self.stats_end_edit.setDateTime(QDateTime.fromMSecsSinceEpoch(int(end * 1000)))
        self._updating_stats_ui = False
        self._update_stats_interval_label()

    def _stats_period(self) -> tuple[float, float]:
        start_ts = self.stats_start_edit.dateTime().toMSecsSinceEpoch() / 1000.0
        end_ts = self.stats_end_edit.dateTime().toMSecsSinceEpoch() / 1000.0
        if end_ts < start_ts:
            start_ts, end_ts = end_ts, start_ts
        return start_ts, end_ts

    def _update_stats_interval_label(self) -> None:
        start_ts, end_ts = self._stats_period()
        delta = max(0.0, float(end_ts - start_ts))
        self.stats_interval_label.setText(
            f"Интервал: {format_ts_ms(start_ts)} .. {format_ts_ms(end_ts)} ({delta:.3f} с)"
        )

    def _on_stats_period_changed(self, _date_time: QDateTime) -> None:
        if self._updating_stats_ui:
            return
        self._disable_auto_x_for_stats()
        self._update_stats_interval_label()
        self._mark_config_dirty()

    def _on_stats_markers_toggled(self, checked: bool) -> None:
        enabled = bool(checked)
        self.chart.set_stats_range_enabled(enabled)
        if enabled:
            self._disable_auto_x_for_stats()
            self.chart.place_stats_range_in_view()
            points = self.chart.get_stats_range()
            if points is not None:
                self._set_stats_period(points[0], points[1])
        self._mark_config_dirty()

    def _disable_auto_x_for_stats(self) -> None:
        # Statistics must not run with moving X-axis.
        self.chart.set_auto_x(False)
        self.action_auto_x.blockSignals(True)
        self.action_auto_x.setChecked(False)
        self.action_auto_x.blockSignals(False)
        if hasattr(self, "values_auto_x_checkbox"):
            self.values_auto_x_checkbox.blockSignals(True)
            self.values_auto_x_checkbox.setChecked(False)
            self.values_auto_x_checkbox.blockSignals(False)

    def _on_stats_from_markers_clicked(self, _checked: bool = False) -> None:
        if not self.stats_markers_checkbox.isChecked():
            self.stats_markers_checkbox.setChecked(True)
        points = self.chart.get_stats_range()
        if points is None:
            self.status_label.setText('Статус: двухточечный режим не включен')
            return
        self._disable_auto_x_for_stats()
        self._set_stats_period(points[0], points[1])
        self._calculate_statistics()

    def _on_stats_from_view_clicked(self, _checked: bool = False) -> None:
        self._disable_auto_x_for_stats()
        x_min, x_max = self.chart.current_x_range()
        self._set_stats_period(x_min, x_max)
        self._calculate_statistics()

    def _on_chart_stats_range_changed(self, start_ts: float, end_ts: float) -> None:
        if not hasattr(self, "stats_start_edit"):
            return
        self._set_stats_period(start_ts, end_ts)

    def _calculate_statistics(self) -> None:
        self._disable_auto_x_for_stats()
        start_ts, end_ts = self._stats_period()
        rows = self.chart.compute_statistics(start_ts, end_ts)
        self.stats_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                str(row.get("name", "")),
                f"{float(row.get('min', 0.0)):.6g}",
                f"{float(row.get('max', 0.0)):.6g}",
                f"{float(row.get('avg', 0.0)):.6g}",
                "-" if row.get("speed") is None else f"{float(row.get('speed')):.6g}",
                f"{float(row.get('span_s', 0.0)):.3f}",
                str(int(row.get("count", 0))),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                self.stats_table.setItem(row_index, col, item)
        if not self._stats_table_fitted_once:
            applied_saved = False
            if isinstance(getattr(self, "current_profile", None), ProfileConfig):
                applied_saved = self._apply_saved_table_column_widths(
                    self.current_profile,
                    "stats_table",
                    self.stats_table,
                )
            if not applied_saved:
                self.stats_table.resizeColumnsToContents()
                header = self.stats_table.horizontalHeader()
                for col in range(self.stats_table.columnCount()):
                    width = self.stats_table.columnWidth(col)
                    self.stats_table.setColumnWidth(col, min(max(56, width + 12), 420))
                    header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self._stats_table_fitted_once = True
        self._update_stats_interval_label()
        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: СЃС‚Р°С‚РёСЃС‚РёРєР° СЂР°СЃСЃС‡РёС‚Р°РЅР°, СЃРёРіРЅР°Р»РѕРІ: {len(rows)}")

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        control_menu = menu_bar.addMenu('Рабочий процесс')
        self.action_mode_online = QAction('Режим: Онлайн (запись/просмотр)', self, checkable=True)
        self.action_mode_offline = QAction('Режим: Офлайн (анализ архива)', self, checkable=True)
        self.mode_action_group = QActionGroup(self)
        self.mode_action_group.setExclusive(True)
        self.mode_action_group.addAction(self.action_mode_online)
        self.mode_action_group.addAction(self.action_mode_offline)
        self.action_mode_online.triggered.connect(lambda checked: self._set_mode_via_menu("online", checked))
        self.action_mode_offline.triggered.connect(lambda checked: self._set_mode_via_menu("offline", checked))
        control_menu.addAction(self.action_mode_online)
        control_menu.addAction(self.action_mode_offline)
        control_menu.addSeparator()
        self.action_start = QAction('3) Старт (подключить и показать данные)', self)
        self.action_start.triggered.connect(self._start_worker)
        control_menu.addAction(self.action_start)
        self.action_stop = QAction('4) Стоп', self)
        self.action_stop.triggered.connect(self._stop_worker)
        control_menu.addAction(self.action_stop)
        control_menu.addSeparator()
        self.action_recorder_start = QAction('Запустить внешний регистратор', self)
        self.action_recorder_start.triggered.connect(self._start_external_recorder)
        control_menu.addAction(self.action_recorder_start)
        self.action_recorder_start.setVisible(False)
        self.action_recorder_start.setEnabled(False)
        self.action_recorder_stop = QAction('Остановить внешний регистратор', self)
        self.action_recorder_stop.triggered.connect(self._stop_external_recorder)
        control_menu.addAction(self.action_recorder_stop)
        self.action_recorder_stop.setVisible(False)
        self.action_recorder_stop.setEnabled(False)
        self.action_recorder_status = QAction('Статус регистратора...', self)
        self.action_recorder_status.triggered.connect(self._show_external_recorder_status)
        control_menu.addAction(self.action_recorder_status)

        windows_menu = menu_bar.addMenu('Настройка')
        self.action_connection = QAction('1) Подключение...', self)
        self.action_connection.triggered.connect(lambda: self._show_tool_window(self.connection_window, True))
        windows_menu.addAction(self.action_connection)

        self.action_signals = QAction('2) Сигналы графика...', self)
        self.action_signals.triggered.connect(lambda: self._show_tool_window(self.signals_window, True))
        windows_menu.addAction(self.action_signals)

        windows_menu.addSeparator()
        advanced_windows_menu = windows_menu.addMenu('Дополнительно')

        self.action_sources = QAction('Источники данных...', self)
        self.action_sources.triggered.connect(lambda: self._show_tool_window(self.sources_window, True))
        advanced_windows_menu.addAction(self.action_sources)

        self.action_tags = QAction('Регистры Modbus...', self)
        self.action_tags.triggered.connect(lambda: self._show_tool_window(self.tags_window, True))
        advanced_windows_menu.addAction(self.action_tags)

        self.action_scales = QAction('Шкалы...', self)
        self.action_scales.triggered.connect(lambda: self._show_tool_window(self.scales_window, True))
        advanced_windows_menu.addAction(self.action_scales)
        self.action_graph_settings = QAction('График...', self)
        self.action_graph_settings.triggered.connect(lambda: self._show_tool_window(self.graph_settings_window))
        advanced_windows_menu.addAction(self.action_graph_settings)
        self.action_statistics = QAction('Статистика...', self)
        self.action_statistics.triggered.connect(self._show_statistics_window)
        advanced_windows_menu.addAction(self.action_statistics)

        archive_menu = menu_bar.addMenu('Архив и экспорт')
        self.action_archive_write_db = QAction('Писать в БД', self, checkable=True)
        self.action_archive_write_db.setChecked(True)
        self.action_archive_write_db.triggered.connect(self._on_archive_write_db_toggled)
        archive_menu.addAction(self.action_archive_write_db)
        archive_menu.addSeparator()
        self.action_save_archive = QAction('Сохранить архив...', self)
        self.action_save_archive.triggered.connect(self._save_archive_to_file)
        archive_menu.addAction(self.action_save_archive)
        self.action_load_archive = QAction('Загрузить архив...', self)
        self.action_load_archive.triggered.connect(self._load_archive_from_file)
        archive_menu.addAction(self.action_load_archive)
        archive_menu.addSeparator()
        self.action_export_chart_image = QAction('График в PNG/JPG...', self)
        self.action_export_chart_image.triggered.connect(self._export_chart_image)
        archive_menu.addAction(self.action_export_chart_image)
        self.action_export_chart_csv = QAction('Данные графика в CSV...', self)
        self.action_export_chart_csv.triggered.connect(self._export_chart_csv)
        archive_menu.addAction(self.action_export_chart_csv)
        self.action_print_chart = QAction('Печать графика...', self)
        self.action_print_chart.triggered.connect(self._print_chart)
        archive_menu.addAction(self.action_print_chart)
        archive_menu.addSeparator()
        self.action_export_connection_config = QAction('Экспорт подключения...', self)
        self.action_export_connection_config.triggered.connect(self._save_connection_config_to_file)
        archive_menu.addAction(self.action_export_connection_config)
        self.action_import_connection_config = QAction('Импорт подключения...', self)
        self.action_import_connection_config.triggered.connect(self._load_connection_config_from_file)
        archive_menu.addAction(self.action_import_connection_config)

        view_menu = menu_bar.addMenu('Вид')
        self.action_auto_x = QAction('Авто X', self, checkable=True)
        self.action_auto_x.setChecked(True)
        self.action_auto_x.toggled.connect(self._on_action_auto_x_toggled)
        view_menu.addAction(self.action_auto_x)
        self.action_cursor = QAction('Курсор', self, checkable=True)
        self.action_cursor.setChecked(False)
        self.action_cursor.toggled.connect(self._on_action_cursor_toggled)
        view_menu.addAction(self.action_cursor)
        self.action_reset_zoom = QAction('Сброс масштаба', self)
        self.action_reset_zoom.triggered.connect(lambda _checked=False: self.chart.reset_view())
        view_menu.addAction(self.action_reset_zoom)
        view_menu.addSeparator()
        self.action_values_panel = QAction('Таблица значений', self, checkable=True)
        self.action_values_panel.setChecked(True)
        self.action_values_panel.triggered.connect(self._on_values_panel_menu_toggled)
        view_menu.addAction(self.action_values_panel)

        app_menu = menu_bar.addMenu('Приложение')
        self.action_save_config = QAction('Сохранить конфигурацию', self)
        self.action_save_config.triggered.connect(self._save_config)
        app_menu.addAction(self.action_save_config)
        app_menu.addSeparator()
        self.action_minimize_tray = QAction('Свернуть в трей', self)
        self.action_minimize_tray.triggered.connect(self._minimize_to_tray)
        app_menu.addAction(self.action_minimize_tray)
        app_menu.addSeparator()
        close_menu = app_menu.addMenu('При закрытии')
        self.action_close_ask = QAction('При закрытии: запрашивать действие', self, checkable=True)
        self.action_close_to_tray = QAction('При закрытии: сворачивать в трей', self, checkable=True)
        self.action_close_exit = QAction('При закрытии: завершать программу', self, checkable=True)
        self.close_action_group = QActionGroup(self)
        self.close_action_group.setExclusive(True)
        for action in (self.action_close_ask, self.action_close_to_tray, self.action_close_exit):
            self.close_action_group.addAction(action)
            close_menu.addAction(action)
        self.action_close_ask.triggered.connect(lambda checked: self._set_close_behavior("ask", checked))
        self.action_close_to_tray.triggered.connect(lambda checked: self._set_close_behavior("tray", checked))
        self.action_close_exit.triggered.connect(lambda checked: self._set_close_behavior("exit", checked))

        close_menu.addSeparator()
        self.action_windows_autostart = QAction('Автозапуск при старте Windows', self, checkable=True)
        self.action_windows_autostart.triggered.connect(self._on_action_windows_autostart_toggled)
        app_menu.addAction(self.action_windows_autostart)
        self.action_auto_connect_startup = QAction('Автостарт просмотра при запуске', self, checkable=True)
        self.action_auto_connect_startup.triggered.connect(self._on_action_auto_connect_startup_toggled)
        app_menu.addAction(self.action_auto_connect_startup)
        app_menu.addSeparator()
        self.action_exit = QAction('Выход', self)
        self.action_exit.triggered.connect(self._exit_from_tray)
        app_menu.addAction(self.action_exit)

    def _show_tool_window(self, window: QDialog, requires_recorder: bool = False) -> None:
        if bool(requires_recorder) and not self._can_use_recorder_features():
            self.status_label.setText('Статус: TrendRecorder не запущен. Сначала запустите TrendRecorder.exe')
            return
        window.show()
        window.raise_()
        window.activateWindow()

    def _set_mode_via_menu(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    def _sync_mode_actions(self) -> None:
        mode = str(self.mode_combo.currentData() or "online")
        self.action_mode_online.setChecked(mode == "online")
        self.action_mode_offline.setChecked(mode == "offline")

    def _build_recorder_config_payload(self) -> dict:
        return {
            "format": RECORDER_CONFIG_FORMAT,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "active_profile_id": self.current_profile.id,
            "profile": copy.deepcopy(self.current_profile.to_dict()),
            "source": "viewer_configurator",
        }

    def _save_recorder_config_snapshot(self, silent: bool = True) -> bool:
        try:
            payload = self._build_recorder_config_payload()
            write_recorder_config(payload)
            return True
        except Exception as exc:
            if not silent:
                self.status_label.setText(f"РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ РєРѕРЅС„РёРіСѓСЂР°С†РёРё СЂРµРіРёСЃС‚СЂР°С‚РѕСЂР°: {exc}")
            return False

    def _local_recorder_api_source(self) -> RecorderSourceConfig | None:
        if not bool(getattr(self.current_profile, "recorder_api_enabled", False)):
            return None
        host = str(getattr(self.current_profile, "recorder_api_host", "") or "127.0.0.1").strip() or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        try:
            port = max(1, min(65535, int(getattr(self.current_profile, "recorder_api_port", 18777) or 18777)))
        except Exception:
            port = 18777
        token = str(getattr(self.current_profile, "recorder_api_token", "") or "")
        return RecorderSourceConfig(
            id="local-recorder-api",
            name="Local recorder",
            host=host,
            port=port,
            token=token,
            enabled=True,
        )

    def _push_profile_to_local_recorder(self, silent: bool = True) -> bool:
        if not self._profile_uses_local_recorder():
            return True
        if not self._is_external_recorder_running():
            return False
        source = self._local_recorder_api_source()
        if source is None:
            return False
        try:
            response = self._source_request_json(
                source,
                "PUT",
                "/v1/config",
                payload={"profile": copy.deepcopy(self.current_profile.to_dict())},
                timeout_s=1.5,
            )
        except Exception as exc:
            if not silent:
                self.status_label.setText(f"Ошибка применения настроек к локальному recorder: {exc}")
            return False
        ok = bool(response.get("ok", False))
        if not ok and not silent:
            self.status_label.setText(
                f"Ошибка применения настроек к локальному recorder: "
                f"{response.get('message') or response.get('error') or 'n/a'}"
            )
        return ok

    def _external_recorder_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            exe_path = Path(sys.executable).resolve()
            recorder_candidate = exe_path.with_name("TrendRecorder.exe")
            if recorder_candidate.exists():
                return [str(recorder_candidate), "--recorder"]
            # Fallback for one-binary recorder app.
            if exe_path.name.lower() == "trendrecorder.exe":
                return [str(exe_path), "--recorder"]
            raise RuntimeError(
                'Не найден TrendRecorder.exe рядом с клиентом. '
                'Положите TrendClient.exe и TrendRecorder.exe в одну папку.'
            )
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        return [sys.executable, str(main_path), "--recorder"]

    def _start_external_recorder(self, _checked: bool = False, silent: bool = False) -> bool:
        self._store_ui_to_profile(self.current_profile)
        if not self._save_recorder_config_snapshot(silent=bool(silent)):
            return False

        pid = resolve_recorder_pid()
        if pid is not None:
            if not silent:
                self.status_label.setText(f'Статус: внешний регистратор уже запущен (PID {pid})')
            self._update_recorder_dependent_ui_state()
            return True

        try:
            cmd = self._external_recorder_command()
        except Exception as exc:
            if not silent:
                self.status_label.setText(f"Ошибка запуска внешнего регистратора: {exc}")
            self._update_recorder_dependent_ui_state()
            return False

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
                self.status_label.setText(f"Ошибка запуска внешнего регистратора: {exc}")
            self._update_recorder_dependent_ui_state()
            return False

        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            pid = resolve_recorder_pid()
            if pid is not None:
                if not silent:
                    self.status_label.setText(f'Статус: внешний регистратор запущен (PID {pid})')
                self._update_recorder_dependent_ui_state()
                return True
            time.sleep(0.2)

        if not silent:
            self.status_label.setText('Статус: запуск регистратора выполнен, ожидание статуса')
        self._update_recorder_dependent_ui_state()
        return resolve_recorder_pid() is not None


    def _stop_external_recorder(self, _checked: bool = False) -> None:
        pid = resolve_recorder_pid()
        if pid is None:
            clear_recorder_pid()
            self.status_label.setText('Статус: внешний регистратор не запущен')
            return

        try:
            request_recorder_stop()
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° РѕС‚РїСЂР°РІРєРё РєРѕРјР°РЅРґС‹ РѕСЃС‚Р°РЅРѕРІРєРё: {exc}")
            return

        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if resolve_recorder_pid() is None:
                clear_recorder_pid()
                self.status_label.setText('Статус: внешний регистратор остановлен')
                return
            time.sleep(0.2)

        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
            else:
                os.kill(int(pid), signal.SIGTERM)
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° РїСЂРёРЅСѓРґРёС‚РµР»СЊРЅРѕР№ РѕСЃС‚Р°РЅРѕРІРєРё СЂРµРіРёСЃС‚СЂР°С‚РѕСЂР°: {exc}")
            return

        clear_recorder_pid()
        self.status_label.setText('Статус: внешний регистратор принудительно остановлен')

    def _show_external_recorder_status(self, _checked: bool = False) -> None:
        payload = read_recorder_status() or {}
        if not payload:
            payload = {
                "state": "unknown",
                "message": 'статус регистратора недоступен',
                "status_path": str(RECORDER_STATUS_PATH),
                "config_path": str(RECORDER_CONFIG_PATH),
                "pid_path": str(RECORDER_PID_PATH),
            }

        dialog = QDialog(self)
        dialog.setWindowTitle('Статус внешнего регистратора')
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

    def _on_action_auto_x_toggled(self, checked: bool) -> None:
        state = bool(checked)
        self.chart.set_auto_x(state)
        if hasattr(self, "values_auto_x_checkbox"):
            self.values_auto_x_checkbox.blockSignals(True)
            self.values_auto_x_checkbox.setChecked(state)
            self.values_auto_x_checkbox.blockSignals(False)
        mode = str(self.mode_combo.currentData() or "online")
        if mode == "online" and bool(self.current_profile.render_chart_enabled):
            if state:
                self._pending_render_samples = []
                self._load_recent_online_history_from_db(adjust_x_range=True, silent=True)
                try:
                    x_left, x_right = self.chart.current_x_range()
                    span_s = max(10.0, float(x_right - x_left))
                except Exception:
                    span_s = self._preferred_live_history_span_s()
                self._load_recent_remote_history_from_sources(span_s)
                self._reset_history_window_cache()
            else:
                self._schedule_visible_history_reload(force=True)
        self._mark_config_dirty()

    def _on_action_cursor_toggled(self, checked: bool) -> None:
        state = bool(checked)
        self.chart.set_cursor_enabled(state)
        if hasattr(self, "values_cursor_checkbox"):
            self.values_cursor_checkbox.blockSignals(True)
            self.values_cursor_checkbox.setChecked(state)
            self.values_cursor_checkbox.blockSignals(False)
        self._mark_config_dirty()

    def _on_values_auto_x_toggled(self, checked: bool) -> None:
        state = bool(checked)
        if self.action_auto_x.isChecked() != state:
            self.action_auto_x.setChecked(state)
            return
        self.chart.set_auto_x(state)
        mode = str(self.mode_combo.currentData() or "online")
        if mode == "online" and bool(self.current_profile.render_chart_enabled):
            if state:
                self._pending_render_samples = []
                self._load_recent_online_history_from_db(adjust_x_range=True, silent=True)
                try:
                    x_left, x_right = self.chart.current_x_range()
                    span_s = max(10.0, float(x_right - x_left))
                except Exception:
                    span_s = self._preferred_live_history_span_s()
                self._load_recent_remote_history_from_sources(span_s)
                self._reset_history_window_cache()
            else:
                self._schedule_visible_history_reload(force=True)
        self._mark_config_dirty()

    def _on_values_cursor_toggled(self, checked: bool) -> None:
        state = bool(checked)
        if self.action_cursor.isChecked() != state:
            self.action_cursor.setChecked(state)
        self.chart.set_cursor_enabled(state)
        self._mark_config_dirty()

    def _toggle_values_panel(self) -> None:
        if self._values_closed:
            self._restore_values_panel()
            return
        self._set_values_collapsed(not self._values_collapsed)

    def _set_values_collapsed(self, collapsed: bool) -> None:
        if self._values_closed and not collapsed:
            self._values_closed = False
            self.values_panel.setVisible(True)

        self._values_collapsed = bool(collapsed)
        self.values_table.setVisible(not self._values_collapsed)
        self.values_collapse_btn.setIcon(
            self._make_panel_control_icon("chevron_up" if self._values_collapsed else "chevron_down")
        )
        self._apply_values_panel_layout()
        self._sync_values_panel_action()
        self._mark_config_dirty()

    def _expand_values_panel(self) -> None:
        self._restore_values_panel()
        self.main_splitter.setSizes([80, 700])

    def _close_values_panel(self) -> None:
        self._values_closed = True
        self._values_collapsed = True
        self.values_table.setVisible(False)
        self.values_panel.setVisible(False)
        self._apply_values_panel_layout()
        self._sync_values_panel_action()
        self._mark_config_dirty()

    def _restore_values_panel(self) -> None:
        self._values_closed = False
        self.values_panel.setVisible(True)
        self._values_collapsed = False
        self.values_table.setVisible(True)
        self.values_collapse_btn.setIcon(self._make_panel_control_icon("chevron_down"))
        self._apply_values_panel_layout()
        self._sync_values_panel_action()
        self._mark_config_dirty()

    def _apply_values_panel_layout(self) -> None:
        if self._values_closed:
            self.main_splitter.setSizes([1, 0])
            return
        if self._values_collapsed:
            self.main_splitter.setSizes([900, 28])
            return

        sizes = self.main_splitter.sizes()
        if len(sizes) < 2 or sizes[1] < 90:
            self.main_splitter.setSizes([780, 180])

    def _on_values_panel_menu_toggled(self, checked: bool) -> None:
        if checked:
            self._restore_values_panel()
        else:
            self._close_values_panel()

    def _sync_values_panel_action(self) -> None:
        self.action_values_panel.blockSignals(True)
        self.action_values_panel.setChecked(not self._values_closed)
        self.action_values_panel.blockSignals(False)

    def _on_archive_write_db_toggled(self, checked: bool) -> None:
        self.archive_to_db_checkbox.setChecked(bool(checked))
        self.current_profile.archive_to_db = bool(checked)
        self._update_runtime_status_panel()
        self._mark_config_dirty()

    def _set_close_behavior(self, behavior: str, checked: bool) -> None:
        if not checked:
            return
        if behavior not in {"ask", "tray", "exit"}:
            return
        self.app_config.close_behavior = behavior
        self._sync_close_behavior_actions()
        self._mark_config_dirty()

    def _sync_close_behavior_actions(self) -> None:
        behavior = self.app_config.close_behavior
        self.action_close_ask.setChecked(behavior == "ask")
        self.action_close_to_tray.setChecked(behavior == "tray")
        self.action_close_exit.setChecked(behavior == "exit")

    def _sync_startup_actions(self) -> None:
        if hasattr(self, "action_windows_autostart"):
            self.action_windows_autostart.setEnabled(sys.platform == "win32")
            self.action_windows_autostart.blockSignals(True)
            self.action_windows_autostart.setChecked(bool(self.app_config.auto_start_windows))
            self.action_windows_autostart.blockSignals(False)
        if hasattr(self, "action_auto_connect_startup"):
            self.action_auto_connect_startup.blockSignals(True)
            self.action_auto_connect_startup.setChecked(bool(self.app_config.auto_connect_on_launch))
            self.action_auto_connect_startup.blockSignals(False)

    def _mark_config_dirty(self) -> None:
        if self._updating_ui:
            return
        self._config_dirty = True
        self._autosave_timer.start()

    def _autosave_config_if_dirty(self) -> None:
        if not self._config_dirty:
            return
        try:
            # Lightweight autosave: persist UI state that is already in-memory
            # (column widths, splitter sizes, view state) without rebuilding
            # full profile structures on every small UI move.
            self._capture_ui_state(self.current_profile)
            self.app_config.active_profile_id = self.current_profile.id
            self.config_store.save(self.app_config)
            self._config_dirty = False
        except Exception:
            # Silent autosave: avoid interrupting operator workflow.
            pass

    def _apply_windows_autostart(self, silent: bool = False) -> None:
        enabled = bool(self.app_config.auto_start_windows)
        ok, error = set_windows_autostart(enabled)
        if ok:
            return
        if not silent:
            self.status_label.setText(f"РћС€РёР±РєР° Р°РІС‚РѕР·Р°РїСѓСЃРєР°: {error}")

    def _on_action_windows_autostart_toggled(self, checked: bool) -> None:
        enabled = bool(checked)
        self.app_config.auto_start_windows = enabled
        ok, error = set_windows_autostart(enabled)
        if not ok:
            self.app_config.auto_start_windows = False
            self._sync_startup_actions()
            self.status_label.setText(f"РћС€РёР±РєР° Р°РІС‚РѕР·Р°РїСѓСЃРєР°: {error}")
            return
        self.config_store.save(self.app_config)
        self.status_label.setText('Статус: автозапуск обновлен')

    def _on_action_auto_connect_startup_toggled(self, checked: bool) -> None:
        self.app_config.auto_connect_on_launch = bool(checked)
        self.config_store.save(self.app_config)
        self.status_label.setText('Статус: автостарт просмотра обновлен')

    def _auto_connect_startup_if_needed(self) -> None:
        if not bool(self.app_config.auto_connect_on_launch):
            return
        if str(self.current_profile.work_mode or "online") != "online":
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._start_worker()

    def _show_ui_state_debug_dialog(self, _checked: bool = False) -> None:
        self._store_ui_to_profile(self.current_profile)
        payload = self.current_profile.ui_state if isinstance(self.current_profile.ui_state, dict) else {}
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        dialog = QDialog(self)
        dialog.setWindowTitle('Текущее ui_state')
        dialog.resize(900, 640)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _init_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = None
            return

        self._tray_icon = QSystemTrayIcon(self)
        icon = self.windowIcon()
        if not icon.isNull():
            self._tray_icon.setIcon(icon)

        tray_menu = QMenu()
        action_open = tray_menu.addAction('Открыть')
        action_open.triggered.connect(self._restore_from_tray)
        action_start = tray_menu.addAction('Старт просмотра')
        action_start.triggered.connect(self._start_worker)
        action_stop = tray_menu.addAction('Стоп просмотра')
        action_stop.triggered.connect(self._stop_worker)
        tray_menu.addSeparator()
        action_exit = tray_menu.addAction('Выход')
        action_exit.triggered.connect(self._exit_from_tray)

        self._tray_icon.setContextMenu(tray_menu)
        self._tray_icon.activated.connect(
            lambda reason: self._restore_from_tray() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self._tray_icon.show()

    def _minimize_to_tray(self) -> None:
        if self._tray_icon is None:
            self.showMinimized()
            return
        self.hide()
        self._tray_icon.showMessage(
            APP_NAME,
            'Приложение свернуто в трей',
            QSystemTrayIcon.MessageIcon.Information,
            1500,
        )

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _exit_from_tray(self) -> None:
        self._force_close = True
        self.close()

    def _populate_profiles(self) -> None:
        self._updating_ui = True
        self.profile_combo.clear()
        active_index = 0

        for idx, profile in enumerate(self.app_config.profiles):
            self.profile_combo.addItem(profile.name, profile.id)
            if profile.id == self.current_profile.id:
                active_index = idx

        self.profile_combo.setCurrentIndex(active_index)
        self._updating_ui = False

    def _find_profile(self, profile_id: str) -> ProfileConfig:
        for profile in self.app_config.profiles:
            if profile.id == profile_id:
                return profile
        return self.app_config.profiles[0]

    def _table_widget_by_key(self, key: str) -> QTableWidget | None:
        mapping = {
            "values_table": getattr(self, "values_table", None),
            "signal_table": getattr(self, "signal_table", None),
            "tags_table": getattr(self, "tags_table", None),
            "scales_table": getattr(self, "scales_table", None),
            "stats_table": getattr(self, "stats_table", None),
        }
        table = mapping.get(str(key))
        return table if isinstance(table, QTableWidget) else None

    def _apply_saved_widths_all_tables(self, profile: ProfileConfig | None = None) -> None:
        # Column-width persistence is intentionally disabled.
        return

    def _schedule_apply_saved_widths_all_tables(self, profile_id: str) -> None:
        # Column-width persistence is intentionally disabled.
        return

    def _ensure_profile_ui_state(self, profile: ProfileConfig) -> dict:
        state = profile.ui_state if isinstance(profile.ui_state, dict) else {}
        profile.ui_state = state
        return state

    def _on_table_column_resized(self, table_key: str) -> None:
        # Column-width persistence is intentionally disabled.
        return

    def _apply_saved_table_header_state(self, profile: ProfileConfig, table_key: str, table: QTableWidget) -> bool:
        # Column-width persistence is intentionally disabled.
        return False

    def _apply_saved_table_column_widths(self, profile: ProfileConfig, table_key: str, table: QTableWidget) -> bool:
        # Column-width persistence is intentionally disabled.
        return False

    def _collect_active_signal_ids(self, profile: ProfileConfig | None = None) -> list[str]:
        target = profile if isinstance(profile, ProfileConfig) else self.current_profile
        active_ids: list[str] = []
        seen: set[str] = set()
        for signal in getattr(target, "signals", []):
            signal_id = str(getattr(signal, "id", "") or "").strip()
            if not signal_id or signal_id in seen:
                continue
            seen.add(signal_id)
            if bool(getattr(signal, "enabled", True)):
                active_ids.append(signal_id)
        return active_ids

    def _apply_active_signal_ids_from_ui_state(self, profile: ProfileConfig) -> None:
        state = profile.ui_state if isinstance(profile.ui_state, dict) else {}
        view = state.get("view")
        if not isinstance(view, dict):
            return
        raw_ids = view.get("active_signal_ids")
        if not isinstance(raw_ids, list):
            return
        active_ids = {str(item).strip() for item in raw_ids if str(item).strip()}
        for signal in profile.signals:
            signal_id = str(getattr(signal, "id", "") or "").strip()
            signal.enabled = bool(signal_id and signal_id in active_ids)

    def _collect_view_state_payload(self) -> dict[str, object]:
        x_min, x_max = self.chart.current_x_range()
        return {
            "auto_x": bool(self.action_auto_x.isChecked()) if hasattr(self, "action_auto_x") else True,
            "cursor_enabled": bool(self.action_cursor.isChecked()) if hasattr(self, "action_cursor") else False,
            "values_sort_column": (
                int(self._values_header_sort_column)
                if self._values_header_sort_column is not None
                else None
            ),
            "values_sort_desc": bool(self._values_header_sort_desc),
            "values_panel_collapsed": bool(self._values_collapsed),
            "values_panel_closed": bool(self._values_closed),
            "x_range": [float(x_min), float(x_max)],
            "scale_states": self.chart.export_scale_states(),
            "active_signal_ids": self._collect_active_signal_ids(),
            "stats_markers_enabled": bool(self.stats_markers_checkbox.isChecked()),
            "stats_start_ms": int(self.stats_start_edit.dateTime().toMSecsSinceEpoch()),
            "stats_end_ms": int(self.stats_end_edit.dateTime().toMSecsSinceEpoch()),
        }

    def _capture_ui_state(self, profile: ProfileConfig) -> None:
        state = self._ensure_profile_ui_state(profile)

        windows: dict[str, dict[str, int]] = {}

        def put_size(key: str, widget: QWidget | QDialog) -> None:
            size = widget.size()
            windows[key] = {"w": int(size.width()), "h": int(size.height())}

        put_size("main_window", self)
        put_size("connection_window", self.connection_window)
        put_size("signals_window", self.signals_window)
        put_size("sources_window", self.sources_window)
        put_size("tags_window", self.tags_window)
        put_size("scales_window", self.scales_window)
        put_size("graph_settings_window", self.graph_settings_window)
        put_size("statistics_window", self.statistics_window)
        state["windows"] = windows
        state["main_splitter_sizes"] = [int(x) for x in self.main_splitter.sizes()]

        state.pop("table_columns", None)
        state.pop("table_header_states", None)
        state["view"] = self._collect_view_state_payload()

    def _apply_ui_state(self, profile: ProfileConfig) -> None:
        state = profile.ui_state if isinstance(profile.ui_state, dict) else {}
        windows = state.get("windows")

        def apply_size(key: str, widget: QWidget | QDialog, min_w: int = 240, min_h: int = 140) -> None:
            if not isinstance(windows, dict):
                return
            payload = windows.get(key)
            if not isinstance(payload, dict):
                return
            try:
                w = int(payload.get("w", 0))
                h = int(payload.get("h", 0))
            except (TypeError, ValueError):
                return
            if w < min_w or h < min_h:
                return
            widget.resize(w, h)

        apply_size("main_window", self, min_w=700, min_h=420)
        apply_size("connection_window", self.connection_window)
        apply_size("signals_window", self.signals_window)
        apply_size("sources_window", self.sources_window)
        apply_size("tags_window", self.tags_window)
        apply_size("scales_window", self.scales_window)
        apply_size("graph_settings_window", self.graph_settings_window)
        apply_size("statistics_window", self.statistics_window)

        splitter_sizes = state.get("main_splitter_sizes")
        if isinstance(splitter_sizes, list) and len(splitter_sizes) >= 2:
            try:
                s0 = max(1, int(splitter_sizes[0]))
                s1 = max(0, int(splitter_sizes[1]))
                self.main_splitter.setSizes([s0, s1])
            except (TypeError, ValueError):
                pass

        # Column-width persistence is intentionally disabled.

    def _apply_runtime_view_state(self, profile: ProfileConfig) -> None:
        state = profile.ui_state if isinstance(profile.ui_state, dict) else {}
        view = state.get("view")
        if not isinstance(view, dict):
            # Safe defaults for a fresh profile.
            self.chart.set_auto_y(True)
            self.chart.set_auto_x(True)
            self.chart.set_cursor_enabled(False)
            return

        raw_sort_column = view.get("values_sort_column")
        try:
            self._values_header_sort_column = None if raw_sort_column is None else int(raw_sort_column)
        except (TypeError, ValueError):
            self._values_header_sort_column = None
        self._values_header_sort_desc = bool(view.get("values_sort_desc", False))
        header = self.values_table.horizontalHeader()
        if self._values_header_sort_column is None:
            try:
                header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            except Exception:
                pass
        else:
            header.setSortIndicator(
                int(self._values_header_sort_column),
                Qt.SortOrder.DescendingOrder if self._values_header_sort_desc else Qt.SortOrder.AscendingOrder,
            )

        values_closed = bool(view.get("values_panel_closed", False))
        values_collapsed = bool(view.get("values_panel_collapsed", False))
        if values_closed:
            self._close_values_panel()
        elif values_collapsed:
            self._set_values_collapsed(True)
        else:
            self._restore_values_panel()

        auto_x = bool(view.get("auto_x", True))
        cursor_enabled = bool(view.get("cursor_enabled", False))

        self.action_auto_x.blockSignals(True)
        self.action_auto_x.setChecked(auto_x)
        self.action_auto_x.blockSignals(False)
        if hasattr(self, "values_auto_x_checkbox"):
            self.values_auto_x_checkbox.blockSignals(True)
            self.values_auto_x_checkbox.setChecked(auto_x)
            self.values_auto_x_checkbox.blockSignals(False)
        self.chart.set_auto_x(auto_x)

        self.action_cursor.blockSignals(True)
        self.action_cursor.setChecked(cursor_enabled)
        self.action_cursor.blockSignals(False)
        if hasattr(self, "values_cursor_checkbox"):
            self.values_cursor_checkbox.blockSignals(True)
            self.values_cursor_checkbox.setChecked(cursor_enabled)
            self.values_cursor_checkbox.blockSignals(False)
        self.chart.set_cursor_enabled(cursor_enabled)

        scales_payload = view.get("scale_states")
        if isinstance(scales_payload, list) and scales_payload:
            self.chart.apply_scale_states(scales_payload)
        else:
            self.chart.set_auto_y(True)

        raw_x_range = view.get("x_range")
        if isinstance(raw_x_range, list) and len(raw_x_range) >= 2:
            try:
                x_left = float(raw_x_range[0])
                x_right = float(raw_x_range[1])
            except (TypeError, ValueError):
                x_left = x_right = 0.0
            if x_right > x_left:
                if auto_x:
                    self.chart.set_x_window_seconds(max(0.1, x_right - x_left))
                else:
                    self.chart.set_x_range(x_left, x_right)

        stats_start_ms = view.get("stats_start_ms")
        stats_end_ms = view.get("stats_end_ms")
        start_ts = None
        end_ts = None
        try:
            start_ts = int(stats_start_ms) / 1000.0
            end_ts = int(stats_end_ms) / 1000.0
        except (TypeError, ValueError):
            start_ts = None
            end_ts = None
        if start_ts is not None and end_ts is not None:
            self._set_stats_period(start_ts, end_ts)

        markers_enabled = bool(view.get("stats_markers_enabled", False))
        self.stats_markers_checkbox.blockSignals(True)
        self.stats_markers_checkbox.setChecked(markers_enabled)
        self.stats_markers_checkbox.blockSignals(False)
        self.chart.set_stats_range_enabled(markers_enabled)
        if markers_enabled and start_ts is not None and end_ts is not None:
            self.chart.set_stats_range(start_ts, end_ts)
            if auto_x:
                self.chart.set_auto_x(False)
                self.action_auto_x.blockSignals(True)
                self.action_auto_x.setChecked(False)
                self.action_auto_x.blockSignals(False)
                if hasattr(self, "values_auto_x_checkbox"):
                    self.values_auto_x_checkbox.blockSignals(True)
                    self.values_auto_x_checkbox.setChecked(False)
                    self.values_auto_x_checkbox.blockSignals(False)

    def _load_profile_to_ui(self, profile: ProfileConfig) -> None:
        self._normalize_profile_signal_sources(profile)
        self._apply_active_signal_ids_from_ui_state(profile)
        self._stop_tags_polling(silent=True)
        self._updating_ui = True
        self.profile_name_edit.setText(profile.name)
        self.ip_edit.setText(profile.ip)
        self.port_spin.setValue(profile.port)
        self.unit_id_spin.setValue(profile.unit_id)
        self.poll_interval_spin.setValue(profile.poll_interval_ms)
        self.render_interval_spin.setValue(max(50, int(profile.render_interval_ms)))
        self.render_chart_checkbox.blockSignals(True)
        self.render_chart_checkbox.setChecked(bool(profile.render_chart_enabled))
        self.render_chart_checkbox.blockSignals(False)
        self.archive_interval_spin.setValue(profile.archive_interval_ms)
        self.archive_on_change_checkbox.setChecked(bool(profile.archive_on_change_only))
        self.archive_deadband_spin.setValue(max(0.0, float(profile.archive_deadband)))
        self.archive_keepalive_spin.setValue(max(0, int(profile.archive_keepalive_s)))
        self.archive_retention_days_spin.setValue(max(0, int(profile.archive_retention_days)))
        self.timeout_spin.setValue(profile.timeout_s)
        self.retries_spin.setValue(profile.retries)
        self.address_offset_spin.setValue(profile.address_offset)
        self.recorder_api_enabled_checkbox.setChecked(bool(profile.recorder_api_enabled))
        self.recorder_api_host_edit.setText(str(profile.recorder_api_host or "0.0.0.0"))
        self.recorder_api_port_spin.setValue(max(1, min(65535, int(profile.recorder_api_port))))
        self.recorder_api_token_edit.setText(str(profile.recorder_api_token or ""))
        self._apply_render_interval_runtime(profile.render_interval_ms)
        mode_index = self.mode_combo.findData(profile.work_mode)
        self.mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.archive_to_db_checkbox.setChecked(bool(profile.archive_to_db))
        self.action_archive_write_db.setChecked(bool(profile.archive_to_db))
        self._load_graph_settings_to_ui(profile)
        self._fill_sources_table(profile.recorder_sources)
        self._refresh_signal_source_tabs()
        self._fill_signal_table(profile.signals, source_id=self._current_signal_source_id())
        self._load_tags_to_ui(profile)
        self._apply_ui_state(profile)
        self._updating_ui = False
        self._apply_render_interval_runtime(profile.render_interval_ms)
        self.chart.configure_signals(profile.signals)
        self.chart.set_visual_settings(
            background_color=profile.plot_background_color,
            grid_color=profile.plot_grid_color,
            grid_alpha=max(0.0, min(1.0, float(profile.plot_grid_alpha) / 100.0)),
            grid_x=profile.plot_grid_x,
            grid_y=profile.plot_grid_y,
        )
        self._apply_runtime_view_state(profile)
        self._on_render_chart_toggled(self.render_chart_checkbox.isChecked())
        self._schedule_apply_saved_widths_all_tables(profile.id)
        self._apply_work_mode_ui(profile.work_mode)
        self._sync_mode_actions()
        if should_preload_history_on_profile_load(
            work_mode=str(profile.work_mode or "online"),
            render_chart_enabled=bool(profile.render_chart_enabled),
            live_running=bool(self._worker is not None and self._worker.isRunning()),
        ):
            self._load_offline_initial_history_from_db(silent=True)
        self._update_recorder_dependent_ui_state()

    @staticmethod
    def _apply_color_button_style(button: QPushButton, color: str) -> None:
        button.setText(color.upper())
        button.setStyleSheet(
            "QPushButton {"
            f"background-color: {color};"
            "border: 1px solid #444;"
            "padding: 2px 6px;"
            "text-align: center;"
            "}"
        )

    def _load_graph_settings_to_ui(self, profile: ProfileConfig) -> None:
        bg = str(profile.plot_background_color or "#000000")
        grid = str(profile.plot_grid_color or "#2f4f6f")
        self.graph_bg_btn.setProperty("color_hex", bg)
        self.graph_grid_color_btn.setProperty("color_hex", grid)
        self._apply_color_button_style(self.graph_bg_btn, bg)
        self._apply_color_button_style(self.graph_grid_color_btn, grid)
        self.graph_grid_alpha_spin.setValue(max(0, min(100, int(profile.plot_grid_alpha))))
        self.graph_grid_x_checkbox.setChecked(bool(profile.plot_grid_x))
        self.graph_grid_y_checkbox.setChecked(bool(profile.plot_grid_y))

    def _reset_graph_settings_ui(self) -> None:
        self.graph_bg_btn.setProperty("color_hex", "#000000")
        self.graph_grid_color_btn.setProperty("color_hex", "#2f4f6f")
        self._apply_color_button_style(self.graph_bg_btn, "#000000")
        self._apply_color_button_style(self.graph_grid_color_btn, "#2f4f6f")
        self.graph_grid_alpha_spin.setValue(25)
        self.graph_grid_x_checkbox.setChecked(True)
        self.graph_grid_y_checkbox.setChecked(True)
        self._apply_graph_settings_from_ui()

    def _pick_graph_color(self, target: str) -> None:
        if target == "background":
            button = self.graph_bg_btn
            title = 'Выбор цвета фона графика'
        else:
            button = self.graph_grid_color_btn
            title = 'Выбор цвета сетки'
        current = QColor(str(button.property("color_hex") or "#000000"))
        chosen = QColorDialog.getColor(current, self, title)
        if not chosen.isValid():
            return
        color = chosen.name()
        button.setProperty("color_hex", color)
        self._apply_color_button_style(button, color)
        self._apply_graph_settings_from_ui()

    def _apply_graph_settings_from_ui(self) -> None:
        bg = str(self.graph_bg_btn.property("color_hex") or "#000000")
        grid = str(self.graph_grid_color_btn.property("color_hex") or "#2f4f6f")
        alpha = max(0, min(100, int(self.graph_grid_alpha_spin.value())))
        grid_x = bool(self.graph_grid_x_checkbox.isChecked())
        grid_y = bool(self.graph_grid_y_checkbox.isChecked())

        self.current_profile.plot_background_color = bg
        self.current_profile.plot_grid_color = grid
        self.current_profile.plot_grid_alpha = alpha
        self.current_profile.plot_grid_x = grid_x
        self.current_profile.plot_grid_y = grid_y

        self.chart.set_visual_settings(
            background_color=bg,
            grid_color=grid,
            grid_alpha=alpha / 100.0,
            grid_x=grid_x,
            grid_y=grid_y,
        )

    @staticmethod
    def _apply_color_swatch_style(button: QPushButton, color: str) -> None:
        button.setText("")
        button.setMinimumWidth(1)
        button.setMaximumWidth(16777215)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        button.setStyleSheet(
            "QPushButton {"
            f"background-color: {color};"
            "border: 1px solid #666;"
            "border-radius: 2px;"
            "padding: 0px;"
            "}"
        )

    def _make_color_button(self, color: str) -> QPushButton:
        button = QPushButton()
        button.setProperty("color_hex", color)
        self._apply_color_button_style(button, color)
        button.clicked.connect(self._on_color_button_clicked)
        return button

    def _on_values_header_clicked(self, column: int) -> None:
        if self._values_header_sort_column == int(column):
            self._values_header_sort_desc = not self._values_header_sort_desc
        else:
            self._values_header_sort_column = int(column)
            self._values_header_sort_desc = False
        order = Qt.SortOrder.DescendingOrder if self._values_header_sort_desc else Qt.SortOrder.AscendingOrder
        self.values_table.horizontalHeader().setSortIndicator(int(column), order)
        if self._last_values_rows:
            self._update_values_table(self._last_values_rows, False)
        self._mark_config_dirty()

    @staticmethod
    def _values_header_sort_key(row: dict, column: int):
        if column == 0:
            return int(bool(row.get("enabled", True)))
        if column == 1:
            return str(row.get("name", "")).lower()
        if column == 2:
            return int(row.get("axis_index", 1))
        if column == 3:
            value = row.get("value")
            return float("-inf") if value is None else float(value)
        if column == 4:
            ts = row.get("ts")
            return float("-inf") if ts is None else float(ts)
        if column == 5:
            return str(row.get("mode", "")).lower()
        if column == 6:
            return str(row.get("color", "")).lower()
        return str(row.get("name", "")).lower()

    def _sorted_values_rows(self, rows: list[dict]) -> list[dict]:
        if self._values_header_sort_column is not None:
            col = int(self._values_header_sort_column)
            reverse = bool(self._values_header_sort_desc)
            return sorted(rows, key=lambda row: self._values_header_sort_key(row, col), reverse=reverse)
        return sorted(rows, key=lambda row: str(row.get("name", "")).lower())

    def _on_color_button_clicked(self) -> None:
        button = self.sender()
        if not isinstance(button, QPushButton):
            return
        current = QColor(str(button.property("color_hex") or "#1f77b4"))
        chosen = QColorDialog.getColor(current, self, 'Выбор цвета сигнала')
        if not chosen.isValid():
            return
        color = chosen.name()
        button.setProperty("color_hex", color)
        self._apply_color_button_style(button, color)

    @staticmethod
    def _parse_address_and_bit(address_text: str, fallback_bit: int = 0) -> tuple[int, int]:
        text = str(address_text or "").strip()
        if not text:
            return 0, max(0, min(15, int(fallback_bit)))

        if "." in text:
            left, right = text.split(".", 1)
            try:
                address = int(left.strip())
            except ValueError:
                address = 0
            try:
                bit = int(right.strip())
            except ValueError:
                bit = fallback_bit
            return address, max(0, min(15, int(bit)))

        try:
            address = int(text)
        except ValueError:
            address = 0
        return address, max(0, min(15, int(fallback_bit)))

    def _store_ui_to_profile(self, profile: ProfileConfig, ensure_signal_minimum: bool = False) -> None:
        profile.name = self.profile_name_edit.text().strip() or profile.name
        profile.ip = self.ip_edit.text().strip() or "127.0.0.1"
        profile.port = self.port_spin.value()
        profile.unit_id = self.unit_id_spin.value()
        profile.poll_interval_ms = self.poll_interval_spin.value()
        profile.render_interval_ms = self.render_interval_spin.value()
        profile.render_chart_enabled = bool(self.render_chart_checkbox.isChecked())
        profile.archive_interval_ms = self.archive_interval_spin.value()
        profile.archive_on_change_only = bool(self.archive_on_change_checkbox.isChecked())
        profile.archive_deadband = max(0.0, float(self.archive_deadband_spin.value()))
        profile.archive_keepalive_s = max(0, int(self.archive_keepalive_spin.value()))
        profile.archive_retention_days = self.archive_retention_days_spin.value()
        profile.archive_to_db = bool(self.archive_to_db_checkbox.isChecked())
        profile.work_mode = str(self.mode_combo.currentData() or "online")
        profile.timeout_s = float(self.timeout_spin.value())
        profile.retries = self.retries_spin.value()
        profile.address_offset = self.address_offset_spin.value()
        profile.recorder_api_enabled = bool(self.recorder_api_enabled_checkbox.isChecked())
        profile.recorder_api_host = self.recorder_api_host_edit.text().strip() or "0.0.0.0"
        profile.recorder_api_port = int(self.recorder_api_port_spin.value())
        profile.recorder_api_token = self.recorder_api_token_edit.text().strip()
        profile.plot_background_color = str(self.graph_bg_btn.property("color_hex") or profile.plot_background_color)
        profile.plot_grid_color = str(self.graph_grid_color_btn.property("color_hex") or profile.plot_grid_color)
        profile.plot_grid_alpha = max(0, min(100, int(self.graph_grid_alpha_spin.value())))
        profile.plot_grid_x = bool(self.graph_grid_x_checkbox.isChecked())
        profile.plot_grid_y = bool(self.graph_grid_y_checkbox.isChecked())
        profile.tags_bulk_start_address = max(0, int(self.tags_start_addr_spin.value()))
        profile.tags_bulk_count = max(1, int(self.tags_count_spin.value()))
        profile.tags_bulk_step = max(1, int(self.tags_step_spin.value()))
        profile.tags_bulk_register_type = str(self.tags_reg_combo.currentData() or "holding")
        profile.tags_bulk_data_type = str(self.tags_type_combo.currentData() or "int16")
        profile.tags_bulk_float_order = str(self.tags_float_order_combo.currentData() or "ABCD")
        profile.tags_poll_interval_ms = max(100, int(self.tags_poll_interval_spin.value()))
        if not profile.db_path:
            profile.db_path = str(DEFAULT_DB_PATH)
        self._store_signal_table_to_profile(profile, ensure_signal_minimum=ensure_signal_minimum)
        profile.recorder_sources = self._collect_sources_table()
        self._normalize_profile_signal_sources(profile)
        profile.tag_tabs = self._collect_tags_tabs()
        profile.tags = self._clone_tag_list(profile.tag_tabs[0].tags) if profile.tag_tabs else []
        self._capture_ui_state(profile)

    def _connection_config_from_profile(self, profile: ProfileConfig) -> dict:
        return {
            "name": str(profile.name or "Profile"),
            "ip": str(profile.ip or "127.0.0.1"),
            "port": int(profile.port),
            "unit_id": int(profile.unit_id),
            "poll_interval_ms": int(profile.poll_interval_ms),
            "render_interval_ms": int(profile.render_interval_ms),
            "render_chart_enabled": bool(profile.render_chart_enabled),
            "archive_interval_ms": int(profile.archive_interval_ms),
            "archive_on_change_only": bool(profile.archive_on_change_only),
            "archive_deadband": float(profile.archive_deadband),
            "archive_keepalive_s": int(profile.archive_keepalive_s),
            "archive_retention_days": int(profile.archive_retention_days),
            "timeout_s": float(profile.timeout_s),
            "retries": int(profile.retries),
            "address_offset": int(profile.address_offset),
            "recorder_api_enabled": bool(profile.recorder_api_enabled),
            "recorder_api_host": str(profile.recorder_api_host or "0.0.0.0"),
            "recorder_api_port": int(profile.recorder_api_port),
            "recorder_api_token": str(profile.recorder_api_token or ""),
            "db_path": str(profile.db_path or str(DEFAULT_DB_PATH)),
        }

    def _apply_connection_config_to_profile(self, profile: ProfileConfig, payload: dict) -> None:
        if not isinstance(payload, dict):
            return

        def int_or(default_value: int, key: str, minimum: int | None = None, maximum: int | None = None) -> int:
            try:
                value = int(payload.get(key, default_value))
            except (TypeError, ValueError):
                value = int(default_value)
            if minimum is not None:
                value = max(minimum, value)
            if maximum is not None:
                value = min(maximum, value)
            return value

        def float_or(default_value: float, key: str, minimum: float | None = None, maximum: float | None = None) -> float:
            try:
                value = float(payload.get(key, default_value))
            except (TypeError, ValueError):
                value = float(default_value)
            if minimum is not None:
                value = max(minimum, value)
            if maximum is not None:
                value = min(maximum, value)
            return value

        name = str(payload.get("name", profile.name)).strip()
        if name:
            profile.name = name
        profile.ip = str(payload.get("ip", profile.ip) or "127.0.0.1").strip() or "127.0.0.1"
        profile.port = int_or(profile.port, "port", minimum=1, maximum=65535)
        profile.unit_id = int_or(profile.unit_id, "unit_id", minimum=1, maximum=255)
        profile.poll_interval_ms = int_or(profile.poll_interval_ms, "poll_interval_ms", minimum=50, maximum=60000)
        profile.render_interval_ms = int_or(
            profile.render_interval_ms,
            "render_interval_ms",
            minimum=50,
            maximum=5000,
        )
        profile.render_chart_enabled = bool(payload.get("render_chart_enabled", profile.render_chart_enabled))
        profile.archive_interval_ms = int_or(
            profile.archive_interval_ms,
            "archive_interval_ms",
            minimum=50,
            maximum=600000,
        )
        profile.archive_on_change_only = bool(payload.get("archive_on_change_only", profile.archive_on_change_only))
        profile.archive_deadband = float_or(profile.archive_deadband, "archive_deadband", minimum=0.0, maximum=1.0e9)
        profile.archive_keepalive_s = int_or(
            profile.archive_keepalive_s,
            "archive_keepalive_s",
            minimum=0,
            maximum=86400,
        )
        profile.archive_retention_days = int_or(
            profile.archive_retention_days,
            "archive_retention_days",
            minimum=0,
            maximum=3650,
        )
        profile.timeout_s = float_or(profile.timeout_s, "timeout_s", minimum=0.1, maximum=30.0)
        profile.retries = int_or(profile.retries, "retries", minimum=0, maximum=10)
        profile.address_offset = int_or(profile.address_offset, "address_offset", minimum=-10, maximum=10)
        profile.recorder_api_enabled = bool(payload.get("recorder_api_enabled", profile.recorder_api_enabled))
        profile.recorder_api_host = str(payload.get("recorder_api_host", profile.recorder_api_host) or "0.0.0.0").strip() or "0.0.0.0"
        profile.recorder_api_port = int_or(profile.recorder_api_port, "recorder_api_port", minimum=1, maximum=65535)
        profile.recorder_api_token = str(payload.get("recorder_api_token", profile.recorder_api_token) or "")
        db_path = str(payload.get("db_path", profile.db_path)).strip()
        profile.db_path = db_path or str(DEFAULT_DB_PATH)

    def _load_connection_fields_to_ui(self, profile: ProfileConfig) -> None:
        self.profile_name_edit.setText(profile.name)
        self.ip_edit.setText(profile.ip)
        self.port_spin.setValue(profile.port)
        self.unit_id_spin.setValue(profile.unit_id)
        self.poll_interval_spin.setValue(profile.poll_interval_ms)
        self.render_interval_spin.setValue(max(50, int(profile.render_interval_ms)))
        self.render_chart_checkbox.blockSignals(True)
        self.render_chart_checkbox.setChecked(bool(profile.render_chart_enabled))
        self.render_chart_checkbox.blockSignals(False)
        self.archive_interval_spin.setValue(profile.archive_interval_ms)
        self.archive_on_change_checkbox.setChecked(bool(profile.archive_on_change_only))
        self.archive_deadband_spin.setValue(max(0.0, float(profile.archive_deadband)))
        self.archive_keepalive_spin.setValue(max(0, int(profile.archive_keepalive_s)))
        self.archive_retention_days_spin.setValue(max(0, int(profile.archive_retention_days)))
        self.timeout_spin.setValue(profile.timeout_s)
        self.retries_spin.setValue(profile.retries)
        self.address_offset_spin.setValue(profile.address_offset)
        self.recorder_api_enabled_checkbox.setChecked(bool(profile.recorder_api_enabled))
        self.recorder_api_host_edit.setText(str(profile.recorder_api_host or "0.0.0.0"))
        self.recorder_api_port_spin.setValue(max(1, min(65535, int(profile.recorder_api_port))))
        self.recorder_api_token_edit.setText(str(profile.recorder_api_token or ""))

    def _save_connection_config_to_file(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        default_name = f"{self.current_profile.name}_connection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        default_path = str(Path.cwd() / default_name)
        file_path, _selected = QFileDialog.getSaveFileName(
            self,
            'Экспорт подключения',
            default_path,
            "Connection config (*.json);;All files (*.*)",
        )
        if not file_path:
            return

        payload = {
            "format": CONNECTION_CONFIG_FORMAT,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "profile_id": self.current_profile.id,
            "profile_name": self.current_profile.name,
            "connection_config": self._connection_config_from_profile(self.current_profile),
        }
        try:
            atomic_write_text(Path(file_path), json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: РїРѕРґРєР»СЋС‡РµРЅРёРµ СЌРєСЃРїРѕСЂС‚РёСЂРѕРІР°РЅРѕ -> {file_path}")
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° СЌРєСЃРїРѕСЂС‚Р° РїРѕРґРєР»СЋС‡РµРЅРёСЏ: {exc}")

    def _load_connection_config_from_file(self) -> None:
        file_path, _selected = QFileDialog.getOpenFileName(
            self,
            'Импорт подключения',
            str(Path.cwd()),
            "Connection config (*.json);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ РїРѕРґРєР»СЋС‡РµРЅРёСЏ: {exc}")
            return

        if not isinstance(payload, dict):
            self.status_label.setText('Ошибка: неверный формат файла подключения')
            return
        if str(payload.get("format", "")) == CONNECTION_CONFIG_FORMAT:
            config_payload = payload.get("connection_config")
        else:
            config_payload = payload

        if not isinstance(config_payload, dict):
            self.status_label.setText('Ошибка: в файле нет валидной конфигурации подключения')
            return

        self._apply_connection_config_to_profile(self.current_profile, config_payload)
        self._updating_ui = True
        self._load_connection_fields_to_ui(self.current_profile)
        self._updating_ui = False
        combo_index = self.profile_combo.currentIndex()
        if combo_index >= 0:
            self.profile_combo.setItemText(combo_index, self.current_profile.name)
        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: РїРѕРґРєР»СЋС‡РµРЅРёРµ РёРјРїРѕСЂС‚РёСЂРѕРІР°РЅРѕ <- {file_path}")

    def _on_clear_archive_db_clicked(self, _checked: bool = False) -> None:
        answer = QMessageBox.question(
            self,
            'Подтверждение очистки архива',
            'Вы уверены? Это удалит все данные архива из базы.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        mode = str(self.mode_combo.currentData() or "online")
        was_running = bool(self._worker is not None and self._worker.isRunning())
        if was_running:
            self._stop_worker()

        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        if not db_path.exists():
            self.status_label.setText('Статус: архивная БД отсутствует, очищать нечего')
            if was_running and mode == "online":
                self._start_worker()
            return

        try:
            with self._busy('Статус: очистка архивной БД...'):
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute("PRAGMA busy_timeout=5000;")
                    tables = {
                        str(row[0])
                        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        if row and row[0]
                    }
                    if "samples" in tables:
                        conn.execute("DELETE FROM samples")
                    if "connection_events" in tables:
                        conn.execute("DELETE FROM connection_events")
                    if "signals_meta" in tables:
                        conn.execute("DELETE FROM signals_meta")
                    if "sqlite_sequence" in tables:
                        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('samples', 'connection_events', 'signals_meta')")
                    conn.commit()
                    conn.execute("VACUUM")
                finally:
                    conn.close()
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° РѕС‡РёСЃС‚РєРё Р‘Р”: {exc}")
            if was_running and mode == "online":
                self._start_worker()
            return

        self._connection_events = []
        self._last_connection_state = None
        self._last_archive_ts = 0.0
        self._last_retention_cleanup_ts = 0.0
        self._archive_last_values = {}
        self._archive_last_written_ts = {}
        self.chart.set_connection_events([])
        self.chart.clear_data()
        self._update_runtime_status_panel()
        self.status_label.setText('Статус: архивная БД очищена')

        if was_running and mode == "online":
            self._start_worker()

    def _fill_signal_table(self, signals: list[SignalConfig], source_id: str | None = None) -> None:
        selected_source_id = str(source_id or self._active_signal_source_id or "local")
        self.signal_table.setRowCount(0)
        for signal in signals:
            sid = str(getattr(signal, "source_id", "local") or "local")
            if sid != selected_source_id:
                continue
            self._add_signal_row(signal)
        self._fit_signal_table_columns(initial=True)

    def _fit_signal_table_columns(self, initial: bool = False) -> None:
        header = self.signal_table.horizontalHeader()
        header.setStretchLastSection(False)
        self.signal_table.resizeColumnsToContents()
        for col in range(self.signal_table.columnCount()):
            width = self.signal_table.columnWidth(col)
            self.signal_table.setColumnWidth(col, min(max(48, width + 12), 320))
        for col in range(self.signal_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        if isinstance(getattr(self, "current_profile", None), ProfileConfig):
            self._apply_saved_table_column_widths(self.current_profile, "signal_table", self.signal_table)

    def _show_signal_columns_menu(self) -> None:
        menu = QMenu(self)
        headers = [
            str(self.signal_table.horizontalHeaderItem(col).text())
            if self.signal_table.horizontalHeaderItem(col) is not None
            else f"РљРѕР»РѕРЅРєР° {col + 1}"
            for col in range(self.signal_table.columnCount())
        ]
        for col, title in enumerate(headers):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(not self.signal_table.isColumnHidden(col))
            action.toggled.connect(lambda checked, c=col: self.signal_table.setColumnHidden(c, not bool(checked)))
            self._signal_column_actions[col] = action

        menu.addSeparator()
        fit_action = menu.addAction('Подогнать ширину по содержимому')
        fit_action.triggered.connect(lambda _checked=False: self._fit_signal_table_columns(initial=False))
        menu.exec(self.signal_columns_btn.mapToGlobal(self.signal_columns_btn.rect().bottomLeft()))

    def _add_signal_row(self, signal: SignalConfig | None = None) -> None:
        if not isinstance(signal, SignalConfig):
            signal = None

        row = self.signal_table.rowCount()
        self.signal_table.insertRow(row)
        selected_source_id = self._current_signal_source_id()

        if signal is None:
            signal = SignalConfig(
                name=f"Signal {row + 1}",
                color=DEFAULT_COLORS[row % len(DEFAULT_COLORS)],
                enabled=False,
                source_id=selected_source_id,
            )

        signal_source_id = str(getattr(signal, "source_id", "local") or "local").strip() or "local"
        signal_remote_tag_id = str(getattr(signal, "remote_tag_id", "") or "").strip()
        if signal_source_id == "local":
            signal_remote_tag_id = ""

        enabled_item = QTableWidgetItem()
        enabled_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        enabled_item.setCheckState(Qt.CheckState.Checked if signal.enabled else Qt.CheckState.Unchecked)
        self.signal_table.setItem(row, 0, enabled_item)

        name_item = QTableWidgetItem(signal.name)
        name_item.setData(ROLE_SIGNAL_ID, signal.id)
        name_item.setData(ROLE_SIGNAL_SOURCE_ID, signal_source_id)
        name_item.setData(ROLE_SIGNAL_REMOTE_TAG_ID, signal_remote_tag_id)
        self.signal_table.setItem(row, 1, name_item)

        addr_text = f"{signal.address}.{signal.bit_index}" if signal.data_type == "bool" else str(signal.address)
        self.signal_table.setItem(row, 2, QTableWidgetItem(addr_text))

        reg_combo = QComboBox()
        reg_combo.addItem('Holding (хран.)', "holding")
        reg_combo.addItem('Input (вход.)', "input")
        reg_idx = reg_combo.findData(signal.register_type)
        reg_combo.setCurrentIndex(reg_idx if reg_idx >= 0 else 0)
        self.signal_table.setCellWidget(row, 3, reg_combo)

        type_combo = QComboBox()
        type_combo.addItem('INT16 (знак.)', "int16")
        type_combo.addItem('UINT16 (без знака)', "uint16")
        type_combo.addItem("REAL / FLOAT32", "float32")
        type_combo.addItem('BOOL (бит)', "bool")
        type_idx = type_combo.findData(signal.data_type)
        type_combo.setCurrentIndex(type_idx if type_idx >= 0 else 0)
        self.signal_table.setCellWidget(row, 4, type_combo)

        bit_spin = QSpinBox()
        bit_spin.setRange(0, 15)
        bit_spin.setValue(max(0, min(15, int(signal.bit_index))))
        self.signal_table.setCellWidget(row, 5, bit_spin)

        order_combo = QComboBox()
        order_combo.addItem('ABCD (обычный)', "ABCD")
        order_combo.addItem("BADC (swap bytes)", "BADC")
        order_combo.addItem("CDAB (swap words)", "CDAB")
        order_combo.addItem("DCBA (reverse)", "DCBA")
        ord_idx = order_combo.findData(signal.float_order)
        order_combo.setCurrentIndex(ord_idx if ord_idx >= 0 else 0)
        axis_spin = QSpinBox()
        axis_spin.setRange(1, 64)
        axis_spin.setValue(max(1, int(signal.axis_index)))
        self.signal_table.setCellWidget(row, 6, axis_spin)

        self.signal_table.setCellWidget(row, 7, order_combo)

        self.signal_table.setItem(row, 8, QTableWidgetItem(str(signal.scale)))
        self.signal_table.setCellWidget(row, 9, self._make_color_button(signal.color))
        source_label = self._source_label_for_source_id(signal_source_id)
        source_item = QTableWidgetItem(source_label)
        source_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.signal_table.setItem(row, 10, source_item)

        def update_type_widgets() -> None:
            data_type = str(type_combo.currentData() or "int16")
            is_float = data_type == "float32"
            is_bool = data_type == "bool"
            order_combo.setEnabled(is_float)
            bit_spin.setEnabled(is_bool)
            if not is_bool:
                bit_spin.setValue(0)

        type_combo.currentIndexChanged.connect(update_type_widgets)
        update_type_widgets()

    def _on_add_signal_clicked(self, _checked: bool = False) -> None:
        self._add_signal_row()
        self._fit_signal_table_columns(initial=False)
        self._apply_current_profile()

    def _on_signals_bulk_type_changed(self, _index: int) -> None:
        data_type = str(self.signals_type_combo.currentData() or "int16")
        is_float = data_type == "float32"
        self.signals_float_order_combo.setEnabled(is_float)
        if not is_float:
            idx = self.signals_float_order_combo.findData("ABCD")
            self.signals_float_order_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_add_signal_range_clicked(self, _checked: bool = False) -> None:
        start_address = int(self.signals_start_addr_spin.value())
        count = int(self.signals_count_spin.value())
        step = int(self.signals_step_spin.value())
        register_type = str(self.signals_reg_combo.currentData() or "holding")
        data_type = str(self.signals_type_combo.currentData() or "int16")
        float_order = str(self.signals_float_order_combo.currentData() or "ABCD")
        axis_index = max(1, int(self.signals_axis_spin.value()))
        selected_source_id = self._current_signal_source_id()

        for i in range(count):
            address = start_address + i * step
            row = self.signal_table.rowCount()
            signal = SignalConfig(
                name=f"Signal {row + 1}",
                address=address,
                register_type=register_type,
                data_type=data_type,
                bit_index=0,
                axis_index=axis_index,
                float_order=float_order if data_type == "float32" else "ABCD",
                scale=1.0,
                unit="",
                color=DEFAULT_COLORS[row % len(DEFAULT_COLORS)],
                enabled=False,
                source_id=selected_source_id,
            )
            self._add_signal_row(signal)

        self._fit_signal_table_columns(initial=False)
        self._apply_current_profile()
        self.status_label.setText(
            f"РЎС‚Р°С‚СѓСЃ: РґРѕР±Р°РІР»РµРЅРѕ {count} СЃРёРіРЅР°Р»РѕРІ (СЃС‚Р°СЂС‚={start_address}, С€Р°Рі={step}, С‚РёРї={data_type})"
        )

    def _on_signal_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_ui:
            return
        row = item.row()
        name_item = self.signal_table.item(row, 1)
        if name_item is None:
            return
        signal_id = str(name_item.data(ROLE_SIGNAL_ID) or "")
        if not signal_id:
            return

        # "Р’РєР»" changed.
        if item.column() == 0:
            enabled = item.checkState() == Qt.CheckState.Checked
            self.chart.set_signal_enabled(signal_id, enabled)
            for signal in self.current_profile.signals:
                if signal.id == signal_id:
                    signal.enabled = enabled
                    break
            self._mark_config_dirty()
            return

        # Signal name changed: update chart/legend/values table immediately.
        if item.column() == 1:
            new_name = str(name_item.text() or "").strip()
            if not new_name:
                new_name = f"Signal {row + 1}"
                self._updating_ui = True
                name_item.setText(new_name)
                self._updating_ui = False
            self.chart.set_signal_name(signal_id, new_name)
            for signal in self.current_profile.signals:
                if signal.id == signal_id:
                    signal.name = new_name
                    break
            self._mark_config_dirty()
            return

    def _sync_signal_table_enabled(self, signal_id: str, enabled: bool) -> None:
        for row in range(self.signal_table.rowCount()):
            name_item = self.signal_table.item(row, 1)
            if name_item is None:
                continue
            row_signal_id = str(name_item.data(ROLE_SIGNAL_ID) or "")
            if row_signal_id != signal_id:
                continue
            enabled_item = self.signal_table.item(row, 0)
            if enabled_item is None:
                break
            self._updating_ui = True
            enabled_item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self._updating_ui = False
            break

    def _sync_signal_table_color(self, signal_id: str, color: str) -> None:
        for row in range(self.signal_table.rowCount()):
            name_item = self.signal_table.item(row, 1)
            if name_item is None:
                continue
            row_signal_id = str(name_item.data(ROLE_SIGNAL_ID) or "")
            if row_signal_id != signal_id:
                continue
            color_btn = self.signal_table.cellWidget(row, 9)
            if isinstance(color_btn, QPushButton):
                color_btn.setProperty("color_hex", color)
                self._apply_color_button_style(color_btn, color)
            break

    def _on_values_visibility_toggled(self, signal_id: str, checked: bool) -> None:
        if self._updating_values_table:
            return
        enabled = bool(checked)
        self.chart.set_signal_enabled(signal_id, enabled)
        self._sync_signal_table_enabled(signal_id, enabled)
        self._mark_config_dirty()

    def _on_values_color_clicked(self, signal_id: str) -> None:
        current = QColor(self.chart.get_signal_color(signal_id))
        chosen = QColorDialog.getColor(current, self, 'Выбор цвета сигнала')
        if not chosen.isValid():
            return
        color = chosen.name()
        self.chart.set_signal_color(signal_id, color)
        self._sync_signal_table_color(signal_id, color)
        self._mark_config_dirty()

    def _selected_values_rows(self) -> list[int]:
        rows = sorted({idx.row() for idx in self.values_table.selectedIndexes() if idx.row() >= 0})
        if rows:
            return rows
        current_row = int(self.values_table.currentRow())
        if current_row >= 0:
            return [current_row]
        return []

    def _selected_values_signal_ids(self) -> list[str]:
        signal_ids: list[str] = []
        seen: set[str] = set()
        for row in self._selected_values_rows():
            checkbox = self.values_table.cellWidget(row, 0)
            if not isinstance(checkbox, QCheckBox):
                continue
            signal_id = str(checkbox.property("signal_id") or "").strip()
            if not signal_id or signal_id in seen:
                continue
            seen.add(signal_id)
            signal_ids.append(signal_id)
        return signal_ids

    def _on_chart_signals_dropped(self, payload: object) -> None:
        signal_ids = [str(item).strip() for item in list(payload or []) if str(item).strip()]
        if not signal_ids:
            return
        unique_ids: list[str] = []
        seen: set[str] = set()
        for signal_id in signal_ids:
            if signal_id in seen:
                continue
            seen.add(signal_id)
            unique_ids.append(signal_id)
        if not unique_ids:
            return

        id_set = set(unique_ids)
        changed = False
        for signal in self.current_profile.signals:
            sid = str(signal.id or "")
            if sid not in id_set:
                continue
            if not bool(signal.enabled):
                signal.enabled = True
                changed = True

        self.chart.set_signals_enabled(unique_ids, True)
        for signal_id in unique_ids:
            self._sync_signal_table_enabled(signal_id, True)

        if changed:
            self._mark_config_dirty()
        self.status_label.setText(f"Статус: добавлено на график сигналов ({len(unique_ids)})")

    def _set_values_signals_visibility(self, signal_ids: list[str], visible: bool) -> None:
        visible_state = bool(visible)
        unique_ids = [sid for sid in signal_ids if str(sid).strip()]
        if not unique_ids:
            return
        unique_set = {str(sid) for sid in unique_ids}
        changed = False

        for signal in self.current_profile.signals:
            sid = str(signal.id or "")
            if sid not in unique_set:
                continue
            if bool(signal.enabled) != visible_state:
                changed = True
            signal.enabled = visible_state

        self.chart.set_signals_enabled(unique_ids, visible_state)
        for signal_id in unique_ids:
            self._sync_signal_table_enabled(signal_id, visible_state)

        if changed:
            self._mark_config_dirty()

        action = "включено" if visible_state else "выключено"
        self.status_label.setText(f"Статус: отображение {action} для выбранных сигналов ({len(unique_set)})")

    def _on_values_table_context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        index = self.values_table.indexAt(pos)
        if index.isValid():
            row = int(index.row())
            selected_rows = set(self._selected_values_rows())
            if row not in selected_rows:
                self.values_table.clearSelection()
                self.values_table.selectRow(row)
                self.values_table.setCurrentCell(row, 1 if self.values_table.columnCount() > 1 else 0)

        selected_rows = self._selected_values_rows()
        signal_ids = self._selected_values_signal_ids()
        if not selected_rows or not signal_ids:
            return

        has_visible = False
        has_hidden = False
        for row in selected_rows:
            checkbox = self.values_table.cellWidget(row, 0)
            if not isinstance(checkbox, QCheckBox):
                continue
            if bool(checkbox.isChecked()):
                has_visible = True
            else:
                has_hidden = True

        menu = QMenu(self)
        show_action = menu.addAction("Показать выбранные")
        hide_action = menu.addAction("Скрыть выбранные")
        show_action.setEnabled(has_hidden)
        hide_action.setEnabled(has_visible)
        chosen = menu.exec(self.values_table.viewport().mapToGlobal(pos))
        if chosen == show_action:
            self._set_values_signals_visibility(signal_ids, True)
            return
        if chosen == hide_action:
            self._set_values_signals_visibility(signal_ids, False)

    def _on_copy_signal_clicked(self, _checked: bool = False) -> None:
        selected = sorted({idx.row() for idx in self.signal_table.selectedIndexes()})
        if not selected:
            current_row = self.signal_table.currentRow()
            if current_row >= 0:
                selected = [current_row]
        if not selected:
            return

        signals = self._collect_signal_table()
        for row in selected:
            if row < 0 or row >= len(signals):
                continue
            source = signals[row]
            duplicated = SignalConfig.from_dict(source.to_dict())
            duplicated.id = str(uuid.uuid4())
            duplicated.name = f"{source.name} (РєРѕРїРёСЏ)"
            self._add_signal_row(duplicated)

        self._fit_signal_table_columns(initial=False)
        self._apply_current_profile()

    def _on_remove_signal_rows_clicked(self, _checked: bool = False) -> None:
        self._remove_selected_signal_rows()
        self._fit_signal_table_columns(initial=False)
        self._apply_current_profile()

    def _remove_selected_signal_rows(self) -> None:
        selected = sorted({idx.row() for idx in self.signal_table.selectedIndexes()}, reverse=True)
        for row in selected:
            self.signal_table.removeRow(row)

    def _signal_ids_from_signal_table(self) -> set[str]:
        signal_ids: set[str] = set()
        for row in range(self.signal_table.rowCount()):
            name_item = self.signal_table.item(row, 1)
            if name_item is None:
                continue
            signal_id = str(name_item.data(ROLE_SIGNAL_ID) or "").strip()
            if signal_id:
                signal_ids.add(signal_id)
        return signal_ids

    def _delete_signals_archive_history(self, signal_ids: set[str]) -> tuple[int, int]:
        ids = sorted({str(item).strip() for item in signal_ids if str(item).strip()})
        if not ids:
            return 0, 0

        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        if not db_path.exists():
            return 0, 0

        store = ArchiveStore(str(db_path))
        try:
            return store.delete_signals(self.current_profile.id, ids, vacuum=True)
        finally:
            store.close()

    def _on_clear_signals_clicked(self, _checked: bool = False) -> None:
        current_source_id = self._current_signal_source_id()
        has_source_signals = any(
            str(getattr(sig, "source_id", "local") or "local") == current_source_id
            for sig in self.current_profile.signals
        )
        if self.signal_table.rowCount() <= 0 and not has_source_signals:
            return

        answer = QMessageBox.question(
            self,
            'Сигналы графика',
            'Удалить все сигналы текущей вкладки и очистить их архивную историю?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        mode = str(self.mode_combo.currentData() or "online")
        was_running = bool(self._worker is not None and self._worker.isRunning())
        if was_running:
            self._stop_worker()

        self._store_signal_table_to_profile(self.current_profile)
        removed_ids = self._signal_ids_from_signal_table()
        removed_ids.update(
            str(sig.id).strip()
            for sig in self.current_profile.signals
            if str(sig.id).strip() and str(getattr(sig, "source_id", "local") or "local") == current_source_id
        )

        removed_samples = 0
        removed_meta = 0
        cleanup_error: Exception | None = None
        try:
            removed_samples, removed_meta = self._delete_signals_archive_history(removed_ids)
        except Exception as exc:
            cleanup_error = exc

        self.signal_table.setRowCount(0)
        self._fit_signal_table_columns(initial=False)
        self._apply_current_profile(ensure_signal_minimum=False)

        if was_running and mode == "online":
            self._start_worker()

        if cleanup_error is None:
            self.status_label.setText(
                f"РЎС‚Р°С‚СѓСЃ: РІСЃРµ СЃРёРіРЅР°Р»С‹ СѓРґР°Р»РµРЅС‹, РѕС‡РёС‰РµРЅРѕ {removed_samples} С‚РѕС‡РµРє Рё {removed_meta} Р·Р°РїРёСЃРµР№ РјРµС‚Р°РґР°РЅРЅС‹С…"
            )
        else:
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: СЃРёРіРЅР°Р»С‹ СѓРґР°Р»РµРЅС‹, РЅРѕ РѕС‡РёСЃС‚РєР° Р°СЂС…РёРІР° РЅРµ РІС‹РїРѕР»РЅРµРЅР°: {cleanup_error}")

    def _collect_signal_table(self, ensure_signal_minimum: bool = False) -> list[SignalConfig]:
        signals: list[SignalConfig] = []
        seen_ids: set[str] = set()
        for row in range(self.signal_table.rowCount()):
            enabled_item = self.signal_table.item(row, 0)
            name_item = self.signal_table.item(row, 1)
            addr_item = self.signal_table.item(row, 2)
            scale_item = self.signal_table.item(row, 8)
            color_button = self.signal_table.cellWidget(row, 9)
            reg_combo = self.signal_table.cellWidget(row, 3)
            type_combo = self.signal_table.cellWidget(row, 4)
            bit_spin = self.signal_table.cellWidget(row, 5)
            axis_spin = self.signal_table.cellWidget(row, 6)
            order_combo = self.signal_table.cellWidget(row, 7)

            if (
                not isinstance(reg_combo, QComboBox)
                or not isinstance(type_combo, QComboBox)
                or not isinstance(bit_spin, QSpinBox)
                or not isinstance(axis_spin, QSpinBox)
                or not isinstance(order_combo, QComboBox)
                or not isinstance(color_button, QPushButton)
            ):
                continue

            name = (name_item.text().strip() if name_item else "") or f"Signal {row + 1}"
            signal_id = str(name_item.data(ROLE_SIGNAL_ID) or uuid.uuid4()) if name_item else str(uuid.uuid4())
            if signal_id in seen_ids:
                signal_id = str(uuid.uuid4())
                if name_item is not None:
                    name_item.setData(ROLE_SIGNAL_ID, signal_id)
            seen_ids.add(signal_id)
            source_id = str(name_item.data(ROLE_SIGNAL_SOURCE_ID) or "local") if name_item else "local"
            remote_tag_id = str(name_item.data(ROLE_SIGNAL_REMOTE_TAG_ID) or "") if name_item else ""

            address_text = addr_item.text() if addr_item else "0"
            fallback_bit = int(bit_spin.value())
            address, bit_index = self._parse_address_and_bit(address_text, fallback_bit)

            try:
                scale = float(scale_item.text()) if scale_item else 1.0
            except ValueError:
                scale = 1.0

            color = str(color_button.property("color_hex") or DEFAULT_COLORS[row % len(DEFAULT_COLORS)])

            enabled = bool(enabled_item and enabled_item.checkState() == Qt.CheckState.Checked)
            axis_index = max(1, int(axis_spin.value()))
            data_type = str(type_combo.currentData() or "int16")
            float_order = str(order_combo.currentData() or "ABCD")
            if data_type != "float32":
                float_order = "ABCD"
            if data_type != "bool":
                bit_index = 0

            signals.append(
                SignalConfig(
                    id=signal_id,
                    name=name,
                    address=address,
                    register_type=str(reg_combo.currentData() or "holding"),
                    data_type=data_type,
                    bit_index=bit_index,
                    axis_index=axis_index,
                    float_order=float_order,
                    scale=scale,
                    unit="",
                    color=color,
                    enabled=enabled,
                    source_id=source_id,
                    remote_tag_id=remote_tag_id,
                )
            )

        if not signals and ensure_signal_minimum:
            signals.append(SignalConfig(name="Signal 1", address=0))
        return signals

    def _on_profile_changed(self, index: int) -> None:
        if self._updating_ui:
            return

        if index < 0:
            return

        self._store_ui_to_profile(self.current_profile)
        profile_id = self.profile_combo.itemData(index)
        self.current_profile = self._find_profile(str(profile_id))
        self.app_config.active_profile_id = self.current_profile.id
        self._load_profile_to_ui(self.current_profile)

    def _new_profile(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        profile = ProfileConfig(
            name=f"Profile {len(self.app_config.profiles) + 1}",
            db_path=str(DEFAULT_DB_PATH),
            signals=[SignalConfig(name="Signal 1", address=0)],
        )
        self.app_config.profiles.append(profile)
        self.current_profile = profile
        self.app_config.active_profile_id = profile.id
        self._populate_profiles()
        self._load_profile_to_ui(profile)

    def _clone_profile(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        payload = self.current_profile.to_dict()
        cloned = ProfileConfig.from_dict(payload)
        cloned.id = str(uuid.uuid4())
        cloned.name = f"{self.current_profile.name} (copy)"
        cloned.signals = [
            SignalConfig.from_dict({**signal.to_dict(), "id": str(uuid.uuid4())})
            for signal in self.current_profile.signals
        ]
        self.app_config.profiles.append(cloned)
        self.current_profile = cloned
        self.app_config.active_profile_id = cloned.id
        self._populate_profiles()
        self._load_profile_to_ui(cloned)

    def _delete_profile(self) -> None:
        if len(self.app_config.profiles) <= 1:
            QMessageBox.warning(self, APP_NAME, 'Нельзя удалить последний профиль')
            return

        target_id = self.current_profile.id
        self.app_config.profiles = [p for p in self.app_config.profiles if p.id != target_id]
        self.current_profile = self.app_config.profiles[0]
        self.app_config.active_profile_id = self.current_profile.id
        self._populate_profiles()
        self._load_profile_to_ui(self.current_profile)

    def _apply_current_profile(
        self,
        ensure_signal_minimum: bool = False,
        restart_live: bool = True,
        history_span_s: float | None = None,
    ) -> None:
        previous_signals_signature = [signal.to_dict() for signal in self.current_profile.signals]
        self._store_ui_to_profile(self.current_profile, ensure_signal_minimum=ensure_signal_minimum)
        self._apply_active_signal_ids_from_ui_state(self.current_profile)
        current_name = self.current_profile.name
        combo_index = self.profile_combo.currentIndex()
        self.profile_combo.setItemText(combo_index, current_name)
        mode = str(self.current_profile.work_mode or "online")
        signals_changed = previous_signals_signature != [signal.to_dict() for signal in self.current_profile.signals]
        if history_span_s is None and mode == "online":
            history_span_s = self._preferred_live_history_span_s()
        self.chart.configure_signals(self.current_profile.signals)
        self._reset_history_window_cache()
        self.chart.set_visual_settings(
            background_color=self.current_profile.plot_background_color,
            grid_color=self.current_profile.plot_grid_color,
            grid_alpha=max(0.0, min(1.0, float(self.current_profile.plot_grid_alpha) / 100.0)),
            grid_x=self.current_profile.plot_grid_x,
            grid_y=self.current_profile.plot_grid_y,
        )
        # Re-apply persisted runtime view state after chart rebuild to keep
        # manual scale settings (AutoY/Min/Max) and other view preferences.
        self._apply_runtime_view_state(self.current_profile)

        should_restart_live = bool(
            mode == "online"
            and restart_live
            and self._is_live_stream_running()
            and signals_changed
        )
        if should_restart_live:
            self._restart_worker(history_span_s=history_span_s)
        else:
            if self._db_live_timer.isActive():
                self._db_live_timer.setInterval(max(120, min(2000, int(self.current_profile.poll_interval_ms))))
            self._apply_work_mode_ui(mode)
            if mode == "offline" and bool(self.current_profile.render_chart_enabled):
                self._schedule_visible_history_reload(force=True)

        self._save_recorder_config_snapshot(silent=True)
        self._push_profile_to_local_recorder(silent=True)
        if mode == "online":
            self.status_label.setText('Статус: настройки применены')
        else:
            self.status_label.setText('Статус: офлайн настройки применены')

    def _save_config(self) -> None:
        # Save persists current UI/profile state without restarting or
        # reconfiguring runtime objects (to avoid chart/scale reset on save).
        self._store_ui_to_profile(self.current_profile)
        self.app_config.active_profile_id = self.current_profile.id
        self.config_store.save(self.app_config)
        self._save_recorder_config_snapshot(silent=True)
        self._push_profile_to_local_recorder(silent=True)
        self._autosave_timer.stop()
        self._config_dirty = False
        self.status_label.setText('Статус: конфигурация сохранена')

    def _export_chart_image(self, _checked: bool = False) -> None:
        suggested = f"{self.current_profile.name}_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            'Экспорт графика в изображение',
            suggested,
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)",
        )
        if not file_path:
            return

        path = Path(file_path)
        if not path.suffix:
            path = path.with_suffix(".png")
        fmt = path.suffix.lower().replace(".", "")
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in {"png", "jpg", "bmp"}:
            fmt = "png"
            path = path.with_suffix(".png")

        pixmap = self.chart.plot_widget.grab()
        ok = pixmap.save(str(path), fmt.upper())
        if ok:
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: РіСЂР°С„РёРє СЌРєСЃРїРѕСЂС‚РёСЂРѕРІР°РЅ -> {path}")
        else:
            self.status_label.setText('Ошибка: не удалось сохранить изображение графика')

    def _export_chart_csv(self, _checked: bool = False) -> None:
        suggested = f"{self.current_profile.name}_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            'Экспорт данных графика (CSV)',
            suggested,
            "CSV (*.csv)",
        )
        if not file_path:
            return

        payload = self.chart.export_samples_payload(only_enabled=False)
        signals = payload.get("signals") if isinstance(payload, dict) else None
        samples = payload.get("samples") if isinstance(payload, dict) else None
        if not isinstance(signals, list) or not isinstance(samples, dict):
            self.status_label.setText('Ошибка: нет данных для экспорта CSV')
            return

        rows_written = 0
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(
                [
                    "timestamp",
                    "datetime",
                    "signal_id",
                    "signal_name",
                    "axis_index",
                    "value",
                    "visible",
                    "color",
                    "unit",
                ]
            )
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                signal_id = str(signal.get("id", ""))
                points = samples.get(signal_id, [])
                if not isinstance(points, list):
                    continue
                for point in points:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        continue
                    try:
                        ts = float(point[0])
                        value = float(point[1])
                    except (TypeError, ValueError):
                        continue
                    writer.writerow(
                        [
                            f"{ts:.3f}",
                            format_ts_ms(ts),
                            signal_id,
                            str(signal.get("name", signal_id)),
                            int(signal.get("axis_index", 1)),
                            f"{value:.6g}",
                            "1" if bool(signal.get("enabled", True)) else "0",
                            str(signal.get("color", "")),
                            str(signal.get("unit", "")),
                        ]
                    )
                    rows_written += 1

        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: CSV СЌРєСЃРїРѕСЂС‚РёСЂРѕРІР°РЅ ({rows_written} СЃС‚СЂРѕРє) -> {file_path}")

    def _print_chart(self, _checked: bool = False) -> None:
        options = self._open_print_options_dialog()
        if options is None:
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle('Печать графика')
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        stats_page = bool(options.get("stats_page", False))
        detailed_stats = bool(options.get("detailed_stats", False))
        force_grid = bool(options.get("force_grid", True))
        font_preset = str(options.get("font_preset") or "medium")

        stats_rows: list[dict] = []
        stats_start = 0.0
        stats_end = 0.0
        if stats_page:
            stats_rows, stats_start, stats_end = self._collect_print_stats_rows()

        painter = QPainter(printer)
        pages_printed = 1
        page_rect = painter.viewport()
        margin = max(24, int(min(page_rect.width(), page_rect.height()) * 0.02))
        content_rect = page_rect.adjusted(margin, margin, -margin, -margin)
        painter.fillRect(content_rect, QColor("#ffffff"))
        chart_height = content_rect.height()

        chart_rect = QRect(
            content_rect.left(),
            content_rect.top(),
            content_rect.width(),
            chart_height,
        )

        # Build a dedicated offscreen chart for print to avoid distorted fonts
        # and keep A4 readability independent from on-screen widget size.
        pixmap = self._build_print_chart_pixmap(
            chart_rect,
            force_grid=force_grid,
            font_preset=font_preset,
        )
        if pixmap.isNull():
            painter.end()
            self.status_label.setText('Ошибка: график недоступен для печати')
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(chart_rect, pixmap, pixmap.rect())
        legend_items = self._collect_print_legend_items()
        page_box_w, page_box_h = self._measure_print_page_box(painter)
        self._draw_print_legend(painter, chart_rect, legend_items, top_reserved_px=page_box_h + 10)
        self._draw_print_page_number(painter, content_rect, 1)

        if stats_page:
            if printer.newPage():
                pages_printed += self._draw_print_detailed_stats_pages(
                    painter,
                    printer,
                    stats_rows,
                    stats_start,
                    stats_end,
                    start_page_num=2,
                    detailed=detailed_stats,
                )

        painter.end()
        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: РіСЂР°С„РёРє РѕС‚РїСЂР°РІР»РµРЅ РЅР° РїРµС‡Р°С‚СЊ (A4, Р°Р»СЊР±РѕРјРЅР°СЏ), СЃС‚СЂР°РЅРёС†: {pages_printed}")

    def _build_print_chart_pixmap(
        self,
        chart_rect: QRect,
        force_grid: bool,
        font_preset: str = "medium",
    ) -> QPixmap:
        width = max(800, int(chart_rect.width()))
        height = max(500, int(chart_rect.height()))
        ratio = float(width) / max(1.0, float(height))

        # Keep memory bounded but preserve page aspect ratio.
        target_w = min(3200, width)
        target_h = max(600, int(target_w / max(0.2, ratio)))
        if target_h > 2200:
            target_h = 2200
            target_w = max(900, int(target_h * ratio))

        payload = self.chart.export_samples_payload(only_enabled=False)
        signal_meta_raw = payload.get("signals") if isinstance(payload, dict) else None
        samples = payload.get("samples") if isinstance(payload, dict) else None
        if not isinstance(signal_meta_raw, list) or not isinstance(samples, dict):
            return QPixmap()

        signal_meta: dict[str, dict] = {}
        for item in signal_meta_raw:
            if not isinstance(item, dict):
                continue
            signal_id = str(item.get("id") or "")
            if signal_id:
                signal_meta[signal_id] = item

        print_signals = [copy.deepcopy(sig) for sig in self.current_profile.signals]
        for signal in print_signals:
            runtime = signal_meta.get(signal.id)
            if not runtime:
                continue
            signal.enabled = bool(runtime.get("enabled", signal.enabled))
            signal.color = str(runtime.get("color", signal.color))
            try:
                signal.axis_index = max(1, int(runtime.get("axis_index", signal.axis_index)))
            except (TypeError, ValueError):
                pass

        print_chart = MultiAxisChart()
        print_chart.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        print_chart.resize(target_w, target_h)
        print_chart.plot_widget.resize(target_w, target_h)
        print_chart.configure_signals(print_signals)
        print_chart.set_print_time_axis_mode(True)
        # For print X axis labels (date/time ticks) are self-explanatory.
        # Axis title "Р’СЂРµРјСЏ" adds clutter and may overlap at large fonts.
        print_chart.plot_item.getAxis("bottom").setLabel("")
        # Draw a dedicated readable legend via QPainter on the print page.
        # Built-in legend depends on runtime theme and may be unreadable on white paper.
        try:
            print_chart.legend.hide()
        except Exception:
            pass
        print_chart.set_archive_data(samples)
        print_chart.set_connection_events(self._connection_events)
        tick_pt, label_pt, line_width = self._print_style_preset(font_preset)
        print_chart.set_line_width(line_width)

        saved_grid = str(self.current_profile.plot_grid_color or "#2f4f6f")
        saved_alpha = max(0.0, min(1.0, float(self.current_profile.plot_grid_alpha) / 100.0))
        saved_grid_x = bool(self.current_profile.plot_grid_x)
        saved_grid_y = bool(self.current_profile.plot_grid_y)
        print_chart.set_visual_settings(
            background_color="#ffffff",
            grid_color="#7f7f7f" if force_grid else saved_grid,
            grid_alpha=0.48 if force_grid else saved_alpha,
            grid_x=True if force_grid else saved_grid_x,
            grid_y=True if force_grid else saved_grid_y,
        )
        print_chart.apply_print_style(tick_pt=tick_pt, label_pt=label_pt)

        # Mirror the currently visible time range and axis scale settings.
        x_min, x_max = self.chart.current_x_range()
        if x_max <= x_min:
            x_max = time.time()
            x_min = x_max - 60.0
        print_chart.set_auto_x(False)
        print_chart.set_x_range(x_min, x_max)
        print_chart.apply_scale_states(self.chart.export_scale_states())
        print_chart.set_x_range(x_min, x_max)

        QApplication.processEvents()
        pixmap = print_chart.plot_widget.grab()
        print_chart.deleteLater()
        return pixmap

    def _collect_print_legend_items(self) -> list[dict]:
        payload = self.chart.export_samples_payload(only_enabled=False)
        signals = payload.get("signals") if isinstance(payload, dict) else None
        if not isinstance(signals, list):
            return []
        items: list[dict] = []
        for row in signals:
            if not isinstance(row, dict):
                continue
            if not bool(row.get("enabled", True)):
                continue
            name = str(row.get("name") or row.get("id") or "")
            if not name:
                continue
            try:
                axis_index = int(row.get("axis_index", 1))
            except (TypeError, ValueError):
                axis_index = 1
            color = str(row.get("color") or "#1f77b4")
            items.append(
                {
                    "name": f"{name} (С€РєР°Р»Р° {axis_index})",
                    "color": color,
                }
            )
        return items

    def _draw_print_legend(
        self,
        painter: QPainter,
        chart_rect: QRect,
        items: list[dict],
        top_reserved_px: int = 0,
    ) -> None:
        if not items:
            return

        # Keep legend on the right to avoid any overlap with stacked left axes.
        y0 = chart_rect.top() + max(12, int(top_reserved_px))
        max_rows = 12
        columns = max(1, int(math.ceil(len(items) / max_rows)))

        base_font = painter.font()
        legend_font = QFont(base_font)
        size = legend_font.pointSizeF() if legend_font.pointSizeF() > 0 else 9.0
        legend_font.setPointSizeF(max(9.5, size + 1.5))
        painter.setFont(legend_font)
        fm = painter.fontMetrics()
        row_h = max(22, int(fm.height() * 1.4))
        color_w = max(26, int(row_h * 1.3))
        gap = 10
        text_w = 0
        for item in items:
            text_w = max(text_w, fm.horizontalAdvance(str(item.get("name", ""))))
        col_w = color_w + gap + text_w + 12
        rows_used = min(max_rows, len(items))
        box_w = columns * col_w + 16
        box_h = rows_used * row_h + 16

        x0 = chart_rect.right() - box_w - 12
        x0 = max(chart_rect.left() + 12, x0)
        box = QRect(x0, y0, box_w, box_h)
        painter.fillRect(box, QColor(255, 255, 255, 228))
        painter.setPen(QPen(QColor("#222222"), 1))
        painter.drawRect(box.adjusted(0, 0, -1, -1))

        for idx, item in enumerate(items):
            col = idx // max_rows
            row = idx % max_rows
            base_x = box.left() + 8 + col * col_w
            base_y = box.top() + 8 + row * row_h
            y_mid = base_y + row_h // 2

            line_pen = QPen(QColor(str(item.get("color") or "#1f77b4")), 3)
            painter.setPen(line_pen)
            painter.drawLine(base_x, y_mid, base_x + color_w, y_mid)

            painter.setPen(QPen(QColor("#111111"), 1))
            text_rect = QRect(base_x + color_w + gap, base_y, text_w + 6, row_h)
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                str(item.get("name", "")),
            )

        painter.setFont(base_font)

    @staticmethod
    def _print_style_preset(font_preset: str) -> tuple[float, float, float]:
        preset = str(font_preset or "medium").lower()
        if preset == "small":
            # Baseline preset: previous "large" size (readable on A4).
            return 17.0, 19.0, 2.7
        if preset == "large":
            return 25.0, 28.0, 3.4
        # Medium and large are intentionally much bigger for A4 printouts.
        return 21.0, 24.0, 3.0

    def _open_print_options_dialog(self) -> dict | None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Параметры печати')
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        info = QLabel('Формат печати: A4, альбомная ориентация.')
        layout.addWidget(info)

        force_grid_checkbox = QCheckBox('Контрастная сетка для печати')
        force_grid_checkbox.setChecked(True)
        layout.addWidget(force_grid_checkbox)

        font_preset_combo = QComboBox()
        font_preset_combo.addItem('Мелкий', "small")
        font_preset_combo.addItem('Средний', "medium")
        font_preset_combo.addItem('Крупный', "large")
        font_preset_combo.setCurrentIndex(1)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel('Размер шрифта графика:'))
        font_row.addWidget(font_preset_combo, 1)
        layout.addLayout(font_row)

        stats_page_checkbox = QCheckBox('Добавить статистику на отдельной странице')
        stats_page_checkbox.setChecked(False)
        layout.addWidget(stats_page_checkbox)

        details_page_checkbox = QCheckBox('Подробная статистика (расширенная)')
        details_page_checkbox.setChecked(False)
        details_page_checkbox.setEnabled(False)
        stats_page_checkbox.toggled.connect(details_page_checkbox.setEnabled)
        layout.addWidget(details_page_checkbox)

        fixed_source_label = QLabel('Источник статистики: период видимой области графика')
        layout.addWidget(fixed_source_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return {
            "force_grid": bool(force_grid_checkbox.isChecked()),
            "stats_page": bool(stats_page_checkbox.isChecked()),
            "detailed_stats": bool(details_page_checkbox.isChecked()),
            "font_preset": str(font_preset_combo.currentData() or "medium"),
        }

    def _collect_print_stats_rows(self) -> tuple[list[dict], float, float]:
        start_ts, end_ts = self.chart.current_x_range()
        if end_ts < start_ts:
            start_ts, end_ts = end_ts, start_ts
        if end_ts <= start_ts:
            return [], start_ts, end_ts
        rows = self.chart.compute_statistics(start_ts, end_ts)
        return rows, start_ts, end_ts

    def _draw_print_stats_table(
        self,
        painter: QPainter,
        rect: QRect,
        rows: list[dict],
        start_ts: float,
        end_ts: float,
    ) -> None:
        if rect.height() <= 50:
            return

        title = (
            'Статистика (видимая область): '
            f"{format_ts_ms(start_ts) if end_ts > start_ts else '-'}"
            f" .. {format_ts_ms(end_ts) if end_ts > start_ts else '-'}"
        )

        base_font = painter.font()
        table_font = painter.font()
        point_size = table_font.pointSizeF() if table_font.pointSizeF() > 0 else 9.0
        table_font.setPointSizeF(max(8.5, point_size))
        painter.setFont(table_font)
        fm = painter.fontMetrics()
        title_height = max(44, int(fm.height() * 1.8))
        header_h = max(36, int(fm.height() * 1.5))
        row_h = max(34, int(fm.height() * 1.35))
        pad = max(8, int(row_h * 0.22))

        painter.setPen(QColor("#1f1f1f"))
        painter.drawText(
            QRect(rect.left(), rect.top(), rect.width(), title_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title,
        )

        table_rect = QRect(rect.left(), rect.top() + title_height, rect.width(), rect.height() - title_height)
        if table_rect.height() < (header_h + row_h):
            painter.setFont(base_font)
            return

        headers = ['Сигнал', 'Мин', 'Макс', 'Среднее', 'Скорость, ед/с', 'Точек']
        fractions = [0.30, 0.13, 0.13, 0.14, 0.18, 0.12]
        x_positions: list[int] = [table_rect.left()]
        cursor_x = float(table_rect.left())
        for frac in fractions[:-1]:
            cursor_x += table_rect.width() * frac
            x_positions.append(int(round(cursor_x)))
        x_positions.append(table_rect.right() + 1)

        data_area_h = max(0, table_rect.height() - header_h)
        max_rows = max(1, data_area_h // row_h)

        draw_rows = rows[:max_rows] if rows else []
        if not draw_rows:
            draw_rows = [{"name": 'Нет данных', "min": "-", "max": "-", "avg": "-", "speed": "-", "count": "-"}]
        elif len(rows) > max_rows:
            draw_rows = rows[: max_rows - 1]
            draw_rows.append({"name": "...", "min": "...", "max": "...", "avg": "...", "speed": "...", "count": "..."})

        painter.fillRect(QRect(table_rect.left(), table_rect.top(), table_rect.width(), header_h), QColor("#e4e8ee"))
        painter.setPen(QColor("#444444"))
        total_h = header_h + row_h * len(draw_rows)
        painter.drawRect(
            table_rect.left(),
            table_rect.top(),
            table_rect.width() - 1,
            total_h - 1,
        )

        for col in range(1, len(x_positions) - 1):
            painter.drawLine(
                x_positions[col],
                table_rect.top(),
                x_positions[col],
                table_rect.top() + total_h,
            )

        for col, text in enumerate(headers):
            cell_w = max(8, x_positions[col + 1] - x_positions[col] - pad * 2)
            header_text = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, cell_w)
            cell_rect = QRect(
                x_positions[col] + pad,
                table_rect.top(),
                cell_w,
                header_h,
            )
            painter.drawText(
                cell_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                header_text,
            )

        for row_idx, row in enumerate(draw_rows):
            y = table_rect.top() + header_h + row_idx * row_h
            painter.setPen(QColor("#555555"))
            painter.drawLine(table_rect.left(), y + row_h, table_rect.right(), y + row_h)
            speed = row.get("speed")
            values = [
                str(row.get("name", "")),
                f"{float(row.get('min', 0.0)):.6g}" if isinstance(row.get("min"), (int, float)) else str(row.get("min", "-")),
                f"{float(row.get('max', 0.0)):.6g}" if isinstance(row.get("max"), (int, float)) else str(row.get("max", "-")),
                f"{float(row.get('avg', 0.0)):.6g}" if isinstance(row.get("avg"), (int, float)) else str(row.get("avg", "-")),
                "-" if speed is None or not isinstance(speed, (int, float)) else f"{float(speed):.6g}",
                str(int(row.get("count", 0))) if isinstance(row.get("count"), (int, float)) else str(row.get("count", "-")),
            ]
            painter.setPen(QColor("#222222"))
            for col, text in enumerate(values):
                cell_w = max(8, x_positions[col + 1] - x_positions[col] - pad * 2)
                cell_text = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, cell_w)
                align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if col == 0 else Qt.AlignmentFlag.AlignRight)
                cell_rect = QRect(
                    x_positions[col] + pad,
                    y,
                    cell_w,
                    row_h,
                )
                painter.drawText(
                    cell_rect,
                    int(align),
                    cell_text,
                )
        painter.setFont(base_font)

    def _measure_print_page_box(self, painter: QPainter) -> tuple[int, int]:
        base_font = painter.font()
        page_font = painter.font()
        point_size = page_font.pointSizeF() if page_font.pointSizeF() > 0 else 9.0
        page_font.setPointSizeF(max(10.5, point_size + 1.5))
        painter.setFont(page_font)
        fm = painter.fontMetrics()
        text = 'Стр. 999'
        text_w = fm.horizontalAdvance(text)
        box_w = text_w + 18
        box_h = max(24, int(fm.height() * 1.5))
        painter.setFont(base_font)
        return box_w, box_h

    def _draw_print_page_number(self, painter: QPainter, content_rect: QRect, page_number: int) -> None:
        if page_number <= 0:
            return
        base_font = painter.font()
        page_font = painter.font()
        point_size = page_font.pointSizeF() if page_font.pointSizeF() > 0 else 9.0
        page_font.setPointSizeF(max(10.5, point_size + 1.5))
        painter.setFont(page_font)
        fm = painter.fontMetrics()
        text = f"РЎС‚СЂ. {int(page_number)}"
        text_w = fm.horizontalAdvance(text)
        box_w = text_w + 18
        box_h = max(24, int(fm.height() * 1.5))
        x = content_rect.right() - box_w
        y = content_rect.top()
        rect = QRect(x, y, box_w, box_h)
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QColor("#111111"))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.setFont(base_font)

    def _draw_print_detailed_stats_pages(
        self,
        painter: QPainter,
        printer: QPrinter,
        rows: list[dict],
        start_ts: float,
        end_ts: float,
        start_page_num: int = 1,
        detailed: bool = True,
    ) -> int:
        page_count = 0
        source_rows = list(rows)
        if not source_rows:
            source_rows = [
                {
                    "name": 'Нет данных',
                    "min": None,
                    "max": None,
                    "avg": None,
                    "speed": None,
                    "count": None,
                    "span_s": None,
                }
            ]

        row_index = 0
        while row_index < len(source_rows):
            page_count += 1
            page_rect = painter.viewport()
            margin = max(24, int(min(page_rect.width(), page_rect.height()) * 0.02))
            content_rect = page_rect.adjusted(margin, margin, -margin, -margin)
            painter.fillRect(content_rect, QColor("#ffffff"))

            base_font = painter.font()
            body_font = painter.font()
            body_point = body_font.pointSizeF() if body_font.pointSizeF() > 0 else 9.0
            body_font.setPointSizeF(max(8.5, body_point))
            painter.setFont(body_font)
            fm = painter.fontMetrics()
            title_h = max(46, int(fm.height() * 2.0))
            subtitle_h = max(34, int(fm.height() * 1.4))
            header_h = max(34, int(fm.height() * 1.45))
            row_h = max(30, int(fm.height() * 1.25))
            pad = max(7, int(row_h * 0.20))

            painter.setPen(QColor("#1f1f1f"))
            painter.drawText(
                QRect(content_rect.left(), content_rect.top(), content_rect.width(), title_h),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                'Подробная статистика (видимая область)' if detailed else 'Статистика (видимая область)',
            )
            period_text = (
                f"РџРµСЂРёРѕРґ: {format_ts_ms(start_ts) if end_ts > start_ts else '-'}"
                f" .. {format_ts_ms(end_ts) if end_ts > start_ts else '-'}"
            )
            painter.drawText(
                QRect(content_rect.left(), content_rect.top() + title_h, content_rect.width(), subtitle_h),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                period_text,
            )

            table_top = content_rect.top() + title_h + subtitle_h + 8
            table_rect = QRect(content_rect.left(), table_top, content_rect.width(), content_rect.bottom() - table_top + 1)
            if table_rect.height() < header_h + row_h:
                painter.setFont(base_font)
                break

            if detailed:
                headers = ['Сигнал', 'Мин', 'Макс', 'Среднее', 'Размах', 'Δ', 'Скорость, ед/с', 'Точек', 'Интервал, с']
                fractions = [0.21, 0.09, 0.09, 0.10, 0.10, 0.10, 0.11, 0.08, 0.12]
            else:
                headers = ['Сигнал', 'Мин', 'Макс', 'Среднее', 'Скорость, ед/с', 'Точек']
                fractions = [0.34, 0.13, 0.13, 0.16, 0.14, 0.10]
            x_positions: list[int] = [table_rect.left()]
            cursor_x = float(table_rect.left())
            for frac in fractions[:-1]:
                cursor_x += table_rect.width() * frac
                x_positions.append(int(round(cursor_x)))
            x_positions.append(table_rect.right() + 1)

            max_rows = max(1, (table_rect.height() - header_h) // row_h)
            draw_rows = source_rows[row_index : row_index + max_rows]
            row_index += len(draw_rows)

            total_h = header_h + row_h * len(draw_rows)
            painter.fillRect(QRect(table_rect.left(), table_rect.top(), table_rect.width(), header_h), QColor("#e4e8ee"))
            painter.setPen(QColor("#444444"))
            painter.drawRect(table_rect.left(), table_rect.top(), table_rect.width() - 1, total_h - 1)
            for col in range(1, len(x_positions) - 1):
                painter.drawLine(x_positions[col], table_rect.top(), x_positions[col], table_rect.top() + total_h)

            for col, text in enumerate(headers):
                cell_w = max(8, x_positions[col + 1] - x_positions[col] - pad * 2)
                header_text = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, cell_w)
                painter.drawText(
                    QRect(x_positions[col] + pad, table_rect.top(), cell_w, header_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    header_text,
                )

            def _fmt_num(value) -> str:
                if not isinstance(value, (int, float)):
                    return "-"
                return f"{float(value):.6g}"

            for idx, row in enumerate(draw_rows):
                y = table_rect.top() + header_h + idx * row_h
                painter.setPen(QColor("#555555"))
                painter.drawLine(table_rect.left(), y + row_h, table_rect.right(), y + row_h)

                v_min = row.get("min")
                v_max = row.get("max")
                v_avg = row.get("avg")
                v_speed = row.get("speed")
                v_span = row.get("span_s")
                if isinstance(v_min, (int, float)) and isinstance(v_max, (int, float)):
                    v_range = float(v_max) - float(v_min)
                else:
                    v_range = None
                if isinstance(v_speed, (int, float)) and isinstance(v_span, (int, float)):
                    v_delta = float(v_speed) * float(v_span)
                else:
                    v_delta = None

                if detailed:
                    values = [
                        str(row.get("name", "")),
                        _fmt_num(v_min),
                        _fmt_num(v_max),
                        _fmt_num(v_avg),
                        _fmt_num(v_range),
                        _fmt_num(v_delta),
                        _fmt_num(v_speed),
                        str(int(row.get("count", 0))) if isinstance(row.get("count"), (int, float)) else "-",
                        _fmt_num(v_span),
                    ]
                else:
                    values = [
                        str(row.get("name", "")),
                        _fmt_num(v_min),
                        _fmt_num(v_max),
                        _fmt_num(v_avg),
                        _fmt_num(v_speed),
                        str(int(row.get("count", 0))) if isinstance(row.get("count"), (int, float)) else "-",
                    ]
                painter.setPen(QColor("#222222"))
                for col, text in enumerate(values):
                    cell_w = max(8, x_positions[col + 1] - x_positions[col] - pad * 2)
                    cell_text = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, cell_w)
                    align = Qt.AlignmentFlag.AlignVCenter | (
                        Qt.AlignmentFlag.AlignLeft if col == 0 else Qt.AlignmentFlag.AlignRight
                    )
                    painter.drawText(
                        QRect(x_positions[col] + pad, y, cell_w, row_h),
                        int(align),
                        cell_text,
                    )

            self._draw_print_page_number(painter, content_rect, start_page_num + page_count - 1)
            painter.setFont(base_font)

            if row_index < len(source_rows):
                if not printer.newPage():
                    break

        return max(1, page_count)

    def _save_from_signals_window(self) -> None:
        self._save_config()

    def _on_mode_combo_changed(self, _index: int) -> None:
        if self._updating_ui:
            return
        mode = str(self.mode_combo.currentData() or "online")
        self.current_profile.work_mode = mode
        self._apply_work_mode_ui(mode)
        self._update_runtime_status_panel()
        self._sync_mode_actions()

    def _on_render_interval_changed(self, value: int) -> None:
        interval_ms = max(50, int(value))
        if not self._updating_ui:
            self.current_profile.render_interval_ms = interval_ms
        self._apply_render_interval_runtime(interval_ms)
        if not self._updating_ui:
            self._mark_config_dirty()

    def _on_render_chart_toggled(self, checked: bool) -> None:
        enabled = bool(checked)
        self.current_profile.render_chart_enabled = enabled
        if enabled:
            mode = str(self.mode_combo.currentData() or "online")
            if mode == "online":
                auto_x_enabled = bool(self.action_auto_x.isChecked()) if hasattr(self, "action_auto_x") else True
                self._load_recent_online_history_from_db(adjust_x_range=auto_x_enabled, silent=True)
                try:
                    x_left, x_right = self.chart.current_x_range()
                    span_s = max(10.0, float(x_right - x_left))
                except Exception:
                    span_s = 120.0
                self._load_recent_remote_history_from_sources(span_s)
            else:
                self._load_offline_initial_history_from_db(silent=True)
            if self._is_live_stream_running():
                if not self._render_timer.isActive():
                    self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
                    self._render_timer.start()
        else:
            self._pending_render_samples = []
            if self._render_timer.isActive():
                self._render_timer.stop()
            self.chart.set_connection_events([])
            self.chart.clear_data()
            self._reset_history_window_cache()
        self._update_runtime_status_panel()
        if not self._updating_ui:
            self._mark_config_dirty()

    def _apply_render_interval_runtime(self, interval_ms: int | None = None) -> None:
        if interval_ms is None:
            interval_ms = int(getattr(self.current_profile, "render_interval_ms", 200) or 200)
        interval_ms = max(50, int(interval_ms))
        self._render_timer.setInterval(interval_ms)

    def _flush_pending_render_samples(self) -> None:
        if not self._pending_render_samples:
            return
        batch = self._pending_render_samples
        self._pending_render_samples = []
        self.chart.append_samples_batch(batch)

    def _apply_work_mode_ui(self, mode: str) -> None:
        online_mode = mode == "online"
        mode_changed = self._last_applied_work_mode != mode
        self._last_applied_work_mode = mode

        if not online_mode and self._is_live_stream_running():
            self._stop_worker()

        self.archive_to_db_checkbox.setEnabled(online_mode)
        self.action_archive_write_db.setEnabled(online_mode)
        self.action_start.setEnabled(online_mode and not self._is_live_stream_running())
        self.action_stop.setEnabled(online_mode and self._is_live_stream_running())
        self.action_mode_online.setChecked(online_mode)
        self.action_mode_offline.setChecked(not online_mode)

        if online_mode:
            if not self._is_live_stream_running():
                self.status_label.setText('Статус: онлайн режим, ожидание запуска просмотра')
            else:
                self.status_label.setText('Статус: онлайн режим, просмотр из БД запущен')
            if mode_changed:
                self._reset_history_window_cache()
        else:
            self.status_label.setText('Статус: офлайн режим (архив)')
            if mode_changed and bool(self.current_profile.render_chart_enabled):
                self._load_offline_initial_history_from_db(silent=True)
        self._update_runtime_status_panel()
        self._update_recorder_dependent_ui_state()

    @staticmethod
    def _archive_safe_ts(ts: float | None) -> str:
        if ts is None:
            return "-"
        try:
            return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "-"

    @staticmethod
    def _archive_stem(path: Path) -> str:
        name = path.name
        lower = name.lower()
        if lower.endswith(".trend.json"):
            return name[:-11]
        if lower.endswith(".json"):
            return name[:-5]
        return path.stem

    @staticmethod
    def _archive_output_path(file_path: str) -> Path:
        out = Path(file_path)
        lower = out.name.lower()
        if lower.endswith(".trend.json") or lower.endswith(".json"):
            return out
        return out.with_suffix(".trend.json")

    @staticmethod
    def _archive_zip_output_path(file_path: str) -> Path:
        out = Path(file_path)
        lower = out.name.lower()
        if lower.endswith(".trend.zip") or lower.endswith(".zip"):
            return out
        return out.with_suffix(".trend.zip")

    @staticmethod
    def _temp_output_path(target_path: Path, suffix: str = ".tmp") -> Path:
        return target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}{suffix}")

    @staticmethod
    def _is_zip_archive_path(path: Path) -> bool:
        return path.name.lower().endswith(".zip")

    def _archive_part_path(self, output_path: Path, part_index: int, part_count: int) -> Path:
        if part_count <= 1:
            return output_path
        stem = self._archive_stem(output_path)
        digits = max(3, len(str(part_count)))
        name = f"{stem}_part{part_index:0{digits}d}.trend.json"
        return output_path.with_name(name)

    def _collect_archive_parts(self, selected_path: Path) -> list[Path]:
        stem = self._archive_stem(selected_path)
        match = re.match(r"^(?P<prefix>.+)_part(?P<num>\d+)$", stem)
        if not match:
            return [selected_path]

        prefix = match.group("prefix")
        candidates: list[tuple[int, Path]] = []
        for item in selected_path.parent.iterdir():
            if not item.is_file():
                continue
            item_stem = self._archive_stem(item)
            part_match = re.match(rf"^{re.escape(prefix)}_part(?P<num>\d+)$", item_stem)
            if not part_match:
                continue
            try:
                part_num = int(part_match.group("num"))
            except ValueError:
                continue
            candidates.append((part_num, item))

        if not candidates:
            return [selected_path]
        candidates.sort(key=lambda pair: pair[0])
        return [path for _idx, path in candidates]

    @staticmethod
    def _archive_zip_stem(path: Path) -> str:
        name = path.name
        lower = name.lower()
        if lower.endswith(".trend.zip"):
            return name[:-10]
        if lower.endswith(".zip"):
            return name[:-4]
        return path.stem

    def _bundle_entry_name(self, zip_path: Path, part_index: int, part_count: int) -> str:
        stem = self._archive_zip_stem(zip_path)
        if part_count <= 1:
            file_name = f"{stem}.trend.json"
        else:
            digits = max(3, len(str(part_count)))
            file_name = f"{stem}_part{part_index:0{digits}d}.trend.json"
        return f"{ARCHIVE_BUNDLE_DIR}/{file_name}"

    @staticmethod
    def _build_bundle_manifest(
        profile_id: str,
        profile_name: str,
        files: list[str],
        part_count: int,
        period_start_ts: float | None,
        period_end_ts: float | None,
        rows_total: int,
        connection_config_file: str | None = None,
    ) -> dict:
        payload = {
            "format": ARCHIVE_BUNDLE_FORMAT,
            "bundle_id": ARCHIVE_BUNDLE_MAGIC,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "profile_id": profile_id,
            "profile_name": profile_name,
            "content_format": "trend_archive_v2",
            "part_count": int(part_count),
            "rows_total": int(rows_total),
            "files": files,
            "period_start_ts": period_start_ts,
            "period_end_ts": period_end_ts,
            "period_start": MainWindow._archive_safe_ts(period_start_ts),
            "period_end": MainWindow._archive_safe_ts(period_end_ts),
        }
        if connection_config_file:
            payload["connection_config_file"] = connection_config_file
        return payload

    def _export_payload_to_zip(self, output_path: Path, payload: dict) -> int:
        entry_name = self._bundle_entry_name(output_path, 1, 1)
        connection_config = payload.get("connection_config")
        if not isinstance(connection_config, dict):
            connection_config = self._connection_config_from_profile(self.current_profile)
        raw_samples = payload.get("samples")
        rows_total = 0
        if isinstance(raw_samples, dict):
            for points in raw_samples.values():
                if isinstance(points, list):
                    rows_total += len(points)

        period_start_ts = payload.get("period_start_ts")
        period_end_ts = payload.get("period_end_ts")
        try:
            period_start_ts = None if period_start_ts is None else float(period_start_ts)
        except Exception:
            period_start_ts = None
        try:
            period_end_ts = None if period_end_ts is None else float(period_end_ts)
        except Exception:
            period_end_ts = None

        manifest = self._build_bundle_manifest(
            profile_id=str(payload.get("profile_id", self.current_profile.id)),
            profile_name=str(payload.get("profile_name", self.current_profile.name)),
            files=[entry_name],
            part_count=1,
            period_start_ts=period_start_ts,
            period_end_ts=period_end_ts,
            rows_total=rows_total,
            connection_config_file=ARCHIVE_BUNDLE_CONNECTION_CONFIG,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_output = self._temp_output_path(output_path, suffix=".ziptmp")
        try:
            with zipfile.ZipFile(tmp_output, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr(entry_name, json.dumps(payload, ensure_ascii=False, indent=2))
                bundle.writestr(
                    ARCHIVE_BUNDLE_CONNECTION_CONFIG,
                    json.dumps(connection_config, ensure_ascii=False, indent=2),
                )
                bundle.writestr(ARCHIVE_BUNDLE_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            tmp_output.replace(output_path)
        finally:
            if tmp_output.exists():
                try:
                    tmp_output.unlink()
                except Exception:
                    pass
        return 1

    def _export_db_archive_to_zip(
        self,
        db_path: Path,
        output_path: Path,
        start_ts: float,
        end_ts: float,
        chunk_rows: int,
    ) -> tuple[int, int]:
        conn = sqlite3.connect(db_path)
        try:
            count_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
                """,
                (self.current_profile.id, float(start_ts), float(end_ts)),
            ).fetchone()
            total_rows = int((count_row or [0])[0] or 0)
            if total_rows <= 0:
                return 0, 0

            signals = self._build_export_signals(conn, start_ts, end_ts)
            connection_events = self._query_connection_events(conn, start_ts, end_ts)
            connection_config = self._connection_config_from_profile(self.current_profile)
            part_count = max(1, (total_rows + chunk_rows - 1) // chunk_rows)
            payload_signals = [item.to_dict() for item in signals]
            cursor = conn.execute(
                """
                SELECT signal_id, ts, value
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
                ORDER BY ts ASC, id ASC
                """,
                (self.current_profile.id, float(start_ts), float(end_ts)),
            )
            now_iso = datetime.now().isoformat(timespec="seconds")

            part_index = 1
            rows_in_part = 0
            part_min_ts: float | None = None
            part_max_ts: float | None = None
            part_samples: dict[str, list[list[float]]] = {}
            bundle_files: list[str] = []

            output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_output = self._temp_output_path(output_path, suffix=".ziptmp")
            with zipfile.ZipFile(tmp_output, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:

                def flush_part() -> None:
                    nonlocal part_index, rows_in_part, part_min_ts, part_max_ts, part_samples
                    if rows_in_part <= 0:
                        return
                    payload = {
                        "format": "trend_archive_v2",
                        "exported_at": now_iso,
                        "profile_id": self.current_profile.id,
                        "profile_name": self.current_profile.name,
                        "signals": payload_signals,
                        "samples": part_samples,
                        "connection_events": connection_events,
                        "connection_config": connection_config,
                        "period_start_ts": float(start_ts),
                        "period_end_ts": float(end_ts),
                        "period_start": self._archive_safe_ts(start_ts),
                        "period_end": self._archive_safe_ts(end_ts),
                        "part_index": part_index,
                        "part_count": part_count,
                        "rows_total": total_rows,
                        "rows_in_part": rows_in_part,
                        "part_start_ts": part_min_ts,
                        "part_end_ts": part_max_ts,
                        "part_start": self._archive_safe_ts(part_min_ts),
                        "part_end": self._archive_safe_ts(part_max_ts),
                    }
                    entry_name = self._bundle_entry_name(output_path, part_index, part_count)
                    bundle.writestr(entry_name, json.dumps(payload, ensure_ascii=False, indent=2))
                    bundle_files.append(entry_name)
                    part_index += 1
                    rows_in_part = 0
                    part_min_ts = None
                    part_max_ts = None
                    part_samples = {}

                while True:
                    batch = cursor.fetchmany(5000)
                    if not batch:
                        break
                    for signal_id, ts, value in batch:
                        sid = str(signal_id or "")
                        if not sid:
                            continue
                        ts_f = float(ts)
                        val_f = float(value)
                        part_samples.setdefault(sid, []).append([ts_f, val_f])
                        if part_min_ts is None or ts_f < part_min_ts:
                            part_min_ts = ts_f
                        if part_max_ts is None or ts_f > part_max_ts:
                            part_max_ts = ts_f
                        rows_in_part += 1
                        if rows_in_part >= chunk_rows:
                            flush_part()
                flush_part()

                manifest = self._build_bundle_manifest(
                    profile_id=self.current_profile.id,
                    profile_name=self.current_profile.name,
                    files=bundle_files,
                    part_count=len(bundle_files),
                    period_start_ts=float(start_ts),
                    period_end_ts=float(end_ts),
                    rows_total=total_rows,
                    connection_config_file=ARCHIVE_BUNDLE_CONNECTION_CONFIG,
                )
                bundle.writestr(
                    ARCHIVE_BUNDLE_CONNECTION_CONFIG,
                    json.dumps(connection_config, ensure_ascii=False, indent=2),
                )
                bundle.writestr(ARCHIVE_BUNDLE_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            tmp_output.replace(output_path)
            return len(bundle_files), total_rows
        finally:
            try:
                tmp_output_path = locals().get("tmp_output")
                if isinstance(tmp_output_path, Path) and tmp_output_path.exists():
                    tmp_output_path.unlink()
            except Exception:
                pass
            conn.close()

    def _load_payloads_from_zip(self, zip_path: Path) -> tuple[list[dict] | None, dict | None, str | None]:
        try:
            with zipfile.ZipFile(zip_path, mode="r") as bundle:
                names = set(bundle.namelist())
                if ARCHIVE_BUNDLE_MANIFEST not in names:
                    return None, None, 'Ошибка: этот ZIP не является архивом Trend Analyzer (нет манифеста)'

                try:
                    manifest_raw = bundle.read(ARCHIVE_BUNDLE_MANIFEST).decode("utf-8")
                    manifest = json.loads(manifest_raw)
                except Exception as exc:
                    return None, None, f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ РјР°РЅРёС„РµСЃС‚Р° Р°СЂС…РёРІР°: {exc}"

                if not isinstance(manifest, dict):
                    return None, None, 'Ошибка: некорректный манифест архива'
                if str(manifest.get("format", "")) != ARCHIVE_BUNDLE_FORMAT:
                    return None, None, 'Ошибка: неизвестный формат ZIP-архива'
                if str(manifest.get("bundle_id", "")) != ARCHIVE_BUNDLE_MAGIC:
                    return None, None, 'Ошибка: ZIP-архив не принадлежит Trend Analyzer'

                files_raw = manifest.get("files")
                if not isinstance(files_raw, list) or not files_raw:
                    return None, None, 'Ошибка: в ZIP-архиве нет списка файлов данных'

                data_files: list[str] = []
                for item in files_raw:
                    file_name = str(item)
                    if not file_name.startswith(f"{ARCHIVE_BUNDLE_DIR}/"):
                        return None, None, 'Ошибка: неверный путь файла в манифесте архива'
                    if file_name not in names:
                        return None, None, f"РћС€РёР±РєР°: РІ ZIP РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚ С„Р°Р№Р» РґР°РЅРЅС‹С… {file_name}"
                    data_files.append(file_name)

                payloads: list[dict] = []
                for file_name in data_files:
                    try:
                        payload_raw = bundle.read(file_name).decode("utf-8")
                        payload = json.loads(payload_raw)
                    except Exception as exc:
                        return None, None, f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ С„Р°Р№Р»Р° {file_name}: {exc}"
                    if not isinstance(payload, dict):
                        return None, None, f"РћС€РёР±РєР°: РЅРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚ С„Р°Р№Р»Р° {file_name}"
                    payloads.append(payload)

                connection_config: dict | None = None
                config_file = str(manifest.get("connection_config_file") or ARCHIVE_BUNDLE_CONNECTION_CONFIG)
                if config_file in names:
                    try:
                        config_raw = bundle.read(config_file).decode("utf-8")
                        config_payload = json.loads(config_raw)
                        if isinstance(config_payload, dict):
                            connection_config = config_payload
                    except Exception:
                        connection_config = None

                return payloads, connection_config, None
        except zipfile.BadZipFile:
            return None, None, 'Ошибка: поврежденный ZIP-архив'
        except Exception as exc:
            return None, None, f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ ZIP-Р°СЂС…РёРІР°: {exc}"

    def _db_archive_range(self, db_path: Path, profile_id: str) -> tuple[float | None, float | None, int]:
        if not db_path.exists():
            return None, None, 0
        try:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT MIN(ts), MAX(ts), COUNT(*) FROM samples WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            return None, None, 0

        if not row:
            return None, None, 0
        min_ts = None if row[0] is None else float(row[0])
        max_ts = None if row[1] is None else float(row[1])
        count = int(row[2] or 0)
        return min_ts, max_ts, count

    @staticmethod
    def _normalize_connection_events(raw_events: list) -> list[list[float]]:
        events: list[tuple[float, int]] = []
        for item in raw_events:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                ts = float(item[0])
                state = 1 if bool(int(item[1])) else 0
            except (TypeError, ValueError):
                continue
            events.append((ts, state))

        events.sort(key=lambda pair: pair[0])
        normalized: list[list[float]] = []
        for ts, state in events:
            if normalized and abs(float(normalized[-1][0]) - ts) < 1e-9:
                normalized[-1] = [ts, float(state)]
                continue
            if normalized and int(normalized[-1][1]) == state:
                continue
            normalized.append([ts, float(state)])
        return normalized

    def _query_connection_events(
        self,
        conn: sqlite3.Connection,
        start_ts: float,
        end_ts: float,
    ) -> list[list[float]]:
        try:
            rows = conn.execute(
                """
                SELECT ts, is_connected
                FROM connection_events
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
                ORDER BY ts ASC, id ASC
                """,
                (self.current_profile.id, float(start_ts), float(end_ts)),
            ).fetchall()
        except sqlite3.Error:
            return []

        raw_events: list[list[float]] = []
        try:
            prev_row = conn.execute(
                """
                SELECT ts, is_connected
                FROM connection_events
                WHERE profile_id = ? AND ts < ?
                ORDER BY ts DESC, id DESC
                LIMIT 1
                """,
                (self.current_profile.id, float(start_ts)),
            ).fetchone()
        except sqlite3.Error:
            prev_row = None

        if prev_row is not None:
            try:
                prev_state = float(int(prev_row[1]))
                raw_events.append([float(start_ts), prev_state])
            except (TypeError, ValueError):
                pass

        for ts, state in rows:
            try:
                raw_events.append([float(ts), float(int(state))])
            except (TypeError, ValueError):
                continue
        return self._normalize_connection_events(raw_events)

    def _prompt_archive_export_options(
        self,
        min_ts: float,
        max_ts: float,
        total_rows: int,
    ) -> tuple[float, float, int] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Параметры экспорта архива')
        layout = QVBoxLayout(dialog)

        info_text = (
            f"Р’ Р±Р°Р·Рµ РЅР°Р№РґРµРЅРѕ Р·Р°РїРёСЃРµР№: {total_rows}\n"
            f"РџРѕР»РЅС‹Р№ РїРµСЂРёРѕРґ: {self._archive_safe_ts(min_ts)} .. {self._archive_safe_ts(max_ts)}"
        )
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        form = QFormLayout()
        start_edit = QDateTimeEdit()
        start_edit.setCalendarPopup(True)
        start_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        start_edit.setDateTime(QDateTime.fromMSecsSinceEpoch(int(min_ts * 1000)))

        end_edit = QDateTimeEdit()
        end_edit.setCalendarPopup(True)
        end_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        end_edit.setDateTime(QDateTime.fromMSecsSinceEpoch(int(max_ts * 1000)))

        chunk_spin = QSpinBox()
        chunk_spin.setRange(1000, 2_000_000)
        chunk_spin.setSingleStep(1000)
        chunk_spin.setValue(200_000 if total_rows > 200_000 else max(10_000, total_rows))
        chunk_spin.setSuffix(' строк/файл')

        form.addRow('Начало периода', start_edit)
        form.addRow('Окончание периода', end_edit)
        form.addRow('Разбиение', chunk_spin)
        layout.addLayout(form)

        hint_label = QLabel('Если строк больше заданного лимита, архив сохранится в несколько файлов *_partNNN.trend.json.')
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def on_accept() -> None:
            start_ts = start_edit.dateTime().toMSecsSinceEpoch() / 1000.0
            end_ts = end_edit.dateTime().toMSecsSinceEpoch() / 1000.0
            if end_ts < start_ts:
                QMessageBox.warning(self, 'Экспорт архива', 'Окончание периода должно быть позже начала.')
                return
            dialog.accept()

        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return (
            start_edit.dateTime().toMSecsSinceEpoch() / 1000.0,
            end_edit.dateTime().toMSecsSinceEpoch() / 1000.0,
            int(chunk_spin.value()),
        )

    def _build_export_signals(self, conn: sqlite3.Connection, start_ts: float, end_ts: float) -> list[SignalConfig]:
        rows = conn.execute(
            """
            SELECT s.signal_id, COALESCE(m.signal_name, '')
            FROM (
                SELECT DISTINCT signal_id
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
            ) AS s
            LEFT JOIN signals_meta AS m
                ON m.profile_id = ? AND m.signal_id = s.signal_id
            ORDER BY s.signal_id
            """,
            (
                self.current_profile.id,
                float(start_ts),
                float(end_ts),
                self.current_profile.id,
            ),
        ).fetchall()
        profile_signals = {signal.id: signal for signal in self.current_profile.signals}
        signals: list[SignalConfig] = []
        for idx, row in enumerate(rows):
            signal_id = str(row[0] or "")
            if not signal_id:
                continue
            signal_name = str(row[1] or f"Signal {idx + 1}")
            source = profile_signals.get(signal_id)
            if source is not None:
                signal = SignalConfig.from_dict(source.to_dict())
                if not signal.name:
                    signal.name = signal_name
            else:
                signal = SignalConfig(
                    id=signal_id,
                    name=signal_name,
                    address=0,
                    register_type="holding",
                    data_type="float32",
                    bit_index=0,
                    axis_index=1,
                    float_order="ABCD",
                    scale=1.0,
                    unit="",
                    color=DEFAULT_COLORS[idx % len(DEFAULT_COLORS)],
                    enabled=True,
                )
            signals.append(signal)
        return signals

    def _export_db_archive(
        self,
        db_path: Path,
        output_path: Path,
        start_ts: float,
        end_ts: float,
        chunk_rows: int,
    ) -> list[Path]:
        conn = sqlite3.connect(db_path)
        try:
            count_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
                """,
                (self.current_profile.id, float(start_ts), float(end_ts)),
            ).fetchone()
            total_rows = int((count_row or [0])[0] or 0)
            if total_rows <= 0:
                return []

            signals = self._build_export_signals(conn, start_ts, end_ts)
            connection_events = self._query_connection_events(conn, start_ts, end_ts)
            connection_config = self._connection_config_from_profile(self.current_profile)
            part_count = max(1, (total_rows + chunk_rows - 1) // chunk_rows)
            payload_signals = [item.to_dict() for item in signals]

            cursor = conn.execute(
                """
                SELECT signal_id, ts, value
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
                ORDER BY ts ASC, id ASC
                """,
                (self.current_profile.id, float(start_ts), float(end_ts)),
            )

            written_paths: list[Path] = []
            now_iso = datetime.now().isoformat(timespec="seconds")
            part_index = 1
            rows_in_part = 0
            part_min_ts: float | None = None
            part_max_ts: float | None = None
            part_samples: dict[str, list[list[float]]] = {}

            def flush_part() -> None:
                nonlocal part_index, rows_in_part, part_min_ts, part_max_ts, part_samples
                if rows_in_part <= 0:
                    return
                payload = {
                    "format": "trend_archive_v2",
                    "exported_at": now_iso,
                    "profile_id": self.current_profile.id,
                    "profile_name": self.current_profile.name,
                    "signals": payload_signals,
                    "samples": part_samples,
                    "connection_events": connection_events,
                    "connection_config": connection_config,
                    "period_start_ts": float(start_ts),
                    "period_end_ts": float(end_ts),
                    "period_start": self._archive_safe_ts(start_ts),
                    "period_end": self._archive_safe_ts(end_ts),
                    "part_index": part_index,
                    "part_count": part_count,
                    "rows_total": total_rows,
                    "rows_in_part": rows_in_part,
                    "part_start_ts": part_min_ts,
                    "part_end_ts": part_max_ts,
                    "part_start": self._archive_safe_ts(part_min_ts),
                    "part_end": self._archive_safe_ts(part_max_ts),
                }
                target = self._archive_part_path(output_path, part_index, part_count)
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                written_paths.append(target)
                part_index += 1
                rows_in_part = 0
                part_min_ts = None
                part_max_ts = None
                part_samples = {}

            while True:
                batch = cursor.fetchmany(5000)
                if not batch:
                    break
                for signal_id, ts, value in batch:
                    sid = str(signal_id or "")
                    if not sid:
                        continue
                    ts_f = float(ts)
                    val_f = float(value)
                    part_samples.setdefault(sid, []).append([ts_f, val_f])
                    if part_min_ts is None or ts_f < part_min_ts:
                        part_min_ts = ts_f
                    if part_max_ts is None or ts_f > part_max_ts:
                        part_max_ts = ts_f
                    rows_in_part += 1
                    if rows_in_part >= chunk_rows:
                        flush_part()
            flush_part()
            return written_paths
        finally:
            conn.close()

    def _save_archive_to_file(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        if self._archive_store is not None:
            try:
                self._archive_store.flush()
            except Exception:
                pass
        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        min_ts, max_ts, total_rows = self._db_archive_range(db_path, self.current_profile.id)

        if min_ts is not None and max_ts is not None and total_rows > 0:
            options = self._prompt_archive_export_options(min_ts, max_ts, total_rows)
            if options is None:
                return
            start_ts, end_ts, chunk_rows = options
            default_name = (
                f"{self.current_profile.name}_"
                f"{datetime.fromtimestamp(start_ts).strftime('%Y%m%d_%H%M%S')}_"
                f"{datetime.fromtimestamp(end_ts).strftime('%Y%m%d_%H%M%S')}.trend.zip"
            )
            default_path = str(Path.cwd() / default_name)
            file_path, selected_filter = QFileDialog.getSaveFileName(
                self,
                'Сохранить архив',
                default_path,
                "Trend bundle (*.trend.zip);;Trend archive (*.trend.json);;JSON (*.json)",
            )
            if not file_path:
                return
            export_zip = ("*.trend.zip" in str(selected_filter)) or str(file_path).lower().endswith(".zip")
            try:
                if export_zip:
                    out_path = self._archive_zip_output_path(file_path)
                    part_count, _rows_total = self._export_db_archive_to_zip(
                        db_path=db_path,
                        output_path=out_path,
                        start_ts=start_ts,
                        end_ts=end_ts,
                        chunk_rows=max(1000, int(chunk_rows)),
                    )
                    if part_count <= 0:
                        self.status_label.setText('Статус: нет данных за выбранный период')
                    else:
                        self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: ZIP-Р°СЂС…РёРІ СЃРѕС…СЂР°РЅРµРЅ ({part_count} С‡Р°СЃС‚РµР№) -> {out_path}")
                    return

                out_path = self._archive_output_path(file_path)
                saved_parts = self._export_db_archive(
                    db_path=db_path,
                    output_path=out_path,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    chunk_rows=max(1000, int(chunk_rows)),
                )
            except Exception as exc:
                self.status_label.setText(f"РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ Р°СЂС…РёРІР°: {exc}")
                return

            if not saved_parts:
                self.status_label.setText('Статус: нет данных за выбранный период')
            elif len(saved_parts) == 1:
                self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: Р°СЂС…РёРІ СЃРѕС…СЂР°РЅРµРЅ -> {saved_parts[0]}")
            else:
                self.status_label.setText(
                    f"РЎС‚Р°С‚СѓСЃ: Р°СЂС…РёРІ СЃРѕС…СЂР°РЅРµРЅ ({len(saved_parts)} С„Р°Р№Р»РѕРІ) -> {saved_parts[0].parent}"
                )
            return

        default_name = f"{self.current_profile.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.trend.zip"
        default_path = str(Path.cwd() / default_name)
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            'Сохранить архив',
            default_path,
            "Trend bundle (*.trend.zip);;Trend archive (*.trend.json);;JSON (*.json)",
        )
        if not file_path:
            return

        payload = self.chart.build_archive_payload(
            profile_id=self.current_profile.id,
            profile_name=self.current_profile.name,
            signals=self.current_profile.signals,
            connection_events=self._connection_events,
            connection_config=self._connection_config_from_profile(self.current_profile),
        )
        try:
            export_zip = ("*.trend.zip" in str(selected_filter)) or str(file_path).lower().endswith(".zip")
            if export_zip:
                out_path = self._archive_zip_output_path(file_path)
                self._export_payload_to_zip(out_path, payload)
                self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: ZIP-Р°СЂС…РёРІ СЃРѕС…СЂР°РЅРµРЅ -> {out_path}")
                return

            out_path = self._archive_output_path(file_path)
            atomic_write_text(out_path, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: Р°СЂС…РёРІ СЃРѕС…СЂР°РЅРµРЅ -> {out_path}")
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ Р°СЂС…РёРІР°: {exc}")

    def _load_archive_from_file(self) -> None:
        file_path, _selected = QFileDialog.getOpenFileName(
            self,
            'Загрузить архив',
            str(Path.cwd()),
            "Trend bundle (*.trend.zip);;Trend archive (*.trend.json *.json);;All files (*.*)",
        )
        if not file_path:
            return

        selected = Path(file_path)
        payloads: list[dict] = []
        loaded_connection_config: dict | None = None
        source_parts_count = 1
        source_type = "json"

        if self._is_zip_archive_path(selected):
            payloads, loaded_connection_config, error = self._load_payloads_from_zip(selected)
            if error:
                self.status_label.setText(error)
                return
            source_parts_count = len(payloads or [])
            source_type = "zip"
        else:
            parts = self._collect_archive_parts(selected)
            source_parts_count = len(parts)
            for part in parts:
                try:
                    payload = json.loads(part.read_text(encoding="utf-8"))
                except Exception as exc:
                    self.status_label.setText(f"РћС€РёР±РєР° С‡С‚РµРЅРёСЏ Р°СЂС…РёРІР° ({part.name}): {exc}")
                    return
                if not isinstance(payload, dict):
                    self.status_label.setText(f"РћС€РёР±РєР°: РЅРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚ С„Р°Р№Р»Р° Р°СЂС…РёРІР° ({part.name})")
                    return
                payloads.append(payload)

        if not payloads:
            self.status_label.setText('Ошибка: архив пуст')
            return

        for payload in payloads:
            if not isinstance(payload, dict):
                self.status_label.setText('Ошибка: неверный формат данных архива')
                return

        loaded_signals_map: dict[str, SignalConfig] = {}
        loaded_signal_order: list[str] = []
        samples_map: dict[str, list[list[float]]] = {}
        raw_connection_events: list[list[float]] = []

        for payload in payloads:
            signals_raw = payload.get("signals")
            if isinstance(signals_raw, list):
                for item in signals_raw:
                    if not isinstance(item, dict):
                        continue
                    signal = SignalConfig.from_dict(item)
                    if signal.id not in loaded_signals_map:
                        loaded_signals_map[signal.id] = signal
                        loaded_signal_order.append(signal.id)

            raw_samples = payload.get("samples")
            if not isinstance(raw_samples, dict):
                raw_samples = {}
            for signal_id, points in raw_samples.items():
                if not isinstance(points, list):
                    continue
                sid = str(signal_id)
                bucket = samples_map.setdefault(sid, [])
                for point in points:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        continue
                    try:
                        bucket.append([float(point[0]), float(point[1])])
                    except (TypeError, ValueError):
                        continue

            payload_events = payload.get("connection_events")
            if isinstance(payload_events, list):
                for item in payload_events:
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        continue
                    try:
                        raw_connection_events.append([float(item[0]), float(item[1])])
                    except (TypeError, ValueError):
                        continue

            if loaded_connection_config is None:
                payload_config = payload.get("connection_config")
                if isinstance(payload_config, dict):
                    loaded_connection_config = payload_config

        if not samples_map and not loaded_signals_map:
            self.status_label.setText('Ошибка: в архиве нет валидных данных')
            return

        for bucket in samples_map.values():
            bucket.sort(key=lambda point: point[0])
        connection_events = self._normalize_connection_events(raw_connection_events)

        loaded_signals: list[SignalConfig] = []
        for signal_id in loaded_signal_order:
            signal = loaded_signals_map.get(signal_id)
            if signal is not None:
                loaded_signals.append(signal)

        if not loaded_signals:
            for idx, signal_id in enumerate(samples_map.keys()):
                loaded_signals.append(
                    SignalConfig(
                        id=str(signal_id),
                        name=f"Signal {idx + 1}",
                        color=DEFAULT_COLORS[idx % len(DEFAULT_COLORS)],
                    )
                )

        self._stop_worker()

        if loaded_connection_config is not None:
            self._apply_connection_config_to_profile(self.current_profile, loaded_connection_config)
        self.current_profile.signals = loaded_signals
        self.current_profile.work_mode = "offline"
        self._updating_ui = True
        self._load_connection_fields_to_ui(self.current_profile)
        mode_index = self.mode_combo.findData("offline")
        self.mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self._active_signal_source_id = "local"
        self._refresh_signal_source_tabs()
        self._fill_signal_table(loaded_signals, source_id=self._current_signal_source_id())
        self._updating_ui = False
        combo_index = self.profile_combo.currentIndex()
        if combo_index >= 0:
            self.profile_combo.setItemText(combo_index, self.current_profile.name)

        self.chart.configure_signals(loaded_signals)
        self.chart.set_archive_data(samples_map)
        self.chart.set_connection_events(connection_events)
        self._connection_events = connection_events
        self._last_connection_state = None if not connection_events else bool(int(connection_events[-1][1]))
        self.chart.reset_view()
        self._apply_work_mode_ui("offline")
        if source_parts_count > 1:
            if source_type == "zip":
                self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: ZIP-Р°СЂС…РёРІ Р·Р°РіСЂСѓР¶РµРЅ ({source_parts_count} С‡Р°СЃС‚РµР№) <- {file_path}")
            else:
                self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: Р°СЂС…РёРІ Р·Р°РіСЂСѓР¶РµРЅ ({source_parts_count} С‡Р°СЃС‚РµР№) <- {selected.name}")
        elif source_type == "zip":
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: ZIP-Р°СЂС…РёРІ Р·Р°РіСЂСѓР¶РµРЅ <- {file_path}")
        else:
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: Р°СЂС…РёРІ Р·Р°РіСЂСѓР¶РµРЅ <- {file_path}")

    def _is_auto_x_enabled(self) -> bool:
        if hasattr(self, "action_auto_x"):
            try:
                return bool(self.action_auto_x.isChecked())
            except Exception:
                return True
        return True

    def _reset_history_window_cache(self) -> None:
        self._pending_history_x_range = None
        self._history_loaded_range = None
        self._history_loaded_bucket_s = None
        if self._history_view_timer.isActive():
            self._history_view_timer.stop()

    def _target_history_points(self) -> int:
        try:
            width_px = int(self.chart.plot_widget.width())
        except Exception:
            width_px = 1200
        width_px = max(320, width_px)
        # 2*width gives enough detail while still bounded by decimation.
        return max(900, min(7000, width_px * 2))

    def _schedule_visible_history_reload(
        self,
        x_left: float | None = None,
        x_right: float | None = None,
        force: bool = False,
    ) -> None:
        if self._history_reload_guard:
            return
        if not bool(self.current_profile.render_chart_enabled):
            return
        if not self._profile_uses_local_recorder():
            return

        mode = str(self.mode_combo.currentData() or "online")
        if mode == "online" and self._is_auto_x_enabled():
            return

        if x_left is None or x_right is None:
            try:
                x_left, x_right = self.chart.current_x_range()
            except Exception:
                return

        left = float(min(x_left, x_right))
        right = float(max(x_left, x_right))
        span = float(right - left)
        if not math.isfinite(span) or span <= 0.0:
            return

        target_points = self._target_history_points()
        target_bucket_s = max(1e-6, span / float(max(32, target_points)))
        if not force and self._history_loaded_range is not None and self._history_loaded_bucket_s is not None:
            loaded_left, loaded_right = self._history_loaded_range
            has_detail = float(self._history_loaded_bucket_s) <= (target_bucket_s * 1.25)
            if left >= float(loaded_left) and right <= float(loaded_right) and has_detail:
                return

        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        if not db_path.exists():
            return

        self._pending_history_x_range = (left, right)
        delay_ms = 1 if force else int(self._history_view_timer.interval())
        self._history_view_timer.start(max(1, delay_ms))

    def _on_chart_x_range_changed(self, x_min: float, x_max: float) -> None:
        self._schedule_visible_history_reload(float(x_min), float(x_max), force=False)

    def _reload_visible_history_from_db(self) -> None:
        pending = self._pending_history_x_range
        self._pending_history_x_range = None
        if pending is None:
            return
        left, right = pending
        self._load_history_window_from_db(left, right, preserve_range=True, silent=True)

    def _query_samples_for_window(
        self,
        conn: sqlite3.Connection,
        start_ts: float,
        end_ts: float,
        target_points_per_signal: int,
    ) -> tuple[dict[str, list[list[float]]], float]:
        signal_ids = [str(signal.id) for signal in self.current_profile.signals if str(signal.id)]
        samples_map: dict[str, list[list[float]]] = {sid: [] for sid in signal_ids}
        if not signal_ids:
            return samples_map, 1.0

        left = float(min(start_ts, end_ts))
        right = float(max(start_ts, end_ts))
        window_span = max(1e-6, right - left)
        bucket_s = max(1e-6, window_span / float(max(32, int(target_points_per_signal))))
        archive_interval_s = max(0.05, float(getattr(self.current_profile, "archive_interval_ms", 1000) or 1000) / 1000.0)
        raw_threshold_s = max(0.35, archive_interval_s * 1.25)
        placeholders = ",".join("?" for _ in signal_ids)

        if bucket_s <= raw_threshold_s:
            rows = conn.execute(
                f"""
                SELECT signal_id, ts, value
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ? AND signal_id IN ({placeholders})
                ORDER BY ts ASC, id ASC
                """,
                [self.current_profile.id, float(left), float(right), *signal_ids],
            ).fetchall()
            for signal_id, ts, value in rows:
                sid = str(signal_id)
                if sid not in samples_map:
                    continue
                try:
                    samples_map[sid].append([float(ts), float(value)])
                except (TypeError, ValueError):
                    continue
            return samples_map, max(1e-6, raw_threshold_s)

        rows = conn.execute(
            f"""
            SELECT
                signal_id,
                CAST((ts - ?) / ? AS INTEGER) AS bucket_idx,
                MIN(ts) AS min_ts,
                MAX(ts) AS max_ts,
                MIN(value) AS min_value,
                MAX(value) AS max_value
            FROM samples
            WHERE profile_id = ? AND ts >= ? AND ts <= ? AND signal_id IN ({placeholders})
            GROUP BY signal_id, bucket_idx
            ORDER BY signal_id ASC, bucket_idx ASC
            """,
            [float(left), float(bucket_s), self.current_profile.id, float(left), float(right), *signal_ids],
        ).fetchall()

        for signal_id, _bucket_idx, min_ts, max_ts, min_value, max_value in rows:
            sid = str(signal_id)
            if sid not in samples_map:
                continue
            try:
                min_ts_f = float(min_ts)
                max_ts_f = float(max_ts)
                min_val_f = float(min_value)
                max_val_f = float(max_value)
            except (TypeError, ValueError):
                continue

            if abs(max_ts_f - min_ts_f) <= 1e-9:
                samples_map[sid].append([min_ts_f, min_val_f])
                continue
            if min_ts_f <= max_ts_f:
                samples_map[sid].append([min_ts_f, min_val_f])
                samples_map[sid].append([max_ts_f, max_val_f])
            else:
                samples_map[sid].append([max_ts_f, max_val_f])
                samples_map[sid].append([min_ts_f, min_val_f])

        for sid, points in list(samples_map.items()):
            if len(points) <= 1:
                continue
            compact: list[list[float]] = [points[0]]
            for ts, value in points[1:]:
                if abs(float(compact[-1][0]) - float(ts)) <= 1e-9:
                    compact[-1] = [float(ts), float(value)]
                else:
                    compact.append([float(ts), float(value)])
            samples_map[sid] = compact
        return samples_map, float(bucket_s)

    def _load_history_window_from_db(
        self,
        x_left: float,
        x_right: float,
        preserve_range: bool = True,
        silent: bool = True,
    ) -> bool:
        if not self._profile_uses_local_recorder():
            return False
        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        if not db_path.exists():
            return False

        left = float(min(x_left, x_right))
        right = float(max(x_left, x_right))
        if right <= left:
            return False

        span = right - left
        margin = max(1.0, span * 0.20)
        query_left = max(0.0, left - margin)
        query_right = right + margin
        target_points = self._target_history_points()

        mode = str(self.mode_combo.currentData() or "online")
        use_shared_conn = bool(mode == "online" and self._db_live_conn is not None)
        conn: sqlite3.Connection | None = self._db_live_conn if use_shared_conn else None
        close_conn = False
        if conn is None:
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA busy_timeout=2000;")
                close_conn = True
            except Exception:
                return False

        try:
            samples_map, bucket_s = self._query_samples_for_window(
                conn,
                start_ts=float(query_left),
                end_ts=float(query_right),
                target_points_per_signal=target_points,
            )
            connection_events = self._query_connection_events(conn, float(query_left), float(query_right))
        except Exception:
            return False
        finally:
            if close_conn and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        latest_values: dict[str, tuple[str, float]] = {}
        signal_name_by_id = {str(item.id): str(item.name) for item in self.current_profile.signals if str(item.id)}
        for sid, points in samples_map.items():
            if not points:
                continue
            try:
                latest_values[str(sid)] = (signal_name_by_id.get(str(sid), str(sid)), float(points[-1][1]))
            except Exception:
                continue
        if latest_values:
            self._last_live_values.update(latest_values)

        self._history_reload_guard = True
        try:
            self.chart.set_archive_data(samples_map)
            self.chart.set_connection_events(connection_events)
            if preserve_range:
                self.chart.set_x_range(float(left), float(right))
        finally:
            self._history_reload_guard = False

        self._connection_events = connection_events
        self._last_connection_state = None if not connection_events else bool(int(connection_events[-1][1]))
        self._history_loaded_range = (float(query_left), float(query_right))
        self._history_loaded_bucket_s = max(1e-6, float(bucket_s))

        if not silent:
            left_text = format_ts_ms(float(left))
            right_text = format_ts_ms(float(right))
            self.status_label.setText(f"Статус: загружен участок архива {left_text} .. {right_text}")
        return True

    def _load_offline_initial_history_from_db(self, silent: bool = True) -> bool:
        if not self._profile_uses_local_recorder():
            return False
        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        min_ts, max_ts, count = self._db_archive_range(db_path, self.current_profile.id)
        if min_ts is None or max_ts is None or int(count) <= 0:
            self._history_reload_guard = True
            try:
                self.chart.set_archive_data({})
                self.chart.set_connection_events([])
            finally:
                self._history_reload_guard = False
            self._connection_events = []
            self._last_connection_state = None
            self._reset_history_window_cache()
            if not silent:
                self.status_label.setText("Статус: архив пуст")
            return False

        span_hint = self._current_chart_span_s()
        if span_hint is None:
            span_hint = self._preferred_live_history_span_s()
        span = max(20.0, float(span_hint))
        archive_span = max(20.0, float(max_ts) - float(min_ts))
        span = min(span, archive_span)
        right = float(max_ts)
        left = max(float(min_ts), right - span)
        return self._load_history_window_from_db(left, right, preserve_range=True, silent=silent)

    def _current_chart_span_s(self) -> float | None:
        try:
            view_left, view_right = self.chart.current_x_range()
            span = float(view_right - view_left)
        except Exception:
            return None
        if not math.isfinite(span) or span <= 0.0:
            return None
        return span

    def _preferred_live_history_span_s(self, span_hint_s: float | None = None) -> float:
        return compute_live_history_span_s(
            poll_interval_ms=int(getattr(self.current_profile, "poll_interval_ms", 500) or 500),
            archive_interval_ms=int(getattr(self.current_profile, "archive_interval_ms", 1000) or 1000),
            archive_on_change_only=bool(getattr(self.current_profile, "archive_on_change_only", False)),
            archive_keepalive_s=int(getattr(self.current_profile, "archive_keepalive_s", 60) or 0),
            span_hint_s=span_hint_s,
            current_span_s=self._current_chart_span_s(),
        )

    def _load_recent_online_history_from_db(
        self,
        adjust_x_range: bool = True,
        silent: bool = True,
        span_override_s: float | None = None,
    ) -> bool:
        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(db_path)
        except Exception:
            return False

        try:
            row = conn.execute(
                "SELECT MAX(ts), COUNT(*) FROM samples WHERE profile_id = ?",
                (self.current_profile.id,),
            ).fetchone()
            if not row:
                return False
            last_ts = None if row[0] is None else float(row[0])
            total_rows = int(row[1] or 0)
            if last_ts is None or total_rows <= 0:
                return False

            span = self._preferred_live_history_span_s(span_override_s)
            start_ts = max(0.0, float(last_ts) - span)

            cursor = conn.execute(
                """
                SELECT signal_id, ts, value
                FROM samples
                WHERE profile_id = ? AND ts >= ? AND ts <= ?
                ORDER BY ts ASC, id ASC
                """,
                (self.current_profile.id, float(start_ts), float(last_ts)),
            )

            samples_map: dict[str, list[list[float]]] = {}
            for signal_id, ts, value in cursor:
                sid = str(signal_id or "")
                if not sid:
                    continue
                try:
                    ts_f = float(ts)
                    value_f = float(value)
                except (TypeError, ValueError):
                    continue
                samples_map.setdefault(sid, []).append([ts_f, value_f])

            if not samples_map:
                fallback_rows = conn.execute(
                    """
                    SELECT signal_id, ts, value
                    FROM samples
                    WHERE profile_id = ?
                    ORDER BY ts DESC, id DESC
                    LIMIT 12000
                    """,
                    (self.current_profile.id,),
                ).fetchall()
                for signal_id, ts, value in reversed(fallback_rows):
                    sid = str(signal_id or "")
                    if not sid:
                        continue
                    try:
                        ts_f = float(ts)
                        value_f = float(value)
                    except (TypeError, ValueError):
                        continue
                    samples_map.setdefault(sid, []).append([ts_f, value_f])
                if samples_map:
                    first_loaded_ts = min(points[0][0] for points in samples_map.values() if points)
                    last_loaded_ts = max(points[-1][0] for points in samples_map.values() if points)
                    start_ts = min(float(start_ts), float(first_loaded_ts))
                    last_ts = max(float(last_ts), float(last_loaded_ts))

            now_ts = datetime.now().timestamp()
            event_end_ts = max(float(last_ts), float(now_ts))
            connection_events = self._query_connection_events(conn, float(start_ts), float(event_end_ts))
        except Exception:
            return False
        finally:
            conn.close()

        if not samples_map:
            return False

        signal_name_by_id = {str(item.id): str(item.name) for item in self.current_profile.signals if str(item.id)}
        latest_values: dict[str, tuple[str, float]] = {}
        for sid, points in samples_map.items():
            if not points:
                continue
            try:
                latest_val = float(points[-1][1])
            except Exception:
                continue
            latest_values[str(sid)] = (signal_name_by_id.get(str(sid), str(sid)), latest_val)
        if latest_values:
            self._last_live_values.update(latest_values)

        gap_threshold = max(2.0, float(self.current_profile.poll_interval_ms) / 1000.0 * 2.5)
        worker_running = self._is_live_stream_running()
        runtime_connected = bool(self._runtime_connected)
        can_infer_gap_disconnect = not (worker_running and runtime_connected)
        if can_infer_gap_disconnect and (now_ts - float(last_ts) > gap_threshold):
            if not connection_events or int(connection_events[-1][1]) != 0:
                connection_events.append([float(last_ts), 0.0])
                connection_events = self._normalize_connection_events(connection_events)

        self.chart.set_archive_data(samples_map)
        self.chart.set_connection_events(connection_events)
        self._connection_events = connection_events
        self._last_connection_state = None if not connection_events else bool(int(connection_events[-1][1]))
        self._history_loaded_range = (float(start_ts), float(last_ts))
        loaded_span = max(1e-6, float(last_ts) - float(start_ts))
        self._history_loaded_bucket_s = loaded_span / float(max(32, self._target_history_points()))

        if adjust_x_range:
            right = float(last_ts)
            left = max(0.0, right - span)
            self.chart.set_x_range(left, right)

        if not silent:
            left_text = format_ts_ms(max(0.0, float(last_ts) - span))
            right_text = format_ts_ms(float(last_ts))
            self.status_label.setText(f"РЎС‚Р°С‚СѓСЃ: РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅР° РёСЃС‚РѕСЂРёСЏ РёР· Р‘Р” ({left_text} .. {right_text})")
        return True

    def _is_live_stream_running(self) -> bool:
        return bool(self._db_live_running or (self._worker is not None and self._worker.isRunning()))

    def _is_external_recorder_running(self) -> bool:
        return resolve_recorder_pid() is not None

    def _profile_uses_local_recorder(self) -> bool:
        signals = list(getattr(self.current_profile, "signals", []) or [])
        if not signals:
            return True
        for signal in signals:
            source_id = str(getattr(signal, "source_id", "local") or "local")
            if source_id == "local":
                return True
        return False

    def _can_use_recorder_features(self) -> bool:
        mode = str(getattr(self.current_profile, "work_mode", "online") or "online")
        if mode != "online":
            return True
        if not self._profile_uses_local_recorder():
            return True
        if self._is_external_recorder_running():
            return True
        # Allow opening setup/start actions when recorder can be auto-started.
        try:
            self._external_recorder_command()
            return True
        except Exception:
            return False

    def _update_recorder_dependent_ui_state(self) -> None:
        enabled = self._can_use_recorder_features()
        if self._recorder_controls_enabled is None or self._recorder_controls_enabled != enabled:
            self._recorder_controls_enabled = enabled

        for action_name in (
            "action_connection",
            "action_signals",
            "action_sources",
            "action_tags",
            "action_scales",
        ):
            action = getattr(self, action_name, None)
            if action is not None:
                action.setEnabled(bool(enabled))

        for window_name in (
            "connection_window",
            "signals_window",
            "sources_window",
            "tags_window",
            "scales_window",
        ):
            win = getattr(self, window_name, None)
            if win is not None:
                win.setEnabled(bool(enabled))

        mode = str(getattr(self.current_profile, "work_mode", "online") or "online")
        online_mode = mode == "online"
        can_start = online_mode and (not self._is_live_stream_running()) and bool(enabled)
        can_stop = online_mode and self._is_live_stream_running()
        if hasattr(self, "action_start"):
            self.action_start.setEnabled(bool(can_start))
        if hasattr(self, "action_stop"):
            self.action_stop.setEnabled(bool(can_stop))

    def _close_db_live_connection(self) -> None:
        if self._db_live_conn is None:
            return
        try:
            self._db_live_conn.close()
        except Exception:
            pass
        self._db_live_conn = None

    def _open_db_live_connection(self) -> bool:
        if self._db_live_conn is not None:
            return True
        db_path = Path(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        if not db_path.exists():
            return False
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout=2000;")
        except Exception:
            self._db_live_conn = None
            return False
        self._db_live_conn = conn
        return True

    def _query_max_table_id(self, table_name: str) -> int:
        if self._db_live_conn is None:
            return 0
        if table_name not in {"samples", "connection_events"}:
            return 0
        try:
            row = self._db_live_conn.execute(
                f"SELECT MAX(id) FROM {table_name} WHERE profile_id = ?",
                (self.current_profile.id,),
            ).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def _bootstrap_db_live_cursors(self) -> None:
        self._db_live_last_sample_row_id = self._query_max_table_id("samples")
        self._db_live_last_connection_event_row_id = self._query_max_table_id("connection_events")
        self._bootstrap_remote_live_cursors()

    def _remote_signal_mapping(self) -> dict[str, dict[str, list[tuple[str, str]]]]:
        mapping: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for signal in self.current_profile.signals:
            source_id = str(getattr(signal, "source_id", "local") or "local")
            remote_tag_id = str(getattr(signal, "remote_tag_id", "") or "")
            if source_id == "local" or not remote_tag_id:
                continue
            mapping.setdefault(source_id, {}).setdefault(remote_tag_id, []).append((str(signal.id), str(signal.name)))
        return mapping

    @staticmethod
    def _remote_bind_key(
        address: int | str,
        register_type: str,
        data_type: str,
        bit_index: int | str,
    ) -> tuple[int, str, str, int]:
        try:
            addr_i = int(address)
        except Exception:
            addr_i = 0
        reg = str(register_type or "").strip().lower()
        dtype = str(data_type or "").strip().lower()
        try:
            bit_i = int(bit_index)
        except Exception:
            bit_i = 0
        if dtype != "bool":
            bit_i = 0
        return (addr_i, reg, dtype, bit_i)

    def _try_repair_remote_bindings(self) -> int:
        sources_by_id = {str(source.id): source for source in self.current_profile.recorder_sources if bool(source.enabled)}
        if not sources_by_id:
            return 0

        repaired_total = 0
        for source_id, source in sources_by_id.items():
            source_signals = [
                signal
                for signal in self.current_profile.signals
                if str(getattr(signal, "source_id", "local") or "local") == source_id
            ]
            if not source_signals:
                continue

            try:
                payload = self._source_request_json(source, "GET", "/v1/tags", timeout_s=max(0.6, self._remote_live_timeout_s()))
            except Exception:
                continue
            if not bool(payload.get("ok", False)):
                continue
            tags_raw = payload.get("tags")
            if not isinstance(tags_raw, list) or not tags_raw:
                continue

            tags_by_id: dict[str, dict] = {}
            tags_by_name: dict[str, list[dict]] = {}
            tags_by_key: dict[tuple[int, str, str, int], list[dict]] = {}
            for item in tags_raw:
                if not isinstance(item, dict):
                    continue
                tag_id = str(item.get("id") or "").strip()
                if not tag_id:
                    continue
                tags_by_id[tag_id] = item
                tag_name = str(item.get("name") or "").strip().casefold()
                if tag_name:
                    tags_by_name.setdefault(tag_name, []).append(item)
                key = self._remote_bind_key(
                    item.get("address", 0),
                    str(item.get("register_type") or ""),
                    str(item.get("data_type") or ""),
                    item.get("bit_index", 0),
                )
                tags_by_key.setdefault(key, []).append(item)

            used_ids = {
                str(getattr(sig, "remote_tag_id", "") or "").strip()
                for sig in source_signals
                if str(getattr(sig, "remote_tag_id", "") or "").strip() in tags_by_id
            }

            repaired_for_source: dict[str, str] = {}
            for signal in source_signals:
                signal_id = str(getattr(signal, "id", "") or "").strip()
                if not signal_id:
                    continue
                current_remote_id = str(getattr(signal, "remote_tag_id", "") or "").strip()
                if current_remote_id and current_remote_id in tags_by_id:
                    continue

                candidate: dict | None = None
                signal_name_key = str(getattr(signal, "name", "") or "").strip().casefold()
                if signal_name_key:
                    name_candidates = [
                        item for item in tags_by_name.get(signal_name_key, []) if str(item.get("id") or "") not in used_ids
                    ]
                    if len(name_candidates) == 1:
                        candidate = name_candidates[0]

                if candidate is None:
                    bind_key = self._remote_bind_key(
                        getattr(signal, "address", 0),
                        str(getattr(signal, "register_type", "") or ""),
                        str(getattr(signal, "data_type", "") or ""),
                        getattr(signal, "bit_index", 0),
                    )
                    key_candidates = [
                        item for item in tags_by_key.get(bind_key, []) if str(item.get("id") or "") not in used_ids
                    ]
                    if len(key_candidates) == 1:
                        candidate = key_candidates[0]

                if candidate is None:
                    continue

                repaired_id = str(candidate.get("id") or "").strip()
                if not repaired_id:
                    continue
                if repaired_id == current_remote_id:
                    continue
                signal.remote_tag_id = repaired_id
                used_ids.add(repaired_id)
                repaired_for_source[signal_id] = repaired_id
                repaired_total += 1

            if repaired_for_source:
                self._apply_runtime_remote_tag_id_repairs(source_id, repaired_for_source)

        if repaired_total > 0:
            self._mark_config_dirty()
        return repaired_total

    def _bootstrap_remote_live_cursors(self) -> None:
        self._remote_live_cursors = {}
        self._remote_live_backoff_until_mono = {}
        self._remote_live_fail_count = {}
        self._remote_last_ok_mono = {}
        for future in list(self._remote_live_futures.values()):
            try:
                future.cancel()
            except Exception:
                pass
        self._remote_live_futures = {}
        mapping = self._remote_signal_mapping()
        if not mapping:
            return
        for source in self.current_profile.recorder_sources:
            source_id = str(source.id)
            if not source.enabled or source_id not in mapping:
                continue
            cursor = {"sample_id": 0, "event_id": 0}
            try:
                payload = self._source_request_json(
                    source,
                    "GET",
                    "/v1/live",
                    query={"bootstrap": 1},
                    timeout_s=0.8,
                )
                if bool(payload.get("ok", False)):
                    cursor["sample_id"] = int(payload.get("next_sample_id", 0) or 0)
                    cursor["event_id"] = int(payload.get("next_event_id", 0) or 0)
            except Exception:
                pass
            self._remote_live_cursors[source_id] = cursor

    def _remote_live_timeout_s(self) -> float:
        # Keep network waits short to avoid UI lag on temporary link issues.
        try:
            base = float(self.current_profile.timeout_s)
        except Exception:
            base = 1.0
        return max(0.25, min(0.8, base * 0.5))

    def _remote_live_fetch_payload(
        self,
        source: RecorderSourceConfig,
        since_sample_id: int,
        since_event_id: int,
        timeout_s: float,
    ) -> dict:
        try:
            payload = self._source_request_json(
                source,
                "GET",
                "/v1/live",
                query={
                    "since_sample_id": int(since_sample_id),
                    "since_event_id": int(since_event_id),
                    "sample_limit": 4000,
                    "event_limit": 2000,
                },
                timeout_s=float(timeout_s),
            )
            return {
                "ok": True,
                "payload": payload,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
            }

    def _remote_connected_stable(self, mapped_source_ids: list[str], now_mono: float) -> bool:
        if not mapped_source_ids:
            return False
        ttl_s = max(2.0, float(self._db_live_timer.interval()) / 1000.0 * 4.0)
        for source_id in mapped_source_ids:
            last_ok = self._remote_last_ok_mono.get(str(source_id))
            if last_ok is not None and (now_mono - float(last_ok)) <= ttl_s:
                return True
        return False

    def _append_remote_samples_rows(
        self,
        source: RecorderSourceConfig,
        rows: list[dict],
        tag_mapping: dict[str, list[tuple[str, str]]],
    ) -> None:
        if not rows:
            return
        source_id = str(getattr(source, "id", "") or "").strip()
        signals_for_source = [
            signal
            for signal in list(getattr(self.current_profile, "signals", []) or [])
            if str(getattr(signal, "source_id", "local") or "local") == source_id
        ]
        name_targets: dict[str, list[tuple[str, str]]] = {}
        name_signals: dict[str, list[SignalConfig]] = {}
        for signal in signals_for_source:
            signal_name = str(getattr(signal, "name", "") or "").strip()
            if not signal_name:
                continue
            name_targets.setdefault(signal_name, []).append((str(signal.id), signal_name))
            name_signals.setdefault(signal_name, []).append(signal)
        repaired_remote_ids: dict[str, str] = {}
        batch: list[tuple[float, dict[str, tuple[str, float]]]] = []
        current_ts: float | None = None
        current_samples: dict[str, tuple[str, float]] = {}
        for row in rows:
            try:
                # Backward compatibility:
                # older recorder APIs may return "signal_id" instead of "tag_id".
                remote_tag_id = str(
                    row.get("tag_id")
                    or row.get("signal_id")
                    or row.get("id")
                    or ""
                ).strip()
                ts_f = float(row.get("ts"))
                value_f = float(row.get("value"))
            except (TypeError, ValueError):
                continue
            targets = tag_mapping.get(remote_tag_id) or []
            if not targets:
                # Self-heal mapping when remote tag ids changed but names stayed.
                row_tag_name = str(row.get("tag_name") or "").strip()
                if row_tag_name:
                    targets = name_targets.get(row_tag_name) or []
                    if targets:
                        tag_mapping[remote_tag_id] = list(targets)
                        for signal in name_signals.get(row_tag_name, []):
                            sid = str(getattr(signal, "id", "") or "").strip()
                            if not sid:
                                continue
                            current_remote_id = str(getattr(signal, "remote_tag_id", "") or "").strip()
                            if current_remote_id != remote_tag_id:
                                signal.remote_tag_id = remote_tag_id
                                repaired_remote_ids[sid] = remote_tag_id
            if not targets:
                continue
            if current_ts is None or abs(float(current_ts) - ts_f) > 1e-9:
                if current_ts is not None and current_samples:
                    batch.append((float(current_ts), current_samples))
                current_ts = ts_f
                current_samples = {}
            for local_signal_id, local_signal_name in targets:
                sid = str(local_signal_id)
                sname = str(local_signal_name)
                current_samples[sid] = (sname, value_f)
                self._last_live_values[sid] = (sname, value_f)
        if current_ts is not None and current_samples:
            batch.append((float(current_ts), current_samples))
        if not batch:
            if repaired_remote_ids:
                self._apply_runtime_remote_tag_id_repairs(source_id, repaired_remote_ids)
            return
        self._live_cycle_has_new_samples = True
        if repaired_remote_ids:
            self._apply_runtime_remote_tag_id_repairs(source_id, repaired_remote_ids)
        if bool(self.current_profile.render_chart_enabled):
            self._pending_render_samples.extend(batch)
            if len(self._pending_render_samples) > self._max_pending_render_batches:
                self._pending_render_samples = self._pending_render_samples[-self._max_pending_render_batches :]
            if not self._render_timer.isActive():
                self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
                self._render_timer.start()

    def _apply_runtime_remote_tag_id_repairs(self, source_id: str, repaired_remote_ids: dict[str, str]) -> None:
        if not repaired_remote_ids:
            return
        # Keep visible source table in sync to avoid losing repaired ids on save.
        current_source_id = self._current_signal_source_id()
        if current_source_id == str(source_id or "").strip() and hasattr(self, "signal_table"):
            for row in range(self.signal_table.rowCount()):
                name_item = self.signal_table.item(row, 1)
                if name_item is None:
                    continue
                signal_id = str(name_item.data(ROLE_SIGNAL_ID) or "").strip()
                if not signal_id:
                    continue
                remote_tag_id = repaired_remote_ids.get(signal_id)
                if remote_tag_id:
                    name_item.setData(ROLE_SIGNAL_REMOTE_TAG_ID, str(remote_tag_id))
        self._mark_config_dirty()

    def _poll_remote_live_stream(self) -> bool:
        mapping = self._remote_signal_mapping()
        if not mapping:
            self._remote_last_ok_mono = {}
            self._remote_live_backoff_until_mono = {}
            self._remote_live_fail_count = {}
            for future in list(self._remote_live_futures.values()):
                try:
                    future.cancel()
                except Exception:
                    pass
            self._remote_live_futures = {}
            return False

        by_id = {str(source.id): source for source in self.current_profile.recorder_sources if source.enabled}
        # Cancel stale tasks for disabled/removed sources.
        mapped_source_ids = {str(source_id) for source_id in mapping.keys()}
        for source_id, future in list(self._remote_live_futures.items()):
            if source_id in mapped_source_ids and source_id in by_id:
                continue
            try:
                future.cancel()
            except Exception:
                pass
            self._remote_live_futures.pop(source_id, None)
            self._remote_live_backoff_until_mono.pop(source_id, None)
            self._remote_live_fail_count.pop(source_id, None)
            self._remote_last_ok_mono.pop(source_id, None)

        now_mono = time.monotonic()
        any_ok_now = False

        # 1) Consume completed requests (no blocking UI thread).
        for source_id, future in list(self._remote_live_futures.items()):
            if not future.done():
                continue
            self._remote_live_futures.pop(source_id, None)
            source = by_id.get(source_id)
            tags_map = mapping.get(source_id)
            if source is None or tags_map is None:
                continue
            cursor = self._remote_live_cursors.setdefault(source_id, {"sample_id": 0, "event_id": 0})
            result: dict
            try:
                result = future.result()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}

            payload = result.get("payload") if isinstance(result, dict) else None
            request_ok = bool(isinstance(result, dict) and result.get("ok", False))
            payload_ok = bool(isinstance(payload, dict) and payload.get("ok", False))

            if request_ok and payload_ok:
                payload_connected = bool(payload.get("connected", True))
                if payload_connected:
                    any_ok_now = True
                    self._remote_last_ok_mono[source_id] = now_mono
                else:
                    self._remote_last_ok_mono.pop(source_id, None)
                self._remote_live_fail_count[source_id] = 0
                self._remote_live_backoff_until_mono[source_id] = now_mono

                sample_rows = payload.get("samples")
                if isinstance(sample_rows, list):
                    self._append_remote_samples_rows(source, sample_rows, tags_map)
                try:
                    cursor["sample_id"] = int(payload.get("next_sample_id", cursor.get("sample_id", 0)) or 0)
                    cursor["event_id"] = int(payload.get("next_event_id", cursor.get("event_id", 0)) or 0)
                except (TypeError, ValueError):
                    pass
                continue

            fail_count = int(self._remote_live_fail_count.get(source_id, 0)) + 1
            self._remote_live_fail_count[source_id] = fail_count
            backoff_s = min(3.0, 0.25 * (2 ** max(0, fail_count - 1)))
            self._remote_live_backoff_until_mono[source_id] = now_mono + backoff_s

        # 2) Submit next requests only for sources that are due.
        executor = self._remote_live_executor
        if executor is not None:
            timeout_s = self._remote_live_timeout_s()
            for source_id, tags_map in mapping.items():
                source = by_id.get(source_id)
                if source is None:
                    continue
                if source_id in self._remote_live_futures:
                    continue
                due_at = float(self._remote_live_backoff_until_mono.get(source_id, 0.0))
                if now_mono < due_at:
                    continue
                cursor = self._remote_live_cursors.setdefault(source_id, {"sample_id": 0, "event_id": 0})
                source_copy = RecorderSourceConfig.from_dict(source.to_dict())
                self._remote_live_futures[source_id] = executor.submit(
                    self._remote_live_fetch_payload,
                    source_copy,
                    int(cursor.get("sample_id", 0)),
                    int(cursor.get("event_id", 0)),
                    float(timeout_s),
                )

        # Do not flap connectivity on transient failures.
        return self._remote_connected_stable(list(mapped_source_ids), now_mono) or any_ok_now

    def _load_recent_remote_history_from_sources(self, span_s: float) -> bool:
        if span_s <= 0.0:
            return False
        mapping = self._remote_signal_mapping()
        if not mapping:
            return False
        by_id = {str(source.id): source for source in self.current_profile.recorder_sources if source.enabled}
        end_ts = time.time()
        start_ts = max(0.0, end_ts - float(span_s))
        loaded_any = False

        def _rows_from_history_payload(payload_obj: dict, tag_name_by_id: dict[str, str] | None = None) -> list[dict]:
            samples_obj = payload_obj.get("samples")
            if not isinstance(samples_obj, dict):
                return []
            rows_local: list[dict] = []
            for remote_tag_id, points in samples_obj.items():
                if not isinstance(points, list):
                    continue
                tag_id = str(remote_tag_id)
                tag_name = ""
                if isinstance(tag_name_by_id, dict):
                    tag_name = str(tag_name_by_id.get(tag_id, "") or "")
                for point in points:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        continue
                    rows_local.append(
                        {
                            "tag_id": tag_id,
                            "tag_name": tag_name,
                            "ts": point[0],
                            "value": point[1],
                        }
                    )
            rows_local.sort(key=lambda item: float(item.get("ts", 0.0)))
            return rows_local

        for source_id, tags_map in mapping.items():
            source = by_id.get(source_id)
            if source is None:
                continue
            timeout_s = max(0.6, min(1.2, self._remote_live_timeout_s() * 2.0))
            tag_ids_csv = ",".join(tags_map.keys())
            rows: list[dict] = []

            try:
                payload = self._source_request_json(
                    source,
                    "GET",
                    "/v1/history",
                    query={
                        "from_ts": f"{start_ts:.3f}",
                        "to_ts": f"{end_ts:.3f}",
                        "tag_ids": tag_ids_csv,
                    },
                    timeout_s=timeout_s,
                )
            except Exception:
                payload = {}
            if bool(payload.get("ok", False)):
                rows = _rows_from_history_payload(payload)

            # Fallback 1: id drift between configured tags and archived rows.
            if not rows:
                tag_name_by_id: dict[str, str] = {}
                try:
                    tags_payload = self._source_request_json(source, "GET", "/v1/tags", timeout_s=timeout_s)
                    if bool(tags_payload.get("ok", False)):
                        tags_raw = tags_payload.get("tags")
                        if isinstance(tags_raw, list):
                            for item in tags_raw:
                                if not isinstance(item, dict):
                                    continue
                                rid = str(item.get("id") or "").strip()
                                if rid:
                                    tag_name_by_id[rid] = str(item.get("name") or rid)
                except Exception:
                    pass

                try:
                    payload_all = self._source_request_json(
                        source,
                        "GET",
                        "/v1/history",
                        query={
                            "from_ts": f"{start_ts:.3f}",
                            "to_ts": f"{end_ts:.3f}",
                        },
                        timeout_s=timeout_s,
                    )
                except Exception:
                    payload_all = {}
                if bool(payload_all.get("ok", False)):
                    rows = _rows_from_history_payload(payload_all, tag_name_by_id=tag_name_by_id)

            # Fallback 2: recorder disconnected now, but archive has old data.
            # Use tail of /v1/live by row-id, independent of wall-clock range.
            if not rows:
                try:
                    bootstrap_payload = self._source_request_json(
                        source,
                        "GET",
                        "/v1/live",
                        query={"bootstrap": 1},
                        timeout_s=timeout_s,
                    )
                except Exception:
                    bootstrap_payload = {}
                next_sample_id = 0
                if bool(bootstrap_payload.get("ok", False)):
                    try:
                        next_sample_id = int(bootstrap_payload.get("next_sample_id", 0) or 0)
                    except (TypeError, ValueError):
                        next_sample_id = 0
                if next_sample_id > 0:
                    tail_since = max(0, int(next_sample_id) - 6000)
                    try:
                        tail_payload = self._source_request_json(
                            source,
                            "GET",
                            "/v1/live",
                            query={
                                "since_sample_id": int(tail_since),
                                "since_event_id": 0,
                                "sample_limit": 6000,
                                "event_limit": 1,
                            },
                            timeout_s=timeout_s,
                        )
                    except Exception:
                        tail_payload = {}
                    if bool(tail_payload.get("ok", False)):
                        tail_samples = tail_payload.get("samples")
                        if isinstance(tail_samples, list):
                            for item in tail_samples:
                                if not isinstance(item, dict):
                                    continue
                                rows.append(
                                    {
                                        "tag_id": str(item.get("tag_id") or item.get("signal_id") or ""),
                                        "tag_name": str(item.get("tag_name") or ""),
                                        "ts": item.get("ts"),
                                        "value": item.get("value"),
                                    }
                                )
                            rows.sort(key=lambda item: float(item.get("ts", 0.0)))

            if rows:
                self._append_remote_samples_rows(source, rows, tags_map)
                loaded_any = True
        return loaded_any

    def _append_db_live_sample_rows(self, rows: list[tuple[int, str, float, float]]) -> None:
        if not rows:
            return

        signal_name_by_id = {str(item.id): str(item.name) for item in self.current_profile.signals if str(item.id)}
        batch: list[tuple[float, dict[str, tuple[str, float]]]] = []
        current_ts: float | None = None
        current_samples: dict[str, tuple[str, float]] = {}
        for _row_id, signal_id, ts, value in rows:
            sid = str(signal_id or "").strip()
            if not sid:
                continue
            try:
                ts_f = float(ts)
                value_f = float(value)
            except (TypeError, ValueError):
                continue

            if current_ts is None or abs(float(current_ts) - ts_f) > 1e-9:
                if current_ts is not None and current_samples:
                    batch.append((float(current_ts), current_samples))
                current_ts = ts_f
                current_samples = {}
            signal_name = signal_name_by_id.get(sid, sid)
            current_samples[sid] = (signal_name, value_f)
            self._last_live_values[sid] = (signal_name, value_f)

        if current_ts is not None and current_samples:
            batch.append((float(current_ts), current_samples))
        if not batch:
            return
        self._live_cycle_has_new_samples = True

        if bool(self.current_profile.render_chart_enabled):
            self._pending_render_samples.extend(batch)
            if len(self._pending_render_samples) > self._max_pending_render_batches:
                self._pending_render_samples = self._pending_render_samples[-self._max_pending_render_batches :]
            if not self._render_timer.isActive():
                self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
                self._render_timer.start()

    def _append_db_live_connection_rows(self, rows: list[tuple[int, float, int]]) -> None:
        if not rows:
            return

        for _row_id, ts, state in rows:
            try:
                ts_f = float(ts)
                state_b = bool(int(state))
            except (TypeError, ValueError):
                continue

            if self._last_connection_state is None or self._last_connection_state != state_b:
                event = [ts_f, 1.0 if state_b else 0.0]
                self._connection_events.append(event)
                if bool(self.current_profile.render_chart_enabled):
                    self.chart.add_connection_event(ts_f, state_b)
                self._last_connection_state = state_b
            self._runtime_connected = state_b

    def _append_ui_heartbeat_if_needed(self, connected_now: bool) -> None:
        # Keep the chart timeline moving in online mode even when
        # data arrive sparsely ("archive by changes") or connection state
        # flaps briefly. Connectivity is still reflected via connection_events.
        if not bool(self.current_profile.render_chart_enabled):
            return
        if not self._is_auto_x_enabled():
            return
        if self._live_cycle_has_new_samples:
            return
        if not self._last_live_values:
            return
        now_ts = float(datetime.now().timestamp())
        min_period_s = max(0.2, float(self.current_profile.render_interval_ms) / 1000.0)
        if (now_ts - float(self._last_ui_heartbeat_ts)) < min_period_s:
            return
        self._pending_render_samples.append((now_ts, dict(self._last_live_values)))
        if len(self._pending_render_samples) > self._max_pending_render_batches:
            self._pending_render_samples = self._pending_render_samples[-self._max_pending_render_batches :]
        if not self._render_timer.isActive():
            self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
            self._render_timer.start()
        self._last_ui_heartbeat_ts = now_ts

    def _sync_effective_connection_state(self, connected_now: bool, ts: float | None = None) -> None:
        state = bool(connected_now)
        if self._last_connection_state is not None and self._last_connection_state == state:
            return
        event_ts = float(datetime.now().timestamp() if ts is None else ts)
        event = [event_ts, 1.0 if state else 0.0]
        self._connection_events.append(event)
        if bool(self.current_profile.render_chart_enabled):
            self.chart.add_connection_event(event_ts, state)
        self._last_connection_state = state

    def _poll_db_live_stream(self) -> None:
        if not self._db_live_running:
            return
        if str(self.mode_combo.currentData() or "online") != "online":
            return

        self._live_cycle_has_new_samples = False

        local_connected = False
        if self._local_live_enabled:
            conn = None
            if self._open_db_live_connection():
                conn = self._db_live_conn

            if conn is not None:
                try:
                    sample_rows = conn.execute(
                        """
                        SELECT id, signal_id, ts, value
                        FROM samples
                        WHERE profile_id = ? AND id > ?
                        ORDER BY id ASC
                        LIMIT 12000
                        """,
                        (self.current_profile.id, int(self._db_live_last_sample_row_id)),
                    ).fetchall()
                    if sample_rows:
                        self._db_live_last_sample_row_id = int(sample_rows[-1][0])
                        self._append_db_live_sample_rows(sample_rows)

                    connection_rows = conn.execute(
                        """
                        SELECT id, ts, is_connected
                        FROM connection_events
                        WHERE profile_id = ? AND id > ?
                        ORDER BY id ASC
                        LIMIT 4000
                        """,
                        (self.current_profile.id, int(self._db_live_last_connection_event_row_id)),
                    ).fetchall()
                    if connection_rows:
                        self._db_live_last_connection_event_row_id = int(connection_rows[-1][0])
                        self._append_db_live_connection_rows(connection_rows)
                except Exception:
                    self._close_db_live_connection()
                    conn = None

            if conn is not None:
                if not self._is_external_recorder_running():
                    local_connected = False
                else:
                    status_payload = read_recorder_status() or {}
                    if str(status_payload.get("profile_id") or "") == str(self.current_profile.id):
                        try:
                            local_connected = bool(status_payload.get("connected", False))
                        except Exception:
                            local_connected = False

        remote_connected = self._poll_remote_live_stream()
        connected_now = bool(local_connected or remote_connected)
        self._sync_effective_connection_state(connected_now)
        self._append_ui_heartbeat_if_needed(connected_now)
        self._runtime_connected = connected_now
        self._update_runtime_status_panel()
        self._update_recorder_dependent_ui_state()

    def _record_shutdown_disconnect_event(self) -> None:
        if self._archive_store is None:
            return
        if self._last_connection_state is False:
            return
        ts = datetime.now().timestamp()
        event = [float(ts), 0.0]
        if self._connection_events and int(self._connection_events[-1][1]) == 0:
            self._last_connection_state = False
            return
        self._connection_events.append(event)
        self.chart.add_connection_event(ts, False)
        try:
            self._archive_store.insert_connection_event(self.current_profile.id, ts, False)
        except Exception:
            pass
        self._last_connection_state = False

    def _restart_worker(self, history_span_s: float | None = None) -> None:
        self._stop_worker()
        self._start_worker(apply_profile=False, history_span_s=history_span_s)

    def _start_worker(self, apply_profile: bool = True, history_span_s: float | None = None) -> None:
        mode = str(self.mode_combo.currentData() or "online")
        if mode != "online":
            self._apply_work_mode_ui("offline")
            return

        if history_span_s is None:
            history_span_s = self._preferred_live_history_span_s()

        if apply_profile:
            self._apply_current_profile(restart_live=False, history_span_s=history_span_s)

        if should_force_auto_x_on_start(self._is_auto_x_enabled()):
            self.chart.set_auto_x(True)
            if hasattr(self, "action_auto_x"):
                self.action_auto_x.blockSignals(True)
                self.action_auto_x.setChecked(True)
                self.action_auto_x.blockSignals(False)
            if hasattr(self, "values_auto_x_checkbox"):
                self.values_auto_x_checkbox.blockSignals(True)
                self.values_auto_x_checkbox.setChecked(True)
                self.values_auto_x_checkbox.blockSignals(False)

        if self._is_live_stream_running():
            return

        self._last_live_values = {}
        self._live_cycle_has_new_samples = False
        self._last_ui_heartbeat_ts = 0.0

        auto_applied_remote, auto_failed_remote = self._auto_sync_remote_profiles_for_live()
        if auto_applied_remote > 0:
            self.status_label.setText(
                f"Статус: синхронизация удалённых источников выполнена ({auto_applied_remote})"
            )
        elif auto_failed_remote > 0:
            self.status_label.setText(
                f"Статус: часть удалённых источников не синхронизирована ({auto_failed_remote})"
            )

        # Try to repair missing/invalid remote tag bindings before live start,
        # so all rows can receive updates even after config edits/import drifts.
        self._try_repair_remote_bindings()

        needs_local_stream = any(
            str(getattr(signal, "source_id", "local") or "local") == "local" for signal in self.current_profile.signals
        )
        has_remote_stream = bool(self._remote_signal_mapping())
        self._local_live_enabled = bool(needs_local_stream)

        if self._local_live_enabled and not self._is_external_recorder_running():
            started = self._start_external_recorder(silent=True)
            if started:
                # Refresh status after successful auto-start.
                self._update_recorder_dependent_ui_state()
            if self._is_external_recorder_running():
                pass
            elif has_remote_stream:
                self._local_live_enabled = False
                self.status_label.setText('Статус: локальный TrendRecorder не запущен, запущен только удаленный live-поток')
            else:
                self.status_label.setText(
                    'Ошибка: TrendRecorder не запущен и не удалось запустить его автоматически'
                )
                self._update_recorder_dependent_ui_state()
                return

        if self._local_live_enabled:
            self._close_db_live_connection()
            if self._local_live_enabled and not self._open_db_live_connection():
                if has_remote_stream:
                    self._local_live_enabled = False
                    self.status_label.setText(
                        'Статус: локальная БД недоступна, запущен только удаленный live-поток'
                    )
                else:
                    self.status_label.setText('Ошибка: не удалось открыть архив БД для live-просмотра')
                    return
        else:
            self._close_db_live_connection()

        restored = False
        if bool(self.current_profile.render_chart_enabled) and self._local_live_enabled:
            restored = self._load_recent_online_history_from_db(
                adjust_x_range=True,
                silent=True,
                span_override_s=history_span_s,
            )
            try:
                x_left, x_right = self.chart.current_x_range()
                span_s = max(10.0, float(x_right - x_left))
            except Exception:
                span_s = 120.0
            self._load_recent_remote_history_from_sources(span_s)
        elif bool(self.current_profile.render_chart_enabled):
            self._load_recent_remote_history_from_sources(max(10.0, float(history_span_s or 120.0)))
        if not restored:
            self._connection_events = []
            self._last_connection_state = None
            self.chart.set_connection_events([])
        self._runtime_connected = bool(self._last_connection_state) if self._last_connection_state is not None else False
        if not bool(self.current_profile.render_chart_enabled):
            self.chart.clear_data()
        self._pending_render_samples = []

        self._bootstrap_db_live_cursors()
        self._db_live_running = True
        self._db_live_timer.setInterval(max(120, min(2000, int(self.current_profile.poll_interval_ms))))
        if not self._db_live_timer.isActive():
            self._db_live_timer.start()

        self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
        if bool(self.current_profile.render_chart_enabled) and not self._render_timer.isActive():
            self._render_timer.start()

        self.action_start.setEnabled(False)
        self.action_stop.setEnabled(True)
        self._poll_db_live_stream()
        self._update_recorder_dependent_ui_state()
        self.status_label.setText('Статус: просмотр из БД запущен')

    def _stop_worker(self) -> None:
        if self._db_live_timer.isActive():
            self._db_live_timer.stop()
        self._db_live_running = False
        self._local_live_enabled = True
        self._remote_live_cursors = {}
        self._last_live_values = {}
        self._live_cycle_has_new_samples = False
        self._last_ui_heartbeat_ts = 0.0
        for future in list(self._remote_live_futures.values()):
            try:
                future.cancel()
            except Exception:
                pass
        self._remote_live_futures = {}
        self._remote_live_backoff_until_mono = {}
        self._remote_live_fail_count = {}
        self._remote_last_ok_mono = {}
        self._close_db_live_connection()

        if self._worker is not None:
            self._stopping_worker = True
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
            self._stopping_worker = False

        if self._archive_store is not None:
            self._archive_store.close()
            self._archive_store = None

        if self._render_timer.isActive():
            self._render_timer.stop()
        self._flush_pending_render_samples()

        mode = str(self.mode_combo.currentData() or "online")
        self.action_start.setEnabled(mode == "online")
        self.action_stop.setEnabled(False)
        self._runtime_connected = False
        self._update_runtime_status_panel()
        self._reset_history_window_cache()
        self._update_recorder_dependent_ui_state()
        self.status_label.setText('Статус: остановлено')

    def _on_connection_changed(self, is_connected: bool) -> None:
        self._runtime_connected = bool(is_connected)
        self._update_runtime_status_panel()

        state = bool(is_connected)
        if self._last_connection_state is None or self._last_connection_state != state:
            ts = datetime.now().timestamp()
            event = [float(ts), 1.0 if state else 0.0]
            is_stop_disconnect = self._stopping_worker and not state
            if not is_stop_disconnect:
                self._connection_events.append(event)
                if bool(self.current_profile.render_chart_enabled):
                    self.chart.add_connection_event(ts, state)
                if self._archive_store is not None:
                    try:
                        self._archive_store.insert_connection_event(self.current_profile.id, ts, state)
                    except Exception as exc:
                        self.status_label.setText(f"РћС€РёР±РєР° Р°СЂС…РёРІР°С†РёРё СЃРѕСЃС‚РѕСЏРЅРёСЏ СЃРІСЏР·Рё: {exc}")
            self._last_connection_state = state

        if self._is_live_stream_running():
            self.action_start.setEnabled(False)
            self.action_stop.setEnabled(True)

    def _on_chart_auto_mode_changed(self, auto_x: bool, auto_y: bool) -> None:
        prev_auto_x = bool(self.action_auto_x.isChecked()) if hasattr(self, "action_auto_x") else bool(auto_x)
        self.action_auto_x.blockSignals(True)
        self.action_auto_x.setChecked(auto_x)
        self.action_auto_x.blockSignals(False)
        if hasattr(self, "values_auto_x_checkbox"):
            self.values_auto_x_checkbox.blockSignals(True)
            self.values_auto_x_checkbox.setChecked(bool(auto_x))
            self.values_auto_x_checkbox.blockSignals(False)
        if prev_auto_x != bool(auto_x):
            mode = str(self.mode_combo.currentData() or "online")
            if mode == "online" and bool(self.current_profile.render_chart_enabled):
                if bool(auto_x):
                    self._pending_render_samples = []
                    self._load_recent_online_history_from_db(adjust_x_range=True, silent=True)
                    self._reset_history_window_cache()
                else:
                    self._schedule_visible_history_reload(force=False)
        self._mark_config_dirty()

    def _on_chart_cursor_enabled_changed(self, enabled: bool) -> None:
        state = bool(enabled)
        self.action_cursor.blockSignals(True)
        self.action_cursor.setChecked(state)
        self.action_cursor.blockSignals(False)
        if hasattr(self, "values_cursor_checkbox"):
            self.values_cursor_checkbox.blockSignals(True)
            self.values_cursor_checkbox.setChecked(state)
            self.values_cursor_checkbox.blockSignals(False)
        self._mark_config_dirty()

    def _set_scale_row_editable(self, row: int, editable: bool) -> None:
        min_item = self.scales_table.item(row, 2)
        max_item = self.scales_table.item(row, 3)
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        if min_item is not None:
            min_item.setFlags(flags)
        if max_item is not None:
            max_item.setFlags(flags)

    def _update_scales_table(self, rows: list[dict]) -> None:
        need_fit = self._scales_table_last_rowcount != len(rows)
        self._updating_scales_table = True
        self.scales_table.blockSignals(True)
        self.scales_table.setRowCount(len(rows))

        for idx, row in enumerate(rows):
            axis_index = int(row.get("axis_index", idx + 1))
            auto_y = bool(row.get("auto_y", True))
            y_min = float(row.get("y_min", 0.0))
            y_max = float(row.get("y_max", 1.0))
            signal_names = row.get("signal_names", [])
            signal_text = ", ".join(str(name) for name in signal_names)

            axis_item = QTableWidgetItem(str(axis_index))
            axis_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.scales_table.setItem(idx, 0, axis_item)

            auto_item = QTableWidgetItem()
            auto_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            auto_item.setCheckState(Qt.CheckState.Checked if auto_y else Qt.CheckState.Unchecked)
            self.scales_table.setItem(idx, 1, auto_item)

            min_item = QTableWidgetItem(f"{y_min:.6g}")
            max_item = QTableWidgetItem(f"{y_max:.6g}")
            self.scales_table.setItem(idx, 2, min_item)
            self.scales_table.setItem(idx, 3, max_item)
            self._set_scale_row_editable(idx, not auto_y)

            sig_item = QTableWidgetItem(signal_text)
            sig_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.scales_table.setItem(idx, 4, sig_item)

        self.scales_table.blockSignals(False)
        applied_saved = False
        if isinstance(getattr(self, "current_profile", None), ProfileConfig):
            applied_saved = self._apply_saved_table_column_widths(
                self.current_profile,
                "scales_table",
                self.scales_table,
            )
        if need_fit and not applied_saved:
            header = self.scales_table.horizontalHeader()
            self.scales_table.resizeColumnsToContents()
            for col in range(self.scales_table.columnCount()):
                width = self.scales_table.columnWidth(col)
                self.scales_table.setColumnWidth(col, min(max(56, width + 12), 420))
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._scales_table_last_rowcount = len(rows)
        self._updating_scales_table = False

    def _on_scale_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_scales_table:
            return
        row = item.row()
        axis_item = self.scales_table.item(row, 0)
        if axis_item is None:
            return
        try:
            axis_index = int(axis_item.text())
        except ValueError:
            return

        if item.column() == 1:
            auto_y = item.checkState() == Qt.CheckState.Checked
            self.chart.set_axis_auto_y(axis_index, auto_y)
            self._mark_config_dirty()
            return

        if item.column() not in (2, 3):
            return

        auto_item = self.scales_table.item(row, 1)
        if auto_item is not None and auto_item.checkState() == Qt.CheckState.Checked:
            return

        min_item = self.scales_table.item(row, 2)
        max_item = self.scales_table.item(row, 3)
        if min_item is None or max_item is None:
            return

        try:
            y_min = float(min_item.text().replace(",", "."))
            y_max = float(max_item.text().replace(",", "."))
        except ValueError:
            self.status_label.setText('Ошибка: Мин/Макс должны быть числами')
            return

        if y_max <= y_min:
            self.status_label.setText('Ошибка: Макс должен быть больше Мин')
            return

        self.chart.set_axis_range(axis_index, y_min, y_max)
        self._mark_config_dirty()

    def _update_values_table(self, rows: list[dict], _cursor_visible: bool) -> None:
        self._last_values_rows = list(rows)
        active_rows = [row for row in list(rows) if bool(row.get("enabled", True))]
        sorted_rows = self._sorted_values_rows(active_rows)
        signal_source_by_id: dict[str, str] = {}
        for signal in getattr(self.current_profile, "signals", []):
            signal_source_by_id[str(getattr(signal, "id", ""))] = str(getattr(signal, "source_id", "local") or "local")
        # Include latest source mapping from the editable signal table (if differs from profile snapshot).
        if hasattr(self, "signal_table"):
            for row_idx in range(self.signal_table.rowCount()):
                name_item = self.signal_table.item(row_idx, 1)
                if name_item is None:
                    continue
                sig_id = str(name_item.data(ROLE_SIGNAL_ID) or "").strip()
                if not sig_id:
                    continue
                sig_source_id = str(name_item.data(ROLE_SIGNAL_SOURCE_ID) or "local")
                signal_source_by_id[sig_id] = sig_source_id
        sources_by_id = {str(item.id): item for item in self._collect_sources_table()}
        self._updating_values_table = True
        try:
            self.values_table.setRowCount(len(sorted_rows))
            for idx, row in enumerate(sorted_rows):
                signal_id = str(row.get("signal_id", ""))
                enabled = bool(row.get("enabled", True))
                color = str(row.get("color", "#1f77b4"))

                visible_checkbox = self.values_table.cellWidget(idx, 0)
                if not isinstance(visible_checkbox, QCheckBox) or str(visible_checkbox.property("signal_id") or "") != signal_id:
                    visible_checkbox = QCheckBox()
                    visible_checkbox.setProperty("signal_id", signal_id)
                    visible_checkbox.toggled.connect(
                        lambda checked, sid=signal_id: self._on_values_visibility_toggled(sid, checked)
                    )
                    self.values_table.setCellWidget(idx, 0, visible_checkbox)
                visible_checkbox.blockSignals(True)
                visible_checkbox.setChecked(enabled)
                visible_checkbox.blockSignals(False)

                signal_text = str(row.get("name", ""))
                signal_name = self.values_table.item(idx, 1)
                if signal_name is None:
                    signal_name = QTableWidgetItem()
                    self.values_table.setItem(idx, 1, signal_name)
                if signal_name.text() != signal_text:
                    signal_name.setText(signal_text)
                signal_name.setForeground(QColor(color))

                axis_index = int(row.get("axis_index", 1))
                axis_item = self.values_table.item(idx, 2)
                if axis_item is None:
                    axis_item = QTableWidgetItem()
                    self.values_table.setItem(idx, 2, axis_item)
                axis_text = str(axis_index)
                if axis_item.text() != axis_text:
                    axis_item.setText(axis_text)

                value = row.get("value")
                value_text = "-" if value is None else f"{float(value):.3f}"
                value_item = self.values_table.item(idx, 3)
                if value_item is None:
                    value_item = QTableWidgetItem()
                    self.values_table.setItem(idx, 3, value_item)
                if value_item.text() != value_text:
                    value_item.setText(value_text)

                ts = row.get("ts")
                if ts is None:
                    ts_text = "-"
                else:
                    ts_text = format_ts_ms(float(ts))
                ts_item = self.values_table.item(idx, 4)
                if ts_item is None:
                    ts_item = QTableWidgetItem()
                    self.values_table.setItem(idx, 4, ts_item)
                if ts_item.text() != ts_text:
                    ts_item.setText(ts_text)

                source_id = signal_source_by_id.get(signal_id, "local")
                mode = self._source_label_for_source_id(source_id, sources_by_id)
                if not enabled:
                    mode = f"{mode} (скрыт)"
                mode_item = self.values_table.item(idx, 5)
                if mode_item is None:
                    mode_item = QTableWidgetItem()
                    self.values_table.setItem(idx, 5, mode_item)
                if mode_item.text() != mode:
                    mode_item.setText(mode)

                color_button = self.values_table.cellWidget(idx, 6)
                if not isinstance(color_button, QPushButton) or str(color_button.property("signal_id") or "") != signal_id:
                    color_button = QPushButton()
                    color_button.setProperty("signal_id", signal_id)
                    color_button.clicked.connect(lambda _checked=False, sid=signal_id: self._on_values_color_clicked(sid))
                    self.values_table.setCellWidget(idx, 6, color_button)
                if str(color_button.property("color_hex") or "") != color:
                    color_button.setProperty("color_hex", color)
                    self._apply_color_swatch_style(color_button, color)
        finally:
            self._updating_values_table = False
        if isinstance(getattr(self, "current_profile", None), ProfileConfig):
            self._apply_saved_table_column_widths(self.current_profile, "values_table", self.values_table)

    def _on_worker_error(self, message: str) -> None:
        self.status_label.setText(f"РћС€РёР±РєР°: {message}")

    def _prune_archive_retention_if_needed(self, ts: float) -> None:
        if self._archive_store is None:
            return
        retention_days = max(0, int(self.current_profile.archive_retention_days))
        if retention_days <= 0:
            return
        # Avoid expensive DELETE on every sample batch.
        if ts - self._last_retention_cleanup_ts < 60.0:
            return

        cutoff_ts = float(ts) - float(retention_days) * 86400.0
        try:
            self._archive_store.prune_older_than(self.current_profile.id, cutoff_ts)
            self._last_retention_cleanup_ts = float(ts)
        except Exception as exc:
            self.status_label.setText(f"РћС€РёР±РєР° РѕС‡РёСЃС‚РєРё Р°СЂС…РёРІР°: {exc}")

    def _should_archive_signal_sample(self, signal_id: str, ts: float, value: float) -> bool:
        last_value = self._archive_last_values.get(signal_id)
        last_ts = self._archive_last_written_ts.get(signal_id)
        if last_value is None or last_ts is None:
            return True

        keepalive_s = max(0.0, float(self.current_profile.archive_keepalive_s))
        if keepalive_s > 0.0 and (float(ts) - float(last_ts)) >= keepalive_s:
            return True

        signal_type = str(self._signal_types_by_id.get(signal_id, "int16")).lower()
        if signal_type == "bool":
            # BOOL should be stored on edge changes only.
            return int(round(float(value))) != int(round(float(last_value)))

        deadband = max(0.0, float(self.current_profile.archive_deadband))
        delta = abs(float(value) - float(last_value))
        if math.isnan(delta):
            return True
        return delta > deadband

    def _filter_archive_rows(self, ts: float, samples: dict[str, tuple[str, float]]) -> list[tuple[str, str, float]]:
        rows: list[tuple[str, str, float]] = []
        only_changes = bool(self.current_profile.archive_on_change_only)
        for signal_id, (signal_name, value) in samples.items():
            sid = str(signal_id)
            val = float(value)
            if only_changes and not self._should_archive_signal_sample(sid, ts, val):
                continue
            rows.append((sid, str(signal_name), val))
            self._archive_last_values[sid] = val
            self._archive_last_written_ts[sid] = float(ts)
        return rows

    def _on_samples_ready(self, ts: float, samples: dict[str, tuple[str, float]]) -> None:
        if bool(self.current_profile.render_chart_enabled):
            self._pending_render_samples.append((float(ts), samples))
            if len(self._pending_render_samples) > self._max_pending_render_batches:
                # Keep the most recent section of stream to avoid unbounded growth
                # if UI thread is temporarily slower than poll thread.
                self._pending_render_samples = self._pending_render_samples[-self._max_pending_render_batches :]
            if not self._render_timer.isActive():
                self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
                self._render_timer.start()
        else:
            self._pending_render_samples = []
            if self._render_timer.isActive():
                self._render_timer.stop()

        archive_interval_s = max(0.05, self.current_profile.archive_interval_ms / 1000.0)
        if ts - self._last_archive_ts < archive_interval_s:
            return

        if not bool(getattr(self.current_profile, "archive_to_db", True)):
            self._last_archive_ts = ts
            return

        if self._archive_store is None:
            return

        rows = self._filter_archive_rows(ts, samples)
        if not rows:
            self._last_archive_ts = ts
            return
        self._archive_store.insert_batch(self.current_profile.id, ts, rows)
        self._last_archive_ts = ts
        self._prune_archive_retention_if_needed(ts)

    def _is_archive_writing_active(self) -> bool:
        mode = str(self.current_profile.work_mode or "online")
        return mode == "online" and self._worker is not None and self._worker.isRunning() and bool(
            self.archive_to_db_checkbox.isChecked()
        )

    def _confirm_archive_stop(self) -> bool:
        if not self._is_archive_writing_active():
            return True
        answer = QMessageBox.question(
            self,
            'Подтверждение закрытия',
            'Сейчас идет запись архива. При закрытии приложения запись остановится.\nЗакрыть приложение?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _shutdown_remote_live_executor(self) -> None:
        for future in list(self._remote_live_futures.values()):
            try:
                future.cancel()
            except Exception:
                pass
        self._remote_live_futures = {}
        executor = self._remote_live_executor
        self._remote_live_executor = None
        if executor is not None:
            try:
                # Wait for worker threads to exit to avoid lingering handles in
                # frozen one-file shutdown.
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass

    def _has_unsaved_config_changes(self) -> bool:
        if self._config_dirty:
            return True
        try:
            self._store_ui_to_profile(self.current_profile)
        except Exception:
            return True
        try:
            persisted = self.config_store.load()
        except Exception:
            return True
        persisted_profile = None
        for profile in persisted.profiles:
            if str(profile.id) == str(self.current_profile.id):
                persisted_profile = profile
                break
        if persisted_profile is None:
            return True
        if self._normalize_profile_payload_for_dirty_check(
            persisted_profile.to_dict()
        ) != self._normalize_profile_payload_for_dirty_check(self.current_profile.to_dict()):
            return True
        if str(persisted.active_profile_id or "") != str(self.current_profile.id):
            return True
        if str(persisted.close_behavior or "") != str(self.app_config.close_behavior or ""):
            return True
        if bool(persisted.auto_start_windows) != bool(self.app_config.auto_start_windows):
            return True
        if bool(persisted.auto_connect_on_launch) != bool(self.app_config.auto_connect_on_launch):
            return True
        return False

    def _normalize_profile_payload_for_dirty_check(self, payload: dict) -> dict:
        normalized = copy.deepcopy(payload if isinstance(payload, dict) else {})
        ui_state = normalized.get("ui_state")
        if not isinstance(ui_state, dict):
            return normalized

        view = ui_state.get("view")
        if not isinstance(view, dict):
            return normalized

        if bool(view.get("auto_x", True)):
            # In Auto X mode range continuously moves and should not mark config as dirty.
            view.pop("x_range", None)

        scale_states = view.get("scale_states")
        if isinstance(scale_states, list):
            rows: list[dict] = []
            for row in scale_states:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                if bool(item.get("auto_y", True)):
                    # In Auto Y mode current min/max are runtime values.
                    item.pop("y_min", None)
                    item.pop("y_max", None)
                rows.append(item)
            view["scale_states"] = rows
        return normalized

    def _resolve_exit_save_policy(self) -> str:
        if not self._has_unsaved_config_changes():
            return "save"
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Несохраненные изменения')
        dialog.setText('Есть несохраненные изменения. Сохранить перед выходом?')
        save_btn = dialog.addButton('Сохранить', QMessageBox.ButtonRole.AcceptRole)
        discard_btn = dialog.addButton('Не сохранять', QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = dialog.addButton('Отмена', QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(save_btn)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == save_btn:
            return "save"
        if clicked == discard_btn:
            self._autosave_timer.stop()
            self._config_dirty = False
            return "discard"
        if clicked == cancel_btn:
            return "cancel"
        return "cancel"

    def closeEvent(self, event) -> None:
        if self._force_close:
            save_policy = self._resolve_exit_save_policy()
            if save_policy == "cancel":
                event.ignore()
                return
            self._stop_tags_polling(silent=True)
            self._record_shutdown_disconnect_event()
            self._stop_worker()
            self._shutdown_remote_live_executor()
            if save_policy == "save":
                self._save_config()
            if self._tray_icon is not None:
                self._tray_icon.hide()
            event.accept()
            return

        behavior = self.app_config.close_behavior
        if behavior == "tray":
            self._minimize_to_tray()
            event.ignore()
            return

        if behavior == "ask":
            dialog = QMessageBox(self)
            dialog.setWindowTitle('Закрытие приложения')
            dialog.setText('Выберите действие при закрытии.')
            tray_btn = dialog.addButton('В трей', QMessageBox.ButtonRole.ActionRole)
            close_btn = dialog.addButton('Закрыть', QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = dialog.addButton('Отмена', QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(close_btn)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked == tray_btn:
                self._minimize_to_tray()
                event.ignore()
                return
            if clicked == cancel_btn:
                event.ignore()
                return
            if clicked != close_btn:
                event.ignore()
                return

        if not self._confirm_archive_stop():
            event.ignore()
            return

        save_policy = self._resolve_exit_save_policy()
        if save_policy == "cancel":
            event.ignore()
            return

        self._force_close = True
        self._stop_tags_polling(silent=True)
        self._record_shutdown_disconnect_event()
        self._stop_worker()
        self._shutdown_remote_live_executor()
        if save_policy == "save":
            self._save_config()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        event.accept()


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


def run_app() -> None:
    setup_logging()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TrendAnalyzer.App")
        except Exception:
            pass

    instance_lock = SingleInstanceLock("trend_client")
    if not instance_lock.acquire():
        show_already_running_message(APP_NAME, 'Клиент уже запущен. Разрешен только один экземпляр.')
        return

    app = QApplication([])
    app._instance_lock = instance_lock  # type: ignore[attr-defined]
    icon = None
    icon_path = _resolve_icon_path()
    if icon_path is not None:
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    window = MainWindow()
    if icon is not None:
        window.setWindowIcon(icon)
    window.showMaximized()
    app.exec()
