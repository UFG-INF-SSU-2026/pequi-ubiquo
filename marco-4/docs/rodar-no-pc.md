# Rodar e testar o TowerAI no Windows

Este roteiro executa o TowerAI a partir do código-fonte em um clone do `pequi-ubiquo`. O Android fornece o áudio, a webcam publica a estimativa de presença e o PC faz a transcrição, gera a ata e guarda o histórico. Para usar o painel gráfico, siga o [guia do instalador Windows](servidor-windows.md).

## Preparar um clone novo

Com Python 3.13 ou 3.14 de 64 bits disponível no PowerShell, prepare os componentes abaixo. Os arquivos baixados ficam em pastas ignoradas pelo Git. Reserve cerca de 6,3 GB para os pesos de transcrição, ata e visão usados neste roteiro.

1. Em `marco-4/servidor`, [crie o ambiente Python e baixe o Whisper Large v3 Turbo](implantacao.md#1-preparar-o-servidor).
2. Em `marco-4`, [baixe o executável Windows do llama.cpp](implantacao.md#baixar-o-servidor-de-texto-para-windows) e [salve as duas partes do modelo Qwen](implantacao.md#baixar-e-iniciar-o-modelo-de-ata). Você vai iniciar o modelo na aba 1 deste roteiro.
3. Em `marco-4/servidor`, [baixe os pesos do detector YOLOX-S](implantacao.md#baixar-os-pesos-do-detector). Você vai iniciar a webcam na aba 3.

## Antes de começar

1. Instale o [APK do TowerAI](../apk/TowerAI-Marco4.apk) no Android. Se já tiver a versão anterior, instale a atualização por cima.
2. Conecte o PC e o Android à mesma rede local. O PC pode ficar conectado por cabo ao mesmo roteador do Wi‑Fi do celular.
3. Rode `ipconfig` no PC e anote o IPv4 da interface conectada à mesma rede do Android. Substitua `192.168.1.20` pelo seu IPv4 no certificado, no app e nos exemplos de diagnóstico.
4. Conecte a webcam por USB. Abra o aplicativo **Câmera** do Windows para conferir a imagem e feche-o antes de iniciar o produtor de presença.

Abra três abas do PowerShell, todas na raiz do clone `pequi-ubiquo`, e mantenha os processos rodando durante o teste.

## Aba 1 · Modelo que escreve a ata

```powershell
Set-Location -LiteralPath '.\marco-4'
& '.\.ferramentas\llama\llama-server.exe' --model '.\.ferramentas\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf' --alias qwen2.5-7b --host 127.0.0.1 --port 18082 --ctx-size 8192 --threads 8
```

Quando aparecer `listening on http://127.0.0.1:18082`, siga para a aba 2.

## Aba 2 · Servidor HTTPS e transcrição

```powershell
Set-Location -LiteralPath '.\marco-4\servidor'
$towerPython = '.\.venv\Scripts\python.exe'
```

Na primeira configuração, gere o certificado e as duas credenciais com o seu IP local. Se já estiverem configurados para o endereço atual, siga para **Iniciar a API**.

```powershell
& $towerPython main.py cert 192.168.1.20 127.0.0.1
& $towerPython main.py provision android-01 microphone sala-204
& $towerPython main.py provision webcam-01 presence sala-204
```

Use a credencial de `android-01` no campo **Credencial do microfone** e a de `webcam-01` na aba 3. Se perder uma delas, com a API parada, repita só o `provision` desse dispositivo e passe a usar o código novo.

### Iniciar a API

```powershell
& $towerPython main.py serve --bind 0.0.0.0 --port 8443 --model large-v3-turbo --device cpu
```

O terminal deve mostrar `TowerAI HTTPS em 0.0.0.0:8443`. Nesse diretório, a API usa os pesos em `servidor/modelos` e o banco em `servidor/local/towerai.sqlite3`.

Para usar uma GPU NVIDIA compatível, prepare as [dependências CUDA](implantacao.md#1-preparar-o-servidor) e troque `--device cpu` por `--device cuda`.

Se o IP da rede mudou desde a emissão do certificado, pare a API, renomeie `local/tls` para guardar a instalação TLS anterior e rode `main.py cert` com o IPv4 novo e `127.0.0.1`. Importe o novo `ca.pem` no celular. Preserve `local/devices.json` e `local/towerai.sqlite3` para manter as credenciais e o histórico.

## Aba 3 · Webcam

Abra a aba após a API estar escutando. Execute este primeiro bloco para informar a credencial.

```powershell
Set-Location -LiteralPath '.\marco-4\servidor'
$towerPython = '.\.venv\Scripts\python.exe'
$webcamSecret = Read-Host -Prompt 'Cole a credencial da webcam' -AsSecureString
```

Quando aparecer **Cole a credencial da webcam**, cole o código de `webcam-01` no prompt e pressione **Enter**. O PowerShell oculta os caracteres digitados. Depois execute:

```powershell
$env:TOWERAI_PRESENCE_TOKEN = [System.Net.NetworkCredential]::new('', $webcamSecret).Password
& $towerPython presenca.py --camera 0
```

Se houver várias câmeras, ajuste `--camera` até selecionar a webcam física. Nesse diretório, o produtor usa `https://127.0.0.1:8443` e `local/tls/ca.pem`. Ao encerrar com `Ctrl+C`, você pode limpar a variável da aba com `Remove-Item Env:TOWERAI_PRESENCE_TOKEN -ErrorAction SilentlyContinue`.

### Conferir a detecção

1. Aponte a câmera para uma parte bem iluminada do cômodo, sem pessoas no quadro. Após alguns segundos, procure `Presença 0 · aplicado` no terminal.
2. Sente-se no quadro com o tronco à vista. Vire o rosto e procure `Presença 1 · aplicado`. Se houver outra pessoa disponível, peça que se sente no enquadramento e confira a contagem. Saia e confira se a contagem volta a zero.
3. Nos primeiros segundos, `Presença desconhecida` pode aparecer enquanto a contagem estabiliza. Se persistir, confira o cabo, a iluminação e se outro programa está usando a webcam. A ordem dos índices pode mudar se você trocar os dispositivos conectados. A mensagem HTTP 401 indica que você deve conferir a credencial de `webcam-01`.

## Android · Conectar e testar a reunião

1. Transfira o arquivo `servidor/local/tls/ca.pem` para o Android, por USB ou outro canal de confiança. No TowerAI, toque em **Importar ca.pem**.
2. Informe a origem HTTPS do seu PC, por exemplo `https://192.168.1.20:8443`, e a credencial de `android-01`. Toque em **Conectar**. O app deve mostrar `sala-204` e a presença estimada.
3. Dê um título, ative o microfone e espere o app mostrar o nível recebido. Com a webcam apontada para a área vazia, fale perto do celular fora do quadro. A captura deve continuar **Aguardando contexto de reunião**.
4. Entre no quadro, espere aparecer **1 pessoa detectada** e fale de forma contínua por pelo menos três segundos. O app deve mostrar **Reunião em gravação**.
5. Fale uma decisão e uma tarefa com responsável e prazo. Por exemplo, o grupo decidiu publicar o manual na sexta e Ana vai revisá-lo até quinta.
6. Toque em **Parar microfone** e aguarde o processamento. Abra **Ver ata** ou **Histórico** para conferir a ata e a transcrição.

Para confirmar a persistência, encerre e suba novamente a API com o mesmo comando da aba 2. Abra o histórico e confira se a mesma reunião continua disponível.

### Se o celular mostrar “Notebook indisponível”

Confira o IP informado, se a aba 2 está rodando e se o Android está no Wi‑Fi da mesma rede, fora da rede de convidados. Ao abrir `/v1/status` na origem HTTPS do PC pelo navegador do celular, um aviso de certificado ou a mensagem de credencial ausente indica que a conexão chegou ao servidor.

Se o Firewall do Windows bloquear a entrada em uma rede privada de confiança, ajuste o nome da interface, o IP e a sub-rede do exemplo. Execute no PowerShell como administrador se ainda precisar liberar essa conexão.

```powershell
New-NetFirewallRule -Name 'TowerAI-Marco4-LAN' -DisplayName 'TowerAI Marco 4 LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -Profile Private -InterfaceAlias 'Ethernet' -LocalAddress '192.168.1.20' -RemoteAddress '192.168.1.0/24'
```

## Espelhar no PC e encerrar

Para mostrar a tela do Android no PC, ative a **Depuração USB**, conecte o celular por USB e confirme a autorização no aparelho. Instale o scrcpy uma vez.

```powershell
winget install --exact Genymobile.scrcpy
```

Abra um terminal novo para iniciar o espelhamento.

```powershell
scrcpy --no-audio
```

O Android continua enviando o áudio pela rede local enquanto a tela fica espelhada por USB. Ao terminar, pare o microfone e aguarde a ata. Encerre a webcam, a API e o modelo com `Ctrl+C` nas respectivas abas.
