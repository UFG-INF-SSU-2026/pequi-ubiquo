"""Configuração comum dos clientes: TLS obrigatório e segredo fora do código.

Os arquivos .env gerados pelo provisionamento são dados, nunca comandos shell.
Variáveis do processo têm precedência; argumentos permitem escolher o endereço
sem alterar credenciais. Uma CA ausente ou um hostname incorreto falham fechado.
"""
import os
import re
import ssl
import signal
from pathlib import Path

ID_VALIDO = re.compile(r"[A-Za-z0-9_-]{1,64}")
PREFIXO = "ufg-ssu-2026/towerai"


def preparar_interrupcao():
    """Permite ao iniciador parar um filho Windows sem matar sua sessão MQTT.

    CTRL_BREAK é dirigido ao grupo do próprio filho. Convertê-lo em
    KeyboardInterrupt reaproveita o encerramento normal e o bloco finally.
    """
    if hasattr(signal, "SIGBREAK"):
        def interromper(numero, quadro):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK, interromper)


def argumentos(ap):
    ap.add_argument("--config", type=Path, help="arquivo KEY=VALUE gerado pelo broker")
    ap.add_argument("--broker", help="nome/IP presente no SAN do certificado")
    ap.add_argument("--porta", type=int, help="porta TLS; padrão 8883")
    ap.add_argument("--prefixo", default=PREFIXO)
    ap.add_argument("--sala", default="sala-204")


def configurar(client, args):
    """Autentica o cliente e devolve (host, porta); não conecta por conta própria."""
    valores = {}
    if args.config:
        for linha in args.config.read_text(encoding="utf-8").splitlines():
            if not linha.strip() or linha.lstrip().startswith("#"):
                continue
            chave, separador, valor = linha.partition("=")
            if not separador or not chave.strip().startswith("MQTT_"):
                raise ValueError("configuração deve conter apenas MQTT_CHAVE=valor")
            valores[chave.strip()] = valor.strip()
    valores.update({k: v for k, v in os.environ.items() if k.startswith("MQTT_")})
    usuario = valores.get("MQTT_USUARIO", "")
    senha = valores.get("MQTT_SENHA", "")
    if not ID_VALIDO.fullmatch(usuario) or not senha:
        raise ValueError("informe MQTT_USUARIO e MQTT_SENHA ou use --config")
    ca = Path(valores.get("MQTT_CA", "broker/local/ca.pem"))
    if args.config and not ca.is_absolute():
        ca = args.config.resolve().parent / ca
    contexto = ssl.create_default_context(cafile=str(ca))
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    # create_default_context verifica a cadeia, validade e nome do servidor.
    # Não existe opção de desligar essas verificações nesta implementação.
    client.tls_set_context(contexto)
    client.username_pw_set(usuario, senha)
    client.max_queued_messages_set(100)  # limita saída acumulada durante reconexão
    host = args.broker or valores.get("MQTT_BROKER", "localhost")
    porta = args.porta or int(valores.get("MQTT_PORTA", "8883"))
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    return host, porta


def dispositivo_do_topico(topico, base):
    """Extrai identidade somente de um caminho exato autorizado pelo broker."""
    inicio = base + "/dev/"
    if not topico.startswith(inicio):
        return None
    partes = topico[len(inicio):].split("/")
    if (len(partes) != 2 or not ID_VALIDO.fullmatch(partes[0])
            or partes[1] not in ("telemetry", "status")):
        return None
    return partes[0], partes[1]
