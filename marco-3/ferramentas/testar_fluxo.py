"""Integração real com broker TLS, processos do consumidor/painel e sensor Python.

Para reproduzir: pare os clientes da demonstração e execute com o broker
provisionado. O processo filho é encerrado abruptamente ao final para provar
o Last Will; seu estado em RAM não deve ser usado por uma demonstração paralela.
"""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import paho.mqtt.client as mqtt

PASTA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PASTA))
from mqtt_seguro import configurar

BASE = "ufg-ssu-2026/towerai/sala-204"
DEV = "mic-injetor"


def main():
    processos, clientes = [], []
    saidas = queue.Queue()
    mensagens = queue.Queue()

    def iniciar(script, config, extras=()):
        p = subprocess.Popen([sys.executable, "-u", str(PASTA / script), "--config",
                              str(PASTA / "broker/local" / config), *extras],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", env=__import__("os").environ | {"PYTHONIOENCODING": "utf-8"})
        processos.append(p)
        def ler():
            for linha in p.stdout:
                saidas.put(linha.strip())
        threading.Thread(target=ler, daemon=True).start()
        return p

    def cliente(conta, topico):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fluxo-" + uuid4().hex)
        args = SimpleNamespace(config=PASTA / "broker/local" / f"{conta}.env", broker=None, porta=None)
        host, porta = configurar(c, args)
        pronto = threading.Event()
        def conectar(c, userdata, flags, reason, properties):
            if not reason.is_failure:
                c.subscribe(topico, qos=1)
        c.on_connect = conectar
        c.on_subscribe = lambda *args: pronto.set()
        c.on_message = lambda c, u, msg: mensagens.put((msg.topic, json.loads(msg.payload), msg.retain))
        c.connect(host, porta, keepalive=10)
        clientes.append(c)
        c.loop_start()
        assert pronto.wait(5), "cliente não conseguiu assinar"
        return c

    def esperar(predicado, timeout=20):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            try:
                msg = mensagens.get(timeout=max(0.01, limite - time.monotonic()))
            except queue.Empty:
                break
            if predicado(*msg):
                return msg
        raise AssertionError("mensagem esperada não chegou")

    boot = uuid4().hex[:8]
    def evento(seq, estado="FALANDO", tipo="audio.state.changed", **extras):
        return {"schemaVersion": 1, "eventId": f"{DEV}:{boot}:{seq}", "eventType": tipo,
                "deviceId": DEV, "entityId": "sala-204", "bootId": boot, "eventTimeMs": seq * 1000,
                "sequence": seq, "value": -20 if estado == "FALANDO" else -60,
                "unit": "dBFS", "state": estado, "attempt": 1, **extras}

    try:
        painel = iniciar("painel/painel.py", "painel-sala-204.env")
        con = iniciar("consumidor/consumidor.py", "consumidor-sala-204.env", ("--dispositivo", DEV))
        leitor = cliente("painel-sala-204", BASE + "/insights/reuniao")
        sensor = cliente(DEV, BASE + f"/dev/{DEV}/ack")
        esperar(lambda t, r, _: r.get("motivo") == "consumidor_iniciando")

        def enviar(e):
            sensor.publish(BASE + f"/dev/{DEV}/telemetry", json.dumps(e), qos=1).wait_for_publish(5)
        enviar(evento(1))
        esperar(lambda t, r, _: r.get("ata") == "ABERTA")
        enviar(evento(1, attempt=2))
        esperar(lambda t, r, _: r.get("result") == "duplicado")
        print("OK: telemetria TLS, ata aberta e retry deduplicado", flush=True)
        enviar(evento(2, tipo="audio.level"))
        enviar(evento(3, "SILENCIO"))
        esperar(lambda t, r, _: r.get("ata") == "ENCERRANDO")
        time.sleep(5)
        enviar(evento(4, "SILENCIO", "device.heartbeat"))
        _, final, _ = esperar(lambda t, r, _: r.get("final") is True)
        assert final["qualidade"] == "COMPLETA" and final["silencioS"] >= 10
        assert final["nivelMedioDbfs"] == -20
        print("OK: resumo final coerente, sem lacuna e com nível de áudio", flush=True)
        tardio = cliente("painel-sala-204", BASE + "/insights/reuniao")
        esperar(lambda t, r, retido: retido and r.get("insightId") == final["insightId"])
        print("OK: novo assinante recebeu o resumo final retido", flush=True)
        enviar(evento(5))
        esperar(lambda t, r, _: r.get("ata") == "ABERTA")
        con.kill()
        con.wait(timeout=5)
        esperar(lambda t, r, _: r.get("motivo") == "consumidor_offline", timeout=25)
        print("OK: queda abrupta publicou Last Will do consumidor", flush=True)
        time.sleep(1)
        textos = []
        while not saidas.empty():
            textos.append(saidas.get_nowait())
        assert any("Resumo final" in l for l in textos), "painel não exibiu o final"
        assert any("consumidor indisponível" in l for l in textos), "painel não invalidou o estado"
        assert painel.poll() is None, "painel encerrou inesperadamente"
        print("OK: processo do painel mostrou resumo e invalidou estado indisponível", flush=True)
    finally:
        for p in processos:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
            p.stdout.close()
        for c in clientes:
            c.disconnect()
            c.loop_stop()


if __name__ == "__main__":
    main()
