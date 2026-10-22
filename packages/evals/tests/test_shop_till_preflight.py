from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from types import ModuleType

import pytest

from scripts import preflight_shop_till

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_SECRET_FILES = tuple(
    re.findall(
        r"^\s+file:\s+\./(.+?)\s*$",
        (REPOSITORY_ROOT / "compose.shop-local.yaml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
)


class _FakeSocket:
    def __init__(self, connect_result: int) -> None:
        self.connect_result = connect_result

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect_ex(self, _address: tuple[str, int]) -> int:
        return self.connect_result


@pytest.fixture
def preflight() -> ModuleType:
    return preflight_shop_till


def test_console_port_passes_when_free(
    monkeypatch: pytest.MonkeyPatch,
    preflight: ModuleType,
) -> None:
    monkeypatch.setattr(
        preflight.socket,
        "socket",
        lambda *_args: _FakeSocket(connect_result=1),
    )

    result = preflight.check_port_free()

    assert result.ok is True
    assert result.detail == "free"


def test_console_port_passes_when_owned_by_shop_tls(
    monkeypatch: pytest.MonkeyPatch,
    preflight: ModuleType,
) -> None:
    monkeypatch.setattr(
        preflight.socket,
        "socket",
        lambda *_args: _FakeSocket(connect_result=0),
    )
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *_command: (0, "nha-trang-laundry-shop/tls\n"),
    )

    result = preflight.check_port_free()

    assert result.ok is True
    assert result.detail == "nha-trang-laundry-shop/tls"


def test_console_port_blocks_when_owned_by_another_stack(
    monkeypatch: pytest.MonkeyPatch,
    preflight: ModuleType,
) -> None:
    monkeypatch.setattr(
        preflight.socket,
        "socket",
        lambda *_args: _FakeSocket(connect_result=0),
    )
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *_command: (0, "nha-trang-laundry-private-staging/tls\n"),
    )

    result = preflight.check_port_free()

    assert result.ok is False
    assert result.blocking is True
    assert "nha-trang-laundry-private-staging/tls" in result.detail


def test_console_port_fails_closed_when_owner_cannot_be_identified(
    monkeypatch: pytest.MonkeyPatch,
    preflight: ModuleType,
) -> None:
    monkeypatch.setattr(
        preflight.socket,
        "socket",
        lambda *_args: _FakeSocket(connect_result=0),
    )
    monkeypatch.setattr(preflight, "_run", lambda *_command: (1, ""))

    result = preflight.check_port_free()

    assert result.ok is False
    assert "unknown process" in result.detail


@pytest.mark.parametrize(
    ("returncode", "output"),
    [(1, "permission denied"), (1, "command timed out"), (1, ""), (127, "find unavailable")],
)
def test_dataless_scan_failure_cannot_report_no_evicted_files(
    monkeypatch: pytest.MonkeyPatch, returncode: int, output: str
) -> None:
    monkeypatch.setattr(preflight_shop_till, "_run", lambda *_args: (returncode, output))

    result = preflight_shop_till.check_dataless_files()

    assert result.ok is False
    assert result.blocking is True
    assert "unknown" in result.detail


@pytest.mark.parametrize(("output", "ok"), [("", True), ("/checkout/deploy/config\n", False)])
def test_successful_dataless_scan_distinguishes_empty_from_evicted(
    monkeypatch: pytest.MonkeyPatch, output: str, ok: bool
) -> None:
    monkeypatch.setattr(preflight_shop_till, "_run", lambda *_args: (0, output))

    assert preflight_shop_till.check_dataless_files().ok is ok


