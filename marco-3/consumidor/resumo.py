"""Transforma decisões da Sala em informação publicável, sem dependência MQTT.

Tempo monotônico mede intervalos; datetime apenas carimba a saída. Cada
intervalo pertence ao estado ANTERIOR. Falha/offline conta como lacuna, nunca
silêncio. A janela de atividade recorta intervalos na fronteira dos 60 s.
"""
from collections import deque
from uuid import uuid4

JANELA_ATIVIDADE_S = 60.0
LIMIARES_ATIVIDADE_S = (10.0, 35.0)
PERIODO_PUBLICACAO_S = 30.0
LACUNA_PARCIAL = 0.10


class Resumo:
    def __init__(self, sala_id):
        self.sala_id = sala_id
        self._t = None
        self._cat = "lacuna"
        self._ata = "FECHADA"
        self._ultima_pub = None
        self.sessao = None
        self._zerar()

    def _zerar(self):
        self.iniciada_em = None
        self.fala_s = self.silencio_s = self.lacuna_s = 0.0
        self.pausas = self._rev = self._n = 0
        self._soma = 0.0
        self.pico = None
        self._janela = deque()  # intervalos (início, fim), apenas fala
        self._forcar_incompleta = False

    def registrar_nivel(self, dbfs):
        """Chamado só para audio.level válido, aplicado e com estado FALANDO."""
        if self._ata == "FECHADA":
            return
        self._soma += dbfs
        self._n += 1
        self.pico = dbfs if self.pico is None else max(self.pico, dbfs)

    def forcar_publicacao(self, agora):
        """Após reconexão, substitui o aviso de inicialização no próximo tick."""
        if self._ata != "FECHADA":
            self._ultima_pub = agora - PERIODO_PUBLICACAO_S

    def avancar(self, sala, agora, agora_dt):
        """Contabiliza e devolve zero ou um resumo por transição/30 segundos."""
        anterior = self._t
        if anterior is not None and agora < anterior:
            raise ValueError("o relógio monotônico não pode retroceder")
        self._t = agora
        dt = 0.0 if anterior is None else agora - anterior
        if self._ata != "FECHADA" and dt > 0:
            if self._cat == "fala":
                self.fala_s += dt
                self._janela.append((anterior, agora))
            elif self._cat == "silencio":
                self.silencio_s += dt
            else:
                self.lacuna_s += dt
        limite = agora - JANELA_ATIVIDADE_S
        while self._janela and self._janela[0][1] <= limite:
            self._janela.popleft()
        if self._janela and self._janela[0][0] < limite:
            self._janela[0] = (limite, self._janela[0][1])

        saidas = []
        if sala.ata != self._ata:
            antes, self._ata = self._ata, sala.ata
            if antes == "FECHADA":
                self._zerar()
                # UUID evita colidir sessões após reinício no mesmo segundo.
                self.sessao = f"{self.sala_id}-{uuid4().hex}"
                self.iniciada_em = agora_dt.isoformat(timespec="seconds")
            if antes == "ENCERRANDO" and sala.ata == "ABERTA":
                self.pausas += 1
            if antes == "SUSPENSA" and sala.ata == "FECHADA":
                self._forcar_incompleta = True
            saidas.append(self._montar(sala, agora_dt, "transicao", sala.ata == "FECHADA"))
        elif self._ata != "FECHADA" and agora - self._ultima_pub >= PERIODO_PUBLICACAO_S:
            saidas.append(self._montar(sala, agora_dt, "periodico", False))
        if saidas:
            self._ultima_pub = agora
        self._cat = ("fala" if sala.online and sala.mic == "FALANDO" else
                     "silencio" if sala.online and sala.mic == "SILENCIO" else "lacuna")
        return saidas

    def _montar(self, sala, agora_dt, motivo, final):
        self._rev += 1
        duracao = self.fala_s + self.silencio_s + self.lacuna_s
        observado = self.fala_s + self.silencio_s
        fracao = self.lacuna_s / duracao if duracao else 0.0
        qualidade = ("INCOMPLETA" if self._forcar_incompleta or fracao > LACUNA_PARCIAL else
                     "PARCIAL" if self.lacuna_s > 0 else "COMPLETA")
        fala_janela = sum(fim - inicio for inicio, fim in self._janela)
        baixo, alto = LIMIARES_ATIVIDADE_S
        return {
            "schemaVersion": 1, "insightType": "reuniao.resumo",
            "insightId": f"{self.sessao}:{self._rev}", "entityId": self.sala_id,
            "sessaoId": self.sessao, "geradoEm": agora_dt.isoformat(timespec="seconds"),
            "motivo": motivo, "final": final, "ata": sala.ata,
            "iniciadaEm": self.iniciada_em, "duracaoS": round(duracao, 3),
            "falaS": round(self.fala_s, 3), "silencioS": round(self.silencio_s, 3),
            "lacunaS": round(self.lacuna_s, 3),
            "taxaFala": round(self.fala_s / observado, 3) if observado else None,
            "pausas": self.pausas,
            "nivelMedioDbfs": round(self._soma / self._n, 1) if self._n else None,
            "nivelPicoDbfs": self.pico,
            "atividade": "BAIXA" if fala_janela < baixo else "MEDIA" if fala_janela < alto else "ALTA",
            "qualidade": qualidade,
            "dispositivo": {"online": sala.online, "microfone": sala.mic},
            "avisos": [f"sem_dado_confiavel_{self.lacuna_s:.3f}s"] if self.lacuna_s else [],
        }
