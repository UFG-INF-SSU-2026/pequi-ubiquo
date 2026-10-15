# Marco 3 — contexto, decisões e comunicação segura no TowerAI

**Tempo estimado de apresentação: 9 minutos e 30 segundos**, com 30 segundos de margem. Os tempos orientam a exposição; as referências permitem localizar e justificar cada implementação, sem precisar ler os arquivos durante a apresentação.

## 1. Introdução: qual problema esta etapa resolve? · 0:00–0:45

O TowerAI propõe apoiar a colaboração em ambientes compartilhados, com processamento local e atenção à privacidade. O marco 3 desenvolve um recorte dessa proposta: observar a atividade sonora de uma sala, decidir o estado de uma ata e disponibilizar um resumo confiável dessa observação.

A evolução sobre o marco 2 combina duas entregas: proteger a comunicação MQTT e transformar eventos do sensor em informação útil. Isso exige saber quem enviou o dado, o que essa identidade pode fazer e quando uma observação deixou de ser confiável.

**Referências:** [contexto geral do projeto](../README.md); [proposta de segurança e insights](proposta-seguranca-e-insights.md); implementação do fluxo em [consumidor/consumidor.py](consumidor/consumidor.py), função `main()`.

## 2. Arquitetura e tecnologias · 0:45–1:30

O caminho principal é **produtor ESP32 → broker local → consumidor de contexto → broker → painel**. O consumidor também devolve confirmações ao produtor. MQTT permite essa comunicação por tópicos, sem conectar diretamente cada componente aos demais.

O produtor usa C++/Arduino, PubSubClient e WiFiClientSecure. Consumidor e painel usam Python e Paho MQTT. O broker é HiveMQ Community Edition 2025.5, com a extensão File RBAC 4.6.15 para controlar acessos. Docker organiza o serviço local; TLS protege os enlaces.

A separação tem um propósito: o ESP32 observa, o consumidor decide e o painel apresenta. Alterar a visualização não exige transferir para ela as regras de decisão.

**Referências:** [produtor/sketch.ino](produtor/sketch.ino); [broker/compose.yaml](broker/compose.yaml); [consumidor/consumidor.py](consumidor/consumidor.py); [painel/painel.py](painel/painel.py).

## 3. Produtor: da leitura ao evento · 1:30–2:30

Nesta etapa, um potenciômetro simula o nível do microfone no ESP32/Wokwi. Os valores em dBFS são simulados: o protótipo classifica atividade por limiar, sem reconhecer palavras ou identificar pessoas.

O firmware combina média móvel de oito amostras com estabilidade de 300 milissegundos antes de confirmar uma mudança. Isso reduz oscilações próximas ao limiar. Distingue fala, silêncio, falha e estado desconhecido; publica mudanças de estado, níveis periódicos e sinais de vida, os *heartbeats*.

Como a PubSubClient publica em QoS 0, mudanças de estado recebem confirmação da aplicação: ficam numa fila e são reenviadas até uma resposta reconhecida do consumidor. O mesmo `eventId` acompanha as tentativas, permitindo detectar duplicatas. Essa proteção é limitada: a fila tem 16 posições, reside em memória e descarta o evento mais antigo quando cheia.

**Referências:** [produtor/sketch.ino](produtor/sketch.ino), funções `loop()`, `trocarEstado()`, `enfileirarEstado()`, `processarFila()` e `aoReceberAck()`; [contrato dos eventos e confirmações](contrato.md).

## 4. Consumidor: transformar observações em decisão · 2:30–3:45

O consumidor valida o evento antes de aplicá-lo: versão, campos, identidade, sala, tipo e faixa de valores. Depois trata duplicação e ordem. O identificador de inicialização, `bootId`, separa as sequências anteriores e posteriores ao reinício do ESP32.

A classe `Sala` concentra a decisão. Fala abre a ata; silêncio inicia `ENCERRANDO`; dez segundos de silêncio contínuo a fecham. Se a fala retorna antes, a ata continua aberta. Essa espera evita fragmentar uma reunião a cada pausa.

