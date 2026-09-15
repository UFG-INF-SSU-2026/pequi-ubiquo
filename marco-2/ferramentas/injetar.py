"""Injeta eventos no broker para testar o consumidor sem o ESP32.

Uso:
    python injetar.py <caso> [--broker HOST] [--porta 1883]
                             [--prefixo ufg-ssu-2026/towerai] [--sala sala-204]

Casos:
    normal      heartbeat SILENCIO, estado FALANDO, estado SILENCIO
    duplicado   o mesmo evento duas vezes (retry com o mesmo eventId)
    atrasado    estado com sequence menor que uma ja aplicada
    versao      schemaVersion 2, que o consumidor nao conhece
    faixa       value fora de -90..0 dBFS
    offline     status offline, o que o Last Will do ESP32 publicaria
    todos       todos os casos acima, em ordem

Usa o deviceId "mic-injetor", entao nao se mistura com os ACKs do ESP32.
Os ACKs devolvidos pelo consumidor aparecem na tela.
"""
import argparse
import json
import secrets
import time

import paho.mqtt.client as mqtt

DEVICE = "mic-injetor"
ESPERA_S = 1.5


def main():
    ap = argparse.ArgumentParser(description="Injeta eventos de teste no consumidor")
    ap.add_argument("caso", choices=["normal", "duplicado", "atrasado", "versao", "faixa", "offline", "todos"])
    ap.add_argument("--broker", default="broker.hivemq.com")
    ap.add_argument("--porta", type=int, default=1883)
    ap.add_argument("--prefixo", default="ufg-ssu-2026/towerai")
    ap.add_argument("--sala", default="sala-204")
    args = ap.parse_args()

    base = f"{args.prefixo}/{args.sala}"
    boot = secrets.token_hex(4)

    def ao_receber(client, userdata, msg):
        a = json.loads(msg.payload)
        print(f"   <- ACK {a.get('result'):<10} {a.get('eventId')} {a.get('reason', '')}", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"towerai-injetor-{boot}")
    client.on_message = ao_receber
    client.connect(args.broker, args.porta, keepalive=30)
    client.subscribe(f"{base}/ack/{DEVICE}", qos=1)
    client.loop_start()
    time.sleep(1)

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
        client.publish(topico or f"{base}/telemetry/audio", json.dumps(carga), qos=1)
        time.sleep(ESPERA_S)

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
               f"{base}/status/{DEVICE}")

    casos = {"normal": normal, "duplicado": duplicado, "atrasado": atrasado,
             "versao": versao, "faixa": faixa, "offline": offline}
    for nome in (casos if args.caso == "todos" else [args.caso]):
        print(f"== {nome} ==")
        casos[nome]()

    time.sleep(1)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
