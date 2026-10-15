"""Provisiona o laboratório local: CA, certificado, RBAC e perfis de clientes.

Não sobrescreve uma instalação existente. Downloads acontecem só nesta fase;
o broker e os clientes funcionam sem internet depois do provisionamento.
Os arquivos em local/ são secretos e ignorados pelo Git. Execute como usuário
normal e limite a pasta ao administrador da sala pelas permissões do sistema.
"""
import argparse
import base64
import hashlib
import ipaddress
import os
import secrets
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PASTA = Path(__file__).resolve().parent
RBAC_VERSAO = "4.6.15"  # versão fixa: formato PBKDF2 salt:iterações:hash
RBAC_URL = ("https://github.com/hivemq/hivemq-file-rbac-extension/releases/download/"
            f"{RBAC_VERSAO}/hivemq-file-rbac-extension-{RBAC_VERSAO}.zip")
BASE = "ufg-ssu-2026/towerai/sala-204"
CONTAS = {"mic-esp32-01": "dispositivo", "consumidor-sala-204": "consumidor",
          "painel-sala-204": "painel", "mic-injetor": "dispositivo"}


def hash_senha(senha):
    """Formato da File RBAC 4.6.15, conforme utils/Hashing.java oficial.

    PBKDF2-HMAC-SHA512, salt aleatório de 32 bytes, chave de 512 bits.
    Não é o formato legado de dois campos mostrado em exemplos antigos.
    """
    sal = secrets.token_bytes(32)
    iteracoes = 210_000
    digest = hashlib.pbkdf2_hmac("sha512", senha.encode(), sal, iteracoes, dklen=64)
    return f"{base64.b64encode(sal).decode()}:{iteracoes}:{base64.b64encode(digest).decode()}"


def escrever_xml(path, raiz):
    ET.indent(raiz)
    ET.ElementTree(raiz).write(path, encoding="utf-8", xml_declaration=True)


def credenciais(senhas):
    """Permissões positivas mínimas; ausência de permissão implica negação."""
    raiz = ET.Element("file-rbac")
    users = ET.SubElement(raiz, "users")
    for nome, papel in CONTAS.items():
        user = ET.SubElement(users, "user")
        ET.SubElement(user, "name").text = nome
        ET.SubElement(user, "password").text = hash_senha(senhas[nome])
        ET.SubElement(ET.SubElement(user, "roles"), "id").text = papel
    roles = ET.SubElement(raiz, "roles")
    # A substituição é da extensão, não de Docker nem do Python.
    caminhos = {
        "dispositivo": [("dev/${{username}}/telemetry", "PUBLISH", "NOT_RETAINED"),
                        ("dev/${{username}}/status", "PUBLISH", "ALL"),
                        ("dev/${{username}}/ack", "SUBSCRIBE", None)],
        "consumidor": [("dev/+/telemetry", "SUBSCRIBE", None),
                       ("dev/+/status", "SUBSCRIBE", None),
                       ("dev/+/ack", "PUBLISH", "NOT_RETAINED"),
                       ("insights/reuniao", "PUBLISH", "ALL")],
        "painel": [("insights/reuniao", "SUBSCRIBE", None)],
    }
    for nome, permissoes in caminhos.items():
        role = ET.SubElement(roles, "role")
        ET.SubElement(role, "id").text = nome
        lista = ET.SubElement(role, "permissions")
        for caminho, atividade, retido in permissoes:
            p = ET.SubElement(lista, "permission")
            ET.SubElement(p, "topic").text = f"{BASE}/{caminho}"
            ET.SubElement(p, "activity").text = atividade
            if retido:
                ET.SubElement(p, "retain").text = retido
    return raiz


def instalar_extensao(destino, arquivo=None):
    downloads = PASTA / "downloads"
    downloads.mkdir(exist_ok=True)
    pacote = arquivo or downloads / f"rbac-{RBAC_VERSAO}.zip"
    if arquivo is None:
        urllib.request.urlretrieve(RBAC_URL, pacote)
        esperado = urllib.request.urlopen(RBAC_URL + ".sha256", timeout=30).read().decode().split()[0]
        if hashlib.sha256(pacote.read_bytes()).hexdigest() != esperado:
            raise ValueError("checksum da extensão não confere")
    else:
        print("ZIP local: use somente distribuição oficial com checksum já conferido.")
    with zipfile.ZipFile(pacote) as zipado:
        # Validar antes de extrair evita que um ZIP escreva fora da pasta alvo.
        for info in zipado.infolist():
            alvo = (destino / info.filename).resolve()
            if not alvo.is_relative_to(destino.resolve()):
                raise ValueError("caminho inválido no ZIP")
        zipado.extractall(destino)
    manifestos = list(destino.rglob("hivemq-extension.xml"))
    if len(manifestos) != 1:
        raise ValueError("o pacote deve conter apenas uma extensão")
    return manifestos[0].parent


