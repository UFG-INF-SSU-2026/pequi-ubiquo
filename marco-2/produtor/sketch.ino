/* ===========================================================================
 * UFG - Instituto de Informatica - Software para Sistemas Ubiquos
 * Marco 2 - Integracao: PRODUTOR
 *
 * Grupo      : TowerAI / pequi-ubiquo
 * Componente : mic-esp32-01, microfone da sala-204
 * Origem     : prototipo de Vinicius na Atividade 03 (sensoriamento e
 *              qualidade). Leitura, media movel, debounce e deteccao de falha
 *              sao os mesmos. O que muda no Marco 2 e que o evento deixa de ir
 *              so para o monitor serial e atravessa a rede ate o consumidor.
 *
 * FLUXO
 *   potenciometro -> media movel + debounce -> estado do microfone
 *   -> evento JSON (contrato v1) -> MQTT -> consumidor (marco-2/consumidor)
 *   <- ACK do consumidor (confirmacao de negocio, nao de transporte)
 *
 * DOIS TIPOS DE ENVIO (ver ../contrato.md)
 *   audio.level e device.heartbeat - TELEMETRIA. Envia e esquece. Com a rede
 *       fora, nem e criada: um nivel velho nao orienta o presente.
 *   audio.state.changed - MUDANCA DE ESTADO. Entra numa fila e so sai quando
 *       o consumidor confirma. Sem ACK, reenvia o MESMO eventId com espera
 *       crescente (2 s, 4 s, 8 s, ate 10 s). O consumidor deduplica.
 *
 * ENTRADAS DE DEMONSTRACAO
 *   botao "sensor off" (GPIO 15) - sensor sem leitura -> FALHA_SENSOR
 *   botao "rede"       (GPIO 19) - derruba / restaura a conexao com o broker
 *   botao "reenviar"   (GPIO 18) - reenvia o ultimo estado ja confirmado com o
 *                                  mesmo eventId (resposta perdida + retry)
 *
 * LIMITACOES DECLARADAS
 *   - o potenciometro nao e microfone; o valor em dBFS e ficticio
 *   - eventTimeMs = millis() desde o boot, nao e hora real (sem NTP)
 *   - broker publico sem TLS nem autenticacao: qualquer um pode ler e publicar
 *     nestes topicos. Contraria o "sem internet" do projeto; serve so a demo
 *   - a PubSubClient so publica em QoS 0; a garantia vem do ACK de aplicacao
 *   - a fila fica na RAM: se o ESP32 reiniciar, estados pendentes se perdem
 * =========================================================================== */

#include <WiFi.h>
#include <PubSubClient.h>

// ---------------------------- configuracao ---------------------------------
const char*    WIFI_SSID      = "Wokwi-GUEST";
const char*    WIFI_SENHA     = "";
const char*    MQTT_BROKER    = "broker.hivemq.com";
const uint16_t MQTT_PORTA     = 1883;
const char*    PREFIXO        = "ufg-ssu-2026/towerai";
const char*    DEVICE_ID      = "mic-esp32-01";
const char*    ENTITY_ID      = "sala-204";
const int      SCHEMA_VERSION = 1;

// ----------------------------- pinos ---------------------------------------
const int PIN_POT          = 34;  // ADC1_CH6 - entrada substituta (nivel dBFS)
const int PIN_LED_ENVIANDO = 2;   // verde    - FALANDO
const int PIN_LED_MUDO     = 4;   // amarelo  - SILENCIO
const int PIN_LED_FALHA    = 5;   // vermelho - FALHA_SENSOR
const int PIN_BTN_SENSOR   = 15;  // botao    - desconecta o sensor
const int PIN_BTN_REENVIAR = 18;  // botao    - reenvia o ultimo estado confirmado
const int PIN_BTN_REDE     = 19;  // botao    - derruba / restaura a rede
const int PIN_LED_REDE     = 23;  // azul     - conectado ao broker

// --------------------------- parametros ------------------------------------
// Sensoriamento: iguais aos da Atividade 03.
const uint32_t PERIODO_AMOSTRA_MS = 50;     // 20 Hz
const uint8_t  N_MEDIA            = 8;      // media movel: 8 x 50 ms = 400 ms
const float    LIMIAR_SILENCIO    = -45.0;  // dBFS - veio da Atividade 02
const uint32_t DEBOUNCE_MS        = 300;
const float    FAIXA_MIN          = -90.0;
const float    FAIXA_MAX          =   0.0;
const uint8_t  AUSENTES_P_FALHA   = 6;      // 6 x 50 ms = 300 ms sem resposta
const int      LEITURA_AUSENTE    = -1;

