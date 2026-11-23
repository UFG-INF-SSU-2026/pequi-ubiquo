# TowerAI · Marco 4

O TowerAI usa o microfone de um Android e uma webcam para acompanhar o contexto de uma sala. A webcam estima quantas pessoas estão visíveis pelo corpo com o YOLOX-S. O notebook decide quando a reunião começa e termina, transcreve o áudio com Whisper Large v3 ou Large v3 Turbo e apresenta a ata no app. A rede local conecta os produtores ao servidor. A inferência e o SQLite ficam no notebook.

## Entrega

| Item | Local |
| --- | --- |
| App Android | [Código em Kotlin e Compose](mobile/) · [APK de demonstração](apk/TowerAI-Marco4.apk) |
| API, decisões, modelos e produtor de webcam | [Servidor](servidor/) |
| Instalador e painel Windows | [Guia do TowerAI Servidor](docs/servidor-windows.md) |
| Instalador e painel Linux | [Guia do TowerAI Servidor para Ubuntu amd64](docs/servidor-linux.md) |
| Execução do código-fonte no Windows | [Roteiro de preparação, inicialização e teste da webcam](docs/rodar-no-pc.md) |
| Instalação, configuração, compilação e operação | [Guia de implantação](docs/implantacao.md) |
| Diagrama de arquitetura | [Mermaid](docs/arquitetura.md) · [Draw.io](docs/arquitetura.drawio) |
| Contrato, estados, tempos e formatos | [Contrato da API v1](docs/contrato.md) |
| Comandos, casos e resultados | [Verificação e evidências](docs/verificacao.md) |
| Artigo técnico no template SBC | [PDF](docs/artigo.pdf) · [Fonte LaTeX](docs/templates%20latex/template_artigo/towerai.tex) |
| Apresentação final no template Beamer UFG | [PDF](docs/apresentacao.pdf) · [Fonte LaTeX](docs/templates%20latex/template_slide/towerai-marco-4.tex) |
| Vídeo, compilação e entrega | [Roteiro, contribuições, feedback e registro de IA](https://www.youtube.com/watch?v=kCKiNdXUMbs) |
| Orientação original | [Spec do Marco 4](Orientacoes_Marco_04_Projeto_Final_Software_para_Sistemas_Ubiquos.pdf) |

## Fluxo

1. Suba o serviço de texto local, a API HTTPS e o produtor da webcam.
2. No app, importe a CA, informe a origem HTTPS do notebook e use a credencial de microfone.
3. Dê um título e ative o microfone. A sala com fala sustentada entra em gravação. Uma estimativa válida de sala vazia bloqueia a abertura. Uma estimativa indisponível aparece como presença desconhecida.
4. A reunião fecha após o período de silêncio ou pelo botão de parada. Consulte transcrição, decisões e ações pelo histórico.
5. No **TowerAI Servidor**, abra **Histórico**, selecione uma reunião encerrada e clique em **Exportar PDF…**. O PDF reúne a ata, a transcrição e os dados da reunião. Para abrir a gravação, procure `audio-da-reuniao.wav` no painel de anexos do leitor de PDF.

O aplicativo é nativo, e o APK de demonstração é assinado para instalação direta. O ambiente pode funcionar sem internet após baixar as dependências e os pesos dos modelos de visão, fala e texto.
