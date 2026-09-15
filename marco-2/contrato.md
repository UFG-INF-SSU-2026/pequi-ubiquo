# Contrato — Marco 2 · versão 1

Fronteira entre o **produtor** (`mic-esp32-01`, ESP32 no Wokwi) e o **consumidor** (`consumidor/consumidor.py`), por **MQTT**.

## Tópicos

Prefixo: `ufg-ssu-2026/towerai` · Sala: `sala-204`

| Tópico | Publica | Assina | QoS efetivo | Retido |
|---|---|---|---|---|
| `…/sala-204/telemetry/audio` | ESP32 | consumidor | 0 | não |
| `…/sala-204/status/mic-esp32-01` | ESP32 (`online`) e broker (Last Will `offline`) | consumidor | 1 | sim |
| `…/sala-204/ack/mic-esp32-01` | consumidor | ESP32 | 1 | não |

Os eventos saem em QoS 0 porque a biblioteca PubSubClient só publica em QoS 0. A entrega confiável dos estados não depende do QoS: vem do ACK de aplicação, descrito abaixo.

## Evento

```json
{
  "schemaVersion": 1,
  "eventId": "mic-esp32-01:3fa2c1d0:17",
  "eventType": "audio.state.changed",
  "deviceId": "mic-esp32-01",
  "entityId": "sala-204",
  "bootId": "3fa2c1d0",
  "eventTimeMs": 18420,
  "sequence": 17,
  "value": -20.3,
  "unit": "dBFS",
  "state": "FALANDO",
  "reason": "media_acima_do_limiar_estavel",
  "attempt": 1
}
```

| Campo | Tipo | Significado |
|---|---|---|
| `schemaVersion` | inteiro | versão deste contrato |
| `eventId` | texto | `deviceId:bootId:sequence`. **Igual em todas as tentativas**; é a chave de deduplicação |
| `eventType` | texto | ver tabela de tipos |
| `deviceId` | texto | quem produziu. Só `A-Z a-z 0-9 _ -`, porque vira parte do tópico do ACK |
| `entityId` | texto | o que foi observado (a sala) |
| `bootId` | texto | sorteado a cada boot do ESP32. Separa a `sequence` de uma vida da anterior |
| `eventTimeMs` | inteiro | `millis()` quando o evento foi **criado**. Tempo desde o boot, não hora real |
| `sequence` | inteiro | cresce a cada evento criado; recomeça em 1 no boot |
| `value` | número | nível médio de áudio, faixa −90..0 |
| `unit` | texto | sempre `dBFS` |
| `state` | texto | `DESCONHECIDO`, `SILENCIO`, `FALANDO` ou `FALHA_SENSOR` |
| `reason` | texto | por que o estado é esse (opcional) |
| `attempt` | inteiro | 1 no primeiro envio; cresce a cada retry |

## Tipos de evento

| `eventType` | Quando | Garantia |
|---|---|---|
| `audio.state.changed` | o estado do microfone mudou | **fila + ACK + retry**. Criado mesmo sem rede; espera na fila (até 16) |
| `audio.level` | a cada 1 s enquanto `FALANDO` | enviar e esquecer. Sem rede, nem é criado |
| `device.heartbeat` | a cada 5 s, sempre | enviar e esquecer. Prova que o dispositivo está vivo |

A telemetria também carrega `state`. Se uma mudança de estado se perder, o próximo heartbeat ressincroniza o consumidor.

**Enquanto houver mudança de estado esperando ACK, o ESP32 não envia telemetria.** Se enviasse, a telemetria sairia com `sequence` maior e o consumidor marcaria a mudança de estado como `atrasado`. O teste no Wokwi mostrou exatamente isso antes da correção.

## Status

```json
{"schemaVersion": 1, "deviceId": "mic-esp32-01", "bootId": "3fa2c1d0", "status": "online"}
```

Ao conectar, o ESP32 publica `online` retido e registra no broker um **Last Will** com `status: "offline"`, também retido. Se a conexão cair sem DISCONNECT, o broker publica o `offline` sozinho.

## ACK (confirmação de negócio)

```json
{"schemaVersion": 1, "eventId": "mic-esp32-01:3fa2c1d0:17", "deviceId": "mic-esp32-01", "result": "aplicado", "reason": ""}
```

Só `audio.state.changed` recebe ACK. O ACK não diz "a mensagem chegou"; diz **o que o consumidor fez com ela**.

| `result` | Significa | O ESP32 faz |
|---|---|---|
| `aplicado` | mudou o estado da sala | tira da fila |
| `duplicado` | esse `eventId` já tinha sido processado | tira da fila |
| `atrasado` | já havia estado mais novo; não reescreve o presente | tira da fila |
| `rejeitado` | inválido (`reason` diz por quê); repetir não adianta | tira da fila |

Sem ACK, o ESP32 reenvia o **mesmo** evento (mesmo `eventId`, `attempt` + 1) após 2 s, 4 s, 8 s e depois a cada 10 s.

## Regras do consumidor, em ordem

1. **Validar:** JSON, `schemaVersion == 1`, campos e tipos, `deviceId` seguro, sala, tipo, estado e `value` em −90..0 dBFS. Falhou: `rejeitado`.
2. **Deduplicar:** `eventId` já visto (memória de 1000): `duplicado`, sem efeito.
3. **Ordenar:** para o mesmo `deviceId` e `bootId`, `sequence` menor ou igual à última aplicada é `atrasado`, sem efeito. `bootId` novo zera a contagem (reinício detectado).
4. **Aplicar:** atualiza o estado do microfone.
5. **Expirar:** 15 s sem nenhuma mensagem ou Last Will recebido: dispositivo offline, microfone `DESCONHECIDO`.
6. **Decidir a ata:**

| Microfone | Ata passa para |
|---|---|
| `FALANDO` | `ABERTA` |
| `SILENCIO` | `ENCERRANDO`; depois de 10 s de silêncio contínuo, `FECHADA` |
| `FALHA_SENSOR` ou `DESCONHECIDO` | `SUSPENSA` (falta de dado **não** é silêncio); depois de 60 s assim, `FECHADA` como incompleta |

## Versionamento

- **Campo novo opcional:** continua versão 1. Consumidores antigos ignoram o campo.
- **Remover ou renomear campo, mudar tipo, unidade ou significado:** vira versão 2.
- **Versão desconhecida:** o consumidor devolve `rejeitado` com `schemaVersion_nao_suportada`. O produtor tira o evento da fila em vez de repetir para sempre.
