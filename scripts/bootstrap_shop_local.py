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
import re
import secrets as _secrets
import subprocess

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path

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


def certificate(host: str, existing: list[str]) -> None:
    """A private CA and one server certificate for the console.

    Two files rather than one: the CA certificate is what gets installed on every tablet, and it
    outlives the server certificate. Keeping them separate means renewing the server certificate
    does not mean re-trusting anything on ten devices.
    """

    authority = SECRET_DIRECTORY.parent / "ca"
    authority.mkdir(parents=True, exist_ok=True)
    # `mkdir` takes the umask default, so this came out 0755 -- world-listable, holding the private
    # key that signs the console certificate. The key file itself is 0600; the directory around it
    # was not, and `chmod` is applied on every run because an existing directory keeps its mode.
    authority.chmod(0o700)
    ca_certificate, ca_key = authority / "ca.crt", authority / "ca.key"

    if not ca_certificate.exists():
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-sha256",
                "-days",
                "3650",
                "-nodes",
                "-keyout",
                str(ca_key),
                "-out",
                str(ca_certificate),
                "-subj",
                "/CN=Giat La Sach Cong Internal CA",
            ],
            check=True,
            capture_output=True,
        )
        ca_key.chmod(0o600)
    else:
        existing.append("ca/ca.crt")

    if (SECRET_DIRECTORY / "tls_certificate").exists():
        existing.append("tls_certificate")
        return

    key = SECRET_DIRECTORY / "tls_private_key"
    request = authority / "console.csr"
    extensions = authority / "console.ext"
    # `subjectAltName` and not only the subject: browsers have ignored the common name for years,
    # and a certificate without a SAN fails with an error that does not say why.
    extensions.write_text(
        f"subjectAltName=DNS:{host}\nbasicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(request),
            "-subj",
            f"/CN={host}",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(request),
            "-CA",
            str(ca_certificate),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-days",
            "825",
            "-sha256",
            "-extfile",
            str(extensions),
            "-out",
            str(SECRET_DIRECTORY / "tls_certificate"),
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)
    (SECRET_DIRECTORY / "tls_certificate").chmod(0o600)
    request.unlink(missing_ok=True)


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
        help="the object-store credential. Empty means archiving stays off until you set one.",
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
    write("keycloak_database_password", password(), existing=existing)
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

    certificate(host, existing)

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
    print("    CREATE ROLE laundry_backup LOGIN REPLICATION PASSWORD '<pick one>';")
    print(
        f"    CREATE ROLE keycloak LOGIN PASSWORD "
        f"'{(SECRET_DIRECTORY / 'keycloak_database_password').read_text(encoding='utf-8')}';"
    )
    print("    CREATE DATABASE keycloak OWNER keycloak;")
    print()
    print("  Install .shop/ca/ca.crt on every tablet, and switch trust ON for it.")
    print(f"  Point the shop's DNS at this machine for {host}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
