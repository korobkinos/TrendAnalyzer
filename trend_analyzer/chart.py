from __future__ import annotations

import bisect
import time
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget

import pyqtgraph as pg

from .models import SignalConfig

SIGNAL_IDS_MIME_TYPE = "application/x-trend-signal-ids"


def format_ts_ms(ts: float) -> str:
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class WallClockAxisItem(pg.AxisItem):
    def __init__(self, orientation: str = "bottom", **kwargs):
        super().__init__(orientation=orientation, **kwargs)
        self._print_mode = False

    def set_print_mode(self, enabled: bool) -> None:
        self._print_mode = bool(enabled)

    def tickStrings(self, values, _scale, spacing):
        labels = []
        for value in values:
            try:
                ts = float(value)
                dt = datetime.fromtimestamp(ts)
                if self._print_mode:
                    # Print mode: two-line X label for readability.
                    date_text = dt.strftime("%Y-%m-%d")
                    if float(spacing) < 1.0:
                        time_text = dt.strftime("%H:%M:%S.%f")[:-3]
                    else:
                        time_text = dt.strftime("%H:%M:%S")
                    labels.append(f"{date_text}\n{time_text}")
                else:
                    labels.append(format_ts_ms(ts))
            except (OverflowError, OSError, ValueError):
                labels.append("")
        return labels


