# Broker local obrigatório — instalação e operação

O broker intermediário é um serviço **instalado no PC da sala**. O Raspberry Pi fornece a rede e o NTP; o PC concentra o HiveMQ e o consumidor. Pode-se migrar o broker para um Pi com recursos suficientes, mas a instalação validada aqui usa Docker Desktop no PC. **HiveMQ Community Edition** é o software; `broker.hivemq.com` era uma instância pública utilizada apenas no marco 2.

Versões fixadas: imagem oficial `hivemq/hivemq-ce:2025.5`, File RBAC **4.6.15**, Java 21 da imagem. Confirmamos essa combinação na execução local. O [projeto oficial](https://github.com/hivemq/hivemq-community-edition) documenta instalação por Docker e por pacote nativo. A [extensão oficial RBAC](https://github.com/hivemq/hivemq-file-rbac-extension/tree/4.6.15) fornece autenticação e permissões por tópico.

## Instalar e instanciar

Para a execução guiada do marco 3, o comando `python marco-3/executar.py preparar`, da raiz, faz a preparação e reutiliza os arquivos existentes. A demonstração em um único terminal começa com `python marco-3/executar.py demo`; veja o [guia de primeira execução](../README.md). Os comandos abaixo são o procedimento manual para administrar o broker separadamente.

Execute da raiz, com Python/venv e Docker ativos:

```powershell
python -m pip install -r marco-3/requirements.txt
python marco-3/broker/preparar.py
docker compose -f marco-3/broker/compose.yaml up -d
docker compose -f marco-3/broker/compose.yaml logs --tail 40
```

O script baixa o ZIP oficial e confere o SHA-256 publicado na mesma release. Em preparação sem internet, forneça o ZIP previamente conferido com `--rbac-zip caminho/do/pacote.zip`; a imagem Docker e as dependências também devem estar disponíveis localmente. O checksum protege contra arquivo corrompido; a origem de confiança continua sendo a distribuição oficial via HTTPS.

O Compose monta **apenas a extensão RBAC**, substituindo a pasta de extensões da imagem. Isso elimina a extensão permissiva `allow-all` que poderia anular a intenção das ACLs. O XML gerado só contém listener TLS 8883. Nem 1883 nem portas administrativas são publicadas. O endereço publicado inicialmente é `127.0.0.1`, adequado para consumidor/painel/injetor no mesmo PC e para o gateway do Wokwi.

Arquivos gerados em `local/` (todos fora do Git):

| Arquivo | Função e cuidado |
|---|---|
| `ca.pem` | certificado público da CA; distribuir por canal confiável aos clientes |
| `ca-key.pem`, `ca-key-password.txt` | material privado da CA; guardar em backup administrativo separado, não distribuir nem montar no broker |
| `server.p12` | certificado/chave do broker em PKCS#12 cifrado, lido pelo Java 21 da imagem |
| `config.xml` | listener TLS e senha do keystore; arquivo administrativo secreto |
| `extensions/hivemq-file-rbac-extension/conf/config.xml` | hash obrigatório e reload de credenciais a cada 10 s |
| `extensions/hivemq-file-rbac-extension/conf/credentials.xml` | usuários e hashes PBKDF2-SHA512 com salt aleatório |
| `<usuario>.env` | senha de uma conta, CA e endereço para o respectivo cliente |
| `segredos.h` | provisionamento do ESP32, a copiar e ajustar localmente |

Cada senha tem 32 bytes aleatórios antes da codificação URL-safe. O hash é compatível com a [implementação da extensão 4.6.15](https://github.com/hivemq/hivemq-file-rbac-extension/blob/4.6.15/src/main/java/com/hivemq/extensions/rbac/file/utils/Hashing.java): PBKDF2-HMAC-SHA512, salt aleatório, 210 mil iterações, chave de 512 bits. O formato é `saltBase64:iterações:hashBase64`. Não usamos os exemplos legados de dois campos.

## Papéis e permissões

Base fixa do laboratório: `ufg-ssu-2026/towerai/sala-204`. Alterar prefixo/sala exige atualizar também as ACLs geradas; flags de cliente sozinhas não concedem acesso.

| Conta | Publicar | Assinar |
|---|---|---|
| `mic-esp32-01` | próprios `dev/mic-esp32-01/telemetry` e `status` | próprio `ack` |
| `mic-injetor` (teste) | próprios `dev/mic-injetor/telemetry` e `status` | próprio `ack` |
| `consumidor-sala-204` | `dev/+/ack` e `insights/reuniao` | `dev/+/telemetry` e `dev/+/status` |
| `painel-sala-204` | nada | `insights/reuniao` |

O marcador `${{username}}` da File RBAC vincula o caminho à conta autenticada. O payload sozinho não prova identidade. O consumidor ainda confere `deviceId` contra o tópico e aceita **uma única fonte configurada**. Telemetria e ACK não podem ser retidos. Todo acesso não concedido é negado, inclusive usuários desconhecidos e assinaturas amplas. A conta de teste deve ser revogada depois da demonstração.

## Acesso pela LAN

Escolha um IP fixo/reserva DHCP para o PC, por exemplo `192.168.50.2`; o Pi fica em `192.168.50.1`. O exemplo é um planejamento da rede, não detecção automática do endereço desta máquina.

1. No **primeiro** provisionamento, use `python marco-3/broker/preparar.py --ip 192.168.50.2`. O certificado já inclui `localhost`, `towerai-broker.local`, `host.wokwi.internal`, `127.0.0.1` e `::1`; `--dns`/`--ip` adicionam SANs.
2. Faça `towerai-broker.local` resolver para o PC no DNS local, ou use o IP incluído no SAN. O script não instala DNS/mDNS.
3. No PowerShell, selecione a interface antes de criar o container:

```powershell
$env:MQTT_BIND_IP = "192.168.50.2"
docker compose -f marco-3/broker/compose.yaml up -d --force-recreate
```

No Bash: `MQTT_BIND_IP=192.168.50.2 docker compose -f marco-3/broker/compose.yaml up -d --force-recreate`. Se o bind for no IP LAN, os clientes no próprio PC também usam esse IP ou DNS com `--broker 192.168.50.2`. Os testes automáticos usam o bind padrão em loopback.

Libere TCP 8883 **somente na rede privada da sala** no firewall local, se necessário; não configure redirecionamento no roteador para a internet. O Compose não muda regras do firewall. Não habilite 1883 como solução para problemas de certificado. Se já provisionou apenas para localhost, emitir um novo certificado com o SAN LAN é uma operação administrativa: preservar a CA evita reinstalar confiança; recriar toda a CA exige reprovisionar todos os clientes.

## NTP local para o ESP32

O TLS verifica validade temporal do certificado. O firmware consulta `NTP_SERVIDOR` e aguarda horário plausível antes de conectar. Use o serviço NTP da sala previsto na Atividade 02; `pool.ntp.org` não é dependência desta etapa.

**Para a simulação neste PC**, há um caminho pronto em [WOKWI.md](../WOKWI.md): compile com `--wokwi` e execute `python marco-3/executar.py esp32 --wokwi`. Esse modo serve a hora do PC em loopback UDP **4123**, consultada pelo build simulado via `host.wokwi.internal`. A porta própria permite conviver com o serviço de hora do Windows na 123. A preparação da rede física com chrony, descrita a seguir, continua separada da simulação.

No Raspberry Pi com Debian/Raspberry Pi OS, prepare o serviço `chrony` e uma fonte de horário correta. As diretivas são descritas no [manual oficial do chrony](https://chrony-project.org/doc/4.6/chrony.conf.html). Exemplo de configuração em `/etc/chrony/chrony.conf`: `allow 192.168.50.0/24`; para servir a hora local mesmo quando isolado, `local stratum 10`. Após configurar, reinicie `chrony` e confira `chronyc tracking` e a data do Pi. Em rede isolada, um relógio errado continuará errado: ajuste a hora antes da demo ou use RTC/fonte confiável. UDP 123 deve ser alcançável pelos sensores. O script do broker não instala nem administra o relógio da rede.

## Revogar e rotacionar sem trocar a CA

```powershell
# Encerrar acesso do injetor descartável
python marco-3/broker/gerenciar_acesso.py mic-injetor --revogar
docker compose -f marco-3/broker/compose.yaml restart

# Gerar nova senha só para o ESP32 (também pode reativar uma conta revogada)
python marco-3/broker/gerenciar_acesso.py mic-esp32-01 --rotacionar
docker compose -f marco-3/broker/compose.yaml restart
```

A atualização é atômica. A File RBAC recarrega o arquivo para **novas conexões**; uma sessão já autenticada mantém as permissões antigas. O restart acima encerra todas as sessões do laboratório e torna a revogação imediata; isso causa uma breve indisponibilidade para todos. Em uma instalação maior, preferir desconexão administrativa dirigida por extensão própria. Essa semântica é documentada no [README da extensão](https://github.com/hivemq/hivemq-file-rbac-extension/tree/4.6.15).

Após rotação, redistribua só o perfil da conta afetada. No ESP32, copie novamente o header gerado, preserve os ajustes de rede e recompile/regrave. Credenciais no firmware e em um projeto Wokwi compartilhado podem ser extraídas; use credenciais descartáveis na demonstração e revogue-as depois. O arquivo histórico da extensão também contém hashes e deve receber o mesmo cuidado administrativo.

O certificado do broker vence em 365 dias; a CA em 10 anos. Planeje renovação antes do vencimento, com CA preservada e novo certificado com os mesmos SANs. A renovação de certificado não foi automatizada nesta etapa. A senha da CA está inicialmente ao lado da chave para facilitar o provisionamento: mova ambas para guarda administrativa protegida e separada do PC operacional antes de uso permanente.

## Permissões do volume no Linux

`preparar.py` restringe os arquivos administrativos ao usuário que o executa. O container precisa ler `config.xml` e `server.p12` e ler/escrever a pasta da extensão. Consulte `docker run --rm --entrypoint id hivemq/hivemq-ce:2025.5` para conhecer o usuário da imagem. Conceda acesso somente a esse usuário/grupo/ACL do host nos caminhos montados, ou execute o serviço com `user: "UID:GID"` correspondente ao usuário que provisionou e garanta escrita nos volumes `dados`/`logs`. Não use `chmod -R 777`. A instalação nativa pode usar os mesmos arquivos com caminhos ajustados no `config.xml`, a extensão em `extensions/` e sem `allow-all`.

## Diagnóstico e parada

```powershell
docker compose -f marco-3/broker/compose.yaml ps
docker compose -f marco-3/broker/compose.yaml logs --tail 80
python marco-3/ferramentas/verificar_broker.py
docker compose -f marco-3/broker/compose.yaml stop
```

`stop` preserva o container, os certificados e os volumes. `down` remove o container/rede e preserva os volumes por padrão. Não use `down -v` para uma simples parada: ele elimina os dados retidos. Falha de CA/hostname/data deve ser resolvida no provisionamento, endereço ou relógio. `Not authorized`/assinatura negada deve ser resolvida na conta/ACL. Erro no socket Docker requer o Docker Desktop ativo; ele não é erro do cliente MQTT.
