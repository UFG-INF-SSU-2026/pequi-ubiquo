"""Retidos e indisponibilidade nunca podem manter um indicador de ata ativa."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from painel import interpretar


class TestPainel(unittest.TestCase):
    def setUp(self):
        self.agora = datetime.now(timezone.utc)
        self.base = {"schemaVersion": 1, "entityId": "sala-204", "insightType": "reuniao.resumo",
                     "ata": "ABERTA", "final": False, "qualidade": "COMPLETA",
                     "geradoEm": self.agora.isoformat()}

    def interpretar(self, **extras):
        return interpretar(json.dumps(self.base | extras), "sala-204", self.agora)

    def test_resumo_antigo_expira(self):
        texto, validade = self.interpretar(geradoEm=(self.agora - timedelta(seconds=91)).isoformat())
        self.assertIn("desconhecido", texto)
        self.assertIs(validade, False)

    def test_retido_recente_so_ganha_tempo_restante(self):
        _, validade = self.interpretar(geradoEm=(self.agora - timedelta(seconds=80)).isoformat())
        self.assertEqual(validade, 10)

    def test_lwt_sem_data_e_desconhecido(self):
        texto, _ = self.interpretar(motivo="consumidor_offline", geradoEm=None)
        self.assertIn("desconhecido", texto)

    def test_final_antigo_pode_ser_lido(self):
        texto, validade = self.interpretar(ata="FECHADA", final=True,
                                         geradoEm=(self.agora - timedelta(days=1)).isoformat())
        self.assertIn("Resumo final", texto)
        self.assertIs(validade, False)

    def test_versao_sala_timestamp_e_json_invalidos(self):
        for extras in ({"schemaVersion": 2}, {"entityId": "outra"}, {"ata": []},
                       {"geradoEm": "sem data"}, {"final": "false"},
                       {"geradoEm": (self.agora + timedelta(minutes=1)).isoformat()}):
            self.assertIn("desconhecido", self.interpretar(**extras)[0])
        self.assertIn("desconhecido", interpretar(b"invalid", "sala-204", self.agora)[0])
