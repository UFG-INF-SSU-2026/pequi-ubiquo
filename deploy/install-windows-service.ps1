# Run TowerAI as a Windows service (starts on boot, restarts on crash) with NSSM.
# Run in an *administrator* PowerShell from the project folder, after run.ps1
# has created .venv:   .\deploy\install-windows-service.ps1
# Needs NSSM (https://nssm.cc, or `winget install nssm`) on the PATH.
$ErrorActionPreference = "Stop"
$dir = (Resolve-Path "$PSScriptRoot\..").Path
$python = Join-Path $dir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run .\run.ps1 first to create .venv" }
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { throw "Install NSSM first: winget install nssm" }
New-Item -ItemType Directory -Force (Join-Path $dir "logs") | Out-Null

nssm install TowerAI $python "serve.py"
nssm set TowerAI AppDirectory $dir
nssm set TowerAI Start SERVICE_AUTO_START
nssm set TowerAI AppExit Default Restart
nssm set TowerAI AppRestartDelay 5000
nssm set TowerAI AppStdout (Join-Path $dir "logs\towerai.log")
nssm set TowerAI AppStderr (Join-Path $dir "logs\towerai.log")
nssm set TowerAI AppRotateFiles 1
nssm set TowerAI AppRotateBytes 10485760   # rotate logs at 10 MB
nssm start TowerAI
Write-Host "TowerAI service installed and started. Manage it with: nssm restart TowerAI / nssm remove TowerAI confirm"
