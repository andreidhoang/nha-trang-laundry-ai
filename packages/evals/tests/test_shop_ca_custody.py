"""`PLATFORM-RESIDUAL-009B` L1 / `DEC-052`: the shop CA's key is never kept, and the script says so.

Round 9 (`PLATFORM-SECURITY-009` P4) stopped writing the CA key to the till, but kept a way to
export it to a USB drive and re-sign with it later. `DEC-052` chose the other way: no CA key is kept
after it signs; near the console certificate's expiry a new CA is minted and re-trusted on each
tablet. The round-9b verifier also found:

- `--export-ca-key` silently ignored whenever a console certificate already existed -- the
  operator asked for a backup of the key, got exit 0, and believed they had one;
- a CA made by an earlier version (no name constraints at all) described by the closing line as
  "name-constrained", because the line was printed, not read from the certificate.

These tests pin the replacement: both key flags refused every time with what to do instead and
nothing written; the CA on disk classified from its own extensions; the expiry warning; `--new-ca`.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from types import ModuleType

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from cryptography.x509.verification import PolicyBuilder, Store

ROOT = Path(__file__).resolve().parents[3]
HOST = "console.giatlasachcong.lan"
RECIPIENT = "age1" + "q" * 58


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"_l1_evals_{name}", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_script("bootstrap_shop_local")
    checkout = tmp_path / "laundry"
    (checkout / ".shop/secrets").mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", checkout)
    monkeypatch.setattr(module, "SECRET_DIRECTORY", checkout / ".shop/secrets")
    return module


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _private_keys_under(directory: Path) -> list[str]:
    return sorted(
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and b"PRIVATE KEY" in path.read_bytes()
    )


# --- the two key flags: refused in every state, nothing written ---------------------------------


def _state_fresh(module: ModuleType) -> None:
    return None


def _state_certificate(module: ModuleType) -> None:
    module.certificate(HOST, [])


def _state_ca_without_certificate(module: ModuleType) -> None:
    module.certificate(HOST, [])
    (module.SECRET_DIRECTORY / "tls_certificate").unlink()
    (module.SECRET_DIRECTORY / "tls_private_key").unlink()


STATES = {
    "fresh": _state_fresh,
    "certificate exists": _state_certificate,
    "CA kept, certificate gone": _state_ca_without_certificate,
}


@pytest.mark.parametrize("state", list(STATES))
@pytest.mark.parametrize("flag", ["export_ca_key", "ca_key"])
def test_a_flag_that_would_keep_the_ca_key_is_refused_in_every_state(
    bootstrap: ModuleType, tmp_path: Path, state: str, flag: str
) -> None:
    """The exact combination the verifier found -- `--export-ca-key` with an existing certificate,
    silently ignored -- and its neighbours. Refused before anything is read or written, and the
    message says what the operator does instead."""

    STATES[state](bootstrap)
    checkout = bootstrap.ROOT
    before = _snapshot(checkout)
    usb = tmp_path / "usb" / "laundry-ca.key"
    usb.parent.mkdir()
    if flag == "ca_key":
        usb.write_bytes(b"-----BEGIN PRIVATE KEY-----\n")

    with pytest.raises(SystemExit) as refused:
        bootstrap.certificate(HOST, [], **{flag: usb})
    message = str(refused.value)

    assert "DEC-052" in message and "--new-ca" in message, message
    if state == "certificate exists":
        assert "giữ nguyên" in message and "nothing was written" in message, message
    assert _snapshot(checkout) == before
    assert (usb.exists() and flag == "ca_key") or not usb.exists()


@pytest.mark.parametrize("flag", ["--export-ca-key", "--ca-key"])
@pytest.mark.parametrize("with_certificate", [False, True])
def test_the_command_line_refuses_a_kept_key_before_writing_any_secret(
    bootstrap: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    with_certificate: bool,
) -> None:
    """The flag is still parsed -- an old command line gets the reason, not "unrecognized
    arguments" -- and the run stops before the database passwords are generated."""

    if with_certificate:
        bootstrap.certificate(HOST, [])
    before = _snapshot(bootstrap.ROOT)
    usb = tmp_path / "usb.key"
    monkeypatch.setattr(
        sys,
        "argv",
        ["bootstrap_shop_local.py", "--backup-recipient", RECIPIENT, flag, str(usb)],
    )
    with pytest.raises(SystemExit) as refused:
        bootstrap.main()
    assert "DEC-052" in str(refused.value)
    assert _snapshot(bootstrap.ROOT) == before
    assert not usb.exists()


