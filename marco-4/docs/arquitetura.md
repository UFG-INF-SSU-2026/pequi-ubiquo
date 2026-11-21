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

    android -->|"HTTPS · áudio e consulta"| api
    api -->|"HTTPS · estado, transcrição e ata"| android
    camera -->|"HTTPS · contagem e validade"| api
    api --> decision
    decision <-->|"transações"| db
    decision <-->|"WAV / segmentos"| whisper
    decision <-->|"texto / ata JSON"| llm
```

O notebook executa a API, o processamento e a persistência. A autorização está na API e usa uma identidade por produtor. O Android capta PCM, confirma a entrega e mostra o estado calculado. O produtor visual usa um processo próprio e envia apenas a estimativa. O modelo de texto aceita conexões de loopback, pois a API é sua única cliente.

[Versão editável em Draw.io](arquitetura.drawio)
