#!/usr/bin/env bash
# TowerAI — configuração e execução (Linux/macOS)
# Uso:  bash run.sh
# Para executar o MARCO 3: python3 marco-3/executar.py
set -e
echo "Este script inicia a aplicação web. Para o MARCO 3: python3 marco-3/executar.py"
echo "== TowerAI: preparando ambiente =="

[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate

python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# Semeia dados de demonstração (usuário demo, dispositivo IoT, reunião, scan)
python demo_setup.py

echo "== Iniciando o servidor em http://localhost:5000 =="
python serve.py
