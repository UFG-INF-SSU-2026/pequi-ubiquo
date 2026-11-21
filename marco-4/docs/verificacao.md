# Verificação do Marco 4

## Testes automatizados

Rode a suíte Python após preparar o ambiente do [guia de implantação](implantacao.md). Ela sobe HTTPS em uma porta de loopback livre, com CA, credenciais e SQLite temporários. Os testes de decisão usam PCM conhecido, relógio controlado e adaptadores de modelo substituídos. O ensaio da seção seguinte usa os modelos reais.

```powershell
# Em marco-4/servidor, com as dependências instaladas
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v

# Em marco-4/mobile, com o SDK configurado
.\gradlew.bat assembleDebug testDebugUnitTest
.\gradlew.bat connectedDebugAndroidTest
```

| Requisito | Entrada e ação | Resultado esperado e observado |
| --- | --- | --- |
| Dois produtores e contexto | Enviar áudio acima do limiar com presença 0, depois publicar presença 2 e repetir o áudio. | Espera com a sala vazia. Abre com presença válida e áudio sustentado. Dez blocos silenciosos fecham. |
| Validade | Enviar presença fora de ordem e avançar além da validade. | O evento fora de ordem recebe `atrasado`. Ao vencer a presença, o áudio pode abrir reunião com `presenceVerified: false`. |
| Persistência | Finalizar processamento, recriar o servidor com o mesmo banco e consultar o histórico. | Recupera o mesmo ID, a transcrição, a ata e seus responsáveis. |
| Canal protegido | Conectar com a CA correta e IP do SAN, depois com CA desconhecida e com nome ausente do SAN. | Sucesso com identidade válida. Falha de verificação TLS nos outros casos. |
| Identidade e autorização | Usar chave inválida, câmera acessando histórico, outro microfone editando a captura e dispositivo de outra sala. | HTTP 401, 403 e 404 conforme a fronteira de acesso. A câmera também recebe status sem dados de captura. |
| Falha e recuperação | Avançar o relógio com envio interrompido, reenviar bloco confirmado e enviar bloco atrasado e depois fresco. | Suspende, confirma o duplicado, arquiva o atrasado sem alterar contexto e retoma com áudio fresco. |
| Falha na IA de texto | Fazer o gerador falhar após concluir a transcrição e repetir o processamento. | Mantém os segmentos e reprocessa somente a ata. |
| Restrições de modelo | Validar esquema de ata, lista de modelos de fala e destino do modelo de texto. | Somente as duas variantes Large configuradas. Campos inválidos e destino externo recusados. |
| Android | Build, assinatura e instalação em Android 15 emulado. Conectar ao servidor de teste, captar, parar e consultar uma ata existente. | APK instalável. A tela exige CA. O app recebeu ACK para os blocos, parou a captura e exibiu ata e transcrição no histórico. |

O [log do servidor](evidencias/testes-servidor.txt) registra **10 testes aprovados**. No Android 4.0.1, passaram **3 testes JVM e 2 instrumentados**. Consulte o [registro da execução Android](evidencias/android.txt) para a versão, a assinatura e o resultado.

O ensaio de regressão da versão 4.0.1 adiantou o relógio de uma API isolada em 120 segundos. O APK anterior enviou 15 blocos sem que a API registrasse o nível sonoro de um bloco fresco. Com o APK atualizado, a mesma verificação recebeu a medida de dBFS e confirmou a parada da captura. Um teste JVM verifica que o cálculo usa o horário do servidor e mantém a idade real de blocos pendentes.

O teste instrumentado da tela de conexão pode rodar sem servidor. Para incluir a integração de áudio, suba uma instância de teste, provisionada com uma sala vazia, certifique o IP do emulador e use uma credencial descartável de microfone. No PowerShell, em `marco-4/mobile`:

```powershell
$towerCaPath = (Resolve-Path '../servidor/local/tls/ca.pem').Path
$towerCaB64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($towerCaPath))
$towerTestToken = Read-Host 'Credencial descartável' -MaskInput
.\gradlew.bat connectedDebugAndroidTest '-Pandroid.testInstrumentationRunnerArguments.towerOrigin=https://10.0.2.2:8443' "-Pandroid.testInstrumentationRunnerArguments.towerToken=$towerTestToken" "-Pandroid.testInstrumentationRunnerArguments.towerCa=$towerCaB64"
Remove-Variable towerTestToken
```

