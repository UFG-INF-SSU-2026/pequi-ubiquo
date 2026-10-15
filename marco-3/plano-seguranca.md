# Plano da etapa de segurança — canal MQTT criptografado

## Objetivo e fronteira

Evoluir a fronteira validada no marco 2 sem alterar sensoriamento, fila, ACK de aplicação nem a regra que diferencia silêncio de falta de dado. O novo canal deve manter telemetria e informação derivada **na rede da sala**, cifrar as conexões, autenticar produtores/consumidores e limitar o que cada conta pode publicar ou ler.

Este plano acompanha a implementação em `marco-3/`. O [marco 2](../marco-2/README.md) permanece histórico. O processamento multimodal, STT, banco e aplicação Flask da raiz não foram reestruturados para esta entrega. A proposta de resumo/painel foi implementada como processos MQTT separados; gravação automática no Flask é evolução.

## Requisitos de aceite

| ID | Requisito |
|---|---|
| S1 | HiveMQ CE instalado no PC da sala; nenhuma dependência de broker público durante operação |
| S2 | Listener TLS 8883, TLS mínimo 1.2; nenhuma porta MQTT em texto claro |
| S3 | Clientes validam cadeia, validade e nome/IP do broker; proibir desativar verificação de certificados |
| S4 | Usuário/senha diferente por cliente; anônimos e senha incorreta recusados |
| S5 | ACL com negação por padrão: dispositivo só escreve na própria subárvore, consumidor responde ACK e publica resumo, painel apenas lê resumo |
| S6 | Identidade do payload coincide com tópico autorizado; divergências não geram ACK nem mudam estado |
| S7 | Segredos, chaves e firmware provisionado fora do Git; geração aleatória, rotação e revogação documentadas |
| S8 | ESP32 só tenta TLS após horário plausível obtido de NTP local; não recorrer a servidor público |
| S9 | Retry/deduplicação/expiração e suspensão do marco 2 continuam funcionando sobre o novo transporte |
| S10 | Resumo derivado tem qualidade e validade explícitas; painel não permanece indicando atividade após perder evidência |

## Ameaças e decisões

| Ameaça da proposta | Controle implementado | Limite do controle |
|---|---|---|
| Leitura de tráfego e captura de senha | TLS com CA da sala e validação de hostname | broker e endpoints ainda acessam o conteúdo em claro |
| Publicação externa de evento/status falso | autenticação e ACL por usuário | conta comprometida pode falsear seu próprio sensor |
| ACK falso apagando a fila | somente o consumidor publica `dev/+/ack` | comprometimento da conta do consumidor continua crítico |
| Dispositivo escrevendo como outro | ACL com `${{username}}` + identidade conferida antes de responder | a conta representa a identidade provisionada, não prova física de integridade do hardware |
| Dispositivo lendo a sala inteira | assinaturas mínimas; painel só recebe derivado | resumo ainda revela atividade da reunião a quem pode lê-lo |
| Payload redirecionando ACK | tópico de resposta derivado do canal, divergência descartada sem ACK | não elimina DoS por saturação de uma conta legítima |
| Dado retido antigo mantendo indicação ativa | carimbo com fuso, idade máxima 90 s, timer independente e aviso de indisponibilidade | relógio do PC/painel precisa estar correto |
| Credencial exposta em repositório/simulador | arquivos locais ignorados; conta de teste descartável | Git ignore não é criptografia de disco nem apaga exposição anterior |

TLS autentica o broker; o usuário/senha autentica o cliente e a ACL autoriza ações. MQTT não fornece automaticamente essas políticas. Não há assinatura de payload nem criptografia ponta a ponta entre sensor e consumidor: o broker é uma parte confiável. O NTP simples também depende da confiança na LAN e no relógio do gateway; proteção de horário contra atacante ativo exigiria mecanismos adicionais.

## Sequência de execução e entregáveis