// Comunicacao: novos no Marco 2.
const uint32_t PERIODO_NIVEL_MS      = 1000;   // audio.level enquanto FALANDO
const uint32_t PERIODO_HEARTBEAT_MS  = 5000;   // device.heartbeat sempre
const uint32_t ESPERA_ACK_INICIAL_MS = 2000;   // primeira espera por ACK
const uint32_t ESPERA_ACK_MAX_MS     = 10000;  // teto da espera crescente
const uint32_t RECONEXAO_MS          = 3000;   // intervalo entre tentativas
const uint8_t  TAM_FILA              = 16;     // estados pendentes na RAM
const uint32_t DEBOUNCE_BOTAO_MS     = 40;

// ---------------------------- estados --------------------------------------
enum Estado { DESCONHECIDO, SILENCIO, FALANDO, FALHA_SENSOR };

const char* nomeEstado(Estado e) {
  switch (e) {
    case SILENCIO:     return "SILENCIO";
    case FALANDO:      return "FALANDO";
    case FALHA_SENSOR: return "FALHA_SENSOR";
    default:           return "DESCONHECIDO";
  }
}

// ------------------------ estado do sensoriamento --------------------------
Estado   estado          = DESCONHECIDO;  // estado confirmado (com debounce)
Estado   candidato       = DESCONHECIDO;
uint32_t candidatoDesde  = 0;

int      buffer[N_MEDIA];
uint8_t  bufIdx          = 0;
uint8_t  bufN            = 0;
long     bufSoma         = 0;
float    mediaAtual      = FAIXA_MIN;     // ultima media, vai no heartbeat
uint16_t leiturasAusentes= 0;

// ------------------------- estado da comunicacao ---------------------------
uint32_t seq = 0;   // cresce a cada evento CRIADO; recomeca no boot (bootId)

// Mudanca de estado pendente de ACK. So a cabeca da fila fica em voo: assim o
// consumidor recebe os estados na ordem em que aconteceram.
struct EventoEstado {
  uint32_t    seq;
  uint32_t    tempoMs;
  float       valor;
  Estado      estado;
  const char* razao;
  uint16_t    tentativas;
};

EventoEstado fila[TAM_FILA];
uint8_t  filaInicio      = 0;
uint8_t  filaTamanho     = 0;
uint32_t descartadosFila = 0;
bool     cabecaEnviada   = false;
uint32_t enviadoEmMs     = 0;
uint32_t esperaAckMs     = ESPERA_ACK_INICIAL_MS;

EventoEstado ultimoConfirmado;
bool         temConfirmado = false;

WiFiClient   rede;
PubSubClient mqtt(rede);
bool     redeLigada     = true;   // o botao "rede" alterna
bool     avisouWifi     = false;
uint32_t tUltimaConexao = 0;

char bootId[9];
char clientId[48];
char topicoEventos[96];
char topicoStatus[96];
char topicoAck[96];

uint32_t tUltimaAmostra   = 0;
uint32_t tUltimoNivel     = 0;
uint32_t tUltimoHeartbeat = 0;
uint32_t tUltimoResumo    = 0;

// ------------------------------ botoes -------------------------------------
// apertou() e metodo, nao funcao solta: o Arduino gera prototipos das funcoes
// no topo do .ino, antes desta struct existir, e a compilacao quebraria.
struct Botao {
  int      pino;
  bool     ultimo;
  uint32_t tBorda;

  bool apertou() {  // true uma vez por pressao (INPUT_PULLUP: LOW)
    bool leitura = digitalRead(pino);
    uint32_t agora = millis();
    if (leitura != ultimo && agora - tBorda > DEBOUNCE_BOTAO_MS) {
      tBorda = agora;
      ultimo = leitura;
      return leitura == LOW;
    }
    return false;
  }
};
Botao btnRede     = { PIN_BTN_REDE,     true, 0 };
Botao btnReenviar = { PIN_BTN_REENVIAR, true, 0 };

// ------------------------------ sensor -------------------------------------
float bruto_para_dbfs(float bruto) {
  return FAIXA_MIN + (bruto / 4095.0f) * (FAIXA_MAX - FAIXA_MIN);
}

