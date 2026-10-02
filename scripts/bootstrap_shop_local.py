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
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Network, IPv6Network
from typing import NamedTuple
from zoneinfo import ZoneInfo

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = _Path(__file__).resolve().parents[1]
SECRET_DIRECTORY = ROOT / ".shop/secrets"
#: Expiry dates are shown in the shop's own calendar.
_SHOP_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

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
#: `DEC-052`: the CA's key is dropped once it has signed the one console certificate, so the CA can
#: never sign again and has no reason to outlive that certificate. It used to be valid for ten
#: years -- a trust anchor left on every tablet long after the certificate it vouched for had gone.
#: A month's margin covers a renewal done late.
AUTHORITY_DAYS = CONSOLE_CERTIFICATE_DAYS + 30
#: When a run starts saying "renew now". Sixty days is two monthly owner check-ins: enough time to
#: reach every tablet, which is the slow part of a renewal under `DEC-052`.
RENEW_BEFORE_DAYS = 60


def mint_console_authority(
    host: str, *, now: datetime | None = None
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
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
    now = now or datetime.now(UTC)
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
    host: str,
    authority: x509.Certificate,
    authority_key: rsa.RSAPrivateKey,
    *,
    now: datetime | None = None,
) -> tuple[bytes, bytes]:
    """(certificate PEM, private key PEM) for the console, signed by `authority`."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = now or datetime.now(UTC)
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


#: `DEC-052`. Said whenever somebody asks this script to keep a CA key, whatever else is on disk.
CUSTODY_RULE = (
    "DEC-052: khóa riêng của CA không được giữ lại sau khi ký -- kể cả bản xuất ra ổ USB. "
    "(The shop CA's private key is never kept after it signs, not even exported.)"
)
RENEWAL_STEP = (
    "Gia hạn: chạy lại với --new-ca, rồi cài .shop/ca/ca.crt mới lên từng máy và bật tin cậy "
    "(docs/runbooks/shop-pilot.md §2). (To renew: run again with --new-ca, then install and trust "
    "the new ca.crt on every tablet.)"
)
#: What the operator reads about an authority that does not limit itself to the console's name.
LEGACY_AUTHORITY = "CA cũ không giới hạn tên miền — hãy tạo lại"


def refuse_kept_ca_key(flag: str, *, certificate_exists: bool) -> CertificateRefused:
    """The refusal for `--export-ca-key` / `--ca-key`: both ask to keep a key `DEC-052` drops.

    `PLATFORM-SECURITY-009` P4 offered to export the key so a renewal could re-sign without touching
    the tablets. `DEC-052` chose the other way -- no key, a new CA at renewal -- and the round-9b
    verifier found the flag silently ignored whenever a certificate already existed, so an operator
    who asked for a backup of the key believed they had one. Now it is refused every time, before
    anything is written, and the message says what the operator does instead.
    """

    situation = (
        "Chứng chỉ console đã có và được giữ nguyên; không có gì được ghi. "
        "(A console certificate already exists and is left as it is; nothing was written.) "
        if certificate_exists
        else "Không có gì được ghi. (Nothing was written.) "
    )
    return CertificateRefused(f"{flag}: {CUSTODY_RULE} {situation}{RENEWAL_STEP}")


class AuthorityStatus(NamedTuple):
    """What the CA certificate on disk actually limits itself to -- read, never assumed.

    A `NamedTuple` rather than a dataclass: the tests load this script by file path, and a
    dataclass needs its module registered in `sys.modules`.
    """

    #: `constrained`, `legacy` (no name constraints at all), `other-host`, `absent`, `unreadable`.
    kind: str
    permitted: tuple[str, ...] = ()

    @property
    def vouches_only_for_console(self) -> bool:
        return self.kind == "constrained"


def authority_status(path: _Path, host: str) -> AuthorityStatus:
    """Classify `.shop/ca/ca.crt`. A CA is called name-constrained only when it is, to `host`.

    Constrained means: a critical `nameConstraints` whose DNS permits are exactly the console host
    and which excludes every IP address, as `mint_console_authority` writes it. A CA made by an
    earlier version of this script (or by hand with `openssl req -x509`) carries no constraint, and
    every tablet that trusts it accepts a certificate it signs for any site.
    """

    if not path.exists():
        return AuthorityStatus("absent")
    try:
        authority = x509.load_pem_x509_certificate(path.read_bytes())
    except ValueError:
        return AuthorityStatus("unreadable")
    try:
        extension = authority.extensions.get_extension_for_class(x509.NameConstraints)
    except x509.ExtensionNotFound:
        return AuthorityStatus("legacy")
    permitted = tuple(
        name.value
        for name in extension.value.permitted_subtrees or ()
        if isinstance(name, x509.DNSName)
    )
    excluded = extension.value.excluded_subtrees or ()
    excludes_every_address = {
        str(name.value) for name in excluded if isinstance(name, x509.IPAddress)
    } >= {"0.0.0.0/0", "::/0"}
    if not extension.critical or not excludes_every_address or not permitted:
        return AuthorityStatus("legacy", permitted)
    if permitted != (host,):
        return AuthorityStatus("other-host", permitted)
    return AuthorityStatus("constrained", permitted)


def _authority_warning(status: AuthorityStatus, host: str) -> str | None:
    if status.kind == "legacy":
        return (
            f"{LEGACY_AUTHORITY}. .shop/ca/ca.crt không có nameConstraints: máy tính bảng tin "
            "nó sẽ tin mọi chứng chỉ nó ký, cho bất kỳ trang nào. (The CA in .shop/ca/ca.crt has "
            "no name constraints: a tablet that trusts it accepts a certificate it signs for any "
            "site.) " + RENEWAL_STEP
        )
    if status.kind == "other-host":
        return (
            f"CA chỉ chấp nhận {', '.join(status.permitted)}, không phải {host} — hãy tạo lại. "
            f"(The CA is constrained to {', '.join(status.permitted)}, not {host}.) " + RENEWAL_STEP
        )
    if status.kind == "unreadable":
        return ".shop/ca/ca.crt không đọc được (is not a PEM certificate). " + RENEWAL_STEP
    if status.kind == "absent":
        # Only reached with a console certificate on disk (a fresh run mints both). The tablets
        # need the CA that signed it, and that file is gone; its key was never kept (`DEC-052`),
        # so the only way back to a CA to install is a new pair.
        return (
            "Thiếu .shop/ca/ca.crt: không có CA nào để cài lên máy tính bảng, và không tạo lại "
            "được CA đã ký chứng chỉ console hiện tại. (.shop/ca/ca.crt is missing: there is no CA "
            "to install on the tablets, and the one that signed the console certificate cannot "
            "be made again.) " + RENEWAL_STEP
        )
    return None


def _leaf_warning(path: _Path, now: datetime) -> str | None:
    """The console certificate's expiry, once it is within `RENEW_BEFORE_DAYS`."""

    try:
        leaf = x509.load_pem_x509_certificate(path.read_bytes())
    except ValueError:
        return f"{path.name} không đọc được (is not a PEM certificate). " + RENEWAL_STEP
    expires = leaf.not_valid_after_utc
    remaining = expires - now
    shown = expires.astimezone(_SHOP_TIMEZONE).strftime("%d/%m/%Y")
    if remaining <= timedelta(0):
        return f"Chứng chỉ console đã hết hạn ngày {shown}. (expired) " + RENEWAL_STEP
    if remaining <= timedelta(days=RENEW_BEFORE_DAYS):
        return (
            f"Chứng chỉ console hết hạn ngày {shown} (còn {remaining.days} ngày). "
            f"(expires in {remaining.days} days) " + RENEWAL_STEP
        )
    return None


