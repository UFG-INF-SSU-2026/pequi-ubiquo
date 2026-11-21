# TowerAI Servidor no Linux

O pacote `.deb` é para Ubuntu 24.04 e 26.04 Desktop em PCs x86-64 (amd64). Ele inclui o painel do Marco 4, o servidor HTTPS, o llama.cpp para CPU e os pesos do detector YOLOX-S. O mesmo APK Android do Marco 4 usa o servidor Linux.

## Instalar

Na pasta que contém o arquivo `towerai-servidor_4.1.4-1_amd64.deb`, rode:

```bash
sudo apt install ./towerai-servidor_4.1.4-1_amd64.deb
```

Abra **TowerAI Servidor** no menu de aplicativos ou execute `towerai-servidor` em um terminal da sessão gráfica, com seu usuário normal. Para começar a baixar os pesos ao abrir o painel, execute `towerai-servidor --preparar-modelos`.

## Configurar e conectar o Android

1. Conecte o PC e o celular à mesma rede local. Em **Rede e microfone**, escolha a interface IPv4 que chega ao Android e clique em **Gerar certificado**.
2. Escolha a sala, cadastre um identificador como `android-01` e clique em **Gerar credencial**. Exporte `ca.pem` e transfira o arquivo e a credencial ao Android por um canal de confiança. No APK, importe a CA, informe o endereço HTTPS do painel e cole a credencial.
3. Em **Modelos**, clique em **Baixar modelos restantes** ou selecione os pesos já disponíveis no PC. O download inicial ocupa cerca de 2,8 GB e inclui Whisper Large v3 Turbo e Qwen2.5 1.5B Instruct. Com os modelos preparados, a inferência em CPU funciona sem internet.
4. Se for usar uma câmera, abra **Webcam**, selecione o dispositivo V4L2 e use **Testar webcam**. O usuário da sessão gráfica precisa ter acesso ao dispositivo `/dev/video*`. A opção **Sem webcam** deixa a presença como desconhecida.
5. Clique em **Iniciar servidor**. No Android, toque em **Conectar**, informe um título e ative o microfone. Dependendo da duração da reunião e da CPU, o processamento pode levar alguns minutos após encerrar a gravação.

Se o Linux bloquear a conexão, use **Configurar firewall** para ver a interface, o IP e a sub-rede usados pelo painel. Em um Ubuntu com UFW ativo, o exemplo abaixo permite a entrada apenas da sub-rede local. Substitua a interface, o IP e a sub-rede pelos valores do seu painel.

```bash
sudo ufw allow in on enp3s0 proto tcp from 192.168.1.0/24 to 192.168.1.20 port 8443
```

Se o IP mudar, gere o certificado para a interface selecionada e importe a nova CA no celular. O histórico, os certificados, os registros e os modelos baixados ficam em `${XDG_DATA_HOME:-$HOME/.local/share}/towerai-servidor`. O botão **Abrir pasta de dados** abre esse diretório. Atualizações e remoções pelo `apt` preservam os dados do usuário.

## Exportar o histórico da reunião em PDF

Abra **Histórico**, selecione uma reunião encerrada e clique em **Exportar PDF…**. O documento reúne dados e avisos da captura, a ata, as decisões, as ações e a transcrição com marcações de tempo. A exportação funciona com o servidor parado. Use **Atualizar lista** para obter o estado mais recente do processamento.

Desde a versão 4.1.3, o servidor preserva a gravação das reuniões no banco local. Quando disponível, ela acompanha o PDF no anexo `audio-da-reuniao.wav`. Abra o painel de anexos de um leitor de PDF que ofereça esse recurso. As reuniões que já tiveram o áudio descartado pelas versões anteriores continuam exportáveis com os dados disponíveis.

Para corrigir uma ata anterior, inicie o servidor e, em **Histórico**, selecione a reunião e clique em **Gerar ata novamente…**. O sistema usa a transcrição salva e substitui a ata após processar. Use **Atualizar lista** até aparecer **Ata pronta** e exporte o PDF de novo.

## Compilar

Em Ubuntu 24.04 amd64, instale as dependências de compilação e rode o script a partir da pasta `marco-4`:

```bash
sudo apt install python3-venv python3-tk libpython3.12 binutils curl ca-certificates libgomp1
bash instalador/compilar-linux.sh
```

O script usa um ambiente Python temporário, obtém o binário CPU x64 do [llama.cpp b12590](https://github.com/ggml-org/llama.cpp/releases/tag/b12590), verifica os hashes do llama.cpp e do YOLOX-S e grava o `.deb` em `instalador/saida/`.