// Com o botao pressionado o sensor esta desconectado: nao ha leitura nenhuma,
// e nao um valor ruim. (Por que nao detectar falha pela constancia do valor:
// ver o sketch da Atividade 03 - sala quieta tambem e constante.)
int lerBruto() {
  if (digitalRead(PIN_BTN_SENSOR) == LOW) return LEITURA_AUSENTE;
  return analogRead(PIN_POT);
}

void acenderLeds() {
  digitalWrite(PIN_LED_ENVIANDO, estado == FALANDO      ? HIGH : LOW);
  digitalWrite(PIN_LED_MUDO,     estado == SILENCIO     ? HIGH : LOW);
  digitalWrite(PIN_LED_FALHA,    estado == FALHA_SENSOR ? HIGH : LOW);
}

// ----------------------------- contrato ------------------------------------
// eventId = deviceId:bootId:sequence. Nunca muda entre tentativas: e por ele
// que o consumidor reconhece um retry.
void montarEvento(char* buf, size_t tam, const char* tipo, uint32_t s,
                  uint32_t tempoMs, float valor, Estado est,
                  const char* razao, uint16_t tentativa) {
  snprintf(buf, tam,
    "{\"schemaVersion\":%d,\"eventId\":\"%s:%s:%lu\",\"eventType\":\"%s\","
    "\"deviceId\":\"%s\",\"entityId\":\"%s\",\"bootId\":\"%s\","
    "\"eventTimeMs\":%lu,\"sequence\":%lu,\"value\":%.1f,\"unit\":\"dBFS\","
    "\"state\":\"%s\",\"reason\":\"%s\",\"attempt\":%u}",
    SCHEMA_VERSION, DEVICE_ID, bootId, (unsigned long) s, tipo,
    DEVICE_ID, ENTITY_ID, bootId,
    (unsigned long) tempoMs, (unsigned long) s, valor,
    nomeEstado(est), razao, (unsigned) tentativa);
}

void montarStatus(char* buf, size_t tam, const char* status) {
  snprintf(buf, tam,
    "{\"schemaVersion\":%d,\"deviceId\":\"%s\",\"bootId\":\"%s\",\"status\":\"%s\"}",
    SCHEMA_VERSION, DEVICE_ID, bootId, status);
}

void publicar(const char* carga) {
  bool ok = mqtt.publish(topicoEventos, carga, false);
  Serial.print(ok ? "MQTT -> " : "MQTT FALHOU -> ");
  Serial.println(carga);
}

// ---------------------------- telemetria -----------------------------------
void enviarTelemetria(const char* tipo, float valor, const char* razao) {
  if (!mqtt.connected()) return;  // offline: nem cria, nao consome sequence
  // Com estado esperando ACK, a telemetria espera: se saisse antes, teria
  // sequence maior e o consumidor marcaria a mudanca de estado como atrasada.
  if (filaTamanho > 0) return;
  seq++;
  char buf[512];
  montarEvento(buf, sizeof buf, tipo, seq, millis(), valor, estado, razao, 1);
  publicar(buf);
}

// ------------------------ fila de mudanca de estado ------------------------
void enfileirarEstado(float valor, const char* razao) {
  if (filaTamanho == TAM_FILA) {
    // Fila cheia: descarta o estado mais antigo. O mais recente vale mais.
    Serial.print("FILA cheia: descartado estado seq=");
    Serial.println(fila[filaInicio].seq);
    filaInicio = (filaInicio + 1) % TAM_FILA;
    filaTamanho--;
    descartadosFila++;
    cabecaEnviada = false;
    esperaAckMs = ESPERA_ACK_INICIAL_MS;
  }
  seq++;
  fila[(filaInicio + filaTamanho) % TAM_FILA] =
      EventoEstado{ seq, millis(), valor, estado, razao, 0 };
  filaTamanho++;
  Serial.print("FILA + estado "); Serial.print(nomeEstado(estado));
  Serial.print(" seq=");          Serial.print(seq);
  Serial.print(" (pendentes: ");  Serial.print(filaTamanho); Serial.println(")");
}

