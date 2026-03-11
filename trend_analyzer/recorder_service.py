from __future__ import annotations

import copy
import os
from datetime import datetime
import logging
import math
from pathlib import Path
import signal
import threading
import time
from typing import Any

from pymodbus.client import ModbusTcpClient

from .logging_utils import setup_logging
from .instance_lock import SingleInstanceLock
from .models import ProfileConfig
from .modbus_worker import ModbusWorker
from .recorder_api import RecorderApiServer, api_modbus_read, api_modbus_read_many, api_modbus_write
from .recorder_shared import (
    RECORDER_CONFIG_FORMAT,
    RECORDER_STATUS_FORMAT,
    clear_recorder_pid,
    consume_stop_request,
    is_recorder_pid_running,
    read_recorder_config,
    read_recorder_pid,
    write_recorder_config,
    write_recorder_pid,
    write_recorder_status,
)
from .storage import ArchiveStore, ConfigStore, DEFAULT_DB_PATH

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


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
        self._runtime_connected = False
        self._api_error = ""
        self._profile_lock = threading.RLock()
        self._runtime_profile = ProfileConfig()
        self._api_server = RecorderApiServer(self)
        self._archive_begin_ts: float | None = None
        self._cpu_percent = 0.0
        self._ram_bytes = 0
        self._db_size_bytes = 0
        self._last_metrics_refresh_mono = 0.0
        self._process = None
        if psutil is not None:
            try:
                self._process = psutil.Process(os.getpid())
                self._process.cpu_percent(interval=None)
            except Exception:
                self._process = None
        self._instance_lock = SingleInstanceLock("trend_recorder_service")

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        setup_logging()
        profile = self._load_profile()
        self._set_runtime_profile(profile)
        pid = self._ensure_single_instance()
        if pid is None:
            return

        write_recorder_pid(pid)
        self._install_signal_handlers()
        self._start_api_server_if_enabled(profile)
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
            try:
                self._api_server.stop()
            except Exception:
                pass
            clear_recorder_pid()
            if self._stop_requested:
                self._write_status(profile, state="stopped", connected=False, message="остановлен")
            try:
                self._instance_lock.release()
            except Exception:
                pass

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
        if not self._instance_lock.acquire():
            LOGGER.warning("Recorder already running (instance lock)")
            return None

        pid = os.getpid()
        existing = read_recorder_pid()
        if existing and existing != pid and is_recorder_pid_running(existing):
            LOGGER.warning("Recorder already running with pid=%s", existing)
            try:
                self._instance_lock.release()
            except Exception:
                pass
            return None
        return pid

    def _set_runtime_profile(self, profile: ProfileConfig) -> None:
        with self._profile_lock:
            self._runtime_profile = ProfileConfig.from_dict(profile.to_dict())

    def get_runtime_profile(self) -> ProfileConfig:
        with self._profile_lock:
            return ProfileConfig.from_dict(self._runtime_profile.to_dict())

    def get_api_token(self) -> str:
        with self._profile_lock:
            return str(self._runtime_profile.recorder_api_token or "")

    def build_health_payload(self, api_host: str, api_port: int) -> dict[str, Any]:
        profile = self.get_runtime_profile()
        return {
            "ok": True,
            "ts": time.time(),
            "state": "running" if not self._stop_requested else "stopping",
            "connected": bool(self._runtime_connected),
            "pid": int(os.getpid()),
            "recorder_id": str(profile.id),
            "profile_id": str(profile.id),
            "profile_name": str(profile.name),
            "api_host": str(api_host),
            "api_port": int(api_port),
            "started_at": str(self._started_at),
            "rows_written_total": int(self._rows_written_total),
            "samples_read_total": int(self._samples_read_total),
            "cycles_total": int(self._cycles_total),
            "errors_total": int(self._errors_total),
            "last_error": str(self._last_error),
            "api_error": str(self._api_error),
            "last_sample_ts": None if self._last_sample_ts is None else float(self._last_sample_ts),
            "last_archive_ts": None if self._last_archive_ts is None else float(self._last_archive_ts),
            "archive_begin_ts": None if self._archive_begin_ts is None else float(self._archive_begin_ts),
            "cpu_percent": float(self._cpu_percent),
            "ram_bytes": int(self._ram_bytes),
            "db_size_bytes": int(self._db_size_bytes),
        }

    def _build_recorder_config_payload(self, profile: ProfileConfig) -> dict[str, Any]:
        return {
            "format": RECORDER_CONFIG_FORMAT,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "active_profile_id": profile.id,
            "profile": copy.deepcopy(profile.to_dict()),
            "source": "recorder_api",
        }

    def apply_runtime_profile(self, raw_profile: dict[str, Any]) -> tuple[bool, str]:
        try:
            profile = ProfileConfig.from_dict(raw_profile)
        except Exception as exc:
            return False, f"invalid_profile: {exc}"
        self._set_runtime_profile(profile)
        try:
            write_recorder_config(self._build_recorder_config_payload(profile))
        except Exception as exc:
            return False, f"save_failed: {exc}"
        self._restart_api_server(profile)
        return True, "applied"

    def api_modbus_read(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        return api_modbus_read(self.get_runtime_profile(), payload)

    def api_modbus_read_many(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        return api_modbus_read_many(self.get_runtime_profile(), payload)

    def api_modbus_write(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        return api_modbus_write(self.get_runtime_profile(), payload)

    def _start_api_server_if_enabled(self, profile: ProfileConfig) -> None:
        if not bool(profile.recorder_api_enabled):
            return
        ok, error = self._api_server.start(profile.recorder_api_host, profile.recorder_api_port)
        if not ok:
            self._api_error = str(error)
            LOGGER.error("Recorder API start failed: %s", error)
        else:
            self._api_error = ""
            LOGGER.info(
                "Recorder API started at %s:%s",
                self._api_server.bind_host,
                self._api_server.port,
            )

    def _restart_api_server(self, profile: ProfileConfig) -> None:
        try:
            self._api_server.stop()
        except Exception:
            pass
        self._start_api_server_if_enabled(profile)

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
        runtime_profile = self.get_runtime_profile()
        db_path = runtime_profile.db_path or str(DEFAULT_DB_PATH)
        store = ArchiveStore(db_path)
        self._archive_begin_ts = store.min_sample_ts(runtime_profile.id)
        self._db_size_bytes = self._estimate_db_size_bytes(db_path)
        client = ModbusTcpClient(host=runtime_profile.ip, port=runtime_profile.port, timeout=runtime_profile.timeout_s)
        conn_signature = (
            str(runtime_profile.ip),
            int(runtime_profile.port),
            float(runtime_profile.timeout_s),
            int(runtime_profile.unit_id),
            int(runtime_profile.address_offset),
        )
        active_profile_id = str(runtime_profile.id)

        reconnect_delay_s = 0.5
        reconnect_delay_max_s = 15.0
        is_connected = False
        last_connection_state: bool | None = None
        last_status_publish = 0.0
        last_retention_cleanup_ts = 0.0
        archive_last_values: dict[str, float] = {}
        archive_last_written_ts: dict[str, float] = {}

        try:
            while not self._stop_requested:
                if consume_stop_request():
                    self._stop_requested = True
                    break

                profile = self.get_runtime_profile()
                profile_conn_signature = (
                    str(profile.ip),
                    int(profile.port),
                    float(profile.timeout_s),
                    int(profile.unit_id),
                    int(profile.address_offset),
                )
                profile_id = str(profile.id)
                if profile_id != active_profile_id:
                    active_profile_id = profile_id
                    self._archive_begin_ts = store.min_sample_ts(profile.id)
                if profile_conn_signature != conn_signature:
                    try:
                        client.close()
                    except Exception:
                        pass
                    client = ModbusTcpClient(host=profile.ip, port=profile.port, timeout=profile.timeout_s)
                    conn_signature = profile_conn_signature
                    is_connected = False
                    self._runtime_connected = False
                    reconnect_delay_s = 0.5
                    archive_last_values = {}
                    archive_last_written_ts = {}

                profile_db_path = profile.db_path or str(DEFAULT_DB_PATH)
                if str(profile_db_path) != str(db_path):
                    try:
                        store.close()
                    except Exception:
                        pass
                    db_path = profile_db_path
                    store = ArchiveStore(db_path)
                    last_connection_state = None
                    self._archive_begin_ts = store.min_sample_ts(profile.id)
                    self._db_size_bytes = self._estimate_db_size_bytes(db_path)

                started = time.monotonic()
                self._cycles_total += 1
                read_attempts = max(1, int(profile.retries) + 1)
                local_signals = self._local_signals(profile)
                signal_types_by_id = {str(sig.id): str(sig.data_type or "int16") for sig in local_signals if str(sig.id)}
                self._refresh_runtime_metrics(profile, store)

                # Local recorder must poll/archive only local signals.
                # Remote source signals are collected by their own recorder APIs.
                if not local_signals:
                    if is_connected:
                        try:
                            client.close()
                        except Exception:
                            pass
                        is_connected = False
                        self._runtime_connected = False
                        self._write_connection_state_event(store, profile, False, last_connection_state)
                        last_connection_state = False
                    self._runtime_connected = False
                    self._publish_status_if_due(profile, False, "локальные сигналы не настроены", last_status_publish)
                    last_status_publish = time.monotonic()
                    self._sleep_interruptible(max(0.2, float(profile.poll_interval_ms) / 1000.0))
                    continue

                if not client.connected:
                    try:
                        connected_now = bool(client.connect())
                    except Exception as exc:
                        connected_now = False
                        self._register_error(f"Ошибка подключения: {exc}")

                    if connected_now != is_connected:
                        is_connected = connected_now
                        self._runtime_connected = bool(is_connected)
                        self._write_connection_state_event(store, profile, is_connected, last_connection_state)
                        last_connection_state = is_connected
                    if not connected_now:
                        self._publish_status_if_due(profile, is_connected, "ожидание подключения", last_status_publish)
                        last_status_publish = time.monotonic()
                        self._sleep_interruptible(reconnect_delay_s)
                        reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)
                        continue
                    reconnect_delay_s = 0.5

                specs = ModbusWorker._build_read_specs(
                    local_signals,
                    address_offset=profile.address_offset,
                    default_scale=1.0,
                )
                samples, read_errors, comm_error = ModbusWorker._read_specs_grouped(
                    client,
                    specs,
                    profile.unit_id,
                    read_attempts=read_attempts,
                )
                for spec, read_exc in read_errors:
                    address = int(spec.get("address", 0))
                    data_type = str(spec.get("data_type", "int16"))
                    bit_index = int(spec.get("bit_index", 0))
                    addr_text = f"{address}.{bit_index}" if data_type == "bool" else str(address)
                    self._register_error(
                        f"{spec.get('name', '?')} addr={addr_text} {data_type}/{spec.get('float_order', 'ABCD')}: {read_exc}"
                    )

                if comm_error is not None:
                    spec, exc = comm_error
                    address = int(spec.get("address", 0))
                    data_type = str(spec.get("data_type", "int16"))
                    bit_index = int(spec.get("bit_index", 0))
                    addr_text = f"{address}.{bit_index}" if data_type == "bool" else str(address)
                    self._register_error(
                        f"{spec.get('name', '?')} addr={addr_text} {data_type}/{spec.get('float_order', 'ABCD')}: {exc}"
                    )
                    try:
                        client.close()
                    except Exception:
                        pass
                    if is_connected:
                        is_connected = False
                        self._runtime_connected = False
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
                            if self._archive_begin_ts is None:
                                self._archive_begin_ts = float(ts)
                        self._last_archive_ts = ts

                        retention_days = max(0, int(profile.archive_retention_days))
                        if retention_days > 0 and (ts - last_retention_cleanup_ts) >= 60.0:
                            cutoff_ts = float(ts) - float(retention_days) * 86400.0
                            store.prune_older_than(profile.id, cutoff_ts)
                            last_retention_cleanup_ts = ts

                self._runtime_connected = bool(is_connected)
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
                    self._write_connection_state_event(store, self.get_runtime_profile(), False, last_connection_state)
            except Exception:
                pass
            self._runtime_connected = False
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

    @staticmethod
    def _estimate_db_size_bytes(db_path: str) -> int:
        total = 0
        base = Path(str(db_path or DEFAULT_DB_PATH))
        for suffix in ("", "-wal", "-shm"):
            part = Path(str(base) + suffix)
            try:
                if part.exists():
                    total += int(part.stat().st_size)
            except Exception:
                continue
        return max(0, int(total))

    def _refresh_runtime_metrics(self, profile: ProfileConfig, store: ArchiveStore) -> None:
        now = time.monotonic()
        if (now - self._last_metrics_refresh_mono) < 0.8:
            return
        self._last_metrics_refresh_mono = now
        if self._process is not None:
            try:
                self._cpu_percent = float(self._process.cpu_percent(interval=None))
            except Exception:
                pass
            try:
                self._ram_bytes = int(self._process.memory_info().rss)
            except Exception:
                pass
        self._db_size_bytes = self._estimate_db_size_bytes(profile.db_path or str(DEFAULT_DB_PATH))
        if self._archive_begin_ts is None:
            try:
                self._archive_begin_ts = store.min_sample_ts(profile.id)
            except Exception:
                self._archive_begin_ts = None

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
            "archive_begin_ts": None if self._archive_begin_ts is None else float(self._archive_begin_ts),
            "cpu_percent": float(self._cpu_percent),
            "ram_bytes": int(self._ram_bytes),
            "db_size_bytes": int(self._db_size_bytes),
        }
        try:
            write_recorder_status(payload)
        except Exception as exc:
            # Status file is auxiliary; recorder should keep running even if
            # another process temporarily locks recorder_status.json.
            LOGGER.warning("Failed to write recorder status: %s", exc)

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

    @staticmethod
    def _local_signals(profile: ProfileConfig) -> list[Any]:
        return [
            signal
            for signal in list(getattr(profile, "signals", []) or [])
            if str(getattr(signal, "source_id", "local") or "local") == "local"
        ]


def run_recorder_service() -> None:
    service = RecorderService()
    service.run()