def preparar(destino, nomes, ips, arquivo=None):
    destino.mkdir()
    agora = datetime.now(timezone.utc)
    chave_ca = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    nome_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TowerAI CA da sala")])
    ca = (x509.CertificateBuilder().subject_name(nome_ca).issuer_name(nome_ca)
          .public_key(chave_ca.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(agora - timedelta(minutes=5)).not_valid_after(agora + timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(chave_ca.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(chave_ca.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(chave_ca, hashes.SHA256()))
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "towerai-broker.local")])
    san = [x509.DNSName(n) for n in nomes] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    certificado = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome_ca)
                   .public_key(chave.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(agora - timedelta(minutes=5)).not_valid_after(agora + timedelta(days=365))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(chave.public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(chave_ca.public_key()), critical=False)
                   .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                   .add_extension(x509.SubjectAlternativeName(san), critical=False)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .sign(chave_ca, hashes.SHA256()))
    # A chave da CA fica em backup separado, cifrada. Nunca é montada no broker.
    senha_ca = secrets.token_urlsafe(32)
    (destino / "ca-key-password.txt").write_text(senha_ca, encoding="utf-8")
    (destino / "ca-key.pem").write_bytes(chave_ca.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(senha_ca.encode())))
    (destino / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    senha_store = secrets.token_urlsafe(32)
    (destino / "server.p12").write_bytes(pkcs12.serialize_key_and_certificates(
        b"broker", chave, certificado, [ca], serialization.BestAvailableEncryption(senha_store.encode())))
    config = ET.fromstring('''<hivemq><listeners><tls-tcp-listener>
      <port>8883</port><bind-address>0.0.0.0</bind-address><name>mqtt-tls</name>
      <tls><keystore><path>/opt/hivemq/conf/server.p12</path><password />
      <private-key-password /></keystore><protocols><protocol>TLSv1.2</protocol>
      <protocol>TLSv1.3</protocol></protocols></tls>
      </tls-tcp-listener></listeners></hivemq>''')
    config.find(".//password").text = senha_store
    config.find(".//private-key-password").text = senha_store
    escrever_xml(destino / "config.xml", config)
    extensions = destino / "extensions"
    extensions.mkdir()
    rbac = instalar_extensao(extensions, arquivo)
    senhas = {nome: secrets.token_urlsafe(32) for nome in CONTAS}
    conf = rbac / "conf"
    conf.mkdir(exist_ok=True)
    escrever_xml(conf / "credentials.xml", credenciais(senhas))
    escrever_xml(conf / "config.xml", ET.fromstring('''<extension-configuration>
      <credentials-reload-interval>10</credentials-reload-interval>
      <password-type>HASHED</password-type>
      <next-extension-instead-of-fail>false</next-extension-instead-of-fail>
      </extension-configuration>'''))
    for usuario, senha in senhas.items():
        (destino / f"{usuario}.env").write_text(
            f"MQTT_BROKER=localhost\nMQTT_PORTA=8883\nMQTT_CA=ca.pem\n"
            f"MQTT_USUARIO={usuario}\nMQTT_SENHA={senha}\n", encoding="utf-8")
    # Provisionamento do firmware é explícito: este arquivo só existe localmente.
    (destino / "segredos.h").write_text(
        '#pragma once\n// Gerado para o laboratório; não publicar nem versionar.\n'
        '#define WIFI_SSID "AJUSTE_REDE_DA_SALA"\n#define WIFI_SENHA "AJUSTE_SENHA_WIFI"\n'
        '#define MQTT_BROKER "towerai-broker.local"\n#define NTP_SERVIDOR "192.168.50.1"\n'
        f'#define MQTT_USUARIO "mic-esp32-01"\n#define MQTT_SENHA "{senhas["mic-esp32-01"]}"\n'
        'static const char CA_RAIZ[] = R"PEM(\n' + (destino / "ca.pem").read_text() + ')PEM";\n',
        encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dns", action="append", default=[], help="SAN DNS adicional")
    ap.add_argument("--ip", action="append", default=[], help="SAN IP LAN adicional")
    ap.add_argument("--rbac-zip", type=Path, help="pacote oficial previamente baixado")
    args = ap.parse_args()
    destino = PASTA / "local"
    if destino.exists():
        ap.error("local/ já existe; preservar credenciais. Consulte o procedimento de rotação.")
    # Temporário no mesmo volume + rename: nunca deixa uma instalação parcial
    # parecendo pronta se o download ou a geração dos certificados falhar.
    temporario = Path(tempfile.mkdtemp(prefix=".preparar-", dir=PASTA))
    try:
        preparar(temporario / "local", ["localhost", "towerai-broker.local", "host.wokwi.internal"] + args.dns,
                 ["127.0.0.1", "::1"] + args.ip, args.rbac_zip)
        (temporario / "local").rename(destino)
        if os.name != "nt":
            destino.chmod(0o700)
            for arquivo in destino.iterdir():
                if arquivo.is_file():
                    arquivo.chmod(0o600)
        print("Broker preparado em broker/local/. Nenhuma senha foi escrita no terminal.")
    finally:
        shutil.rmtree(temporario)


if __name__ == "__main__":
    main()
