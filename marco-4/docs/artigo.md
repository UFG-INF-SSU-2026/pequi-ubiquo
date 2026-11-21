# TowerAI

## Registro de reuniões com contexto e processamento local

Thiago Nascente · Samuel Machado · Moisés Protázio · Vinícius Benevides

Universidade Federal de Goiás · Instituto de Informática · Engenharia de Software

INF0483 · Software para Sistemas Ubíquos · Pequi Ubíquo · Novembro de 2026

### Resumo

Registrar reuniões manualmente divide a atenção dos participantes e dificulta recuperar decisões e compromissos. O TowerAI integra um Android que capta áudio e um produtor visual que estima presença com uma webcam. Uma API no notebook recebe os eventos em HTTPS, combina os sinais e mantém o estado da sala. Quando a reunião fecha, o Faster Whisper transcreve o áudio em português e um modelo de linguagem local produz uma ata estruturada. O histórico é persistido em SQLite e pode ser consultado pelo aplicativo.

A implantação mantém a inferência e os dados derivados no notebook. O protótipo valida a identidade do serviço usando uma autoridade certificadora local e aplica permissões por sala e identidade de dispositivo. Os testes verificam mudança de contexto, entrega repetida, falha e recuperação, recusa de acesso e persistência. Um ensaio com fala sintética e presença injetada verifica os modelos reais, o transporte e a recuperação após reinício. Os resultados delimitam o que já é reproduzível e o que ainda requer avaliação com pessoas e sensores em uma sala.

**Palavras-chave** · sistemas ubíquos, computação de borda, reconhecimento de fala, contexto, atas de reunião.

### Abstract

TowerAI combines microphone audio from an Android device with occupancy estimates produced by a webcam. A laptop receives authenticated events over HTTPS, applies a context-dependent meeting policy, and stores the transcript and structured minutes in SQLite. Both speech recognition and text generation run locally. Automated tests exercise context transitions, authorization, temporary connectivity loss and persistence. A reproducible walkthrough uses synthetic Portuguese speech to evaluate the local inference pipeline and database recovery.

---

## 1. Introdução

Reuniões de estudo e de planejamento produzem decisões que frequentemente se perdem em anotações dispersas. Capturar toda conversa, em contrapartida, cria volume de dados e aumenta a exposição de informações pessoais. O problema deste trabalho é registrar com baixo atrito os pontos relevantes de uma reunião de sala e oferecer um histórico acessível no aparelho que capturou o áudio.

O cenário alvo é um grupo reunido no mesmo ambiente e com acesso a uma rede Wi-Fi local. Um telefone e um notebook são suficientes para um ensaio. O microfone identifica atividade sonora e fornece a matéria-prima para transcrição. Uma câmera fornece uma estimativa de presença que ajuda a distinguir uma reunião de áudio ambiente tocando em uma sala vazia. O notebook combina os sinais, grava o estado e executa os modelos. Após o fechamento, o aplicativo oferece a transcrição e a ata gerada.

### 1.1 Questão e hipótese de trabalho

A questão prática é se dois sinais simples de contexto, combinados com confirmação de entrega, permitem produzir um registro útil e recuperável com processamento no próprio ambiente. A hipótese é que a estimativa visual válida reduz alguns falsos inícios e que explicitar validade, silêncio e indisponibilidade torna a decisão observável para os participantes. O trabalho avalia a realização do fluxo, a robustez de sua implementação e os limites desse desenho. A avaliação de precisão em diferentes salas exigiria uma coleta maior.

### 1.2 Evolução do projeto

A Atividade 02 descreveu uma arquitetura multimodal e a distinção entre sala vazia e presença desconhecida. O Marco 3 validou telemetria, autenticação, TLS, permissões, deduplicação e estados de ata com um ESP32 simulado. O Marco 4 leva esses princípios a um aplicativo Android com áudio real. A unidade de evidência passa a abranger a captura, o estado contextual, o processamento local e a consulta do resultado.

### 1.3 Contribuição

A entrega reúne aplicativo nativo, dois produtores independentes, API versionada, regra de contexto, armazenamento local, geração de ata e verificação reproduzível. Os contratos registram explicitamente as decisões de projeto que se afastam das janelas de pesquisa dos marcos anteriores.

---

## 2. Fundamentação e trabalhos relacionados

### 2.1 Sistemas cientes de contexto