A instrumentação importa a CA temporária na tela, conecta e verifica que a captura recebe a confirmação do primeiro bloco PCM. Ela também verifica a parada e a navegação ao histórico. O seletor de arquivos e a pergunta de permissão do sistema são exercitados no ensaio manual do grupo.

Para verificar a exibição de uma ata existente, use a mesma sala de uma reunião finalizada e passe também `-Pandroid.testInstrumentationRunnerArguments.towerMeetingTitle=<título>`. Na execução registrada, o banco de teste recebeu uma cópia da transcrição e da ata do ensaio padrão para essa verificação.

## Ensaio com modelos locais

No Windows, com uma voz pt-BR instalada, gere a fala fictícia em `marco-4/servidor`. É possível fornecer outro WAV PCM16 a `ensaio.py`.

```powershell
$towerWav = Join-Path (Get-Location) 'local/ensaio.wav'
New-Item -ItemType Directory -Force 'local' | Out-Null
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'tests/voz-de-teste.ps1' -Saida $towerWav
.\.venv\Scripts\python.exe ensaio.py $towerWav --model large-v3-turbo --device cuda --output local/ensaio.json
```

O ensaio sobe a API real e cria dois clientes TLS com credenciais distintas. Injeta uma observação de presença, envia o WAV em blocos e aguarda o processamento. Em seguida reinicia o processo Python e compara transcrição e ata. A entrada diz que o manual será publicado sexta-feira, Ana o revisará até quinta-feira e Bruno testará o aplicativo até quarta-feira. Os nomes e as falas foram criados para o teste.

No [ensaio Turbo com Qwen 1.5B](evidencias/ensaio-turbo.json), o servidor processou **17 segundos** de áudio sintético. A ata retornou **uma decisão e as duas ações** esperadas, com os responsáveis e os prazos. O fluxo completo, incluindo subida, inferência, reinício e consulta, levou **16,32 segundos** no computador de teste com RTX 5060 Ti. O resumo descreveu a publicação como concluída, embora fosse uma decisão futura. Isso motivou selecionar Qwen 7B na configuração principal. O OpenCV também foi exercitado para a ausência de câmera e respondeu com presença desconhecida.

O [ensaio da configuração padrão](evidencias/ensaio-padrao.json) usou Whisper Large v3 Turbo em CUDA e Qwen2.5-7B em CPU. Preservou a decisão como ação futura, com as atribuições corretas, em **29,86 segundos**. O [ensaio com Large v3](evidencias/ensaio-large-v3.json) usou o mesmo Qwen e áudio. Recuperou os mesmos responsáveis, prazos e decisão em **47,88 segundos**. Os tempos descrevem os fluxos completos no equipamento testado.

## Ensaio no notebook e celular do grupo

No PC de demonstração, a ordem do DirectShow colocou a câmera virtual **Iriun Webcam** no índice 0 e a webcam USB **XWF-1080P** no índice 1. A webcam USB entregou quadros de 640 × 480 ao OpenCV. O produtor estabilizou a leitura em uma pessoa detectada após três observações. Esse teste exercitou a captura e a detecção locais.

| Passo | Resultado esperado | Registro |
| --- | --- | --- |
| Instalar APK, importar CA real, conectar pela mesma Wi-Fi e ativar o microfone. | App mostra sala e estado do servidor. | Registrar versão Android, IP ou nome e captura de tela, ocultando a credencial. |
| Com webcam estável, alternar enquadramento vazio e com pessoa, mantendo áudio suficiente. | A presença altera a abertura. | Registrar enquadramento de teste, sinais e transição. |
| Cortar Wi-Fi por mais de 6 s e religar antes do fim da suspensão. | O servidor suspende, o app trata a falha e retoma após reconectar. | Registrar tempos, estado, ACK e qualidade. |
| Encerrar, rever texto e ata, reiniciar API e consultar novamente. | Mesmos itens antes e depois do reinício. | Registrar comparação e tempo observado no notebook de apresentação. |

Esses passos usam a câmera, o microfone e a rede do grupo. As evidências sintéticas delimitam os resultados obtidos sem esses dispositivos.
