"""Liga a Sala e o Resumo ao MQTT autenticado sobre TLS.

A identidade vem do tópico autorizado pelo broker. Um único lock serializa
callbacks e ticks: nenhuma publicação observa metade de uma transição.
O resumo indica a decisão de ata; a gravação real no Flask é evolução futura.
"""
import argparse
import json
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_seguro import argumentos, configurar, dispositivo_do_topico, ID_VALIDO, preparar_interrupcao
from resumo import Resumo
from sala import Sala


def log(tag, texto):
    print(f"{datetime.now():%H:%M:%S} {tag:<10} {texto}", flush=True)


def main():
    preparar_interrupcao()
    ap = argparse.ArgumentParser(description="Consumidor seguro do Marco 3")
    argumentos(ap)
    ap.add_argument("--dispositivo", default="mic-esp32-01", help="única fonte desta sala")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if not ID_VALIDO.fullmatch(args.dispositivo):
        ap.error("dispositivo inválido")
    sala = Sala(args.sala)
    resumo = Resumo(args.sala)
    trava = threading.Lock()
    base = f"{args.prefixo}/{args.sala}"
    insights = f"{base}/insights/reuniao"
    conectado = threading.Event()
    rejeicoes = deque()  # instantes de inválidos da única fonte configurada
    alerta_quarentena = False
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"consumidor-{args.sala}-{uuid4().hex}")
    try:
        host, porta = configurar(client, args)
    except (ValueError, OSError) as erro:
        ap.error(str(erro))

    def indisponivel(motivo):
        return json.dumps({"schemaVersion": 1, "insightType": "reuniao.resumo",
                           "entityId": args.sala, "motivo": motivo,
                           "ata": "DESCONHECIDO", "final": False})

    # O broker publica este aviso se o processo ou o enlace cair sem DISCONNECT.
    client.will_set(insights, indisponivel("consumidor_offline"), qos=1, retain=True)

    def publicar_resumos(agora):
        for saida in resumo.avancar(sala, agora, datetime.now().astimezone()):
            info = client.publish(insights, json.dumps(saida), qos=1, retain=True)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                log("MQTT", "resumo não enfileirado; painel expirará o dado")
            log("RESUMO", f"{saida['ata']} qualidade={saida['qualidade']} final={saida['final']}")

    def ao_conectar(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log("MQTT", f"conexão recusada: {reason_code}")
            return
        conectado.set()
        log("MQTT", f"conectado por TLS a {host}:{porta}")
        client.subscribe([(f"{base}/dev/+/telemetry", 1), (f"{base}/dev/+/status", 1)])
        # Substitui o resumo retido de uma execução anterior. Só uma nova
        # evidência do sensor autoriza apresentar a sala como ativa novamente.
        client.publish(insights, indisponivel("consumidor_iniciando"), qos=1, retain=True)
        with trava:
            resumo.forcar_publicacao(time.monotonic())

    def ao_desconectar(client, userdata, flags, reason_code, properties):
        conectado.clear()
        if reason_code == 0:
            log("MQTT", "sessão encerrada normalmente")
        else:
            log("MQTT", f"desconectado ({reason_code}); tentando reconectar")
        with trava:
            # Perdemos a observação da fonte: não contabilizar fala durante
            # uma partição entre consumidor e broker.
            bruto = json.dumps({"schemaVersion": 1, "deviceId": args.dispositivo, "status": "offline"})
            sala.receber_status(bruto, time.monotonic(), args.dispositivo)
            publicar_resumos(time.monotonic())

    def ao_assinar(client, userdata, mid, reason_codes, properties):
        # O iniciador só libera o sensor depois da autorização do broker.
        if reason_codes and not any(codigo.is_failure for codigo in reason_codes):
            log("PRONTO", "assinaturas autorizadas; aguardando eventos do microfone")
        else:
            log("MQTT", "assinatura recusada; confira as permissões da conta")

    def ao_receber(client, userdata, msg):
        nonlocal alerta_quarentena
        origem = dispositivo_do_topico(msg.topic, base)
        if origem is None or origem[0] != args.dispositivo:
            log("REJEITADO", "tópico fora da fonte configurada")
            return
        dispositivo, tipo = origem
        with trava:
            agora = time.monotonic()
            # Atualizar os relógios antes de aceitar dado novo evita estender
            # artificialmente um estado que já tinha expirado.
            for transicao in sala.tick(agora):
                log("SALA", transicao)
            publicar_resumos(agora)
            if tipo == "telemetry":
                # Telemetria não pode ser retida pelo contrato.
                if msg.retain:
                    log("REJEITADO", "telemetria retida")
                    return
                res = sala.receber_evento(msg.payload, agora, dispositivo)
            else:
                res = sala.receber_status(msg.payload, agora, dispositivo)
            # A Atividade 02 prevê 20 inválidos/60 s. Aqui emitimos o alerta
            # operacional; o administrador revoga a conta com gerenciar_acesso.
            while rejeicoes and rejeicoes[0] < agora - 60:
                rejeicoes.popleft()
            if len(rejeicoes) < 20:
                alerta_quarentena = False
            if res.status == "REJEITADO":
                if len(rejeicoes) < 20:
                    rejeicoes.append(agora)
                else:
                    rejeicoes.popleft()
                    rejeicoes.append(agora)
                if len(rejeicoes) >= 20 and not alerta_quarentena:
                    log("SEGURANCA", f"20 inválidos em 60 s: colocar {dispositivo} em quarentena administrativa")
                    alerta_quarentena = True
            if args.verbose or res.status != "TELEMETRIA" or res.transicoes:
                log(res.status, res.motivo)
            if res.ack:
                client.publish(f"{base}/dev/{dispositivo}/ack", json.dumps(res.ack), qos=1)
                log("ACK", f"{res.ack['result']} {res.ack['eventId']}")
            publicar_resumos(agora)
            # Fora de ordem, duplicado ou inválido não entra na média de áudio.
            if (res.status == "TELEMETRIA" and not res.motivo and res.evento
                    and res.evento['eventType'] == "audio.level"
                    and res.evento['state'] == "FALANDO"):
                resumo.registrar_nivel(res.evento['value'])
            for transicao in res.transicoes:
                log("SALA", transicao)

    client.on_connect = ao_conectar
    client.on_disconnect = ao_desconectar
    client.on_message = ao_receber
    client.on_subscribe = ao_assinar
    client.on_connect_fail = lambda *_: log("MQTT", f"falha ao alcançar {host}:{porta}")
    client.connect_async(host, porta, keepalive=10)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
            with trava:
                agora = time.monotonic()
                for transicao in sala.tick(agora):
                    log("SALA", transicao)
                publicar_resumos(agora)
    except KeyboardInterrupt:
        log("MQTT", "encerrando")
    finally:
        # DISCONNECT suprime o Last Will, portanto encerramento normal também
        # precisa invalidar o estado retido explicitamente.
        if conectado.is_set():
            aviso = client.publish(insights, indisponivel("consumidor_offline"), qos=1, retain=True)
            aviso.wait_for_publish(timeout=3)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
