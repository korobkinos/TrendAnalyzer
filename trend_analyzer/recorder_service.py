from __future__ import annotations

from collections import deque
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
        self._live_lock = threading.RLock()
        self._live_sample_seq = 0
        self._live_event_seq = 0
        self._live_samples: deque[dict[str, Any]] = deque(maxlen=50000)
        self._live_connection_events: deque[dict[str, Any]] = deque(maxlen=8000)
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
        self._reset_live_buffers()
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

    def _reset_live_buffers(self) -> None:
        with self._live_lock:
            self._live_sample_seq = 0
            self._live_event_seq = 0
            self._live_samples.clear()
            self._live_connection_events.clear()

    def _record_live_samples(self, ts: float, samples: dict[str, tuple[str, float]]) -> None:
        ts_f = float(ts)
        with self._live_lock:
            for signal_id, (signal_name, value) in samples.items():
                self._live_sample_seq += 1
                self._live_samples.append(
                    {
                        "id": int(self._live_sample_seq),
                        "tag_id": str(signal_id),
                        "tag_name": str(signal_name),
                        "ts": ts_f,
                        "value": float(value),
                    }
                )

    def _record_live_connection_event(self, ts: float, connected: bool) -> None:
        with self._live_lock:
            self._live_event_seq += 1
            self._live_connection_events.append(
                {
                    "id": int(self._live_event_seq),
                    "ts": float(ts),
                    "is_connected": int(bool(connected)),
                }
            )

    def get_live_stream_payload(
        self,
        since_sample_id: int,
        since_event_id: int,
        sample_limit: int,
        event_limit: int,
        bootstrap: bool = False,
    ) -> dict[str, Any]:
        profile = self.get_runtime_profile()
        health = self.build_health_payload(self._api_server.bind_host, self._api_server.port)
        with self._live_lock:
            next_sample_id = int(self._live_sample_seq)
            next_event_id = int(self._live_event_seq)
            if bootstrap:
                samples_payload: list[dict[str, Any]] = []
                events_payload: list[dict[str, Any]] = []
            else:
                samples_payload = [
                    dict(item)
                    for item in self._live_samples
                    if int(item.get("id", 0)) > int(since_sample_id)
                ][: max(1, int(sample_limit))]
                events_payload = [
                    dict(item)
                    for item in self._live_connection_events
                    if int(item.get("id", 0)) > int(since_event_id)
                ][: max(1, int(event_limit))]
                if samples_payload:
                    next_sample_id = int(samples_payload[-1].get("id", next_sample_id))
                if events_payload:
                    next_event_id = int(events_payload[-1].get("id", next_event_id))
        return {
            "ok": True,
            "format": RECORDER_STATUS_FORMAT,
            "profile_id": profile.id,
            "connected": bool(health.get("connected", False)),
            "samples": samples_payload,
            "connection_events": events_payload,
            "next_sample_id": int(next_sample_id),
            "next_event_id": int(next_event_id),
            "server_ts": time.time(),
        }

    def get_live_history_payload(
        self,
        start_ts: float,
        end_ts: float,
        tag_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        profile = self.get_runtime_profile()
        tag_filter = {str(item).strip() for item in list(tag_ids or []) if str(item).strip()}
        samples_map: dict[str, list[list[float]]] = {}
        events_payload: list[list[float]] = []
        with self._live_lock:
            for item in self._live_samples:
                ts_f = float(item.get("ts", 0.0))
                if ts_f < float(start_ts) or ts_f > float(end_ts):
                    continue
                tag_id = str(item.get("tag_id", "")).strip()
                if not tag_id:
                    continue
                if tag_filter and tag_id not in tag_filter:
                    continue
                samples_map.setdefault(tag_id, []).append([ts_f, float(item.get("value", 0.0))])
            for item in self._live_connection_events:
                ts_f = float(item.get("ts", 0.0))
                if ts_f < float(start_ts) or ts_f > float(end_ts):
                    continue
                events_payload.append([ts_f, float(int(item.get("is_connected", 0) or 0))])
        return {
            "ok": True,
            "format": RECORDER_STATUS_FORMAT,
            "profile_id": profile.id,
            "from_ts": float(start_ts),
            "to_ts": float(end_ts),
            "samples": samples_map,
            "connection_events": events_payload,
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
        active_profile_id = str(runtime_profile.id)
        active_poll_signature: tuple[tuple[str, str, int, int, float, int, int], ...] = ()

        reconnect_delay_s = 0.5
        reconnect_delay_max_s = 15.0
        last_connection_state: bool | None = None
        last_status_publish = 0.0
        last_retention_cleanup_ts = 0.0
        last_retention_vacuum_ts = 0.0
        archive_last_values: dict[str, float] = {}
        archive_last_written_ts: dict[str, float] = {}
        clients: dict[str, ModbusTcpClient] = {}
        client_signatures: dict[str, tuple[str, int, float]] = {}
        source_connected: dict[str, bool] = {}

        try:
            while not self._stop_requested:
                if consume_stop_request():
                    self._stop_requested = True
                    break

                profile = self.get_runtime_profile()
                profile_id = str(profile.id)
                if profile_id != active_profile_id:
                    active_profile_id = profile_id
                    self._archive_begin_ts = store.min_sample_ts(profile.id)
                    self._reset_live_buffers()

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
                poll_groups = self._local_poll_groups(profile)
                poll_signature = tuple(
                    sorted(
                        (
                            str(source_id),
                            str(host),
                            int(port),
                            int(unit_id),
                            float(timeout_s),
                            int(address_offset),
                            int(retries),
                        )
                        for (source_id, host, port, unit_id, timeout_s, address_offset, retries) in poll_groups.keys()
                    )
                )
                if poll_signature != active_poll_signature:
                    active_poll_signature = poll_signature
                    reconnect_delay_s = 0.5
                    archive_last_values = {}
                    archive_last_written_ts = {}

                active_source_ids = {
                    str(source_id) for (source_id, _host, _port, _unit, _timeout, _offset, _retries) in poll_groups.keys()
                }
                for source_id in list(clients.keys()):
                    if source_id in active_source_ids:
                        continue
                    try:
                        clients[source_id].close()
                    except Exception:
                        pass
                    clients.pop(source_id, None)
                    client_signatures.pop(source_id, None)
                    source_connected.pop(source_id, None)

                polled_signals = [signal for signals in poll_groups.values() for signal in signals]
                signal_types_by_id = {
                    str(sig.id): str(sig.data_type or "int16")
                    for sig in polled_signals
                    if str(sig.id)
                }
                self._refresh_runtime_metrics(profile, store)

                if not polled_signals:
                    for source_id, source_client in list(clients.items()):
                        try:
                            source_client.close()
                        except Exception:
                            pass
                        clients.pop(source_id, None)
                    client_signatures = {}
                    source_connected = {}
                    self._runtime_connected = False
                    self._write_connection_state_event(store, profile, False, last_connection_state)
                    last_connection_state = False
                    self._publish_status_if_due(
                        profile,
                        False,
                        "local modbus signals are not configured",
                        last_status_publish,
                    )
                    last_status_publish = time.monotonic()
                    self._sleep_interruptible(max(0.2, float(profile.poll_interval_ms) / 1000.0))
                    continue

                samples: dict[str, tuple[str, float]] = {}
                any_connected = False
                for (source_id, host, port, unit_id, timeout_s, address_offset, retries), source_signals in poll_groups.items():
                    source_key = str(source_id or "local")
                    signature = (str(host), int(port), float(timeout_s))
                    source_client = clients.get(source_key)
                    if source_client is None or client_signatures.get(source_key) != signature:
                        if source_client is not None:
                            try:
                                source_client.close()
                            except Exception:
                                pass
                        source_client = ModbusTcpClient(host=str(host), port=int(port), timeout=float(timeout_s))
                        clients[source_key] = source_client
                        client_signatures[source_key] = signature
                        source_connected[source_key] = False

                    if not source_client.connected:
                        try:
                            source_connected[source_key] = bool(source_client.connect())
                        except Exception as exc:
                            source_connected[source_key] = False
                            self._register_error(f"Connect failed [{source_key}] {host}:{int(port)}: {exc}")
                    if not source_connected.get(source_key, False):
                        continue

                    specs = ModbusWorker._build_read_specs(
                        source_signals,
                        address_offset=int(address_offset),
                        default_scale=1.0,
                    )
                    group_samples, read_errors, comm_error = ModbusWorker._read_specs_grouped(
                        source_client,
                        specs,
                        int(unit_id),
                        read_attempts=max(1, int(retries) + 1),
                    )
                    endpoint = f"{host}:{int(port)}"
                    for spec, read_exc in read_errors:
                        address = int(spec.get("address", 0))
                        data_type = str(spec.get("data_type", "int16"))
                        bit_index = int(spec.get("bit_index", 0))
                        addr_text = f"{address}.{bit_index}" if data_type == "bool" else str(address)
                        self._register_error(
                            f"[{source_key}] {endpoint} {spec.get('name', '?')} addr={addr_text} "
                            f"{data_type}/{spec.get('float_order', 'ABCD')}: {read_exc}"
                        )

                    if comm_error is not None:
                        spec, exc = comm_error
                        address = int(spec.get("address", 0))
                        data_type = str(spec.get("data_type", "int16"))
                        bit_index = int(spec.get("bit_index", 0))
                        addr_text = f"{address}.{bit_index}" if data_type == "bool" else str(address)
                        self._register_error(
                            f"[{source_key}] {endpoint} {spec.get('name', '?')} addr={addr_text} "
                            f"{data_type}/{spec.get('float_order', 'ABCD')}: {exc}"
                        )
                        source_connected[source_key] = False
                        try:
                            source_client.close()
                        except Exception:
                            pass
                        continue

                    source_connected[source_key] = True
                    any_connected = True
                    if group_samples:
                        samples.update(group_samples)

                self._runtime_connected = bool(any_connected)
                self._write_connection_state_event(store, profile, any_connected, last_connection_state)
                last_connection_state = bool(any_connected)

                if not any_connected:
                    self._publish_status_if_due(
                        profile,
                        False,
                        "waiting for modbus connection",
                        last_status_publish,
                    )
                    last_status_publish = time.monotonic()
                    self._sleep_interruptible(reconnect_delay_s)
                    reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)
                    continue
                reconnect_delay_s = 0.5

                ts = time.time()
                if samples:
                    self._samples_read_total += len(samples)
                    self._last_sample_ts = ts
                    self._record_live_samples(ts, samples)
                    archive_interval_s = max(0.05, profile.archive_interval_ms / 1000.0)
                    if self._last_archive_ts is None or (ts - self._last_archive_ts) >= archive_interval_s:
                        if bool(getattr(profile, "archive_to_db", True)):
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
                        (
                            last_retention_cleanup_ts,
                            last_retention_vacuum_ts,
                        ) = self._apply_archive_retention_policy(
                            store=store,
                            profile=profile,
                            ts=ts,
                            last_retention_cleanup_ts=last_retention_cleanup_ts,
                            last_retention_vacuum_ts=last_retention_vacuum_ts,
                        )

                now_mono = time.monotonic()
                if (now_mono - last_status_publish) >= 1.0:
                    status_message = "writing" if bool(getattr(profile, "archive_to_db", True)) else "monitoring"
                    self._write_status(profile, state="running", connected=bool(any_connected), message=status_message)
                    last_status_publish = now_mono

                elapsed_ms = (time.monotonic() - started) * 1000.0
                sleep_ms = max(0, int(profile.poll_interval_ms) - int(elapsed_ms))
                if sleep_ms > 0:
                    self._sleep_interruptible(sleep_ms / 1000.0)
        finally:
            try:
                if bool(last_connection_state):
                    self._write_connection_state_event(store, self.get_runtime_profile(), False, last_connection_state)
            except Exception:
                pass
            self._runtime_connected = False
            try:
                store.close()
            except Exception:
                pass
            for source_client in list(clients.values()):
                try:
                    source_client.close()
                except Exception:
                    pass

    def _run_loop_legacy_single_source(self, profile: ProfileConfig) -> None:
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
        last_retention_vacuum_ts = 0.0
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
                    self._record_live_samples(ts, samples)
                    archive_interval_s = max(0.05, profile.archive_interval_ms / 1000.0)
                    if self._last_archive_ts is None or (ts - self._last_archive_ts) >= archive_interval_s:
                        if bool(getattr(profile, "archive_to_db", True)):
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
                        (
                            last_retention_cleanup_ts,
                            last_retention_vacuum_ts,
                        ) = self._apply_archive_retention_policy(
                            store=store,
                            profile=profile,
                            ts=ts,
                            last_retention_cleanup_ts=last_retention_cleanup_ts,
                            last_retention_vacuum_ts=last_retention_vacuum_ts,
                        )

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
            self._record_live_connection_event(ts, state)
            if bool(getattr(profile, "archive_to_db", True)):
                store.insert_connection_event(profile.id, ts, state)

    @staticmethod
    def _archive_size_limit_bytes(profile: ProfileConfig) -> int:
        try:
            value = int(getattr(profile, "archive_max_size_value", 1024))
        except (TypeError, ValueError):
            value = 1024
        value = max(1, min(1024 * 1024, value))
        unit = str(getattr(profile, "archive_max_size_unit", "MB") or "MB").strip().upper()
        if unit == "GB":
            multiplier = 1024 * 1024 * 1024
        else:
            multiplier = 1024 * 1024
        return max(1, int(value) * int(multiplier))

    def _apply_archive_retention_policy(
        self,
        store: ArchiveStore,
        profile: ProfileConfig,
        ts: float,
        last_retention_cleanup_ts: float,
        last_retention_vacuum_ts: float,
    ) -> tuple[float, float]:
        if not bool(getattr(profile, "archive_to_db", True)):
            return last_retention_cleanup_ts, last_retention_vacuum_ts
        if (float(ts) - float(last_retention_cleanup_ts)) < 60.0:
            return last_retention_cleanup_ts, last_retention_vacuum_ts

        mode = str(getattr(profile, "archive_retention_mode", "days") or "days").strip().lower()
        if mode == "size":
            limit_bytes = self._archive_size_limit_bytes(profile)
            db_size = int(store.db_size_bytes())
            if db_size > limit_bytes:
                use_vacuum = (float(ts) - float(last_retention_vacuum_ts)) >= 900.0
                store.prune_to_max_size(
                    profile_id=str(profile.id),
                    max_size_bytes=int(limit_bytes),
                    vacuum=bool(use_vacuum),
                )
                if use_vacuum:
                    last_retention_vacuum_ts = float(ts)
                self._archive_begin_ts = store.min_sample_ts(profile.id)
                self._db_size_bytes = int(store.db_size_bytes())
        else:
            retention_days = max(0, int(getattr(profile, "archive_retention_days", 0)))
            if retention_days > 0:
                cutoff_ts = float(ts) - float(retention_days) * 86400.0
                store.prune_older_than(profile.id, cutoff_ts)
                self._archive_begin_ts = store.min_sample_ts(profile.id)
                self._db_size_bytes = int(store.db_size_bytes())
        return float(ts), last_retention_vacuum_ts

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
    def _is_modbus_source(source: Any) -> bool:
        return str(getattr(source, "source_kind", "remote_recorder") or "remote_recorder").strip().lower() == "modbus_tcp"

    @classmethod
    def _enabled_modbus_sources(cls, profile: ProfileConfig) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for source in list(getattr(profile, "recorder_sources", []) or []):
            source_id = str(getattr(source, "id", "") or "").strip()
            if not source_id:
                continue
            if not bool(getattr(source, "enabled", True)):
                continue
            if not cls._is_modbus_source(source):
                continue
            result[source_id] = source
        return result

    @classmethod
    def _signal_poll_endpoint(
        cls,
        profile: ProfileConfig,
        signal: Any,
        modbus_sources: dict[str, Any],
    ) -> tuple[str, str, int, int, float, int, int] | None:
        source_id = str(getattr(signal, "source_id", "local") or "local").strip() or "local"
        if source_id.lower() == "local":
            source_id = "local"
        if source_id == "local":
            return (
                "local",
                str(profile.ip),
                int(profile.port),
                int(profile.unit_id),
                float(profile.timeout_s),
                int(profile.address_offset),
                int(profile.retries),
            )
        source = modbus_sources.get(source_id)
        if source is None:
            return None
        return (
            str(source_id),
            str(getattr(source, "host", profile.ip) or profile.ip),
            int(getattr(source, "port", profile.port) or profile.port),
            int(getattr(source, "unit_id", profile.unit_id) or profile.unit_id),
            float(getattr(source, "timeout_s", profile.timeout_s) or profile.timeout_s),
            int(getattr(source, "address_offset", 0) or 0),
            int(getattr(source, "retries", profile.retries) or profile.retries),
        )

    @classmethod
    def _local_poll_groups(cls, profile: ProfileConfig) -> dict[tuple[str, str, int, int, float, int, int], list[Any]]:
        groups: dict[tuple[str, str, int, int, float, int, int], list[Any]] = {}
        modbus_sources = cls._enabled_modbus_sources(profile)
        for signal in list(getattr(profile, "signals", []) or []):
            endpoint = cls._signal_poll_endpoint(profile, signal, modbus_sources)
            if endpoint is None:
                continue
            groups.setdefault(endpoint, []).append(signal)
        return groups

    @classmethod
    def _local_signals(cls, profile: ProfileConfig) -> list[Any]:
        groups = cls._local_poll_groups(profile)
        return [signal for signals in groups.values() for signal in signals]


def run_recorder_service() -> None:
    service = RecorderService()
    service.run()