Dey define contexto como informação que caracteriza a situação de uma entidade relevante para a interação [1]. No TowerAI, a entidade é a sala. A fala observada, a validade da presença e a disponibilidade do microfone são sinais distintos. A resposta do sistema é ajustar seu estado e produzir informação. Um evento sem fonte identificada, atrasado ou ausente tem significado diferente de um evento válido que informa silêncio ou sala vazia.

### 2.2 Processamento de fala e texto

Whisper é um modelo de reconhecimento de fala treinado em grande escala com dados multilíngues [2]. A variante Large v3 Turbo e a Large v3 são os modelos habilitados na aplicação. Faster Whisper executa os pesos convertidos em CTranslate2 [3]. A transcrição é feita em português e gera segmentos textuais com offsets. Reconhecimento de fala e escrita da ata são etapas separadas. O segundo modelo interpreta a transcrição, recebe um esquema de saída e produz resumo, decisões e ações.

O agrupamento de decisões e tarefas facilita a consulta aplicada. Uma ata gerada pode reproduzir erros de transcrição ou omitir informações mesmo quando respeita o esquema JSON. Por isso a aplicação conserva a transcrição e a disponibiliza junto ao resultado. A validação da estrutura detecta campos ausentes ou tipos incorretos. A correção semântica requer comparação humana.

### 2.3 Estimativa de presença

O método de Viola e Jones oferece detecção rápida a partir de características calculadas sobre regiões da imagem [4]. O produtor visual utiliza o classificador frontal distribuído pelo OpenCV. A contagem de detecções é uma aproximação de ocupação dependente de enquadramento e iluminação. A câmera fornece contexto adicional com custo computacional baixo para um notebook.

### 2.4 Borda e persistência

A inferência local combina capacidade computacional e controle sobre o destino dos dados. O aplicativo envia áudio pela LAN para o notebook, e o modelo de texto recebe a transcrição em loopback. SQLite oferece transações, acesso concorrente para leitura e escrita via WAL e uma operação simples para uma sala [5]. TLS permite que o cliente valide o serviço antes de trocar dados [6].

---

## 3. Proposta e arquitetura

A arquitetura tem duas fronteiras de captura e uma fronteira de processamento. O Android é um produtor autenticado de áudio e também a interface de acompanhamento. A webcam opera em um processo produtor independente, com outra credencial. O notebook recebe os dois fluxos e concentra a avaliação. O modelo de texto é executado como um serviço local acessível apenas pela interface de loopback.

### 3.1 Responsabilidades dos produtores

O app Android abre a captura mediante ação do participante e permissão do sistema. Ele capta áudio mono, organiza blocos PCM de um segundo, numera cada bloco, marca seu horário e envia com confirmação. Ele exibe o estado calculado e consulta a ata. A webcam produz uma estimativa de presença por observação. O processo usa a imagem em memória e transmite uma contagem inteira ou o valor desconhecido.

### 3.2 Responsabilidades do notebook

A API valida versão, formatos, credenciais, sala, posse e horários. Cada bloco aceito gera uma confirmação, persistida junto ao efeito da operação. A regra separa estados de captura e de processamento de ata. Um trabalhador recupera os blocos da reunião fechada, monta um WAV temporário, transcreve e grava os segmentos antes de solicitar a ata. Assim é possível repetir a geração do texto depois de uma falha.

### 3.3 Distribuição

O processamento visual simples permanece ao lado da câmera. Concentrar a transcrição no notebook permite usar CUDA e reduz a responsabilidade computacional do telefone. O servidor centraliza o estado de uma sala e impede que dois dispositivos abram capturas concorrentes na mesma entidade. O armazenamento local permite recuperar a reunião após encerrar ou reiniciar o componente responsável.

### 3.4 Representações do desenho

O repositório inclui a arquitetura em Mermaid e em Draw.io. Nas duas representações, os fluxos Android–API e produtor visual–API têm a mesma autoridade de confiança e credenciais independentes. API, estado, fila, SQLite e modelos são componentes lógicos do notebook. As duas formas do diagrama permitem revisão por código e edição visual.

---

## 4. Regra de contexto e contratos

Uma captura inicia em AGUARDANDO após consentimento e ativação do microfone. O estado permaneceu explícito porque habilitar um sensor e reconhecer uma reunião são ações diferentes. A API mantém uma captura ativa por sala. Cada amostra de áudio recebe um nível RMS em dBFS. Três blocos consecutivos com nível de pelo menos −42 dBFS habilitam a abertura quando a presença válida é positiva ou está desconhecida. A estimativa válida de zero impede a abertura.

