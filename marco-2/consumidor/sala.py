"""Estado da sala no consumidor do Marco 2 (TowerAI).

Logica pura, sem rede: cada metodo recebe a mensagem e o relogio do consumidor
e devolve o que aconteceu. O consumidor.py so liga isto ao MQTT, e o
test_sala.py testa as regras sem precisar de broker.

Caminho de cada evento:
    validar -> marcar dispositivo vivo -> deduplicar (eventId)
            -> ordenar (sequence por bootId) -> aplicar estado -> decidir a ata

Decisoes reaproveitadas do Marco 1:
  - Samuel  : deduplicacao por identificador; silencio do dispositivo detectado
              pela ausencia de mensagens (heartbeat)
  - Moises  : sessao da ata com janela de silencio; dado que expira vira
              DESCONHECIDO em vez de ficar congelado
  - Thiago  : a ata so muda em transicao, e ENCERRANDO e uma faixa intermediaria
              que impede abrir e fechar a cada pausa
  - Vinicius: falta de dado nao e silencio: FALHA_SENSOR e DESCONHECIDO
              suspendem a ata em vez de fecha-la
"""
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field

SCHEMA_VERSION = 1

TIPOS_ESTADO = {"audio.state.changed"}                  # exige ACK e ordem
TIPOS_TELEMETRIA = {"audio.level", "device.heartbeat"}  # perda tolerada, sem ACK
ESTADOS_MIC = {"DESCONHECIDO", "SILENCIO", "FALANDO", "FALHA_SENSOR"}

CAMPOS_OBRIGATORIOS = {
    "schemaVersion": int,
    "eventId": str,
    "eventType": str,
    "deviceId": str,
    "entityId": str,
    "bootId": str,
    "eventTimeMs": int,
    "sequence": int,
    "value": (int, float),
    "unit": str,
    "state": str,
    "attempt": int,
}
FAIXA_DBFS = (-90.0, 0.0)
# deviceId vira parte do topico do ACK: nada de "/", "+" ou "#"
ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SILENCIO_DISPOSITIVO_S = 15.0  # 3 heartbeats (5 s) sem nada => dado expirado
GAP_ATA_S = 10.0               # silencio continuo que encerra a ata
SUSPENSA_MAX_S = 60.0          # sem dado confiavel por tanto tempo, a ata fecha
MAX_IDS_VISTOS = 1000          # memoria da deduplicacao


@dataclass
class Resultado:
    status: str                # APLICADO | TELEMETRIA | DUPLICADO | ATRASADO | REJEITADO | STATUS
    motivo: str = ""
    evento: dict | None = None
    ack: dict | None = None    # publicar em <prefixo>/<sala>/ack/<deviceId>
    transicoes: list[str] = field(default_factory=list)


