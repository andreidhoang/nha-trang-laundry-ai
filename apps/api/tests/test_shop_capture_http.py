"""`SHOP-CAPTURE-001` over HTTP: machines, the wash step's machine, trip costs, Sổ thu chi.

The repository tests (`packages/db/tests/test_shop_capture.py`) prove what is written. These prove
the routes: who may do what (one opaque 403 for every refusal of role, MFA or store), `If-Match`
and `Idempotency-Key` behaving as on every other versioned write, the 422 bodies that name why a
value was refused, and a cross-store row answering 404.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_operations_service,
    get_shop_capture_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.shop_capture import ShopCaptureService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.reports import shop_today
from nha_trang_laundry_domain.catalog import FulfillmentMode

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_shop_capture import _person, _Shop

DENIED = {"detail": "operation denied"}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_database_url())
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[get_shop_capture_service] = lambda: ShopCaptureService(settings)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _headers(version: int | None = None, key: str | None = None) -> dict[str, str]:
    headers = {"Idempotency-Key": key or f"key-{uuid4().hex}"}
    if version is not None:
        headers["If-Match"] = str(version)
    return headers


def _machines(client: TestClient, store_id: UUID, query: str = "") -> dict[str, Any]:
    response = client.get(f"/internal/v1/stores/{store_id}/machines{query}")
    assert response.status_code == 200, response.text
    return response.json()


# --- machines ---------------------------------------------------------------------------------


def test_the_counter_reads_the_wash_machines_and_only_the_owner_edits_them(
    connection: Any, client: TestClient
) -> None:
    shop = _Shop(connection)
    _as(shop.staff)
    wash = _machines(client, shop.store_id, "?purpose=WASH")
    assert [machine["code"] for machine in wash["machines"]] == ["DC-01", "WASH-01", "WASH-02"]
    assert all(machine["starts_cycle"] for machine in wash["machines"])
    assert wash["truncated"] is False
    everything = _machines(client, shop.store_id)
    assert {machine["code"] for machine in everything["machines"]} >= {"DRY-01"}

    body = {"code": "WASH-03", "display_name": "Máy giặt mới", "category": "washer"}
    path = f"/internal/v1/stores/{shop.store_id}/machines"
    refused = client.post(path, headers=_headers(), json=body)
    assert (refused.status_code, refused.json()) == (403, DENIED)

    _as(shop.owner)
    headers = _headers(key="add-wash-03")
    created = client.post(path, headers=headers, json=body)
    assert created.status_code == 201, created.text
    replay = client.post(path, headers=headers, json=body)
    assert replay.json()["replayed"] is True
    assert replay.json()["machine_id"] == created.json()["machine_id"]
    changed = client.post(path, headers=headers, json={**body, "display_name": "Khác"})
    assert (changed.status_code, changed.json()) == (409, {"detail": "IDEMPOTENCY_CONFLICT"})
    taken = client.post(path, headers=_headers(), json=body)
    assert (taken.status_code, taken.json()) == (
        422,
        {"detail": {"reason_code": "MACHINE_CODE_TAKEN"}},
    )
    bad = client.post(path, headers=_headers(), json={**body, "code": "WASH 04"})
    assert bad.json() == {"detail": {"reason_code": "MACHINE_CODE_INVALID"}}

    machine = created.json()
    target = f"{path}/{machine['machine_id']}"
    missing = client.patch(target, headers=_headers(), json={"display_name": "A"})
    assert missing.status_code == 428
    stale = client.patch(target, headers=_headers(7), json={"display_name": "A"})
    assert stale.status_code == 409 and "STALE_VERSION" in stale.json()["detail"]
    renamed = client.patch(target, headers=_headers(1), json={"display_name": "Máy giặt số 3"})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["display_name"], renamed.json()["row_version"]) == ("Máy giặt số 3", 2)
    retired = client.patch(target, headers=_headers(2), json={"retire": True})
    assert retired.json()["retired_at"] is not None
    assert "WASH-03" not in {m["code"] for m in _machines(client, shop.store_id)["machines"]}
    assert "WASH-03" in {
        m["code"] for m in _machines(client, shop.store_id, "?include_retired=true")["machines"]
    }

    # Another store's machine addressed under this store's path does not exist.
    elsewhere = _Shop(connection)
    foreign = client.patch(
        f"{path}/{elsewhere.machines['WASH-01']}", headers=_headers(1), json={"display_name": "X"}
    )
    assert foreign.status_code == 404
    # A role outside the list is refused before anything is read.
    _as(_person(connection, shop.store_id, frozenset({StaffRole.ACCOUNTANT})))
    denied = client.get(f"/internal/v1/stores/{shop.store_id}/machines")
    assert (denied.status_code, denied.json()) == (403, DENIED)
    # A member of another store reads nothing here.
    _as(elsewhere.staff)
    assert client.get(f"/internal/v1/stores/{shop.store_id}/machines").status_code == 403


# --- the wash step ------------------------------------------------------------------------------


def test_start_wash_takes_a_machine_and_only_a_wash_or_rewash_names_one(
    connection: Any, client: TestClient
) -> None:
    shop = _Shop(connection)
    order_id, version = shop.order()
    _as(shop.staff)
    path = f"/internal/v1/orders/{order_id}/steps"
    dryer = client.post(
        path,
        headers=_headers(version),
        json={"step": "START_WASH", "machine_id": str(shop.machines["DRY-01"])},
    )
    assert dryer.status_code == 422
    assert dryer.json() == {
        "detail": {"outcome": "NOT_SUPPORTED", "reason_code": "MACHINE_UNAVAILABLE"}
    }
    wrong_step = client.post(
        path,
        headers=_headers(version),
        json={"step": "HOLD", "machine_id": str(shop.machines["WASH-01"])},
    )
    assert wrong_step.status_code == 422
    started = client.post(
        path,
        headers=_headers(version),
        json={"step": "START_WASH", "machine_id": str(shop.machines["WASH-01"])},
    )
    assert started.status_code == 200, started.text
    assert started.json()["production"] == "IN_PROCESS"
    checked = client.post(
        path, headers=_headers(started.json()["row_version"]), json={"step": "QUALITY_CHECK"}
    )
    assert checked.status_code == 200, checked.text

    capture = client.get(f"/internal/v1/orders/{order_id}/capture")
    assert capture.status_code == 200, capture.text
    [cycle] = capture.json()["cycles"]
    assert (cycle["kind"], cycle["machine_code"]) == ("WASH", "WASH-01")
    assert cycle["ended_at"] is not None and cycle["minutes"] == 0
    # The chooser now offers WASH-01 first: the last one used.
    wash = _machines(client, shop.store_id, "?purpose=WASH")
    assert wash["machines"][0]["code"] == "WASH-01"
    # Outside the order's store, the capture read is a 404, as the order read is.
    elsewhere = _Shop(connection)
    _as(elsewhere.staff)
    assert client.get(f"/internal/v1/orders/{order_id}/capture").status_code == 404


# --- trip costs ---------------------------------------------------------------------------------


def test_a_delivery_leg_carries_its_trip_cost_and_refuses_a_bad_one(
    connection: Any, client: TestClient
) -> None:
    shop = _Shop(connection)
    order_id, _ = shop.order(FulfillmentMode.PICKUP_AND_RETURN)
    _as(shop.staff)
    path = f"/internal/v1/orders/{order_id}/delivery-legs"
    base = {"leg_kind": "PICKUP", "outcome": "SUCCEEDED"}
    for extra, code in (
        ({"note": "gọi 0382.318.492"}, "NOTE_LOOKS_LIKE_PHONE"),
        ({"km": "4.55"}, "TRIP_KM_INVALID"),
        ({"cost_vnd": 10_000_001}, "TRIP_COST_TOO_LARGE"),
    ):
        refused = client.post(path, headers=_headers(), json={**base, **extra})
        assert (refused.status_code, refused.json()) == (422, {"detail": {"reason_code": code}})
    floaty = client.post(path, headers=_headers(), json={**base, "cost_vnd": 15000.0})
    assert floaty.status_code == 422
    headers = _headers(key="trip-1")
    body = {**base, "vehicle": "XE_MAY", "km": "3,5", "cost_vnd": 15_000, "note": "gửi xe"}
    recorded = client.post(path, headers=headers, json=body)
    assert recorded.status_code == 201, recorded.text
    again = client.post(path, headers=headers, json=body)
    assert again.json()["leg_id"] == recorded.json()["leg_id"]
    conflict = client.post(path, headers=headers, json={**body, "cost_vnd": 16_000})
    assert conflict.status_code == 409
    [leg] = client.get(f"/internal/v1/orders/{order_id}/capture").json()["legs"]
    assert (leg["vehicle"], leg["km"], leg["cost_vnd"], leg["note"]) == (
        "XE_MAY",
        "3.5",
        15_000,
        "gửi xe",
    )


# --- Sổ thu chi ---------------------------------------------------------------------------------


def test_so_thu_chi_is_written_by_owner_and_accountant_and_read_by_the_auditor(
    connection: Any, client: TestClient
) -> None:
    shop = _Shop(connection)
    accountant = _person(connection, shop.store_id, frozenset({StaffRole.ACCOUNTANT}))
    auditor = _person(connection, shop.store_id, frozenset({StaffRole.AUDITOR}))
    today = shop_today(datetime.now(UTC))
    month = today.strftime("%Y-%m")
    path = f"/internal/v1/stores/{shop.store_id}/expenses"
    body = {"spent_on": today.isoformat(), "category": "DIEN", "amount_vnd": 1_250_000}

    for refused_as in (shop.staff, auditor):
        _as(refused_as)
        response = client.post(path, headers=_headers(), json=body)
        assert (response.status_code, response.json()) == (403, DENIED)
    _as(shop.staff)
    assert client.get(f"{path}?month={month}").status_code == 403

    _as(shop.owner)
    first = client.post(path, headers=_headers(), json={**body, "note": "tiền điện tháng"})
    assert first.status_code == 201, first.text
    _as(accountant)
    second = client.post(
        path, headers=_headers(), json={**body, "category": "NUOC", "amount_vnd": 300_000}
    )
    assert second.status_code == 201
    wrong = client.post(path, headers=_headers(), json={**body, "amount_vnd": 99_000_000})
    assert wrong.status_code == 201
    for extra, code in (
        ({"spent_on": (today + timedelta(days=1)).isoformat()}, "EXPENSE_DATE_IN_FUTURE"),
        ({"amount_vnd": 1_000_000_001}, "EXPENSE_AMOUNT_TOO_LARGE"),
        ({"note": "0382318492"}, "NOTE_LOOKS_LIKE_PHONE"),
    ):
        refused = client.post(path, headers=_headers(), json={**body, **extra})
        assert (refused.status_code, refused.json()) == (422, {"detail": {"reason_code": code}})
    assert (
        client.post(path, headers=_headers(), json={**body, "amount_vnd": "5000"}).status_code
        == 422
    )

    void_path = f"{path}/{wrong.json()['expense_id']}/void"
    assert client.post(void_path, headers=_headers()).status_code == 428
    voided = client.post(void_path, headers=_headers(1))
    assert voided.status_code == 200, voided.text
    assert voided.json()["voided_at"] is not None
    twice = client.post(void_path, headers=_headers(2))
    assert twice.json() == {"detail": {"reason_code": "EXPENSE_ALREADY_VOIDED"}}

    _as(auditor)
    read = client.get(f"{path}?month={month}")
    assert read.status_code == 200, read.text
    page = read.json()
    totals = {total["category"]: total["amount_vnd"] for total in page["totals"]}
    assert totals["DIEN"] == 1_250_000 and totals["NUOC"] == 300_000 and totals["KHAC"] == 0
    assert (page["total_vnd"], page["entries"], page["voided_entries"]) == (1_550_000, 2, 1)
    assert len(page["lines"]) == 3 and page["truncated"] is False
    # The core categories still missing before the month's margin can be shown (DEC-038).
    assert page["core_missing"] == ["HOA_CHAT", "LUONG", "MAT_BANG"]
    assert client.get(f"{path}?month=2026-13").json() == {
        "detail": {"reason_code": "MONTH_INVALID"}
    }
    assert client.get(f"{path}?month=202609").status_code == 422

    # Another store's line under this store's path does not exist; another store's staff is 403.
    elsewhere = _Shop(connection)
    _as(elsewhere.owner)
    assert client.post(void_path, headers=_headers(1)).status_code == 404
    assert client.get(f"{path}?month={month}").status_code == 403