### 4.1 Silêncio e expiração

Depois da abertura, um bloco silencioso inicia ENCERRANDO. Um bloco acima do limiar retoma ABERTA. Dez blocos silenciosos fecham a reunião. Se o servidor fica seis segundos sem receber áudio fresco, a captura entra em SUSPENSA e fica marcada como incompleta. Ela pode retomar com um bloco fresco. Após sessenta segundos adicionais sem fonte, fecha como incompleta. A parada manual também fecha a sessão.

O produtor visual publica aproximadamente a cada dois segundos e estabiliza a contagem em observações sucessivas. A presença expira em quinze segundos. Amostra fora de ordem ou vencida não reescreve a estimativa atual. Esse comportamento possibilita abrir com a marca de presença não verificada quando a webcam está indisponível. A câmera influencia a abertura e deixa de vetar a decisão quando o dado vence.

### 4.2 Validade e atraso do áudio

O aplicativo inclui horário Unix e sequência por captura. O servidor recusa horários mais de cinco segundos no futuro. Blocos atrasados em mais de seis segundos podem recuperar o histórico de uma reunião já aberta, preservando sua ordem. Esses blocos não retomam o estado ao vivo. Durante a espera, um bloco atrasado é descartado após confirmar sua sequência.

### 4.3 Confirmação e idempotência

Os endereços de escrita combinam captura e número de bloco. O hash da carga permite confirmar repetição com os mesmos bytes e detectar colisão semântica de um identificador reutilizado. Reenvio e processamento concorrente são resolvidos em transações do SQLite. A fila no telefone é limitada para conter uso de memória e impedir captação indefinida durante uma falha.

---

## 5. Implementação

### 5.1 Interface Android

A aplicação foi implementada em Kotlin e Jetpack Compose a partir do protótipo existente. A tela inicial recebe o endereço do notebook, a credencial do produtor e a CA. A tela de reunião apresenta contexto visual, estado do fluxo, nível de áudio, envio pendente e controles de microfone. O histórico informa situação do processamento e permite consultar transcrição e ata. O controle de ciclo de vida encerra a captação quando o aplicativo sai do primeiro plano.

O Android usa AudioRecord e leitura em blocos curtos. As amostras são empacotadas como PCM16 little-endian com uma taxa de 16 kHz e um canal. A coleta e o envio executam separadamente, e o envio preserva a ordem. A lógica de reconexão repete a requisição pendente com o mesmo identificador. Um limite evita acumular mais de dois minutos de áudio ainda não confirmado no aplicativo.

### 5.2 Processamento da reunião

O servidor Python calcula RMS durante a recepção, atualiza a regra e grava as transições em SQLite. Ao fechar uma reunião, a fila chama Faster Whisper em CPU ou GPU. O código restringe o modelo de fala a Large v3 e Large v3 Turbo. O modelo Turbo é o padrão devido à prioridade de tempo de resposta para a demonstração. O arquivo salvo em cache permite carregar o modelo localmente após a preparação.

O modelo local de texto recebe a transcrição e um esquema estruturado. A aplicação valida lista de decisões, tarefas, responsável e prazo. A configuração avaliada usa a família Qwen2.5 [7]. Ações começam com o campo de conclusão falso. A ata e os segmentos são gravados em transações separadas. Uma falha na segunda etapa mantém a primeira acessível. A abertura e o fechamento automáticos dependem do contexto e da entrega de amostras, independentemente do tempo de inferência.

### 5.3 Persistência

O banco registra identidade de captura, sala, título, horários, estado, qualidade, segmentos e ata. Blocos brutos aguardam a transcrição. A conclusão desta etapa remove logicamente os blocos e retém seus hashes de confirmação. O servidor usa WAL para permitir leitura durante gravações. Após reiniciar, trabalhos que ficaram em estado intermediário voltam à fila, e o histórico permanece consultável.

---

## 6. Proteção e autorização

### 6.1 Identidade do serviço

O servidor aceita TLS 1.2 ou superior. Uma autoridade certificadora local emite um certificado para os nomes e IPs da instalação. A CA é transferida separadamente ao aplicativo. O cliente valida a cadeia, a validade do certificado e o nome ou endereço usado. Uma conexão a um servidor com CA desconhecida ou por IP que não está no SAN é recusada. O serviço de modelo de texto escuta em loopback e o app acessa exclusivamente a API HTTPS.

