"""`PLATFORM-SECURITY-009` P4: the shop CA vouches for the console and nothing else.

The CA certificate `bootstrap_shop_local.py` makes is installed *and trusted* on every tablet. It
was created with no name constraints and its private key was left at `.shop/ca/ca.key` on the till
that serves the console -- so whoever could read that file could mint a certificate every tablet
accepts for any site. Now the CA is name-constrained to the console host (critical), and its key
is generated in memory, signs the console certificate, and is dropped; it reaches disk only when
the operator exports it, passphrase-encrypted, to a path outside the checkout.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

ROOT = Path(__file__).resolve().parents[3]
HOST = "console.giatlasachcong.lan"


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"_ps009_evals_{name}", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


# --- P4: the shop CA ----------------------------------------------------------------------------


@pytest.fixture
def bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_script("bootstrap_shop_local")
    checkout = tmp_path / "laundry"
    (checkout / ".shop/secrets").mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", checkout)
    monkeypatch.setattr(module, "SECRET_DIRECTORY", checkout / ".shop/secrets")
    return module


def _private_keys_under(directory: Path) -> list[str]:
    return sorted(
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and b"PRIVATE KEY" in path.read_bytes()
    )


def test_the_shop_ca_is_name_constrained_and_its_key_is_not_left_on_the_host(
    bootstrap: ModuleType,
) -> None:
    checkout = bootstrap.ROOT
    warnings = bootstrap.certificate(HOST, [])
    assert warnings == []

    # The only private key on the serving host is the console's own TLS key.
    assert _private_keys_under(checkout) == [".shop/secrets/tls_private_key"]
    assert not (checkout / ".shop/ca/ca.key").exists()

    authority = x509.load_pem_x509_certificate((checkout / ".shop/ca/ca.crt").read_bytes())
    constraints = authority.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical is True
    assert constraints.value.permitted_subtrees == [x509.DNSName(HOST)]
    excluded = constraints.value.excluded_subtrees or []
    assert {type(item) for item in excluded} == {x509.IPAddress}
    basic = authority.extensions.get_extension_for_class(x509.BasicConstraints)
    assert (basic.critical, basic.value.ca, basic.value.path_length) == (True, True, 0)
    usage = authority.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign and usage.crl_sign and not usage.digital_signature

    console = x509.load_pem_x509_certificate(
        (checkout / ".shop/secrets/tls_certificate").read_bytes()
    )
    verifier = (
        PolicyBuilder()
        .store(Store([authority]))
        .time(datetime.now(UTC))
        .build_server_verifier(x509.DNSName(HOST))
    )
    verifier.verify(console, [])  # the console certificate chains to the constrained CA


def _leaf(host: str, authority: x509.Certificate, key: rsa.RSAPrivateKey) -> x509.Certificate:
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(authority.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )


@pytest.mark.parametrize(
    ("leaf_host", "trusted"),
    [
        (HOST, True),
        (f"cashier.{HOST}", True),  # a subdomain is inside a DNS permit, by X.509's rule
        ("www.vietcombank.com.vn", False),
        ("accounts.google.com", False),
        ("giatlasachcong.lan", False),  # the parent of the console name is outside it
    ],
)
def test_a_stolen_shop_ca_key_cannot_mint_a_certificate_for_anything_but_the_console(
    bootstrap: ModuleType, leaf_host: str, trusted: bool
) -> None:
    """What the constraint is for: even with the key, a tablet rejects any other name."""

    authority, key = bootstrap.mint_console_authority(HOST)
    verifier = (
        PolicyBuilder()
        .store(Store([authority]))
        .time(datetime.now(UTC))
        .build_server_verifier(x509.DNSName(leaf_host))
    )
    leaf = _leaf(leaf_host, authority, key)
    if trusted:
        verifier.verify(leaf, [])
    else:
        with pytest.raises(VerificationError):
            verifier.verify(leaf, [])


def test_an_exported_ca_key_is_encrypted_off_the_checkout_and_renews_without_retrusting(
    bootstrap: ModuleType, tmp_path: Path
) -> None:
    checkout = bootstrap.ROOT
    removable = tmp_path / "usb" / "laundry-ca.key"
    removable.parent.mkdir()
    phrase = b"correct horse battery staple"
    bootstrap.certificate(HOST, [], export_ca_key=removable, passphrase=lambda _: phrase)

    assert b"ENCRYPTED PRIVATE KEY" in removable.read_bytes()
    assert removable.stat().st_mode & 0o777 == 0o600
    assert _private_keys_under(checkout) == [".shop/secrets/tls_private_key"]
    first_ca = (checkout / ".shop/ca/ca.crt").read_bytes()

    # Renewal: the console certificate expires; the tablets keep trusting the same CA.
    (checkout / ".shop/secrets/tls_certificate").unlink()
    (checkout / ".shop/secrets/tls_private_key").unlink()
    existing: list[str] = []
    bootstrap.certificate(HOST, existing, ca_key=removable, passphrase=lambda _: phrase)
    assert (checkout / ".shop/ca/ca.crt").read_bytes() == first_ca
    renewed = x509.load_pem_x509_certificate(
        (checkout / ".shop/secrets/tls_certificate").read_bytes()
    )
    PolicyBuilder().store(Store([x509.load_pem_x509_certificate(first_ca)])).time(
        datetime.now(UTC)
    ).build_server_verifier(x509.DNSName(HOST)).verify(renewed, [])
    assert _private_keys_under(checkout) == [".shop/secrets/tls_private_key"]


def test_an_export_path_inside_the_checkout_is_refused_before_anything_is_written(
    bootstrap: ModuleType,
) -> None:
    checkout = bootstrap.ROOT
    with pytest.raises(SystemExit, match="inside this checkout"):
        bootstrap.certificate(
            HOST,
            [],
            export_ca_key=checkout / ".shop" / "ca-backup.key",
            passphrase=lambda _: b"x" * 20,
        )
    assert not (checkout / ".shop/ca/ca.crt").exists()
    assert _private_keys_under(checkout) == []


def test_renewal_without_the_ca_key_refuses_and_says_how_to_proceed(
    bootstrap: ModuleType,
) -> None:
    checkout = bootstrap.ROOT
    bootstrap.certificate(HOST, [])
    (checkout / ".shop/secrets/tls_certificate").unlink()
    with pytest.raises(SystemExit) as refused:
        bootstrap.certificate(HOST, [])
    message = str(refused.value)
    assert "--ca-key" in message and "move .shop/ca aside" in message


def test_a_ca_key_left_by_an_earlier_version_is_reported(bootstrap: ModuleType) -> None:
    checkout = bootstrap.ROOT
    (checkout / ".shop/ca").mkdir(parents=True)
    (checkout / ".shop/ca/ca.key").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    warnings = bootstrap.certificate(HOST, [])
    assert len(warnings) == 1 and "ca.key" in warnings[0] and "name constraints" in warnings[0]


def test_the_bootstrap_no_longer_shells_out_to_openssl_for_the_ca() -> None:
    """The CA key used to be written by `openssl req -keyout .shop/ca/ca.key`."""

    source = (ROOT / "scripts/bootstrap_shop_local.py").read_text(encoding="utf-8")
    assert "-keyout" not in source and '"openssl"' not in source
