$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Step {
  param(
    [string]$Title,
    [scriptblock]$ScriptBlock
  )
  Write-Host "==> $Title"
  & $ScriptBlock
  if ($LASTEXITCODE -ne 0) {
    throw "Step failed: $Title (exit code $LASTEXITCODE)"
  }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  python -m venv .venv
}

Invoke-Step "Upgrade pip" { .\.venv\Scripts\python.exe -m pip install --upgrade pip }
Invoke-Step "Install requirements" { .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt }
Invoke-Step "Preflight checks" { .\.venv\Scripts\python.exe scripts\preflight_check.py }
Invoke-Step "Generate app icon" { .\.venv\Scripts\python.exe assets\make_icon.py }

# Clean old/legacy binaries to avoid confusion.
$legacy = @(
  "dist\\TrendAnalyzer.exe",
  "dist\\TrendRecorderCore.exe",
  "dist\\TrendRecorderTray.exe",
  "dist\\TrendClient.exe",
  "dist\\TrendRecorder.exe"
)
foreach ($item in $legacy) {
  if (Test-Path $item) {
    try {
      Remove-Item -Force $item -ErrorAction Stop
    } catch {
      Write-Warning "Cannot remove $item (probably in use)."
      if ($item -like "*TrendClient.exe" -or $item -like "*TrendRecorder.exe") {
        throw "File $item is locked. Close running TrendClient/TrendRecorder and retry build."
      }
    }
  }
}

Invoke-Step "Build TrendClient.exe" {
  .\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    TrendClient.spec
}

Invoke-Step "Build TrendRecorder.exe" {
  .\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name TrendRecorder `
    --icon assets\app_icon.ico `
    --add-data "assets\app_icon.ico;assets" `
    recorder_tray_main.py
}

Write-Host "Build complete:"
Write-Host " - dist\\TrendClient.exe"
Write-Host " - dist\\TrendRecorder.exe (tray + recorder core)"
