import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from towerai import Api, Database, Device
from desktop_runtime import WHISPER_FILES, Configuration, DesktopServer, download_models


class DesktopRuntimeTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.folder = Path(workspace.name)
        self.config = Configuration(self.folder / "instalado")

    def test_certificado_e_historico_da_instalacao_anterior_sao_reutilizados(self):
        old = Configuration(self.folder / "anterior")
        old.issue_certificate("192.168.10.20", "Ethernet")
        DesktopServer(old).provision("android-01")
        Database(old.database).start(Device("android-01", "microphone", old.settings.room), "a" * 32, "Reunião anterior")

        # A execução anterior usa o nome fixo local/tls.
        old.tls.rename(old.local / "tls")
        self.config.import_installation(old.local)
        self.config.issue_certificate("192.168.10.20", "Ethernet")
        self.assertEqual(self.config.tls.name, "tls")
        self.assertEqual(self.config.microphones(), ["android-01"])
        with Database(self.config.database).connection() as db:
            self.assertEqual(db.execute("SELECT title FROM captures").fetchone()[0], "Reunião anterior")
        fingerprint = self.config.fingerprint()
        self.config.issue_certificate("192.168.10.25", "Ethernet")
        self.assertNotEqual(fingerprint, self.config.fingerprint())
        self.assertTrue(self.config.certificate_matches("192.168.10.25"))
        self.assertFalse(self.config.certificate_matches("192.168.10.20"))

    def test_painel_acompanha_microfone_da_sala_e_estado_da_ata(self):
        runtime = DesktopServer(self.config)
        database = Database(self.config.database)
        runtime.server = SimpleNamespace(api=Api(database, {}, runtime.mark_activity))
        runtime.http_thread = SimpleNamespace(is_alive=lambda: True)
        room = self.config.settings.room
        microphone = Device("android-01", "microphone", room)
        runtime.mark_activity(Device("android-02", "microphone", "outra"))
        self.assertIsNone(runtime.dashboard()[3])
        runtime.mark_activity(microphone)
        self.assertIsNotNone(runtime.dashboard()[3])

        runtime.publish_presence(database.clock(), 2)
        self.assertEqual(database.status(microphone)["presence"]["people"], 2)
        database.start(microphone, "b" * 32, "Reunião ao vivo")
        self.assertEqual(runtime.dashboard()[0]["state"], "AGUARDANDO")
        with database.connection(write=True) as db:
            db.execute("UPDATE captures SET state='FECHADA', processing='PENDENTE', ended_at=?", (database.clock(),))
        self.assertEqual(runtime.dashboard()[2], 1)
        database.claim_job()
        self.assertEqual(runtime.dashboard()[1]["processing"], "TRANSCREVENDO")
        database.save_transcript("b" * 32, [])
        self.assertEqual(runtime.dashboard()[1]["processing"], "GERANDO_ATA")
        database.save_minutes("b" * 32, {"summary": "Teste"})
        self.assertEqual(runtime.dashboard()[1]["processing"], "PRONTA")

    def test_download_reaproveita_whisper_e_so_publica_gguf_verificado(self):
        model = b"GGUF-modelo-de-teste"
        digest = hashlib.sha256(model).hexdigest()

        def whisper(*args, local_dir, **kwargs):
            local_dir.mkdir(parents=True)
            for name in WHISPER_FILES:
                (local_dir / name).touch()

        def qwen(*args, local_dir, **kwargs):
            local_dir.mkdir(parents=True, exist_ok=True)
            path = local_dir / self.config.text.name
            path.write_bytes(model)
            return path

        messages = Mock()
        with patch("huggingface_hub.snapshot_download", side_effect=whisper) as snapshot:
            with patch("huggingface_hub.hf_hub_download", side_effect=qwen):
                with patch("desktop_runtime.QWEN_SHA256", "hash-incorreto"):
                    download_models(self.config.folder, messages)
                self.assertTrue(self.config.whisper_ready)
                self.assertFalse(self.config.text.exists())
                self.assertEqual(messages.send.call_args.args[0][0], "error")

                with patch("desktop_runtime.QWEN_SHA256", digest):
                    download_models(self.config.folder, messages)
                self.assertTrue(self.config.models_ready)
                self.assertEqual(self.config.text.read_bytes(), model)
                self.assertEqual(messages.send.call_args.args[0][0], "done")
            snapshot.assert_called_once()
