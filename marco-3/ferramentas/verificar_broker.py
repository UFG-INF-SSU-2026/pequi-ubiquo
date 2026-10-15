"""Provas ativas de TLS/autenticação/ACL no broker LOCAL provisionado.

Executar com os demais clientes parados. Usa MQTT 5 para observar PUBACK de
recusa e uma assinatura autorizada para verificar o efeito das permissões.
Nunca imprime credenciais; os perfis são lidos de broker/local/.
"""
import argparse
import json
import queue
import socket
import ssl
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_seguro import configurar

PASTA = Path(__file__).resolve().parents[1]
BASE = "ufg-ssu-2026/towerai/sala-204"


class TestBroker(unittest.TestCase):
    def setUp(self):
        self.clientes = []

    def tearDown(self):
        for c in self.clientes:
            c.disconnect()
            c.loop_stop()

    def cliente(self, conta, senha_errada=False):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                        client_id="verificar-" + uuid4().hex, protocol=mqtt.MQTTv5)
        args = SimpleNamespace(config=PASTA / "broker/local" / f"{conta}.env", broker=None, porta=None)
        host, porta = configurar(c, args)
        if senha_errada:
            c.username_pw_set(conta, "senha-intencionalmente-incorreta")
        c.recebidas = queue.Queue()
        c.conectou = threading.Event()
        c.assinou = threading.Event()
        c.publicou = threading.Event()
        def conectado(client, userdata, flags, reason, properties):
            c.resultado_conexao = reason
            c.conectou.set()
        def assinado(client, userdata, mid, reasons, properties):
            c.resultado_assinatura = reasons
            c.assinou.set()
        def publicado(client, userdata, mid, reason, properties):
            c.resultado_publicacao = reason
            c.publicou.set()
        c.on_connect = conectado
        c.on_subscribe = assinado
        c.on_publish = publicado
        c.on_message = lambda client, userdata, msg: c.recebidas.put(msg)
        self.clientes.append(c)
        c.connect(host, porta, keepalive=10)
        c.loop_start()
        self.assertTrue(c.conectou.wait(5), "broker não enviou CONNACK")
        return c

    def assinar(self, c, topico, negar=False):
        c.assinou.clear()
        c.subscribe(topico, qos=1)
        self.assertTrue(c.assinou.wait(5), "sem SUBACK")
        self.assertEqual(any(r.is_failure for r in c.resultado_assinatura), negar)

    def publicar(self, c, topico, carga, negar=False):
        c.publicou.clear()
        c.publish(topico, carga, qos=1)
        self.assertTrue(c.publicou.wait(5), "sem PUBACK")
        self.assertEqual(c.resultado_publicacao.is_failure, negar)

    def test_tls_cadeia_hostname_e_porta_sem_tls(self):
        contexto = ssl.create_default_context(cafile=str(PASTA / "broker/local/ca.pem"))
        with socket.create_connection(("127.0.0.1", 8883), timeout=3) as s:
            with contexto.wrap_socket(s, server_hostname="localhost") as tls:
                self.assertIn(tls.version(), ("TLSv1.2", "TLSv1.3"))
        for ctx, nome in ((contexto, "nome-invalido.local"), (ssl.create_default_context(), "localhost")):
            with socket.create_connection(("127.0.0.1", 8883), timeout=3) as s:
                with self.assertRaises(ssl.SSLCertVerificationError):
                    ctx.wrap_socket(s, server_hostname=nome)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", 1883), timeout=2)

    def test_anonimo_e_senha_errada_recusados(self):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="anonimo-" + uuid4().hex)
        c.tls_set(ca_certs=str(PASTA / "broker/local/ca.pem"))
        pronto = threading.Event()
        resultado = []
        c.on_connect = lambda client, userdata, flags, reason, properties: (resultado.append(reason), pronto.set())
        self.clientes.append(c)
        c.connect("localhost", 8883)
        c.loop_start()
        self.assertTrue(pronto.wait(5))
        self.assertTrue(resultado[0].is_failure)
        ruim = self.cliente("mic-esp32-01", senha_errada=True)
        self.assertTrue(ruim.resultado_conexao.is_failure)

    def test_acl_dispositivo_e_entrega_permitida(self):
        mic = self.cliente("mic-esp32-01")
        con = self.cliente("consumidor-sala-204")
        self.assertFalse(mic.resultado_conexao.is_failure)
        self.assinar(con, BASE + "/dev/+/telemetry")
        self.assinar(mic, BASE + "/dev/mic-esp32-01/ack")
        carga = uuid4().hex
        self.publicar(mic, BASE + "/dev/mic-esp32-01/telemetry", carga)
        self.assertEqual(con.recebidas.get(timeout=5).payload.decode(), carga)
        self.publicar(con, BASE + "/dev/mic-esp32-01/ack", carga)
        self.assertEqual(mic.recebidas.get(timeout=5).payload.decode(), carga)
        # O HiveMQ pode encerrar a conexão após uma publicação negada.
        # Cada tentativa usa uma sessão nova para medir a ACL, não a reconexão.
        for caminho in ("dev/outro/telemetry", "dev/mic-esp32-01/ack"):
            self.publicar(self.cliente("mic-esp32-01"), BASE + "/" + caminho, "forjado", negar=True)
        for caminho in ("#", "insights/reuniao"):
            self.assinar(self.cliente("mic-esp32-01"), BASE + "/" + caminho, negar=True)
        self.assertTrue(con.recebidas.empty())

    def test_painel_apenas_le_resumo(self):
        painel = self.cliente("painel-sala-204")
        con = self.cliente("consumidor-sala-204")
        self.assinar(painel, BASE + "/insights/reuniao")
        while not painel.recebidas.empty():
            painel.recebidas.get_nowait()  # descarta um eventual retained anterior
        carga = uuid4().hex
        self.publicar(con, BASE + "/insights/reuniao", carga)
        self.assertEqual(painel.recebidas.get(timeout=5).payload.decode(), carga)
        for caminho in ("insights/reuniao", "dev/mic-esp32-01/ack"):
            self.publicar(self.cliente("painel-sala-204"), BASE + "/" + caminho, "forjado", negar=True)
        self.assinar(self.cliente("painel-sala-204"), BASE + "/dev/+/telemetry", negar=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
