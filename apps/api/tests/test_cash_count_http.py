"""`CASH-COUNT-009` (`DEC-049`) over HTTP: today's sheet, recording, corrections, the owner's days.

The repository tests (`packages/db/tests/test_cash_count_postgres.py`) prove what is written and
summed. These prove the routes: the drawer route's gate for the counter, the owner's gate for the
history, one opaque 403 for role, MFA or store, `Idempotency-Key` behaving as on every other write,
409 `CODE: text` for a state the console must re-read, 422 `{reason_code}` for a value the rules
refuse -- and that no figure on the wire is signed.
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
from nha_trang_laundry_api.cash_count import CashCountService
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_cash_count_service,
    get_operations_service,
    get_shop_capture_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.shop_capture import ShopCaptureService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.reports import shop_today

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_drawer_postgres import _the_day
from test_reports import _person

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
    app.dependency_overrides[get_cash_count_service] = lambda: CashCountService(settings)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _key(key: str | None = None) -> dict[str, str]:
    return {"Idempotency-Key": key or f"key-{uuid4().hex}"}


def _today() -> str:
    return shop_today(datetime.now(UTC)).isoformat()


def _no_signed_figure(value: Any) -> bool:
    """No integer anywhere in a body is below zero: every gap is a size and a word."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, dict):
        return all(_no_signed_figure(item) for item in value.values())
    if isinstance(value, list):
        return all(_no_signed_figure(item) for item in value)
    return True


def _post(client: TestClient, store: UUID, body: dict[str, Any], key: str | None = None) -> Any:
    return client.post(f"/internal/v1/stores/{store}/cash-count", headers=_key(key), json=body)