void processarFila(uint32_t agora) {
  if (filaTamanho == 0 || !mqtt.connected()) return;
  if (cabecaEnviada && agora - enviadoEmMs < esperaAckMs) return;

  EventoEstado& ev = fila[filaInicio];
  if (cabecaEnviada) {
    // Estourou a espera sem ACK. Reenvia o mesmo evento e espera mais da
    // proxima vez, para nao martelar um consumidor que esta fora.
    Serial.print("RETRY sem ACK em "); Serial.print(esperaAckMs);
    Serial.print(" ms: seq=");         Serial.println(ev.seq);
    esperaAckMs = (esperaAckMs * 2 > ESPERA_ACK_MAX_MS) ? ESPERA_ACK_MAX_MS
                                                        : esperaAckMs * 2;
  }
  ev.tentativas++;
  char buf[512];
  montarEvento(buf, sizeof buf, "audio.state.changed", ev.seq, ev.tempoMs,
               ev.valor, ev.estado, ev.razao, ev.tentativas);
  publicar(buf);
  cabecaEnviada = true;
  enviadoEmMs = agora;
}

// Le "chave":"valor" de um JSON simples, sem biblioteca. Basta para o ACK,
// cujo formato e fixo (ver contrato). Aceita espaco ao redor dos dois-pontos.
bool extrairCampo(const char* json, const char* chave, char* saida, size_t tam) {
  char padrao[32];
  snprintf(padrao, sizeof padrao, "\"%s\"", chave);
  const char* p = strstr(json, padrao);
  if (!p) return false;
  p += strlen(padrao);
  while (*p == ' ') p++;
  if (*p++ != ':') return false;
  while (*p == ' ') p++;
  if (*p++ != '"') return false;
  const char* fim = strchr(p, '"');
  if (!fim || (size_t) (fim - p) >= tam) return false;
  memcpy(saida, p, fim - p);
  saida[fim - p] = '\0';
  return true;
}

void aoReceberAck(char* topico, byte* carga, unsigned int tam) {
  char json[256];
  if (tam >= sizeof json) return;
  memcpy(json, carga, tam);
  json[tam] = '\0';

  char eventId[64], resultado[16];
  if (!extrairCampo(json, "eventId", eventId, sizeof eventId) ||
      !extrairCampo(json, "result", resultado, sizeof resultado)) {
    Serial.print("ACK ilegivel: "); Serial.println(json);
    return;
  }
  Serial.print("ACK <- "); Serial.print(resultado);
  Serial.print(" ");       Serial.println(eventId);

  if (filaTamanho == 0 || !cabecaEnviada) return;
  char idCabeca[64];
  snprintf(idCabeca, sizeof idCabeca, "%s:%s:%lu",
           DEVICE_ID, bootId, (unsigned long) fila[filaInicio].seq);
  if (strcmp(idCabeca, eventId) != 0) return;  // ACK de reenvio manual ou antigo

  // Qualquer resposta encerra a entrega: aplicado, duplicado e atrasado dizem
  // que o consumidor ja tem o evento; rejeitado nao melhora com retry.
  ultimoConfirmado = fila[filaInicio];
  temConfirmado = true;
  filaInicio = (filaInicio + 1) % TAM_FILA;
  filaTamanho--;
  cabecaEnviada = false;
  esperaAckMs = ESPERA_ACK_INICIAL_MS;
  Serial.print("FILA - entregue seq="); Serial.print(ultimoConfirmado.seq);
  Serial.print(" (pendentes: ");       Serial.print(filaTamanho); Serial.println(")");
}

void reenviarUltimoConfirmado() {
  if (!temConfirmado) { Serial.println("REENVIAR: nenhum estado confirmado ainda"); return; }
  if (!mqtt.connected()) { Serial.println("REENVIAR: sem conexao com o broker"); return; }
  ultimoConfirmado.tentativas++;
  Serial.print("REENVIAR (simula resposta perdida): mesmo eventId, tentativa ");
  Serial.println(ultimoConfirmado.tentativas);
  char buf[512];
  montarEvento(buf, sizeof buf, "audio.state.changed", ultimoConfirmado.seq,
               ultimoConfirmado.tempoMs, ultimoConfirmado.valor,
               ultimoConfirmado.estado, ultimoConfirmado.razao,
               ultimoConfirmado.tentativas);
  publicar(buf);
}

