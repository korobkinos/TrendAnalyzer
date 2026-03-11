from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import APP_DIR, atomic_write_text

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None


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
    RECORDER_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # recorder_status.json is updated frequently and often read by tray/client.
    # On Windows, atomic replace may fail if another process has the target
    # file open without FILE_SHARE_DELETE. Use direct overwrite with retries.
    last_error: Exception | None = None
    for _ in range(6):
        try:
            with open(RECORDER_STATUS_PATH, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.03)
    if last_error is not None:
        raise last_error


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
    if psutil is not None:
        try:
            proc = psutil.Process(int(pid))
            return bool(proc.is_running())
        except Exception:
            pass
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        # On Windows AccessDenied may still indicate that process exists.
        return True
    except OSError:
        return False
    except Exception:
        return False
    return True


def _looks_like_recorder_process(pid: int) -> bool:
    if psutil is None:
        return True
    try:
        proc = psutil.Process(int(pid))
        name = str(proc.name() or "").strip().lower()
        cmdline = [str(part).strip().lower() for part in (proc.cmdline() or [])]
    except Exception:
        return False
    if "trendrecorder" in name:
        return True
    if any("--recorder" == part for part in cmdline):
        return True
    if any(part.endswith("recorder_main.py") for part in cmdline):
        return True
    return False


def is_recorder_pid_running(pid: int | None) -> bool:
    if not is_pid_running(pid):
        return False
    if pid is None:
        return False
    return _looks_like_recorder_process(int(pid))


def resolve_recorder_pid(heal_pid_file: bool = True) -> int | None:
    pid = read_recorder_pid()
    if is_recorder_pid_running(pid):
        return int(pid) if pid is not None else None

    payload = read_recorder_status() or {}
    status_pid_raw = payload.get("pid")
    try:
        status_pid = int(status_pid_raw)
    except Exception:
        status_pid = 0
    if status_pid > 0 and is_recorder_pid_running(status_pid):
        if heal_pid_file:
            try:
                write_recorder_pid(status_pid)
            except Exception:
                pass
        return status_pid
    return None


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
