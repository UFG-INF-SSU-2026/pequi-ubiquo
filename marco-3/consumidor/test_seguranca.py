"""A identidade do canal prevalece sobre qualquer campo do remetente."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_seguro import dispositivo_do_topico
from sala import Sala
from test_sala import evento


class TestIdentidade(unittest.TestCase):
    def test_payload_excessivo_nao_gera_ack(self):
        sala = Sala()
        self.assertIsNone(sala.receber_evento(b" " * 4097, 0).ack)
        self.assertEqual(sala.receber_status(b" " * 1025, 0).status, "REJEITADO")

    def test_payload_finge_outro_dispositivo_sem_ack(self):
        sala = Sala()
        r = sala.receber_evento(evento(1, deviceId="outro"), 0, "mic-esp32-01")
        self.assertEqual(r.status, "REJEITADO")
        self.assertIsNone(r.ack)
        self.assertFalse(sala.online)
        self.assertEqual(sala.ata, "FECHADA")

    def test_versao_errada_nao_desvia_ack(self):
        r = Sala().receber_evento(evento(1, schemaVersion=2, deviceId="outro"), 0, "mic-esp32-01")
        self.assertIsNone(r.ack)

    def test_status_falso_nao_suspende(self):
        sala = Sala()
        sala.receber_evento(evento(1), 0, "mic-esp32-01")
        r = sala.receber_status(json.dumps({"schemaVersion": 1, "deviceId": "outro", "status": "offline"}),
                                1, "mic-esp32-01")
        self.assertEqual(r.status, "REJEITADO")
        self.assertEqual(sala.ata, "ABERTA")

    def test_versao_status_ou_identidade_ausente_rejeitada(self):
        for carga in ({"status": "offline"}, {"schemaVersion": 2, "deviceId": "mic-esp32-01", "status": "offline"}):
            self.assertEqual(Sala().receber_status(json.dumps(carga), 0).status, "REJEITADO")

    def test_parse_exato_do_topico(self):
        base = "ufg-ssu-2026/towerai/sala-204"
        self.assertEqual(dispositivo_do_topico(base + "/dev/mic-01/telemetry", base), ("mic-01", "telemetry"))
        for sufixo in ("/dev/mic-01/telemetry/extra", "/dev/+/status", "/dev/mic-01/ack", "/dev/mic-01/status\n"):
            self.assertIsNone(dispositivo_do_topico(base + sufixo, base))
        self.assertIsNone(dispositivo_do_topico("outra/dev/mic-01/status", base))
