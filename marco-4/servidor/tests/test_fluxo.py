"""Integração na API pública, com relógio e modelos substituídos em teste."""

import hashlib
import json
import ssl
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from pki import issue, tls_context
from towerai import Api, Database, Device, MeetingProcessor, Policy, Server


LOUD = struct.pack("<h", 8000) * 16000
QUIET = bytes(32000)


class FluxoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tower.db"
        self.tls = Path(self.temp.name) / "tls"
        self.ca = issue(self.tls, ["127.0.0.1"])
        self.context = ssl.create_default_context(cafile=self.ca)
        self.now = 1000.0
        self.policy = Policy()
        self.new_server()
        self.capture = uuid4().hex

    def new_server(self):
        self.db = Database(self.path, self.policy, clock=lambda: self.now)
        devices = {
            hashlib.sha256(token.encode()).hexdigest(): Device(token, role, room)
            for token, role, room in (
                ("mic", "microphone", "sala-204"), ("cam", "presence", "sala-204"),
                ("vizinho", "microphone", "sala-205"), ("outro", "microphone", "sala-204"),
            )
        }
        server = Server(("127.0.0.1", 0), Api(self.db, devices), tls_context(self.tls))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.server = server
        self.thread = thread
        self.addCleanup(self.close_server, server, thread)

    @staticmethod
    def close_server(server, thread):
        if thread.is_alive():
            server.shutdown()
            server.server_close()
            thread.join()

    def request(self, method, path, body=None, token="mic", captured_at=None):
        binary = isinstance(body, bytes)
        payload = body if binary or body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"https://127.0.0.1:{self.server.server_port}/v1{path}",
            method=method, data=payload,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/octet-stream" if binary else "application/json",
                     "X-Captured-At": str(self.now if captured_at is None else captured_at)},
        )
        try:
            response = urllib.request.urlopen(request, timeout=3, context=self.context)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def start(self):
        return self.request("PUT", f"/captures/{self.capture}", {"title": "Planejamento"})

    def presence(self, count, *, sent_at=None, event_id=None):
        return self.request("POST", "/presence", {
            "eventId": event_id or uuid4().hex,
            "observedAt": self.now if sent_at is None else sent_at, "people": count,
        }, token="cam")

    def chunk(self, number, pcm=LOUD, token="mic"):
        return self.request("PUT", f"/captures/{self.capture}/chunks/{number}", pcm, token)

    def current(self):
        return self.request("GET", f"/captures/{self.capture}")[1]

    def test_dois_produtores_mudam_a_decisao_e_ata_sobrevive_ao_reinicio(self):
        self.assertEqual(self.start()[0], 201)
        self.assertEqual(self.presence(0)[0], 200)
        for number in range(3):
            self.assertEqual(self.chunk(number)[1]["state"], "AGUARDANDO")
        self.now += 1
        self.assertEqual(self.presence(2)[1]["ack"], "aplicado")
        for number in range(3, 6):
            self.chunk(number)
        self.assertEqual(self.current()["state"], "ABERTA")
        self.assertEqual(self.chunk(5)[1]["ack"], "duplicado")
        self.assertEqual(self.chunk(5, QUIET)[0], 409)
        for number in range(6, 16):
            self.chunk(number, QUIET)
        self.assertEqual(self.current()["state"], "FECHADA")

        def transcribe(path):
            import wave
            with wave.open(str(path)) as audio:
                self.assertEqual(audio.getframerate(), 16000)
                self.assertEqual(audio.getnframes(), 13 * 16000)
            return [{"start": 0.0, "end": 2.0, "text": "Ana envia o relatório na sexta."}]

        processor = MeetingProcessor(
            self.db, transcribe,
            lambda segments: {"summary": segments[0]["text"], "decisions": [],
                              "action_items": [{"task": "Enviar o relatório", "owner": "Ana",
                                                "due": "sexta", "done": False}]},
        )
        self.assertTrue(processor.run_once())
        self.close_server(self.server, self.thread)
        self.new_server()
        history = self.request("GET", "/meetings")[1]["meetings"]
        self.assertEqual([entry["id"] for entry in history], [self.capture])
        result = self.current()
        self.assertEqual(result["processing"], "PRONTA")
        self.assertEqual(result["minutes"]["action_items"][0]["owner"], "Ana")
        self.assertEqual(result["transcript"][0]["text"], "Ana envia o relatório na sexta.")

    def test_identidade_e_autorizacao(self):
        self.assertEqual(self.request("GET", "/status", token="incorreta")[0], 401)
        self.assertEqual(self.request("GET", "/meetings", token="cam")[0], 403)
        self.start()
        self.assertIsNone(self.request("GET", "/status", token="cam")[1]["capture"])
        self.assertEqual(self.chunk(0, token="vizinho")[0], 404)
        self.assertEqual(self.chunk(0, token="outro")[0], 403)
        self.assertEqual(self.chunk(0, token="cam")[0], 403)
        self.assertEqual(self.request("POST", "/presence", {}, token="mic")[0], 403)
        self.assertEqual(self.request("GET", "/status?room=sala-205")[1]["room"], "sala-204")

    def test_tls_recusa_ca_desconhecida_e_nome_ausente_no_certificado(self):
        for host, context in (("127.0.0.1", ssl.create_default_context()), ("localhost", self.context)):
            with self.subTest(host=host):
                with self.assertRaises(urllib.error.URLError) as caught:
                    urllib.request.urlopen(
                        f"https://{host}:{self.server.server_port}/v1/status", timeout=3, context=context,
                    )
                self.assertIsInstance(caught.exception.reason, ssl.SSLCertVerificationError)
        self.assertEqual(self.request("GET", "/status")[0], 200)

    def test_queda_da_rede_suspende_e_reenvio_recupera_sem_duplicar(self):
        self.start()
        for number in range(3):
            self.chunk(number)
        self.assertEqual(self.current()["state"], "ABERTA")
        self.now += self.policy.audio_timeout + 1
        self.assertEqual(self.current()["state"], "SUSPENSA")
        self.assertEqual(self.chunk(2)[1]["state"], "SUSPENSA")
        self.assertEqual(self.chunk(4)[0], 409)
        self.assertEqual(self.chunk(3)[1]["state"], "ABERTA")
        self.assertEqual(self.chunk(3)[1]["ack"], "duplicado")
        self.now += self.policy.audio_timeout + self.policy.suspension_timeout + 1
        self.assertEqual(self.current()["state"], "FECHADA")
        self.assertEqual(self.current()["quality"], "INCOMPLETA")

    def test_audio_atrasado_vai_ao_historico_sem_reabrir_contexto(self):
        self.start()
        for number in range(3):
            self.chunk(number)
        capture_time = self.now
        self.now += self.policy.audio_timeout + 1
        _, replay = self.request("PUT", f"/captures/{self.capture}/chunks/3", LOUD, captured_at=capture_time)
        self.assertEqual((replay["ack"], replay["state"], replay["audioSeconds"]), ("arquivado", "SUSPENSA", 4))
        self.assertEqual(self.chunk(4)[1]["state"], "ABERTA")

    def test_falha_da_ata_preserva_transcricao_e_reprocessa_so_a_ata(self):
        self.start()
        for number in range(3):
            self.chunk(number)
        finish = f"/captures/{self.capture}/finish"
        self.assertEqual(self.request("PUT", finish)[1]["state"], "FECHADA")
        self.assertEqual(self.request("PUT", finish)[1]["processing"], "PENDENTE")
        segments = [{"start": 0.0, "end": 2.0, "text": "Reunião de teste."}]

        def offline(_):
            raise TimeoutError("modelo indisponível")

        worker = MeetingProcessor(self.db, lambda _: segments, offline, log=lambda _: None)
        self.assertTrue(worker.run_once())
        self.assertEqual(self.current()["processing"], "FALHOU")
        self.assertEqual(self.current()["transcript"], segments)
        self.assertEqual(self.request("PUT", f"/captures/{self.capture}/retry")[0], 202)

        def must_not_transcribe(_):
            self.fail("Uma transcrição já persistida não deve ser refeita.")

        worker = MeetingProcessor(self.db, must_not_transcribe, lambda _: {
            "summary": "Houve uma reunião de teste.", "decisions": [], "action_items": [],
        })
        self.assertTrue(worker.run_once())
        self.assertEqual(self.current()["processing"], "PRONTA")

    def test_presenca_expira_e_amostra_atrasada_nao_reescreve_o_contexto(self):
        self.start()
        self.presence(2)
        self.now += 1
        self.assertEqual(self.presence(0, sent_at=self.now - 2)[1]["ack"], "atrasado")
        self.assertEqual(self.request("GET", "/status")[1]["presence"]["people"], 2)
        self.now += self.policy.presence_ttl + 1
        self.assertIsNone(self.request("GET", "/status")[1]["presence"]["people"])
        for number in range(3):
            self.chunk(number)
        self.assertEqual(self.current()["state"], "ABERTA")
        self.assertFalse(self.current()["presenceVerified"])
