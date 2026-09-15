# Marco 2 — TowerAI: microfone → MQTT → consumidor

Um ESP32 (derivado do protótipo do Marco 1) publica as mudanças de estado do microfone da `sala-204`. Um consumidor em Python valida, deduplica, confirma e decide se a ata da sala está aberta. Falha, confirmação e retry ficam visíveis dos dois lados.

- **Decisões e responsabilidades:** [decisoes.md](decisoes.md)
- **Contrato (tópicos, payload, ACK, versão):** [contrato.md](contrato.md)

```
marco-2/
├── produtor/        sketch.ino, diagram.json, libraries.txt, wokwi.toml
│   ├── build/       firmware já compilado (merged.bin + elf)
│   └── testes/      integracao.yaml, falhas.yaml (cenários do wokwi-cli)
├── consumidor/      consumidor.py (MQTT), sala.py (regras), test_sala.py
├── ferramentas/     injetar.py (eventos de teste sem o ESP32)
└── evidencias/      serial do ESP32 e log do consumidor das simulações
```

## Pré-requisitos

- **Python 3.10+:** `pip install -r consumidor/requirements.txt`
- **Internet:** o Wokwi e o broker `broker.hivemq.com:1883` precisam dela.

## 1. Subir o consumidor

```bash
cd marco-2/consumidor
python consumidor.py          # -v mostra também heartbeat e audio.level
```

Espere `conectado a broker.hivemq.com:1883`.

## 2. Subir o produtor no Wokwi

1. Abra <https://wokwi.com/projects/new/esp32>.
2. Substitua `sketch.ino` e `diagram.json` pelo conteúdo de `produtor/`.
3. Adicione a biblioteca **PubSubClient**: aba *Library Manager* → `+` → PubSubClient. Ou crie `libraries.txt` com o conteúdo do arquivo.
4. Clique em **Play** e espere `MQTT conectado` no monitor serial.

Sem mudar o prefixo, qualquer pessoa que rodar o consumidor recebe os mesmos eventos. Para a demo, isso é o esperado.

### Se o Wokwi disser "Build Servers Busy"

A fila gratuita de compilação do Wokwi pode travar, inclusive no dia da demo. Nesse caso, pule a compilação:

1. No editor do Wokwi, com o `diagram.json` já colado, clique no código e aperte **F1**.
2. Escolha **Upload Firmware and Start Simulation…**.
3. Selecione `produtor/build/sketch.ino.merged.bin`, o firmware já compilado com bootloader, partição e aplicação.

**Esse `.bin` precisa ser recompilado sempre que o `sketch.ino` mudar.** Senão, a versão demonstrada deixa de ser a do repositório. Por isso, alterações pedidas na hora são mais seguras no consumidor, que é Python e não depende de compilação.

Evidência de que o produtor compilado roda de verdade: [evidencias/](evidencias/) tem o serial do ESP32 e o log do consumidor de uma simulação completa pelo `wokwi-cli` (ACK aplicado, duplicado, queda de rede com Last Will e fila entregue na reconexão).

## 3. Roteiro da demonstração (~4 min)

| # | Ação no Wokwi | ESP32 (serial) | Consumidor |
|---|---|---|---|
| 1 | potenciômetro para baixo (≈ −60 dBFS) | `FILA - entregue` | `mic -> SILENCIO`, ata `FECHADA` |
| 2 | potenciômetro para cima (≈ −20 dBFS) | `ACK <- aplicado`, LED verde | `APLICADO`, `ata FECHADA -> ABERTA` |
| 3 | botão **reenviar** | `REENVIAR … tentativa 2`, `ACK <- duplicado` | `DUPLICADO`; ata não muda |
| 4 | botão **rede**, depois potenciômetro para baixo | `REDE DERRUBADA`, `FILA + estado SILENCIO` | `STATUS offline`, `ata -> SUSPENSA` |
| 5 | botão **rede** de novo | `MQTT conectado`, `FILA - entregue` | `ata -> ENCERRANDO`; 10 s depois `FECHADA` |
| 6 | Ctrl+C no consumidor, mexa no potenciômetro | `RETRY sem ACK em 2000 ms`, 4000, 8000… | — |
| 7 | suba o consumidor de novo | `ACK <- aplicado`, `FILA - entregue` | `APLICADO` |
| 8 | segure **sensor off** com a ata aberta | LED vermelho, `FALHA_SENSOR` | `ata -> SUSPENSA` (não fecha) |

Cada evento tem `eventId` único (`mic-esp32-01:<bootId>:<sequence>`), visível nos dois lados.

## Testes

```bash
cd marco-2/consumidor && python -m unittest -v      # 16 testes das regras, sem rede
cd marco-2/ferramentas && python injetar.py todos   # consumidor rodando: duplicado, atrasado, versão, faixa, offline
```

Cenários do simulador (`wokwi-cli`, com `WOKWI_CLI_TOKEN` definido), rodados na pasta que contém `wokwi.toml`, `diagram.json` e `build/`:

| Cenário | Consumidor | Cobre |
|---|---|---|
| `testes/integracao.yaml` | ligado desde o início | ACK aplicado, retry duplicado, queda de rede com Last Will e fila entregue |
| `testes/falhas.yaml` | ligado só ~1 min depois do simulador | retry com espera crescente (2 s, 4 s, 8 s), entrega quando o consumidor volta, sensor off suspendendo a ata |

Os dois passaram. Os logs estão em [evidencias/](evidencias/). Mesmo simulador da extensão Wokwi do VS Code: **Wokwi: Select Config File** → `produtor/wokwi.toml` → **Start Simulation**.

## Alterações pequenas prováveis na verificação

| Pedido | Onde |
|---|---|
| mudar a janela que fecha a ata | `GAP_ATA_S` em `consumidor/sala.py` |
| mudar quando o dispositivo é dado como silencioso | `SILENCIO_DISPOSITIVO_S` em `sala.py` e `PERIODO_HEARTBEAT_MS` no sketch |
| mudar o limiar de fala | `LIMIAR_SILENCIO` no sketch |
| mudar a espera do retry | `ESPERA_ACK_INICIAL_MS` / `ESPERA_ACK_MAX_MS` no sketch |
| novo campo no evento | `montarEvento()` no sketch; se obrigatório, `CAMPOS_OBRIGATORIOS` em `sala.py` e `schemaVersion` 2 nos dois lados |
| trocar broker ou prefixo | `MQTT_BROKER`/`PREFIXO` no sketch e `--broker`/`--prefixo` no consumidor |

## Limitações

Ver [decisoes.md](decisoes.md#limitações). Resumo:
- broker público sem TLS nem autenticação;
- `eventTimeMs` é tempo desde o boot, não hora real;
- a fila do ESP32 e a memória de deduplicação ficam na RAM;
- o potenciômetro não é um microfone.
