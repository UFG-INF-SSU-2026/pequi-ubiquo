# Validação do Marco 3

Execução em **15/10/2026**, no PC Windows do projeto. Este registro descreve as verificações executadas; não reaproveita como evidência do canal seguro os logs antigos do marco 2.

## Ambiente

| Componente | Versão/configuração |
|---|---|
| Python | 3.14.6 em `.venv` local |
| Paho MQTT | 2.1.0 |
| cryptography | 47.0.0 |
| Docker Engine | 29.8.2, Docker Desktop com motor Linux |
| Broker | HiveMQ CE 2025.5, Java 21, imagem `hivemq/hivemq-ce:2025.5` |
| Autenticação/autorização | File RBAC 4.6.15, hashes PBKDF2-SHA512 |
| Rede do laboratório | bind `127.0.0.1:8883`; sem publicação de 1883 |
| Firmware | Arduino ESP32 core 3.3.2, PubSubClient 2.8.0, alvo `esp32:esp32:esp32` |

Os certificados incluem SKI/AKI e uso de chave adequado para a verificação estrita dos clientes Python. O keystore PKCS#12 cifrado foi efetivamente lido pelo Java 21 da imagem. Os caminhos atuais `conf/config.xml` e `conf/credentials.xml` da extensão foram utilizados. A pasta montada contém somente a extensão RBAC.

## Resultados

| Verificação | Resultado observado |
|---|---|
| `python -m unittest discover -s marco-3/consumidor -v` | **29 testes aprovados**: regras herdadas, identidade, limites de payload e resumo |
| `python -m unittest discover -s marco-3/painel -v` | **5 testes aprovados**: expiração, retained, Last Will e entradas inválidas |
| `python marco-3/ferramentas/verificar_broker.py` | **4 testes ativos aprovados**, com múltiplos cenários de TLS/autenticação/ACL |
| `python marco-3/ferramentas/testar_fluxo.py` | integração real aprovada, com consumidor e painel em processos separados e produtor Python autenticado |
| Compilação do firmware | **aprovada** para ESP32; 1.023.551 bytes de programa (78%) e 46.620 bytes de variáveis globais (14%) |
| Revogação e rotação de `mic-injetor` | perfil antigo recusado após revogação + restart; perfil rotacionado aceito, preservando a CA |
| `python -m compileall -q marco-3` | sem erro de sintaxe Python |
| `git diff --check` | sem erro de whitespace nos arquivos rastreados |
| Preservação do marco 2 | `git diff -- marco-2` vazio |
| Segredos/build | `git check-ignore` confirmou ignorados: keystore, perfis `.env`, `produtor/segredos.h`, firmware e cache de ferramentas |

### Provas ativas do broker

Foram observados: handshake TLS válido; falha com CA não confiável; falha com hostname incorreto; recusa de conexão anônima e de senha incorreta; porta 1883 indisponível; dispositivo autorizado a enviar sua telemetria e receber seu ACK; negação de escrita no tópico de outro dispositivo, ACK falso e assinatura ampla; painel autorizado a ler resumo e impedido de publicar ou ler telemetria.

O teste de permissões usa MQTT 5 para capturar a falha por PUBACK/SUBACK e verifica entrega autorizada. Cada tentativa negativa usa conexão independente: o HiveMQ pode encerrar a sessão após publicação negada. Isso não altera o firmware, que permanece MQTT 3.1.1 com ACK de aplicação.

### Facilidade de execução — iniciador do Marco 3

Na melhoria de execução, também foram verificados:

