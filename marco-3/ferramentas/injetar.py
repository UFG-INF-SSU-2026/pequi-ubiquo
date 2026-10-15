"""Injeta eventos no broker para testar o consumidor sem o ESP32.

Uso:
    python injetar.py <caso> [--broker HOST] [--porta 8883] [--config ARQUIVO]
                             [--prefixo ufg-ssu-2026/towerai] [--sala sala-204]

Casos:
    normal      heartbeat SILENCIO, estado FALANDO, estado SILENCIO
    duplicado   o mesmo evento duas vezes (retry com o mesmo eventId)
    atrasado    estado com sequence menor que uma ja aplicada
    versao      schemaVersion 2, que o consumidor nao conhece
    faixa       value fora de -90..0 dBFS
    offline     status offline, o que o Last Will do ESP32 publicaria
    todos       todos os casos acima, em ordem
    demonstracao  roteiro narrado para primeira execução, com resumo final

Usa o deviceId "mic-injetor", entao nao se mistura com os ACKs do ESP32.
Os ACKs devolvidos pelo consumidor aparecem na tela.
"""
import argparse
import json
import secrets
import time
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_seguro import argumentos, configurar, preparar_interrupcao

import paho.mqtt.client as mqtt

DEVICE = "mic-injetor"
ESPERA_S = 1.5


def main():
    preparar_interrupcao()
    ap = argparse.ArgumentParser(description="Injeta eventos de teste no consumidor")
    ap.add_argument("caso", choices=["normal", "duplicado", "atrasado", "versao", "faixa", "offline", "todos", "demonstracao"])
    argumentos(ap)
    args = ap.parse_args()

    base = f"{args.prefixo}/{args.sala}"
    boot = secrets.token_hex(4)

    def ao_receber(client, userdata, msg):
        a = json.loads(msg.payload)
        print(f"   <- ACK {a.get('result'):<10} {a.get('eventId')} {a.get('reason', '')}", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"towerai-injetor-{boot}")
    client.on_message = ao_receber
    try:
        host, porta = configurar(client, args)
    except (ValueError, OSError) as erro:
        ap.error(str(erro))
    pronto = threading.Event()
    def conectado(c, userdata, flags, reason, properties):
        if reason.is_failure:
            print(f"Conexão recusada: {reason}", flush=True)
            return
        c.subscribe(f"{base}/dev/{DEVICE}/ack", qos=1)
    def assinado(c, userdata, mid, reasons, properties):
        if reasons and not any(r.is_failure for r in reasons):
            pronto.set()
    client.on_connect = conectado
    client.on_subscribe = assinado
    client.connect(host, porta, keepalive=10)
    client.loop_start()
    if not pronto.wait(10):
        client.disconnect()
        client.loop_stop()
        ap.error("não foi possível autenticar/assinar o próprio ACK em 10 s")

    def evento(seq, state, tipo="audio.state.changed", attempt=1, **extra):
        e = {
            "schemaVersion": 1,
            "eventId": f"{DEVICE}:{boot}:{seq}",
            "eventType": tipo,
            "deviceId": DEVICE,
            "entityId": args.sala,
            "bootId": boot,
            "eventTimeMs": 1000 * seq,
            "sequence": seq,
            "value": -20.0 if state == "FALANDO" else -60.0,
            "unit": "dBFS",
            "state": state,
            "reason": "injetado",
            "attempt": attempt,
        }
        e.update(extra)
        return e

    def enviar(descricao, carga, topico=None):
        print(f"-> {descricao}", flush=True)
        info = client.publish(topico or f"{base}/dev/{DEVICE}/telemetry", json.dumps(carga), qos=1)
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise RuntimeError("broker não confirmou a publicação em 5 s")
        time.sleep(ESPERA_S)

    def demonstracao():
        # Esta trilha dura o bastante para o painel mostrar o encerramento real.
        # Heartbeats mantêm a fonte válida enquanto a janela de silêncio avança.
        enviar("1/6 - Sala em silêncio: a ata deve permanecer FECHADA", evento(1, "SILENCIO", "device.heartbeat"))
        enviar("2/6 - Alguém começou a falar: veja a ata ABERTA no painel", evento(2, "FALANDO"))
        enviar("Amostra de volume para o resumo (-20 dBFS)", evento(3, "FALANDO", "audio.level"))
        enviar("3/6 - Repetindo o mesmo evento: espere ACK duplicado", evento(2, "FALANDO", attempt=2))
        enviar("4/6 - Sensor falhou: a ata deve ficar SUSPENSA", evento(4, "FALHA_SENSOR"))
        enviar("5/6 - Sensor voltou e detectou fala: a ata reabre", evento(5, "FALANDO"))
        enviar("6/6 - Fala terminou: aguardando 10 s para encerrar a ata", evento(6, "SILENCIO"))
        for seq in (7, 8):
            time.sleep(4)
            enviar("Microfone responde e confirma que a sala continua em silêncio", evento(seq, "SILENCIO", "device.heartbeat"))
        time.sleep(2)
        print("Roteiro encerrado. Procure 'Resumo final' na saída do painel.", flush=True)

    def normal():
        enviar("heartbeat SILENCIO (seq 1)", evento(1, "SILENCIO", "device.heartbeat"))
        enviar("estado FALANDO (seq 2)", evento(2, "FALANDO"))
        enviar("estado SILENCIO (seq 3)", evento(3, "SILENCIO"))

    def duplicado():
        enviar("estado FALANDO (seq 10, tentativa 1)", evento(10, "FALANDO"))
        enviar("mesmo eventId (seq 10, tentativa 2)", evento(10, "FALANDO", attempt=2))

    def atrasado():
        enviar("estado SILENCIO (seq 20)", evento(20, "SILENCIO"))
        enviar("estado FALANDO antigo (seq 15)", evento(15, "FALANDO"))

    def versao():
        enviar("schemaVersion 2 (seq 30)", evento(30, "FALANDO", schemaVersion=2))

    def faixa():
        enviar("value 12 dBFS (seq 31)", evento(31, "FALANDO", value=12.0))

    def offline():
        enviar("status offline", {"schemaVersion": 1, "deviceId": DEVICE, "status": "offline"},
               f"{base}/dev/{DEVICE}/status")

    casos = {"normal": normal, "duplicado": duplicado, "atrasado": atrasado,
             "versao": versao, "faixa": faixa, "offline": offline}
    try:
        if args.caso == "demonstracao":
            demonstracao()
        else:
            for nome in (casos if args.caso == "todos" else [args.caso]):
                print(f"== {nome} ==")
                casos[nome]()
        time.sleep(1)
    except KeyboardInterrupt:
        print("Sensor de demonstração encerrado.", flush=True)
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
