"""Prepare a shop machine to run the console for the pilot week.

Writes the secrets `compose.shop-local.yaml` reads, and the private CA and certificate the console
needs because its hostname is internal and no public CA can issue for it.

**What this deliberately does not do.** It does not generate the backup identity. `DEC-026` places
the private half of that key off the machine that writes the archive, and a script that generated it
here would have written it next to the thing it protects -- the exact failure ADR-0007 §3 excludes.
Generate it on your own device and pass the public half in:

    age-keygen -o ~/laundry-backup-identity.txt
    grep 'public key' ~/laundry-backup-identity.txt

It also refuses to overwrite anything. Re-running is safe; a second run after the shop has been
trading would otherwise rotate the database passwords out from under a live system.
"""

from __future__ import annotations

# Put this directory on sys.path before importing the workspace bootstrap, so the script
# behaves identically whether it is run as __main__ or loaded by file path from a test.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import getpass
import re
import secrets as _secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Network, IPv6Network

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = _Path(__file__).resolve().parents[1]
SECRET_DIRECTORY = ROOT / ".shop/secrets"

#: `age` public keys are bech32 with this prefix. Checked because a mistyped recipient produces an
#: archive nobody can read, and the first time anyone would find out is a restore.
RECIPIENT = re.compile(r"^age1[0-9a-z]{50,}$")

#: A hostname rather than an IP: the certificate carries it, the browser checks it, and the API's
#: trusted-host and origin lists are built from it. `.lan` is not a public suffix, so no public CA
#: can issue for it -- which is why this script mints a private one.
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def password() -> str:
    """A password nobody types, so it is long rather than memorable."""

    return _secrets.token_urlsafe(32)


def write(name: str, value: str, *, existing: list[str]) -> None:
    target = SECRET_DIRECTORY / name
    if target.exists():
        existing.append(name)
        return
    target.write_text(value, encoding="utf-8")
    target.chmod(0o600)


#: How long the console certificate lives. 825 days is the ceiling Apple platforms accept for a
#: TLS server certificate from a private CA; longer and iPads refuse it.
CONSOLE_CERTIFICATE_DAYS = 825
#: The authority outlives several console certificates only if its key is kept (`--export-ca-key`).
AUTHORITY_DAYS = 3650


def mint_console_authority(host: str) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """A private CA that can vouch for the console's name and for nothing else.

    `PLATFORM-SECURITY-009` P4. The CA certificate is installed *and trusted* on every tablet, so
    without constraints its key can mint a certificate those tablets accept for any site -- a bank,
    a mail provider -- which makes a key sitting on the till the most valuable file on it.
    `nameConstraints` (critical, so a client that cannot enforce it must reject the chain) permits
    the console's DNS name and its subdomains only, and excludes every IP address so an
    IP-addressed certificate cannot slip past a DNS-only permit. `pathlen:0` stops it minting
    another CA; `keyUsage` limits it to signing certificates and CRLs.
    """

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Giat La Sach Cong Internal CA")])
    now = datetime.now(UTC)
    authority = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=AUTHORITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(host)],
                excluded_subtrees=[
                    x509.IPAddress(IPv4Network("0.0.0.0/0")),
                    x509.IPAddress(IPv6Network("::/0")),
                ],
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return authority, key


