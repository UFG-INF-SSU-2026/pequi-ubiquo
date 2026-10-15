"""Relógio SNTP mínimo do laboratório Wokwi, restrito a 127.0.0.1.

O firmware valida certificados TLS e precisa de horário. Este serviço entrega
o horário do PC pelo gateway privado, sem depender de um Raspberry Pi ou NTP
público durante a simulação. Não ajusta o relógio do computador: ele já deve
estar correto. Não é um substituto do chrony na rede física da sala.
"""
import socket
import struct
import threading
import time

EPOCA_NTP = 2_208_988_800  # diferença entre 1900-01-01 e a época Unix


def timestamp(instante):
    ntp = instante + EPOCA_NTP
    return struct.pack("!II", int(ntp) & 0xFFFFFFFF, int((ntp % 1) * 2**32))


def responder(pedido, recebido_em, enviado_em):
    """Responde apenas a consulta NTP v3/v4 no modo cliente (RFC 5905).

    Copiar o transmit timestamp para o originate timestamp permite ao cliente
    associar a resposta à consulta. Os tempos de recepção/envio vêm do PC.
    """
    if len(pedido) < 48 or pedido[0] & 7 != 3:
        return None
    versao = (pedido[0] >> 3) & 7
    if versao not in (3, 4):
        return None
    resposta = bytearray(48)
    resposta[0] = versao << 3 | 4  # servidor, sem indicação de leap pendente
    resposta[1] = 10  # fonte local da demonstração, conforme a confiança no PC
    resposta[2] = pedido[2]
    resposta[3] = 0xEC  # precisão -20, codificada como inteiro de 8 bits
    struct.pack_into("!I", resposta, 8, 1 << 16)  # dispersão de 1 segundo
    resposta[12:16] = b"LOCL"
    resposta[16:24] = timestamp(recebido_em)
    resposta[24:32] = pedido[40:48]
    resposta[32:40] = timestamp(recebido_em)
    resposta[40:48] = timestamp(enviado_em)
    return bytes(resposta)


class RelogioLocal:
    """Context manager: abre o relógio e libera socket/thread ao encerrar o modo Wokwi."""
    def __init__(self, porta=4123):
        self.porta = porta
        self.parar = threading.Event()

    def __enter__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.bind(("127.0.0.1", self.porta))
            self.porta = self.socket.getsockname()[1]
            self.socket.settimeout(0.5)
        except OSError:
            self.socket.close()
            raise
        self.thread = threading.Thread(target=self._servir, daemon=True)
        self.thread.start()
        return self

    def _servir(self):
        while not self.parar.is_set():
            try:
                pedido, origem = self.socket.recvfrom(512)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows pode sinalizar um ICMP de um cliente que já fechou
                # a porta. Isso não deve derrubar o relógio dos outros clientes.
                continue
            recebido_em = time.time()
            resposta = responder(pedido, recebido_em, time.time())
            if resposta is not None:
                self.socket.sendto(resposta, origem)

    def __exit__(self, *erro):
        self.parar.set()
        self.thread.join(timeout=2)
        self.socket.close()
