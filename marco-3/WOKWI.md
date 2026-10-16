# Rodar o Marco 3 no Wokwi — do início à primeira mensagem

**A opção 4 já iniciou o lado do PC:** broker local, consumidor e painel. A simulação da placa precisa ser iniciada separadamente no Wokwi. Ficar em “Aguardando o ESP32 publicar” é esperado enquanto o simulador não estiver conectado.

Este guia usa **Wokwi dentro do VS Code**, com os arquivos que já estão neste repositório. Esse caminho permite conectar a placa simulada ao broker instalado neste PC pelo gateway privado incluído na extensão. O circuito e o firmware continuam sendo os do marco 3.

**No Linux desta instalação**, use os [comandos com `sg docker -c` no README](README.md#execução-no-linux--comandos-com-sg). O comando de acompanhamento é `sg docker -c 'python3 marco-3/executar.py esp32 --wokwi'`; a compilação usa `python3 marco-3/ferramentas/compilar_produtor.py --wokwi`.

## 1. Abrir o projeto e habilitar o Wokwi no VS Code

1. Abra o **Visual Studio Code**.
2. Use **Arquivo → Abrir Pasta** e selecione a pasta principal **`pequi-ubiquo`**, onde estão `README.md` e a subpasta `marco-3`.
3. Abra **Extensões** na barra lateral (`Ctrl+Shift+X`). Procure **Wokwi Simulator**, do publicador Wokwi, e instale a extensão.
4. Pressione **F1**, digite **`Wokwi: Request a new License`** e selecione a ação.
5. Autorize abrir o site do Wokwi, entre na sua conta e use **GET YOUR LICENSE**. Autorize o retorno ao VS Code e aguarde a confirmação da ativação.

A extensão exige licença da sua conta Wokwi; a disponibilidade depende da modalidade de uso. No plano gratuito (Community), a página aberta pelo comando gera uma licença pessoal de 30 dias para uso não comercial; quando vencer, repita o comando. Essa ativação é feita por você no site e no VS Code. O roteiro oficial está em [Getting Started with Wokwi for VS Code](https://docs.wokwi.com/vscode/getting-started). Não é necessário montar outro circuito nem criar um projeto web para seguir este guia.

## 2. Preparar o firmware para a rede simulada

No VS Code, abra **Terminal → Novo Terminal**. Os comandos abaixo partem da pasta principal **`pequi-ubiquo`**.

Se o terminal anterior ainda está executando a opção 4, pressione **Ctrl+C** nele para encerrar aquela espera antes de iniciar outra cópia do consumidor/painel. Se voltar ao menu, escolha **0** para sair. O broker continua ativo. Digite os comandos seguintes no novo terminal, no prompt do PowerShell, e não no campo “Escolha uma opção” do menu.

```powershell
# Reutiliza a instalação e os certificados existentes
python marco-3/executar.py preparar

# Compila uma versão própria para o Wokwi
python marco-3/ferramentas/compilar_produtor.py --wokwi
```

O script detecta o Arduino CLI no PATH ou no cache local do projeto: `.tmp/arduino-cli/arduino-cli` no Linux/macOS e `.tmp/arduino-cli/arduino-cli.exe` no Windows, usando `.tmp/arduino-cli.yaml` quando recorre ao cache. Os binários do ESP32 não acompanham o Git; precisam ser gerados neste PC. Espere aparecer:

```text
Firmware compilado em produtor/build-wokwi/.
Wokwi: Wi-Fi Wokwi-GUEST, broker e NTP em host.wokwi.internal.
```

O comando produz os arquivos que o simulador usa:

```text
marco-3/produtor/
├── diagram.json                         circuito com ESP32, potenciômetro, botões e LEDs
├── wokwi.toml                           configuração que você selecionará no VS Code
└── build-wokwi/
    ├── sketch.ino.merged.bin             firmware completo
    └── sketch.ino.elf                    informações do programa para o simulador
```

Esse build usa automaticamente `Wokwi-GUEST` sem senha, **broker e NTP em `host.wokwi.internal`**, conta `mic-esp32-01` e CA já provisionadas. O nome especial permite ao simulador alcançar os serviços deste computador. TLS e a validação do certificado permanecem habilitados.

O arquivo `produtor/segredos.h` da placa física é preservado. O header do Wokwi só existe no diretório temporário de compilação. Os binários gerados também contêm a credencial, ficam fora do Git e precisam ser recompilados se a senha/CA mudar.

**Se o compilador não for encontrado**, siga a seção [Instalar o compilador em outro PC](#instalar-o-compilador-em-outro-pc), ao fim deste guia. O VS Code/Wokwi carrega o firmware já compilado; apertar Play não compila o `.ino`.

## 3. Iniciar o lado do PC com horário local para o simulador

No mesmo terminal, depois que a compilação terminar:

```powershell
python marco-3/executar.py esp32 --wokwi
```

**Deixe esse terminal aberto enquanto usa o simulador.** Ele deve mostrar:

```text
[OK] Broker local acessível em localhost:8883 (... certificado validado).
[OK] NTP local ativo em 127.0.0.1:4123/UDP; usa a hora deste PC.
[CONSUMIDOR] ... PRONTO ... aguardando eventos do microfone
[PAINEL] PRONTO: painel conectado; aguardando o resumo da sala
Aguardando o ESP32 publicar.
```

O parâmetro **`--wokwi`** acrescenta um relógio SNTP de laboratório no próprio PC. Isso é necessário porque o ESP32 valida a data do certificado TLS. A configuração da placa física continua usando o NTP da sala; na simulação, não é preciso ter o Raspberry Pi para obter horário.

Confira a data/hora do Windows antes de começar. O serviço fornece essa hora, sem consultar um NTP público e sem ajustar o relógio do PC. O firmware Wokwi consulta SNTP na porta própria 4123, para conviver com o serviço de hora do Windows na porta padrão 123. O serviço escuta apenas em `127.0.0.1:4123/UDP` e é fechado junto com esta execução. O broker permanece em `127.0.0.1:8883`.

## 4. Escolher a configuração certa e iniciar a placa simulada

Agora use a interface do VS Code, mantendo o terminal anterior em execução:

1. Pressione **F1**.
2. Escolha **`Wokwi: Select Config File`**.
3. Selecione **`marco-3/produtor/wokwi.toml`**. Há outra configuração no marco 2; confira o caminho selecionado.
4. Pressione **F1** outra vez e execute **`Wokwi: Start Simulator`**.
5. Aguarde abrir a aba do circuito: ESP32, um potenciômetro, os botões **sensor off**, **rede**, **reenviar** e quatro LEDs.

Para este roteiro, use a ação **`Wokwi: Start Simulator`** da paleta F1. **F5 / Iniciar Depuração** segue o fluxo de debug do VS Code e pode aguardar um depurador antes de executar a placa. O `wokwi.toml` também inclui `gdbServerPort = 3333`, exigido quando o modo debug é acionado. Alterar esse TOML não exige recompilar o firmware: salve o arquivo e reinicie a simulação. No sentido inverso, recompilar o firmware com a simulação aberta faz a extensão recarregar a placa sozinha.

O gateway privado já faz parte da extensão para VS Code. Neste caminho, não é preciso iniciar manualmente outro gateway. A configuração e os comandos são documentados em [Wokwi Project Configuration](https://docs.wokwi.com/vscode/project-config). O serviço Wokwi normalmente exige internet para executar a simulação; o broker e o relógio deste projeto continuam locais.

Mantenha a aba do simulador visível: o VS Code pode pausar a simulação quando ela fica oculta. Se quiser acompanhar também o painel, deixe o terminal na região inferior da janela.

## 5. Reconhecer a primeira conexão

Há duas saídas para acompanhar:

| Onde olhar | O que aparece |
|---|---|
| **Monitor serial da simulação**, junto à aba do Wokwi | mensagens de boot, Wi-Fi, TLS/MQTT e ACKs da placa |
| **Terminal em que roda `esp32 --wokwi`** | `[CONSUMIDOR]` recebendo eventos e `[PAINEL]` apresentando a ata |

No monitor serial, espere mensagens como:

```text
=== TowerAI / Marco 3 / produtor mic-esp32-01 ===
PRONTO
WiFi conectado, IP ...
MQTT: conectando a host.wokwi.internal...
MQTT conectado
ACK <- aplicado ...
FILA - entregue ...
```

`PRONTO` sozinho indica que a placa iniciou; **`MQTT conectado`** é o sinal de que ela alcançou e autenticou no broker. O LED azul indica conexão MQTT. No terminal do consumidor, devem aparecer mensagens `APLICADO`, `TELEMETRIA` ou transições do microfone. No painel, a ata aparece quando houver fala detectada.

## 6. Fazer a demonstração com o circuito

O potenciômetro representa o nível de áudio. Ele não é um microfone real.

| Ação na aba do Wokwi | Resultado esperado |
|---|---|
| Gire o potenciômetro para aproximadamente **30%** | `SILENCIO`; LED amarelo |
| Gire para aproximadamente **80%** e aguarde estabilizar | `FALANDO`; LED verde; consumidor abre a ata; painel mostra `Ata ABERTA` |
| Clique e solte **reenviar** | a placa repete o último estado; aparece `ACK <- duplicado` e a ação não é reaplicada |
| Clique e solte **rede** | LED azul apaga; o consumidor percebe a queda e a ata é suspensa |
| Ainda sem rede, mude o potenciômetro; depois clique em **rede** novamente | a placa reconecta e entrega os eventos que ficaram na fila |
| Com a ata aberta, **mantenha sensor off pressionado** | LED vermelho; `FALHA_SENSOR`; painel mostra ata suspensa, porque falha não significa silêncio |
| Solte **sensor off** com o nível alto | sensor volta a informar fala e a ata reabre |
| Gire novamente para cerca de **30%** e espere **10 s de silêncio contínuo** | ata fecha e o painel apresenta `Resumo final` |

Os tempos de sensoriamento seguem o relógio do simulador; o consumidor conta os segundos no PC. Em uma simulação muito lenta, atrasos de mensagens podem levar o consumidor a expirar os dados. Para repetir, basta mudar o potenciômetro e os botões; para recomeçar o firmware, pare e reinicie a simulação.

## 7. Encerrar

1. Pare a simulação pelo botão de parada da aba Wokwi.
2. No terminal de `esp32 --wokwi`, pressione **Ctrl+C**. Consumidor, painel e relógio local são encerrados.
3. Se também quiser parar o broker: `python marco-3/executar.py parar`.

## Resolver os problemas mais comuns

| Situação | Próximo passo |
|---|---|
| A opção 4 fica esperando | é o lado do PC; compile o build Wokwi, use `esp32 --wokwi` e inicie a simulação no VS Code |
| `wokwi.toml ... not found` | F1 → `Wokwi: Select Config File` → `marco-3/produtor/wokwi.toml` |
| `gdbServerPort is not set ... cannot start in debug mode` | salve o TOML atualizado, que inclui `gdbServerPort = 3333`; pare a tentativa de debug e use F1 → `Wokwi: Start Simulator` |
| A simulação aguarda conexão de um depurador | encerre a tentativa de depuração no VS Code e inicie pela ação `Wokwi: Start Simulator` para executar o roteiro normal |
| Arquivo `.bin` ou `.elf` não encontrado | execute `python marco-3/ferramentas/compilar_produtor.py --wokwi` e aguarde a compilação terminar; os dois arquivos devem estar em `marco-3/produtor/build-wokwi/`. Os caminhos do TOML são relativos a `marco-3/produtor/wokwi.toml`; selecione esse arquivo na extensão |
| Simulação do marco 2 ou broker público no serial | selecione a configuração do **marco 3**, compile com `--wokwi` e reinicie a simulação |
| `WiFi: aguardando conexao...` continuamente | use o build `--wokwi`, que configura `Wokwi-GUEST`; reinicie após compilar |
| `TLS: aguardando horario do NTP local...` continuamente | confira se `esp32 --wokwi` está aberto e se apareceu `NTP local ativo`; o firmware deve ter sido compilado com `--wokwi` |
| Não foi possível abrir UDP 4123 | confira se outra execução `esp32 --wokwi` já está aberta. Encerre aquela execução com Ctrl+C antes de abrir outra; se persistir, confira as permissões locais para UDP 4123 |
| `MQTT: falhou, estado=4` ou `5` | confira a conta do ESP32; após rotação de credencial, recompile com `--wokwi` e reinicie a simulação |
| Wi-Fi conecta, mas MQTT não | confira Docker, endereço `host.wokwi.internal`, certificado/relógio e gateway; teste o ambiente com `python marco-3/executar.py diagnostico` |
| Wokwi pede licença ou não ativa | conclua a ativação da extensão na conta Wokwi conforme a etapa 1 |
| Terminal recebe estado de outro produtor | pare demonstrações Python ou outra cópia do consumidor; nesta execução o produtor é `mic-esp32-01` |

## Instalar o compilador em outro PC

Se `--wokwi` já compilou, pule esta seção. Ela existe para uma instalação nova.

1. Instale o **Arduino CLI** seguindo a [instalação oficial](https://docs.arduino.cc/arduino-cli/installation/), deixando `arduino-cli` acessível pelo terminal.
2. Abra um novo terminal na raiz do projeto e execute:

```powershell
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@3.3.2 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli lib install PubSubClient@2.8
python marco-3/ferramentas/compilar_produtor.py --wokwi
```

O download das ferramentas ESP32 pode ser grande; prepare isso antes da apresentação. O script também aceita `--cli CAMINHO` e `--config-cli ARQUIVO` quando a instalação não está no PATH.

## Se você preferir o Wokwi no navegador

O navegador com gateway público não alcança este broker local. Nesse caminho é necessário ter acesso ao **Private IoT Gateway** do Wokwi, conforme a modalidade da conta; siga o [guia oficial de rede](https://docs.wokwi.com/guides/esp32-wifi). Na conta gratuita, o editor recusa o gateway com *"This feature is only available for paying users"*.

Será preciso iniciar o gateway no PC, habilitá-lo no editor web e usar o mesmo circuito/firmware provisionado para `host.wokwi.internal`. A disponibilidade de upload de firmware depende do plano. Este guia prioriza VS Code para reutilizar diretamente `diagram.json`, `wokwi.toml` e o build local, evitando copiar credenciais para um projeto web compartilhado.

## O que foi verificado e o que depende da sua conta

A configuração e o build do Wokwi são preparados neste projeto. O relógio SNTP tem testes locais de protocolo e socket; o fluxo MQTT já tem testes reais de TLS, ACK, resumo e painel. **A ativação da licença e o início da simulação na sua conta Wokwi são etapas que você executa no VS Code.** Compilar o firmware e testar o relógio local, isoladamente, não comprovam uma simulação completa pelo gateway.

Em 15/10/2026 a simulação completa foi executada por este guia, com licença do plano Community: conexão TLS com a conta `mic-esp32-01`, roteiro da seção 6 e revogação da conta (T10). O registro está em [evidências](evidencias/validacao.md#execução-do-firmware-no-wokwi).