class Sala:
    def __init__(self, entity_id="sala-204"):
        self.entity_id = entity_id
        self.mic = "DESCONHECIDO"   # ultimo estado aceito do microfone
        self.online = False
        self.visto_em = None        # relogio do consumidor na ultima mensagem valida
        self.ata = "FECHADA"        # FECHADA | ABERTA | ENCERRANDO | SUSPENSA
        self.ata_desde = None       # quando a ata entrou no estado atual
        self._boots = {}            # deviceId -> [bootId, maior sequence aplicada]
        self._ids_vistos = OrderedDict()
        self.contadores = dict.fromkeys(
            ["aplicados", "telemetria", "duplicados", "atrasados", "rejeitados"], 0)

    # ------------------------------------------------------------- eventos --
    def receber_evento(self, bruto, agora):
        try:
            e = json.loads(bruto)
        except (ValueError, UnicodeDecodeError):
            return self._rejeitar("json_invalido")
        if not isinstance(e, dict):
            return self._rejeitar("json_nao_e_objeto")

        if e.get("schemaVersion") != SCHEMA_VERSION:
            return self._rejeitar(f"schemaVersion_nao_suportada:{e.get('schemaVersion')}", e)
        for campo, tipo in CAMPOS_OBRIGATORIOS.items():
            valor = e.get(campo)
            if isinstance(valor, bool) or not isinstance(valor, tipo):
                return self._rejeitar(f"campo_ausente_ou_invalido:{campo}", e)
        if not ID_VALIDO.match(e["deviceId"]):
            return self._rejeitar("deviceId_invalido", e)
        if e["entityId"] != self.entity_id:
            return self._rejeitar(f"entidade_desconhecida:{e['entityId']}", e)
        if e["eventType"] not in TIPOS_ESTADO | TIPOS_TELEMETRIA:
            return self._rejeitar(f"tipo_desconhecido:{e['eventType']}", e)
        if e["state"] not in ESTADOS_MIC:
            return self._rejeitar(f"estado_desconhecido:{e['state']}", e)
        if e["unit"] != "dBFS" or not FAIXA_DBFS[0] <= e["value"] <= FAIXA_DBFS[1]:
            return self._rejeitar(f"valor_fora_da_faixa:{e['value']} {e['unit']}", e)

        # Mensagem valida prova que o dispositivo esta vivo, mesmo se repetida.
        transicoes = self._marcar_vivo(agora)

        # Retry com o mesmo eventId: confirma de novo, mas nao aplica de novo.
        if e["eventId"] in self._ids_vistos:
            self.contadores["duplicados"] += 1
            return Resultado("DUPLICADO", f"eventId ja processado (tentativa {e['attempt']})",
                             e, self._ack(e, "duplicado"), transicoes)
        self._lembrar(e["eventId"])

        # A sequence recomeca quando o ESP32 reinicia; o bootId separa as vidas.
        boot = self._boots.get(e["deviceId"])
        if boot is None or boot[0] != e["bootId"]:
            if boot is not None:
                transicoes.append(f"reinicio de {e['deviceId']} detectado "
                                  f"(bootId {boot[0]} -> {e['bootId']})")
            boot = self._boots[e["deviceId"]] = [e["bootId"], 0]

        # O estado so anda para frente: evento antigo nao reescreve o presente.
        if e["sequence"] <= boot[1]:
            if e["eventType"] in TIPOS_ESTADO:
                self.contadores["atrasados"] += 1
                return Resultado("ATRASADO",
                                 f"sequence {e['sequence']} <= {boot[1]} ja aplicada",
                                 e, self._ack(e, "atrasado"), transicoes)
            self.contadores["telemetria"] += 1
            return Resultado("TELEMETRIA", "fora de ordem, ignorada", e, None, transicoes)
        boot[1] = e["sequence"]

        # Heartbeat e audio.level tambem carregam o estado: ressincronizam o
        # consumidor se uma mudanca de estado se perdeu ou ainda esta na fila.
        if e["state"] != self.mic:
            origem = "" if e["eventType"] in TIPOS_ESTADO else f" (ressincronizado por {e['eventType']})"
            transicoes.append(f"mic {self.mic} -> {e['state']}{origem}")
            self.mic = e["state"]
        transicoes += self._avaliar_ata(agora)

        if e["eventType"] in TIPOS_ESTADO:
            self.contadores["aplicados"] += 1
            return Resultado("APLICADO", e.get("reason", ""), e, self._ack(e, "aplicado"), transicoes)
        self.contadores["telemetria"] += 1
        return Resultado("TELEMETRIA", "", e, None, transicoes)

    # -------------------------------------------------------------- status --
    def receber_status(self, bruto, agora):
        """online/offline retido pelo broker; "offline" e o Last Will do ESP32."""
        try:
            s = json.loads(bruto)
        except (ValueError, UnicodeDecodeError):
            return Resultado("REJEITADO", "status_json_invalido")
        if not isinstance(s, dict) or s.get("status") not in ("online", "offline"):
            return Resultado("REJEITADO", "status_desconhecido", s if isinstance(s, dict) else None)

        if s["status"] == "offline":
            transicoes = self._perder_dispositivo("broker publicou o Last Will", agora)
        else:
            transicoes = self._marcar_vivo(agora) + self._avaliar_ata(agora)
        return Resultado("STATUS", s["status"], s, None, transicoes)

    # ---------------------------------------------------------------- tick --
    def tick(self, agora):
        """Chamado a cada segundo: expira o dado e avanca as janelas da ata."""
        if self.online and agora - self.visto_em > SILENCIO_DISPOSITIVO_S:
            return self._perder_dispositivo(
                f"nenhuma mensagem ha {agora - self.visto_em:.0f} s, dado expirado", agora)
        return self._avaliar_ata(agora)

    def resumo(self):
        c = self.contadores
        return (f"[{self.entity_id}] mic={self.mic} dispositivo={'online' if self.online else 'offline'} "
                f"ata={self.ata} | aplicados={c['aplicados']} duplicados={c['duplicados']} "
                f"atrasados={c['atrasados']} rejeitados={c['rejeitados']} telemetria={c['telemetria']}")

    # ------------------------------------------------------------- regras --
    def _avaliar_ata(self, agora):
        antes = self.ata
        mic = self.mic if self.online else "DESCONHECIDO"

        if mic == "FALANDO":
            novo, motivo = "ABERTA", "fala confirmada pelo microfone"
        elif mic == "SILENCIO":
            if antes in ("ABERTA", "SUSPENSA"):
                novo, motivo = "ENCERRANDO", f"silencio; fecha em {GAP_ATA_S:.0f} s se continuar"
            elif antes == "ENCERRANDO" and agora - self.ata_desde >= GAP_ATA_S:
                novo, motivo = "FECHADA", f"{GAP_ATA_S:.0f} s de silencio continuo"
            else:
                return []
        else:  # FALHA_SENSOR ou DESCONHECIDO: nao se sabe se ha fala
            if antes in ("ABERTA", "ENCERRANDO"):
                novo, motivo = "SUSPENSA", f"sem dado confiavel ({mic}); falta de dado nao e silencio"
            elif antes == "SUSPENSA" and agora - self.ata_desde >= SUSPENSA_MAX_S:
                novo, motivo = "FECHADA", f"{SUSPENSA_MAX_S:.0f} s sem dado confiavel; ata incompleta"
            else:
                return []

        if novo == antes:
            return []
        self.ata, self.ata_desde = novo, agora
        return [f"ata {antes} -> {novo} ({motivo})"]

    def _marcar_vivo(self, agora):
        self.visto_em = agora
        if self.online:
            return []
        self.online = True
        return ["dispositivo online"]

    def _perder_dispositivo(self, motivo, agora):
        transicoes = [f"dispositivo offline ({motivo})"] if self.online else []
        self.online = False
        if self.mic != "DESCONHECIDO":
            transicoes.append(f"mic {self.mic} -> DESCONHECIDO")
            self.mic = "DESCONHECIDO"
        return transicoes + self._avaliar_ata(agora)

    def _rejeitar(self, motivo, e=None):
        self.contadores["rejeitados"] += 1
        return Resultado("REJEITADO", motivo, e, self._ack(e, "rejeitado", motivo) if e else None)

    def _lembrar(self, event_id):
        self._ids_vistos[event_id] = True
        if len(self._ids_vistos) > MAX_IDS_VISTOS:
            self._ids_vistos.popitem(last=False)

    @staticmethod
    def _ack(e, resultado, motivo=""):
        # So mudanca de estado e confirmada; telemetria e enviar e esquecer.
        if e.get("eventType") not in TIPOS_ESTADO:
            return None
        if not isinstance(e.get("eventId"), str) or not isinstance(e.get("deviceId"), str):
            return None
        if not ID_VALIDO.match(e["deviceId"]):
            return None
        return {"schemaVersion": SCHEMA_VERSION, "eventId": e["eventId"],
                "deviceId": e["deviceId"], "result": resultado, "reason": motivo}
