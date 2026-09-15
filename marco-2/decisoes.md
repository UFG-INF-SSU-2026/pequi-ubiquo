# Marco 2 — decisões do grupo (TowerAI)

**Integrantes:** Thiago Nascente, Samuel Machado, Moisés Protázio, Vinícius Benevides
**Verificação:** 18/09/2026 · **Contrato:** [contrato.md](contrato.md) · **Como rodar:** [README.md](README.md)

Fronteira validada: **o microfone da sala avisa a mudança de estado e o consumidor decide a ata**, com confirmação, retry e queda de rede tratados de forma observável.

## As cinco decisões

### 1. Produtor: `mic-esp32-01`

Derivado do protótipo de Vinícius (Atividade 03, sensoriamento e qualidade). A leitura, a média móvel de 400 ms, o debounce de 300 ms e a falha explícita do sensor são os mesmos.

**Por que ele:**
- já produzia `audio.state.changed` com sequência;
- é a origem do dado na Decisão 2 da Atividade 02 (corte de silêncio no próprio dispositivo).

### 2. Consumidor: `consumidor/consumidor.py`

Recebe, valida, deduplica, mantém o estado da sala e decide a ata. O efeito aparece em dois lugares:
- **no terminal do consumidor:** estado do microfone, estado da ata e ACK enviado;
- **no monitor serial do ESP32:** ACK recebido e a fila esvaziando.

A regra fica em `consumidor/sala.py`, sem código de rede, e é testada por `test_sala.py`. Não integramos ao servidor Flask do TowerAI: o objetivo do Marco 2 é validar uma fronteira real, não o projeto inteiro.

### 3. Contrato mínimo: versão 1

Os campos do contrato da Atividade 03 mais:

| Campo | Para quê |
|---|---|
| `schemaVersion` | evoluir o formato |
| `eventId` | reconhecer repetição |
| `bootId` | a `sequence` recomeça quando o ESP32 reinicia |
| `attempt` | tornar o retry visível |

**Regra de versão:** campo novo opcional mantém a versão 1; remover, renomear ou mudar significado vira versão 2. O consumidor rejeita versão desconhecida.

### 4. Mecanismo: MQTT

| Requisito | Por que MQTT atende |
|---|---|
| O ESP32 no Wokwi web precisa alcançar o outro componente | Pelo gateway público do Wokwi, o ESP32 simulado conecta a brokers MQTT na internet. Para alcançar um servidor HTTP no nosso PC seria preciso o Private Gateway, que é pago |
| Queda do dispositivo precisa ser percebida | O **Last Will** faz o broker avisar a queda sem o consumidor fazer polling |
| O projeto terá mais de um consumidor (legenda, ata, painel, app Android) | Publicação e assinatura desacoplam em espaço: o ESP32 não conhece o endereço de ninguém |

**O que MQTT não resolve sozinho:** a PubSubClient só publica em QoS 0, então a entrega confiável vem do ACK de aplicação com `eventId`. E o QoS nunca diria se a regra foi aplicada.

**Custo assumido:** o broker público (`broker.hivemq.com`) contraria o "sem internet" e o risco de privacidade das Atividades 01 e 02. Na arquitetura real, o broker seria um Mosquitto no Raspberry Pi ou no PC host, com usuário e senha por dispositivo e TLS.

### 5. Responsabilidades

Proposta. **Todos precisam saber explicar e alterar qualquer parte** na verificação oral.

| Integrante | Parte no Marco 2 | Decisão do Marco 1 reaproveitada |
|---|---|---|
| Vinícius | produtor: sensoriamento, fila de estados, retry com espera crescente | média móvel, debounce, falha explícita |
| Samuel | entrega: `eventId`, deduplicação, ordem por `sequence` e `bootId`, ACK | deduplicação e detecção de silêncio |
| Moisés | tempo: expiração do dado (15 s), Last Will, janela de silêncio da ata (10 s) | janela de sessão e validade do dado |
| Thiago | decisão: estados da ata e efeito observável | atuação só em transição; faixa intermediária contra oscilação |

## O que foi preservado, adaptado e descartado

| | Decisão | Motivo |
|---|---|---|
| **Preservar** | pipeline de leitura do Vinícius | já trata ruído, oscilação e sensor ausente |
| **Preservar** | atuação só em transição (Thiago) | o consumidor só registra quando o estado muda |
| **Adaptar** | deduplicação do Samuel (`sequence <= último aceito`) | sozinha quebra no reinício: a sequência volta a 1 e tudo seria descartado. Agora é `eventId` mais sequência por `bootId` |
| **Adaptar** | expiração do dado do Moisés | foi para o consumidor: um dispositivo que cai não consegue avisar que caiu |
| **Corrigir** | no protótipo do Moisés, sensor desligado lia 0 e contava como silêncio, fechando a ata em 5 s | no consumidor, `FALHA_SENSOR` e `DESCONHECIDO` **suspendem** a ata; não fecham |
| **Descartar** | contadores ingênuos, display OLED, buzzer | serviam aos testes da Atividade 03; não fazem parte da fronteira |
| **Descartar, por ora** | ocupação por câmera (Thiago) | outro fluxo; pode entrar depois pelo mesmo contrato |

## Incompatibilidades encontradas e como resolvemos

| Incompatibilidade | Resolução |
|---|---|
| `entityId` diferente: `sala-204`, `sala-01`, `sala-reuniao-A` | contrato fixa `sala-204` |
| sem `schemaVersion` nem `eventId` em nenhum protótipo | adicionados |
| `sequence` só avançava em transição (Thiago, Moisés) ou a cada evento (Vinícius) | contrato: avança a cada evento criado |
| `eventTimeMs` é `millis()` de cada placa: não dá para comparar entre dispositivos nem entre boots | usado só dentro do mesmo `bootId`; as janelas do consumidor usam o relógio do consumidor |

## Falhas exercitadas na demonstração

1. **Resposta perdida e retry:** o botão *reenviar* repete o último estado com o mesmo `eventId`. O consumidor responde `duplicado` e não aplica de novo.
2. **Queda de rede:** o botão *rede* fecha a conexão sem DISCONNECT.
   - O broker publica o Last Will e a ata fica `SUSPENSA`.
   - As mudanças de estado ficam na fila do ESP32.
   - Na volta, a fila é entregue em ordem; estado antigo recebe `atrasado`.
3. **Consumidor fora do ar:** o ESP32 reenvia após 2 s, 4 s, 8 s, 10 s… Quando o consumidor volta, recebe ACK e esvazia a fila.
4. **Sensor desligado:** `FALHA_SENSOR` suspende a ata em vez de fechá-la.

## Limitações

- **Segurança:** broker público, sem TLS e sem autenticação. Qualquer um pode publicar nos tópicos; o consumidor valida o formato, mas não autentica a origem.
- **Tempo:** as janelas do consumidor (15 s, 10 s, 60 s) usam **tempo de processamento**, e não o tempo do evento como na Atividade 02. Aceitável aqui porque há uma única fonte e a latência da rede é pequena diante das janelas; eventos que saem da fila atrasados são tratados pela ordem da `sequence`.
- **Memória:** a fila do ESP32 (16 estados) e a memória de deduplicação do consumidor (1000 `eventId`) ficam na RAM. Se o consumidor reiniciar, um retry antigo pode ser aplicado de novo.
- **Escopo:** um microfone por sala.
- **Sensor:** o potenciômetro não é um microfone; o valor em dBFS é fictício.
