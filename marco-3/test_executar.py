"""Distingue falhas de acesso ao Docker das falhas do Compose, sem iniciar serviços."""
import subprocess
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import executar


class TestVerificarDocker(unittest.TestCase):
    def setUp(self):
        self.which = patch("executar.shutil.which", return_value="docker").start()
        self.rodar = patch("executar.rodar").start()
        self.addCleanup(patch.stopall)

    def test_docker_ausente(self):
        self.which.return_value = None
        with self.assertRaisesRegex(executar.Orientacao, "Docker não encontrado"):
            executar.verificar_docker()
        self.rodar.assert_not_called()

    def test_permissao_negada_preserva_causa(self):
        detalhe = "permission denied while trying to connect to unix:///var/run/docker.sock"
        self.rodar.side_effect = subprocess.CalledProcessError(1, ["docker", "info"], stderr=detalhe)
        with self.assertRaises(executar.Orientacao) as contexto:
            executar.verificar_docker()
        self.assertIn("não tem permissão", str(contexto.exception))
        self.assertIn(detalhe, str(contexto.exception))
        self.assertNotIn("Abra o Docker Desktop", str(contexto.exception))
        self.assertEqual(self.rodar.call_count, 1)

    def test_motor_indisponivel_linux(self):
        detalhe = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
        self.rodar.side_effect = subprocess.CalledProcessError(1, ["docker", "info"], stderr=detalhe)
        with patch("executar.os.name", "posix"):
            with self.assertRaises(executar.Orientacao) as contexto:
                executar.verificar_docker()
        self.assertIn("systemctl status docker", str(contexto.exception))
        self.assertIn(detalhe, str(contexto.exception))

    def test_motor_indisponivel_windows(self):
        detalhe = "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."
        self.rodar.side_effect = subprocess.CalledProcessError(1, ["docker", "info"], stderr=detalhe)
        with patch("executar.os.name", "nt"):
            with self.assertRaises(executar.Orientacao) as contexto:
                executar.verificar_docker()
        self.assertIn("Abra o Docker Desktop", str(contexto.exception))
        self.assertIn(detalhe, str(contexto.exception))

    def test_exige_motor_linux(self):
        self.rodar.return_value = subprocess.CompletedProcess([], 0, stdout="windows\n")
        with self.assertRaisesRegex(executar.Orientacao, "mude para containers Linux"):
            executar.verificar_docker()
        self.assertEqual(self.rodar.call_count, 1)

    def test_compose_ausente_nao_acusa_motor_parado(self):
        detalhe = "docker: 'compose' is not a docker command."
        self.rodar.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="linux\n"),
            subprocess.CalledProcessError(1, ["docker", "compose"], stderr=detalhe),
        ]
        with self.assertRaises(executar.Orientacao) as contexto:
            executar.verificar_docker()
        self.assertIn("motor Docker está acessível", str(contexto.exception))
        self.assertIn(detalhe, str(contexto.exception))

    def test_timeout_identifica_etapa(self):
        for etapa, respostas in (
            ("Docker não respondeu em 15 s", [subprocess.TimeoutExpired(["docker", "info"], 15)]),
            ("Compose não respondeu em 10 s", [subprocess.CompletedProcess([], 0, stdout="linux\n"),
                                                subprocess.TimeoutExpired(["docker", "compose"], 10)]),
        ):
            with self.subTest(etapa=etapa):
                self.rodar.side_effect = respostas
                with self.assertRaisesRegex(executar.Orientacao, etapa):
                    executar.verificar_docker()

    def test_motor_e_compose_prontos(self):
        self.rodar.side_effect = [subprocess.CompletedProcess([], 0, stdout="linux\n"),
                                 subprocess.CompletedProcess([], 0, stdout="Docker Compose version v2.0\n")]
        executar.verificar_docker()
        self.assertEqual(self.rodar.call_count, 2)
        self.assertEqual(self.rodar.call_args.args[0], executar.COMPOSE + ["version"])


@unittest.skipUnless(os.name == "posix", "Permissões POSIX dos volumes Linux")
class TestPermissoesBroker(unittest.TestCase):
    def setUp(self):
        temporario = tempfile.TemporaryDirectory()
        self.addCleanup(temporario.cleanup)
        self.local = Path(temporario.name) / "local"
        self.local.mkdir(mode=0o700)
        for nome in ("config.xml", "server.p12", "ca-key.pem", "mic-esp32-01.env"):
            arquivo = self.local / nome
            arquivo.write_bytes(b"conteudo preservado")
            arquivo.chmod(0o600)
        local = patch("executar.LOCAL", self.local)
        local.start()
        self.addCleanup(local.stop)
        plataforma = patch("executar.sys.platform", "linux")
        plataforma.start()
        self.addCleanup(plataforma.stop)

    def test_abre_leitura_somente_para_grupo_dos_arquivos_montados(self):
        executar.ajustar_permissoes_broker()
        executar.ajustar_permissoes_broker()  # Repetir a preparação preserva o resultado.
        for nome in ("config.xml", "server.p12", "ca-key.pem", "mic-esp32-01.env"):
            with self.subTest(arquivo=nome):
                arquivo = self.local / nome
                esperado = 0o640 if nome in {"config.xml", "server.p12"} else 0o600
                self.assertEqual(stat.S_IMODE(arquivo.stat().st_mode), esperado)
                self.assertEqual(arquivo.read_bytes(), b"conteudo preservado")
        self.assertEqual(stat.S_IMODE(self.local.stat().st_mode), 0o700)

    def test_ambiente_informa_grupo_real_ao_compose(self):
        with patch.dict(os.environ, {"MQTT_ARQUIVOS_GID": "123456"}):
            env = executar.ambiente()
        self.assertEqual(env["MQTT_ARQUIVOS_GID"], str((self.local / "config.xml").stat().st_gid))

    def test_windows_nao_altera_permissoes(self):
        with patch("executar.sys.platform", "win32"):
            executar.ajustar_permissoes_broker()
        self.assertEqual(stat.S_IMODE((self.local / "config.xml").stat().st_mode), 0o600)

    def test_instalacao_existente_e_ajustada_antes_de_iniciar_container(self):
        def compose(comando):
            self.assertEqual(comando, executar.COMPOSE + ["up", "-d"])
            self.assertEqual(stat.S_IMODE((self.local / "config.xml").stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE((self.local / "server.p12").stat().st_mode), 0o640)

        with patch("executar.verificar_arquivos"), patch("executar.rodar", side_effect=compose) as rodar:
            with patch("executar.verificar_tls", return_value="TLSv1.3"), patch("executar.mostrar"):
                executar.iniciar_broker("localhost")
        # Sem provisionamento ou rotação de contas: somente compose up.
        rodar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