def test_the_counter_counts_the_day_and_the_owner_sees_the_shortfall(
    connection: Any, client: TestClient
) -> None:
    shop, staff, _ = _the_day(connection)
    store = shop.store_id
    path = f"/internal/v1/stores/{store}/cash-count"

    # Sổ thu chi: 50.000 handed out of the drawer, recorded by the owner.
    _as(shop.owner)
    spent = client.post(
        f"/internal/v1/stores/{store}/expenses",
        headers=_key(),
        json={
            "spent_on": _today(),
            "category": "HOA_CHAT",
            "amount_vnd": 50_000,
            "paid_from_drawer": True,
        },
    )
    assert spent.status_code == 201, spent.text
    assert spent.json()["paid_from_drawer"] is True
    plain = client.post(
        f"/internal/v1/stores/{store}/expenses",
        headers=_key(),
        json={"spent_on": _today(), "category": "DIEN", "amount_vnd": 70_000},
    )
    assert plain.json()["paid_from_drawer"] is False
    flag = client.post(
        f"/internal/v1/stores/{store}/expenses",
        headers=_key(),
        json={"spent_on": _today(), "category": "DIEN", "amount_vnd": 1, "paid_from_drawer": 1},
    )
    assert flag.status_code == 422  # a yes/no is a boolean, never coerced

    _as(staff)
    sheet = client.get(path)
    assert sheet.status_code == 200, sheet.text
    body = sheet.json()
    assert body["business_day"] == _today()
    assert body["opening_float"] is None and body["closing_count"] is None
    assert body["expected"]["status"] == "FLOAT_MISSING"
    assert body["expected"]["expected_vnd"] is None
    assert (body["expected"]["cash_in_vnd"], body["expected"]["drawer_expenses_vnd"]) == (
        210_000,
        50_000,
    )
    assert body["query_version"].startswith("cash-count-v1:")

    opened = _post(
        client, store, {"business_day": _today(), "kind": "OPENING_FLOAT", "counted_vnd": 500_000}
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["expected"]["expected_vnd"] == 600_000
    assert opened.json()["expected"]["status"] == "INCOMPLETE"
    assert opened.json()["expected"]["excluded_unknown_refunds_count"] == 1

    closed = _post(
        client,
        store,
        {"business_day": _today(), "kind": "CLOSING_COUNT", "counted_vnd": 590_000},
        key="close-1",
    )
    assert closed.status_code == 201, closed.text
    entry = closed.json()["entry"]
    assert (entry["difference_direction"], entry["difference_vnd"]) == ("SHORT", 10_000)
    assert entry["expected_vnd"] == 600_000 and entry["expected_status"] == "INCOMPLETE"
    assert closed.json()["closing_count"]["entry_id"] == entry["entry_id"]
    assert _no_signed_figure(closed.json())
    replay = _post(
        client,
        store,
        {"business_day": _today(), "kind": "CLOSING_COUNT", "counted_vnd": 590_000},
        key="close-1",
    )
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert replay.json()["entry"]["entry_id"] == entry["entry_id"]
    changed = _post(
        client,
        store,
        {"business_day": _today(), "kind": "CLOSING_COUNT", "counted_vnd": 580_000},
        key="close-1",
    )
    assert (changed.status_code, changed.json()) == (409, {"detail": "IDEMPOTENCY_CONFLICT"})

    # Once a day: a second original is a 409 the console re-reads; a correction needs a reason.
    twice = _post(
        client, store, {"business_day": _today(), "kind": "CLOSING_COUNT", "counted_vnd": 1}
    )
    assert twice.status_code == 409
    assert twice.json()["detail"].startswith("CASH_COUNT_ALREADY_RECORDED")
    no_reason = _post(
        client,
        store,
        {
            "business_day": _today(),
            "kind": "CLOSING_COUNT",
            "counted_vnd": 600_000,
            "supersedes_entry_id": entry["entry_id"],
        },
    )
    assert (no_reason.status_code, no_reason.json()) == (
        422,
        {"detail": {"reason_code": "CASH_COUNT_REASON_REQUIRED"}},
    )
    corrected = _post(
        client,
        store,
        {
            "business_day": _today(),
            "kind": "CLOSING_COUNT",
            "counted_vnd": 600_000,
            "supersedes_entry_id": entry["entry_id"],
            "reason": "đếm sót tờ 10.000",
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["entry"]["difference_direction"] == "EVEN"
    assert corrected.json()["entry"]["correction_reason"] == "đếm sót tờ 10.000"
    assert [e["superseded"] for e in corrected.json()["entries"]] == [False, True, False]
    stale = _post(
        client,
        store,
        {
            "business_day": _today(),
            "kind": "CLOSING_COUNT",
            "counted_vnd": 1,
            "supersedes_entry_id": entry["entry_id"],
            "reason": "x",
        },
    )
    assert stale.status_code == 409 and stale.json()["detail"].startswith("CASH_COUNT_STALE")
    yesterday = (shop_today(datetime.now(UTC)) - timedelta(days=1)).isoformat()
    late = _post(
        client, store, {"business_day": yesterday, "kind": "OPENING_FLOAT", "counted_vnd": 1}
    )
    assert late.json() == {"detail": {"reason_code": "CASH_COUNT_DAY_NOT_TODAY"}}
    for bad in (
        {"counted_vnd": -1},
        {"counted_vnd": 1.5},
        {"counted_vnd": True},
        {"counted_vnd": "1"},
        {"kind": "MIDDAY"},
        {"extra": 1},
    ):
        refused = _post(
            client,
            store,
            {"business_day": _today(), "kind": "OPENING_FLOAT", "counted_vnd": 1, **bad},
        )
        assert refused.status_code == 422, bad
    missing_key = client.post(
        path, json={"business_day": _today(), "kind": "OPENING_FLOAT", "counted_vnd": 1}
    )
    assert missing_key.status_code in (400, 422, 428)

    # The owner's days, over the report's window.
    history_path = f"/internal/v1/stores/{store}/cash-counts?from={_today()}&to={_today()}"
    assert (client.get(history_path).status_code, client.get(history_path).json()) == (
        403,
        DENIED,
    )
    _as(shop.owner)
    history = client.get(history_path)
    assert history.status_code == 200, history.text
    (day,) = history.json()["days"]
    assert day["closing_count"]["difference_direction"] == "EVEN"
    assert len(day["entries"]) == 3
    assert _no_signed_figure(history.json())
    long = client.get(f"/internal/v1/stores/{store}/cash-counts?from=2026-01-01&to={_today()}")
    assert long.json() == {"detail": {"reason_code": "REPORT_WINDOW_TOO_LONG"}}


def test_the_gates_one_opaque_refusal_for_role_mfa_or_store(
    connection: Any, client: TestClient
) -> None:
    shop, staff, _ = _the_day(connection)
    store = shop.store_id
    body = {"business_day": _today(), "kind": "OPENING_FLOAT", "counted_vnd": 1}
    refused = [
        _person(connection, store, frozenset({StaffRole.AUDITOR})),
        _person(connection, store, frozenset({StaffRole.ACCOUNTANT})),
        _person(connection, store, frozenset({StaffRole.OPERATOR}), mfa=False),
        _person(connection, None, frozenset({StaffRole.OPERATOR})),
    ]
    for person in refused:
        _as(person)
        read = client.get(f"/internal/v1/stores/{store}/cash-count")
        wrote = _post(client, store, body)
        assert (read.status_code, read.json()) == (403, DENIED)
        assert (wrote.status_code, wrote.json()) == (403, DENIED)
    approver = _person(connection, store, frozenset({StaffRole.OPS_APPROVER}))
    for person in (staff, approver):
        _as(person)
        assert client.get(f"/internal/v1/stores/{store}/cash-count").status_code == 200
        history = client.get(
            f"/internal/v1/stores/{store}/cash-counts?from={_today()}&to={_today()}"
        )
        assert (history.status_code, history.json()) == (403, DENIED)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM cash_counts WHERE store_id = %s", (store,))
        assert cursor.fetchone()[0] == 0
    _as(staff)
    other = uuid4()
    assert client.get(f"/internal/v1/stores/{other}/cash-count").status_code == 403


def test_a_malformed_entry_is_answered_without_the_values_it_held(
    connection: Any, client: TestClient
) -> None:
    """A correction reason is free text a person typed, refused by the rules when it looks like a
    phone number -- so a body the framework itself refuses (a field missing, the wrong type, a
    reason too long, a field that is not ours) is answered with where and what, never the value:
    FastAPI's default 422 echoes `input`, and for a missing field that is the whole body."""
    shop, staff, _ = _the_day(connection)
    _as(staff)
    phone = "0905123456"
    reason = f"khach {phone} tra thieu"
    base = {
        "business_day": _today(),
        "kind": "CLOSING_COUNT",
        "counted_vnd": 590_000,
        "supersedes_entry_id": str(uuid4()),
        "reason": reason,
    }
    malformed = {
        "kind missing": {k: v for k, v in base.items() if k != "kind"},
        "day missing": {k: v for k, v in base.items() if k != "business_day"},
        "count a string": {**base, "counted_vnd": "590000"},
        "count below 0": {**base, "counted_vnd": -1},
        "kind unknown": {**base, "kind": "MIDDAY"},
        "reason too long": {**base, "reason": (reason + " ") * 10},
        "reason not text": {**base, "reason": [reason]},
        "a field not ours": {**base, "note": reason},
        "supersedes not an id": {**base, "supersedes_entry_id": phone},
    }
    for name, body in malformed.items():
        refused = _post(client, shop.store_id, body)
        assert refused.status_code == 422, name
        assert phone not in refused.text, name
        detail = refused.json()["detail"]
        assert detail and all(set(item) == {"type", "loc", "msg"} for item in detail), name
        assert all(item["loc"] and item["loc"][0] == "body" for item in detail), name
    # The neighbour: the Sổ thu chi line that carries the "Trả từ két" tick has a free-text note.
    _as(shop.owner)
    line = {
        "spent_on": _today(),
        "category": "HOA_CHAT",
        "amount_vnd": 50_000,
        "paid_from_drawer": True,
        "note": reason,
    }
    for name, body in {
        "category missing": {k: v for k, v in line.items() if k != "category"},
        "tick not a yes/no": {**line, "paid_from_drawer": 1},
        "note too long": {**line, "note": (reason + " ") * 20},
        "a field not ours": {**line, "reason": reason},
    }.items():
        refused = client.post(
            f"/internal/v1/stores/{shop.store_id}/expenses", headers=_key(), json=body
        )
        assert refused.status_code == 422, name
        assert phone not in refused.text, name
        assert all("input" not in item for item in refused.json()["detail"]), name
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM cash_counts WHERE store_id = %s", (shop.store_id,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM expenses WHERE store_id = %s", (shop.store_id,))
        assert cursor.fetchone()[0] == 0
    # A refusal the rules make is a code alone (no value), as before.
    _as(staff)
    first = {"business_day": _today(), "kind": "CLOSING_COUNT", "counted_vnd": 1}
    assert _post(client, shop.store_id, first).status_code == 201
    sheet = client.get(f"/internal/v1/stores/{shop.store_id}/cash-count").json()
    looks_like_phone = _post(
        client,
        shop.store_id,
        {**base, "supersedes_entry_id": sheet["closing_count"]["entry_id"]},
    )
    assert looks_like_phone.json() == {
        "detail": {"reason_code": "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"}
    }
    # Verification round 3 of 9b: a phone punctuated with brackets or slashes is refused the
    # same way, and nothing is stored -- the correction reason is the shop's books, not a contact
    # list.
    # Verification round 4 of 9b: spaced hyphens or dots, an en-dash, a slash beside a space and
    # square brackets reached the sheet; every punctuation is refused alike now.
    for punctuated in (
        "khach (090) 512 3456",
        "sdt 0905/123/456",
        "khach 0905 - 123 - 456 tra thieu",
        "khach 0905 . 123 . 456",
        "khach 0905\u2013123\u2013456",
        "khach 0905/123 456",
        "khach (090) 512/3456",
        "khach [0905] 123 456",
    ):
        refused = _post(
            client,
            shop.store_id,
            {
                **base,
                "reason": punctuated,
                "supersedes_entry_id": sheet["closing_count"]["entry_id"],
            },
        )
        assert refused.json() == {
            "detail": {"reason_code": "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"}
        }, punctuated
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM cash_counts"
            " WHERE store_id = %s AND correction_reason IS NOT NULL",
            (shop.store_id,),
        )
        assert cursor.fetchone()[0] == 0
