# Implantação na rede local

Use um notebook com Windows 10 ou 11, Python 3.13 ou 3.14 de 64 bits, webcam e espaço local para os pesos. Para instalação do painel em Ubuntu Desktop, veja o [guia do servidor Linux](servidor-linux.md). O APK requer Android 8.0 (API 26) ou superior e microfone. Para compilar, instale JDK 17 ou superior e Android SDK Platform 36 com Build Tools 36.0.0. A compilação do Marco 4 foi feita no Windows com JDK 25, Gradle 9.2.1 e AGP 9.0.1.

## 1. Preparar o servidor

No PowerShell, a partir de `marco-4/servidor`, crie o ambiente Python e instale as dependências.

```powershell
python -m venv .venv
$towerPython = '.\.venv\Scripts\python.exe'
& $towerPython -m pip install -r requirements.txt
```

Para executar o servidor pelo código-fonte, prepare também o Whisper.

```powershell
& $towerPython main.py prepare-whisper --model large-v3-turbo
```

O último comando baixa os pesos para `servidor/modelos`. Para usar o modelo completo, prepare e selecione `--model large-v3`. O servidor busca os pesos nesse diretório, inclusive quando está offline. Execute o serviço a partir de `marco-4/servidor` para usar o ambiente, o cache de modelos e o armazenamento próprios do Marco 4.

