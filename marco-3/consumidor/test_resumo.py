"""Verifica tempo, qualidade e publicação com relógio determinístico."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from resumo import Resumo
from sala import Sala
from test_sala import evento, OFFLINE


class TestResumo(unittest.TestCase):
    def setUp(self):
        self.sala = Sala()
        self.resumo = Resumo("sala-204")
        self.inicio = datetime(2026, 10, 15, tzinfo=timezone.utc)

    def avancar(self, t, bruto=None, status=False):
        if bruto is not None:
            metodo = self.sala.receber_status if status else self.sala.receber_evento
            metodo(bruto, t)
        else:
            self.sala.tick(t)
        return self.resumo.avancar(self.sala, t, self.inicio + timedelta(seconds=t))

    def test_reuniao_normal_duracao_e_final(self):
        self.assertEqual(self.avancar(0, evento(1))[0]["ata"], "ABERTA")
        self.resumo.registrar_nivel(-20)
        self.avancar(5, evento(2, "SILENCIO"))
        self.avancar(10, evento(3, "SILENCIO", tipo="device.heartbeat"))
        final = self.avancar(15)[0]
        self.assertTrue(final["final"])
        self.assertEqual((final["duracaoS"], final["falaS"], final["silencioS"]), (15, 5, 10))
        self.assertEqual((final["qualidade"], final["nivelMedioDbfs"]), ("COMPLETA", -20))

    def test_lacuna_incompleta_e_nao_silencio(self):
        self.avancar(0, evento(1))
        self.avancar(1, OFFLINE, status=True)
        final = self.avancar(61)[0]
        self.assertEqual((final["lacunaS"], final["silencioS"]), (60, 0))
        self.assertEqual(final["qualidade"], "INCOMPLETA")
        self.assertTrue(final["avisos"])

    def test_lacuna_curta_menor_que_um_segundo_conta(self):
        self.avancar(0, evento(1))
        self.avancar(10, OFFLINE, status=True)
        saida = self.avancar(10.5, evento(2))[0]
        self.assertEqual(saida["qualidade"], "PARCIAL")
        self.assertEqual(saida["lacunaS"], 0.5)

    def test_pausa_curta_incrementa(self):
        self.avancar(0, evento(1))
        self.avancar(1, evento(2, "SILENCIO"))
        self.assertEqual(self.avancar(3, evento(3))[0]["pausas"], 1)

    def test_periodico_sem_transicao_e_uma_vez(self):
        self.avancar(0, evento(1))
        for n, t in enumerate(range(5, 31, 5), 2):
            saidas = self.avancar(t, evento(n, tipo="device.heartbeat"))
            self.assertEqual(len(saidas), 1 if t == 30 else 0)
        self.assertEqual(saidas[0]["motivo"], "periodico")
        self.assertEqual(self.avancar(30), [])

    def test_janela_recorta_intervalo_parcial(self):
        self.avancar(0, evento(1))
        for n, t in enumerate(range(5, 41, 5), 2):
            self.avancar(t, evento(n, tipo="device.heartbeat"))
        # Mesmo com chamadas esparsas, só os últimos 60 s entram na atividade.
        self.avancar(45, evento(10, "FALHA_SENSOR"))
        saida = self.avancar(100, evento(11, "FALHA_SENSOR"))[0]
        self.assertEqual(saida["atividade"], "BAIXA")  # apenas fala em [40,45]

    def test_nova_sessao_zera_niveis_e_nao_colide(self):
        primeira = self.avancar(0, evento(1))[0]["sessaoId"]
        self.resumo.registrar_nivel(-20)
        self.avancar(1, evento(2, "SILENCIO"))
        self.avancar(6, evento(3, "SILENCIO", tipo="device.heartbeat"))
        self.avancar(11)
        nova = self.avancar(12, evento(4))[0]
        self.assertNotEqual(primeira, nova["sessaoId"])
        self.assertIsNone(nova["nivelMedioDbfs"])
        self.assertEqual(nova["falaS"], 0)


if __name__ == "__main__":
    unittest.main()
