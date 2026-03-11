#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -r requirements-build.txt
.venv/bin/python scripts/preflight_check.py

# 1) Client UI
.venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onefile \
  --name TrendClient \
  --collect-all pyqtgraph \
  --hidden-import PySide6.QtSvg \
  --hidden-import PySide6.QtOpenGLWidgets \
  --hidden-import PySide6.QtPrintSupport \
  client_main.py

# 2) Headless Recorder (no UI)
.venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name TrendRecorder \
  recorder_main.py

echo "Build complete:"
echo " - dist/TrendClient"
echo " - dist/TrendRecorder"
