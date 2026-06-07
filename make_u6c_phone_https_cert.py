#!/usr/bin/env python3
"""
Create local HTTPS certificates for U6C phone-camera mode.

The generated CA certificate is meant to be installed and trusted only on your
own phone for your own LAN. Do not reuse these files for public websites.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
except ImportError as exc:
    print("Missing dependency: cryptography")
    print("Install it with: py -3 -m pip install cryptography")
    raise SystemExit(1) from exc


def guess_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def local_names() -> list[str]:
    names = ["localhost", guess_lan_ip()]
    hostname = socket.gethostname()
    if hostname:
        names.extend([hostname, f"{hostname}.local"])
    try:
        _name, _aliases, addresses = socket.gethostbyname_ex(hostname)
        names.extend(addresses)
    except OSError:
        pass

    cleaned: list[str] = []
    for name in names:
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def load_key(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"{path} is not an RSA private key")
    return key


def load_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def make_ca(ca_name: str, days: int) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = new_key()
    now = datetime.now(timezone.utc)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, ca_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "U6C Local"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                content_commitment=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    return key, cert


def san_entries(hosts: list[str]) -> list[x509.GeneralName]:
    entries: list[x509.GeneralName] = []
    for host in hosts:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            entries.append(x509.DNSName(host))
    return entries


def make_server_cert(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    server_key: rsa.RSAPrivateKey,
    hosts: list[str],
    days: int,
) -> x509.Certificate:
    now = datetime.now(timezone.utc)
    primary = hosts[0] if hosts else "localhost"
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, primary),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "U6C Local"),
        ]
    )
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_entries(hosts)), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                data_encipherment=False,
                key_agreement=False,
                content_commitment=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )


def parse_args() -> argparse.Namespace:
    here = app_base_dir()
    parser = argparse.ArgumentParser(
        description="Create HTTPS certs for U6C phone-camera mode",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--out-dir", type=Path, default=here / "certs", help="certificate output folder")
    parser.add_argument("--host", action="append", default=[], help="extra IP or DNS name for the server cert")
    parser.add_argument("--force", action="store_true", help="replace the local CA as well as the server cert")
    parser.add_argument("--ca-days", type=int, default=3650, help="local CA lifetime in days")
    parser.add_argument("--server-days", type=int, default=365, help="server certificate lifetime in days")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    ca_cert_path = out_dir / "u6c_phone_ca.crt"
    ca_key_path = out_dir / "u6c_phone_ca.key"
    server_cert_path = out_dir / "u6c_phone_server.crt"
    server_key_path = out_dir / "u6c_phone_server.key"

    if ca_cert_path.exists() and ca_key_path.exists() and not args.force:
        ca_cert = load_cert(ca_cert_path)
        ca_key = load_key(ca_key_path)
        reused_ca = True
    else:
        ca_key, ca_cert = make_ca("U6C Phone Camera Local CA", args.ca_days)
        write_key(ca_key_path, ca_key)
        write_cert(ca_cert_path, ca_cert)
        reused_ca = False

    hosts = local_names()
    for host in args.host:
        if host and host not in hosts:
            hosts.insert(0, host)

    server_key = new_key()
    server_cert = make_server_cert(
        ca_key=ca_key,
        ca_cert=ca_cert,
        server_key=server_key,
        hosts=hosts,
        days=max(1, min(args.server_days, 825)),
    )
    write_key(server_key_path, server_key)
    write_cert(server_cert_path, server_cert)

    print("U6C HTTPS certificate files are ready.")
    print(f"CA certificate for phone: {ca_cert_path}")
    print(f"Server certificate:       {server_cert_path}")
    print(f"Server private key:       {server_key_path}")
    print(f"CA reused:                {'yes' if reused_ca else 'no'}")
    print(f"Server names:             {', '.join(hosts)}")
    print()
    print("Next run the scanner with --phone-camera --phone-https.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