def retire_authority(*, now: datetime) -> _Path | None:
    """`--new-ca`: move the CA certificate and the console's certificate and key aside.

    Moved, not deleted: until every tablet trusts the new CA, putting the old pair back is the way
    to undo a renewal that went wrong. The two files are the console's own certificate and key --
    the CA's key is not among them, because under `DEC-052` there is none. A CA key an earlier
    version left behind is the one thing deleted outright: keeping it anywhere, even aside, is the
    custody `DEC-052` ends, and nothing here ever needs it again.
    """

    authority_directory = SECRET_DIRECTORY.parent / "ca"
    legacy_key = authority_directory / "ca.key"
    if legacy_key.exists():
        legacy_key.unlink()
    sources = [
        source
        for source in (
            authority_directory / "ca.crt",
            SECRET_DIRECTORY / "tls_certificate",
            SECRET_DIRECTORY / "tls_private_key",
        )
        if source.exists()
    ]
    if not sources:
        return None  # a first run with --new-ca: nothing to retire
    retired = SECRET_DIRECTORY.parent / "retired" / now.strftime("%Y%m%dT%H%M%SZ")
    retired.mkdir(parents=True, exist_ok=False)
    retired.parent.chmod(0o700)
    retired.chmod(0o700)
    for source in sources:
        source.rename(retired / source.name)
    return retired


