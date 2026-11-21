"""Copia os avisos legais das dependências de execução para o instalador."""

import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement


def collect(output):
    python = Path(sys.base_prefix)
    for source, name in ((python / "LICENSE.txt", "Python-LICENSE.txt"),
                         (python / "tcl/tk8.6/license.terms", "Tcl-Tk-LICENSE.txt")):
        if source.is_file():
            shutil.copyfile(source, output / name)
    pending = [
        "faster-whisper", "ctranslate2", "opencv-python-headless", "cryptography",
        "cv2-enumerate-cameras", "reportlab", "pypdf", "py3langid",
    ]
    copied = set()
    while pending:
        package = distribution(pending.pop())
        name = package.metadata["Name"]
        if name in copied:
            continue
        copied.add(name)
        for entry in package.files or ():
            if ".." not in entry.parts and (entry.name.upper().startswith(("LICENSE", "NOTICE", "COPYING"))
                                             or entry.name.lower() == "bitstream-vera-license.txt"):
                destination = output / name / Path(*entry.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package.locate_file(entry), destination)
        for value in package.requires or ():
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)


if __name__ == "__main__":
    collect(Path(sys.argv[1]))