@pytest.fixture
def bootstrapped_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The Compose file is the independent inventory; adding a required mount must make a missing
    # file fail here without teaching the test the preflight's own hand-written list.
    assert COMPOSE_SECRET_FILES
    for relative in (*COMPOSE_SECRET_FILES, ".shop/ca/ca.crt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(preflight_shop_till, "ROOT", tmp_path)
    return tmp_path


@pytest.mark.parametrize("missing", (*COMPOSE_SECRET_FILES, ".shop/ca/ca.crt"))
def test_every_compose_secret_file_and_ca_is_required(
    bootstrapped_directory: Path, missing: str
) -> None:
    (bootstrapped_directory / missing).unlink()

    result = preflight_shop_till.check_secret_material()

    assert result.ok is False
    assert missing in result.detail


def test_bootstrap_presence_check_does_not_read_secret_values(
    bootstrapped_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_open(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the presence check must not read credential contents")

    monkeypatch.setattr(Path, "open", forbidden_open)

    assert preflight_shop_till.check_secret_material().ok is True
    assert preflight_shop_till.check_archive_credential().ok is True


def test_directory_cannot_stand_in_for_required_secret_file(bootstrapped_directory: Path) -> None:
    path = bootstrapped_directory / ".shop/secrets/backup_repository_credential"
    path.unlink()
    path.mkdir()

    assert preflight_shop_till.check_secret_material().ok is False
    assert preflight_shop_till.check_archive_credential().ok is False


@pytest.mark.parametrize("symlink", [False, True])
def test_explicit_icloud_drive_is_refused_without_desktop_sync_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symlink: bool
) -> None:
    home = tmp_path / "home"
    cloud = home / "Library/Mobile Documents/com~apple~CloudDocs/laundry"
    cloud.mkdir(parents=True)
    checkout = cloud
    if symlink:
        checkout = home / "laundry"
        checkout.symlink_to(cloud, target_is_directory=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(preflight_shop_till, "ROOT", checkout)

    result = preflight_shop_till.check_checkout_location()

    assert result.ok is False
    assert "inside iCloud Drive" in result.detail


@pytest.mark.parametrize("symlink", [False, True])
def test_desktop_sync_check_applies_to_resolved_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symlink: bool
) -> None:
    home = tmp_path / "home"
    desktop = home / "Desktop/laundry"
    desktop.mkdir(parents=True)
    accounts = home / "Library/Preferences/MobileMeAccounts.plist"
    accounts.parent.mkdir(parents=True)
    accounts.write_bytes(plistlib.dumps({"services": ["CLOUDDESKTOP"]}))
    checkout = desktop
    if symlink:
        checkout = home / "laundry"
        checkout.symlink_to(desktop, target_is_directory=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(preflight_shop_till, "ROOT", checkout)

    assert preflight_shop_till.check_checkout_location().ok is False


def test_local_checkout_is_allowed_even_when_desktop_sync_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    checkout = home / "laundry"
    checkout.mkdir(parents=True)
    accounts = home / "Library/Preferences/MobileMeAccounts.plist"
    accounts.parent.mkdir(parents=True)
    accounts.write_bytes(plistlib.dumps({"services": ["CLOUDDESKTOP"]}))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(preflight_shop_till, "ROOT", checkout)

    assert preflight_shop_till.check_checkout_location().ok is True


def test_unresolvable_checkout_cannot_report_outside_icloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "laundry"
    checkout.symlink_to(checkout)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(preflight_shop_till, "ROOT", checkout)

    assert preflight_shop_till.check_checkout_location().ok is False


@pytest.fixture
def archive_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    checkout, archive = tmp_path / "checkout", tmp_path / "archive"
    checkout.mkdir()
    archive.mkdir()
    monkeypatch.setattr(preflight_shop_till, "ROOT", checkout)
    monkeypatch.setenv("R1_LOCAL_ARCHIVE_PATH", str(archive))
    return checkout, archive


def test_same_filesystem_archive_is_a_blocker(archive_paths: tuple[Path, Path]) -> None:
    result = preflight_shop_till.check_archive_path()

    assert result.ok is False
    assert result.blocking is True
    assert "same filesystem" in result.detail


def test_archive_filesystem_probe_failure_cannot_report_ready(
    archive_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _archive = archive_paths
    original_stat = Path.stat

    def inaccessible_checkout(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == checkout:
            raise PermissionError("synthetic unavailable filesystem metadata")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", inaccessible_checkout)

    result = preflight_shop_till.check_archive_path()

    assert result.ok is False
    assert "could not be checked" in result.detail


def test_unreadable_archive_metadata_is_a_refusal_not_a_crash(
    archive_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _checkout, archive = archive_paths
    original_stat = Path.stat

    def inaccessible_archive(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == archive:
            raise PermissionError("synthetic unavailable archive")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", inaccessible_archive)

    result = preflight_shop_till.check_archive_path()

    assert result.ok is False
    assert "could not be checked" in result.detail


def test_distinct_archive_filesystem_does_not_claim_physical_failure_domain_verified(
    archive_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _checkout, archive = archive_paths
    original_stat = Path.stat

    def separate_archive(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        value = original_stat(path, follow_symlinks=follow_symlinks)
        if path == archive:
            fields = list(value)
            fields[2] += 1
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "stat", separate_archive)

    result = preflight_shop_till.check_archive_path()

    assert result.ok is True
    assert "confirm the drive is physically separate" in result.detail
