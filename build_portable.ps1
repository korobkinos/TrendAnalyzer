$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\.venv\Scripts\python.exe scripts\preflight_check.py

.\.venv\Scripts\python.exe assets\make_icon.py

.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name TrendAnalyzer `
  --icon assets\app_icon.ico `
  --add-data "assets\app_icon.ico;assets" `
  --hidden-import PySide6.QtPrintSupport `
  --collect-submodules pyqtgraph `
  --exclude-module pyqtgraph.examples `
  --exclude-module pyqtgraph.tests `
  --exclude-module PySide2 `
  --exclude-module PyQt5 `
  --exclude-module PyQt6 `
  --exclude-module tkinter `
  --exclude-module matplotlib `
  --exclude-module IPython `
  main.py

Write-Host "Build complete: dist\TrendAnalyzer.exe"
