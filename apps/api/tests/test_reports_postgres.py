"""`REPORT-DASHBOARD-001` over HTTP: who may read the owner's numbers, and what the wire carries.

The repository tests (`packages/db/tests/test_reports.py`) prove every figure against a seeded shop.
These prove the two routes serve exactly those figures in the `FR-RPT-005` shape, refuse the roles
§3.5 refuses with the one opaque 403 every other refusal uses (and without a figure in the body),
and refuse a window the report does not answer with a 422 that names why.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.assistant import SLA_POLICY, sla_policy_notice_vi
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, current_principal, get_ops_board_service
from nha_trang_laundry_api.ops_board import OpsBoardService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.reports import report_query_version, shop_today

# The seeded shop lives beside the repository tests that own it, reached by path exactly as
# `quote_test_data` is: a second fixture built some other way could pass against a shape the first
# one never checked.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_reports import DAY, NEXT, _person, _seeded_shop, _store

CSRF = "y" * 40
KPI_FIELDS = {
    "key",
    "numerator",
    "denominator",
    "denominator_key",
    "unit",
    "window",
    "data_quality",
    "query_version",
    "direction",
    "entries",
    "amount_vnd",
    "by_kind",
    # PROMISE-001: how many of the on-time denominator the stated rule judged (null elsewhere).
    "rule_assumed",
}


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
    app.dependency_overrides[get_ops_board_service] = lambda: OpsBoardService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _path(store_id: Any, kind: str, start: Any, end: Any) -> str:
    return f"/internal/v1/stores/{store_id}/reports/{kind}?from={start}&to={end}"


def test_the_summary_serves_the_seeded_figures_in_the_fr_rpt_005_shape(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = _seeded_shop(connection)
    _as(shop.owner)

    response = client.get(_path(shop.store_id, "summary", DAY, NEXT))

    assert response.status_code == 200, response.text
    body = response.json()
    version = report_query_version(SLA_POLICY).label
    assert body["query_version"] == version
    assert body["window"] == {
        "from_date": DAY.isoformat(),
        "to_date": NEXT.isoformat(),
        "days": 2,
        "business_timezone": "Asia/Ho_Chi_Minh",
        "ends_today": False,
    }
    kpis = {kpi["key"]: kpi for kpi in body["kpis"]}
    for kpi in kpis.values():
        assert set(kpi) == KPI_FIELDS
        assert kpi["query_version"] == version
        assert kpi["window"] == {"from_date": DAY.isoformat(), "to_date": NEXT.isoformat()}
        # No rate on the wire: every figure is a non-negative integer pair.
        assert isinstance(kpi["numerator"], int) and kpi["numerator"] >= 0
        assert kpi["denominator"] is None or isinstance(kpi["denominator"], int)
    assert {key: (kpi["numerator"], kpi["denominator"]) for key, kpi in kpis.items()} == {
        "ORDERS_CREATED": (7, None),
        "ORDERS_COMPLETED": (1, None),
        "ORDERS_CANCELLED": (2, None),
        "ON_TIME_INTERNAL": (2, 3),
        "REWASH": (2, 4),
        "COMPLAINTS": (3, 1),
        "MONEY_COLLECTED": (220_000, None),
        "MONEY_REFUNDED": (110_000, None),
        "MONEY_NET": (110_000, None),
        "REMEDIES_EXECUTED": (2, None),
    }
    assert kpis["ON_TIME_INTERNAL"]["data_quality"] == "RULE_ASSUMED"
    # No order in the seeded shop has a promise, so the stated rule judged every one of them.
    on_time = kpis["ON_TIME_INTERNAL"]
    assert on_time["rule_assumed"] == on_time["denominator"] and on_time["rule_assumed"] > 0
    assert kpis["MONEY_NET"]["direction"] == "IN"
    assert kpis["REMEDIES_EXECUTED"]["by_kind"][0] == {
        "kind": "FREE_REWASH",
        "count": 1,
        "amount_vnd": None,
    }
    # The rule is named in the board's own words, and margin is refused with its reason.
    assert body["sla_rule"]["notice_vi"] == sla_policy_notice_vi(SLA_POLICY)
    assert body["sla_rule"]["policy_id"] == SLA_POLICY.policy_id
    assert body["margin"] == {
        "shown": False,
        "reason_code": "COST_NOT_CAPTURED",
        "blocked_by": "SHOP-INSTRUMENT-001",
    }


def test_the_daily_route_lists_every_day_with_the_same_figures(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = _seeded_shop(connection)
    _as(shop.owner)
    start = DAY - timedelta(days=1)

    body = client.get(_path(shop.store_id, "daily", start, NEXT)).json()

    assert [day["date"] for day in body["days"]] == [
        start.isoformat(),
        DAY.isoformat(),
        NEXT.isoformat(),
    ]
    created = [
        next(kpi["numerator"] for kpi in day["kpis"] if kpi["key"] == "ORDERS_CREATED")
        for day in body["days"]
    ]
    assert created == [1, 6, 1]
    refunds = [
        next(kpi for kpi in day["kpis"] if kpi["key"] == "MONEY_NET") for day in body["days"]
    ]
    assert [(kpi["numerator"], kpi["direction"]) for kpi in refunds] == [
        (0, "IN"),
        (220_000, "IN"),
        (110_000, "OUT"),
    ]
    assert body["days"][1]["kpis"][0]["window"] == {
        "from_date": DAY.isoformat(),
        "to_date": DAY.isoformat(),
    }


def test_an_operator_is_refused_without_a_figure_in_the_body_and_the_four_roles_read(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id = _store(connection)
    connection.commit()
    today = shop_today(datetime.now(UTC))
    for role in (StaffRole.OPERATOR, StaffRole.DRIVER):
        _as(_person(connection, store_id, frozenset({role})))
        for kind in ("summary", "daily"):
            refused = client.get(_path(store_id, kind, today, today))
            assert refused.status_code == 403, (role, kind)
            assert "numerator" not in refused.text and "kpis" not in refused.text
    for role in (
        StaffRole.OWNER_ADMIN,
        StaffRole.OPS_APPROVER,
        StaffRole.ACCOUNTANT,
        StaffRole.AUDITOR,
    ):
        _as(_person(connection, store_id, frozenset({role})))
        for kind in ("summary", "daily"):
            allowed = client.get(_path(store_id, kind, today, today))
            assert allowed.status_code == 200, (role, kind, allowed.text)
            assert allowed.json()["window"]["ends_today"] is True


def test_membership_and_mfa_are_required_and_refused_like_every_other_store_read(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id = _store(connection)
    today = shop_today(datetime.now(UTC))
    outsider = _person(connection, _store(connection), frozenset({StaffRole.OWNER_ADMIN}))
    unverified = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}), mfa=False)
    connection.commit()
    bodies = set()
    for principal in (outsider, unverified):
        _as(principal)
        refused = client.get(_path(store_id, "summary", today, today))
        assert refused.status_code == 403
        bodies.add(refused.text)
    _as(outsider)
    bodies.add(client.get(_path(uuid4(), "summary", today, today)).text)
    # One body for a wrong store, a missing MFA and a store that does not exist.
    assert len(bodies) == 1


def test_a_window_the_report_does_not_answer_is_a_422_that_names_why(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    connection.commit()
    _as(owner)
    today = shop_today(datetime.now(UTC))

    widest = client.get(_path(store_id, "summary", today - timedelta(days=91), today))
    assert widest.status_code == 200 and widest.json()["window"]["days"] == 92
    cases = {
        "REPORT_WINDOW_TOO_LONG": (today - timedelta(days=92), today),
        "REPORT_WINDOW_REVERSED": (today, today - timedelta(days=1)),
        "REPORT_WINDOW_IN_FUTURE": (today, today + timedelta(days=1)),
    }
    for reason, (start, end) in cases.items():
        for kind in ("summary", "daily"):
            refused = client.get(_path(store_id, kind, start, end))
            assert refused.status_code == 422, (reason, kind)
            assert refused.json()["detail"] == {"outcome": "NOT_SUPPORTED", "reason_code": reason}
    # Both dates are required and must be dates.
    assert (
        client.get(f"/internal/v1/stores/{store_id}/reports/summary?from={today}").status_code
        == 422
    )
    assert client.get(_path(store_id, "summary", "2026-13-01", today)).status_code == 422