A regra central é **ausência de dado não significa silêncio**. Falha, indisponibilidade ou expiração da fonte suspendem uma ata ativa. Sem mensagens por mais de 15 segundos, o dado expira; após 60 segundos em suspensão, a ata fecha como incompleta.

As regras ficam separadas do transporte MQTT para serem testadas sem rede. Os intervalos usam o relógio monotônico do consumidor, evitando comparar tempos desde a inicialização de dispositivos diferentes.

**Referências:** [consumidor/sala.py](consumidor/sala.py), métodos `receber_evento()`, `receber_status()`, `tick()` e `_avaliar_ata()`; [adaptação ao MQTT](consumidor/consumidor.py), callback `ao_receber()`; [decisões herdadas](decisoes.md).

## 5. Broker e camada de segurança · 3:45–5:45

O **HiveMQ instalado localmente é requisito da arquitetura**. Ele distribui mensagens e aplica permissões; o consumidor decide sobre a ata. A sala deixa de depender do broker público do marco 2. A configuração publica somente a porta TLS 8883, restrita ao próprio computador por padrão.

A proteção combina quatro camadas:

1. **TLS:** cifra o tráfego e verifica o certificado do broker, incluindo a autoridade certificadora e o nome do servidor. Os clientes Python exigem pelo menos TLS 1.2; o ESP32 recebe a CA confiável. O relógio correto permite validar a vigência dos certificados.
2. **Autenticação:** cada dispositivo ou serviço possui usuário e senha próprios. As senhas são aleatórias; o cadastro do broker armazena hashes PBKDF2-HMAC-SHA512 com salt individual e 210 mil iterações.
3. **Autorização:** permissões mínimas por papel e tópico limitam as operações de cada conta. A ausência de permissão implica negação.
4. **Identidade na aplicação:** o consumidor compara o `deviceId` declarado com a identidade do tópico autorizado. Uma divergência é rejeitada sem ACK, evitando responder em nome de outro dispositivo.

| Papel | Permissão essencial |
|---|---|
| Dispositivo | Publicar sua telemetria/status e receber seu ACK. |
| Consumidor | Ler eventos/status e publicar confirmações e resumos. |
| Painel | Ler somente o resumo da sala. |

Assim, um sensor não pode forjar ACKs nem escrever na área de outro. As extensões substituem a configuração permissiva da imagem do broker. Segredos e firmware ficam fora do Git, mas exigem proteção no computador.

**O broker é uma entidade de confiança e acessa as mensagens**. Credenciais comprometidas ainda permitem dados falsos dentro das permissões concedidas. Há rotação e revogação individual; para invalidar o acesso existente, também é necessário encerrar as sessões estabelecidas.

**Referências:** [broker/compose.yaml](broker/compose.yaml); [broker/preparar.py](broker/preparar.py), funções `preparar()`, `hash_senha()` e `credenciais()`; [mqtt_seguro.py](mqtt_seguro.py), funções `configurar()` e `dispositivo_do_topico()`; [produtor/sketch.ino](produtor/sketch.ino), `setup()` e `conectar()`; [broker/gerenciar_acesso.py](broker/gerenciar_acesso.py); [exclusões do Git](.gitignore).

## 6. Contrato: compatibilidade e confiabilidade · 5:45–6:45

O contrato define tópicos, campos, versões e respostas. Os tópicos passaram a incluir `dev/<deviceId>`, permitindo vincular a conta autenticada ao espaço em que ela publica. Essa revisão é a versão 1.1 dos tópicos; o formato do evento continua com `schemaVersion: 1`.

`eventId` identifica o evento, `bootId` identifica a inicialização e `sequence` permite verificar a ordem. A confirmação informa o tratamento recebido: aplicado, duplicado, atrasado ou rejeitado. Receber um ACK, portanto, não significa necessariamente que o estado foi aplicado.

Telemetria admite perda; mudanças de estado recebem fila e repetição. O consumidor deduplica eventos para não repetir seus efeitos. Isso melhora a confiabilidade sem prometer entrega ilimitada ou processamento exatamente uma vez entre reinícios. Telemetria não é retida, evitando que uma observação antiga seja apresentada como nova.

