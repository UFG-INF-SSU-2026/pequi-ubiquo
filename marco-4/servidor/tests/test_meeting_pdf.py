import io
import json
import struct
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader

from meeting_pdf import AUDIO_ATTACHMENT, save_meeting_pdf
from towerai import RATE, Database, Device, MeetingProcessor


class MeetingExportTest(unittest.TestCase):
    def test_missing_database_keeps_new_installation_empty(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "local" / "towerai.sqlite3"
            self.assertEqual(Database.exportable_meetings(database), [])
            self.assertFalse(database.parent.exists())

    def test_reads_legacy_history_and_writes_pdf_with_paginated_transcript(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "histórico #1.sqlite3"
            destination = Path(folder) / "ata.pdf"
            existing = Database(database)
            minutes = {
                "summary": "Revisão do projeto & publicação <prevista>.",
                "decisions": ["Publicar o manual na sexta-feira."],
                "action_items": [
                    {"task": f"Revisar a seção {i} do manual.", "owner": "João", "due": "", "done": False}
                    for i in range(60)
                ],
            }
            ready = json.dumps(minutes, ensure_ascii=False)
            segments = json.dumps([{"start": 65.9, "end": 68.2, "text": "João confirmou & revisará <o manual>."}])
            with existing.connection(write=True) as connection:
                connection.executemany(
                    "INSERT INTO captures (id,room,owner,title,created_at,last_audio,started_at,ended_at,state,"
                    "processing,minutes,transcript) VALUES (?,?,'android','Revisão de implantação',10,10,?,20,'FECHADA',?,?,?)",
                    [
                        ("ready-old", "sala-a", 11, "PRONTA", ready, segments),
                        ("ready-new", "sala-b", 15, "PRONTA", ready, segments),
                        ("failed", "sala-b", 16, "FALHOU", None, segments),
                        ("discarded", "sala-b", None, "DESCARTADA", None, None),
                    ],
                )
            rows = Database.exportable_meetings(database)
            self.assertEqual([row["id"] for row in rows], ["failed", "ready-new", "ready-old"])
            self.assertNotIn("minutes", rows[0])
            previous = database.read_bytes()
            save_meeting_pdf(database, "ready-new", destination)
            output = destination.read_bytes()
            reader = PdfReader(io.BytesIO(output))
            self.assertGreater(len(reader.pages), 1)
            text = "\n".join(page.extract_text() for page in reader.pages)
            self.assertIn("1:05 João confirmou & revisará <o manual>.", text)
            self.assertIn("Revisão do projeto & publicação <prevista>.", text)
            self.assertIn("Gravação WAV indisponível", text)
            self.assertFalse(reader.attachments)
            with self.assertRaisesRegex(ValueError, "não está mais disponível"):
                save_meeting_pdf(database, "discarded", destination)
            self.assertEqual(destination.read_bytes(), output)
            save_meeting_pdf(database, "failed", destination)
            failed = PdfReader(destination).pages[0].extract_text()
            self.assertIn("Falha no processamento", failed)
            self.assertIn("Ata indisponível.", failed)
            self.assertIn("João confirmou", failed)
            self.assertEqual(database.read_bytes(), previous)

    def test_retains_pcm_after_processing_and_embeds_the_exact_recording(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "towerai.sqlite3"
            destination = Path(folder) / "reuniao.pdf"
            server = Database(database, clock=lambda: 100)
            device = Device("android", "microphone", "sala-a")
            capture = "a" * 32
            server.start(device, capture, "Reunião de teste")
            blocks = [struct.pack("<h", 1000 + number) * RATE for number in range(3)]
            for sequence, pcm in enumerate(blocks):
                server.add_audio(device, capture, sequence, pcm, 100)
            server.finish(device, capture)
            minutes = {"summary": "Decisão aprovada.", "decisions": [], "action_items": []}
            processor = MeetingProcessor(
                server, lambda _: [{"start": 0, "end": 3, "text": "Vamos seguir."}], lambda _: minutes,
            )
            self.assertTrue(processor.run_once())

            Database(database)  # Reabre o banco após o fim do processamento.
            save_meeting_pdf(database, capture, destination)
            reader = PdfReader(destination)
            text = reader.pages[0].extract_text()
            self.assertIn("Presença não verificada na abertura.", text)
            self.assertIn("Ata disponível no histórico", text)
            self.assertIn("Duração do áudio registrado: 0:03", text)
            self.assertIn("0:00 Vamos seguir.", text)
            self.assertIn(AUDIO_ATTACHMENT, text)
            with wave.open(io.BytesIO(reader.attachments[AUDIO_ATTACHMENT][0]), "rb") as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, RATE))
                self.assertEqual(wav.readframes(wav.getnframes()), b"".join(blocks))
