from __future__ import annotations

import sys


def main() -> None:
    args = set(sys.argv[1:])
    if "--recorder-tray" in args:
        from trend_analyzer.recorder_tray import run_recorder_tray

        run_recorder_tray()
        return
    if "--recorder" in args:
        from trend_analyzer.recorder_service import run_recorder_service

        run_recorder_service()
        return
    from trend_analyzer.ui import run_app

    run_app()


if __name__ == "__main__":
    main()
