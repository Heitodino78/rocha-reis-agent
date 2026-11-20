"""Certificado TLS de DESENVOLVIMENTO para testes locais (Fase 13).

Autoassinado, só para 127.0.0.1/localhost, validade curta. NÃO é certificado público confiável e
não serve para produção: lá o proxy reverso obtém certificado de uma autoridade reconhecida.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def create_dev_certificate(directory: Path, *, days: int = 30) -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rocha-reis-dev-local"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DESENVOLVIMENTO - nao confiavel")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                    x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = directory / "dev.key", directory / "dev.crt"
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def certificate_days_left(cert_path: Path) -> int:
    certificate = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    return (certificate.not_valid_after_utc - datetime.now(timezone.utc)).days
