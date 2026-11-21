"""Exporta o histórico da reunião como PDF com a gravação WAV em anexo."""

import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape

import reportlab
from pypdf import PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from towerai import Database


AUDIO_ATTACHMENT = "audio-da-reuniao.wav"
PROCESSING_LABELS = {
    "PENDENTE": "Na fila de processamento",
    "TRANSCREVENDO": "Transcrevendo no notebook",
    "GERANDO_ATA": "Gerando a ata",
    "PRONTA": "Ata disponível no histórico",
    "FALHOU": "Falha no processamento",
}


def meeting_date(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone()


def audio_offset(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}:{seconds:02d}"


def save_meeting_pdf(database, capture_id, destination):
    destination = Path(destination)
    if destination.suffix.lower() != ".pdf":
        raise ValueError("Use a extensão .pdf para salvar o histórico da reunião.")
    result = Database.export_meeting(database, capture_id)
    if result is None:
        raise ValueError("Esta reunião não está mais disponível. Atualize o histórico e selecione outra reunião.")
    meeting, audio = result
    with TemporaryDirectory(dir=destination.parent) as staging:
        temporary = Path(staging) / "reuniao.pdf"
        render_meeting(temporary, meeting, has_audio=audio is not None)
        if audio is not None:
            writer = PdfWriter(clone_from=temporary)
            writer.add_attachment(AUDIO_ATTACHMENT, audio)
            temporary = Path(staging) / "reuniao-com-audio.pdf"
            writer.write(temporary)
        temporary.replace(destination)


def render_meeting(path, meeting, *, has_audio):
    fonts = Path(reportlab.__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("Ata-Regular", str(fonts / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("Ata-Bold", str(fonts / "VeraBd.ttf")))
    ink = colors.HexColor("#221C31")
    muted = colors.HexColor("#514761")
    accent = colors.HexColor("#504084")
    body = ParagraphStyle("Corpo", fontName="Ata-Regular", fontSize=10.5, leading=16,
                          textColor=ink, spaceAfter=8, alignment=TA_LEFT, splitLongWords=True)
    title = ParagraphStyle("Título", parent=body, fontName="Ata-Bold", fontSize=21, leading=26, spaceAfter=10)
    heading = ParagraphStyle("Seção", parent=body, fontName="Ata-Bold", fontSize=12, leading=17,
                             textColor=accent, spaceBefore=12, spaceAfter=5, keepWithNext=True)
    metadata = ParagraphStyle("Dados", parent=body, fontSize=9, leading=14, textColor=muted, spaceAfter=3)

    def paragraph(value, style=body):
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
        text = escape(text.replace("\r\n", "\n").replace("\r", "\n"))
        return Paragraph(text.replace("\n", "<br/>"), style)

    started = meeting_date(meeting["startedAt"])
    ended = meeting_date(meeting["endedAt"])
    minutes = meeting["minutes"]
    story = [
        paragraph("Histórico da reunião", metadata), paragraph(meeting["title"], title),
        paragraph(f"Sala: {meeting['room']}", metadata),
        paragraph(f"Início: {started:%d/%m/%Y %H:%M:%S %z}  |  Fim: {ended:%d/%m/%Y %H:%M:%S %z}", metadata),
        paragraph(f"Status: {PROCESSING_LABELS.get(meeting['processing'], 'Encerrada')}", metadata),
        paragraph(f"Duração do áudio registrado: {audio_offset(meeting['audioSeconds'])}", metadata),
    ]
    quality = "Captura incompleta. O áudio pode ter lacunas." if meeting["quality"] == "INCOMPLETA" else "Captura completa."
    story.append(paragraph(quality, metadata))
    if meeting["presenceVerified"] is False:
        story.append(paragraph("Presença não verificada na abertura.", metadata))
    if meeting["processing"] == "FALHOU" and meeting["error"]:
        story.append(paragraph(meeting["error"], metadata))
    story.extend([Spacer(1, 5), paragraph("Gravação original", heading)])
    if has_audio:
        story.append(paragraph(f"Na lista de anexos do leitor de PDF, abra {AUDIO_ATTACHMENT}."))
    else:
        story.append(paragraph("Gravação WAV indisponível no histórico salvo."))

    story.append(paragraph("Ata", heading))
    if minutes is None:
        story.append(paragraph("Ata indisponível."))
    else:
        story.extend([paragraph(minutes["summary"]), paragraph("Decisões", heading)])
        for number, decision in enumerate(minutes["decisions"], 1):
            story.append(paragraph(f"{number}. {decision}"))
        if not minutes["decisions"]:
            story.append(paragraph("Nenhuma decisão registrada."))

        story.append(paragraph("Ações", heading))
        for number, action in enumerate(minutes["action_items"], 1):
            owner = action["owner"] or "Não informado"
            due = action["due"] or "Não informado"
            status = "Concluída" if action.get("done") else "Pendente"
            story.append(paragraph(f"{number}. {action['task']}\nResponsável: {owner}  |  Prazo: {due}  |  {status}"))
        if not minutes["action_items"]:
            story.append(paragraph("Nenhuma ação registrada."))

    story.append(paragraph("Transcrição", heading))
    if not meeting["transcript"]:
        story.append(paragraph("Nenhum trecho disponível."))
    for segment in meeting["transcript"]:
        story.append(paragraph(f"{audio_offset(segment['start'])}  {segment['text']}"))

    document = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=49, rightMargin=49,
                                 topMargin=48, bottomMargin=54, title=f"Reunião - {meeting['title']}", author="TowerAI")

    def page_footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor("#DDD5E7"))
        canvas.line(doc.leftMargin, 42, A4[0] - doc.rightMargin, 42)
        canvas.setFont("Ata-Regular", 8)
        canvas.setFillColor(muted)
        canvas.drawString(doc.leftMargin, 28, "TowerAI Servidor")
        canvas.drawRightString(A4[0] - doc.rightMargin, 28, f"Página {doc.page}")

    document.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
