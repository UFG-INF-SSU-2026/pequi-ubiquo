import unittest

from models import LocalMinutes, MODELS, validate_minutes


class ModelsTest(unittest.TestCase):
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
