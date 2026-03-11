from __future__ import annotations

import compileall
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "trend_analyzer"


def _run_step(title: str, cmd: list[str]) -> None:
    print(f"==> {title}")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> int:
    print("==> Preflight: compile trend_analyzer")
    ok = compileall.compile_dir(str(PKG_DIR), quiet=1, force=False)
    if not ok:
        print("Compile check failed")
        return 1

    _run_step(
        "Preflight: run unit tests",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
    )
    print("Preflight OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