- `INICIAR-MARCO-3.cmd`: abertura da ajuda e do menu, inclusive chamado com o terminal dentro de `marco-3`, e saída pela opção 0.
- `python marco-3/executar.py diagnostico`: Docker, dependências, TLS e login individual de consumidor, painel e injetor aprovados.
- `python marco-3/executar.py demo`: preparação reutilizada, assinaturas confirmadas antes de iniciar o sensor e roteiro narrado no mesmo terminal. O painel apresentou `Resumo final`, com qualidade `PARCIAL` após a falha simulada. Os processos filhos encerraram e o broker permaneceu ativo.
- `python marco-3/executar.py parar`: broker parado sem apagar arquivos; o diagnóstico seguinte apontou corretamente a ausência do canal TLS.
- Duas execuções de `preparar`, uma da raiz e outra com o terminal dentro de `marco-3`: broker reiniciado e **hashes de CA, keystore, configuração e perfis inalterados**. Isso comprova que repetir a preparação não regenerou segredos.
- Modo `esp32`: consumidor e painel chegaram à espera pela placa sem gerar eventos. Uma interrupção Windows (`CTRL_BREAK_EVENT`, tratada como `KeyboardInterrupt`) encerrou os clientes normalmente, com MQTT DISCONNECT, preservando o broker. Este teste verifica a orquestração; não substitui o teste da placa física.
- Os **34 testes de lógica** continuaram aprovados e `ferramentas/testar_fluxo.py` foi repetido com sucesso após adicionar os avisos de prontidão e o encerramento assistido.

Os atalhos Windows e a operação local foram testados neste PC. A preparação reutilizou o ambiente já instalado; este registro não afirma uma instalação do zero em outro computador nem validação do iniciador em Linux/macOS. A documentação desses sistemas mantém os requisitos de permissões dos volumes do broker.

### Prova do fluxo

Saída do teste de integração:

```text
OK: telemetria TLS, ata aberta e retry deduplicado
OK: resumo final coerente, sem lacuna e com nível de áudio
OK: novo assinante recebeu o resumo final retido
OK: queda abrupta publicou Last Will do consumidor
OK: processo do painel mostrou resumo e invalidou estado indisponível
```

O teste matou deliberadamente o processo filho do consumidor, sem MQTT DISCONNECT; o aviso de indisponibilidade veio do **Last Will publicado pelo broker**, não de uma chamada manual de status. Os processos criados pelo teste foram encerrados ao final.

### Compilação e acesso

O Arduino CLI e as dependências de compilação foram preparados no cache `.tmp/` do repositório, sem mudar uma instalação Arduino global. A compilação usou o script `ferramentas/compilar_produtor.py` com as opções `--cli .tmp/arduino-cli/arduino-cli.exe --config-cli .tmp/arduino-cli.yaml`. Uma primeira tentativa dentro do sandbox falhou por relocação de DLL do Windows; a execução do mesmo compilador fora do sandbox compilou o código com sucesso.

O `segredos.h` gerado ainda contém **campos de Wi-Fi e NTP a ajustar para a sala**. Compilar não comprova conectividade física. Os binários contêm o segredo provisionado e permanecem fora do Git. Após alterar rede, NTP, endereço ou credencial do ESP32, recompilar e regravar; os binários desta execução não devem ser compartilhados.

### Preparação específica para Wokwi

O guia [WOKWI.md](../WOKWI.md) foi adicionado para ligar a opção 4 à execução do simulador. O compilador agora aceita `--wokwi`, usa o toolchain local quando disponível e gera `produtor/build-wokwi/`, com Wi-Fi `Wokwi-GUEST` e broker/relógio em `host.wokwi.internal`. Esse build temporário não altera o header da placa física; `produtor/wokwi.toml` aponta para os binários separados, ignorados pelo Git.

Foi verificado que o serviço de hora do Windows ocupa UDP 123 neste PC e não oferece NTP servidor no loopback. A integração do simulador usa **UDP 4123**, restrita a `127.0.0.1`, com cliente SNTP de laboratório no build Wokwi. `esp32 --wokwi` administra o relógio junto com os clientes MQTT. O Windows não teve serviço, registro ou firewall reconfigurados.

Verificações executadas: compilação do build Wokwi com ESP32 core 3.3.2 e PubSubClient 2.8; três testes de protocolo/socket do relógio aprovados; consulta real a `127.0.0.1:4123/UDP` com timestamp consistente e porta liberada ao encerrar; os 34 testes de lógica da aplicação continuam aprovados; caminhos de firmware/ELF no TOML conferidos.

