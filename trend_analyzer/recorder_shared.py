from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import APP_DIR, atomic_write_text


RECORDER_CONFIG_FORMAT = "trend_recorder_config_v1"
RECORDER_STATUS_FORMAT = "trend_recorder_status_v1"
RECORDER_CONTROL_FORMAT = "trend_recorder_control_v1"

RECORDER_CONFIG_PATH = APP_DIR / "recorder_config.json"
RECORDER_STATUS_PATH = APP_DIR / "recorder_status.json"
RECORDER_CONTROL_PATH = APP_DIR / "recorder_control.json"
RECORDER_PID_PATH = APP_DIR / "recorder.pid"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def read_recorder_config() -> dict[str, Any] | None:
    return _read_json(RECORDER_CONFIG_PATH)


def write_recorder_config(payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write_text(RECORDER_CONFIG_PATH, data, encoding="utf-8")


def read_recorder_status() -> dict[str, Any] | None:
    return _read_json(RECORDER_STATUS_PATH)


def write_recorder_status(payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write_text(RECORDER_STATUS_PATH, data, encoding="utf-8")


def write_recorder_pid(pid: int) -> None:
    RECORDER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECORDER_PID_PATH.write_text(str(int(pid)), encoding="utf-8")


def read_recorder_pid() -> int | None:
    if not RECORDER_PID_PATH.exists():
        return None
    try:
        return int(RECORDER_PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def clear_recorder_pid() -> None:
    try:
        if RECORDER_PID_PATH.exists():
            RECORDER_PID_PATH.unlink()
    except Exception:
        pass


def is_pid_running(pid: int | None) -> bool:
    if pid is None or int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def request_recorder_stop() -> None:
    payload = {
        "format": RECORDER_CONTROL_FORMAT,
        "command": "stop",
        "issued_at": datetime.now().isoformat(timespec="seconds"),
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    atomic_write_text(RECORDER_CONTROL_PATH, data, encoding="utf-8")


def consume_stop_request() -> bool:
    payload = _read_json(RECORDER_CONTROL_PATH)
    if payload is None:
        return False
    command = str(payload.get("command") or "").strip().lower()
    try:
        RECORDER_CONTROL_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    return command == "stop"