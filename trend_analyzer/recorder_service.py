from __future__ import annotations

import os
from datetime import datetime
import logging
import math
import signal
import time
from typing import Any

from pymodbus.client import ModbusTcpClient

from .logging_utils import setup_logging
from .models import ProfileConfig
from .modbus_worker import ModbusWorker
from .recorder_shared import (
    RECORDER_CONFIG_FORMAT,
    RECORDER_STATUS_FORMAT,
    clear_recorder_pid,
    consume_stop_request,
    is_pid_running,
    read_recorder_config,
    read_recorder_pid,
    write_recorder_pid,
    write_recorder_status,
)
from .storage import ArchiveStore, ConfigStore, DEFAULT_DB_PATH


LOGGER = logging.getLogger("trend_analyzer.recorder")


class RecorderService:
    def __init__(self) -> None:
        self._stop_requested = False
        self._started_at = datetime.now().isoformat(timespec="seconds")
        self._last_sample_ts: float | None = None
        self._last_archive_ts: float | None = None
        self._rows_written_total = 0
        self._samples_read_total = 0
        self._cycles_total = 0
        self._errors_total = 0
        self._last_error = ""

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        setup_logging()
        profile = self._load_profile()
        pid = self._ensure_single_instance()
        if pid is None:
            return

        write_recorder_pid(pid)
        self._install_signal_handlers()
        # Drop stale stop command from previous session, if any.
        consume_stop_request()
        self._write_status(profile, state="starting", connected=False, message="инициализация")

        try:
            self._run_loop(profile)
        except Exception as exc:
            self._errors_total += 1
            self._last_error = str(exc)
            LOGGER.exception("Recorder crashed")
            self._write_status(profile, state="error", connected=False, message=f"критическая ошибка: {exc}")
        finally:
            clear_recorder_pid()
            if self._stop_requested:
                self._write_status(profile, state="stopped", connected=False, message="остановлен")

    def _load_profile(self) -> ProfileConfig:
        payload = read_recorder_config()
        if isinstance(payload, dict) and str(payload.get("format") or "") == RECORDER_CONFIG_FORMAT:
            raw_profile = payload.get("profile")
            if isinstance(raw_profile, dict):
                try:
                    profile = ProfileConfig.from_dict(raw_profile)
                    LOGGER.info("Loaded recorder profile from recorder_config.json: %s", profile.name)
                    return profile
                except Exception:
                    LOGGER.exception("Failed to parse recorder profile config")

        config = ConfigStore().load()
        active = config.active_profile_id
        for profile in config.profiles:
            if profile.id == active:
                LOGGER.info("Loaded recorder profile from app config: %s", profile.name)
                return profile
        return config.profiles[0]

    def _ensure_single_instance(self) -> int | None:
        pid = os.getpid()
        existing = read_recorder_pid()
        if existing and existing != pid and is_pid_running(existing):
            LOGGER.warning("Recorder already running with pid=%s", existing)
            return None
        return pid

    def _install_signal_handlers(self) -> None:
        def _handler(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
            self.request_stop()

        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass

    def _run_loop(self, profile: ProfileConfig) -> None:
        db_path = profile.db_path or str(DEFAULT_DB_PATH)
        store = ArchiveStore(db_path)
        client = ModbusTcpClient(host=profile.ip, port=profile.port, timeout=profile.timeout_s)

        reconnect_delay_s = 0.5
        reconnect_delay_max_s = 15.0
        read_attempts = max(1, int(profile.retries) + 1)
        is_connected = False
        last_connection_state: bool | None = None
        last_status_publish = 0.0
        last_retention_cleanup_ts = 0.0
        archive_last_values: dict[str, float] = {}
        archive_last_written_ts: dict[str, float] = {}
        signal_types_by_id = {str(sig.id): str(sig.data_type or "int16") for sig in profile.signals if str(sig.id)}

        try:
            while not self._stop_requested:
                if consume_stop_request():
                    self._stop_requested = True
                    break

                started = time.monotonic()
                self._cycles_total += 1

                if not client.connected:
                    try:
                        connected_now = bool(client.connect())
                    except Exception as exc:
                        connected_now = False
                        self._register_error(f"Ошибка подключения: {exc}")

                    if connected_now != is_connected:
                        is_connected = connected_now
                        self._write_connection_state_event(store, profile, is_connected, last_connection_state)
                        last_connection_state = is_connected
                    if not connected_now:
                        self._publish_status_if_due(profile, is_connected, "ожидание подключения", last_status_publish)
                        last_status_publish = time.monotonic()
                        self._sleep_interruptible(reconnect_delay_s)
                        reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)
                        continue
                    reconnect_delay_s = 0.5

                samples: dict[str, tuple[str, float]] = {}
                comm_error: tuple[str, Exception] | None = None
                for signal_cfg in profile.signals:
                    value: float | None = None
                    read_exc: Exception | None = None
                    for attempt in range(read_attempts):
                        try:
                            value = ModbusWorker._read_signal(
                                client,
                                signal_cfg,
                                profile.unit_id,
                                profile.address_offset,
                            )
                            read_exc = None
                            break
                        except Exception as exc:
                            read_exc = exc
                            if not ModbusWorker._is_connection_error(exc):
                                break
                            if attempt + 1 < read_attempts:
                                time.sleep(min(0.05 * (attempt + 1), 0.25))

                    if read_exc is not None:
                        if ModbusWorker._is_connection_error(read_exc):
                            comm_error = (signal_cfg.name, read_exc)
                            break
                        self._register_error(
                            f"{signal_cfg.name} addr={signal_cfg.address} {signal_cfg.data_type}/{signal_cfg.float_order}: {read_exc}"
                        )
                        continue

                    samples[str(signal_cfg.id)] = (str(signal_cfg.name), float(value))

                if comm_error is not None:
                    self._register_error(f"{comm_error[0]}: {comm_error[1]}")
                    try:
                        client.close()
                    except Exception:
                        pass
                    if is_connected:
                        is_connected = False
                        self._write_connection_state_event(store, profile, False, last_connection_state)
                        last_connection_state = False
                    self._sleep_interruptible(reconnect_delay_s)
                    reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)
                    continue
                reconnect_delay_s = 0.5

                ts = time.time()
                if samples:
                    self._samples_read_total += len(samples)
                    self._last_sample_ts = ts
                    archive_interval_s = max(0.05, profile.archive_interval_ms / 1000.0)
                    if self._last_archive_ts is None or (ts - self._last_archive_ts) >= archive_interval_s:
                        rows = self._filter_archive_rows(
                            profile,
                            signal_types_by_id,
                            archive_last_values,
                            archive_last_written_ts,
                            ts,
                            samples,
                        )
                        if rows:
                            store.insert_batch(profile.id, ts, rows)
                            self._rows_written_total += len(rows)
                        self._last_archive_ts = ts

                        retention_days = max(0, int(profile.archive_retention_days))
                        if retention_days > 0 and (ts - last_retention_cleanup_ts) >= 60.0:
                            cutoff_ts = float(ts) - float(retention_days) * 86400.0
                            store.prune_older_than(profile.id, cutoff_ts)
                            last_retention_cleanup_ts = ts

                now_mono = time.monotonic()
                if (now_mono - last_status_publish) >= 1.0:
                    self._write_status(profile, state="running", connected=is_connected, message="запись")
                    last_status_publish = now_mono

                elapsed_ms = (time.monotonic() - started) * 1000.0
                sleep_ms = max(0, int(profile.poll_interval_ms) - int(elapsed_ms))
                if sleep_ms > 0:
                    self._sleep_interruptible(sleep_ms / 1000.0)
        finally:
            try:
                if is_connected:
                    self._write_connection_state_event(store, profile, False, last_connection_state)
            except Exception:
                pass
            try:
                store.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    def _sleep_interruptible(self, duration_s: float) -> None:
        deadline = time.monotonic() + max(0.0, float(duration_s))
        while not self._stop_requested and time.monotonic() < deadline:
            if consume_stop_request():
                self._stop_requested = True
                break
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _register_error(self, message: str) -> None:
        self._errors_total += 1
        self._last_error = str(message)
        LOGGER.warning(message)

    def _publish_status_if_due(
        self,
        profile: ProfileConfig,
        connected: bool,
        message: str,
        last_status_publish: float,
    ) -> None:
        now_mono = time.monotonic()
        if (now_mono - last_status_publish) >= 1.0:
            self._write_status(profile, state="running", connected=connected, message=message)

    def _write_connection_state_event(
        self,
        store: ArchiveStore,
        profile: ProfileConfig,
        connected: bool,
        last_connection_state: bool | None,
    ) -> None:
        state = bool(connected)
        if last_connection_state is None or last_connection_state != state:
            ts = time.time()
            store.insert_connection_event(profile.id, ts, state)

    def _write_status(self, profile: ProfileConfig, state: str, connected: bool, message: str) -> None:
        payload: dict[str, Any] = {
            "format": RECORDER_STATUS_FORMAT,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": self._started_at,
            "state": state,
            "message": str(message),
            "pid": int(os.getpid()),
            "profile_id": profile.id,
            "profile_name": profile.name,
            "db_path": profile.db_path or str(DEFAULT_DB_PATH),
            "connected": bool(connected),
            "signals_configured": int(len(profile.signals)),
            "cycles_total": int(self._cycles_total),
            "samples_read_total": int(self._samples_read_total),
            "rows_written_total": int(self._rows_written_total),
            "errors_total": int(self._errors_total),
            "last_error": str(self._last_error),
            "last_sample_ts": None if self._last_sample_ts is None else float(self._last_sample_ts),
            "last_archive_ts": None if self._last_archive_ts is None else float(self._last_archive_ts),
        }
        write_recorder_status(payload)

    @staticmethod
    def _should_archive_signal_sample(
        profile: ProfileConfig,
        signal_types_by_id: dict[str, str],
        archive_last_values: dict[str, float],
        archive_last_written_ts: dict[str, float],
        signal_id: str,
        ts: float,
        value: float,
    ) -> bool:
        last_value = archive_last_values.get(signal_id)
        last_ts = archive_last_written_ts.get(signal_id)
        if last_value is None or last_ts is None:
            return True

        keepalive_s = max(0.0, float(profile.archive_keepalive_s))
        if keepalive_s > 0.0 and (float(ts) - float(last_ts)) >= keepalive_s:
            return True

        signal_type = str(signal_types_by_id.get(signal_id, "int16")).lower()
        if signal_type == "bool":
            return int(round(float(value))) != int(round(float(last_value)))

        deadband = max(0.0, float(profile.archive_deadband))
        delta = abs(float(value) - float(last_value))
        if math.isnan(delta):
            return True
        return delta > deadband

    def _filter_archive_rows(
        self,
        profile: ProfileConfig,
        signal_types_by_id: dict[str, str],
        archive_last_values: dict[str, float],
        archive_last_written_ts: dict[str, float],
        ts: float,
        samples: dict[str, tuple[str, float]],
    ) -> list[tuple[str, str, float]]:
        rows: list[tuple[str, str, float]] = []
        only_changes = bool(profile.archive_on_change_only)
        for signal_id, (signal_name, value) in samples.items():
            sid = str(signal_id)
            val = float(value)
            if only_changes and not self._should_archive_signal_sample(
                profile,
                signal_types_by_id,
                archive_last_values,
                archive_last_written_ts,
                sid,
                ts,
                val,
            ):
                continue
            rows.append((sid, str(signal_name), val))
            archive_last_values[sid] = val
            archive_last_written_ts[sid] = float(ts)
        return rows


def run_recorder_service() -> None:
    service = RecorderService()
    service.run()
