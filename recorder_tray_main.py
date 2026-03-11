from __future__ import annotations

import sys

from trend_analyzer.recorder_service import run_recorder_service
from trend_analyzer.recorder_tray import run_recorder_tray


def main() -> None:
    args = set(sys.argv[1:])
    if "--recorder" in args:
        run_recorder_service()
        return
    run_recorder_tray()


if __name__ == "__main__":
    main()
