"""Verifica o protocolo mínimo e o ciclo de vida do relógio do Wokwi."""
import socket
import struct
import time
import unittest

from ntp_local import EPOCA_NTP, RelogioLocal, responder, timestamp


class TestNtpLocal(unittest.TestCase):
    def pedido(self):
        pedido = bytearray(48)
        pedido[0] = 0x23  # v4, modo cliente
        pedido[40:48] = timestamp(time.time())
        return bytes(pedido)

    def test_preserva_consulta_e_carimba_tempos(self):
        pedido = self.pedido()
        resposta = responder(pedido, 1_800_000_000.25, 1_800_000_000.5)
        self.assertEqual(len(resposta), 48)
        self.assertEqual(resposta[0], 0x24)
        self.assertEqual(resposta[24:32], pedido[40:48])
        self.assertEqual(resposta[40:48], timestamp(1_800_000_000.5))

    def test_ignora_pacotes_invalidos_ou_modo_servidor(self):
        self.assertIsNone(responder(b"incompleto", 0, 0))
        self.assertIsNone(responder(bytes([0x24]) + bytes(47), 0, 0))
        self.assertIsNone(responder(bytes([0x0B]) + bytes(47), 0, 0))

    def test_responde_por_udp_e_libera_a_porta(self):
        with RelogioLocal(porta=0) as relogio:
            porta = relogio.porta
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as cliente:
                cliente.settimeout(2)
                pedido = self.pedido()
                cliente.sendto(pedido, ("127.0.0.1", porta))
                resposta, _ = cliente.recvfrom(512)
            self.assertEqual(resposta[24:32], pedido[40:48])
            segundos, fracao = struct.unpack("!II", resposta[40:48])
            unix = segundos - EPOCA_NTP + fracao / 2**32
            self.assertLess(abs(unix - time.time()), 2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as livre:
            livre.bind(("127.0.0.1", porta))
