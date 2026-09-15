"""Consumidor do Marco 2 (TowerAI).

Assina os eventos do microfone da sala, passa cada mensagem pela Sala
(validar -> deduplicar -> ordenar -> aplicar -> decidir a ata) e publica a
confirmacao de cada mudanca de estado.

Uso:
    python consumidor.py [-v] [--broker HOST] [--porta 1883]
                         [--prefixo ufg-ssu-2026/towerai] [--sala sala-204]

Topicos (detalhes em ../contrato.md):
    <prefixo>/<sala>/telemetry/audio      eventos do microfone     assina
    <prefixo>/<sala>/status/<deviceId>    online/offline + LWT     assina
    <prefixo>/<sala>/ack/<deviceId>       confirmacao de negocio   publica
"""
import argparse
import json
import os
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt

from sala import Sala

os.system("")  # habilita cores ANSI no terminal do Windows

COR = {
    "APLICADO": "\033[92m",
    "DUPLICADO": "\033[93m",
    "ATRASADO": "\033[93m",
    "REJEITADO": "\033[91m",
    "TELEMETRIA": "\033[90m",
    "STATUS": "\033[96m",
    "ACK": "\033[94m",
    "SALA": "\033[95m",
    "MQTT": "\033[97m",
}
FIM = "\033[0m"


def log(tag, texto):
    hora = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{hora}  {COR.get(tag, '')}{tag:<10}{FIM}  {texto}", flush=True)


def descrever(res):
    e = res.evento
    if not isinstance(e, dict):
        return ""
    if res.status == "STATUS":
        return f"{e.get('deviceId')} {e.get('status')}"
    campos = ("eventType", "sequence", "attempt", "state", "value", "eventId")
    return " ".join(f"{c}={e.get(c)}" for c in campos if c in e)


def main():
    ap = argparse.ArgumentParser(description="Consumidor MQTT do Marco 2 (TowerAI)")
    ap.add_argument("--broker", default="broker.hivemq.com")
    ap.add_argument("--porta", type=int, default=1883)
    ap.add_argument("--prefixo", default="ufg-ssu-2026/towerai")
    ap.add_argument("--sala", default="sala-204")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="mostra toda telemetria, nao so a que muda estado")
    args = ap.parse_args()

    sala = Sala(args.sala)
    trava = threading.Lock()
    base = f"{args.prefixo}/{args.sala}"
    topico_eventos = f"{base}/telemetry/audio"
    topico_status = f"{base}/status/+"

    def ao_conectar(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log("MQTT", f"broker recusou a conexao: {reason_code}")
            return
        log("MQTT", f"conectado a {args.broker}:{args.porta}")
        log("MQTT", f"assinando {topico_eventos} e {topico_status}")
        client.subscribe([(topico_eventos, 1), (topico_status, 1)])

    def ao_desconectar(client, userdata, flags, reason_code, properties):
        log("MQTT", f"desconectado do broker ({reason_code}); reconectando")

    def ao_falhar(client, userdata):
        log("MQTT", f"nao foi possivel conectar a {args.broker}:{args.porta}; tentando de novo")

    def ao_receber(client, userdata, msg):
        with trava:
            agora = time.monotonic()
            if msg.topic == topico_eventos:
                res = sala.receber_evento(msg.payload, agora)
            else:
                res = sala.receber_status(msg.payload, agora)

            if args.verbose or res.status != "TELEMETRIA" or res.transicoes:
                detalhe = descrever(res)
                if msg.retain:
                    detalhe += " (retida pelo broker)"
                if res.motivo:
                    detalhe += f"  -> {res.motivo}"
                log(res.status, detalhe.strip())
            if res.ack:
                client.publish(f"{base}/ack/{res.ack['deviceId']}", json.dumps(res.ack), qos=1)
                log("ACK", f"{res.ack['result']} -> {res.ack['eventId']}")
            for t in res.transicoes:
                log("SALA", t)
            if res.transicoes:
                log("SALA", sala.resumo())

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"towerai-consumidor-{args.sala}-{os.getpid()}")
    client.on_connect = ao_conectar
    client.on_disconnect = ao_desconectar
    client.on_connect_fail = ao_falhar
    client.on_message = ao_receber
    client.reconnect_delay_set(min_delay=1, max_delay=10)

    log("MQTT", f"consumidor da {args.sala} iniciando ({args.broker}:{args.porta}, prefixo {args.prefixo})")
    client.connect_async(args.broker, args.porta, keepalive=10)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
            with trava:
                transicoes = sala.tick(time.monotonic())
                for t in transicoes:
                    log("SALA", t)
                if transicoes:
                    log("SALA", sala.resumo())
    except KeyboardInterrupt:
        log("MQTT", "encerrando")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