def certificate(
    host: str,
    existing: list[str],
    *,
    export_ca_key: _Path | None = None,
    ca_key: _Path | None = None,
    new_ca: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """A private, name-constrained CA and one server certificate for the console.

    Two files rather than one: the CA certificate is what gets installed on every tablet; the
    console's certificate and key are what the console serves.

    **No CA private key is kept** (`DEC-052`, `PLATFORM-SECURITY-009` P4). It is generated in
    memory, signs the console certificate, and is dropped -- never written to this machine, and
    never exported: `--export-ca-key` and `--ca-key` are refused. When the console certificate nears
    expiry (every run says so from `RENEW_BEFORE_DAYS` out) `--new-ca` retires the old pair and
    mints a new CA, which is then installed and trusted on each tablet.

    Returns warnings for the operator: a legacy or wrongly constrained CA, a CA key an earlier
    version left behind, a console certificate near or past expiry.
    """

    moment = now or datetime.now(UTC)
    certificate_path = SECRET_DIRECTORY / "tls_certificate"
    # Refused before anything is read or written: the flag asks for a key this design never keeps.
    for flag, value in (("--export-ca-key", export_ca_key), ("--ca-key", ca_key)):
        if value is not None:
            raise refuse_kept_ca_key(flag, certificate_exists=certificate_path.exists())

    warnings: list[str] = []
    authority_directory = SECRET_DIRECTORY.parent / "ca"
    authority_directory.mkdir(parents=True, exist_ok=True)
    # `mkdir` takes the umask default, so this came out 0755 -- world-listable. `chmod` is applied
    # on every run because an existing directory keeps its mode.
    authority_directory.chmod(0o700)
    ca_certificate_path = authority_directory / "ca.crt"

    retired = retire_authority(now=moment) if new_ca else None
    if retired is not None:
        warnings.append(
            f"CA cũ và chứng chỉ console cũ đã chuyển sang {retired.relative_to(ROOT)}. Cài "
            ".shop/ca/ca.crt mới lên từng máy và bật tin cậy; khi mọi máy đã dùng được, xóa thư "
            "mục đó và gỡ CA cũ khỏi các máy. (The old CA and console certificate were moved "
            "aside; delete that folder once every tablet trusts the new CA.)"
        )

    legacy_key = authority_directory / "ca.key"
    if legacy_key.exists():
        warnings.append(
            f"{legacy_key.relative_to(ROOT)} là khóa riêng của CA mà phiên bản cũ của script để "
            "lại trên máy này. " + RENEWAL_STEP + " --new-ca cũng xóa khóa này. "
            "(A CA private key an earlier version left on this machine, for a CA with no name "
            "constraints; --new-ca deletes it.)"
        )

    if certificate_path.exists():
        existing.append("tls_certificate")
        # A missing ca.crt is not listed among the files that "already exist": the warning from
        # `_authority_warning` reports it and names `--new-ca`.
        warning = _authority_warning(authority_status(ca_certificate_path, host), host)
        if warning is not None:
            warnings.append(warning)
        warning = _leaf_warning(certificate_path, moment)
        if warning is not None:
            warnings.append(warning)
        return warnings

    if ca_certificate_path.exists():
        # The tablets trust this CA, but its key was never kept (`DEC-052`), so it cannot sign a
        # new console certificate. Minting a second CA silently would leave every tablet refusing
        # the console with no idea why; the operator asks for it instead.
        existing.append("ca/ca.crt")
        raise CertificateRefused(
            "Thiếu chứng chỉ console, và CA hiện có không ký thêm được: khóa của nó không được giữ "
            "lại (DEC-052). (The console certificate is missing and the existing CA cannot sign "
            "another: its key was not kept.) " + RENEWAL_STEP
        )

    authority, authority_key = mint_console_authority(host, now=moment)
    _write_secret(ca_certificate_path, authority.public_bytes(serialization.Encoding.PEM))
    ca_certificate_path.chmod(0o644)  # a certificate is public; it goes to every tablet
    certificate_pem, key_pem = issue_console_certificate(host, authority, authority_key, now=moment)
    _write_secret(SECRET_DIRECTORY / "tls_private_key", key_pem)
    _write_secret(certificate_path, certificate_pem)
    del authority_key
    return warnings


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
        "--new-ca",
        action="store_true",
        help=(
            "renewal (DEC-052): move the CA certificate and the console certificate and key to "
            ".shop/retired/<time>/, mint a new CA and certificate, then install the new ca.crt on "
            "every tablet. Run it when this script says the certificate is near expiry."
        ),
    )
    # Kept as flags so an old command line gets the reason instead of "unrecognized arguments".
    parser.add_argument("--export-ca-key", type=_Path, default=None, help="refused (DEC-052)")
    parser.add_argument("--ca-key", type=_Path, default=None, help="refused (DEC-052)")
    arguments = parser.parse_args()
    # Before a single secret is written: a run that asked to keep a CA key is refused whole.
    kept = (("--export-ca-key", arguments.export_ca_key), ("--ca-key", arguments.ca_key))
    for flag, value in kept:
        if value is not None:
            raise refuse_kept_ca_key(
                flag, certificate_exists=(SECRET_DIRECTORY / "tls_certificate").exists()
            )

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
    # `PLATFORM-RESIDUAL-009B` L4: the folder the API container writes its log file to
    # (compose.r1.yaml, `R1_API_LOG_DIRECTORY`), made here so Docker does not create it as root.
    # 0755: the host-side check reads it; the lines carry no secret (the logger redacts).
    api_logs = SECRET_DIRECTORY.parent / "logs" / "api"
    api_logs.mkdir(parents=True, exist_ok=True)
    api_logs.parent.chmod(0o755)
    api_logs.chmod(0o755)
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

    warnings = certificate(host, existing, new_ca=arguments.new_ca)

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
    # Read from the certificate on disk, not assumed from this version of the script: a CA an
    # earlier version made has no constraint at all, and this line used to call it constrained.
    status = authority_status(SECRET_DIRECTORY.parent / "ca" / "ca.crt", host)
    if status.kind == "absent":
        # Round-9b verifier: this used to print "install ca.crt" and "see the warning below" with
        # no file to install and no warning below. `certificate` warns for exactly this state.
        print("  There is no .shop/ca/ca.crt to install on the tablets: see the warning below.")
    else:
        print("  Install .shop/ca/ca.crt on every tablet, and switch trust ON for it.")
    if status.vouches_only_for_console:
        print(
            f"  It vouches for {host} only (name-constrained); its private key was not kept "
            "(DEC-052)."
        )
    elif status.kind != "absent":
        print(f"  It is NOT limited to {host}: see the warning below.")
    if not status.vouches_only_for_console and not warnings:
        # Every state that is not a constrained CA has a warning in `certificate`; should one ever
        # not, the operator is still told what to do rather than pointed at nothing.
        warnings = [f"{status.kind}: {RENEWAL_STEP}"]
    for warning in warnings:
        print(f"  WARNING: {warning}")
    print(f"  Point the shop's DNS at this machine for {host}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