// ------------------------------- rede --------------------------------------
void conectar(uint32_t agora) {
  if (!redeLigada || mqtt.connected()) return;
  if (tUltimaConexao != 0 && agora - tUltimaConexao < RECONEXAO_MS) return;
  tUltimaConexao = agora;

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi: aguardando conexao...");
    return;
  }
  if (!avisouWifi) {
    avisouWifi = true;
    Serial.print("WiFi conectado, IP "); Serial.println(WiFi.localIP());
  }

  // Last Will: se a conexao cair sem DISCONNECT, o broker publica isto,
  // retido, no topico de status. O consumidor fica sabendo sem esperar.
  char will[160];
  montarStatus(will, sizeof will, "offline");
  Serial.print("MQTT: conectando a "); Serial.print(MQTT_BROKER); Serial.println("...");
  if (!mqtt.connect(clientId, topicoStatus, 1, true, will)) {
    Serial.print("MQTT: falhou, estado="); Serial.println(mqtt.state());
    return;
  }
  char online[160];
  montarStatus(online, sizeof online, "online");
  mqtt.publish(topicoStatus, online, true);
  mqtt.subscribe(topicoAck, 1);
  Serial.println("MQTT conectado");

  // A cabeca da fila (se houver) sai de novo ja, sem esperar o timeout antigo.
  cabecaEnviada = false;
  esperaAckMs = ESPERA_ACK_INICIAL_MS;
}

void alternarRede() {
  redeLigada = !redeLigada;
  if (!redeLigada) {
    // Fecha o TCP sem mandar DISCONNECT: para o broker e uma queda de verdade,
    // entao ele publica o Last Will para os assinantes.
    rede.stop();
    Serial.println("-- REDE DERRUBADA: conexao fechada sem DISCONNECT --");
  } else {
    tUltimaConexao = 0;
    Serial.println("-- REDE RESTAURADA: reconectando --");
  }
}

// ------------------------------- estado ------------------------------------
void trocarEstado(Estado novo, float valor, const char* razao) {
  Estado antigo = estado;
  estado = novo;
  acenderLeds();

  Serial.print("["); Serial.print(millis()); Serial.print("] ");
  Serial.print("estado="); Serial.print(nomeEstado(antigo));
  Serial.print(" -> ");    Serial.print(nomeEstado(novo));
  Serial.print("  media="); Serial.print(valor, 1); Serial.print(" dBFS");
  Serial.print("  razao="); Serial.println(razao);

  enfileirarEstado(valor, razao);
}

// ------------------------------- setup -------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(PIN_LED_ENVIANDO, OUTPUT);
  pinMode(PIN_LED_MUDO,     OUTPUT);
  pinMode(PIN_LED_FALHA,    OUTPUT);
  pinMode(PIN_LED_REDE,     OUTPUT);
  pinMode(PIN_BTN_SENSOR,   INPUT_PULLUP);
  pinMode(PIN_BTN_REENVIAR, INPUT_PULLUP);
  pinMode(PIN_BTN_REDE,     INPUT_PULLUP);

  // bootId aleatorio: separa a sequence desta vida do ESP32 das anteriores.
  snprintf(bootId, sizeof bootId, "%08lx",
           (unsigned long) ((uint32_t) random(0x7FFFFFFF) ^ (uint32_t) micros()));
  snprintf(clientId, sizeof clientId, "towerai-%s-%s", DEVICE_ID, bootId);
  snprintf(topicoEventos, sizeof topicoEventos, "%s/%s/telemetry/audio", PREFIXO, ENTITY_ID);
  snprintf(topicoStatus,  sizeof topicoStatus,  "%s/%s/status/%s", PREFIXO, ENTITY_ID, DEVICE_ID);
  snprintf(topicoAck,     sizeof topicoAck,     "%s/%s/ack/%s",    PREFIXO, ENTITY_ID, DEVICE_ID);

  mqtt.setServer(MQTT_BROKER, MQTT_PORTA);
  mqtt.setCallback(aoReceberAck);
  mqtt.setBufferSize(768);   // o evento tem ~330 bytes; o padrao e 256
  mqtt.setKeepAlive(10);
  mqtt.setSocketTimeout(3);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_SENHA, 6);

  Serial.println();
  Serial.println("=== TowerAI / Marco 2 / produtor mic-esp32-01 ===");
  Serial.print("bootId=");   Serial.print(bootId);
  Serial.print(" broker=");  Serial.print(MQTT_BROKER);
  Serial.print(":");         Serial.println(MQTT_PORTA);
  Serial.print("publica: "); Serial.println(topicoEventos);
  Serial.print("status : "); Serial.println(topicoStatus);
  Serial.print("ACK    : "); Serial.println(topicoAck);
  Serial.println("PRONTO");

  acenderLeds();
}

