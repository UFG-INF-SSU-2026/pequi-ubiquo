"""Gera o artigo A4 com ReportLab a partir da fonte editável."""

import argparse
import html
import re
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate


ROOT = Path(__file__).resolve().parent
INK = HexColor("#162720")
ACCENT = HexColor("#176552")
MUTED = HexColor("#52635B")
BODY = ParagraphStyle("Body", fontName="Helvetica", fontSize=11, leading=16, textColor=INK,
                      spaceAfter=12, alignment=TA_JUSTIFY)
TITLE = ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=45, leading=49,
                       textColor=ACCENT, spaceBefore=26, spaceAfter=12, keepWithNext=True)
SECTION = ParagraphStyle("Section", fontName="Helvetica-Bold", fontSize=22, leading=26,
                         textColor=INK, spaceBefore=10, spaceAfter=16, keepWithNext=True)
SUBHEAD = ParagraphStyle("Subhead", fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=ACCENT,
                         spaceBefore=9, spaceAfter=5, keepWithNext=True)
REFERENCE = ParagraphStyle("Reference", parent=BODY, fontSize=9.5, leading=12.5, spaceAfter=6)


def inline(text):
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return re.sub(r'(https?://[^\s<]+)', r'<a href="\1" color="#176552">\1</a>', escaped)


def parts(markdown):
    for block in markdown.strip().split("\n\n"):
        text = " ".join(block.strip().splitlines())
        if not text:
            continue
        if text == "---":
            yield PageBreak()
        else:
            prefix, _, rest = text.partition(" ")
            styles = {"#": TITLE, "##": SECTION, "###": SUBHEAD}
            style = styles.get(prefix, REFERENCE if re.match(r"^\[\d+\]", text) else BODY)
            yield Paragraph(inline(rest if prefix in styles else text), style)


def page(canvas, doc):
    width, height = A4
    canvas.saveState()
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.8)
    canvas.line(49, height - 55, width - 49, height - 55)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(49, height - 42, "TowerAI  /  Software para Sistemas Ubíquos")
    canvas.drawString(49, 37, "Pequi Ubíquo · UFG · Marco 4")
    canvas.drawRightString(width - 49, 37, str(doc.page))
    canvas.restoreState()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artigo.pdf")
    args = parser.parse_args()
    markdown = (ROOT / "artigo.md").read_text(encoding="utf-8")
    if "{{" in markdown:
        parser.error("Preencha os resultados observados antes de gerar o artigo.")
    doc = SimpleDocTemplate(
        str(args.output), pagesize=A4, leftMargin=49, rightMargin=49, topMargin=69, bottomMargin=62,
        title="TowerAI — Registro de reuniões com contexto e processamento local",
        author="Thiago Nascente, Samuel Machado, Moisés Protázio e Vinícius Benevides",
    )
    doc.build(list(parts(markdown)), onFirstPage=page, onLaterPages=page)


if __name__ == "__main__":
    main()
