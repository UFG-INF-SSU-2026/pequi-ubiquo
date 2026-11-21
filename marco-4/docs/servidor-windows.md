# TowerAI Servidor no Windows

Instale `TowerAI-Servidor-Setup.exe` no PC que receberá o áudio. O instalador é para Windows 10 ou 11 x64 e inclui o ambiente de execução, o servidor HTTPS, o llama.cpp e os pesos do detector YOLOX-S. A opção de baixar os pesos de transcrição e ata já vem marcada. Ao abrir o TowerAI pela última tela do instalador, os downloads dos modelos restantes começam em segundo plano. Você pode configurar a rede, o microfone e a webcam enquanto os pesos são baixados.

## Configurar pela primeira vez

1. Em **Rede e microfone**, selecione a interface usada pelo Android. Os dois dispositivos precisam estar na mesma rede local, com comunicação entre eles.
2. Clique em **Gerar certificado** e em **Autorizar porta 8443**. Confirme a solicitação do Windows. A regra é restrita ao executável do TowerAI, à interface escolhida e à sub-rede local em perfil privado. Para uma rede de confiança marcada como pública, altere o perfil em **Configurações → Rede e Internet → Propriedades** e atualize a lista.
3. Use a sala sugerida `sala-204` ou salve o nome da sua sala. Cadastre um identificador, como `android-01`, e clique em **Gerar credencial**. A credencial é exibida uma vez. Gerar outra para o mesmo identificador revoga a anterior.
4. Clique em **Exportar ca.pem**. Transfira esse arquivo e a credencial para o Android por um canal de confiança. No app, use **Importar ca.pem**, informe o endereço HTTPS copiado do painel e cole a **Credencial do microfone**. O Windows guarda a chave privada do servidor na pasta de dados.
5. Em **Modelos**, acompanhe o download ou clique em **Baixar modelos restantes** para iniciá-lo ou tentar novamente. O download completo soma aproximadamente 2,8 GB. Se você já tem os pesos, pode desmarcar a opção de download no instalador e indicar a pasta de um Whisper convertido para CTranslate2 e o primeiro arquivo GGUF de um modelo de instruções compatível. Mantenha todos os arquivos de um GGUF dividido na mesma pasta. O padrão é Whisper Large v3 Turbo com INT8 em CPU e Qwen2.5 1.5B Instruct Q4_K_M. Com os pesos preparados, a inferência funciona localmente, inclusive sem acesso à internet.
6. Na aba **Webcam**, selecione uma câmera pelo nome e clique em **Testar webcam**. O painel abre a prévia e mostra a contagem. Selecione **Sem webcam** para desativar esse sinal de contexto. A webcam conectada a este PC é gerenciada pelo painel.
7. Clique em **Iniciar servidor**. No Android, toque em **Conectar**, dê um título e ative o microfone. Mantenha a aplicação Windows aberta durante a reunião. Ela pode ficar minimizada.

## Acompanhar o servidor

O **Painel** atualiza a cada 750 ms e mostra, para a sala selecionada:

- **Pessoas detectadas** na webcam. O número estabiliza após três leituras consecutivas. `—` indica que a estimativa está indisponível e `0` indica uma leitura válida sem pessoas detectadas.
- **Microfone Android** aguardando conexão, conectado sem áudio, recebendo áudio com nível em dBFS ou sem comunicação recente. O Android aparece sem comunicação após oito segundos sem contato autenticado. A captura usa o limite de seis segundos sem blocos para suspender o áudio.
- **Reunião** aguardando contexto, gravando, em encerramento por silêncio ou suspensa.
- **Processamento da ata** na fila, em transcrição, em geração de ata, concluído ou com falha. O histórico e a ata ficam disponíveis no Android.

Os controles da rede mostram se a autorização TCP 8443 está ativa. Se o endereço do PC mudar, selecione a interface atual e gere o certificado para o novo endereço. Quando a CA mudar, exporte e importe o novo `ca.pem` no Android.

## Dados, migração e diagnóstico

O comando **Abrir pasta de dados** abre `%LOCALAPPDATA%\TowerAI Servidor`. Ali ficam o banco, os certificados, o cadastro, os modelos baixados e os registros `servidor.log` e `ata.log`. As atualizações e a desinstalação preservam essa pasta.

Para migrar da execução por linha de comando, pare o servidor anterior, abra o painel pela primeira vez e use **Importar instalação anterior**, apontando para `marco-4/servidor/local`. O painel copia o banco SQLite, os certificados e o cadastro. Escolha a mesma sala e gere ou reutilize o certificado para o endereço atual. Os pesos existentes são selecionados pela aba **Modelos**.

Se o Android não conectar, confira o endereço, o `ca.pem`, a credencial, a regra de firewall e a rede compartilhada. O instalador não altera o roteador. Se a webcam estiver ocupada, feche o outro programa que a usa, atualize a lista e teste novamente.

## Compilar

No Windows x64, tenha Python 3.13 ou 3.14 de 64 bits e o Inno Setup 6 instalados. [Inno Setup](https://jrsoftware.org/inno-setup)

Em um clone limpo, prepare o [binário Windows do llama.cpp](implantacao.md#baixar-o-servidor-de-texto-para-windows) e os [pesos do YOLOX-S](implantacao.md#baixar-os-pesos-do-detector). O instalador oferece o download do Whisper e do Qwen na primeira abertura do painel.

Na pasta `marco-4`, crie o ambiente Python, instale as dependências de compilação e gere o pacote. Se já tiver o ambiente `.venv`, reutilize-o a partir da segunda linha.

```powershell
python -m venv '.\servidor\.venv'
$towerPython = '.\servidor\.venv\Scripts\python.exe'
& $towerPython -m pip install -r '.\instalador\requirements-build.txt'
& '.\instalador\compilar.ps1'
```

O executável de instalação fica em `instalador/saida/TowerAI-Servidor-Setup.exe`. Use `-Llama` e `-Inno` para indicar caminhos diferentes dos padrões do script.