// -------------------------------- loop -------------------------------------
void loop() {
  uint32_t agora = millis();

  // ------------------------------------------------------------ REDE -------
  if (redeLigada) {
    conectar(agora);
    mqtt.loop();  // mantem a sessao e entrega os ACKs em aoReceberAck
  }
  if (btnRede.apertou())     alternarRede();
  if (btnReenviar.apertou()) reenviarUltimoConfirmado();
  digitalWrite(PIN_LED_REDE, mqtt.connected() ? HIGH : LOW);

  processarFila(agora);

  if (agora - tUltimoHeartbeat >= PERIODO_HEARTBEAT_MS) {
    tUltimoHeartbeat = agora;
    enviarTelemetria("device.heartbeat", mediaAtual, "heartbeat");
  }

  if (agora - tUltimaAmostra < PERIODO_AMOSTRA_MS) return;
  tUltimaAmostra = agora;

  // ----------------------------------------------------------- LER ---------
  int  bruto   = lerBruto();
  bool ausente = (bruto == LEITURA_AUSENTE);

  if (ausente) { if (leiturasAusentes < 65535) leiturasAusentes++; }
  else         { leiturasAusentes = 0; }

  float dbfsInstantaneo = ausente ? NAN : bruto_para_dbfs(bruto);
  bool foraDaFaixa = !ausente && (dbfsInstantaneo < FAIXA_MIN ||
                                  dbfsInstantaneo > FAIXA_MAX);
  bool amostraBoa  = !ausente && !foraDaFaixa;

  // ---------------------------------------------------- MEDIA MOVEL --------
  if (amostraBoa) {
    bufSoma -= buffer[bufIdx];
    buffer[bufIdx] = bruto;
    bufSoma += bruto;
    bufIdx = (bufIdx + 1) % N_MEDIA;
    if (bufN < N_MEDIA) bufN++;
  }
  if (bufN > 0) mediaAtual = bruto_para_dbfs((float) bufSoma / bufN);

  // ----------------------------------------------- ESTADO CANDIDATO --------
  Estado alvo;
  const char* razao;

  if (leiturasAusentes >= AUSENTES_P_FALHA) {
    alvo  = FALHA_SENSOR;
    razao = "sensor_nao_respondeu_300ms";
  } else if (foraDaFaixa) {
    alvo  = FALHA_SENSOR;
    razao = "valor_fora_da_faixa";
  } else if (ausente) {
    alvo  = estado;
    razao = "leitura_perdida_tolerada";
  } else if (bufN < N_MEDIA) {
    alvo  = DESCONHECIDO;
    razao = "amostras_insuficientes";
  } else if (mediaAtual >= LIMIAR_SILENCIO) {
    alvo  = FALANDO;
    razao = "media_acima_do_limiar_estavel";
  } else {
    alvo  = SILENCIO;
    razao = "media_abaixo_do_limiar_estavel";
  }

  // ------------------------------------------------------- DEBOUNCE --------
  // A falha nao espera o debounce; o caminho normal exige estabilidade.
  if (alvo != estado) {
    if (alvo == FALHA_SENSOR) {
      trocarEstado(alvo, mediaAtual, razao);
      candidato = alvo;
      candidatoDesde = 0;
    } else if (alvo != candidato) {
      candidato = alvo;
      candidatoDesde = agora;
    } else if (candidatoDesde > 0 && agora - candidatoDesde >= DEBOUNCE_MS) {
      trocarEstado(alvo, mediaAtual, razao);
      candidatoDesde = 0;
    }
  } else {
    candidato = estado;
    candidatoDesde = 0;
  }

  // -------------------------------------------------------- NIVEL ----------
  if (estado == FALANDO && agora - tUltimoNivel >= PERIODO_NIVEL_MS) {
    tUltimoNivel = agora;
    enviarTelemetria("audio.level", mediaAtual, "amostragem_periodica");
  }

  // ------------------------------------------------------- RESUMO ----------
  if (agora - tUltimoResumo >= 2000) {
    tUltimoResumo = agora;
    Serial.print("["); Serial.print(agora); Serial.print("] RESUMO ");
    Serial.print("estado=");      Serial.print(nomeEstado(estado));
    Serial.print(" media=");      Serial.print(mediaAtual, 1);
    Serial.print(" mqtt=");       Serial.print(mqtt.connected() ? "conectado" : "desconectado");
    Serial.print(" pendentes=");  Serial.print(filaTamanho);
    Serial.print(" descartados="); Serial.print(descartadosFila);
    Serial.print(" seq=");        Serial.println(seq);
  }
}