def issue_console_certificate(
    host: str, authority: x509.Certificate, authority_key: rsa.RSAPrivateKey
) -> tuple[bytes, bytes]:
    """(certificate PEM, private key PEM) for the console, signed by `authority`."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(authority.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CONSOLE_CERTIFICATE_DAYS))
        # `subjectAltName` and not only the subject: browsers have ignored the common name for
        # years, and a certificate without a SAN fails with an error that does not say why.
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .sign(authority_key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


class CertificateRefused(SystemExit):
    """A certificate step that cannot be done safely; the message says what to do instead."""


def _write_secret(path: _Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def certificate(
    host: str,
    existing: list[str],
    *,
    export_ca_key: _Path | None = None,
    ca_key: _Path | None = None,
    passphrase: Callable[[str], bytes] | None = None,
) -> list[str]:
    """A private, name-constrained CA and one server certificate for the console.

    Two files rather than one: the CA certificate is what gets installed on every tablet, and it
    outlives the server certificate.

    **The CA private key is never written to this machine** (`PLATFORM-SECURITY-009` P4). It is
    generated in memory, signs the console certificate, and is dropped. This machine serves the
    console, so it is the one most exposed; a CA key beside the certificate it signed is the key to
    every tablet's trust. Two ways to keep it, both off this host:

    - `--export-ca-key PATH` writes it passphrase-encrypted to PATH, which must be outside this
      checkout -- a removable drive, then unplug it. `--ca-key PATH` reads it back to renew the
      console certificate without touching the tablets.
    - Or keep nothing: when the console certificate expires, move `.shop/ca` aside, run this again,
      and install the new `ca.crt` on each tablet.

    Returns warnings for the operator (a CA key an earlier version left behind).
    """

    warnings: list[str] = []
    authority_directory = SECRET_DIRECTORY.parent / "ca"
    authority_directory.mkdir(parents=True, exist_ok=True)
    # `mkdir` takes the umask default, so this came out 0755 -- world-listable. `chmod` is applied
    # on every run because an existing directory keeps its mode.
    authority_directory.chmod(0o700)
    ca_certificate_path = authority_directory / "ca.crt"
    legacy_key = authority_directory / "ca.key"
    if legacy_key.exists():
        warnings.append(
            f"{legacy_key.relative_to(ROOT)} is a CA private key an earlier version of this script "
            "left on this machine, for a CA with no name constraints. Mint a constrained one: move "
            ".shop/ca and .shop/secrets/tls_* aside, run this again, reinstall ca.crt on every "
            "tablet, then delete the old key (docs/runbooks/shop-pilot.md §2)."
        )
    if export_ca_key is not None:
        _refuse_inside_checkout(export_ca_key, "--export-ca-key")

    if (SECRET_DIRECTORY / "tls_certificate").exists():
        existing.append("tls_certificate")
        if not ca_certificate_path.exists():
            existing.append("ca/ca.crt (absent)")
        return warnings

    if ca_certificate_path.exists():
        # Renewal: the tablets already trust this CA, so the console certificate is re-signed by
        # it -- which needs the key that was deliberately not kept here.
        authority = x509.load_pem_x509_certificate(ca_certificate_path.read_bytes())
        existing.append("ca/ca.crt")
        if ca_key is None:
            raise CertificateRefused(
                "The console certificate is missing and the CA's private key is not on this "
                "machine (it never is). Either pass --ca-key PATH with the key you exported, or "
                "move .shop/ca aside and run this again to mint a new CA -- then install the new "
                "ca.crt on every tablet."
            )
        material = ca_key.read_bytes()
        loaded = serialization.load_pem_private_key(
            material,
            password=(passphrase or _ask)("Passphrase for the CA key: ")
            if b"ENCRYPTED" in material
            else None,
        )
        if not isinstance(loaded, rsa.RSAPrivateKey) or (
            loaded.public_key().public_numbers() != authority.public_key().public_numbers()
        ):
            raise CertificateRefused(f"{ca_key} is not the key of {ca_certificate_path}")
        authority_key = loaded
    else:
        authority, authority_key = mint_console_authority(host)
        _write_secret(ca_certificate_path, authority.public_bytes(serialization.Encoding.PEM))
        ca_certificate_path.chmod(0o644)  # a certificate is public; it goes to every tablet
        if export_ca_key is not None:
            secret = (passphrase or _ask_twice)("Passphrase to protect the exported CA key: ")
            if len(secret) < 12:
                raise CertificateRefused("the CA key passphrase must be at least 12 characters")
            _write_secret(
                export_ca_key,
                authority_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(secret),
                ),
            )

    certificate_pem, key_pem = issue_console_certificate(host, authority, authority_key)
    _write_secret(SECRET_DIRECTORY / "tls_private_key", key_pem)
    _write_secret(SECRET_DIRECTORY / "tls_certificate", certificate_pem)
    del authority_key
    return warnings


def _refuse_inside_checkout(path: _Path, flag: str) -> None:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise CertificateRefused(
            f"{flag} {path} is inside this checkout, on the machine that serves the console. "
            "Write it to a removable drive (then unplug it) or another machine."
        )


def _ask(prompt: str) -> bytes:
    return getpass.getpass(prompt).encode()


def _ask_twice(prompt: str) -> bytes:
    first = _ask(prompt)
    if _ask("Again: ") != first:
        raise CertificateRefused("the two passphrases differ; nothing was exported")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console-host", default="console.giatlasachcong.lan")
    parser.add_argument(
        "--backup-recipient",
        required=True,
        help="the age PUBLIC key. Generate the pair on your own device, not on this machine.",
    )
    parser.add_argument(
        "--backup-repository-credential",
        default="",
        help=(
            "the object-store credential. REQUIRED in practice: `postgresql.conf` sets "
            "archive_mode = on unconditionally, so leaving this empty does not turn archiving off "
            "-- it leaves every archive attempt failing, WAL pinned on disk, and the "
            "volume filling "
            "until PostgreSQL stops accepting writes."
        ),
    )
    parser.add_argument(
        "--export-ca-key",
        type=_Path,
        default=None,
        help=(
            "write the new CA's private key, passphrase-encrypted, to this path OUTSIDE the "
            "checkout (a removable drive). Without it the key is never written anywhere and a "
            "future renewal mints a new CA."
        ),
    )
    parser.add_argument(
        "--ca-key",
        type=_Path,
        default=None,
        help="renew the console certificate with the CA key exported earlier",
    )
    arguments = parser.parse_args()

    host = arguments.console_host.strip().lower()
    if not HOSTNAME.fullmatch(host):
        raise SystemExit(f"--console-host must be a dotted hostname, not {host!r}")
    recipient = arguments.backup_recipient.strip()
    if not RECIPIENT.fullmatch(recipient):
        raise SystemExit(
            "--backup-recipient must be an age public key beginning age1. A mistyped recipient "
            "produces an archive nobody can read, and a restore is where you would find out."
        )

    SECRET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    SECRET_DIRECTORY.chmod(0o700)
    # The parent too: `mkdir(parents=True)` gives it the umask default, so `.shop/` itself came out
    # 0755 and world-listable around a 0700 directory of secrets.
    SECRET_DIRECTORY.parent.chmod(0o700)
    existing: list[str] = []

    roles = {name: password() for name in ("laundry_migrate", "laundry_api", "laundry_worker")}
    for role, secret in roles.items():
        short = role.removeprefix("laundry_")
        name = "migration" if short == "migrate" else short
        write(
            f"{name}_database_url",
            f"postgresql://{role}:{secret}@postgres:5432/nha_trang_laundry",
            existing=existing,
        )
    write("postgres_password", roles["laundry_migrate"], existing=existing)
    # `HASH-KEYING-001`. The commitments this key produces outlive the data they describe, so it is
    # generated here rather than typed: a human-chosen key would be guessable, and a manual step
    # between cloning and running is a step somebody skips. It is written once and never rotated by
    # a re-run -- `write` refuses to overwrite -- because rotating it under a trading shop makes
    # every previously stored commitment incomparable.
    write("hash_key", _secrets.token_urlsafe(48), existing=existing)
    write("keycloak_database_password", password(), existing=existing)
    # `laundry_backup`'s password. `pg_basebackup --no-password` never prompts and `pg_hba` demands
    # scram for the replication connection, so this file is the only way the base backup can
    # authenticate -- and it was missing entirely, which is why the first pilot bring-up could
    # archive WAL and never take a base backup to replay it onto.
    write("backup_source_password", password(), existing=existing)
    # No `keycloak_bootstrap_admin_password`: nothing mounts it and nothing reads it, so it was a
    # live admin credential sitting on disk for no reason. `SHOP-FIRST-START-001` removed the
    # mount and this line outlived it. The admin is created interactively with
    # `kc.sh bootstrap-admin user`, once, on the host.

    write("oidc_issuer", f"https://{host}:8443/idp/realms/nhatrang", existing=existing)
    write("oidc_audience", "staff-console", existing=existing)
    write(
        "oidc_jwks_url",
        "http://keycloak:8080/idp/realms/nhatrang/protocol/openid-connect/certs",
        existing=existing,
    )
    # `acr`, not `amr`. Measured against Keycloak with a real browser: `amr` is absent entirely and
    # the verifier reads only strings, so binding to it fails every privileged sign-in with a
    # generic 401 that looks exactly like a bad password.
    write("oidc_mfa_claim", "acr", existing=existing)
    write("oidc_mfa_value", "mfa", existing=existing)

    write("backup_encryption_recipients", recipient, existing=existing)
    write(
        "backup_repository_credential",
        arguments.backup_repository_credential,
        existing=existing,
    )

    warnings = certificate(
        host, existing, export_ca_key=arguments.export_ca_key, ca_key=arguments.ca_key
    )

    print(f"  secrets written to {SECRET_DIRECTORY.relative_to(ROOT)} (0600, gitignored)")
    if existing:
        print(f"  left untouched because they already exist: {', '.join(sorted(set(existing)))}")
    print()
    print("  Create the database roles with these passwords, once postgres is up:")
    print()
    # Read back from the files rather than printing what was just generated. `write()` leaves an
    # existing secret alone, but `roles` is regenerated on every run -- so a re-run printed fresh
    # passwords that matched nothing on disk, and an operator who pasted them created roles the
    # API and worker could not authenticate as. The docstring says re-running is safe, and this is
    # what makes that true. The keycloak line below always did it this way.
    for role in roles:
        short = role.removeprefix("laundry_")
        name = "migration" if short == "migrate" else short
        if role == "laundry_migrate":
            continue
        dsn = (SECRET_DIRECTORY / f"{name}_database_url").read_text(encoding="utf-8")
        stored = dsn.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
        print(f"    CREATE ROLE {role} LOGIN PASSWORD '{stored}';")
    stored_backup = (SECRET_DIRECTORY / "backup_source_password").read_text(encoding="utf-8")
    print(f"    CREATE ROLE laundry_backup LOGIN REPLICATION PASSWORD '{stored_backup}';")
    print(
        f"    CREATE ROLE keycloak LOGIN PASSWORD "
        f"'{(SECRET_DIRECTORY / 'keycloak_database_password').read_text(encoding='utf-8')}';"
    )
    print("    CREATE DATABASE keycloak OWNER keycloak;")
    print()
    print("  Install .shop/ca/ca.crt on every tablet, and switch trust ON for it.")
    print(
        f"  It vouches for {host} only (name-constrained); its private key is not on this machine."
    )
    for warning in warnings:
        print(f"  WARNING: {warning}")
    print(f"  Point the shop's DNS at this machine for {host}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