| Etapa | Entrega concreta | Dependência | Situação |
|---|---|---|---|
| 1. Derivação | copiar produtor, consumidor, injetor e contrato do marco 2; manter etapa anterior preservada | conhecer fronteira e regra da ata | concluída |
| 2. Infraestrutura | Compose local; provisionamento de CA, certificado, hashes e quatro contas; sem `allow-all` | Docker e instalação inicial com internet | implementada e validada no PC |
| 3. Identidade | tópicos `dev/<deviceId>/...`; comparar payload antes de ACK/status; fonte única por sala | ACLs da etapa 2 | implementada, testes de regra e broker aprovados |
| 4. Clientes seguros | Python com contexto TLS obrigatório; ESP32 com `WiFiClientSecure`, credenciais e NTP local | CA e segredos provisionados | Python validado; firmware compilado para ESP32, execução física pendente |
| 5. Informação útil | `Resumo` puro, tópico retido QoS 1, Last Will do consumidor; painel somente leitura com expiração | etapas 2–4 para integração | implementada e validada com sensor Python |
| 6. Operação | guia de subida/parada/LAN, geração de firmware, revogação, rotação e alerta de 20 inválidos/60 s | etapas anteriores | documentada; acesso físico LAN/NTP requer preparação da sala |
| 7. Aceite físico | sensor real, NTP da sala, queda de Wi-Fi, retorno e oito passos da demonstração | ESP32 ou Wokwi com gateway privado | roteiro pronto; execução depende do ambiente físico/simulador |

A divisão da proposta pode ser mantida: Vinícius cuida de broker/firmware; Thiago de consumidor/resumo/painel; Samuel verifica ACK, retry e duplicação; Moisés verifica relógio, expiração e qualidade do dado. Todos devem explicar os controles e o fluxo inteiro na apresentação.

## Matriz de comprovação

| ID da proposta | Ensaio | Prova reproduzível |
|---|---|---|
| T1–T3 | anônimo/senha incorreta recusados; sem porta 1883 | `ferramentas/verificar_broker.py` |
| adicional TLS | CA não confiável e nome errado falham; cadeia correta negocia TLS | `ferramentas/verificar_broker.py` |
| T4–T7, R7 | publicar em outra identidade, enviar ACK falso, assinar `#` e painel publicador são negados | `ferramentas/verificar_broker.py`; cada tentativa em sessão independente |
| T8 | identidade divergente e status forjado não alteram estado nem produzem ACK | `consumidor/test_seguranca.py` |
| T9 parcial | retry duplicado, abertura, silêncio, resumo final, painel e consumidor offline | `ferramentas/testar_fluxo.py`, broker real, sensor Python |
| T9 completo | roteiro de oito passos, LEDs/fila/rede do ESP32 | `produtor/testes/*.yaml` e roteiro do README; requer firmware provisionado e ambiente de rede |
| T10 | remover conta; encerrar sessão antiga; tentativa de conexão com perfil antigo recusada | `broker/gerenciar_acesso.py --revogar`, restart e tentativa do cliente |
| R1–R4 | fala/silêncio, lacuna, pausa e periodicidade | `consumidor/test_resumo.py` |
| R5–R6 | Last Will real após processo morto e leitura tardia do retained | `ferramentas/testar_fluxo.py` |
| R8 | painel acompanha demo física | roteiro no README, pendente de ambiente físico/simulador |
| adicional painel | retained velho/futuro/versão inválida e contagem do tempo restante | `painel/test_painel.py` |

Os [resultados de validação](evidencias/validacao.md) registram o que foi efetivamente executado. Teste lógico, compilação e teste com hardware são evidências diferentes. Uma publicação QoS 0 negada pode ser descartada ou causar desconexão sem erro detalhado para o produtor; o teste ativo usa MQTT 5 para observar PUBACK/SUBACK, enquanto o firmware continua MQTT 3.1.1.

## Operação e manutenção

O procedimento completo está em [broker/README.md](broker/README.md). Na primeira instalação, definir DNS/IP estável, incluir SANs, ajustar Wi-Fi e NTP, distribuir somente CA pública e o segredo individual de cada cliente. Restringir o bind/firewall à sala. Preparar imagem Docker, bibliotecas e firmware antes de ficar sem internet.

Após a demo, revogar o injetor. Diante de perda ou suspeita de exposição, revogar/rotacionar somente a conta afetada e encerrar a sessão antiga. **File RBAC não remove direitos de clientes já conectados ao recarregar o arquivo.** No laboratório, o restart encerra sessões e aplica a revogação; a indisponibilidade breve deve ser considerada no roteiro. Rotacionar credenciais no semestre ou em mudança de membros, conforme a proposta.

Certificado do broker: 365 dias; CA: 10 anos. Planejar renovação, backup protegido da CA e recuperação antes do vencimento. Nunca resolver falha de conexão com `setInsecure()`, `tls_insecure_set(True)` ou abertura de 1883.

## Evoluções posteriores

Persistir sessões/deduplicação em SQLite; integrar o resumo ao Flask com idempotência e autenticação própria; agregar vários sensores com estado por dispositivo; mTLS por cliente; assinatura de payload se houver intermediários não confiáveis; controle de taxa e quarentena automática; renovar certificado preservando a CA. Nenhuma dessas evoluções é apresentada como entregue neste marco.
