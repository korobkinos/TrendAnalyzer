from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        return f'"{exe_path}"'
    exe_path = Path(sys.executable).resolve()
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{exe_path}" "{main_path}"'


def set_windows_autostart(enabled: bool, app_name: str = "TrendAnalyzer") -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Опция доступна только в Windows"
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if bool(enabled):
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
    except Exception as exc:
        return False, str(exc)
    return True, ""