Em Windows com GPU NVIDIA compatível, instale `requirements-gpu-windows.txt` na mesma `.venv` para incluir cuBLAS e NVRTC. O CTranslate2 pode exigir cuDNN compatível com o runtime instalado na máquina. Em seguida, use `--device cuda` para inferência FP16. Com CUDA indisponível, use `--device cpu`, que usa INT8 com o mesmo modelo. [Requisitos do Faster Whisper](https://github.com/SYSTRAN/faster-whisper#gpu)

## 2. Preparar o modelo de ata

### Baixar o servidor de texto para Windows

Na pasta `marco-4`, execute o bloco abaixo para baixar e extrair a versão Windows x64 CPU do llama.cpp. Ele prepara `.ferramentas/llama`, que também é o caminho padrão do instalador Windows. [Release b12590 do llama.cpp](https://github.com/ggml-org/llama.cpp/releases/tag/b12590)

```powershell
New-Item -ItemType Directory -Force -Path '.\.ferramentas' | Out-Null
$towerLlamaZip = '.\.ferramentas\llama-b12590-bin-win-cpu-x64.zip'
curl.exe --fail --location --output $towerLlamaZip 'https://github.com/ggml-org/llama.cpp/releases/download/b12590/llama-b12590-bin-win-cpu-x64.zip'
if ($LASTEXITCODE -ne 0 -or (Get-FileHash -LiteralPath $towerLlamaZip -Algorithm SHA256).Hash -ne 'E895687489CEED01770FD5101AC377AD77ED41B5B2B7DAFC8E1E1BFCA613BA62') {
    throw 'Falha no download ou na verificação do ZIP do llama.cpp.'
}
Expand-Archive -LiteralPath $towerLlamaZip -DestinationPath '.\.ferramentas\llama' -Force
```

### Baixar e iniciar o modelo de ata

Para executar o servidor pelo código-fonte, baixe as duas partes do Qwen2.5-7B-Instruct Q4_K_M e salve ambas em `marco-4/.ferramentas/` com os nomes originais. [Parte 1 do GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/blob/main/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf) · [Parte 2 do GGUF](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/blob/main/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf)

Em uma aba do PowerShell aberta em `marco-4`, passe a primeira parte em `--model` e deixe o processo rodando.

```powershell
& '.\.ferramentas\llama\llama-server.exe' --model '.\.ferramentas\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf' --alias qwen2.5-7b --host 127.0.0.1 --port 18082 --ctx-size 8192 --threads 8
```

Para um ensaio mais rápido, foi avaliado o [Qwen2.5-1.5B-Instruct Q4_K_M](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/main/qwen2.5-1.5b-instruct-q4_k_m.gguf). Nesse caso, use o GGUF correspondente, `--alias qwen2.5-1.5b` no servidor de texto e `--text-model qwen2.5-1.5b` no TowerAI. Mantenha o servidor de texto em loopback. A origem pode ser alterada com `--text-url`.

## 3. Gerar CA e provisionar dois dispositivos

Use `ipconfig` para identificar o IPv4 da interface que chega ao Android. Em outra aba do PowerShell aberta em `marco-4/servidor`, substitua `192.168.1.20` pelo IP real. Inclua `127.0.0.1` para a webcam local. O exemplo inclui `10.0.2.2` para o emulador do Android Studio no mesmo computador, que usa esse endereço no app.

```powershell
$towerPython = '.\.venv\Scripts\python.exe'
& $towerPython main.py cert 192.168.1.20 127.0.0.1 10.0.2.2
& $towerPython main.py provision android-01 microphone sala-204
& $towerPython main.py provision webcam-01 presence sala-204
& $towerPython main.py serve --bind 0.0.0.0 --port 8443 --device cpu --model large-v3-turbo
```

As credenciais de teste são geradas durante o provisionamento. A primeira vai para o Android. A segunda vai apenas para o processo da webcam. Compartilhe somente `local/tls/ca.pem` por um canal confiável, por exemplo USB. Para conferir o arquivo, compare o SHA-256 de seu certificado com o mostrado no app:

```powershell
& $towerPython -c "import ssl,hashlib,pathlib; print(hashlib.sha256(ssl.PEM_cert_to_DER_cert(pathlib.Path('local/tls/ca.pem').read_text())).hexdigest().upper())"
```

Use um Wi-Fi que permita comunicação entre os clientes. Se o Firewall do Windows bloquear a entrada, use uma regra de entrada TCP 8443 restrita à rede privada e aos clientes necessários, administrada no notebook. Verifique que celular e notebook estão na mesma rede e que o IP foi incluído no SAN do certificado. Se o IP mudar, use um novo diretório local para emitir certificados ou preserve o IP durante o ensaio.

Exemplo de regra para uma rede privada de demonstração, executado no notebook com autorização administrativa:

```powershell
New-NetFirewallRule -DisplayName 'TowerAI Marco 4' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -Profile Private -RemoteAddress LocalSubnet
```

Provisione o mesmo ID novamente para rotacionar seu token. Use `main.py revoke <id>` para revogar. Reinicie a API para aplicar o cadastro. A chave TLS, o SQLite, os hashes de tokens e os pesos ficam em diretórios ignorados pelo Git. Se for necessário manter os mesmos registros após mudar de certificado, pare a API e preserve o diretório SQLite com os arquivos de WAL associados.

## 4. Iniciar a webcam

### Baixar os pesos do detector

Em `marco-4/servidor`, baixe o modelo e confira o SHA-256 esperado pelo instalador. O produtor usa o OpenCV e processa os quadros localmente em CPU. [Modelo YOLOX-S do OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox)

```powershell
New-Item -ItemType Directory -Force -Path '.\modelos' | Out-Null
$towerPersonModel = '.\modelos\object_detection_yolox_2022nov.onnx'
curl.exe --fail --location --output $towerPersonModel 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx'
if ($LASTEXITCODE -ne 0 -or (Get-FileHash -LiteralPath $towerPersonModel -Algorithm SHA256).Hash -ne 'C5C2D13E59AE883E6AF3B45DAEA64AF4833A4951C92D116EC270D9DDBE998063') {
    throw 'Falha no download ou na verificação dos pesos do YOLOX-S.'
}
```

### Executar o produtor

Em uma terceira janela, entre em `marco-4/servidor`.

```powershell
$towerPython = '.\.venv\Scripts\python.exe'
$webcamSecret = Read-Host -Prompt 'Cole a credencial da webcam' -AsSecureString
```

Cole a credencial de `webcam-01` quando o PowerShell mostrar o prompt e pressione **Enter**. Depois execute:

```powershell
$env:TOWERAI_PRESENCE_TOKEN = [System.Net.NetworkCredential]::new('', $webcamSecret).Password
& $towerPython presenca.py --server https://127.0.0.1:8443 --ca local/tls/ca.pem --camera 0
```

O terminal mostra a contagem e o ACK. Se a câmera estiver ocupada, feche o aplicativo que a usa. Se houver múltiplas webcams, ajuste `--camera`. Use boa iluminação e enquadre a parte visível do corpo das pessoas sentadas, incluindo o tronco sempre que possível. Ao encerrar com `Ctrl+C`, você pode limpar a variável da aba com `Remove-Item Env:TOWERAI_PRESENCE_TOKEN -ErrorAction SilentlyContinue`.

## 5. Instalar e operar o app

1. Instale [TowerAI-Marco4.apk](../apk/TowerAI-Marco4.apk) com `adb install -r <caminho-do-apk>`, ou transfira o arquivo para o celular e autorize a instalação pelo gerenciador de arquivos. O APK é de demonstração, assinado com chave de depuração.
2. No app, selecione **Importar ca.pem**, confira o SHA-256 e informe, por exemplo, `https://192.168.1.20:8443` e a credencial de microfone.
3. Informe o título e toque em **Ativar microfone**. Conceda a permissão e mantenha o app à frente. O painel mostra a presença estimada, o nível e o estado. Para testar a regra, produza som com a câmera enquadrando uma cena vazia e depois use voz com uma pessoa enquadrada.
4. Aguarde dez segundos de silêncio ou toque em **Parar microfone**. A ata aparece no **Histórico** após o processamento. Reinicie a API com o mesmo diretório local, reconecte e recupere a ata.
5. Se ocorrer falha de processamento, confira a saída do servidor local e use **Tentar processar novamente**. O job usa a transcrição existente quando já foi concluída.

Para compilar a partir de `marco-4/mobile`, configure `JAVA_HOME` e `ANDROID_HOME` ou abra o projeto no Android Studio:

```powershell
.\gradlew.bat assembleDebug testDebugUnitTest
.\gradlew.bat connectedDebugAndroidTest
```

O APK compilado fica em `app/build/outputs/apk/debug/app-debug.apk`. O segundo comando requer um Android ou emulador conectado. Em macOS e Linux, use `./gradlew`.

## Dados e limites

O SQLite fica em `servidor/local/towerai.sqlite3`. Preserve essa pasta entre execuções. O servidor armazena horários, áudio, transcrição e ata. Desde a versão 4.1.3, os blocos PCM persistidos de uma reunião são preservados após a transcrição para exportação em WAV pelo painel. O limite de dez minutos corresponde a até 19,2 MB de PCM por reunião. Restrinja o acesso ao banco, ao WAL e aos backups. A webcam processa quadros em memória e publica a estimativa, sem identificar pessoas.

Execute uma única instância da API por diretório local. Um servidor novo usa o banco existente e recoloca processamentos interrompidos na fila.

O APK mantém credencial, CA e áudio pendente em memória. Encerrar o processo pode perder áudio ainda não confirmado. A regra usa nível acústico e estimativa visual de presença. A contagem pode cair quando móveis ou outras pessoas encobrem boa parte do corpo. Iluminação e enquadramento também afetam a estimativa. O app opera em primeiro plano e o uso contínuo do microfone e da rede afeta a bateria. A geração da ata varia com o hardware, o tamanho da reunião e o modelo. Revise a ata com a transcrição.
