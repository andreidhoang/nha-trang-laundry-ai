"""`PLATFORM-SECURITY-009` P4: the shop CA vouches for the console and nothing else.

The CA certificate `bootstrap_shop_local.py` makes is installed *and trusted* on every tablet. It
was created with no name constraints and its private key was left at `.shop/ca/ca.key` on the till
that serves the console -- so whoever could read that file could mint a certificate every tablet
accepts for any site. Now the CA is name-constrained to the console host (critical), and its key
is generated in memory, signs the console certificate, and is dropped.

`PLATFORM-RESIDUAL-009B` L1 / `DEC-052`: the key is never kept -- not exported either. Round 9 let
the operator export it to a USB drive; `DEC-052` decided the other way (a new CA, re-trusted on each
tablet, when the console certificate nears expiry), and the verifier found `--export-ca-key`
silently ignored whenever a certificate already existed. Both export and re-sign flags are refused
with what to do instead, a CA without name constraints is reported as such and never described as
constrained, and `--new-ca` is the renewal.
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


def test_renewal_without_a_kept_key_refuses_and_says_how_to_proceed(
    bootstrap: ModuleType,
) -> None:
    """The console certificate is gone and the CA stays: nothing can sign (DEC-052). The refusal
    names the renewal -- it used to name `--ca-key`, the export path DEC-052 retired."""

    checkout = bootstrap.ROOT
    bootstrap.certificate(HOST, [])
    first_ca = (checkout / ".shop/ca/ca.crt").read_bytes()
    (checkout / ".shop/secrets/tls_certificate").unlink()
    with pytest.raises(SystemExit) as refused:
        bootstrap.certificate(HOST, [])
    message = str(refused.value)
    assert "--new-ca" in message and "DEC-052" in message and "--ca-key" not in message
    assert (checkout / ".shop/ca/ca.crt").read_bytes() == first_ca  # nothing re-minted silently


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
