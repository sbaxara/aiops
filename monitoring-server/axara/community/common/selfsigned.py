# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create a self-signed TLS certificate for the server and dashboard (`manage.py make-cert`).

Used by bootstrap on a source install when certs/server.crt does not exist yet. Replace the
files with a certificate from your own CA for production.

Environment:
    CERT_DIR    folder to write server.crt and server.key to (default /certs)
    CERT_HOSTS  comma-separated host names for the certificate (default: this host's name);
                localhost and monitoring-server are always added
    CERT_FORCE  1 to overwrite existing files
"""
import datetime
import os
import socket
import sys
from pathlib import Path


def build(hosts):
    """A new RSA-3072 key and a self-signed certificate valid for 825 days.

    Args:
        hosts: Host names; the first is the common name, all are subject alternative names.

    Returns:
        (key PEM bytes, certificate PEM bytes).
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


def main() -> int:
    """Write server.crt and server.key unless they exist (CERT_FORCE=1 overwrites).

    Returns:
        Exit status.
    """
    out = Path(os.getenv("CERT_DIR", "/certs"))
    crt, key = out / "server.crt", out / "server.key"
    if crt.exists() and key.exists() and os.getenv("CERT_FORCE") != "1":
        print(f"{crt} already exists - kept (CERT_FORCE=1 to replace)")
        return 0
    given = [h.strip().lower() for h in os.getenv("CERT_HOSTS", "").split(",") if h.strip()]
    hosts = list(dict.fromkeys((given or [socket.gethostname().lower()]) + ["localhost", "monitoring-server"]))
    key_pem, crt_pem = build(hosts)
    out.mkdir(parents=True, exist_ok=True)
    key.write_bytes(key_pem)
    os.chmod(key, 0o600)
    crt.write_bytes(crt_pem)
    print(f"Self-signed certificate for {', '.join(hosts)} written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
