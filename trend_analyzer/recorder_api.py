from __future__ import annotations

import json
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pymodbus.client import ModbusTcpClient

from .models import ProfileConfig
from .modbus_worker import ModbusWorker
from .storage import DEFAULT_DB_PATH


API_FORMAT = "trend_recorder_api_v1"


def _safe_int(value: str | None, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(int(minimum), result)
    if maximum is not None:
        result = min(int(maximum), result)
    return result


def _safe_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


class RecorderApiServer:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._bind_host = "0.0.0.0"
        self._port = 18777

    @property
    def bind_host(self) -> str:
        return self._bind_host

    @property
    def port(self) -> int:
        return int(self._port)

    def start(self, host: str, port: int) -> tuple[bool, str]:
        self.stop()
        self._bind_host = str(host or "0.0.0.0")
        self._port = max(1, min(65535, int(port)))

        handler = self._build_handler()
        try:
            self._server = ThreadingHTTPServer((self._bind_host, self._port), handler)
        except Exception as exc:
            self._server = None
            return False, str(exc)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="RecorderApiServer", daemon=True)
        self._thread.start()
        return True, ""

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
        self._server = None
        self._thread = None

    def _build_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TrendRecorderAPI/1.0"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args) -> None:  # noqa: A003
                return

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)

            def _read_json_body(self) -> dict[str, Any] | None:
                length = _safe_int(self.headers.get("Content-Length"), 0, minimum=0, maximum=10_000_000)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    return None
                if not isinstance(payload, dict):
                    return None
                return payload

            def _authorized(self) -> bool:
                token = str(outer._service.get_api_token() or "")
                if not token:
                    return True
                header_token = str(self.headers.get("X-Recorder-Token") or "").strip()
                if not header_token:
                    auth = str(self.headers.get("Authorization") or "").strip()
                    if auth.lower().startswith("bearer "):
                        header_token = auth[7:].strip()
                return bool(header_token) and (header_token == token)

            def _unauthorized(self) -> None:
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"ok": False, "error": "unauthorized"},
                )

            def _db_connect(self) -> sqlite3.Connection:
                profile = outer._service.get_runtime_profile()
                db_path = Path(str(profile.db_path or DEFAULT_DB_PATH))
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA busy_timeout=2000;")
                return conn

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                query = parse_qs(parsed.query, keep_blank_values=True)

                if path == "/v1/health":
                    payload = outer._service.build_health_payload(api_host=outer.bind_host, api_port=outer.port)
                    payload["format"] = API_FORMAT
                    self._send_json(HTTPStatus.OK, payload)
                    return

                if not self._authorized():
                    self._unauthorized()
                    return

                if path == "/v1/tags":
                    profile = outer._service.get_runtime_profile()
                    tags = []
                    for signal in profile.signals:
                        tags.append(
                            {
                                "id": str(signal.id),
                                "name": str(signal.name),
                                "address": int(signal.address),
                                "register_type": str(signal.register_type),
                                "data_type": str(signal.data_type),
                                "bit_index": int(signal.bit_index),
                                "float_order": str(signal.float_order),
                                "scale": float(signal.scale),
                                "enabled": bool(signal.enabled),
                                "axis_index": int(signal.axis_index),
                                "color": str(signal.color),
                            }
                        )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "format": API_FORMAT,
                            "profile_id": profile.id,
                            "profile_name": profile.name,
                            "tags": tags,
                        },
                    )
                    return

                if path == "/v1/live":
                    since_sample_id = _safe_int((query.get("since_sample_id") or [None])[0], 0, minimum=0)
                    since_event_id = _safe_int((query.get("since_event_id") or [None])[0], 0, minimum=0)
                    sample_limit = _safe_int((query.get("sample_limit") or [None])[0], 6000, minimum=1, maximum=50000)
                    event_limit = _safe_int((query.get("event_limit") or [None])[0], 3000, minimum=1, maximum=20000)
                    bootstrap = _safe_int((query.get("bootstrap") or [None])[0], 0, minimum=0, maximum=1) == 1
                    profile = outer._service.get_runtime_profile()
                    if not bool(getattr(profile, "archive_to_db", True)):
                        payload = outer._service.get_live_stream_payload(
                            since_sample_id=int(since_sample_id),
                            since_event_id=int(since_event_id),
                            sample_limit=int(sample_limit),
                            event_limit=int(event_limit),
                            bootstrap=bool(bootstrap),
                        )
                        payload["format"] = API_FORMAT
                        self._send_json(HTTPStatus.OK, payload)
                        return
                    try:
                        conn = self._db_connect()
                    except Exception as exc:
                        self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                        return
                    try:
                        if bootstrap:
                            sample_row = conn.execute(
                                "SELECT MAX(id) FROM samples WHERE profile_id = ?",
                                (profile.id,),
                            ).fetchone()
                            event_row = conn.execute(
                                "SELECT MAX(id) FROM connection_events WHERE profile_id = ?",
                                (profile.id,),
                            ).fetchone()
                            self._send_json(
                                HTTPStatus.OK,
                                {
                                    "ok": True,
                                    "format": API_FORMAT,
                                    "profile_id": profile.id,
                                    "connected": bool(outer._service.build_health_payload(outer.bind_host, outer.port).get("connected", False)),
                                    "samples": [],
                                    "connection_events": [],
                                    "next_sample_id": int(sample_row[0] or 0) if sample_row else 0,
                                    "next_event_id": int(event_row[0] or 0) if event_row else 0,
                                    "server_ts": time.time(),
                                },
                            )
                            return

                        name_rows = conn.execute(
                            """
                            SELECT signal_id, signal_name
                            FROM signals_meta
                            WHERE profile_id = ?
                            """,
                            (profile.id,),
                        ).fetchall()
                        names = {str(item[0]): str(item[1]) for item in name_rows}

                        sample_rows = conn.execute(
                            """
                            SELECT id, signal_id, ts, value
                            FROM samples
                            WHERE profile_id = ? AND id > ?
                            ORDER BY id ASC
                            LIMIT ?
                            """,
                            (profile.id, int(since_sample_id), int(sample_limit)),
                        ).fetchall()
                        event_rows = conn.execute(
                            """
                            SELECT id, ts, is_connected
                            FROM connection_events
                            WHERE profile_id = ? AND id > ?
                            ORDER BY id ASC
                            LIMIT ?
                            """,
                            (profile.id, int(since_event_id), int(event_limit)),
                        ).fetchall()
                    finally:
                        conn.close()

                    samples_payload = [
                        {
                            "id": int(row_id),
                            "tag_id": str(tag_id),
                            "tag_name": names.get(str(tag_id), str(tag_id)),
                            "ts": float(ts),
                            "value": float(value),
                        }
                        for row_id, tag_id, ts, value in sample_rows
                    ]
                    events_payload = [
                        {"id": int(row_id), "ts": float(ts), "is_connected": int(is_connected)}
                        for row_id, ts, is_connected in event_rows
                    ]
                    next_sample_id = int(samples_payload[-1]["id"]) if samples_payload else int(since_sample_id)
                    next_event_id = int(events_payload[-1]["id"]) if events_payload else int(since_event_id)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "format": API_FORMAT,
                            "profile_id": profile.id,
                            "connected": bool(outer._service.build_health_payload(outer.bind_host, outer.port).get("connected", False)),
                            "samples": samples_payload,
                            "connection_events": events_payload,
                            "next_sample_id": next_sample_id,
                            "next_event_id": next_event_id,
                            "server_ts": time.time(),
                        },
                    )
                    return

                if path == "/v1/history":
                    profile = outer._service.get_runtime_profile()
                    end_ts = _safe_float((query.get("to_ts") or [None])[0], time.time())
                    start_ts = _safe_float((query.get("from_ts") or [None])[0], end_ts - 600.0)
                    if end_ts < start_ts:
                        start_ts, end_ts = end_ts, start_ts

                    raw_tag_ids = (query.get("tag_ids") or [""])[0]
                    tag_ids = [item.strip() for item in str(raw_tag_ids).split(",") if item.strip()]
                    if not bool(getattr(profile, "archive_to_db", True)):
                        payload = outer._service.get_live_history_payload(
                            start_ts=float(start_ts),
                            end_ts=float(end_ts),
                            tag_ids=tag_ids,
                        )
                        payload["format"] = API_FORMAT
                        self._send_json(HTTPStatus.OK, payload)
                        return
                    tag_filter_sql = ""
                    params: list[Any] = [profile.id, float(start_ts), float(end_ts)]
                    if tag_ids:
                        placeholders = ",".join("?" for _ in tag_ids)
                        tag_filter_sql = f" AND signal_id IN ({placeholders})"
                        params.extend(tag_ids)

                    try:
                        conn = self._db_connect()
                    except Exception as exc:
                        self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                        return
                    try:
                        rows = conn.execute(
                            f"""
                            SELECT signal_id, ts, value
                            FROM samples
                            WHERE profile_id = ? AND ts >= ? AND ts <= ? {tag_filter_sql}
                            ORDER BY ts ASC, id ASC
                            """,
                            tuple(params),
                        ).fetchall()
                        conn_rows = conn.execute(
                            """
                            SELECT ts, is_connected
                            FROM connection_events
                            WHERE profile_id = ? AND ts >= ? AND ts <= ?
                            ORDER BY ts ASC, id ASC
                            """,
                            (profile.id, float(start_ts), float(end_ts)),
                        ).fetchall()
                    finally:
                        conn.close()

                    samples_map: dict[str, list[list[float]]] = {}
                    for tag_id, ts, value in rows:
                        sid = str(tag_id)
                        samples_map.setdefault(sid, []).append([float(ts), float(value)])
                    events_payload = [[float(ts), float(int(is_connected))] for ts, is_connected in conn_rows]
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "format": API_FORMAT,
                            "profile_id": profile.id,
                            "from_ts": float(start_ts),
                            "to_ts": float(end_ts),
                            "samples": samples_map,
                            "connection_events": events_payload,
                        },
                    )
                    return

                if path == "/v1/config":
                    profile = outer._service.get_runtime_profile()
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "format": API_FORMAT,
                            "profile": profile.to_dict(),
                        },
                    )
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

            def do_PUT(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/v1/config":
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                    return
                if not self._authorized():
                    self._unauthorized()
                    return
                payload = self._read_json_body()
                if payload is None:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
                    return
                raw_profile = payload.get("profile")
                if not isinstance(raw_profile, dict):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "profile_missing"})
                    return
                ok, message = outer._service.apply_runtime_profile(raw_profile)
                status = HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST
                self._send_json(status, {"ok": ok, "message": str(message)})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if not self._authorized():
                    self._unauthorized()
                    return
                payload = self._read_json_body()
                if payload is None:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
                    return
                if parsed.path == "/v1/modbus/read":
                    ok, result = outer._service.api_modbus_read(payload)
                    self._send_json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result})
                    return
                if parsed.path == "/v1/modbus/read_many":
                    ok, result = outer._service.api_modbus_read_many(payload)
                    self._send_json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result})
                    return
                if parsed.path == "/v1/modbus/write":
                    ok, result = outer._service.api_modbus_write(payload)
                    self._send_json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, {"ok": ok, **result})
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

        return Handler


