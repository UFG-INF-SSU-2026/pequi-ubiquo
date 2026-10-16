"""Compila sketch.ino com Arduino CLI, preservando o nome exigido pelo Arduino.

O diretório temporário contém sketch/sketch.ino + segredos.h. Os binários
resultantes contêm credenciais e ficam em produtor/build/, fora do Git.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1]


def header_wokwi():
    """Provisiona só o build simulado; nunca altera o segredos.h da placa física."""
    local = PASTA / "broker/local"
    valores = dict(linha.split("=", 1) for linha in
                   (local / "mic-esp32-01.env").read_text(encoding="utf-8").splitlines()
                   if linha and not linha.startswith("#") and "=" in linha)
    campos = {"WIFI_SSID": "Wokwi-GUEST", "WIFI_SENHA": "",
              "MQTT_BROKER": "host.wokwi.internal", "NTP_SERVIDOR": "host.wokwi.internal",
              "MQTT_USUARIO": valores["MQTT_USUARIO"], "MQTT_SENHA": valores["MQTT_SENHA"]}
    linhas = ["#pragma once", "// Provisionamento temporario do Wokwi; nunca versionar."]
    # A porta 123 já pode pertencer ao serviço de hora do Windows. O cliente
    # SNTP de laboratório no firmware usa esta porta própria sem reconfigurá-lo.
    linhas.append("#define TOWERAI_WOKWI_NTP_PORTA 4123")
    linhas += [f"#define {chave} {json.dumps(valor)}" for chave, valor in campos.items()]
    ca = (local / "ca.pem").read_text(encoding="utf-8")
    return "\n".join(linhas) + '\nstatic const char CA_RAIZ[] = R"PEM(\n' + ca + ')PEM";\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cli", help="Arduino CLI; detecta PATH ou o cache local do projeto")
    ap.add_argument("--config-cli", type=Path)
    ap.add_argument("--wokwi", action="store_true", help="gera build separado com Wi-Fi e serviços locais do Wokwi")
    args = ap.parse_args()
    produtor = PASTA / "produtor"
    if not args.wokwi and not (produtor / "segredos.h").is_file():
        ap.error("copie broker/local/segredos.h para produtor/segredos.h e ajuste a rede")
    if args.wokwi and not (PASTA / "broker/local/mic-esp32-01.env").is_file():
        ap.error("execute primeiro: python marco-3/executar.py preparar")
    executavel = "arduino-cli.exe" if os.name == "nt" else "arduino-cli"
    cache = PASTA.parent / ".tmp/arduino-cli" / executavel
    cli = args.cli or shutil.which("arduino-cli")
    config_cli = args.config_cli
    if not cli and cache.is_file():
        cli = str(cache)
        config_cli = config_cli or PASTA.parent / ".tmp/arduino-cli.yaml"
    if not cli:
        ap.error("Arduino CLI não encontrado. Siga a preparação em marco-3/WOKWI.md.")
    saida = produtor / ("build-wokwi" if args.wokwi else "build")
    saida.mkdir(exist_ok=True)
    comando = [cli]
    if config_cli:
        comando += ["--config-file", str(config_cli.resolve())]
    with tempfile.TemporaryDirectory(prefix=".compilar-", dir=produtor) as temp:
        sketch = Path(temp) / "sketch"
        sketch.mkdir()
        shutil.copy2(produtor / "sketch.ino", sketch / "sketch.ino")
        if args.wokwi:
            (sketch / "segredos.h").write_text(header_wokwi(), encoding="utf-8")
        else:
            shutil.copy2(produtor / "segredos.h", sketch / "segredos.h")
        subprocess.run(comando + ["compile", "--fqbn", "esp32:esp32:esp32",
                                  "--output-dir", str(saida), str(sketch)], check=True)
    print(f"Firmware compilado em produtor/{saida.name}/. Contém segredos: não compartilhar.")
    if args.wokwi:
        print("Wokwi: Wi-Fi Wokwi-GUEST, broker e NTP em host.wokwi.internal. Configuração física preservada.")


if __name__ == "__main__":
    main()
