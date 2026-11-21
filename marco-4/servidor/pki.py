"""Provisiona uma CA local e um certificado com os SANs da demonstração."""

import ipaddress
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path


def issue(directory, hosts):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {name: directory / name for name in ("ca.pem", "server.pem", "server-key.pem")}
    if any(path.exists() for path in files.values()):
        raise FileExistsError("Já existem certificados nesse diretório. Escolha outro diretório para trocar a CA.")

    def name(text):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

    def san(host):
        try:
            return x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            if not host or any(char in host for char in "/: *"):
                raise ValueError(f"Nome inválido para o certificado: {host}")
            return x509.DNSName(host.encode("idna").decode("ascii"))

    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = name("TowerAI Marco 4 — CA local")
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    certificate = (
        x509.CertificateBuilder().subject_name(name("Servidor TowerAI")).issuer_name(ca_name)
        .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([san(host) for host in dict.fromkeys(hosts)]), critical=False)
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    with os.fdopen(os.open(files["server-key.pem"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as key:
        key.write(server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
    files["server.pem"].write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    files["ca.pem"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    return files["ca.pem"]


def tls_context(directory):
    directory = Path(directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(directory / "server.pem", directory / "server-key.pem")
    return context
