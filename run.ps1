# TowerAI — configuração e execução (Windows / PowerShell)
# Uso:  .\run.ps1
# Para executar o MARCO 3, use INICIAR-MARCO-3.cmd ou python marco-3/executar.py.
$ErrorActionPreference = "Stop"
Write-Host "Este script inicia a aplicação web. Para o MARCO 3, abra INICIAR-MARCO-3.cmd." -ForegroundColor Yellow
Write-Host "== TowerAI: preparando ambiente ==" -ForegroundColor Cyan

if (-not (Test-Path .venv)) { python -m venv .venv }
. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip | Out-Null
pip install -r requirements.txt

# Semeia dados de demonstração (usuário demo, dispositivo IoT, reunião, scan)
python demo_setup.py

Write-Host "== Iniciando o servidor em http://localhost:5000 ==" -ForegroundColor Green
python serve.py