def api_modbus_read(profile: ProfileConfig, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    register_type = str(payload.get("register_type") or "holding")
    data_type = str(payload.get("data_type") or "int16")
    float_order = str(payload.get("float_order") or "ABCD")
    address_offset = int(payload.get("address_offset", profile.address_offset) or 0)
    unit_id = int(payload.get("unit_id", profile.unit_id) or profile.unit_id)
    address = _safe_int(str(payload.get("address", "0")), 0, minimum=0)
    bit_index = _safe_int(str(payload.get("bit_index", "0")), 0, minimum=0, maximum=15)
    count = _safe_int(str(payload.get("count", "1")), 1, minimum=1, maximum=125)
    timeout_s = _safe_float(str(payload.get("timeout_s") if payload.get("timeout_s") is not None else profile.timeout_s), profile.timeout_s)
    host = str(payload.get("host") or profile.ip)
    port = _safe_int(str(payload.get("port") if payload.get("port") is not None else profile.port), profile.port, minimum=1, maximum=65535)

    client = ModbusTcpClient(host=host, port=port, timeout=timeout_s)
    try:
        if not client.connect():
            return False, {"error": f"connect_failed {host}:{port}"}
        if count > 1:
            addr = max(0, int(address) + int(address_offset))
            if register_type == "input":
                response = ModbusWorker._read_input_registers(client, addr, count, unit_id)
            else:
                response = ModbusWorker._read_holding_registers(client, addr, count, unit_id)
            if response.isError():
                return False, {"error": str(response)}
            registers = [int(v) for v in list(getattr(response, "registers", []) or [])]
            return True, {"address": int(address), "count": int(count), "registers": registers}

        signal = type("TempSignal", (), {})()
        signal.address = int(address)
        signal.register_type = register_type
        signal.data_type = data_type
        signal.bit_index = bit_index
        signal.float_order = float_order
        signal.scale = 1.0
        value = ModbusWorker._read_signal(client, signal, unit_id, address_offset)
        return True, {"address": int(address), "value": float(value)}
    except Exception as exc:
        return False, {"error": str(exc)}
    finally:
        try:
            client.close()
        except Exception:
            pass


def api_modbus_read_many(profile: ProfileConfig, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return False, {"error": "items_missing"}
    if not raw_items:
        return True, {"values": [], "errors": []}

    address_offset = int(payload.get("address_offset", profile.address_offset) or 0)
    unit_id = int(payload.get("unit_id", profile.unit_id) or profile.unit_id)
    timeout_s = _safe_float(
        str(payload.get("timeout_s") if payload.get("timeout_s") is not None else profile.timeout_s),
        profile.timeout_s,
    )
    host = str(payload.get("host") or profile.ip)
    port = _safe_int(
        str(payload.get("port") if payload.get("port") is not None else profile.port),
        profile.port,
        minimum=1,
        maximum=65535,
    )
    read_attempts = _safe_int(
        str(payload.get("read_attempts") if payload.get("read_attempts") is not None else (int(profile.retries) + 1)),
        int(profile.retries) + 1,
        minimum=1,
        maximum=10,
    )

    temp_items: list[Any] = []
    order: list[str] = []
    for index, item in enumerate(raw_items[:5000]):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or f"item_{index}")
        register_type = str(item.get("register_type") or "holding")
        data_type = str(item.get("data_type") or "int16")
        float_order = str(item.get("float_order") or "ABCD")
        address = _safe_int(str(item.get("address", "0")), 0, minimum=0)
        bit_index = _safe_int(str(item.get("bit_index", "0")), 0, minimum=0, maximum=15)

        signal = type("ApiBatchSignal", (), {})()
        signal.id = item_id
        signal.name = str(item.get("name") or item_id)
        signal.address = int(address)
        signal.register_type = register_type
        signal.data_type = data_type
        signal.bit_index = int(bit_index)
        signal.float_order = float_order
        signal.scale = 1.0
        temp_items.append(signal)
        order.append(item_id)

    if not temp_items:
        return False, {"error": "items_invalid"}

    client = ModbusTcpClient(host=host, port=port, timeout=timeout_s)
    try:
        if not client.connect():
            return False, {"error": f"connect_failed {host}:{port}"}

        specs = ModbusWorker._build_read_specs(temp_items, address_offset=address_offset, default_scale=1.0)
        values, errors, comm_error = ModbusWorker._read_specs_grouped(
            client,
            specs,
            unit_id=unit_id,
            read_attempts=read_attempts,
        )
        values_payload = []
        for item_id in order:
            entry = values.get(str(item_id))
            if entry is None:
                continue
            _name, value = entry
            values_payload.append({"id": str(item_id), "value": float(value)})

        errors_payload = [
            {"id": str(spec.get("id", "")), "error": str(exc)}
            for spec, exc in errors
        ]
        comm_payload = None
        if comm_error is not None:
            comm_spec, comm_exc = comm_error
            comm_payload = {
                "id": str(comm_spec.get("id", "")),
                "error": str(comm_exc),
            }

        return True, {
            "values": values_payload,
            "errors": errors_payload,
            "connection_error": comm_payload,
        }
    except Exception as exc:
        return False, {"error": str(exc)}
    finally:
        try:
            client.close()
        except Exception:
            pass


def api_modbus_write(profile: ProfileConfig, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    register_type = str(payload.get("register_type") or "holding")
    data_type = str(payload.get("data_type") or "int16")
    float_order = str(payload.get("float_order") or "ABCD")
    address_offset = int(payload.get("address_offset", profile.address_offset) or 0)
    unit_id = int(payload.get("unit_id", profile.unit_id) or profile.unit_id)
    address = _safe_int(str(payload.get("address", "0")), 0, minimum=0)
    bit_index = _safe_int(str(payload.get("bit_index", "0")), 0, minimum=0, maximum=15)
    timeout_s = _safe_float(str(payload.get("timeout_s") if payload.get("timeout_s") is not None else profile.timeout_s), profile.timeout_s)
    host = str(payload.get("host") or profile.ip)
    port = _safe_int(str(payload.get("port") if payload.get("port") is not None else profile.port), profile.port, minimum=1, maximum=65535)
    value = _safe_float(str(payload.get("value", "0")), 0.0)

    if register_type == "input":
        return False, {"error": "input_register_read_only"}

    client = ModbusTcpClient(host=host, port=port, timeout=timeout_s)
    try:
        if not client.connect():
            return False, {"error": f"connect_failed {host}:{port}"}

        addr = max(0, int(address) + int(address_offset))
        if data_type == "int16":
            raw = int(round(value))
            if raw < -32768 or raw > 32767:
                return False, {"error": "int16_out_of_range"}
            response = ModbusWorker._write_single_register(client, addr, raw & 0xFFFF, unit_id)
        elif data_type == "uint16":
            raw = int(round(value))
            if raw < 0 or raw > 65535:
                return False, {"error": "uint16_out_of_range"}
            response = ModbusWorker._write_single_register(client, addr, raw, unit_id)
        elif data_type == "bool":
            desired = 1 if int(round(value)) != 0 else 0
            current = ModbusWorker._read_holding_registers(client, addr, 1, unit_id)
            if current.isError():
                return False, {"error": f"read_before_write_failed: {current}"}
            current_reg = int(current.registers[0]) if current.registers else 0
            if desired:
                raw = current_reg | (1 << int(bit_index))
            else:
                raw = current_reg & ~(1 << int(bit_index))
            response = ModbusWorker._write_single_register(client, addr, raw & 0xFFFF, unit_id)
        else:
            reg0, reg1 = ModbusWorker._encode_float32_words(float(value), float_order)
            response = ModbusWorker._write_multiple_registers(client, addr, [reg0, reg1], unit_id)
        if response.isError():
            return False, {"error": str(response)}
        return True, {"address": int(address), "value": float(value)}
    except Exception as exc:
        return False, {"error": str(exc)}
    finally:
        try:
            client.close()
        except Exception:
            pass