### 6.2 Identidade dos produtores

O notebook provisiona tokens aleatórios de alta entropia. O servidor guarda hashes desses tokens e vincula cada um a uma identidade, um papel e uma sala. O aplicativo envia a credencial no cabeçalho HTTPS. O papel de presença publica somente observações e consulta seu status. O papel de microfone envia áudio e consulta registros da sala. Edições de captura, parada e reprocessamento exigem a identidade que abriu a sessão.

### 6.3 Dados e operação

Tokens, chaves e banco de dados são locais à instalação. Rotação e revogação entram em vigor após reiniciar a API. O aplicativo carrega a credencial do microfone em memória, e a operação administrativa é feita no notebook. A chave privada usada para assinar a CA descartável é mantida em memória durante a emissão. O certificado público da CA pode ser conferido por impressão digital.

### 6.4 Limites do modelo de confiança

O notebook é uma parte confiável e recebe o áudio em claro depois de terminar a sessão TLS. Os administradores do ambiente podem acessar o banco. A remoção lógica de áudio bruto não equivale a eliminação forense. O detector visual não autentica pessoas e o token comprova a identidade provisionada, sem atestar o hardware. A aplicação é destinada à LAN e requer seleção consciente da rede e da CA.

O cenário de uso envolve dados pessoais de voz e a presença de pessoas no enquadramento. O grupo deve informar os participantes antes de ativar os sensores e usar conteúdo fictício na demonstração. O processo de visão comunica uma estimativa e evita persistir quadros.

---

## 7. Metodologia de avaliação

A avaliação combinou testes automatizados na API, testes do Android e um ensaio do pipeline local com um WAV sintético em português. Em cada caso, foram registrados a entrada, a expectativa, o resultado observado e os limites da evidência. A substituição controlada de relógio e inferência nos testes da API permite testar tempos e falhas sem depender da duração real da reunião.

### 7.1 Contexto e falhas

O primeiro caso injeta contagem zero e atividade sonora pela API. O resultado esperado é AGUARDANDO. Um novo evento com presença positiva, seguido de fala, deve levar a ABERTA. Blocos silenciosos encerram a reunião. Outro caso avança o relógio sem enviar áudio, observa SUSPENSA e verifica a recuperação com um novo bloco. Um bloco tardio é incorporado quando faz parte da reunião, sem retroagir o estado ao vivo.

### 7.2 Controle de acesso e persistência

Os testes iniciam HTTPS com uma CA temporária. Verificam o caminho autorizado e a rejeição por chave incorreta, papel insuficiente, captura de outra sala, CA desconhecida e nome fora do SAN. Após o fechamento, recuperam a ata e o texto por API em uma nova instância que usa o mesmo SQLite. Também verificam que uma falha de geração conserva o texto original e permite reprocessar somente a ata.

### 7.3 Modelos reais

O ensaio reproduz uma fala sintética em português com uma decisão e duas tarefas. Dois clientes autenticados exercitam a API da mesma forma operacional que os produtores. A presença de teste é injetada. O áudio passa por uma variante Whisper Large em CUDA e o texto passa por Qwen via llama.cpp. O processo da API é encerrado, iniciado novamente e consultado com a mesma CA e banco. A duração observada inclui entrada, inferência, reinício e leitura.

### 7.4 Android

O build gera APK assinado para instalação direta. Os testes JVM verificam validação da origem e da credencial. O teste instrumentado abre a tela no Android e verifica a exigência de CA. O ensaio presencial complementa essas evidências com a câmera e o microfone físicos na rede do grupo.

---

## 8. Resultados e discussão

Os dez testes Python do Marco 4 passaram. A mudança da presença de zero para positiva permitiu a abertura pela mesma sequência de áudio. A ausência temporária de entrega levou a SUSPENSA e retornou à gravação após um bloco fresco. Um reenvio idêntico foi confirmado como duplicado. A persistência preservou a transcrição e a ata após recriar o servidor com o mesmo banco. As tentativas não autorizadas e as conexões com identidade de serviço inválida foram recusadas.

O APK foi compilado e a assinatura verificou com APK Signature Scheme v2. Os dois testes JVM e os dois testes instrumentados passaram no Android 15 emulado. A aplicação exigiu a CA, conectou por TLS, enviou blocos do microfone emulado e parou a captura. O histórico exibiu a ata e a transcrição do ensaio local, copiadas para o banco de teste.

