from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg


def startup_command(extra_args: list[str] | None = None) -> str:
    args = [str(item) for item in (extra_args or []) if str(item).strip()]
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        return subprocess.list2cmdline([str(exe_path), *args])
    exe_path = Path(sys.executable).resolve()
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    return subprocess.list2cmdline([str(exe_path), str(main_path), *args])


def set_windows_autostart(
    enabled: bool,
    app_name: str = "TrendAnalyzer",
    extra_args: list[str] | None = None,
) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Опция доступна только в Windows"
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if bool(enabled):
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, startup_command(extra_args=extra_args))
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
    except Exception as exc:
        return False, str(exc)
    return True, ""


def is_windows_autostart_enabled(app_name: str = "TrendAnalyzer") -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Опция доступна только в Windows"

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _reg_type = winreg.QueryValueEx(key, app_name)
            return bool(str(value).strip()), ""
    except FileNotFoundError:
        return False, ""
    except OSError as exc:
        if int(getattr(exc, "winerror", 0) or 0) == 2:
            return False, ""
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)