# --- what the CA on disk actually is ------------------------------------------------------------


def _authority(
    constraints: x509.NameConstraints | None, *, critical: bool = True
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """A CA as an earlier version (or `openssl req -x509` by hand) made it, or a variant."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Giat La Sach Cong Internal CA")])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    )
    if constraints is not None:
        builder = builder.add_extension(constraints, critical=critical)
    return builder.sign(key, hashes.SHA256()), key


def _install(module: ModuleType, authority: x509.Certificate, key: rsa.RSAPrivateKey) -> None:
    """Put a CA and a console certificate it signed where the till keeps them."""

    directory = module.SECRET_DIRECTORY.parent / "ca"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ca.crt").write_bytes(authority.public_bytes(serialization.Encoding.PEM))
    certificate_pem, key_pem = module.issue_console_certificate(HOST, authority, key)
    (module.SECRET_DIRECTORY / "tls_certificate").write_bytes(certificate_pem)
    (module.SECRET_DIRECTORY / "tls_private_key").write_bytes(key_pem)


_EVERY_ADDRESS = [x509.IPAddress(IPv4Network("0.0.0.0/0")), x509.IPAddress(IPv6Network("::/0"))]

AUTHORITIES: dict[str, tuple[x509.NameConstraints | None, bool, str | None]] = {
    # name: (constraints, critical, the warning's distinguishing words or None)
    "legacy, no constraints": (None, True, "CA cũ không giới hạn tên miền — hãy tạo lại"),
    "constrained, not critical": (
        x509.NameConstraints([x509.DNSName(HOST)], _EVERY_ADDRESS),
        False,
        "CA cũ không giới hạn tên miền — hãy tạo lại",
    ),
    "DNS only, addresses open": (
        x509.NameConstraints([x509.DNSName(HOST)], None),
        True,
        "CA cũ không giới hạn tên miền — hãy tạo lại",
    ),
    "constrained to another host": (
        x509.NameConstraints([x509.DNSName("console.other.lan")], _EVERY_ADDRESS),
        True,
        "không phải console.giatlasachcong.lan — hãy tạo lại",
    ),
    "constrained to the console": (
        x509.NameConstraints([x509.DNSName(HOST)], _EVERY_ADDRESS),
        True,
        None,
    ),
}


@pytest.mark.parametrize("kind", list(AUTHORITIES))
def test_an_existing_ca_is_reported_as_what_it_is(
    bootstrap: ModuleType,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A re-run with a CA already on disk: warned about unless it is constrained to the console,
    and the closing line calls it name-constrained only when it is."""

    constraints, critical, expected = AUTHORITIES[kind]
    authority, key = _authority(constraints, critical=critical)
    _install(bootstrap, authority, key)

    warnings = bootstrap.certificate(HOST, [])
    if expected is None:
        assert warnings == []
    else:
        assert len(warnings) == 1 and expected in warnings[0], warnings
        assert "--new-ca" in warnings[0]

    monkeypatch.setattr(sys, "argv", ["bootstrap_shop_local.py", "--backup-recipient", RECIPIENT])
    assert bootstrap.main() == 0
    printed = capsys.readouterr().out
    if expected is None:
        assert "name-constrained" in printed and "WARNING" not in printed
    else:
        assert "name-constrained" not in printed, printed
        assert f"WARNING: {expected}" in printed or expected in printed


def test_a_ca_key_left_beside_a_legacy_ca_is_reported_with_the_ca(bootstrap: ModuleType) -> None:
    authority, key = _authority(None)
    _install(bootstrap, authority, key)
    (bootstrap.SECRET_DIRECTORY.parent / "ca" / "ca.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    warnings = bootstrap.certificate(HOST, [])
    assert len(warnings) == 2
    assert any("ca.key" in warning for warning in warnings)
    assert any("CA cũ không giới hạn tên miền — hãy tạo lại" in warning for warning in warnings)


# --- renewal ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [
        (61, None),
        (60, "còn 60 ngày"),
        (1, "còn 1 ngày"),
        (0, "đã hết hạn"),
        (-30, "đã hết hạn"),
    ],
)
def test_every_run_says_when_the_console_certificate_needs_renewing(
    bootstrap: ModuleType, days_left: int, expected: str | None
) -> None:
    minted = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    bootstrap.certificate(HOST, [], now=minted)
    leaf = x509.load_pem_x509_certificate(
        (bootstrap.SECRET_DIRECTORY / "tls_certificate").read_bytes()
    )
    later = leaf.not_valid_after_utc - timedelta(days=days_left)
    warnings = bootstrap.certificate(HOST, [], now=later)
    if expected is None:
        assert warnings == []
    else:
        assert len(warnings) == 1 and expected in warnings[0] and "--new-ca" in warnings[0]


