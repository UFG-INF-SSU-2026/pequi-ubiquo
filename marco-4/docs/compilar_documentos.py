"""Compila o artigo SBC e a apresentação UFG com Tectonic."""

import argparse
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


DOCS = Path(__file__).resolve().parent
SOURCES = {
    "artigo": Path("template_artigo/towerai.tex"),
    "apresentacao": Path("template_slide/towerai-marco-4.tex"),
}


def compile_document(name, tectonic):
    source = DOCS / "templates latex" / SOURCES[name]
    with TemporaryDirectory(prefix="towerai-latex-", dir=DOCS) as folder:
        subprocess.run(
            [tectonic, "--outdir", folder, source.name], cwd=source.parent, check=True,
        )
        (Path(folder) / source.with_suffix(".pdf").name).replace(DOCS / f"{name}.pdf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", nargs="?", choices=("todos", *SOURCES), default="todos")
    args = parser.parse_args()
    tectonic = shutil.which("tectonic")
    if tectonic is None:
        parser.error("Instale o Tectonic e confira se o executável está no PATH.")
    for name in SOURCES if args.document == "todos" else (args.document,):
        compile_document(name, tectonic)


if __name__ == "__main__":
    main()
