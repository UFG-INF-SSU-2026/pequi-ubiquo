#pragma once
// Copie broker/local/segredos.h para segredos.h: a CA e a senha sao geradas
// pelo preparar.py. Este modelo explica os campos, nao contem acesso valido.
#define WIFI_SSID "REDE_DA_SALA"
#define WIFI_SENHA "SENHA_DA_REDE"
// Wokwi com gateway privado: host.wokwi.internal e Wi-Fi Wokwi-GUEST.
// Hardware: DNS local do PC, ou IP LAN incluido no SAN ao gerar certificado.
#define MQTT_BROKER "towerai-broker.local"
#define NTP_SERVIDOR "192.168.50.1"
#define MQTT_USUARIO "mic-esp32-01"
#define MQTT_SENHA "SENHA_GERADA_PELO_PROVISIONAMENTO"
static const char CA_RAIZ[] = R"PEM(
COLE_O_CERTIFICADO_PUBLICO_DE_broker/local/ca.pem
)PEM";
