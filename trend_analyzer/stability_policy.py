from __future__ import annotations


def should_preload_history_on_profile_load(
    work_mode: str,
    render_chart_enabled: bool,
    live_running: bool,
) -> bool:
    """Startup policy for history warmup.

    We keep online startup lightweight to avoid UI freezes on launch.
    History in online mode is loaded on explicit "Start".
    """
    if not bool(render_chart_enabled):
        return False
    mode = str(work_mode or "online").strip().lower()
    if mode == "offline":
        return True
    if mode == "online":
        return False
    return False


def should_force_auto_x_on_start(current_auto_x: bool) -> bool:
    """Return False by default: user setting must not be overridden."""
    _ = bool(current_auto_x)
    return False

