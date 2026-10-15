"""Compila sketch.ino com Arduino CLI, preservando o nome exigido pelo Arduino.

O diretório temporário contém sketch/sketch.ino + segredos.h. Os binários
resultantes contêm credenciais e ficam em produtor/build/, fora do Git.
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cli", default="arduino-cli")
    ap.add_argument("--config-cli", type=Path)
    args = ap.parse_args()
    produtor = PASTA / "produtor"
    if not (produtor / "segredos.h").is_file():
        ap.error("copie broker/local/segredos.h para produtor/segredos.h e ajuste a rede")
    saida = produtor / "build"
    saida.mkdir(exist_ok=True)
    comando = [args.cli]
    if args.config_cli:
        comando += ["--config-file", str(args.config_cli.resolve())]
    with tempfile.TemporaryDirectory(prefix=".compilar-", dir=produtor) as temp:
        sketch = Path(temp) / "sketch"
        sketch.mkdir()
        for arquivo in ("sketch.ino", "segredos.h"):
            shutil.copy2(produtor / arquivo, sketch / arquivo)
        subprocess.run(comando + ["compile", "--fqbn", "esp32:esp32:esp32",
                                  "--output-dir", str(saida), str(sketch)], check=True)
    print("Firmware compilado em produtor/build/. Contém segredos: não compartilhar.")


if __name__ == "__main__":
    main()
