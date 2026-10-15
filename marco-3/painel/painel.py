"""Consumidor somente leitura do resumo: mostra decisão, validade e qualidade.

Exibir 'ata aberta' não significa que o Flask esteja gravando. A integração
com a transcrição real fica fora deste marco. O timer monotônico continua
rodando mesmo quando não chega mensagem: retained antigo não congela a tela.
"""
import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_seguro import argumentos, configurar, preparar_interrupcao

VALIDADE_S = 90.0
ESTADOS = {"ABERTA", "ENCERRANDO", "SUSPENSA", "FECHADA", "DESCONHECIDO"}


def interpretar(bruto, sala, agora_dt):
    """Valida a saída antes de apresentá-la, inclusive timestamp de retidos."""
    try:
        resumo = json.loads(bruto)
        if (not isinstance(resumo, dict) or type(resumo.get("schemaVersion")) is not int
                or resumo["schemaVersion"] != 1 or resumo.get("entityId") != sala
                or resumo.get("insightType") != "reuniao.resumo"
                or not isinstance(resumo.get("ata"), str) or resumo["ata"] not in ESTADOS
                or type(resumo.get("final")) is not bool):
            return "Estado desconhecido: resumo inválido", False
        if resumo.get("motivo") == "consumidor_iniciando":
            return "Aguardando os primeiros dados da sala (estado ainda desconhecido)", False
        if resumo.get("motivo") == "consumidor_offline":
            return "Estado desconhecido: consumidor indisponível", False
        gerado = datetime.fromisoformat(resumo["geradoEm"])
        if gerado.tzinfo is None:
            raise ValueError("timestamp sem fuso")
        idade = (agora_dt - gerado).total_seconds()
        if idade < -5 or (idade > VALIDADE_S and resumo["ata"] != "FECHADA"):
            return "Estado desconhecido: resumo expirado ou relógio divergente", False
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return "Estado desconhecido: resumo inválido", False
    ata = resumo["ata"]
    if resumo["final"] and ata == "FECHADA":
        return (f"Resumo final: duração={resumo.get('duracaoS')}s, "
                f"fração de fala={resumo.get('taxaFala')}, qualidade={resumo.get('qualidade')}"), False
    if ata == "SUSPENSA" or resumo.get("qualidade") in ("PARCIAL", "INCOMPLETA"):
        texto = f"Atenção: ata {ata}; faltam dados confiáveis"
    else:
        texto = {"ABERTA": "Ata ABERTA: reunião detectada",
                 "ENCERRANDO": "Reunião em pausa; encerra em instantes",
                 "FECHADA": "Ata FECHADA", "DESCONHECIDO": "Estado desconhecido"}[ata]
    # Descontar a idade do retained evita conceder mais 90 s a dado já velho.
    restante = max(0.0, VALIDADE_S - idade) if ata != "FECHADA" else False
    return texto, restante


def main():
    preparar_interrupcao()
    ap = argparse.ArgumentParser(description="Painel MQTT somente leitura")
    argumentos(ap)
    args = ap.parse_args()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"painel-{args.sala}-{uuid4().hex}")
    try:
        host, porta = configurar(client, args)
    except (ValueError, OSError) as erro:
        ap.error(str(erro))
    trava = threading.Lock()
    expira_em = None

    def ao_conectar(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"Conexão recusada: {reason_code}", flush=True)
            return
        client.subscribe(f"{args.prefixo}/{args.sala}/insights/reuniao", qos=1)

    def ao_receber(client, userdata, msg):
        nonlocal expira_em
        texto, restante = interpretar(msg.payload, args.sala, datetime.now(timezone.utc))
        with trava:
            expira_em = time.monotonic() + restante if restante is not False else None
            print(texto, flush=True)

    def ao_desconectar(*_):
        nonlocal expira_em
        with trava:
            expira_em = None
            print("Estado desconhecido: painel sem conexão com o broker", flush=True)

    def ao_assinar(client, userdata, mid, reason_codes, properties):
        if reason_codes and not any(codigo.is_failure for codigo in reason_codes):
            print("PRONTO: painel conectado; aguardando o resumo da sala", flush=True)
        else:
            print("Assinatura recusada: confira as permissões da conta do painel", flush=True)

    client.on_connect = ao_conectar
    client.on_message = ao_receber
    client.on_disconnect = ao_desconectar
    client.on_subscribe = ao_assinar
    client.on_connect_fail = lambda *_: print("Estado desconhecido: broker inacessível", flush=True)
    client.connect_async(host, porta, keepalive=10)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
            with trava:
                if expira_em is not None and time.monotonic() >= expira_em:
                    print("Estado desconhecido: resumo expirado", flush=True)
                    expira_em = None
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
