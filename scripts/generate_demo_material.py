"""Generate the local demo's keys, certificates, passwords and secret files.

This script exists so that the demo stack can be brought up without a single credential living
in the repository. It writes everything into one gitignored directory; that directory is the
material, and this file is the only thing committed.

Nothing here is production material. The certificate authority is created fresh on each run, the
identity-provider key signs only tokens for synthetic subjects, and the database passwords are
random per run. None of it is reusable anywhere else, which is the point: a reader who finds this
directory on a machine learns nothing about any real system.

Usage:
    uv run python scripts/generate_demo_material.py [--force]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import workspace_env  # noqa: F401  (import for side effects: workspace sys.path integrity)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIRECTORY = ROOT / ".demo"

TLS_HOSTNAME = "staging.internal"
ISSUER_URL = "https://demo-idp.local"
AUDIENCE = "nha-trang-laundry-staff-demo"
JWKS_URL = "http://demo-idp:9000/.well-known/jwks.json"
MFA_CLAIM = "amr_mfa"
MFA_VALUE = "mfa"

DATABASE_NAME = "nha_trang_laundry"
DATABASE_HOST = "postgres"
SUPERUSER = "app"

ROLES = ("laundry_migrate", "laundry_api", "laundry_worker")


def _write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def _password() -> str:
    """A URL-safe password: it is embedded in a DSN, so it must not need percent-encoding."""
    return secrets.token_urlsafe(24)


def generate_certificate_authority() -> tuple[str, x509.Certificate, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nha Trang Laundry demo CA")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    return pem, certificate, key


def generate_leaf_certificate(
    authority: x509.Certificate, authority_key: rsa.RSAPrivateKey
) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, TLS_HOSTNAME)]))
        .issuer_name(authority.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(TLS_HOSTNAME),
                    x509.DNSName("localhost"),
                    x509.IPAddress(IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(authority_key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode(),
    )


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_identity_provider_key() -> tuple[str, str]:
    """Return (private key PEM, JWKS document) for the demo issuer.

    The key is RSA because apps/api pins algorithms=["RS256"]; the demo issuer must satisfy the
    real verifier rather than the verifier being relaxed to satisfy the demo.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    numbers = key.public_key().public_numbers()
    key_id = secrets.token_hex(8)
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": key_id,
                "n": _b64(numbers.n),
                "e": _b64(numbers.e),
            }
        ]
    }
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return private_pem, json.dumps(jwks, indent=2)


def role_initialisation_sql(passwords: dict[str, str]) -> str:
    """Create the three roles the deployment topology separates.

    Table-level grants cannot be issued here: the tables do not exist until the migration job has
    run. `scripts/apply_demo_grants.py` completes the split afterwards. What this file guarantees
    is that the API and worker roles exist with no DDL rights and own nothing.
    """
    statements = [
        "-- Generated by scripts/generate_demo_material.py. Local demo only.",
        "-- Runs once, on first initialisation of the PostgreSQL data directory.",
    ]
    for role in ROLES:
        statements.append(
            f"CREATE ROLE {role} LOGIN PASSWORD '{passwords[role]}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;"
        )
    statements += [
        f"GRANT CONNECT ON DATABASE {DATABASE_NAME} TO " + ", ".join(ROLES) + ";",
        "GRANT USAGE ON SCHEMA public TO " + ", ".join(ROLES) + ";",
        "-- Only the migration identity may create objects.",
        "GRANT CREATE ON SCHEMA public TO laundry_migrate;",
        "REVOKE CREATE ON SCHEMA public FROM laundry_api, laundry_worker;",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
    ]
    return "\n".join(statements) + "\n"


def generate(force: bool) -> None:
    if DEMO_DIRECTORY.exists() and any(DEMO_DIRECTORY.iterdir()) and not force:
        raise SystemExit(
            f"{DEMO_DIRECTORY} already contains material. Re-run with --force to replace it; "
            "the running stack must be stopped first or it will keep the previous keys."
        )

    secrets_directory = DEMO_DIRECTORY / "secrets"
    passwords = {role: _password() for role in ROLES}

    authority_pem, authority, authority_key = generate_certificate_authority()
    certificate_pem, certificate_key_pem = generate_leaf_certificate(authority, authority_key)
    _write(DEMO_DIRECTORY / "ca.crt", authority_pem, mode=0o644)
    _write(secrets_directory / "tls_certificate", certificate_pem)
    _write(secrets_directory / "tls_private_key", certificate_key_pem)

    private_pem, jwks = generate_identity_provider_key()
    _write(DEMO_DIRECTORY / "idp" / "private_key.pem", private_pem)
    _write(DEMO_DIRECTORY / "idp" / "jwks.json", jwks, mode=0o644)

    for role, secret_name in (
        ("laundry_migrate", "migration_database_url"),
        ("laundry_api", "api_database_url"),
        ("laundry_worker", "worker_database_url"),
    ):
        dsn = f"postgresql://{role}:{passwords[role]}@{DATABASE_HOST}:5432/{DATABASE_NAME}"
        _write(secrets_directory / secret_name, dsn)

    _write(secrets_directory / "oidc_issuer", ISSUER_URL)
    _write(secrets_directory / "oidc_audience", AUDIENCE)
    _write(secrets_directory / "oidc_jwks_url", JWKS_URL)
    _write(secrets_directory / "oidc_mfa_claim", MFA_CLAIM)
    _write(secrets_directory / "oidc_mfa_value", MFA_VALUE)

    _write(
        DEMO_DIRECTORY / "postgres-init" / "00-roles.sql",
        role_initialisation_sql(passwords),
        mode=0o644,
    )

    # The superuser DSN is what seeding and grants use; it never reaches a service container.
    _write(
        DEMO_DIRECTORY / "superuser_database_url",
        f"postgresql://{SUPERUSER}:app@127.0.0.1:5432/{DATABASE_NAME}",
    )

    print(f"Demo material written to {DEMO_DIRECTORY.relative_to(ROOT)}/ (gitignored).")
    print("Certificate authority for the smoke check: .demo/ca.crt")
    print("Nothing in that directory is production material and none of it is committed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace existing demo material")
    generate(parser.parse_args().force)


if __name__ == "__main__":
    main()
