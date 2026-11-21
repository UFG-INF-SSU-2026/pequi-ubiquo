# Arquitetura

```mermaid
flowchart LR
    android["Android · microfone e histórico"]
    camera["Notebook · produtor de presença<br/>Webcam e detector de pessoas YOLOX-S"]
    api["Notebook · API HTTPS<br/>Autenticação, autorização e ACK"]
    decision["Regra da sala e fila de processamento"]
    db[("SQLite<br/>Reuniões, segmentos e atas")]
    whisper["Whisper Large v3 / Turbo<br/>CPU ou GPU local"]
    llm["Modelo de texto local<br/>API em loopback"]
    desktop["Notebook · painel Tkinter<br/>Histórico de reuniões e PDF com WAV"]

    android -->|"HTTPS · áudio e consulta"| api
    api -->|"HTTPS · estado, transcrição e ata"| android
    camera -->|"CLI · HTTPS"| api
    camera -->|"Painel · publicação local"| db
    api --> decision
    decision <-->|"transações"| db
    decision <-->|"WAV / segmentos"| whisper
    decision <-->|"texto / ata JSON"| llm
    db -->|"Consulta somente leitura"| desktop
```

O notebook executa a API, a política de decisão, o processamento e a persistência. O Android capta PCM, confirma a entrega e mostra o estado calculado. Em linha de comando, o produtor visual usa credencial própria para enviar a contagem e o horário via HTTPS. No painel, a webcam publica a observação localmente com uma identidade interna da sala. O modelo de texto aceita conexões de loopback. A aba **Histórico** lê o SQLite local e exporta os detalhes da reunião, com o áudio salvo em anexo quando disponível, mesmo com a API parada.

[Versão editável em Draw.io](arquitetura.drawio)
