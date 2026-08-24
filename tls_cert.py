"""Generate a self-signed TLS certificate for the appliance.

A stable cert on disk (unlike Flask's 'adhoc', which regenerates every restart)
lets devices trust it once. Covers the mDNS hostname, localhost, and *.local.
"""
import datetime
import ipaddress
import os
from pathlib import Path


def generate_self_signed(cert_path: str, key_path: str, hostname: str = "towerai"):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{hostname}.local")])
    alt = x509.SubjectAlternativeName([
        x509.DNSName(f"{hostname}.local"),
        x509.DNSName(hostname),
        x509.DNSName("localhost"),
        x509.DNSName("*.local"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(alt, critical=False)
        .sign(key, hashes.SHA256())
    )

    Path(os.path.dirname(cert_path) or ".").mkdir(parents=True, exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