def test_new_ca_retires_the_old_pair_and_mints_a_constrained_one_with_no_key_kept(
    bootstrap: ModuleType,
) -> None:
    checkout = bootstrap.ROOT
    # Start from the worst case: a legacy CA whose key an earlier version left on the till.
    old_authority, old_key = _authority(None)
    _install(bootstrap, old_authority, old_key)
    legacy_key = checkout / ".shop/ca/ca.key"
    legacy_key.write_bytes(
        old_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    old_certificate = (checkout / ".shop/secrets/tls_certificate").read_bytes()
    moment = datetime(2028, 12, 1, 2, 0, tzinfo=UTC)

    warnings = bootstrap.certificate(HOST, [], new_ca=True, now=moment)

    retired = checkout / ".shop/retired/20281201T020000Z"
    assert (retired / "ca.crt").read_bytes() == old_authority.public_bytes(
        serialization.Encoding.PEM
    )
    assert (retired / "tls_certificate").read_bytes() == old_certificate
    assert retired.stat().st_mode & 0o777 == 0o700
    assert not legacy_key.exists(), "DEC-052: the old CA key is deleted, not kept aside"
    # The only private keys anywhere are console keys: the new one, and the old one kept for undo.
    assert _private_keys_under(checkout) == [
        ".shop/retired/20281201T020000Z/tls_private_key",
        ".shop/secrets/tls_private_key",
    ]
    assert any("retired" in warning for warning in warnings)

    status = bootstrap.authority_status(checkout / ".shop/ca/ca.crt", HOST)
    assert status.vouches_only_for_console
    authority = x509.load_pem_x509_certificate((checkout / ".shop/ca/ca.crt").read_bytes())
    console = x509.load_pem_x509_certificate(
        (checkout / ".shop/secrets/tls_certificate").read_bytes()
    )
    PolicyBuilder().store(Store([authority])).time(
        moment + timedelta(days=1)
    ).build_server_verifier(x509.DNSName(HOST)).verify(console, [])
    # A CA whose key is gone signs nothing more: it lives as long as its one certificate, plus the
    # month's margin, rather than ten years on every tablet.
    assert authority.not_valid_after_utc - console.not_valid_after_utc == timedelta(days=30)


def test_the_runbooks_describe_dec_052_custody() -> None:
    pilot = (ROOT / "docs/runbooks/shop-pilot.md").read_text("utf-8")
    section = pilot[pilot.index("## 2. Prepare the machine") : pilot.index("## 3.")]
    assert "DEC-052" in section and "--new-ca" in section
    assert "--export-ca-key" not in section.replace(
        "`--export-ca-key` and `--ca-key` are refused", ""
    )
    today = (ROOT / "docs/runbooks/deploy-today.md").read_text("utf-8")
    section = today[today.index("## 3. The certificate") : today.index("## 4.")]
    assert "DEC-052" in section
    assert "keep it on a" not in section
    assert "is then deleted" in section, "DEC-052: the CA key is not kept, not even offline"
