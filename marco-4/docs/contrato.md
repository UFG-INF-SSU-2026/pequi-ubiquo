# Contrato v1

Cada chamada usa HTTPS e o cabeçalho `Authorization: Bearer <credencial>`. A API obtém dispositivo, papel e sala pelo cadastro do servidor. Os tempos UTC de captura são segundos Unix e podem ter fração. O identificador de captura e o de presença têm 32 caracteres hexadecimais minúsculos.

## Operações

| Método e caminho | Papel | Dados e resposta |
| --- | --- | --- |
| `GET /v1/status` | ambos | Identidade, sala, papel e presença observada. O estado da captura ativa é visível ao microfone. |
| `POST /v1/presence` | presença | JSON com `eventId`, `observedAt`, `people` inteiro de 0 a 50 ou `null`. Confirma `aplicado`, `duplicado` ou `atrasado`. |
| `PUT /v1/captures/{id}` | microfone | JSON com `title` de 1 a 100 caracteres. Abre uma captura armada por sala. Mesmo ID, dono e título retornam a captura existente. |
| `PUT /v1/captures/{id}/chunks/{seq}` | microfone dono | 32.000 bytes exatos de PCM16 LE, 16.000 Hz, mono, sem cabeçalho WAV. `Content-Type: application/octet-stream` e `X-Captured-At` com a hora do fim do bloco. `seq` começa em 0. Retorna estado, sequência esperada, nível e ACK. |
| `PUT /v1/captures/{id}/finish` | microfone dono | Encerra de forma idempotente. O app chama depois de esvaziar a fila. O último bloco parcial é completado com zeros. |
| `GET /v1/meetings` | microfone | Últimas 50 reuniões iniciadas da sala, em ordem decrescente. |
| `GET /v1/captures/{id}` | microfone da sala | Dados da reunião, transcrição com offsets em segundos e ata estruturada. |
| `PUT /v1/captures/{id}/retry` | microfone dono | Recoloca um processamento com falha na fila. Transcrição que já foi persistida é reutilizada. |

Os ACKs de áudio são `aplicado`, `duplicado`, `atrasado`, `arquivado` e `encerrado`. Um retry após perda de resposta usa o mesmo ID, sequência, horário e corpo. A transação grava o efeito e o hash da amostra antes de confirmar. Reuso de sequência com bytes diferentes ou salto de sequência recebe HTTP 409. `encerrado` informa que o bloco novo não foi incorporado. JSON malformado, tamanho ou formato inválidos e relógios adiantados em mais de 5 s também são recusados. HTTP 401 indica credencial ausente ou inválida, 403 falta de papel ou posse, e 404 inclui recurso fora da sala.

O Android utiliza o cabeçalho `Date` da resposta HTTPS de abertura e o tempo monotônico do dispositivo para datar os blocos. A diferença entre os relógios do celular e do servidor não entra no cálculo. Blocos à espera de reenvio preservam sua hora de captação.

## Decisão e tempo

| Condição | Efeito |
| --- | --- |
| Microfone armado | `AGUARDANDO`. A câmera atualiza presença separadamente. |
| 3 blocos consecutivos com RMS ≥ −42 dBFS | Abre a reunião se a presença for positiva ou desconhecida. Os três blocos iniciais compõem o começo do áudio gravado. |
| Presença válida igual a zero | Impede que a captura armada abra reunião a partir do som. |
| Presença com mais de 15 s, câmera indisponível ou sem observação | Presença desconhecida. Se abrir, registra `presenceVerified: false`. Amostras atrasadas ou fora de ordem não alteram a decisão atual. |
| Bloco silencioso após abrir | `ENCERRANDO`. Fala retorna a `ABERTA`. Dez blocos silenciosos seguidos fecham a reunião. |
| 6 s sem áudio fresco após abrir | `SUSPENSA` com qualidade `INCOMPLETA`. Um bloco fresco retoma a avaliação. Após mais 60 s sem áudio fresco, fecha a reunião como incompleta. |
| Áudio com mais de 6 s | Se já havia reunião, incorpora ao áudio histórico em ordem, sem retomar a decisão atual. Se estava aguardando, confirma a sequência e descarta a amostra. |
| Parada manual, 600 blocos persistidos ou 120 s sem áudio fresco com captura armada | Fecha a captura. Captura sem reunião iniciada é descartada. |

O limiar de RMS mede energia, que é uma aproximação de fala. O áudio é limitado a dez minutos persistidos por reunião. A conexão pode manter até 120 blocos pendentes em memória no Android. Se o app sair do primeiro plano, cessa a captação e tenta entregar a fila. O estado `ABERTA` segue a continuidade dos eventos, e a presença funciona como contexto de abertura.

Os estados de processamento são `AGUARDANDO`, `PENDENTE`, `TRANSCREVENDO`, `GERANDO_ATA`, `PRONTA`, `FALHOU` e `DESCARTADA`. A inicialização recoloca trabalhos interrompidos na fila. Na versão 4.1.3, os blocos PCM das reuniões iniciadas ficam no SQLite após a transcrição. O WAV de trabalho é temporário. A exportação no painel local envolve o mesmo áudio em um WAV PCM16 sem compressão e o anexa ao PDF. A API de consulta do Android continua a fornecer metadados, ata e transcrição. Intervalos sem blocos recebidos são marcados por qualidade incompleta. Os offsets correspondem ao áudio recebido e concatenado.

## Formato da ata

```json
{
  "summary": "A equipe definiu a publicação do manual.",
  "decisions": ["Publicar o manual na sexta-feira."],
  "action_items": [
    {"task": "Revisar o manual.", "owner": "Ana", "due": "quinta-feira", "done": false}
  ]
}
```

`owner` e `due` são strings vazias quando o texto não fornece a informação. O aplicativo exibe a transcrição junto com a ata para revisão humana. Os registros ficam no SQLite por sala, com horário de início e encerramento, qualidade, status e duração. Os eventos de presença expiram para decisão em 15 s e são podados do banco após uma hora.