**Referências:** [contrato.md](contrato.md); [produtor/sketch.ino](produtor/sketch.ino), `montarEvento()` e `enviarTelemetria()`; [consumidor/sala.py](consumidor/sala.py), `receber_evento()` e `_ack()`; [consumidor/consumidor.py](consumidor/consumidor.py), `ao_receber()`.

## 7. Insights e segundo consumidor: o painel · 6:45–8:15

O primeiro consumidor publica um resumo derivado em `insights/reuniao`. Ele reúne estado da ata, duração, tempos de fala, silêncio e lacuna, pausas, níveis de áudio e atividade nos últimos 60 segundos. A publicação ocorre nas transições e a cada 30 segundos durante uma sessão ativa.

Cada intervalo é atribuído ao estado anterior, evitando contabilizar uma falha como fala. A taxa de fala considera somente o tempo observado, excluindo lacunas. A qualidade é `COMPLETA` sem lacunas, `PARCIAL` com lacunas de até 10% da duração e `INCOMPLETA` acima disso ou quando a suspensão encerra a ata. Essas regras tornam explícita a confiança disponível no resultado.

O painel é um segundo consumidor, com acesso somente ao resumo. A mensagem retida permite que ele receba o último resultado ao conectar. Para não manter uma reunião aparentemente ativa indefinidamente, resumos ativos expiram após 90 segundos desde sua geração. O Last Will do consumidor também sinaliza indisponibilidade quando a conexão cai abruptamente.

O insight representa atividade sonora classificada, sem comprovar fala humana ou participação individual. Da mesma forma, `Ata ABERTA` representa uma decisão de contexto: a integração que inicia gravação ou transcrição no Flask continua como evolução.

**Referências:** [consumidor/resumo.py](consumidor/resumo.py), classe `Resumo`, métodos `avancar()` e `_montar()`; [consumidor/consumidor.py](consumidor/consumidor.py), `publicar_resumos()` e configuração do Last Will; [painel/painel.py](painel/painel.py), `interpretar()` e `main()`.

## 8. Evidências, limites e próximos passos · 8:15–9:30

As duas propostas principais foram materializadas: comunicação protegida com identidades e permissões, e um resumo derivado consumido por outro componente. A documentação de proposta explica a intenção; os arquivos executáveis mostram o comportamento entregue.

O registro de validação documenta 34 testes de lógica do consumidor/painel, quatro testes ativos do broker e uma integração real com produtor Python autenticado. Foram verificadas recusas de CA, hostname, credenciais e operações não autorizadas, além de deduplicação, resumo retido e Last Will. O firmware também rodou no Wokwi, pelo gateway privado da extensão do VS Code, contra o broker local: conectou com TLS e conta própria, passou pelo roteiro de fala, reenvio, queda com Last Will, fila, falha do sensor e resumo final, e deixou de reconectar quando sua conta foi revogada (T10). Falta a execução no ESP32 físico.

Permanecem limites claros: estado em memória, um microfone por sala e alerta administrativo após 20 mensagens inválidas em 60 segundos, sem quarentena automática. Os próximos passos incluem validar o sensor real e a rede da sala, persistir sessões e integrar a decisão ao Flask. mTLS e assinatura ponta a ponta são possibilidades adicionais, ainda não implementadas.

**Mensagem de fechamento:** o marco 3 associa cada observação a uma identidade autorizada, protege seu transporte e transforma eventos em uma decisão que também informa quando faltam dados confiáveis.

**Referências:** [evidências e alcance da validação](evidencias/validacao.md); [testes da sala](consumidor/test_sala.py), [segurança da aplicação](consumidor/test_seguranca.py), [resumos](consumidor/test_resumo.py) e [painel](painel/test_painel.py); [verificações do broker](ferramentas/verificar_broker.py); [integração do fluxo](ferramentas/testar_fluxo.py); [plano de segurança](plano-seguranca.md).