**Ainda depende da conta Wokwi do usuário:** ativação da extensão/licença e execução do simulador através do gateway. Não foi declarada uma simulação completa aprovada apenas com base na compilação e na consulta NTP local. Essa execução foi feita depois, em outro PC: ver [Execução do firmware no Wokwi](#execução-do-firmware-no-wokwi).

## Execução do firmware no Wokwi

Executada em **15/10/2026, à tarde**, no PC Windows do Vinícius, com o código desta pasta (`main` em `ce2a45c`) e provisionamento próprio desta máquina (`executar.py preparar`: CA, contas e broker gerados aqui). Antes da simulação, as verificações da seção anterior foram repetidas nesta máquina e aprovadas: 29 + 5 testes de lógica, 3 testes do relógio local, 4 testes ativos do broker e `ferramentas/testar_fluxo.py`.

| Componente | Versão/configuração |
|---|---|
| Python / Docker | 3.13.14 em `.venv` / Docker Desktop 29.6.1, motor Linux |
| Simulador | Wokwi for VS Code 4.0.0, com o gateway privado embutido na extensão |
| Licença | pessoal de 30 dias do plano Community (gratuito), gerada em wokwi.com/license |
| Firmware | `compilar_produtor.py --wokwi`, core ESP32 3.3.2 e PubSubClient 2.8.0: 1.014.223 bytes (77%) |
| Lado do PC | `executar.py esp32 --wokwi`: broker em `127.0.0.1:8883`, NTP local em `127.0.0.1:4123/UDP`, consumidor e painel |

O Wokwi no navegador não serve com a conta gratuita: ao ativar o gateway privado, o editor responde *"This feature is only available for paying users"*. Por isso a execução usou a extensão do VS Code, que alcança o broker local por `host.wokwi.internal`.

### Roteiro com a placa simulada

Horários do relógio do consumidor. O `eventId` do boot era `mic-esp32-01:07d8e679:<sequence>`.

| Hora | Ação no circuito | Observado no consumidor e no painel |
|---|---|---|
| 15:29:53 | placa liga | `STATUS online` pela conta `mic-esp32-01` sobre TLS; primeiro estado (`SILENCIO`) aplicado; tentativa 2 logo em seguida, `DUPLICADO` (ver correção abaixo) |
| 15:32:49 | potenciômetro alto | microfone `FALANDO`; ata `FECHADA → ABERTA`; painel "Ata ABERTA: reunião detectada" |
| 15:33:13 | botão **reenviar** | mesmo `eventId`, tentativa 2: `DUPLICADO` e ACK `duplicado`; ata inalterada |
| 15:33:19 e 15:33:49 | — | resumo periódico a cada 30 s com a ata aberta (R4) |
| 15:34:06 | botão **rede** | broker publica o Last Will `offline`; microfone `DESCONHECIDO`; ata `SUSPENSA`; painel alerta falta de dados |
| 15:34:29 | **rede** de novo | reconexão TLS; o heartbeat ressincroniza `FALANDO`; ata `ABERTA` com qualidade `INCOMPLETA` |
| 15:35:25 | segurar **sensor off** | `FALHA_SENSOR` (`sensor_nao_respondeu_300ms`); ata `SUSPENSA`, não `FECHADA` |
| 15:35:28 | soltar | `FALANDO`; ata `ABERTA` |
| 15:35:50 | **rede**, potenciômetro sem rede | Last Will; ata `SUSPENSA`; três mudanças de estado ficam na fila do ESP32 |
| 15:36:15 | **rede** de novo | fila entregue em ordem (`:218`, `:219`, `:220`), todos com ACK; tentativa 2 de `:218` respondida `DUPLICADO` |
| 15:36:26 | 10 s de silêncio | ata `FECHADA`; painel: "Resumo final: duração=217.172s, fração de fala=0.936, qualidade=INCOMPLETA" |

A qualidade `INCOMPLETA` é esperada: as duas quedas de rede e a falha do sensor somaram mais de 10% da sessão sem dado confiável.

### T10: revogação com a placa conectada

| Hora | Ação | Observado |
|---|---|---|
| 15:47:04 | `gerenciar_acesso.py mic-esp32-01 --revogar` | conta removida de `credentials.xml` |
| 15:47:17 | depois da releitura do RBAC (10 s) | a placa continua conectada: a revogação não derruba a sessão existente |
| 15:47:33 | `docker compose -f marco-3/broker/compose.yaml restart` | consumidor e painel reconectam às 15:47:48; a placa não |
| ~15:48:50 | assinar `dev/mic-esp32-01/telemetry` e `status` com a conta do consumidor por 15 s | nenhuma telemetria (conectada, a placa envia heartbeat a cada 5 s); só o status retido `offline` do boot `07d8e679` |
| 15:49:51–15:50:03 | portas remotas das conexões em `8883` | consumidor e painel mantêm portas fixas; a terceira muda a cada ~3 s: a placa tenta reconectar no intervalo `RECONEXAO_MS` e é recusada |
| 15:50:18 | `gerenciar_acesso.py mic-esp32-01 --rotacionar` e `compilar_produtor.py --wokwi` | a extensão recarregou o firmware sozinha; às 15:52:19 `STATUS online` com a senha nova, e o consumidor detectou o reinício (`bootId 07d8e679 → d28f0973`) |

O monitor serial da placa não foi capturado nesta etapa; a recusa foi observada pelo lado do broker.

### Correção no firmware

Na primeira conexão e em cada reconexão com estado na fila, o consumidor recebia a tentativa 2 logo depois da 1. Não havia efeito na ata, porque a deduplicação respondia `duplicado`. Causa: `processarFila()` gravava `enviadoEmMs` com o `agora` lido no início do `loop()`, antes de `conectar()`. Como o handshake TLS no simulador leva mais que a espera inicial de 2 s, o loop seguinte já via a espera estourada. Correção: `enviadoEmMs = millis()`. Depois de recompilar, o boot das 15:52:20 teve o primeiro estado `APLICADO` sem tentativa 2. A reconexão com fila depois da correção ainda não foi repetida.

### Estado deixado no PC do Vinícius

Broker local ativo em `127.0.0.1:8883`. Conta `mic-esp32-01` rotacionada: a senha nova está só nos perfis locais e no firmware do Wokwi, fora do Git. O log da sessão ficou em `evidencias/wokwi-esp32-2026-10-15.log`, também fora do Git.

## O que ainda precisa ser validado na sala

- Execução no ESP32 físico. O Wokwi com gateway privado foi executado: ver [Execução do firmware no Wokwi](#execução-do-firmware-no-wokwi).
- NTP do Raspberry Pi/PC e comportamento com horário incorreto/indisponível no sensor.
- Queda real de Wi-Fi no hardware: no Wokwi, o botão **rede** fecha apenas a conexão MQTT. No simulador também não foi repetido o passo do consumidor fora do ar (retry com 2 s, 4 s e 8 s).
- Operação pela interface LAN com IP/DNS/SAN da instalação e regras locais de firewall.
- Operação completamente offline com hardware real, após provisionar dependências e relógio.
- Encerramento normal por Ctrl+C está implementado para publicar indisponibilidade, mas a prova de integração automatizada exercita a **queda abrupta**.

A aplicação Flask não foi ligada à gravação automática; o painel comunica a decisão da ata. Não foram declarados como implementados mTLS, assinatura ponta a ponta, persistência ou quarentena automática no broker. O alerta de 20 inválidos/60 s requer ação administrativa de revogação.

## Estado deixado após a validação

Broker local instalado e em execução em `127.0.0.1:8883`; perfis de acesso gerados em `broker/local/`. Consumidor, painel e produtores de teste encerrados. A conta descartável `mic-injetor` foi rotacionada e está disponível no perfil local atualizado para a próxima demonstração; revogá-la ao finalizar o uso.
