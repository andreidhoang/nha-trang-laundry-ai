from __future__ import annotations

from types import ModuleType

import pytest

from scripts import preflight_shop_till


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
