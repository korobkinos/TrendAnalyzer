$ErrorActionPreference = "Stop"

param(
  [Parameter(Mandatory = $false)]
  [ValidateSet("patch", "minor", "major")]
  [string]$Part = "patch",

  [Parameter(Mandatory = $false)]
  [string]$SetVersion = "",

  [Parameter(Mandatory = $true)]
  [string]$Note
)

$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

if ([string]::IsNullOrWhiteSpace($SetVersion)) {
  & $python scripts\bump_version.py --part $Part --note $Note
} else {
  & $python scripts\bump_version.py --set $SetVersion --note $Note
}