### 8.1 Ensaio com inferência real

Os ensaios com 17 segundos de áudio sintético transcreveram quatro segmentos. A ata trouxe uma decisão sobre publicar o manual na sexta-feira e duas ações com os responsáveis e prazos esperados. Ana revisaria o manual até quinta-feira e Bruno testaria o aplicativo até quarta-feira. Transcrição e ata permaneceram iguais após o reinício. No computador Windows com RTX 5060 Ti, Whisper Large v3 Turbo em CUDA e Qwen2.5-7B em CPU concluíram o fluxo em 29,86 segundos. Com Large v3 e Qwen2.5-7B, foram 47,88 segundos e as mesmas atribuições. Turbo com Qwen2.5-1.5B levou 16,32 segundos, mas o resumo descreveu como concluída a publicação futura. A configuração principal usa Qwen2.5-7B.

### 8.2 Interpretação

Os resultados confirmam a viabilidade do fluxo local com os dados simulados e os modelos reais. A comparação dos primeiros resultados de geração indicou que a semântica das categorias de ata precisa ser descrita ao modelo com exemplos. Erros de transcrição podem alterar um prazo, e a validação sintática sozinha não garante fidelidade. A transcrição e a ata devem ser lidas em conjunto.

A avaliação de contexto confirma o mecanismo de decisão, mas os níveis de ruído, a distância do telefone, a posição da câmera e a qualidade do Wi-Fi podem mudar o desempenho percebido. Uma amostra sintética mede a execução do pipeline. Ela não estima a taxa de erro em reuniões espontâneas, sotaques variados, fala sobreposta e salas com eco.

### 8.3 Limites

O APK mantém a fila pendente em memória, portanto encerrar o processo antes do ACK pode perder blocos. A câmera é sensível a rostos de perfil, baixa luz e oclusões. A decisão acústica usa energia como aproximação de fala. O sistema impõe limite de dez minutos de áudio persistido por reunião. O consumo de bateria e a latência sob carga não foram medidos de forma representativa. O ensaio final do grupo deve confirmar conexão real, sensores, tempo de execução e qualidade de ata no notebook de apresentação.

---

## 9. Conclusão

O Marco 4 entrega um caminho verificável entre uma captura de áudio no Android e o registro de reunião consultável no aparelho. O processamento no notebook centraliza as decisões e permite executar modelos locais. A webcam acrescenta um sinal de contexto identificável e sujeito à validade. Persistência, confirmação e permissões ajudam a recuperar o resultado e limitar o que cada produtor pode fazer.

As evidências sustentam a integração dos componentes que foram exercitados. Para avaliações futuras, cabem amostras com ruído, interrupções e fala sobreposta, medição de latência de cada estágio e avaliação de energia no Android. O exame humano da transcrição e das tarefas extraídas continua relevante para a utilidade do sistema.

### Referências

[1] DEY, A. K. Understanding and Using Context. Personal and Ubiquitous Computing, v. 5, p. 4–7, 2001. https://doi.org/10.1007/s007790170019

[2] RADFORD, A. et al. Robust Speech Recognition via Large-Scale Weak Supervision. Proceedings of ICML, 2023. https://proceedings.mlr.press/v202/radford23a.html

[3] SYSTRAN. Faster Whisper, repositório e requisitos de execução. https://github.com/SYSTRAN/faster-whisper

[4] VIOLA, P. e JONES, M. Rapid Object Detection using a Boosted Cascade of Simple Features. CVPR, 2001. https://doi.org/10.1109/CVPR.2001.990517

[5] SQLITE. Write-Ahead Logging. https://sqlite.org/wal.html

[6] RESCORLA, E. The Transport Layer Security (TLS) Protocol Version 1.3. RFC 8446, 2018. https://www.rfc-editor.org/rfc/rfc8446

[7] QWEN TEAM. Qwen2.5 Technical Report. arXiv, 2024. https://arxiv.org/abs/2412.15115

[8] ANDROID DEVELOPERS. AudioRecord. https://developer.android.com/reference/android/media/AudioRecord

### Transparência

O registro de entrega acompanha o roteiro, as contribuições individuais e o uso de IA na preparação. A ata do ensaio usa falas e nomes fictícios. A fonte do artigo, o contrato e os comandos de verificação estão disponibilizados no repositório.
