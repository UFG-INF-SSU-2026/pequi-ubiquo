import io
import json
import unittest
from unittest.mock import patch

from models import LocalMinutes, MODELS, validate_minutes


class ModelsTest(unittest.TestCase):
    @staticmethod
    def model_reply(summary, task="Revisar a ata", due=""):
        minutes = {"summary": summary, "decisions": [],
                   "action_items": [{"task": task, "owner": "Ana", "due": due}]}
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(minutes)}}]}).encode())

    def test_ata_repetida_quando_modelo_responde_em_ingles(self):
        model = LocalMinutes("teste", "http://127.0.0.1:1234")
        wrong = self.model_reply("Ana tested the notebook before the meeting and will read the minutes.",
                                 "Read the minutes and export them", "immediate")
        corrected = self.model_reply("Ana testou o notebook e vai ler a ata depois da reunião.", "Ler e exportar a ata")
        with patch.object(model.http, "open", side_effect=[wrong, corrected]) as request:
            minutes = model([{"text": "Ana testou o notebook. Vou ler e exportar a ata."}])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(minutes["action_items"][0]["task"], "Ler e exportar a ata")
        self.assertEqual(minutes["action_items"][0]["due"], "")

    def test_ata_rejeitada_quando_tarefa_permanece_em_ingles(self):
        model = LocalMinutes("teste", "http://127.0.0.1:1234")
        replies = [self.model_reply("Ana testou o notebook e encerrou a reunião.", "Read the minutes and export them")
                   for _ in range(2)]
        with patch.object(model.http, "open", side_effect=replies) as request:
            with self.assertRaisesRegex(ValueError, "português do Brasil"):
                model([{"text": "A Ana encerrou a reunião e ficou de ler e exportar a ata."}])
        self.assertEqual(request.call_count, 2)

    def test_ata_em_portugues_aceita_termos_tecnicos_e_campos_curtos(self):
        model = LocalMinutes("teste", "http://127.0.0.1:1234")
        reply = self.model_reply("A equipe revisou o deploy no Windows e os testes no Ubuntu.", "Testar o deploy", "sexta")
        with patch.object(model.http, "open", return_value=reply) as request:
            minutes = model([{"text": "Revimos o deploy no Windows. Ana ficou de testar até sexta."}])
        request.assert_called_once()
        self.assertEqual(minutes["action_items"][0]["due"], "sexta")

    def test_ata_repete_quando_so_o_prazo_curto_esta_em_ingles(self):
        model = LocalMinutes("teste", "http://127.0.0.1:1234")
        summary = "Ana vai revisar o manual e conferir o resultado."
        replies = [self.model_reply(summary, due=due) for due in ("Friday", "sexta")]
        with patch.object(model.http, "open", side_effect=replies) as request:
            minutes = model([{"text": "A Ana vai revisar a ata até sexta-feira."}])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(minutes["action_items"][0]["due"], "sexta")

    def test_transcricao_aceita_apenas_whisper_large_v3_e_turbo(self):
        self.assertEqual(MODELS, {"large-v3": "large-v3", "large-v3-turbo": "turbo"})

    def test_formato_da_ata_e_campos_ausentes_na_transcricao(self):
        minutes = validate_minutes({
            "summary": "A equipe discutiu os testes.",
            "decisions": [],
            "action_items": [{"task": "Repetir os testes", "owner": "Não informado", "due": ""}],
        })
        self.assertEqual(minutes["action_items"], [
            {"task": "Repetir os testes", "owner": "", "due": "", "done": False},
        ])
        with self.assertRaises(ValueError):
            validate_minutes({"summary": "Teste", "decisions": "Repetir", "action_items": []})

    def test_texto_so_pode_ser_enviado_a_um_modelo_de_loopback(self):
        with self.assertRaises(ValueError):
            LocalMinutes("teste", "https://example.invalid")
        with self.assertRaises(ValueError):
            LocalMinutes("teste", "http://127.0.0.1:1234@host-externo.invalid")
        LocalMinutes("teste", "http://127.0.0.1:1234")