class MultiAxisChart(QWidget):
    auto_mode_changed = Signal(bool, bool)
    cursor_enabled_changed = Signal(bool)
    display_updated = Signal(object, bool)  # rows, cursor_visible
    scales_changed = Signal(object)  # list[dict]
    stats_range_changed = Signal(float, float)  # start_ts, end_ts
    x_range_changed = Signal(float, float)  # x_min, x_max
    export_image_requested = Signal()
    export_csv_requested = Signal()
    print_requested = Signal()
    signals_dropped = Signal(object)  # list[str]

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        self.plot_widget = pg.PlotWidget(axisItems={"bottom": WallClockAxisItem(orientation="bottom")})
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.setMenuEnabled(False, enableViewBoxMenu=False)
        self.plot_item.vb.setMenuEnabled(False)
        self._background_color = "#000000"
        self._grid_color = "#2f4f6f"
        self._grid_alpha = 0.25
        self._grid_x = True
        self._grid_y = True
        self.plot_widget.setBackground(self._background_color)
        self.plot_item.showGrid(x=self._grid_x, y=self._grid_y, alpha=self._grid_alpha)
        self.legend = self.plot_item.addLegend(offset=(10, 10))
        left_axis = self.plot_item.getAxis("left")
        left_axis.setLabel("Значение")
        left_axis.enableAutoSIPrefix(False)
        self.plot_item.showAxis("right", False)
        bottom_axis = self.plot_item.getAxis("bottom")
        bottom_axis.setLabel("Время")
        bottom_axis.enableAutoSIPrefix(False)
        layout.addWidget(self.plot_widget)

        self.cursor_label = QLabel("")
        self.cursor_label.hide()

        self.cursor_line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#f1c40f", width=1.5))
        self.cursor_line.hide()
        self.plot_item.addItem(self.cursor_line)
        self.stats_line_start = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#2ecc71", width=1.4))
        self.stats_line_end = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#e67e22", width=1.4))
        self.stats_line_start.hide()
        self.stats_line_end.hide()
        self.plot_item.addItem(self.stats_line_start)
        self.plot_item.addItem(self.stats_line_end)
        self.stats_region = pg.LinearRegionItem(
            values=[0.0, 1.0],
            movable=False,
            brush=pg.mkBrush(46, 204, 113, 55),
            pen=pg.mkPen(46, 204, 113, 110),
        )
        self.stats_region.setZValue(-15)
        self.stats_region.hide()
        self.plot_item.addItem(self.stats_region)

        self.plot_widget.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.plot_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plot_widget.customContextMenuRequested.connect(self._on_context_menu_requested)
        self.plot_widget.setAcceptDrops(True)
        self.plot_widget.viewport().setAcceptDrops(True)
        self.cursor_line.sigPositionChanged.connect(self._update_cursor_values)
        self.stats_line_start.sigPositionChanged.connect(self._on_stats_line_position_changed)
        self.stats_line_end.sigPositionChanged.connect(self._on_stats_line_position_changed)
        self.plot_item.vb.sigResized.connect(self._sync_views)
        self.plot_item.vb.sigXRangeChanged.connect(self._on_x_range_changed)
        self.plot_item.vb.sigYRangeChanged.connect(self._on_main_y_range_changed)
        self.plot_item.vb.sigStateChanged.connect(self._on_main_view_state_changed)
        self.plot_item.vb.sigRangeChangedManually.connect(self._on_main_range_changed_manually)
        self.plot_widget.viewport().installEventFilter(self)

        self._live_buffers: dict[str, tuple[list[float], list[float]]] = {}
        self._history_buffers: dict[str, tuple[list[float], list[float]]] = {}
        self._buffers: dict[str, tuple[list[float], list[float]]] = {}
        self._meta: dict[str, dict] = {}
        self._curves: dict[str, pg.PlotCurveItem] = {}
        self._signal_order: list[str] = []
        self._hidden_curve_cleared: set[str] = set()

        self._axis_views: dict[int, pg.ViewBox] = {}
        self._axis_items: dict[int, pg.AxisItem] = {}
        self._axis_signals: dict[int, list[str]] = {}
        self._axis_auto_y: dict[int, bool] = {}

        self._main_axis_index: int = 1

        self._max_points = 8000
        self._max_render_points = 2500
        self._min_render_points = 900
        self._max_detail_render_points = 18000
        self._max_total_render_points = 90000
        self._live_follow_render_points_cap = 1400
        self._live_follow_detail_points_cap = 6000
        self._live_follow_total_points_cap = 32000
        self._render_margin_ratio = 0.15
        self._curve_smoothing_enabled = False
        self._curve_smoothing_window = 5
        self._last_sample_ts: float | None = None
        self._auto_x = True
        self._soft_follow_latest_x = False
        self._auto_y = True
        self._applying_auto_range = False
        self._cursor_ratio: float | None = None
        self._setting_cursor_pos = False
        self._cursor_enabled = False
        self._stats_range_enabled = False
        self._setting_stats_range = False
        self._programmatic_x_change = False
        self._connection_events: list[tuple[float, bool]] = []
        self._connection_regions: list[pg.LinearRegionItem] = []
        self._last_wheel_ts = 0.0
        self._last_follow_x_max: float | None = None

    def eventFilter(self, watched, event):
        if watched is self.plot_widget.viewport():
            event_type = event.type()
            if event_type == QEvent.Type.Wheel:
                self._last_wheel_ts = time.monotonic()
            elif event_type in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                mime = event.mimeData() if hasattr(event, "mimeData") else None
                if mime is not None and mime.hasFormat(SIGNAL_IDS_MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif event_type == QEvent.Type.Drop:
                mime = event.mimeData() if hasattr(event, "mimeData") else None
                if mime is not None and mime.hasFormat(SIGNAL_IDS_MIME_TYPE):
                    try:
                        raw = bytes(mime.data(SIGNAL_IDS_MIME_TYPE)).decode("utf-8", errors="ignore")
                    except Exception:
                        raw = ""
                    signal_ids: list[str] = []
                    seen: set[str] = set()
                    for line in raw.splitlines():
                        signal_id = str(line).strip()
                        if not signal_id or signal_id in seen:
                            continue
                        seen.add(signal_id)
                        signal_ids.append(signal_id)
                    if signal_ids:
                        self.signals_dropped.emit(signal_ids)
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(watched, event)

    def _on_context_menu_requested(self, pos) -> None:
        menu = QMenu(self)

        action_auto_x = menu.addAction("Авто X")
        action_auto_x.setCheckable(True)
        action_auto_x.setChecked(bool(self._auto_x))
        action_auto_x.toggled.connect(self.set_auto_x)

        action_cursor = menu.addAction("Курсор")
        action_cursor.setCheckable(True)
        action_cursor.setChecked(bool(self._cursor_enabled))
        action_cursor.toggled.connect(self.set_cursor_enabled)

        action_reset = menu.addAction("Сброс масштаба")
        action_reset.triggered.connect(self.reset_view)

        menu.addSeparator()
        action_export_image = menu.addAction("Экспорт изображения...")
        action_export_image.triggered.connect(self.export_image_requested.emit)
        action_export_csv = menu.addAction("Экспорт данных (CSV)...")
        action_export_csv.triggered.connect(self.export_csv_requested.emit)
        action_print = menu.addAction("Печать графика...")
        action_print.triggered.connect(self.print_requested.emit)

        menu.exec(self.plot_widget.mapToGlobal(pos))

    def _arrange_layout_for_left_axes(self, total_axes: int) -> None:
        shift = max(0, int(total_axes) - 1)
        layout = self.plot_item.layout

        items = [
            (self.plot_item.vb, 2, 1 + shift),
            (self.plot_item.getAxis("left"), 2, shift),
            (self.plot_item.getAxis("bottom"), 3, 1 + shift),
            (self.plot_item.getAxis("top"), 1, 1 + shift),
            (self.plot_item.getAxis("right"), 2, 2 + shift),
            (self.plot_item.titleLabel, 0, 1 + shift),
        ]

        for item, _row, _col in items:
            try:
                layout.removeItem(item)
            except Exception:
                pass

        for item, row, col in items:
            layout.addItem(item, row, col)

        max_col = 2 + shift
        for col in range(max_col + 1):
            # Axis columns should not reserve free space.
            layout.setColumnStretchFactor(col, 0)
            layout.setColumnMinimumWidth(col, 0)
            layout.setColumnPreferredWidth(col, 0)
        layout.setColumnStretchFactor(1 + shift, 100)
        self.plot_item.showAxis("right", False)

    def _clear_extra_axes(self) -> None:
        scene = self.plot_item.scene()
        for view_box in list(self._axis_views.values()):
            if scene is not None:
                scene.removeItem(view_box)
        for axis in list(self._axis_items.values()):
            try:
                self.plot_item.layout.removeItem(axis)
            except Exception:
                pass
            if scene is not None:
                scene.removeItem(axis)

        self._axis_views.clear()
        self._axis_items.clear()

    def _view_for_axis(self, axis_index: int) -> pg.ViewBox | None:
        if axis_index == self._main_axis_index:
            return self.plot_item.vb
        return self._axis_views.get(axis_index)

    def _clear_connection_overlay(self) -> None:
        for item in self._connection_regions:
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass
        self._connection_regions = []

    def _render_connection_overlay(self) -> None:
        self._clear_connection_overlay()
        if not self._connection_events:
            return

        coverage_end = self._last_sample_ts
        if coverage_end is None:
            coverage_end = self._connection_events[-1][0]

        disconnect_start: float | None = None
        for ts, is_connected in self._connection_events:
            if not is_connected and disconnect_start is None:
                disconnect_start = ts
            elif is_connected and disconnect_start is not None:
                if ts > disconnect_start:
                    region = pg.LinearRegionItem(
                        values=[disconnect_start, ts],
                        movable=False,
                        brush=pg.mkBrush(220, 64, 64, 45),
                        pen=pg.mkPen(220, 64, 64, 90),
                    )
                    region.setZValue(-20)
                    self.plot_item.addItem(region)
                    self._connection_regions.append(region)
                disconnect_start = None

        if disconnect_start is not None:
            end_ts = max(disconnect_start + 1.0, float(coverage_end))
            region = pg.LinearRegionItem(
                values=[disconnect_start, end_ts],
                movable=False,
                brush=pg.mkBrush(220, 64, 64, 45),
                pen=pg.mkPen(220, 64, 64, 90),
            )
            region.setZValue(-20)
            self.plot_item.addItem(region)
            self._connection_regions.append(region)

    def set_connection_events(self, events: list[list[float]] | list[tuple[float, bool]]) -> None:
        normalized: list[tuple[float, bool]] = []
        for item in events:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                ts = float(item[0])
                state = bool(int(item[1]))
            except (TypeError, ValueError):
                continue
            normalized.append((ts, state))

        normalized.sort(key=lambda pair: pair[0])
        deduped: list[tuple[float, bool]] = []
        for ts, state in normalized:
            if deduped and abs(deduped[-1][0] - ts) < 1e-9:
                deduped[-1] = (ts, state)
                continue
            if deduped and deduped[-1][1] == state:
                continue
            deduped.append((ts, state))

        self._connection_events = deduped
        self._render_connection_overlay()

    def add_connection_event(self, ts: float, is_connected: bool) -> None:
        state = bool(is_connected)
        ts_f = float(ts)
        if self._connection_events and self._connection_events[-1][1] == state:
            return
        self._connection_events.append((ts_f, state))
        self._render_connection_overlay()

    def _data_ts_bounds(self) -> tuple[float | None, float | None]:
        min_ts: float | None = None
        max_ts: float | None = None
        for xs, _ys in self._buffers.values():
            if not xs:
                continue
            left = float(xs[0])
            right = float(xs[-1])
            if min_ts is None or left < min_ts:
                min_ts = left
            if max_ts is None or right > max_ts:
                max_ts = right
        return min_ts, max_ts

    def _prune_connection_events_to_data_window(self) -> None:
        if not self._connection_events:
            return
        min_ts, _max_ts = self._data_ts_bounds()
        if min_ts is None:
            return
        cutoff = float(min_ts) - 5.0
        first_idx: int | None = None
        for idx, (ts, _state) in enumerate(self._connection_events):
            if float(ts) >= cutoff:
                first_idx = idx
                break
        if first_idx is None:
            keep_from = max(0, len(self._connection_events) - 1)
        else:
            keep_from = max(0, first_idx - 1)
        if keep_from > 0:
            self._connection_events = self._connection_events[keep_from:]

    @staticmethod
    def _merge_series(
        history_pair: tuple[list[float], list[float]],
        live_pair: tuple[list[float], list[float]],
    ) -> tuple[list[float], list[float]]:
        hx, hy = history_pair
        lx, ly = live_pair
        if not hx:
            return list(lx), list(ly)
        if not lx:
            return list(hx), list(hy)

        out_xs: list[float] = []
        out_ys: list[float] = []
        i = 0
        j = 0
        while i < len(hx) and j < len(lx):
            hxv = float(hx[i])
            lxv = float(lx[j])
            if abs(hxv - lxv) <= 1e-9:
                out_xs.append(lxv)
                out_ys.append(float(ly[j]))
                i += 1
                j += 1
                continue
            if hxv < lxv:
                out_xs.append(hxv)
                out_ys.append(float(hy[i]))
                i += 1
            else:
                out_xs.append(lxv)
                out_ys.append(float(ly[j]))
                j += 1

        while i < len(hx):
            out_xs.append(float(hx[i]))
            out_ys.append(float(hy[i]))
            i += 1
        while j < len(lx):
            out_xs.append(float(lx[j]))
            out_ys.append(float(ly[j]))
            j += 1
        return out_xs, out_ys

    def _refresh_signal_buffer(self, signal_id: str) -> None:
        live_pair = self._live_buffers.get(signal_id, ([], []))
        history_pair = self._history_buffers.get(signal_id, ([], []))
        self._buffers[signal_id] = self._merge_series(history_pair, live_pair)

    def _append_live_point_to_buffer(self, signal_id: str, ts: float, value: float, *, overflowed: bool = False) -> None:
        if overflowed:
            self._refresh_signal_buffer(signal_id)
            return

        history_xs, _history_ys = self._history_buffers.get(signal_id, ([], []))
        buffer_xs, buffer_ys = self._buffers.get(signal_id, ([], []))
        if history_xs:
            if not buffer_xs or len(buffer_xs) < len(history_xs):
                self._refresh_signal_buffer(signal_id)
                return
            try:
                history_tail = float(history_xs[-1])
            except Exception:
                self._refresh_signal_buffer(signal_id)
                return
            if float(ts) < history_tail:
                self._refresh_signal_buffer(signal_id)
                return

        buffer_xs.append(float(ts))
        buffer_ys.append(float(value))
        self._buffers[signal_id] = (buffer_xs, buffer_ys)

    def _refresh_all_buffers(self) -> None:
        for signal_id in list(self._signal_order):
            self._refresh_signal_buffer(signal_id)

    def _recompute_last_sample_ts(self) -> None:
        last_ts: float | None = None
        for xs, _ys in self._buffers.values():
            if not xs:
                continue
            candidate = float(xs[-1])
            if last_ts is None or candidate > last_ts:
                last_ts = candidate
        self._last_sample_ts = last_ts

    def _axis_y_range(self, axis_index: int) -> tuple[float, float]:
        view = self._view_for_axis(axis_index)
        if view is None:
            return 0.0, 1.0
        yr = view.viewRange()[1]
        return float(yr[0]), float(yr[1])

    def configure_signals(self, signals: list[SignalConfig]) -> None:
        old_live_buffers = self._live_buffers
        old_history_buffers = self._history_buffers
        old_buffers = self._buffers
        old_axis_auto = dict(self._axis_auto_y)

        for curve in list(self._curves.values()):
            self.plot_item.removeItem(curve)
            for view_box in self._axis_views.values():
                try:
                    view_box.removeItem(curve)
                except Exception:
                    pass

        self._clear_extra_axes()

        self._meta.clear()
        self._curves.clear()
        self._axis_signals.clear()
        self._axis_auto_y.clear()
        self._live_buffers = {}
        self._history_buffers = {}
        self._buffers = {}
        self._signal_order = []
        self._hidden_curve_cleared = set()

        if self.legend.scene() is not None:
            self.legend.scene().removeItem(self.legend)
        self.legend = self.plot_item.addLegend(offset=(10, 10))

        if not signals:
            self._arrange_layout_for_left_axes(1)
            main_axis = self.plot_item.getAxis("left")
            main_axis.setLabel("Значение")
            main_axis.enableAutoSIPrefix(False)
            main_axis.setTextPen(pg.mkPen("#9aa0a6"))
            main_axis.setTickPen(pg.mkPen("#9aa0a6"))
            self._emit_display_rows()
            self._emit_scales_changed()
            return

        axis_index_to_signals: dict[int, list[SignalConfig]] = {}
        for signal in signals:
            axis_index = max(1, int(signal.axis_index))
            axis_index_to_signals.setdefault(axis_index, []).append(signal)

        axis_indexes = sorted(axis_index_to_signals.keys())
        self._main_axis_index = axis_indexes[0]
        self._arrange_layout_for_left_axes(len(axis_indexes))

        main_axis_signals = axis_index_to_signals[self._main_axis_index]
        main_color = main_axis_signals[0].color if main_axis_signals else "#1f77b4"
        main_axis = self.plot_item.getAxis("left")
        main_axis.setLabel(f"Шкала {self._main_axis_index}", color=main_color)
        main_axis.enableAutoSIPrefix(False)
        main_axis.setTextPen(pg.mkPen(main_color))
        main_axis.setTickPen(pg.mkPen(main_color))

        shift = len(axis_indexes) - 1
        scene = self.plot_item.scene()
        for pos, axis_index in enumerate(axis_indexes[1:], start=1):
            axis_signals = axis_index_to_signals[axis_index]
            axis_color = axis_signals[0].color if axis_signals else "#1f77b4"

            axis_item = pg.AxisItem("left", parent=self.plot_item)
            axis_item.setLabel(f"Шкала {axis_index}", color=axis_color)
            axis_item.enableAutoSIPrefix(False)
            axis_item.setTextPen(pg.mkPen(axis_color))
            axis_item.setTickPen(pg.mkPen(axis_color))
            self.plot_item.layout.addItem(axis_item, 2, shift - pos)

            view_box = pg.ViewBox(enableMenu=False)
            if scene is not None:
                scene.addItem(view_box)
            axis_item.linkToView(view_box)
            view_box.setXLink(self.plot_item.vb)
            # Allow axis-specific Y scaling via linked axis interaction.
            view_box.setMouseEnabled(x=False, y=True)
            view_box.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            view_box.setMenuEnabled(False)
            view_box.sigYRangeChanged.connect(
                lambda _vb, _range, axis_idx=axis_index: self._on_secondary_y_range_changed(axis_idx)
            )
            view_box.sigStateChanged.connect(
                lambda _vb, axis_idx=axis_index: self._on_secondary_view_state_changed(axis_idx)
            )
            view_box.sigRangeChangedManually.connect(
                lambda mask, axis_idx=axis_index: self._on_secondary_range_changed_manually(axis_idx, mask)
            )

            self._axis_items[axis_index] = axis_item
            self._axis_views[axis_index] = view_box

        for signal in signals:
            axis_index = max(1, int(signal.axis_index))
            self._signal_order.append(signal.id)
            self._axis_signals.setdefault(axis_index, []).append(signal.id)
            self._axis_auto_y.setdefault(axis_index, old_axis_auto.get(axis_index, self._auto_y))

            self._meta[signal.id] = {
                "name": signal.name,
                "color": signal.color,
                "unit": signal.unit,
                "enabled": signal.enabled,
                "axis_index": axis_index,
                "data_type": str(getattr(signal, "data_type", "") or ""),
            }
            self._live_buffers[signal.id] = old_live_buffers.get(signal.id, old_buffers.get(signal.id, ([], [])))
            self._history_buffers[signal.id] = old_history_buffers.get(signal.id, ([], []))
            self._buffers[signal.id] = ([], [])
            self._refresh_signal_buffer(signal.id)

            curve = pg.PlotCurveItem(pen=pg.mkPen(signal.color, width=2), name=signal.name)
            curve.setVisible(signal.enabled)
            try:
                curve.setSkipFiniteCheck(True)
            except Exception:
                pass

            if axis_index == self._main_axis_index:
                self.plot_item.addItem(curve)
            else:
                self._axis_views[axis_index].addItem(curve)

            self._curves[signal.id] = curve
            self.legend.addItem(curve, signal.name)

        for axis_index in axis_indexes:
            self._axis_auto_y.setdefault(axis_index, old_axis_auto.get(axis_index, self._auto_y))

        self._recompute_last_sample_ts()
        self._sync_views()
        self.set_visual_settings()
        self._redraw_all()
        self._apply_auto_range()
        self._render_connection_overlay()
        self._emit_display_rows()
        self._emit_scales_changed()

    def append_samples(self, ts: float, samples: dict[str, tuple[str, float]]) -> None:
        self.append_samples_batch([(float(ts), samples)])

    def append_samples_batch(self, batch: list[tuple[float, dict[str, tuple[str, float]]]]) -> None:
        changed = False
        last_ts: float | None = None
        changed_signal_ids: set[str] = set()
        for ts, samples in batch:
            ts_f = float(ts)
            sample_changed = False
            sample_last_ts: float | None = None
            for signal_id, (_signal_name, value) in samples.items():
                if signal_id not in self._live_buffers:
                    continue

                xs, ys = self._live_buffers[signal_id]
                # Guard against out-of-order timestamps from mixed sources/heartbeat:
                # keep X monotonic for each signal so auto-scroll never "jumps back".
                ts_plot = ts_f
                if xs:
                    prev_ts = float(xs[-1])
                    if ts_plot <= prev_ts:
                        ts_plot = prev_ts + 1e-6
                xs.append(ts_plot)
                ys.append(value)
                overflowed = len(xs) > self._max_points
                if overflowed:
                    xs.pop(0)
                    ys.pop(0)
                self._append_live_point_to_buffer(signal_id, ts_plot, value, overflowed=overflowed)
                sample_changed = True
                changed_signal_ids.add(signal_id)
                if sample_last_ts is None or ts_plot > sample_last_ts:
                    sample_last_ts = ts_plot

            if sample_changed:
                changed = True
                if last_ts is None or (sample_last_ts is not None and sample_last_ts > last_ts):
                    last_ts = sample_last_ts

        if not changed:
            return

        if last_ts is not None:
            if self._last_sample_ts is None or last_ts > self._last_sample_ts:
                self._last_sample_ts = last_ts
            else:
                last_ts = float(self._last_sample_ts)
        self._prune_connection_events_to_data_window()
        follow_latest_x = self._follows_latest_x()
        if follow_latest_x and last_ts is not None:
            self._scroll_x_to_latest(last_ts)
            self._redraw_all()
        else:
            self._redraw_signal_ids(changed_signal_ids)
            self._update_axis_visibility()
        if self._connection_events and not self._connection_events[-1][1]:
            self._render_connection_overlay()
        if self.cursor_line.isVisible() and follow_latest_x:
            self._place_cursor_by_ratio()
        self._update_cursor_values()
        self._emit_display_rows()
        if any(self._axis_auto_y.get(axis_index, False) for axis_index in self._axis_signals):
            self._emit_scales_changed()

    def clear_data(self) -> None:
        for signal_id in list(self._live_buffers.keys()):
            self._live_buffers[signal_id] = ([], [])
        for signal_id in list(self._history_buffers.keys()):
            self._history_buffers[signal_id] = ([], [])
        for signal_id in list(self._buffers.keys()):
            self._buffers[signal_id] = ([], [])
        self._last_sample_ts = None
        self._last_follow_x_max = None
        self.cursor_line.hide()
        self._cursor_ratio = None
        self._redraw_all()
        self._apply_auto_range()
        self._render_connection_overlay()
        self._emit_display_rows()

    def set_archive_data(self, payload: dict[str, list[list[float]]]) -> None:
        for signal_id in list(self._history_buffers.keys()):
            raw_points = payload.get(signal_id) or []
            xs: list[float] = []
            ys: list[float] = []
            for point in raw_points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    xs.append(float(point[0]))
                    ys.append(float(point[1]))
                except (TypeError, ValueError):
                    continue
            self._history_buffers[signal_id] = (xs, ys)
            self._refresh_signal_buffer(signal_id)

        self._recompute_last_sample_ts()
        self._last_follow_x_max = None
        self._prune_connection_events_to_data_window()
        self._redraw_all()
        self._apply_auto_range()
        self._render_connection_overlay()
        self._emit_display_rows()
        self._emit_scales_changed()

    def build_archive_payload(
        self,
        profile_id: str,
        profile_name: str,
        signals: list[SignalConfig],
        connection_events: list[list[float]] | None = None,
        connection_config: dict | None = None,
    ) -> dict:
        samples: dict[str, list[list[float]]] = {}
        min_ts: float | None = None
        max_ts: float | None = None
        for signal_id in self._signal_order:
            xs, ys = self._buffers.get(signal_id, ([], []))
            points: list[list[float]] = []
            for ts, value in zip(xs, ys):
                ts_f = float(ts)
                val_f = float(value)
                points.append([ts_f, val_f])
                if min_ts is None or ts_f < min_ts:
                    min_ts = ts_f
                if max_ts is None or ts_f > max_ts:
                    max_ts = ts_f
            samples[signal_id] = points

        payload = {
            "format": "trend_archive_v1",
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "profile_id": profile_id,
            "profile_name": profile_name,
            "signals": [item.to_dict() for item in signals],
            "samples": samples,
        }
        if connection_events:
            payload["connection_events"] = connection_events
        if connection_config:
            payload["connection_config"] = connection_config
        if min_ts is not None and max_ts is not None:
            payload["period_start_ts"] = float(min_ts)
            payload["period_end_ts"] = float(max_ts)
            payload["period_start"] = datetime.fromtimestamp(min_ts).isoformat(timespec="seconds")
            payload["period_end"] = datetime.fromtimestamp(max_ts).isoformat(timespec="seconds")
        return payload

    def set_auto_x(self, enabled: bool) -> None:
        prev_auto_x = bool(self._auto_x)
        self._auto_x = bool(enabled)
        # Explicit Auto X toggle from UI/menu must switch the chart immediately.
        # Hidden "soft follow" here makes the checkbox look broken because the
        # viewport keeps scrolling until the user drags X by hand.
        self._soft_follow_latest_x = False
        if self._auto_x and not prev_auto_x:
            # Re-enable should always re-anchor to latest time immediately.
            self._last_follow_x_max = None
        if not self._follows_latest_x():
            self._last_follow_x_max = None
        if self._follows_latest_x() and self._last_sample_ts is not None:
            self._scroll_x_to_latest(self._last_sample_ts)
        self._apply_auto_range()
        self._emit_scales_changed()

    def _follows_latest_x(self) -> bool:
        return bool(self._auto_x or self._soft_follow_latest_x)

    def follows_latest_x(self) -> bool:
        return self._follows_latest_x()

    def force_manual_x(self) -> None:
        self._auto_x = False
        self._soft_follow_latest_x = False
        self._last_follow_x_max = None
        self._apply_auto_range()
        self._emit_scales_changed()

    def set_auto_y(self, enabled: bool) -> None:
        self._auto_y = bool(enabled)
        for axis_index in list(self._axis_signals.keys()):
            self._axis_auto_y[axis_index] = self._auto_y
        self._apply_auto_range()
        self._emit_scales_changed()

    def set_cursor_enabled(self, enabled: bool) -> None:
        new_state = bool(enabled)
        state_changed = self._cursor_enabled != new_state
        self._cursor_enabled = new_state
        if not self._cursor_enabled:
            self.cursor_line.hide()
        else:
            x_min, x_max = self.plot_item.vb.viewRange()[0]
            x_center = x_min + (x_max - x_min) * 0.5
            if not self.cursor_line.isVisible():
                self._set_cursor_pos(float(x_center))
                self._update_cursor_ratio_from_x(float(x_center))
            self.cursor_line.show()
            self._update_cursor_values()
        if state_changed:
            self.cursor_enabled_changed.emit(self._cursor_enabled)
        self._emit_display_rows()

    def current_x_range(self) -> tuple[float, float]:
        x_min, x_max = self.plot_item.vb.viewRange()[0]
        return float(min(x_min, x_max)), float(max(x_min, x_max))

    def set_x_range(self, x_min: float, x_max: float) -> None:
        left = float(x_min)
        right = float(x_max)
        if right <= left:
            return
        self._programmatic_x_change = True
        try:
            self.plot_item.vb.setXRange(left, right, padding=0)
        finally:
            self._programmatic_x_change = False
        self._last_follow_x_max = float(right)
        self._redraw_all()
        if self.cursor_line.isVisible():
            self._update_cursor_values()

    def set_x_window_seconds(self, span_seconds: float, anchor_ts: float | None = None) -> None:
        span = max(0.1, float(span_seconds))
        anchor = float(anchor_ts) if anchor_ts is not None else float(self._last_sample_ts or time.time())
        self.set_x_range(anchor - span, anchor)

    def set_stats_range_enabled(self, enabled: bool) -> None:
        self._stats_range_enabled = bool(enabled)
        if not self._stats_range_enabled:
            self.stats_line_start.hide()
            self.stats_line_end.hide()
            self.stats_region.hide()
            return
        self.place_stats_range_in_view()

    def place_stats_range_in_view(self) -> None:
        x_min, x_max = self.current_x_range()
        if x_max <= x_min:
            x_center = x_min
            start_x = x_center - 0.5
            end_x = x_center + 0.5
        else:
            span = x_max - x_min
            start_x = x_min + span * 0.33
            end_x = x_min + span * 0.66
        self.set_stats_range(start_x, end_x)

    def set_stats_range(self, start_ts: float, end_ts: float) -> None:
        start = float(start_ts)
        end = float(end_ts)
        if end < start:
            start, end = end, start
        self._setting_stats_range = True
        self.stats_line_start.setPos(start)
        self.stats_line_end.setPos(end)
        self._setting_stats_range = False
        if self._stats_range_enabled:
            self.stats_line_start.show()
            self.stats_line_end.show()
            self.stats_region.setRegion([start, end])
            self.stats_region.show()
        else:
            self.stats_region.hide()
        self._emit_stats_range_changed()

    def get_stats_range(self) -> tuple[float, float] | None:
        if not self._stats_range_enabled:
            return None
        if not self.stats_line_start.isVisible() or not self.stats_line_end.isVisible():
            return None
        start = float(self.stats_line_start.value())
        end = float(self.stats_line_end.value())
        if end < start:
            start, end = end, start
        return start, end

    def _emit_stats_range_changed(self) -> None:
        points = self.get_stats_range()
        if points is None:
            return
        self.stats_range_changed.emit(points[0], points[1])

    def _on_stats_line_position_changed(self) -> None:
        if self._setting_stats_range:
            return
        points = self.get_stats_range()
        if points is not None:
            self.stats_region.setRegion([points[0], points[1]])
            self.stats_region.show()
        else:
            self.stats_region.hide()
        self._emit_stats_range_changed()

    def compute_statistics(self, start_ts: float, end_ts: float) -> list[dict]:
        start = float(start_ts)
        end = float(end_ts)
        if end < start:
            start, end = end, start

        rows: list[dict] = []
        for signal_id in self._signal_order:
            meta = self._meta.get(signal_id)
            if not meta or not bool(meta.get("enabled", True)):
                continue
            xs, ys = self._buffers.get(signal_id, ([], []))
            if not xs:
                continue

            left = bisect.bisect_left(xs, start)
            right = bisect.bisect_right(xs, end)
            if right <= left:
                continue

            sub_xs = xs[left:right]
            sub_ys = ys[left:right]
            if not sub_xs:
                continue

            min_value = min(sub_ys)
            max_value = max(sub_ys)
            avg_value = sum(sub_ys) / len(sub_ys)
            time_span = float(sub_xs[-1] - sub_xs[0]) if len(sub_xs) > 1 else 0.0
            speed = None
            if len(sub_xs) > 1 and time_span > 0:
                speed = float((sub_ys[-1] - sub_ys[0]) / time_span)

            rows.append(
                {
                    "signal_id": signal_id,
                    "name": str(meta.get("name", signal_id)),
                    "unit": str(meta.get("unit", "")),
                    "count": len(sub_ys),
                    "min": float(min_value),
                    "max": float(max_value),
                    "avg": float(avg_value),
                    "speed": speed,
                    "span_s": float(time_span),
                }
            )
        return rows

    def set_signal_enabled(self, signal_id: str, enabled: bool) -> None:
        meta = self._meta.get(signal_id)
        if meta is None:
            return
        new_state = bool(enabled)
        if bool(meta.get("enabled", True)) == new_state:
            return
        meta["enabled"] = new_state
        curve = self._curves.get(signal_id)
        if curve is not None:
            curve.setVisible(new_state)
        self._hidden_curve_cleared.discard(str(signal_id))
        self._redraw_all()
        self._emit_display_rows()
        self._emit_scales_changed()

    def set_signals_enabled(self, signal_ids: list[str], enabled: bool) -> int:
        unique_ids: list[str] = []
        seen: set[str] = set()
        for signal_id in list(signal_ids or []):
            sid = str(signal_id).strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            unique_ids.append(sid)
        if not unique_ids:
            return 0

        new_state = bool(enabled)
        changed_count = 0
        for signal_id in unique_ids:
            meta = self._meta.get(signal_id)
            if meta is None:
                continue
            if bool(meta.get("enabled", True)) == new_state:
                continue
            meta["enabled"] = new_state
            changed_count += 1
            curve = self._curves.get(signal_id)
            if curve is not None:
                curve.setVisible(new_state)
            self._hidden_curve_cleared.discard(str(signal_id))

        if changed_count > 0:
            self._redraw_all()
            self._emit_display_rows()
            self._emit_scales_changed()
        return changed_count

    def _rebuild_legend(self) -> None:
        if self.legend.scene() is not None:
            self.legend.scene().removeItem(self.legend)
        self.legend = self.plot_item.addLegend(offset=(10, 10))
        for signal_id in self._signal_order:
            curve = self._curves.get(signal_id)
            meta = self._meta.get(signal_id)
            if curve is None or not isinstance(meta, dict):
                continue
            self.legend.addItem(curve, str(meta.get("name", signal_id)))

    def set_signal_name(self, signal_id: str, name: str) -> None:
        meta = self._meta.get(signal_id)
        if meta is None:
            return
        new_name = str(name or "").strip() or str(meta.get("name") or signal_id)
        if str(meta.get("name", "")) == new_name:
            return
        meta["name"] = new_name
        curve = self._curves.get(signal_id)
        if curve is not None:
            try:
                curve.setName(new_name)
            except Exception:
                pass
        self._rebuild_legend()
        self._emit_display_rows()
        self._emit_scales_changed()

    def set_signal_color(self, signal_id: str, color: str) -> None:
        meta = self._meta.get(signal_id)
        curve = self._curves.get(signal_id)
        if meta is None or curve is None:
            return

        meta["color"] = str(color)
        curve.setPen(pg.mkPen(str(color), width=2))
        self._emit_display_rows()

    def get_signal_color(self, signal_id: str) -> str:
        meta = self._meta.get(signal_id, {})
        return str(meta.get("color", "#1f77b4"))

    def export_samples_payload(self, only_enabled: bool = False) -> dict:
        signals: list[dict] = []
        samples: dict[str, list[list[float]]] = {}

        for signal_id in self._signal_order:
            meta = self._meta.get(signal_id)
            if not isinstance(meta, dict):
                continue
            enabled = bool(meta.get("enabled", True))
            if only_enabled and not enabled:
                continue
            signals.append(
                {
                    "id": signal_id,
                    "name": str(meta.get("name", signal_id)),
                    "axis_index": int(meta.get("axis_index", 1)),
                    "enabled": enabled,
                    "color": str(meta.get("color", "#1f77b4")),
                    "unit": str(meta.get("unit", "")),
                }
            )

            xs, ys = self._buffers.get(signal_id, ([], []))
            points: list[list[float]] = []
            for ts, value in zip(xs, ys):
                points.append([float(ts), float(value)])
            samples[signal_id] = points

        return {"signals": signals, "samples": samples}

    def _scroll_x_to_latest(self, latest_ts: float) -> None:
        x_min, x_max = self.plot_item.vb.viewRange()[0]
        span = x_max - x_min
        if span <= 0 or span > 86400:
            span = 60.0
        margin = max(0.02 * span, 0.1)
        target_max = float(latest_ts) + margin
        # Keep follow motion monotonic to avoid visible "step back" jitter
        # when multiple timing sources (poll/render/heartbeat) race.
        # Do not hard-bind to current x_max; otherwise after a manual pan to
        # future Auto-X may appear frozen until time "catches up".
        if self._last_follow_x_max is None:
            new_max = float(target_max)
        else:
            snap_back_threshold = max(0.05 * span, 0.2)
            if float(x_max) > (float(target_max) + snap_back_threshold):
                new_max = float(target_max)
            else:
                new_max = max(float(self._last_follow_x_max), float(target_max))
        new_min = new_max - span
        self._programmatic_x_change = True
        try:
            self.plot_item.vb.setXRange(new_min, new_max, padding=0)
        finally:
            self._programmatic_x_change = False
        self._last_follow_x_max = float(new_max)

    def reset_view(self) -> None:
        for axis_index in self._axis_signals.keys():
            view_box = self._view_for_axis(axis_index)
            if view_box is None:
                continue
            view_box.autoRange()
        self._emit_scales_changed()

    def set_axis_auto_y(self, axis_index: int, enabled: bool) -> None:
        axis_index = int(axis_index)
        self._axis_auto_y[axis_index] = bool(enabled)
        if axis_index == self._main_axis_index:
            self._auto_y = bool(enabled)
        self._apply_auto_range()
        self._emit_scales_changed()

    def set_axis_range(self, axis_index: int, y_min: float, y_max: float) -> None:
        axis_index = int(axis_index)
        if y_max <= y_min:
            return

        view_box = self._view_for_axis(axis_index)
        if view_box is None:
            return

        self._axis_auto_y[axis_index] = False
        if axis_index == self._main_axis_index:
            self._auto_y = False
        view_box.setYRange(y_min, y_max, padding=0)

        self._apply_auto_range()
        self._emit_scales_changed()

    def export_scale_states(self) -> list[dict]:
        rows: list[dict] = []
        for axis_index in sorted(self._axis_signals.keys()):
            y_min, y_max = self._axis_y_range(axis_index)
            auto_y = bool(self._axis_auto_y.get(axis_index, axis_index == self._main_axis_index and self._auto_y))
            rows.append(
                {
                    "axis_index": int(axis_index),
                    "auto_y": auto_y,
                    "y_min": float(y_min),
                    "y_max": float(y_max),
                }
            )
        return rows

    def apply_scale_states(self, rows: list[dict]) -> None:
        if not isinstance(rows, list):
            return
        mapping: dict[int, dict] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                axis_index = int(item.get("axis_index"))
            except (TypeError, ValueError):
                continue
            mapping[axis_index] = item

        for axis_index in sorted(self._axis_signals.keys()):
            saved = mapping.get(axis_index)
            if saved is None:
                continue
            auto_y = bool(saved.get("auto_y", True))
            self.set_axis_auto_y(axis_index, auto_y)
            if auto_y:
                continue
            try:
                y_min = float(saved.get("y_min"))
                y_max = float(saved.get("y_max"))
            except (TypeError, ValueError):
                continue
            if y_max <= y_min:
                continue
            self.set_axis_range(axis_index, y_min, y_max)

    def _apply_auto_range(self) -> None:
        main_auto_y = self._axis_auto_y.get(self._main_axis_index, self._auto_y)
        self._auto_y = bool(main_auto_y)
        self._applying_auto_range = True
        try:
            # X autoscroll is controlled explicitly in append_samples.
            self.plot_item.vb.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            self.plot_item.vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=main_auto_y)
            for axis_index, view_box in self._axis_views.items():
                view_box.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
                view_box.enableAutoRange(
                    axis=pg.ViewBox.YAxis,
                    enable=self._axis_auto_y.get(axis_index, self._auto_y),
                )
            self.plot_item.vb.updateAutoRange()
            for view_box in self._axis_views.values():
                view_box.updateAutoRange()
        finally:
            self._applying_auto_range = False
        self.auto_mode_changed.emit(self._auto_x, self._auto_y)

    def _on_main_view_state_changed(self, _view_box) -> None:
        if self._applying_auto_range:
            return
        state = self.plot_item.vb.state.get("autoRange", [False, False])
        auto_y = bool(state[1])
        self._axis_auto_y[self._main_axis_index] = auto_y
        self._auto_y = auto_y
        for axis_index, view_box in self._axis_views.items():
            view_box.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=self._axis_auto_y.get(axis_index, auto_y))
        self.auto_mode_changed.emit(self._auto_x, auto_y)
        self._emit_scales_changed()

    def _redraw_signal_ids(self, signal_ids: list[str] | set[str] | tuple[str, ...]) -> None:
        x_range = self.plot_item.vb.viewRange()[0]
        x_min, x_max = float(x_range[0]), float(x_range[1])
        seen_ids: set[str] = set()
        for raw_signal_id in list(signal_ids or []):
            signal_id = str(raw_signal_id)
            if not signal_id or signal_id in seen_ids:
                continue
            seen_ids.add(signal_id)
            curve = self._curves.get(signal_id)
            if curve is None:
                continue
            xs, ys = self._buffers.get(signal_id, ([], []))
            meta = self._meta.get(signal_id)
            is_enabled = bool(meta.get("enabled", True)) if isinstance(meta, dict) else True
            if not is_enabled:
                # Keep data in buffers for table/cursor/history, but do not spend
                # CPU on decimation/drawing for hidden signals.
                if signal_id not in self._hidden_curve_cleared:
                    curve.setData([], [])
                    self._hidden_curve_cleared.add(signal_id)
                continue
            self._hidden_curve_cleared.discard(signal_id)
            draw_xs, draw_ys = self._curve_data_for_view(xs, ys, x_min, x_max)
            if self._curve_smoothing_enabled and draw_ys:
                data_type = str(meta.get("data_type", "") or "").strip().lower() if isinstance(meta, dict) else ""
                if data_type != "bool":
                    # Always use centered smoothing so live-follow and history
                    # views look identical — the same signal shape regardless of
                    # which viewing mode is active.
                    draw_ys = self._smooth_series_moving_average(
                        draw_ys,
                        self._curve_smoothing_window,
                        causal=False,
                    )
            curve.setData(draw_xs, draw_ys)

    def _update_axis_visibility(self) -> None:
        main_signal_ids = self._axis_signals.get(self._main_axis_index, [])
        main_visible = any(bool(self._meta.get(sig_id, {}).get("enabled", True)) for sig_id in main_signal_ids)
        main_axis = self.plot_item.getAxis("left")
        main_axis.setVisible(main_visible)
        try:
            main_axis.setWidth(None if main_visible else 0)
        except Exception:
            pass

        for axis_index, axis in self._axis_items.items():
            signal_ids = self._axis_signals.get(axis_index, [])
            visible = any(bool(self._meta.get(sig_id, {}).get("enabled", True)) for sig_id in signal_ids)
            axis.setVisible(visible)
            try:
                axis.setWidth(None if visible else 0)
            except Exception:
                pass

    def _redraw_all(self) -> None:
        self._redraw_signal_ids(self._curves.keys())
        self._update_axis_visibility()

    def _sync_views(self) -> None:
        target_rect = self.plot_item.vb.sceneBoundingRect()
        for view_box in self._axis_views.values():
            view_box.setGeometry(target_rect)
            view_box.linkedViewChanged(self.plot_item.vb, view_box.XAxis)

    def set_visual_settings(
        self,
        background_color: str | None = None,
        grid_color: str | None = None,
        grid_alpha: float | None = None,
        grid_x: bool | None = None,
        grid_y: bool | None = None,
    ) -> None:
        if background_color is not None:
            self._background_color = str(background_color)
        if grid_color is not None:
            self._grid_color = str(grid_color)
        if grid_alpha is not None:
            self._grid_alpha = max(0.0, min(1.0, float(grid_alpha)))
        if grid_x is not None:
            self._grid_x = bool(grid_x)
        if grid_y is not None:
            self._grid_y = bool(grid_y)

        self.plot_widget.setBackground(self._background_color)
        self.plot_item.showGrid(x=self._grid_x, y=self._grid_y, alpha=self._grid_alpha)
        self._apply_grid_pen_color()

    def apply_print_style(self, tick_pt: float = 15.5, label_pt: float = 17.5) -> None:
        tick_size = max(7.0, float(tick_pt))
        label_size = max(tick_size, float(label_pt))

        tick_font = QFont()
        tick_font.setPointSizeF(tick_size)

        label_font = QFont(tick_font)
        label_font.setPointSizeF(label_size)
        label_font.setBold(True)

        axes: list[pg.AxisItem] = [
            self.plot_item.getAxis("left"),
            self.plot_item.getAxis("right"),
            self.plot_item.getAxis("bottom"),
            self.plot_item.getAxis("top"),
        ]
        axes.extend(self._axis_items.values())

        for axis in axes:
            if axis is None:
                continue
            orientation = str(getattr(axis, "orientation", "") or "").lower()
            is_bottom = orientation == "bottom"
            try:
                axis.setTickFont(tick_font)
            except Exception:
                pass
            try:
                axis.setStyle(
                    tickTextOffset=14 if is_bottom else 10,
                    autoExpandTextSpace=True,
                    autoReduceTextSpace=True,
                    hideOverlappingLabels=True,
                )
            except Exception:
                pass
            if is_bottom:
                # Reserve enough vertical space for large tick labels + axis title
                # on printed A4 output to avoid overlap ("Время" over time ticks).
                try:
                    axis.setHeight(int(max(86, tick_size * 4.5 + label_size * 1.4)))
                except Exception:
                    pass
                try:
                    axis.setTextPen(pg.mkPen("#1f1f1f"))
                    axis.setTickPen(pg.mkPen("#555555"))
                    axis.setPen(pg.mkPen("#555555"))
                except Exception:
                    pass
            try:
                axis.label.setFont(label_font)
            except Exception:
                pass

    def set_print_time_axis_mode(self, enabled: bool) -> None:
        axis = self.plot_item.getAxis("bottom")
        if isinstance(axis, WallClockAxisItem):
            axis.set_print_mode(bool(enabled))

    def set_line_width(self, width: float) -> None:
        line_w = max(0.8, float(width))
        for signal_id, curve in self._curves.items():
            color = str(self.get_signal_color(signal_id))
            curve.setPen(pg.mkPen(color, width=line_w))

    def set_curve_smoothing(self, enabled: bool, window: int = 5) -> None:
        smooth_enabled = bool(enabled)
        try:
            smooth_window = int(window)
        except (TypeError, ValueError):
            smooth_window = 5
        smooth_window = max(3, min(31, smooth_window))
        if smooth_window % 2 == 0:
            smooth_window = smooth_window + 1 if smooth_window < 31 else smooth_window - 1

        changed = (
            smooth_enabled != self._curve_smoothing_enabled
            or smooth_window != self._curve_smoothing_window
        )
        self._curve_smoothing_enabled = smooth_enabled
        self._curve_smoothing_window = smooth_window
        if changed:
            self._redraw_all()

    @staticmethod
    def _smooth_series_moving_average(
        values: list[float],
        window: int,
        *,
        causal: bool = False,
    ) -> list[float]:
        count = len(values)
        if count <= 2:
            return values
        win = max(3, int(window))
        if win % 2 == 0:
            win += 1

        prefix: list[float] = [0.0] * (count + 1)
        for idx, value in enumerate(values, start=1):
            prefix[idx] = prefix[idx - 1] + float(value)

        smoothed: list[float] = [0.0] * count
        if causal:
            for idx in range(count):
                right = idx + 1
                left = max(0, right - win)
                span = max(1, right - left)
                smoothed[idx] = (prefix[right] - prefix[left]) / float(span)
            return smoothed

        half = win // 2
        for idx in range(count):
            left = max(0, idx - half)
            right = min(count, idx + half + 1)
            span = max(1, right - left)
            smoothed[idx] = (prefix[right] - prefix[left]) / float(span)
        return smoothed

    def _apply_grid_pen_color(self) -> None:
        grid_pen = pg.mkPen(self._grid_color)
        left_axis = self.plot_item.getAxis("left")
        right_axis = self.plot_item.getAxis("right")
        bottom_axis = self.plot_item.getAxis("bottom")
        top_axis = self.plot_item.getAxis("top")
        try:
            left_axis.setTickPen(grid_pen)
            left_axis.setPen(grid_pen)
        except Exception:
            pass
        try:
            right_axis.setTickPen(grid_pen)
            right_axis.setPen(grid_pen)
        except Exception:
            pass
        try:
            bottom_axis.setTickPen(grid_pen)
            bottom_axis.setPen(grid_pen)
        except Exception:
            pass
        try:
            top_axis.setTickPen(grid_pen)
            top_axis.setPen(grid_pen)
        except Exception:
            pass
        for axis in self._axis_items.values():
            try:
                axis.setTickPen(grid_pen)
            except Exception:
                pass

    def _curve_data_for_view(
        self,
        xs: list[float],
        ys: list[float],
        x_min: float,
        x_max: float,
    ) -> tuple[list[float], list[float]]:
        if not xs:
            return xs, ys
        render_points = self._render_points_for_view(x_min, x_max)
        if len(xs) <= render_points:
            return xs, ys
        if x_max <= x_min:
            return self._decimate_series(xs, ys, render_points)

        left_idx, right_idx = self._visible_slice_bounds(xs, x_min, x_max)

        if right_idx - left_idx <= 0:
            return [], []

        sub_xs = xs[left_idx:right_idx]
        sub_ys = ys[left_idx:right_idx]
        if len(sub_xs) <= render_points:
            return sub_xs, sub_ys
        return self._decimate_series(sub_xs, sub_ys, render_points)

    def _enabled_signal_count(self) -> int:
        enabled = 0
        for signal_id in self._signal_order:
            meta = self._meta.get(signal_id, {})
            if bool(meta.get("enabled", True)):
                enabled += 1
        return max(1, enabled)

    def _render_points_for_view(self, x_min: float, x_max: float) -> int:
        try:
            width_px = int(self.plot_widget.width())
        except Exception:
            width_px = 1200
        width_px = max(320, width_px)

        span = max(0.1, float(x_max) - float(x_min))
        if span <= 5.0 * 60.0:
            density = 8.0
        elif span <= 30.0 * 60.0:
            density = 4.5
        elif span <= 2.0 * 3600.0:
            density = 3.0
        else:
            density = 1.8

        target = int(width_px * density)
        target = max(self._max_render_points, target)
        target = min(target, int(self._max_detail_render_points))

        enabled = self._enabled_signal_count()
        per_signal_cap = max(self._min_render_points, int(self._max_total_render_points / max(1, enabled)))
        target = min(target, per_signal_cap)
        # Cap at 2x screen pixels — forces averaging on dense data,
        # making live-follow and history look consistent at equal zoom.
        screen_cap = max(self._min_render_points, width_px * 2)
        target = min(target, screen_cap)
        return max(self._min_render_points, int(target))

    def _visible_slice_bounds(self, xs: list[float], x_min: float, x_max: float) -> tuple[int, int]:
        if not xs:
            return 0, 0
        if x_max <= x_min:
            return 0, len(xs)

        span = float(x_max) - float(x_min)
        margin = max(0.1, span * self._render_margin_ratio)
        left_bound = float(x_min) - margin
        right_bound = float(x_max) + margin
        left_idx = max(0, bisect.bisect_left(xs, left_bound) - 1)
        right_idx = min(len(xs), bisect.bisect_right(xs, right_bound) + 1)
        return left_idx, right_idx

    @staticmethod
    def _decimate_series(
        xs: list[float],
        ys: list[float],
        max_points: int,
    ) -> tuple[list[float], list[float]]:
        """Average-bucket decimation.
        Keeps visual shape identical at any zoom level.
        No rectangular spikes from min/max selection.
        """
        count = len(xs)
        if count <= max_points or max_points <= 0:
            return xs, ys
        if max_points < 2:
            return [xs[0], xs[-1]], [ys[0], ys[-1]]
        buckets = max(1, max_points)
        bucket_size = float(count) / float(buckets)
        out_xs: list[float] = []
        out_ys: list[float] = []
        for b in range(buckets):
            start = int(b * bucket_size)
            end = min(count, int((b + 1) * bucket_size))
            if end <= start:
                continue
            mid = start + (end - start) // 2
            avg_y = sum(ys[start:end]) / (end - start)
            out_xs.append(xs[mid])
            out_ys.append(avg_y)
        if out_xs and out_xs[-1] != xs[-1]:
            out_xs.append(xs[-1])
            out_ys.append(ys[-1])
        return out_xs, out_ys

    def _on_mouse_clicked(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if not self.plot_item.sceneBoundingRect().contains(event.scenePos()):
            return

        if not self._cursor_enabled:
            self._cursor_enabled = True
            self.cursor_enabled_changed.emit(True)

        mouse_point = self.plot_item.vb.mapSceneToView(event.scenePos())
        self._set_cursor_pos(float(mouse_point.x()))
        self._update_cursor_ratio_from_x(float(mouse_point.x()))
        self.cursor_line.show()
        self._update_cursor_values()
        self._emit_display_rows()

    def _set_cursor_pos(self, x_value: float) -> None:
        self._setting_cursor_pos = True
        self.cursor_line.setPos(x_value)
        self._setting_cursor_pos = False

    def _update_cursor_ratio_from_x(self, x_value: float) -> None:
        x_min, x_max = self.plot_item.vb.viewRange()[0]
        span = x_max - x_min
        if span <= 0:
            self._cursor_ratio = 0.5
            return
        ratio = (x_value - x_min) / span
        self._cursor_ratio = max(0.0, min(1.0, ratio))

    def _place_cursor_by_ratio(self) -> None:
        if self._cursor_ratio is None:
            return
        x_min, x_max = self.plot_item.vb.viewRange()[0]
        x_value = x_min + (x_max - x_min) * self._cursor_ratio
        self._set_cursor_pos(x_value)

    def _nearest_value(self, signal_id: str, x_pos: float) -> float | None:
        xs, ys = self._buffers.get(signal_id, ([], []))
        if not xs:
            return None

        index = bisect.bisect_left(xs, x_pos)
        if index <= 0:
            return ys[0]
        if index >= len(xs):
            return ys[-1]

        left_delta = abs(xs[index - 1] - x_pos)
        right_delta = abs(xs[index] - x_pos)
        return ys[index - 1] if left_delta <= right_delta else ys[index]

    def _nearest_point(self, signal_id: str, x_pos: float) -> tuple[float, float] | None:
        xs, ys = self._buffers.get(signal_id, ([], []))
        if not xs:
            return None

        index = bisect.bisect_left(xs, x_pos)
        if index <= 0:
            return xs[0], ys[0]
        if index >= len(xs):
            return xs[-1], ys[-1]

        left_delta = abs(xs[index - 1] - x_pos)
        right_delta = abs(xs[index] - x_pos)
        if left_delta <= right_delta:
            return xs[index - 1], ys[index - 1]
        return xs[index], ys[index]

    def _latest_point(self, signal_id: str) -> tuple[float, float] | None:
        xs, ys = self._buffers.get(signal_id, ([], []))
        if not xs:
            return None
        return xs[-1], ys[-1]

    def _update_cursor_values(self) -> None:
        if not self.cursor_line.isVisible():
            return

        x_pos = float(self.cursor_line.value())
        if not self._setting_cursor_pos:
            self._update_cursor_ratio_from_x(x_pos)
        time_text = format_ts_ms(x_pos)
        chunks = [f"Курсор {time_text}"]

        for signal_id, meta in self._meta.items():
            if not meta.get("enabled", True):
                continue
            value = self._nearest_value(signal_id, x_pos)
            if value is None:
                continue
            unit = meta.get("unit") or ""
            chunks.append(f"{meta['name']}: {value:.3f} {unit}".strip())

        self._emit_display_rows()

    def _on_x_range_changed(self, _view_box, _new_range) -> None:
        x_min, x_max = self.current_x_range()
        if not self._programmatic_x_change:
            needs_redraw = False
            for signal_id, (xs, _ys) in self._buffers.items():
                meta = self._meta.get(signal_id)
                if isinstance(meta, dict) and not bool(meta.get("enabled", True)):
                    continue
                # Redraw on X-range changes whenever the series is "long enough"
                # to be potentially decimated/subsliced.
                if len(xs) > self._max_render_points:
                    needs_redraw = True
                    break
            if needs_redraw:
                self._redraw_all()
            if self.cursor_line.isVisible() and self._auto_x:
                self._place_cursor_by_ratio()
                self._update_cursor_values()
        try:
            self.x_range_changed.emit(float(x_min), float(x_max))
        except Exception:
            pass

    def _on_main_range_changed_manually(self, mask) -> None:
        if self._applying_auto_range:
            return
        if self._programmatic_x_change:
            return
        affects_x = bool(len(mask) > 0 and mask[0]) if mask is not None else True
        if affects_x and (not self._auto_x) and self._soft_follow_latest_x:
            self._soft_follow_latest_x = False
            self._last_follow_x_max = None
        # Keep Auto X enabled for wheel zoom; disable for manual X pan/scroll.
        wheel_recent = (time.monotonic() - self._last_wheel_ts) <= 0.25
        if affects_x and self._auto_x and not wheel_recent:
            self._auto_x = False
            self._soft_follow_latest_x = False
            self._last_follow_x_max = None
            self.auto_mode_changed.emit(self._auto_x, self._auto_y)

    def _on_main_y_range_changed(self, _view_box, new_range) -> None:
        if not self._applying_auto_range:
            state = self.plot_item.vb.state.get("autoRange", [False, False])
            self._axis_auto_y[self._main_axis_index] = bool(state[1])
            self._auto_y = bool(state[1])
            self.auto_mode_changed.emit(self._auto_x, self._auto_y)
        self._emit_scales_changed()

    def _on_secondary_view_state_changed(self, axis_index: int) -> None:
        if self._applying_auto_range:
            return
        view_box = self._axis_views.get(axis_index)
        if view_box is None:
            return
        state = view_box.state.get("autoRange", [False, False])
        auto_y = bool(state[1])
        if self._axis_auto_y.get(axis_index, True) != auto_y:
            self._axis_auto_y[axis_index] = auto_y
            self._emit_scales_changed()

    def _on_secondary_range_changed_manually(self, axis_index: int, mask) -> None:
        if self._applying_auto_range:
            return
        affects_y = bool(len(mask) > 1 and mask[1]) if mask is not None else True
        if not affects_y:
            return
        if self._axis_auto_y.get(axis_index, True):
            self._axis_auto_y[axis_index] = False
            view_box = self._axis_views.get(axis_index)
            if view_box is not None:
                view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
        self._emit_scales_changed()

    def _on_secondary_y_range_changed(self, axis_index: int) -> None:
        if self._applying_auto_range:
            return
        if axis_index in self._axis_views:
            self._emit_scales_changed()

    def _emit_display_rows(self) -> None:
        rows: list[dict] = []
        cursor_active = self._cursor_enabled and self.cursor_line.isVisible()
        cursor_x = float(self.cursor_line.value()) if cursor_active else None
        mode = "Курсор" if cursor_active else "Текущее"
        seen_signal_ids: set[str] = set()

        for signal_id in self._signal_order:
            if signal_id in seen_signal_ids:
                continue
            seen_signal_ids.add(signal_id)
            meta = self._meta.get(signal_id)
            if not meta:
                continue
            if not bool(meta.get("enabled", True)):
                continue

            point = (
                self._nearest_point(signal_id, cursor_x)
                if cursor_active and cursor_x is not None
                else self._latest_point(signal_id)
            )
            if point is None:
                value = None
                ts = None
            else:
                ts, value = point

            rows.append(
                {
                    "signal_id": signal_id,
                    "name": meta.get("name", signal_id),
                    "color": meta.get("color", "#1f77b4"),
                    "unit": meta.get("unit", ""),
                    "enabled": bool(meta.get("enabled", True)),
                    "axis_index": int(meta.get("axis_index", 1)),
                    "value": value,
                    "ts": ts,
                    "mode": mode,
                }
            )

        self.display_updated.emit(rows, cursor_active)

    def _emit_scales_changed(self) -> None:
        rows: list[dict] = []
        for axis_index in sorted(self._axis_signals.keys()):
            signal_ids = self._axis_signals.get(axis_index, [])
            signal_names = [self._meta[sig_id]["name"] for sig_id in signal_ids if sig_id in self._meta]
            y_min, y_max = self._axis_y_range(axis_index)

            rows.append(
                {
                    "axis_index": axis_index,
                    "auto_x": bool(self._auto_x),
                    "auto_y": bool(self._axis_auto_y.get(axis_index, axis_index == self._main_axis_index and self._auto_y)),
                    "y_min": y_min,
                    "y_max": y_max,
                    "signal_names": signal_names,
                    "signal_count": len(signal_ids),
                }
            )

        self.scales_changed.emit(rows)