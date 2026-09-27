"""`DAILY-SUMMARY-001` (`DEC-039`) over HTTP: the owner's evening summary route.

The repository tests (`packages/db/tests/test_daily_summary_repository.py`) prove the lines
against the report's seeded shop and the live sources. These prove what the wire carries -- the
`{template_version, date, lines, omitted}` shape plus the copyable `text` -- who may read it (the
report's four roles, with the report's one opaque 403 and no sentence in the body), that `?date=`
defaults to the shop's today and refuses a day after it with the report's 422, and that the route
makes no network call of any kind while it writes the text: no model, no provider.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.daily_summary import DailySummaryService
from nha_trang_laundry_api.main import app, current_principal, get_daily_summary_service
from nha_trang_laundry_db.daily_summary import daily_summary_template_version
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.reports import report_query_version, shop_today
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_reports import DAY, _person, _seeded_shop, _store

CSRF = "y" * 40


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    settings = AuthSettings(database_url=database_url)
    app.dependency_overrides[get_daily_summary_service] = lambda: DailySummaryService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _path(store_id: Any, day: Any = None) -> str:
    base = f"/internal/v1/stores/{store_id}/reports/daily-summary"
    return base if day is None else f"{base}?date={day}"


def test_the_summary_of_a_closed_day_on_the_wire(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = _seeded_shop(connection)
    _as(shop.owner)

    response = client.get(_path(shop.store_id, DAY))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "store_id",
        "date",
        "template_version",
        "evaluated_at",
        "so_far",
        "lines",
        "omitted",
        "text",
        "sources",
    }
    assert body["date"] == DAY.isoformat()
    assert body["template_version"] == daily_summary_template_version().label
    assert body["so_far"] is False
    assert [line["key"] for line in body["lines"]] == [
        "HEADER",
        "ORDERS",
        "MONEY",
        "FINISHED_ON_TIME",
        "COMPLAINTS_NEW",
        "SPENDING",
    ]
    for line in body["lines"]:
        assert set(line) == {"key", "text", "figures"}
        assert line["text"] and line["text"].endswith(".")
    assert body["lines"][1] == {
        "key": "ORDERS",
        "text": "Nhận 6 đơn mới. Hoàn tất 1 đơn. Huỷ 1 đơn.",
        "figures": {"orders_created": 6, "orders_completed": 1, "orders_cancelled": 1},
    }
    assert {item["key"]: item["reason"] for item in body["omitted"]} == {
        "LATE_AGAINST_PROMISE": "LIVE_ONLY_TODAY",
        "WITHOUT_PROMISE": "LIVE_ONLY_TODAY",
        # Round 7 wave 2 integration: the waiting list is live (today only), and this shop has
        # opened no customer account.
        "WAITING_PICKUP": "LIVE_ONLY_TODAY",
        "COMPLAINTS_OPEN": "LIVE_ONLY_TODAY",
        "ACCOUNTS_DUE": "NO_ACCOUNTS",
    }
    for item in body["omitted"]:
        assert set(item) == {"key", "reason", "source", "note"} and item["note"]
    # What Sao chép copies is the lines, one per row, byte for byte.
    assert body["text"] == "\n".join(line["text"] for line in body["lines"])
    assert body["sources"] == [
        {"key": "report", "query_version": report_query_version(STANDARD_WASH_SLA).label}
    ]


def test_date_defaults_to_the_shops_today_and_a_later_day_is_the_reports_422(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    connection.commit()
    _as(owner)
    today = shop_today(datetime.now(UTC))

    default = client.get(_path(store_id))
    assert default.status_code == 200, default.text
    assert default.json()["date"] == today.isoformat() and default.json()["so_far"] is True
    assert default.json()["lines"][0]["text"].startswith("Tóm tắt ")

    later = client.get(_path(store_id, today + timedelta(days=1)))
    assert later.status_code == 422
    assert later.json()["detail"] == {
        "outcome": "NOT_SUPPORTED",
        "reason_code": "REPORT_WINDOW_IN_FUTURE",
    }
    assert client.get(_path(store_id, "2026-13-01")).status_code == 422


def test_the_counter_and_outsiders_are_refused_with_no_sentence_and_the_four_readers_read(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id = _store(connection)
    connection.commit()
    today = shop_today(datetime.now(UTC))
    bodies = set()
    refused_principals = [
        _person(connection, store_id, frozenset({StaffRole.OPERATOR})),
        _person(connection, store_id, frozenset({StaffRole.DRIVER})),
        _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}), mfa=False),
        _person(connection, _store(connection), frozenset({StaffRole.OWNER_ADMIN})),
    ]
    connection.commit()
    for principal in refused_principals:
        _as(principal)
        refused = client.get(_path(store_id, today))
        assert refused.status_code == 403
        assert "lines" not in refused.text and "Tóm tắt" not in refused.text
        bodies.add(refused.text)
    _as(refused_principals[-1])
    bodies.add(client.get(_path(uuid4(), today)).text)
    assert len(bodies) == 1
    for role in (
        StaffRole.OWNER_ADMIN,
        StaffRole.OPS_APPROVER,
        StaffRole.ACCOUNTANT,
        StaffRole.AUDITOR,
    ):
        _as(_person(connection, store_id, frozenset({role})))
        connection.commit()
        allowed = client.get(_path(store_id, today))
        assert allowed.status_code == 200, (role, allowed.text)


def test_the_route_makes_no_network_call_while_it_writes_the_text(
    connection: psycopg.Connection[Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No model, no provider, no network: every Python-level socket connect is refused during the
    request, and the summary is still written. (PostgreSQL is reached through libpq, below Python's
    socket module, and is the only thing the route talks to.)"""

    shop = _seeded_shop(connection)
    _as(shop.owner)
    attempts: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append(args)
        raise OSError("DAILY-SUMMARY-001: no network while writing the summary")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    for day in (DAY, shop_today(datetime.now(UTC))):
        response = client.get(_path(shop.store_id, day))
        assert response.status_code == 200, response.text
        assert response.json()["lines"]
    assert attempts == []
