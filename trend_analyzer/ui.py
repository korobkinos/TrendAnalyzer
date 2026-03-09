from __future__ import annotations

import copy
import csv
from datetime import datetime
import bisect
import ctypes
import math
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid
import zipfile

from PySide6.QtCore import QDateTime, QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPageLayout, QPageSize, QPen, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
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
    QMenu,
    QPlainTextEdit,
    QSystemTrayIcon,
    QToolButton,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
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

from .models import ProfileConfig, SignalConfig, TagConfig, TagTabConfig
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

from .chart import MultiAxisChart, format_ts_ms


class AutoClearStatusLabel(QLabel):
    def __init__(self, idle_text: str = "Статус: ожидание", clear_after_ms: int = 5000, parent: QWidget | None = None):
        super().__init__(idle_text, parent)
        self._idle_text = str(idle_text)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(max(500, int(clear_after_ms)))
        self._clear_timer.timeout.connect(self._restore_idle_text)

    def setText(self, text: str) -> None:  # type: ignore[override]
        value = str(text)
        super().setText(value)
        if value.startswith("Статус:") and value != self._idle_text:
            self._clear_timer.start()
        else:
            self._clear_timer.stop()

    def _restore_idle_text(self) -> None:
        super().setText(self._idle_text)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(app_title())
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
        self._values_sort_mode = "name_asc"
        self._last_values_rows: list[dict] = []
        self._force_close = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._worker: ModbusWorker | None = None
        self._archive_store: ArchiveStore | None = None
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
        self._config_dirty = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1200)
        self._autosave_timer.timeout.connect(self._autosave_config_if_dirty)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(False)
        self._render_timer.timeout.connect(self._flush_pending_render_samples)
        self._pending_render_samples: list[tuple[float, dict[str, tuple[str, float]]]] = []
        # Backpressure cap for UI queue to avoid high RAM usage when UI redraw
        # cannot keep up with very fast polling / many signals.
        self._max_pending_render_batches = 1200
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

        self.config_store = ConfigStore()
        self.app_config = self.config_store.load()
        self.current_profile = self._find_profile(self.app_config.active_profile_id)

        self._build_ui()
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
        self.mode_combo.addItem("Онлайн", "online")
        self.mode_combo.addItem("Офлайн", "offline")
        self.archive_to_db_checkbox = QCheckBox("Писать в БД")
        self.archive_to_db_checkbox.setChecked(True)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)

        self.status_label = AutoClearStatusLabel("Статус: ожидание", 5000, self)

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
        values_header_layout.addWidget(QLabel("Сортировка:"))
        self.values_sort_combo = QComboBox()
        self.values_sort_combo.addItem("Имя (А-Я)", "name_asc")
        self.values_sort_combo.addItem("Имя (Я-А)", "name_desc")
        self.values_sort_combo.addItem("Вид: активные", "visible_first")
        self.values_sort_combo.addItem("Вид: скрытые", "hidden_first")
        self.values_sort_combo.setCurrentIndex(0)
        self.values_sort_combo.currentIndexChanged.connect(self._on_values_sort_changed)
        values_header_layout.addWidget(self.values_sort_combo)
        self.values_auto_x_checkbox = QCheckBox("Авто X")
        self.values_auto_x_checkbox.setChecked(True)
        self.values_auto_x_checkbox.toggled.connect(self._on_values_auto_x_toggled)
        values_header_layout.addWidget(self.values_auto_x_checkbox)
        self.values_cursor_checkbox = QCheckBox("Курсор")
        self.values_cursor_checkbox.setChecked(False)
        self.values_cursor_checkbox.toggled.connect(self._on_values_cursor_toggled)
        values_header_layout.addWidget(self.values_cursor_checkbox)
        self.values_reset_zoom_btn = QPushButton("Сброс масштаба")
        self.values_reset_zoom_btn.clicked.connect(lambda _checked=False: self.chart.reset_view())
        values_header_layout.addWidget(self.values_reset_zoom_btn)
        self.values_statistics_btn = QPushButton("Статистика")
        self.values_statistics_btn.clicked.connect(self._show_statistics_window)
        values_header_layout.addWidget(self.values_statistics_btn)
        values_header_layout.addStretch(1)

        self.values_collapse_btn = QToolButton()
        self.values_collapse_btn.setText("—")
        self.values_collapse_btn.setToolTip("Свернуть/развернуть")
        self.values_collapse_btn.clicked.connect(self._toggle_values_panel)
        values_header_layout.addWidget(self.values_collapse_btn)

        values_expand_btn = QToolButton()
        values_expand_btn.setText("□")
        values_expand_btn.setToolTip("Развернуть панель")
        values_expand_btn.clicked.connect(self._expand_values_panel)
        values_header_layout.addWidget(values_expand_btn)

        values_close_btn = QToolButton()
        values_close_btn.setText("×")
        values_close_btn.setToolTip("Скрыть таблицу")
        values_close_btn.clicked.connect(self._close_values_panel)
        values_header_layout.addWidget(values_close_btn)

        values_layout.addWidget(values_header)

        self.values_table = QTableWidget(0, 7)
        self.values_table.setHorizontalHeaderLabels(["Вид", "Сигнал", "Шкала", "Значение", "Время", "Источник", "Цвет"])
        header = self.values_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(self.values_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(lambda *_args: self._on_table_column_resized("values_table"))
        self.values_table.verticalHeader().setVisible(False)
        self.values_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.values_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
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
        self.chart.display_updated.connect(self._update_values_table)
        self.chart.scales_changed.connect(self._update_scales_table)
        self.chart.export_image_requested.connect(self._export_chart_image)
        self.chart.export_csv_requested.connect(self._export_chart_csv)
        self.chart.print_requested.connect(self._print_chart)

        self._build_connection_window()
        self._build_signals_window()
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
        self.runtime_indicator_label = QLabel("●")
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

    @staticmethod
    def _format_bytes_human(size_bytes: int | float) -> str:
        value = max(0.0, float(size_bytes))
        units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
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

    def _update_runtime_status_panel(self) -> None:
        try:
            connected = bool(self._runtime_connected and self._worker is not None and self._worker.isRunning())
            indicator_color = "#70d26f" if connected else "#ef5350"
            indicator_text = "подключено" if connected else "не подключено"
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
                archive_mode_text = "архив выкл"
            elif bool(getattr(self.current_profile, "archive_on_change_only", False)):
                archive_mode_text = "архив: изменения"
            else:
                archive_mode_text = "архив: все точки"
            if hasattr(self, "runtime_indicator_label"):
                self.runtime_indicator_label.setStyleSheet(f"font-size: 12px; color: {indicator_color};")
            if hasattr(self, "runtime_status_label"):
                self.runtime_status_label.setText(
                    f"{indicator_text}, CPU {cpu_percent:.1f}%, RAM {mem_text}, Архив {archive_text}, {archive_mode_text}"
                )
        except Exception:
            if hasattr(self, "runtime_indicator_label"):
                self.runtime_indicator_label.setStyleSheet("font-size: 12px; color: #ef5350;")
            if hasattr(self, "runtime_status_label"):
                self.runtime_status_label.setText("не подключено, CPU -, RAM -, Архив -")

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
        self.connection_window.setWindowTitle("Настройки подключения")
        self.connection_window.resize(520, 520)
        self.connection_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.connection_window)

        top_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        top_row.addWidget(QLabel("Профиль"))
        top_row.addWidget(self.profile_combo, 1)
        layout.addLayout(top_row)

        button_row = QHBoxLayout()
        self.new_profile_btn = QPushButton("Новый")
        self.clone_profile_btn = QPushButton("Клон")
        self.delete_profile_btn = QPushButton("Удалить")
        self.save_profiles_btn = QPushButton("Сохранить")
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
        self.archive_interval_spin = QSpinBox()
        self.archive_interval_spin.setRange(50, 600000)
        self.archive_interval_spin.setSuffix(" ms")
        self.archive_on_change_checkbox = QCheckBox("Только при изменении")
        self.archive_on_change_checkbox.toggled.connect(self._on_archive_filter_settings_changed)
        self.archive_deadband_spin = QDoubleSpinBox()
        self.archive_deadband_spin.setRange(0.0, 1_000_000_000.0)
        self.archive_deadband_spin.setDecimals(6)
        self.archive_deadband_spin.setSingleStep(0.001)
        self.archive_deadband_spin.valueChanged.connect(self._on_archive_filter_settings_changed)
        self.archive_keepalive_spin = QSpinBox()
        self.archive_keepalive_spin.setRange(0, 86400)
        self.archive_keepalive_spin.setSuffix(" c")
        self.archive_keepalive_spin.setSpecialValueText("Отключен")
        self.archive_keepalive_spin.valueChanged.connect(self._on_archive_filter_settings_changed)
        self.archive_retention_days_spin = QSpinBox()
        self.archive_retention_days_spin.setRange(0, 3650)
        self.archive_retention_days_spin.setSuffix(" дн")
        self.archive_retention_days_spin.setSpecialValueText("Без ограничения")
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30.0)
        self.timeout_spin.setSingleStep(0.1)
        self.timeout_spin.setSuffix(" s")
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.address_offset_spin = QSpinBox()
        self.address_offset_spin.setRange(-10, 10)

        form.addRow("Имя профиля", self.profile_name_edit)
        form.addRow("IP", self.ip_edit)
        form.addRow("Порт", self.port_spin)
        form.addRow("ID устройства (Unit ID)", self.unit_id_spin)
        form.addRow("Частота опроса", self.poll_interval_spin)
        form.addRow("Интервал отрисовки", self.render_interval_spin)
        form.addRow("Частота архивации", self.archive_interval_spin)
        form.addRow("Архив: только изменения", self.archive_on_change_checkbox)
        form.addRow("Архив: deadband", self.archive_deadband_spin)
        form.addRow("Архив: keepalive", self.archive_keepalive_spin)
        form.addRow("Глубина архива", self.archive_retention_days_spin)
        form.addRow("Таймаут", self.timeout_spin)
        form.addRow("Повторы", self.retries_spin)
        form.addRow("Смещение адреса", self.address_offset_spin)
        layout.addLayout(form)

        self.apply_btn = QPushButton("Применить")
        self.apply_btn.clicked.connect(self._apply_current_profile)
        self.clear_archive_db_btn = QPushButton("Очистить архив БД")
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
        self.signals_window.setWindowTitle("Сигналы графика")
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
        self.signals_reg_combo.addItem("Holding (хран.)", "holding")
        self.signals_reg_combo.addItem("Input (вход.)", "input")
        self.signals_type_combo = QComboBox()
        self.signals_type_combo.addItem("INT16 (знак.)", "int16")
        self.signals_type_combo.addItem("UINT16 (без знака)", "uint16")
        self.signals_type_combo.addItem("REAL / FLOAT32", "float32")
        self.signals_type_combo.addItem("BOOL (бит)", "bool")
        self.signals_float_order_combo = QComboBox()
        self.signals_float_order_combo.addItem("ABCD", "ABCD")
        self.signals_float_order_combo.addItem("BADC", "BADC")
        self.signals_float_order_combo.addItem("CDAB", "CDAB")
        self.signals_float_order_combo.addItem("DCBA", "DCBA")
        self.signals_type_combo.currentIndexChanged.connect(self._on_signals_bulk_type_changed)
        self.signals_axis_spin = QSpinBox()
        self.signals_axis_spin.setRange(1, 64)
        self.signals_axis_spin.setValue(1)
        self.signals_add_range_btn = QPushButton("Добавить диапазон")
        self.signals_add_range_btn.clicked.connect(self._on_add_signal_range_clicked)

        bulk_row.addWidget(QLabel("Старт"))
        bulk_row.addWidget(self.signals_start_addr_spin)
        bulk_row.addWidget(QLabel("Кол-во"))
        bulk_row.addWidget(self.signals_count_spin)
        bulk_row.addWidget(QLabel("Шаг"))
        bulk_row.addWidget(self.signals_step_spin)
        bulk_row.addWidget(self.signals_reg_combo)
        bulk_row.addWidget(self.signals_type_combo)
        bulk_row.addWidget(self.signals_float_order_combo)
        bulk_row.addWidget(QLabel("Шкала"))
        bulk_row.addWidget(self.signals_axis_spin)
        bulk_row.addWidget(self.signals_add_range_btn)
        bulk_row.addStretch(1)
        layout.addLayout(bulk_row)
        self._on_signals_bulk_type_changed(self.signals_type_combo.currentIndex())

        self.signal_table = QTableWidget(0, 10)
        self.signal_table.setHorizontalHeaderLabels(
            ["Вкл", "Имя", "Адрес", "Регистр", "Тип", "Бит", "Шкала", "Порядок REAL", "Коэфф.", "Цвет"]
        )
        signal_header = self.signal_table.horizontalHeader()
        signal_header.setStretchLastSection(False)
        signal_header.sectionResized.connect(lambda *_args: self._on_table_column_resized("signal_table"))
        layout.addWidget(self.signal_table, 1)

        signal_buttons = QHBoxLayout()
        self.add_signal_btn = QPushButton("+ Сигнал")
        self.copy_signal_btn = QPushButton("Копировать")
        self.remove_signal_btn = QPushButton("- Сигнал")
        self.save_signals_config_btn = QPushButton("Сохранить конфигурацию")
        self.signal_columns_btn = QPushButton("Колонки...")
        signal_buttons.addWidget(self.add_signal_btn)
        signal_buttons.addWidget(self.copy_signal_btn)
        signal_buttons.addWidget(self.remove_signal_btn)
        signal_buttons.addWidget(self.signal_columns_btn)
        signal_buttons.addStretch(1)
        signal_buttons.addWidget(self.save_signals_config_btn)
        layout.addLayout(signal_buttons)

        self.add_signal_btn.clicked.connect(self._on_add_signal_clicked)
        self.copy_signal_btn.clicked.connect(self._on_copy_signal_clicked)
        self.remove_signal_btn.clicked.connect(self._on_remove_signal_rows_clicked)
        self.signal_columns_btn.clicked.connect(self._show_signal_columns_menu)
        self.save_signals_config_btn.clicked.connect(self._save_from_signals_window)
        self.signal_table.itemChanged.connect(self._on_signal_table_item_changed)

        self._signal_column_actions: dict[int, QAction] = {}

    def _build_tags_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.tags_window = QDialog(self, flags)
        self.tags_window.setWindowTitle("Регистры Modbus")
        self.tags_window.resize(980, 620)
        self.tags_window.setSizeGripEnabled(True)
        self.tags_window.finished.connect(lambda _code: self._stop_tags_polling(silent=True))
        layout = QVBoxLayout(self.tags_window)

        tabs_row = QHBoxLayout()
        tabs_row.addWidget(QLabel("Вкладки"))
        self.tags_tabbar = QTabBar()
        self.tags_tabbar.setMovable(True)
        self.tags_tabbar.setExpanding(False)
        self.tags_tabbar.setTabsClosable(True)
        self.tags_tabbar.currentChanged.connect(self._on_tags_tab_changed)
        self.tags_tabbar.tabMoved.connect(self._on_tags_tab_moved)
        self.tags_tabbar.tabCloseRequested.connect(self._on_tags_tab_close_requested)
        tabs_row.addWidget(self.tags_tabbar, 1)
        self.tags_add_tab_btn = QPushButton("+ Вкладка")
        self.tags_add_tab_btn.clicked.connect(self._on_add_tags_tab_clicked)
        tabs_row.addWidget(self.tags_add_tab_btn)
        self.tags_rename_tab_btn = QPushButton("Переименовать")
        self.tags_rename_tab_btn.clicked.connect(self._on_rename_tags_tab_clicked)
        tabs_row.addWidget(self.tags_rename_tab_btn)
        layout.addLayout(tabs_row)

        bulk_row = QHBoxLayout()
        self.tags_start_addr_spin = QSpinBox()
        self.tags_start_addr_spin.setRange(0, 65535)
        self.tags_count_spin = QSpinBox()
        self.tags_count_spin.setRange(1, 1000)
        self.tags_step_spin = QSpinBox()
        self.tags_step_spin.setRange(1, 1000)
        self.tags_reg_combo = QComboBox()
        self.tags_reg_combo.addItem("Holding (хран.)", "holding")
        self.tags_reg_combo.addItem("Input (вход.)", "input")
        self.tags_type_combo = QComboBox()
        self.tags_type_combo.addItem("INT16 (знак.)", "int16")
        self.tags_type_combo.addItem("UINT16 (без знака)", "uint16")
        self.tags_type_combo.addItem("REAL / FLOAT32", "float32")
        self.tags_type_combo.addItem("BOOL (бит)", "bool")
        self.tags_float_order_combo = QComboBox()
        self.tags_float_order_combo.addItem("ABCD (обычный)", "ABCD")
        self.tags_float_order_combo.addItem("BADC (swap bytes)", "BADC")
        self.tags_float_order_combo.addItem("CDAB (swap words)", "CDAB")
        self.tags_float_order_combo.addItem("DCBA (reverse)", "DCBA")
        self.tags_add_range_btn = QPushButton("Добавить диапазон")
        self.tags_add_range_btn.clicked.connect(self._on_add_tag_range_clicked)

        bulk_row.addWidget(QLabel("Старт"))
        bulk_row.addWidget(self.tags_start_addr_spin)
        bulk_row.addWidget(QLabel("Кол-во"))
        bulk_row.addWidget(self.tags_count_spin)
        bulk_row.addWidget(QLabel("Шаг"))
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
        self.tags_poll_start_btn = QPushButton("Старт")
        self.tags_poll_stop_btn = QPushButton("Стоп")
        self.tags_poll_start_btn.clicked.connect(self._start_tags_polling)
        self.tags_poll_stop_btn.clicked.connect(self._stop_tags_polling)
        poll_row.addWidget(QLabel("Интервал чтения"))
        poll_row.addWidget(self.tags_poll_interval_spin)
        poll_row.addWidget(self.tags_poll_start_btn)
        poll_row.addWidget(self.tags_poll_stop_btn)
        poll_row.addStretch(1)
        layout.addLayout(poll_row)

        self.tags_table = QTableWidget(0, 10)
        self.tags_table.setHorizontalHeaderLabels(
            ["Чтение", "Имя", "Адрес", "Регистр", "Тип", "Бит", "Порядок REAL", "Значение", "Запись", "Статус"]
        )
        header = self.tags_table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(self.tags_table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(lambda *_args: self._on_table_column_resized("tags_table"))
        self.tags_table.verticalHeader().setVisible(False)
        layout.addWidget(self.tags_table, 1)

        buttons_row = QHBoxLayout()
        self.add_tag_btn = QPushButton("+ Тег")
        self.remove_tag_btn = QPushButton("- Тег")
        self.clear_tags_btn = QPushButton("Удалить все")
        self.read_tags_btn = QPushButton("Прочитать отмеченные")
        self.write_tags_btn = QPushButton("Записать отмеченные")
        self.save_tags_config_btn = QPushButton("Сохранить конфигурацию")

        self.add_tag_btn.clicked.connect(self._on_add_tag_clicked)
        self.remove_tag_btn.clicked.connect(self._on_remove_tag_rows_clicked)
        self.clear_tags_btn.clicked.connect(self._on_clear_tags_clicked)
        self.read_tags_btn.clicked.connect(self._on_read_tags_clicked)
        self.write_tags_btn.clicked.connect(self._on_write_tags_clicked)
        self.save_tags_config_btn.clicked.connect(self._save_from_tags_window)

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
            title = str(tab.name or f"Вкладка {idx + 1}")
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
        current_name = str(self._tags_tabs[idx].name or f"Вкладка {idx + 1}")
        new_name, ok = QInputDialog.getText(self, "Переименовать вкладку", "Название вкладки:", text=current_name)
        if not ok:
            return
        cleaned = str(new_name).strip()
        if not cleaned:
            return
        self._tags_tabs[idx].name = cleaned
        self.tags_tabbar.setTabText(idx, cleaned)

    def _on_tags_tab_close_requested(self, index: int) -> None:
        if len(self._tags_tabs) <= 1:
            QMessageBox.information(self, "Регистры Modbus", "Должна оставаться минимум одна вкладка.")
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
        self.status_label.setText(f"Статус: вкладка '{removed_name}' удалена")

    def _sync_tags_poll_buttons(self) -> None:
        running = bool(self._tags_poll_timer.isActive())
        self.tags_poll_start_btn.setEnabled(not running)
        self.tags_poll_stop_btn.setEnabled(running)

    def _start_tags_polling(self, _checked: bool = False) -> None:
        interval_ms = max(100, int(self.tags_poll_interval_spin.value()))
        self._tags_poll_timer.start(interval_ms)
        self._sync_tags_poll_buttons()
        self.status_label.setText(f"Статус: авточтение регистров запущено, интервал {interval_ms} ms")
        self._read_tags_once(update_status=True)

    def _stop_tags_polling(self, _checked: bool = False, silent: bool = False) -> None:
        if self._tags_poll_timer.isActive():
            self._tags_poll_timer.stop()
        self._sync_tags_poll_buttons()
        if not silent:
            self.status_label.setText("Статус: авточтение регистров остановлено")

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
        reg_combo.addItem("Holding (хран.)", "holding")
        reg_combo.addItem("Input (вход.)", "input")
        reg_index = reg_combo.findData(str(tag.register_type or "holding"))
        reg_combo.setCurrentIndex(reg_index if reg_index >= 0 else 0)
        self.tags_table.setCellWidget(row, 3, reg_combo)

        type_combo = QComboBox()
        type_combo.addItem("INT16 (знак.)", "int16")
        type_combo.addItem("UINT16 (без знака)", "uint16")
        type_combo.addItem("REAL / FLOAT32", "float32")
        type_combo.addItem("BOOL (бит)", "bool")
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

        write_btn = QPushButton("Запись")
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
                tabs_src = [TagTabConfig(name="Вкладка 1", tags=self._clone_tag_list(profile.tags))]
            else:
                tabs_src = [TagTabConfig(name="Вкладка 1", tags=[])]
        self._tags_tabs = [
            TagTabConfig(
                id=str(tab.id),
                name=str(tab.name or f"Вкладка {idx + 1}"),
                tags=self._clone_tag_list(tab.tags),
            )
            for idx, tab in enumerate(tabs_src)
        ]
        if not self._tags_tabs:
            self._tags_tabs = [TagTabConfig(name="Вкладка 1", tags=[])]
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
            name = str(tab.name or f"Вкладка {idx + 1}")
            result.append(
                TagTabConfig(
                    id=str(tab.id or uuid.uuid4()),
                    name=name,
                    tags=self._clone_tag_list(tab.tags),
                )
            )
        if not result:
            result.append(TagTabConfig(name="Вкладка 1", tags=[]))
        return result

    def _write_tag_row_with_client(self, client: ModbusTcpClient, row: int) -> tuple[bool, str]:
        tag = self._collect_tag_row(row)
        if tag is None:
            return False, "Строка не распознана"
        value_spin = self.tags_table.cellWidget(row, 7)
        if not isinstance(value_spin, QDoubleSpinBox):
            return False, "Нет поля значения"
        value = float(value_spin.value())
        old_value: float | None = None
        try:
            old_value = self._read_single_tag(client, tag)
        except Exception:
            old_value = None
        self._write_single_tag(client, tag, value)
        if old_value is None:
            return True, f"{tag.name} MW{tag.address}: записано {value:.6g}"
        return True, f"{tag.name} MW{tag.address}: {old_value:.6g} -> {value:.6g}"

    def _on_write_single_tag_row_clicked(self, _checked: bool = False) -> None:
        button = self.sender()
        if not isinstance(button, QPushButton):
            return
        row = self._table_row_for_widget(self.tags_table, button, 8)
        if row < 0:
            return
        client = self._open_tags_client()
        if client is None:
            self._set_tag_row_status(row, "Нет связи", error=True)
            return
        try:
            try:
                _ok, message = self._write_tag_row_with_client(client, row)
                self._set_tag_row_status(row, "Записано", error=False)
                self.status_label.setText(f"Статус: {message}")
            except Exception as exc:
                self._set_tag_row_status(row, "Ошибка записи", error=True)
                self.status_label.setText(f"Ошибка записи строки {row + 1}: {exc}")
        finally:
            try:
                client.close()
            except Exception:
                pass

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
            "Регистры Modbus",
            "Удалить все теги из таблицы?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.tags_table.setRowCount(0)
        self._fit_tags_table_columns(initial=False)
        self.status_label.setText("Статус: все регистры удалены из таблицы")

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
            f"Статус: добавлено {count} регистров (старт={start_address}, шаг={step}, тип={data_type})"
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
            self.status_label.setText(f"Ошибка: подключение к {self.current_profile.ip}:{self.current_profile.port} не удалось")
            return None
        if not ok:
            self.status_label.setText(f"Ошибка: подключение к {self.current_profile.ip}:{self.current_profile.port} не удалось")
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
            raise RuntimeError("Input-регистры доступны только для чтения")
        address = max(0, int(tag.address) + int(self.current_profile.address_offset))
        if tag.data_type == "int16":
            raw = int(round(write_value))
            if raw < -32768 or raw > 32767:
                raise RuntimeError("INT16 вне диапазона -32768..32767")
            response = ModbusWorker._write_single_register(client, address, raw & 0xFFFF, self.current_profile.unit_id)
        elif tag.data_type == "uint16":
            raw = int(round(write_value))
            if raw < 0 or raw > 65535:
                raise RuntimeError("UINT16 вне диапазона 0..65535")
            response = ModbusWorker._write_single_register(client, address, raw, self.current_profile.unit_id)
        elif tag.data_type == "bool":
            bit = max(0, min(15, int(tag.bit_index)))
            desired = 1 if int(round(write_value)) != 0 else 0
            current = ModbusWorker._read_holding_registers(client, address, 1, self.current_profile.unit_id)
            if current.isError():
                raise RuntimeError(f"Ошибка чтения регистра перед записью BOOL: {current}")
            current_reg = int(current.registers[0]) if current.registers else 0
            if desired:
                raw = current_reg | (1 << bit)
            else:
                raw = current_reg & ~(1 << bit)
            response = ModbusWorker._write_single_register(client, address, raw & 0xFFFF, self.current_profile.unit_id)
        else:
            if not math.isfinite(float(write_value)):
                raise RuntimeError("FLOAT32 должен быть конечным числом")
            reg0, reg1 = ModbusWorker._encode_float32_words(float(write_value), tag.float_order)
            response = ModbusWorker._write_multiple_registers(client, address, [reg0, reg1], self.current_profile.unit_id)
        if response.isError():
            raise RuntimeError(str(response))

    def _read_tags_once(self, update_status: bool = True) -> tuple[int, int]:
        client = self._open_tags_client()
        if client is None:
            return 0, 0
        ok_count = 0
        fail_count = 0
        try:
            for row in range(self.tags_table.rowCount()):
                tag = self._collect_tag_row(row)
                if tag is None or not tag.read_enabled:
                    continue
                value_spin = self.tags_table.cellWidget(row, 7)
                if not isinstance(value_spin, QDoubleSpinBox):
                    continue
                try:
                    old_value = float(value_spin.value())
                    new_value = self._read_single_tag(client, tag)
                    value_spin.blockSignals(True)
                    value_spin.setValue(new_value)
                    value_spin.blockSignals(False)
                    self._set_tag_row_status(row, "Прочитано", error=False)
                    ok_count += 1
                except Exception as exc:
                    self._set_tag_row_status(row, "Ошибка чтения", error=True)
                    fail_count += 1
                    if update_status:
                        self.status_label.setText(f"Ошибка чтения {tag.name}: {exc}")
        finally:
            try:
                client.close()
            except Exception:
                pass

        if update_status:
            self.status_label.setText(f"Статус: чтение регистров завершено (успешно {ok_count}, ошибок {fail_count})")
        return ok_count, fail_count

    def _on_read_tags_clicked(self, _checked: bool = False) -> None:
        self._read_tags_once(update_status=True)

    def _on_write_tags_clicked(self, _checked: bool = False) -> None:
        client = self._open_tags_client()
        if client is None:
            return
        ok_count = 0
        fail_count = 0
        try:
            for row in range(self.tags_table.rowCount()):
                tag = self._collect_tag_row(row)
                if tag is None or not tag.read_enabled:
                    continue
                try:
                    _ok, _message = self._write_tag_row_with_client(client, row)
                    self._set_tag_row_status(row, "Записано", error=False)
                    ok_count += 1
                except Exception as exc:
                    self._set_tag_row_status(row, "Ошибка записи", error=True)
                    fail_count += 1
                    self.status_label.setText(f"Ошибка записи {tag.name}: {exc}")
        finally:
            try:
                client.close()
            except Exception:
                pass

        self.status_label.setText(f"Статус: запись регистров завершена (успешно {ok_count}, ошибок {fail_count})")

    def _save_from_tags_window(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        self.app_config.active_profile_id = self.current_profile.id
        self.config_store.save(self.app_config)
        self.status_label.setText("Статус: конфигурация (включая теги) сохранена")

    def _build_scales_window(self) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.scales_window = QDialog(self, flags)
        self.scales_window.setWindowTitle("Настройка шкал")
        self.scales_window.resize(700, 380)
        self.scales_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.scales_window)

        self.scales_table = QTableWidget(0, 5)
        self.scales_table.setHorizontalHeaderLabels(["Шкала", "Авто Y", "Мин", "Макс", "Сигналы"])
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
        self.graph_settings_window.setWindowTitle("Настройки графика")
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
        self.graph_grid_x_checkbox = QCheckBox("Вертикальная (X)")
        self.graph_grid_y_checkbox = QCheckBox("Горизонтальная (Y)")

        form.addRow("Цвет фона", self.graph_bg_btn)
        form.addRow("Цвет сетки", self.graph_grid_color_btn)
        form.addRow("Прозрачность сетки", self.graph_grid_alpha_spin)
        form.addRow("Сетка X", self.graph_grid_x_checkbox)
        form.addRow("Сетка Y", self.graph_grid_y_checkbox)
        layout.addLayout(form)

        apply_row = QHBoxLayout()
        self.graph_apply_btn = QPushButton("Применить")
        self.graph_apply_btn.clicked.connect(self._apply_graph_settings_from_ui)
        self.graph_reset_btn = QPushButton("Сброс")
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
        self.statistics_window.setWindowTitle("Анализ участка графика")
        self.statistics_window.resize(920, 480)
        self.statistics_window.setSizeGripEnabled(True)
        layout = QVBoxLayout(self.statistics_window)

        controls_row = QHBoxLayout()
        self.stats_markers_checkbox = QCheckBox("Статистика по 2-м точкам")
        self.stats_markers_checkbox.toggled.connect(self._on_stats_markers_toggled)
        controls_row.addWidget(self.stats_markers_checkbox)
        self.stats_from_markers_btn = QPushButton("Применить 2 точки")
        self.stats_from_markers_btn.clicked.connect(self._on_stats_from_markers_clicked)
        controls_row.addWidget(self.stats_from_markers_btn)
        self.stats_from_view_btn = QPushButton("Период из видимой области")
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
        period_form.addRow("Начало периода", self.stats_start_edit)
        period_form.addRow("Конец периода", self.stats_end_edit)
        layout.addLayout(period_form)

        self.stats_interval_label = QLabel("Интервал: -")
        layout.addWidget(self.stats_interval_label)

        calc_row = QHBoxLayout()
        self.stats_calc_btn = QPushButton("Рассчитать")
        self.stats_calc_btn.clicked.connect(self._calculate_statistics)
        calc_row.addWidget(self.stats_calc_btn)
        calc_row.addStretch(1)
        layout.addLayout(calc_row)

        self.stats_table = QTableWidget(0, 7)
        self.stats_table.setHorizontalHeaderLabels(
            ["Сигнал", "Мин", "Макс", "Среднее", "Скорость, ед/с", "Интервал, с", "Точек"]
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
            self.status_label.setText("Статус: двухточечный режим не включен")
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
        self.status_label.setText(f"Статус: статистика рассчитана, сигналов: {len(rows)}")

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("Файл")
        self.action_save_config = QAction("Сохранить конфигурацию", self)
        self.action_save_config.triggered.connect(self._save_config)
        file_menu.addAction(self.action_save_config)
        self.action_export_connection_config = QAction("Экспорт подключения...", self)
        self.action_export_connection_config.triggered.connect(self._save_connection_config_to_file)
        file_menu.addAction(self.action_export_connection_config)
        self.action_import_connection_config = QAction("Импорт подключения...", self)
        self.action_import_connection_config.triggered.connect(self._load_connection_config_from_file)
        file_menu.addAction(self.action_import_connection_config)
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("Экспорт и печать")
        self.action_export_chart_image = QAction("График в PNG/JPG...", self)
        self.action_export_chart_image.triggered.connect(self._export_chart_image)
        export_menu.addAction(self.action_export_chart_image)
        self.action_export_chart_csv = QAction("Данные графика в CSV...", self)
        self.action_export_chart_csv.triggered.connect(self._export_chart_csv)
        export_menu.addAction(self.action_export_chart_csv)
        self.action_print_chart = QAction("Печать графика...", self)
        self.action_print_chart.triggered.connect(self._print_chart)
        export_menu.addAction(self.action_print_chart)
        file_menu.addSeparator()
        self.action_minimize_tray = QAction("Свернуть в трей", self)
        self.action_minimize_tray.triggered.connect(self._minimize_to_tray)
        file_menu.addAction(self.action_minimize_tray)
        file_menu.addSeparator()
        self.action_exit = QAction("Выход", self)
        self.action_exit.triggered.connect(self._exit_from_tray)
        file_menu.addAction(self.action_exit)

        settings_menu = menu_bar.addMenu("Настройки")
        self.action_connection = QAction("Подключение...", self)
        self.action_connection.triggered.connect(lambda: self._show_tool_window(self.connection_window))
        settings_menu.addAction(self.action_connection)

        self.action_signals = QAction("Сигналы графика...", self)
        self.action_signals.triggered.connect(lambda: self._show_tool_window(self.signals_window))
        settings_menu.addAction(self.action_signals)

        self.action_tags = QAction("Регистры Modbus...", self)
        self.action_tags.triggered.connect(lambda: self._show_tool_window(self.tags_window))
        settings_menu.addAction(self.action_tags)

        self.action_scales = QAction("Шкалы...", self)
        self.action_scales.triggered.connect(lambda: self._show_tool_window(self.scales_window))
        settings_menu.addAction(self.action_scales)
        self.action_graph_settings = QAction("График...", self)
        self.action_graph_settings.triggered.connect(lambda: self._show_tool_window(self.graph_settings_window))
        settings_menu.addAction(self.action_graph_settings)

        mode_menu = menu_bar.addMenu("Режим")
        self.action_mode_online = QAction("Онлайн", self, checkable=True)
        self.action_mode_offline = QAction("Офлайн", self, checkable=True)
        self.mode_action_group = QActionGroup(self)
        self.mode_action_group.setExclusive(True)
        self.mode_action_group.addAction(self.action_mode_online)
        self.mode_action_group.addAction(self.action_mode_offline)
        self.action_mode_online.triggered.connect(lambda checked: self._set_mode_via_menu("online", checked))
        self.action_mode_offline.triggered.connect(lambda checked: self._set_mode_via_menu("offline", checked))
        mode_menu.addAction(self.action_mode_online)
        mode_menu.addAction(self.action_mode_offline)
        mode_menu.addSeparator()
        self.action_start = QAction("Старт опроса", self)
        self.action_start.triggered.connect(self._start_worker)
        mode_menu.addAction(self.action_start)
        self.action_stop = QAction("Стоп опроса", self)
        self.action_stop.triggered.connect(self._stop_worker)
        mode_menu.addAction(self.action_stop)

        archive_menu = menu_bar.addMenu("Архив")
        self.action_archive_write_db = QAction("Писать в БД", self, checkable=True)
        self.action_archive_write_db.setChecked(True)
        self.action_archive_write_db.triggered.connect(self._on_archive_write_db_toggled)
        archive_menu.addAction(self.action_archive_write_db)
        archive_menu.addSeparator()
        self.action_save_archive = QAction("Сохранить архив...", self)
        self.action_save_archive.triggered.connect(self._save_archive_to_file)
        archive_menu.addAction(self.action_save_archive)
        self.action_load_archive = QAction("Загрузить архив...", self)
        self.action_load_archive.triggered.connect(self._load_archive_from_file)
        archive_menu.addAction(self.action_load_archive)

        view_menu = menu_bar.addMenu("Вид")
        self.action_auto_x = QAction("Авто X", self, checkable=True)
        self.action_auto_x.setChecked(True)
        self.action_auto_x.triggered.connect(self._on_action_auto_x_toggled)
        view_menu.addAction(self.action_auto_x)
        self.action_cursor = QAction("Курсор", self, checkable=True)
        self.action_cursor.setChecked(False)
        self.action_cursor.triggered.connect(self._on_action_cursor_toggled)
        view_menu.addAction(self.action_cursor)
        self.action_reset_zoom = QAction("Сброс масштаба", self)
        self.action_reset_zoom.triggered.connect(lambda _checked=False: self.chart.reset_view())
        view_menu.addAction(self.action_reset_zoom)
        self.action_statistics = QAction("Статистика...", self)
        self.action_statistics.triggered.connect(self._show_statistics_window)
        view_menu.addAction(self.action_statistics)
        view_menu.addSeparator()
        self.action_values_panel = QAction("Таблица значений", self, checkable=True)
        self.action_values_panel.setChecked(True)
        self.action_values_panel.triggered.connect(self._on_values_panel_menu_toggled)
        view_menu.addAction(self.action_values_panel)

        close_menu = menu_bar.addMenu("Параметры")
        self.action_close_ask = QAction("При закрытии: запрашивать действие", self, checkable=True)
        self.action_close_to_tray = QAction("При закрытии: сворачивать в трей", self, checkable=True)
        self.action_close_exit = QAction("При закрытии: завершать программу", self, checkable=True)
        self.close_action_group = QActionGroup(self)
        self.close_action_group.setExclusive(True)
        for action in (self.action_close_ask, self.action_close_to_tray, self.action_close_exit):
            self.close_action_group.addAction(action)
            close_menu.addAction(action)
        self.action_close_ask.triggered.connect(lambda checked: self._set_close_behavior("ask", checked))
        self.action_close_to_tray.triggered.connect(lambda checked: self._set_close_behavior("tray", checked))
        self.action_close_exit.triggered.connect(lambda checked: self._set_close_behavior("exit", checked))

        close_menu.addSeparator()
        self.action_windows_autostart = QAction("Автозапуск при старте Windows", self, checkable=True)
        self.action_windows_autostart.triggered.connect(self._on_action_windows_autostart_toggled)
        close_menu.addAction(self.action_windows_autostart)
        self.action_auto_connect_startup = QAction("Автозапуск опроса при запуске", self, checkable=True)
        self.action_auto_connect_startup.triggered.connect(self._on_action_auto_connect_startup_toggled)
        close_menu.addAction(self.action_auto_connect_startup)
        close_menu.addSeparator()
        self.action_show_ui_state = QAction("Диагностика: состояние интерфейса (ui_state)", self)
        self.action_show_ui_state.triggered.connect(self._show_ui_state_debug_dialog)
        close_menu.addAction(self.action_show_ui_state)

    @staticmethod
    def _show_tool_window(window: QDialog) -> None:
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

    def _on_action_auto_x_toggled(self, checked: bool) -> None:
        state = bool(checked)
        self.chart.set_auto_x(state)
        if hasattr(self, "values_auto_x_checkbox"):
            self.values_auto_x_checkbox.blockSignals(True)
            self.values_auto_x_checkbox.setChecked(state)
            self.values_auto_x_checkbox.blockSignals(False)
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
        self.chart.set_auto_x(state)
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
        self.values_collapse_btn.setText("▸" if self._values_collapsed else "—")
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
        self.values_collapse_btn.setText("—")
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
            self.status_label.setText(f"Ошибка автозапуска: {error}")

    def _on_action_windows_autostart_toggled(self, checked: bool) -> None:
        enabled = bool(checked)
        self.app_config.auto_start_windows = enabled
        ok, error = set_windows_autostart(enabled)
        if not ok:
            self.app_config.auto_start_windows = False
            self._sync_startup_actions()
            self.status_label.setText(f"Ошибка автозапуска: {error}")
            return
        self.config_store.save(self.app_config)
        self.status_label.setText("Статус: автозапуск обновлен")

    def _on_action_auto_connect_startup_toggled(self, checked: bool) -> None:
        self.app_config.auto_connect_on_launch = bool(checked)
        self.config_store.save(self.app_config)
        self.status_label.setText("Статус: автоподключение обновлено")

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
        dialog.setWindowTitle("Текущее ui_state")
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
        action_open = tray_menu.addAction("Открыть")
        action_open.triggered.connect(self._restore_from_tray)
        action_start = tray_menu.addAction("Старт")
        action_start.triggered.connect(self._start_worker)
        action_stop = tray_menu.addAction("Стоп")
        action_stop.triggered.connect(self._stop_worker)
        tray_menu.addSeparator()
        action_exit = tray_menu.addAction("Выход")
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
            "Приложение свернуто в трей",
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

    def _collect_view_state_payload(self) -> dict[str, object]:
        x_min, x_max = self.chart.current_x_range()
        return {
            "auto_x": bool(self.action_auto_x.isChecked()) if hasattr(self, "action_auto_x") else True,
            "cursor_enabled": bool(self.action_cursor.isChecked()) if hasattr(self, "action_cursor") else False,
            "values_sort_mode": str(self.values_sort_combo.currentData() or self._values_sort_mode),
            "values_panel_collapsed": bool(self._values_collapsed),
            "values_panel_closed": bool(self._values_closed),
            "x_range": [float(x_min), float(x_max)],
            "scale_states": self.chart.export_scale_states(),
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

        sort_mode = str(view.get("values_sort_mode") or "")
        if sort_mode:
            idx = self.values_sort_combo.findData(sort_mode)
            if idx >= 0:
                self.values_sort_combo.setCurrentIndex(idx)
                self._values_sort_mode = sort_mode

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
        self._stop_tags_polling(silent=True)
        self._updating_ui = True
        self.profile_name_edit.setText(profile.name)
        self.ip_edit.setText(profile.ip)
        self.port_spin.setValue(profile.port)
        self.unit_id_spin.setValue(profile.unit_id)
        self.poll_interval_spin.setValue(profile.poll_interval_ms)
        self.render_interval_spin.setValue(max(50, int(profile.render_interval_ms)))
        self.archive_interval_spin.setValue(profile.archive_interval_ms)
        self.archive_on_change_checkbox.setChecked(bool(profile.archive_on_change_only))
        self.archive_deadband_spin.setValue(max(0.0, float(profile.archive_deadband)))
        self.archive_keepalive_spin.setValue(max(0, int(profile.archive_keepalive_s)))
        self.archive_retention_days_spin.setValue(max(0, int(profile.archive_retention_days)))
        self.timeout_spin.setValue(profile.timeout_s)
        self.retries_spin.setValue(profile.retries)
        self.address_offset_spin.setValue(profile.address_offset)
        self._apply_render_interval_runtime(profile.render_interval_ms)
        mode_index = self.mode_combo.findData(profile.work_mode)
        self.mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.archive_to_db_checkbox.setChecked(bool(profile.archive_to_db))
        self.action_archive_write_db.setChecked(bool(profile.archive_to_db))
        self._load_graph_settings_to_ui(profile)
        self._fill_signal_table(profile.signals)
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
        self._schedule_apply_saved_widths_all_tables(profile.id)
        self._apply_work_mode_ui(profile.work_mode)
        self._sync_mode_actions()
        if str(profile.work_mode or "online") == "online" and (self._worker is None or not self._worker.isRunning()):
            auto_x_enabled = bool(self.action_auto_x.isChecked()) if hasattr(self, "action_auto_x") else True
            self._load_recent_online_history_from_db(adjust_x_range=auto_x_enabled, silent=True)

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
            title = "Выбор цвета фона графика"
        else:
            button = self.graph_grid_color_btn
            title = "Выбор цвета сетки"
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
        button.setFixedWidth(28)
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

    def _on_values_sort_changed(self, _index: int) -> None:
        self._values_sort_mode = str(self.values_sort_combo.currentData() or "name_asc")
        if self._last_values_rows:
            self._update_values_table(self._last_values_rows, False)
        self._mark_config_dirty()

    def _sorted_values_rows(self, rows: list[dict]) -> list[dict]:
        mode = str(self._values_sort_mode or "name_asc")
        if mode == "name_desc":
            return sorted(rows, key=lambda row: str(row.get("name", "")).lower(), reverse=True)
        if mode == "visible_first":
            return sorted(
                rows,
                key=lambda row: (not bool(row.get("enabled", True)), str(row.get("name", "")).lower()),
            )
        if mode == "hidden_first":
            return sorted(
                rows,
                key=lambda row: (bool(row.get("enabled", True)), str(row.get("name", "")).lower()),
            )
        return sorted(rows, key=lambda row: str(row.get("name", "")).lower())

    def _on_color_button_clicked(self) -> None:
        button = self.sender()
        if not isinstance(button, QPushButton):
            return
        current = QColor(str(button.property("color_hex") or "#1f77b4"))
        chosen = QColorDialog.getColor(current, self, "Выбор цвета сигнала")
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

    def _store_ui_to_profile(self, profile: ProfileConfig) -> None:
        profile.name = self.profile_name_edit.text().strip() or profile.name
        profile.ip = self.ip_edit.text().strip() or "127.0.0.1"
        profile.port = self.port_spin.value()
        profile.unit_id = self.unit_id_spin.value()
        profile.poll_interval_ms = self.poll_interval_spin.value()
        profile.render_interval_ms = self.render_interval_spin.value()
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
        profile.signals = self._collect_signal_table()
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
            "archive_interval_ms": int(profile.archive_interval_ms),
            "archive_on_change_only": bool(profile.archive_on_change_only),
            "archive_deadband": float(profile.archive_deadband),
            "archive_keepalive_s": int(profile.archive_keepalive_s),
            "archive_retention_days": int(profile.archive_retention_days),
            "timeout_s": float(profile.timeout_s),
            "retries": int(profile.retries),
            "address_offset": int(profile.address_offset),
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
        db_path = str(payload.get("db_path", profile.db_path)).strip()
        profile.db_path = db_path or str(DEFAULT_DB_PATH)

    def _load_connection_fields_to_ui(self, profile: ProfileConfig) -> None:
        self.profile_name_edit.setText(profile.name)
        self.ip_edit.setText(profile.ip)
        self.port_spin.setValue(profile.port)
        self.unit_id_spin.setValue(profile.unit_id)
        self.poll_interval_spin.setValue(profile.poll_interval_ms)
        self.render_interval_spin.setValue(max(50, int(profile.render_interval_ms)))
        self.archive_interval_spin.setValue(profile.archive_interval_ms)
        self.archive_on_change_checkbox.setChecked(bool(profile.archive_on_change_only))
        self.archive_deadband_spin.setValue(max(0.0, float(profile.archive_deadband)))
        self.archive_keepalive_spin.setValue(max(0, int(profile.archive_keepalive_s)))
        self.archive_retention_days_spin.setValue(max(0, int(profile.archive_retention_days)))
        self.timeout_spin.setValue(profile.timeout_s)
        self.retries_spin.setValue(profile.retries)
        self.address_offset_spin.setValue(profile.address_offset)

    def _save_connection_config_to_file(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        default_name = f"{self.current_profile.name}_connection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        default_path = str(Path.cwd() / default_name)
        file_path, _selected = QFileDialog.getSaveFileName(
            self,
            "Экспорт подключения",
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
            self.status_label.setText(f"Статус: подключение экспортировано -> {file_path}")
        except Exception as exc:
            self.status_label.setText(f"Ошибка экспорта подключения: {exc}")

    def _load_connection_config_from_file(self) -> None:
        file_path, _selected = QFileDialog.getOpenFileName(
            self,
            "Импорт подключения",
            str(Path.cwd()),
            "Connection config (*.json);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except Exception as exc:
            self.status_label.setText(f"Ошибка чтения подключения: {exc}")
            return

        if not isinstance(payload, dict):
            self.status_label.setText("Ошибка: неверный формат файла подключения")
            return
        if str(payload.get("format", "")) == CONNECTION_CONFIG_FORMAT:
            config_payload = payload.get("connection_config")
        else:
            config_payload = payload

        if not isinstance(config_payload, dict):
            self.status_label.setText("Ошибка: в файле нет валидной конфигурации подключения")
            return

        self._apply_connection_config_to_profile(self.current_profile, config_payload)
        self._updating_ui = True
        self._load_connection_fields_to_ui(self.current_profile)
        self._updating_ui = False
        combo_index = self.profile_combo.currentIndex()
        if combo_index >= 0:
            self.profile_combo.setItemText(combo_index, self.current_profile.name)
        self.status_label.setText(f"Статус: подключение импортировано <- {file_path}")

    def _on_clear_archive_db_clicked(self, _checked: bool = False) -> None:
        answer = QMessageBox.question(
            self,
            "Подтверждение очистки архива",
            "Вы уверены? Это удалит все данные архива из базы.",
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
            self.status_label.setText("Статус: архивная БД отсутствует, очищать нечего")
            if was_running and mode == "online":
                self._start_worker()
            return

        try:
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
            self.status_label.setText(f"Ошибка очистки БД: {exc}")
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
        self.status_label.setText("Статус: архивная БД очищена")

        if was_running and mode == "online":
            self._start_worker()

    def _fill_signal_table(self, signals: list[SignalConfig]) -> None:
        self.signal_table.setRowCount(0)
        for signal in signals:
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
            else f"Колонка {col + 1}"
            for col in range(self.signal_table.columnCount())
        ]
        for col, title in enumerate(headers):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(not self.signal_table.isColumnHidden(col))
            action.toggled.connect(lambda checked, c=col: self.signal_table.setColumnHidden(c, not bool(checked)))
            self._signal_column_actions[col] = action

        menu.addSeparator()
        fit_action = menu.addAction("Подогнать ширину по содержимому")
        fit_action.triggered.connect(lambda _checked=False: self._fit_signal_table_columns(initial=False))
        menu.exec(self.signal_columns_btn.mapToGlobal(self.signal_columns_btn.rect().bottomLeft()))

    def _add_signal_row(self, signal: SignalConfig | None = None) -> None:
        if not isinstance(signal, SignalConfig):
            signal = None

        row = self.signal_table.rowCount()
        self.signal_table.insertRow(row)

        if signal is None:
            signal = SignalConfig(
                name=f"Signal {row + 1}",
                color=DEFAULT_COLORS[row % len(DEFAULT_COLORS)],
            )

        enabled_item = QTableWidgetItem()
        enabled_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        enabled_item.setCheckState(Qt.CheckState.Checked if signal.enabled else Qt.CheckState.Unchecked)
        self.signal_table.setItem(row, 0, enabled_item)

        name_item = QTableWidgetItem(signal.name)
        name_item.setData(Qt.ItemDataRole.UserRole, signal.id)
        self.signal_table.setItem(row, 1, name_item)

        addr_text = f"{signal.address}.{signal.bit_index}" if signal.data_type == "bool" else str(signal.address)
        self.signal_table.setItem(row, 2, QTableWidgetItem(addr_text))

        reg_combo = QComboBox()
        reg_combo.addItem("Holding (хран.)", "holding")
        reg_combo.addItem("Input (вход.)", "input")
        reg_idx = reg_combo.findData(signal.register_type)
        reg_combo.setCurrentIndex(reg_idx if reg_idx >= 0 else 0)
        self.signal_table.setCellWidget(row, 3, reg_combo)

        type_combo = QComboBox()
        type_combo.addItem("INT16 (знак.)", "int16")
        type_combo.addItem("UINT16 (без знака)", "uint16")
        type_combo.addItem("REAL / FLOAT32", "float32")
        type_combo.addItem("BOOL (бит)", "bool")
        type_idx = type_combo.findData(signal.data_type)
        type_combo.setCurrentIndex(type_idx if type_idx >= 0 else 0)
        self.signal_table.setCellWidget(row, 4, type_combo)

        bit_spin = QSpinBox()
        bit_spin.setRange(0, 15)
        bit_spin.setValue(max(0, min(15, int(signal.bit_index))))
        self.signal_table.setCellWidget(row, 5, bit_spin)

        order_combo = QComboBox()
        order_combo.addItem("ABCD (обычный)", "ABCD")
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
                enabled=True,
            )
            self._add_signal_row(signal)

        self._fit_signal_table_columns(initial=False)
        self._apply_current_profile()
        self.status_label.setText(
            f"Статус: добавлено {count} сигналов (старт={start_address}, шаг={step}, тип={data_type})"
        )

    def _on_signal_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_ui:
            return
        row = item.row()
        name_item = self.signal_table.item(row, 1)
        if name_item is None:
            return
        signal_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
        if not signal_id:
            return

        # "Вкл" changed.
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
            row_signal_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
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
            row_signal_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
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
        chosen = QColorDialog.getColor(current, self, "Выбор цвета сигнала")
        if not chosen.isValid():
            return
        color = chosen.name()
        self.chart.set_signal_color(signal_id, color)
        self._sync_signal_table_color(signal_id, color)
        self._mark_config_dirty()

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
            duplicated.name = f"{source.name} (копия)"
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

    def _collect_signal_table(self) -> list[SignalConfig]:
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
            signal_id = str(name_item.data(Qt.ItemDataRole.UserRole) or uuid.uuid4()) if name_item else str(uuid.uuid4())
            if signal_id in seen_ids:
                signal_id = str(uuid.uuid4())
                if name_item is not None:
                    name_item.setData(Qt.ItemDataRole.UserRole, signal_id)
            seen_ids.add(signal_id)

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
                )
            )

        if not signals:
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
            QMessageBox.warning(self, APP_NAME, "Нельзя удалить последний профиль")
            return

        target_id = self.current_profile.id
        self.app_config.profiles = [p for p in self.app_config.profiles if p.id != target_id]
        self.current_profile = self.app_config.profiles[0]
        self.app_config.active_profile_id = self.current_profile.id
        self._populate_profiles()
        self._load_profile_to_ui(self.current_profile)

    def _apply_current_profile(self) -> None:
        self._store_ui_to_profile(self.current_profile)
        current_name = self.current_profile.name
        combo_index = self.profile_combo.currentIndex()
        self.profile_combo.setItemText(combo_index, current_name)
        self.chart.configure_signals(self.current_profile.signals)
        self.chart.set_visual_settings(
            background_color=self.current_profile.plot_background_color,
            grid_color=self.current_profile.plot_grid_color,
            grid_alpha=max(0.0, min(1.0, float(self.current_profile.plot_grid_alpha) / 100.0)),
            grid_x=self.current_profile.plot_grid_x,
            grid_y=self.current_profile.plot_grid_y,
        )
        mode = str(self.current_profile.work_mode or "online")
        if mode == "online":
            self.chart.clear_data()
        # Re-apply persisted runtime view state after chart rebuild to keep
        # manual scale settings (AutoY/Min/Max) and other view preferences.
        self._apply_runtime_view_state(self.current_profile)

        if mode == "online" and self._worker is not None and self._worker.isRunning():
            self._restart_worker()
        else:
            self._apply_work_mode_ui(mode)

        if mode == "online":
            self.status_label.setText("Статус: настройки применены")
        else:
            self.status_label.setText("Статус: офлайн настройки применены")

    def _save_config(self) -> None:
        # Save persists current UI/profile state without restarting or
        # reconfiguring runtime objects (to avoid chart/scale reset on save).
        self._store_ui_to_profile(self.current_profile)
        self.app_config.active_profile_id = self.current_profile.id
        self.config_store.save(self.app_config)
        self.status_label.setText("Статус: конфигурация сохранена")

    def _export_chart_image(self, _checked: bool = False) -> None:
        suggested = f"{self.current_profile.name}_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт графика в изображение",
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
            self.status_label.setText(f"Статус: график экспортирован -> {path}")
        else:
            self.status_label.setText("Ошибка: не удалось сохранить изображение графика")

    def _export_chart_csv(self, _checked: bool = False) -> None:
        suggested = f"{self.current_profile.name}_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспорт данных графика (CSV)",
            suggested,
            "CSV (*.csv)",
        )
        if not file_path:
            return

        payload = self.chart.export_samples_payload(only_enabled=False)
        signals = payload.get("signals") if isinstance(payload, dict) else None
        samples = payload.get("samples") if isinstance(payload, dict) else None
        if not isinstance(signals, list) or not isinstance(samples, dict):
            self.status_label.setText("Ошибка: нет данных для экспорта CSV")
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

        self.status_label.setText(f"Статус: CSV экспортирован ({rows_written} строк) -> {file_path}")

    def _print_chart(self, _checked: bool = False) -> None:
        options = self._open_print_options_dialog()
        if options is None:
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Печать графика")
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
            self.status_label.setText("Ошибка: график недоступен для печати")
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
        self.status_label.setText(f"Статус: график отправлен на печать (A4, альбомная), страниц: {pages_printed}")

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
        # Axis title "Время" adds clutter and may overlap at large fonts.
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
                    "name": f"{name} (шкала {axis_index})",
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
        dialog.setWindowTitle("Параметры печати")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        info = QLabel("Формат печати: A4, альбомная ориентация.")
        layout.addWidget(info)

        force_grid_checkbox = QCheckBox("Контрастная сетка для печати")
        force_grid_checkbox.setChecked(True)
        layout.addWidget(force_grid_checkbox)

        font_preset_combo = QComboBox()
        font_preset_combo.addItem("Мелкий", "small")
        font_preset_combo.addItem("Средний", "medium")
        font_preset_combo.addItem("Крупный", "large")
        font_preset_combo.setCurrentIndex(1)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Размер шрифта графика:"))
        font_row.addWidget(font_preset_combo, 1)
        layout.addLayout(font_row)

        stats_page_checkbox = QCheckBox("Добавить статистику на отдельной странице")
        stats_page_checkbox.setChecked(False)
        layout.addWidget(stats_page_checkbox)

        details_page_checkbox = QCheckBox("Подробная статистика (расширенная)")
        details_page_checkbox.setChecked(False)
        details_page_checkbox.setEnabled(False)
        stats_page_checkbox.toggled.connect(details_page_checkbox.setEnabled)
        layout.addWidget(details_page_checkbox)

        fixed_source_label = QLabel("Источник статистики: период видимой области графика")
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
            "Статистика (видимая область): "
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

        headers = ["Сигнал", "Мин", "Макс", "Среднее", "Скорость, ед/с", "Точек"]
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
            draw_rows = [{"name": "Нет данных", "min": "-", "max": "-", "avg": "-", "speed": "-", "count": "-"}]
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
        text = "Стр. 999"
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
        text = f"Стр. {int(page_number)}"
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
                    "name": "Нет данных",
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
                "Подробная статистика (видимая область)" if detailed else "Статистика (видимая область)",
            )
            period_text = (
                f"Период: {format_ts_ms(start_ts) if end_ts > start_ts else '-'}"
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
                headers = ["Сигнал", "Мин", "Макс", "Среднее", "Размах", "Δ", "Скорость, ед/с", "Точек", "Интервал, с"]
                fractions = [0.21, 0.09, 0.09, 0.10, 0.10, 0.10, 0.11, 0.08, 0.12]
            else:
                headers = ["Сигнал", "Мин", "Макс", "Среднее", "Скорость, ед/с", "Точек"]
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

        if not online_mode and self._worker is not None and self._worker.isRunning():
            self._stop_worker()

        self.archive_to_db_checkbox.setEnabled(online_mode)
        self.action_archive_write_db.setEnabled(online_mode)
        self.action_start.setEnabled(online_mode and (self._worker is None or not self._worker.isRunning()))
        self.action_stop.setEnabled(online_mode and self._worker is not None and self._worker.isRunning())
        self.action_mode_online.setChecked(online_mode)
        self.action_mode_offline.setChecked(not online_mode)

        if online_mode:
            if self._worker is None or not self._worker.isRunning():
                self.status_label.setText("Статус: онлайн режим, ожидание запуска")
            else:
                self.status_label.setText("Статус: онлайн режим, опрос запущен")
        else:
            self.status_label.setText("Статус: офлайн режим (архив)")
        self._update_runtime_status_panel()

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
                    return None, None, "Ошибка: этот ZIP не является архивом Trend Analyzer (нет манифеста)"

                try:
                    manifest_raw = bundle.read(ARCHIVE_BUNDLE_MANIFEST).decode("utf-8")
                    manifest = json.loads(manifest_raw)
                except Exception as exc:
                    return None, None, f"Ошибка чтения манифеста архива: {exc}"

                if not isinstance(manifest, dict):
                    return None, None, "Ошибка: некорректный манифест архива"
                if str(manifest.get("format", "")) != ARCHIVE_BUNDLE_FORMAT:
                    return None, None, "Ошибка: неизвестный формат ZIP-архива"
                if str(manifest.get("bundle_id", "")) != ARCHIVE_BUNDLE_MAGIC:
                    return None, None, "Ошибка: ZIP-архив не принадлежит Trend Analyzer"

                files_raw = manifest.get("files")
                if not isinstance(files_raw, list) or not files_raw:
                    return None, None, "Ошибка: в ZIP-архиве нет списка файлов данных"

                data_files: list[str] = []
                for item in files_raw:
                    file_name = str(item)
                    if not file_name.startswith(f"{ARCHIVE_BUNDLE_DIR}/"):
                        return None, None, "Ошибка: неверный путь файла в манифесте архива"
                    if file_name not in names:
                        return None, None, f"Ошибка: в ZIP отсутствует файл данных {file_name}"
                    data_files.append(file_name)

                payloads: list[dict] = []
                for file_name in data_files:
                    try:
                        payload_raw = bundle.read(file_name).decode("utf-8")
                        payload = json.loads(payload_raw)
                    except Exception as exc:
                        return None, None, f"Ошибка чтения файла {file_name}: {exc}"
                    if not isinstance(payload, dict):
                        return None, None, f"Ошибка: неверный формат файла {file_name}"
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
            return None, None, "Ошибка: поврежденный ZIP-архив"
        except Exception as exc:
            return None, None, f"Ошибка чтения ZIP-архива: {exc}"

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
        dialog.setWindowTitle("Параметры экспорта архива")
        layout = QVBoxLayout(dialog)

        info_text = (
            f"В базе найдено записей: {total_rows}\n"
            f"Полный период: {self._archive_safe_ts(min_ts)} .. {self._archive_safe_ts(max_ts)}"
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
        chunk_spin.setSuffix(" строк/файл")

        form.addRow("Начало периода", start_edit)
        form.addRow("Окончание периода", end_edit)
        form.addRow("Разбиение", chunk_spin)
        layout.addLayout(form)

        hint_label = QLabel("Если строк больше заданного лимита, архив сохранится в несколько файлов *_partNNN.trend.json.")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def on_accept() -> None:
            start_ts = start_edit.dateTime().toMSecsSinceEpoch() / 1000.0
            end_ts = end_edit.dateTime().toMSecsSinceEpoch() / 1000.0
            if end_ts < start_ts:
                QMessageBox.warning(self, "Экспорт архива", "Окончание периода должно быть позже начала.")
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
                "Сохранить архив",
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
                        self.status_label.setText("Статус: нет данных за выбранный период")
                    else:
                        self.status_label.setText(f"Статус: ZIP-архив сохранен ({part_count} частей) -> {out_path}")
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
                self.status_label.setText(f"Ошибка сохранения архива: {exc}")
                return

            if not saved_parts:
                self.status_label.setText("Статус: нет данных за выбранный период")
            elif len(saved_parts) == 1:
                self.status_label.setText(f"Статус: архив сохранен -> {saved_parts[0]}")
            else:
                self.status_label.setText(
                    f"Статус: архив сохранен ({len(saved_parts)} файлов) -> {saved_parts[0].parent}"
                )
            return

        default_name = f"{self.current_profile.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.trend.zip"
        default_path = str(Path.cwd() / default_name)
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить архив",
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
                self.status_label.setText(f"Статус: ZIP-архив сохранен -> {out_path}")
                return

            out_path = self._archive_output_path(file_path)
            atomic_write_text(out_path, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_label.setText(f"Статус: архив сохранен -> {out_path}")
        except Exception as exc:
            self.status_label.setText(f"Ошибка сохранения архива: {exc}")

    def _load_archive_from_file(self) -> None:
        file_path, _selected = QFileDialog.getOpenFileName(
            self,
            "Загрузить архив",
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
                    self.status_label.setText(f"Ошибка чтения архива ({part.name}): {exc}")
                    return
                if not isinstance(payload, dict):
                    self.status_label.setText(f"Ошибка: неверный формат файла архива ({part.name})")
                    return
                payloads.append(payload)

        if not payloads:
            self.status_label.setText("Ошибка: архив пуст")
            return

        for payload in payloads:
            if not isinstance(payload, dict):
                self.status_label.setText("Ошибка: неверный формат данных архива")
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
            self.status_label.setText("Ошибка: в архиве нет валидных данных")
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
        self._fill_signal_table(loaded_signals)
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
                self.status_label.setText(f"Статус: ZIP-архив загружен ({source_parts_count} частей) <- {file_path}")
            else:
                self.status_label.setText(f"Статус: архив загружен ({source_parts_count} частей) <- {selected.name}")
        elif source_type == "zip":
            self.status_label.setText(f"Статус: ZIP-архив загружен <- {file_path}")
        else:
            self.status_label.setText(f"Статус: архив загружен <- {file_path}")

    def _load_recent_online_history_from_db(self, adjust_x_range: bool = True, silent: bool = True) -> bool:
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

            view_left, view_right = self.chart.current_x_range()
            span = float(view_right - view_left)
            if not math.isfinite(span) or span <= 0.0:
                span = max(60.0, float(self.current_profile.poll_interval_ms) / 1000.0 * 120.0)
            span = max(10.0, min(span, 7 * 24 * 3600.0))
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

            connection_events = self._query_connection_events(conn, float(start_ts), float(last_ts))
        except Exception:
            return False
        finally:
            conn.close()

        if not samples_map:
            return False

        now_ts = datetime.now().timestamp()
        gap_threshold = max(2.0, float(self.current_profile.poll_interval_ms) / 1000.0 * 2.5)
        if now_ts - float(last_ts) > gap_threshold:
            if not connection_events or int(connection_events[-1][1]) != 0:
                connection_events.append([float(last_ts), 0.0])
                connection_events = self._normalize_connection_events(connection_events)

        self.chart.set_archive_data(samples_map)
        self.chart.set_connection_events(connection_events)
        self._connection_events = connection_events
        self._last_connection_state = None if not connection_events else bool(int(connection_events[-1][1]))

        if adjust_x_range:
            right = float(last_ts)
            left = max(0.0, right - span)
            self.chart.set_x_range(left, right)

        if not silent:
            left_text = format_ts_ms(max(0.0, float(last_ts) - span))
            right_text = format_ts_ms(float(last_ts))
            self.status_label.setText(f"Статус: восстановлена история из БД ({left_text} .. {right_text})")
        return True

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

    def _restart_worker(self) -> None:
        self._stop_worker()
        self._start_worker()

    def _start_worker(self) -> None:
        mode = str(self.mode_combo.currentData() or "online")
        if mode != "online":
            self._apply_work_mode_ui("offline")
            return

        self._apply_current_profile()

        if self._worker is not None and self._worker.isRunning():
            return

        if self._archive_store is not None:
            self._archive_store.close()
            self._archive_store = None
        self._archive_last_values = {}
        self._archive_last_written_ts = {}
        self._signal_types_by_id = {}

        if self.archive_to_db_checkbox.isChecked():
            self._archive_store = ArchiveStore(self.current_profile.db_path or str(DEFAULT_DB_PATH))
        else:
            self._archive_store = None
        self._last_archive_ts = 0.0
        self._last_retention_cleanup_ts = 0.0
        self._archive_last_values = {}
        self._archive_last_written_ts = {}
        self._signal_types_by_id = {
            str(signal.id): str(signal.data_type or "int16") for signal in self.current_profile.signals if str(signal.id)
        }
        restored = self._load_recent_online_history_from_db(adjust_x_range=True, silent=True)
        if not restored:
            self._connection_events = []
            self._last_connection_state = None
            self.chart.set_connection_events([])
        self._pending_render_samples = []

        self._worker = ModbusWorker(copy.deepcopy(self.current_profile))
        self._worker.samples_ready.connect(self._on_samples_ready)
        self._worker.connection_changed.connect(self._on_connection_changed)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()
        self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
        if not self._render_timer.isActive():
            self._render_timer.start()

        self.action_start.setEnabled(False)
        self.action_stop.setEnabled(True)
        self.status_label.setText("Статус: опрос запущен (ожидание связи)")

    def _stop_worker(self) -> None:
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
        self.status_label.setText("Статус: остановлено")

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
                self.chart.add_connection_event(ts, state)
                if self._archive_store is not None:
                    try:
                        self._archive_store.insert_connection_event(self.current_profile.id, ts, state)
                    except Exception as exc:
                        self.status_label.setText(f"Ошибка архивации состояния связи: {exc}")
            self._last_connection_state = state

        if self._worker is not None and self._worker.isRunning():
            self.action_start.setEnabled(False)
            self.action_stop.setEnabled(True)

    def _on_chart_auto_mode_changed(self, auto_x: bool, auto_y: bool) -> None:
        self.action_auto_x.blockSignals(True)
        self.action_auto_x.setChecked(auto_x)
        self.action_auto_x.blockSignals(False)
        if hasattr(self, "values_auto_x_checkbox"):
            self.values_auto_x_checkbox.blockSignals(True)
            self.values_auto_x_checkbox.setChecked(bool(auto_x))
            self.values_auto_x_checkbox.blockSignals(False)
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
            self.status_label.setText("Ошибка: Мин/Макс должны быть числами")
            return

        if y_max <= y_min:
            self.status_label.setText("Ошибка: Макс должен быть больше Мин")
            return

        self.chart.set_axis_range(axis_index, y_min, y_max)
        self._mark_config_dirty()

    def _update_values_table(self, rows: list[dict], _cursor_visible: bool) -> None:
        self._last_values_rows = list(rows)
        sorted_rows = self._sorted_values_rows(list(rows))
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

                mode = str(row.get("mode", ""))
                if not enabled:
                    mode = f"{mode} (скрыт)".strip()
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
        self.status_label.setText(f"Ошибка: {message}")

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
            self.status_label.setText(f"Ошибка очистки архива: {exc}")

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
        self._pending_render_samples.append((float(ts), samples))
        if len(self._pending_render_samples) > self._max_pending_render_batches:
            # Keep the most recent section of stream to avoid unbounded growth
            # if UI thread is temporarily slower than poll thread.
            self._pending_render_samples = self._pending_render_samples[-self._max_pending_render_batches :]
        if not self._render_timer.isActive():
            self._apply_render_interval_runtime(self.current_profile.render_interval_ms)
            self._render_timer.start()

        archive_interval_s = max(0.05, self.current_profile.archive_interval_ms / 1000.0)
        if ts - self._last_archive_ts < archive_interval_s:
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
            "Подтверждение закрытия",
            "Сейчас идет запись архива. При закрытии приложения запись остановится.\nЗакрыть приложение?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        if self._force_close:
            self._stop_tags_polling(silent=True)
            self._record_shutdown_disconnect_event()
            self._stop_worker()
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
            dialog.setWindowTitle("Закрытие приложения")
            dialog.setText("Выберите действие при закрытии.")
            tray_btn = dialog.addButton("В трей", QMessageBox.ButtonRole.ActionRole)
            close_btn = dialog.addButton("Закрыть", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = dialog.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
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

        self._force_close = True
        self._stop_tags_polling(silent=True)
        self._record_shutdown_disconnect_event()
        self._stop_worker()
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

    app = QApplication([])
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

