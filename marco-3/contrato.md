# Contrato — Marco 3 · tópicos v1.1, payload de evento v1

Fronteira entre o **produtor** (`mic-esp32-01`, ESP32 no Wokwi) e o **consumidor** (`consumidor/consumidor.py`), por **MQTT sobre TLS 1.2/1.3**, com autenticação e ACL em **HiveMQ CE instalado localmente** (porta 8883).

## Tópicos

Prefixo: `ufg-ssu-2026/towerai` · Sala: `sala-204`

| Tópico | Publica | Assina | QoS efetivo | Retido |
|---|---|---|---|---|
| `…/sala-204/dev/mic-esp32-01/telemetry` | ESP32 | consumidor | 0 | não |
| `…/sala-204/dev/mic-esp32-01/status` | ESP32 (`online`) e broker (Last Will `offline`) | consumidor | 0 online / 1 Will | sim |
| `…/sala-204/dev/mic-esp32-01/ack` | consumidor | ESP32 | 1 | não |

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

1. **Conferir canal e identidade:** extrair `deviceId` apenas do tópico `dev/<deviceId>/{telemetry,status}`, validar a fonte configurada da sala e comparar com o payload antes de gerar ACK. Divergência: rejeitar **sem ACK** e sem alterar o estado.
2. **Validar:** JSON, `schemaVersion == 1`, campos e tipos, `deviceId` seguro, sala, tipo, estado e `value` em −90..0 dBFS. Falhou: `rejeitado`.
3. **Deduplicar:** `eventId` já visto (memória de 1000): `duplicado`, sem efeito.
4. **Ordenar:** para o mesmo `deviceId` e `bootId`, `sequence` menor ou igual à última aplicada é `atrasado`, sem efeito. `bootId` novo zera a contagem (reinício detectado).
5. **Aplicar:** atualiza o estado do microfone.
6. **Expirar:** 15 s sem nenhuma mensagem ou Last Will recebido: dispositivo offline, microfone `DESCONHECIDO`.
7. **Decidir a ata:**

| Microfone | Ata passa para |
|---|---|
| `FALANDO` | `ABERTA` |
| `SILENCIO` | `ENCERRANDO`; depois de 10 s de silêncio contínuo, `FECHADA` |
| `FALHA_SENSOR` ou `DESCONHECIDO` | `SUSPENSA` (falta de dado **não** é silêncio); depois de 60 s assim, `FECHADA` como incompleta |

## Identidade do canal e acesso

O broker autentica **todos os clientes**, inclusive o consumidor e o painel. A ACL usa a conta autenticada para permitir somente os tópicos do respectivo papel. `deviceId` no JSON não concede permissão. O destino de cada ACK vem do tópico de entrada, nunca de um campo arbitrário do payload.

O adaptador aceita uma fonte por sala (`--dispositivo`, padrão `mic-esp32-01`; na demo Python, `mic-injetor`). Os filtros MQTT usam `+` para facilitar a evolução, mas não agregam múltiplos microfones. Mensagens fora dessa fonte são descartadas. `status` também exige `schemaVersion` inteiro 1 e identidade correspondente. Telemetria retida é recusada. Há limites de 4096 bytes para eventos e 1024 bytes para status no consumidor.

Mudam os nomes dos tópicos em relação ao marco 2, por isso este é o **contrato de transporte v1.1**; o payload do sensor mantém `schemaVersion: 1`. Um cliente do marco 2 não se conecta nem interoperará com esta instalação sem migrar TLS, credencial, CA e tópicos. [Contas e ACLs](broker/README.md#papéis-e-permissões).

## Saída: `insights/reuniao`

Tópico: `ufg-ssu-2026/towerai/sala-204/insights/reuniao`. Somente o consumidor publica; o painel assina. QoS 1, retido. `reuniao.resumo` é uma informação calculada a partir do estado da sala, não um evento bruto de sensor.

| Campo | Significado |
|---|---|
| `schemaVersion`, `insightType` | inteiro 1, tipo `reuniao.resumo` |
| `insightId` | `sessaoId:revisão`, único por publicação da sessão |
| `entityId`, `sessaoId` | sala e UUID da sessão; reinício do processo cria outra sessão |
| `geradoEm`, `iniciadaEm` | timestamps ISO 8601 com fuso; data vem do host |
| `ata`, `final` | estado decidido e indicador do encerramento definitivo |
| `motivo` | `transicao` ou `periodico`; avisos de disponibilidade descritos abaixo |
| `duracaoS`, `falaS`, `silencioS`, `lacunaS` | segundos desde a decisão de abertura; soma de fala + silêncio + lacuna |
| `taxaFala` | fala / (fala + silêncio), entre 0 e 1; `null` se não houve dado observado; lacunas não entram no denominador |
| `pausas` | número de retornos `ENCERRANDO → ABERTA` |
| `nivelMedioDbfs`, `nivelPicoDbfs` | média aritmética e maior nível dos `audio.level` válidos e aplicados durante fala; `null` sem amostras |
| `atividade` | fala nos últimos 60 s: `<10 s` BAIXA, `<35 s` MEDIA, demais ALTA |
| `qualidade` | zero lacuna: COMPLETA; lacuna até 10%: PARCIAL; acima disso ou fechamento por suspensão: INCOMPLETA |
| `dispositivo` | `online` e `microfone` observados na fonte configurada |
| `avisos` | lista; inclui o tempo sem dado confiável quando houver lacuna |

O intervalo passado pertence ao estado anterior: online + FALANDO conta como fala, online + SILENCIO como silêncio, qualquer outra combinação como lacuna. Os cálculos usam `time.monotonic()` e a janela de 60 s recorta os intervalos na fronteira. A duração começa quando a Sala abre a ata; não recupera fala anterior à abertura. São medidas de atividade sonora, sem identificação de pessoas nem reconhecimento de voz.

A publicação ocorre em transição de ata e a cada 30 s durante uma sessão ativa. Entrar em FECHADA produz `final: true`. `audio.level` não é retransmitido individualmente no tópico de saída.

Antes de conectar, o consumidor registra Last Will retido com apenas `schemaVersion`, `insightType`, `entityId`, `motivo: consumidor_offline`, `ata: DESCONHECIDO`, `final: false`. O timestamp do Will não representa a hora da queda e é omitido. Encerramento normal também publica esse aviso, pois DISCONNECT suprime o Last Will. Toda conexão publica primeiro `consumidor_iniciando` para invalidar retained de uma execução anterior.

O painel trata avisos de disponibilidade imediatamente como desconhecidos. Resumos ativos com mais de 90 s ou mais de 5 s no futuro são desconhecidos; o timer continua correndo mesmo sem mensagens. Desconexão do próprio painel também invalida sua indicação. Resumo final de sessão encerrada pode ser consultado depois. O painel ignora campos adicionais e recusa versão/sala/formato desconhecido.

## Versionamento

- **Campo novo opcional:** continua versão 1. Consumidores antigos ignoram o campo.
- **Remover ou renomear campo, mudar tipo, unidade ou significado:** vira versão 2.
- **Versão desconhecida:** o consumidor devolve `rejeitado` com `schemaVersion_nao_suportada`. O produtor tira o evento da fila em vez de repetir para sempre.
