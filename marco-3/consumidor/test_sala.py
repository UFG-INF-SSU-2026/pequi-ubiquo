"""Testes das regras do consumidor, sem broker.

    cd marco-3/consumidor
    python -m unittest -v
"""
import json
import unittest

from sala import GAP_ATA_S, SILENCIO_DISPOSITIVO_S, SUSPENSA_MAX_S, Sala

OFFLINE = b'{"schemaVersion":1,"deviceId":"mic-esp32-01","status":"offline"}'


def evento(seq, state="FALANDO", tipo="audio.state.changed", boot="b001", attempt=1, **extra):
    e = {
        "schemaVersion": 1,
        "eventId": f"mic-esp32-01:{boot}:{seq}",
        "eventType": tipo,
        "deviceId": "mic-esp32-01",
        "entityId": "sala-204",
        "bootId": boot,
        "eventTimeMs": 1000 * seq,
        "sequence": seq,
        "value": -20.0 if state == "FALANDO" else -60.0,
        "unit": "dBFS",
        "state": state,
        "reason": "teste",
        "attempt": attempt,
    }
    e.update(extra)
    return json.dumps(e).encode()


class TestValidacao(unittest.TestCase):
    def setUp(self):
        self.sala = Sala()

    def test_json_invalido_e_rejeitado_sem_ack(self):
        r = self.sala.receber_evento(b"{nao e json", 0)
        self.assertEqual(r.status, "REJEITADO")
        self.assertIsNone(r.ack)

    def test_versao_desconhecida_e_rejeitada_com_ack(self):
        r = self.sala.receber_evento(evento(1, schemaVersion=2), 0)
        self.assertEqual(r.status, "REJEITADO")
        self.assertEqual(r.ack["result"], "rejeitado")

    def test_campo_faltando_e_rejeitado(self):
        e = json.loads(evento(1))
        del e["sequence"]
        r = self.sala.receber_evento(json.dumps(e).encode(), 0)
        self.assertEqual(r.status, "REJEITADO")
        self.assertIn("sequence", r.motivo)

    def test_valor_fora_da_faixa_nao_muda_estado(self):
        r = self.sala.receber_evento(evento(1, value=12.0), 0)
        self.assertEqual(r.status, "REJEITADO")
        self.assertEqual(self.sala.mic, "DESCONHECIDO")

    def test_device_id_com_curinga_mqtt_nao_gera_ack(self):
        r = self.sala.receber_evento(evento(1, deviceId="mic/#"), 0)
        self.assertEqual(r.status, "REJEITADO")
        self.assertIsNone(r.ack)


class TestEntrega(unittest.TestCase):
    def setUp(self):
        self.sala = Sala()

    def test_retry_com_mesmo_event_id_nao_aplica_de_novo(self):
        r1 = self.sala.receber_evento(evento(1), 0)
        r2 = self.sala.receber_evento(evento(1, attempt=2), 1)
        self.assertEqual((r1.status, r1.ack["result"]), ("APLICADO", "aplicado"))
        self.assertEqual((r2.status, r2.ack["result"]), ("DUPLICADO", "duplicado"))
        self.assertEqual(self.sala.contadores["aplicados"], 1)
        self.assertEqual(r2.transicoes, [])

    def test_estado_antigo_nao_reescreve_o_presente(self):
        self.sala.receber_evento(evento(5, "SILENCIO"), 0)
        r = self.sala.receber_evento(evento(3, "FALANDO"), 1)
        self.assertEqual(r.status, "ATRASADO")
        self.assertEqual(r.ack["result"], "atrasado")
        self.assertEqual(self.sala.mic, "SILENCIO")

    def test_heartbeat_ressincroniza_e_estado_da_fila_chega_atrasado(self):
        self.sala.receber_evento(evento(1, "SILENCIO"), 0)
        r = self.sala.receber_evento(evento(4, "FALANDO", tipo="device.heartbeat"), 1)
        self.assertEqual(r.status, "TELEMETRIA")
        self.assertIsNone(r.ack)
        self.assertEqual(self.sala.mic, "FALANDO")
        r = self.sala.receber_evento(evento(2, "FALANDO"), 2)  # saiu da fila offline
        self.assertEqual(r.status, "ATRASADO")

    def test_reinicio_do_dispositivo_zera_a_sequencia(self):
        self.sala.receber_evento(evento(40, "SILENCIO", boot="b001"), 0)
        r = self.sala.receber_evento(evento(1, "FALANDO", boot="b002"), 1)
        self.assertEqual(r.status, "APLICADO")
        self.assertTrue(any("reinicio" in t for t in r.transicoes))


class TestAta(unittest.TestCase):
    def setUp(self):
        self.sala = Sala()

    def test_fala_abre_e_silencio_fecha_depois_da_janela(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        self.assertEqual(self.sala.ata, "ABERTA")
        self.sala.receber_evento(evento(2, "SILENCIO"), 1)
        self.assertEqual(self.sala.ata, "ENCERRANDO")
        self.sala.receber_evento(evento(3, "SILENCIO", tipo="device.heartbeat"), 6)
        self.sala.tick(1 + GAP_ATA_S - 0.5)
        self.assertEqual(self.sala.ata, "ENCERRANDO")
        self.sala.tick(1 + GAP_ATA_S)
        self.assertEqual(self.sala.ata, "FECHADA")

    def test_pausa_curta_nao_fecha(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        self.sala.receber_evento(evento(2, "SILENCIO"), 1)
        self.sala.receber_evento(evento(3, "FALANDO"), 3)
        self.sala.tick(12)
        self.assertEqual(self.sala.ata, "ABERTA")

    def test_falha_do_sensor_suspende_em_vez_de_fechar(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        self.sala.receber_evento(evento(2, "FALHA_SENSOR"), 1)
        self.assertEqual(self.sala.ata, "SUSPENSA")
        self.sala.tick(1 + GAP_ATA_S + 1)
        self.assertEqual(self.sala.ata, "SUSPENSA")

    def test_dispositivo_silencioso_expira_o_dado(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        self.sala.tick(SILENCIO_DISPOSITIVO_S + 1)
        self.assertFalse(self.sala.online)
        self.assertEqual(self.sala.mic, "DESCONHECIDO")
        self.assertEqual(self.sala.ata, "SUSPENSA")

    def test_last_will_suspende_na_hora(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        r = self.sala.receber_status(OFFLINE, 1)
        self.assertEqual(r.status, "STATUS")
        self.assertEqual(self.sala.ata, "SUSPENSA")

    def test_suspensa_por_muito_tempo_fecha(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        self.sala.receber_status(OFFLINE, 1)
        self.sala.tick(1 + SUSPENSA_MAX_S - 1)
        self.assertEqual(self.sala.ata, "SUSPENSA")
        self.sala.tick(1 + SUSPENSA_MAX_S)
        self.assertEqual(self.sala.ata, "FECHADA")

    def test_dispositivo_volta_e_ata_reabre(self):
        self.sala.receber_evento(evento(1, "FALANDO"), 0)
        self.sala.receber_status(OFFLINE, 1)
        r = self.sala.receber_evento(evento(2, "FALANDO", tipo="device.heartbeat"), 5)
        self.assertIn("dispositivo online", r.transicoes)
        self.assertEqual(self.sala.ata, "ABERTA")


if __name__ == "__main__":
    unittest.main()
