"""`MONEY-LIFECYCLE-009` over HTTP against real PostgreSQL: a cancellation never pays a remedy twice
(review M3, `DEC-045`) and never loses a remedy credit the bill spent (review M7, `DEC-046`).

A cancellation in this system never charges the customer: the two `DEC-024` resolutions that end a
paid order refund what the ledger holds, and `NOT_RECEIVED` takes nothing. The owner decided on
2026-09-30 (delegated, `docs/DECISION_RECORD_ROUND9_2026-09-30.md`) what that does to remedy money:

* **`DEC-045` (M3).** An order a money remedy was executed for -- a late-delivery 10% or a damage
  compensation -- cancelled "lỗi tiệm, không thu tiền": a credit from it still **unspent** is
  **voided** in the same transaction (it can never be spent, and it leaves the counter's list); a
  credit from it already **spent** elsewhere is **netted** -- the refund is what was paid less its
  face value, never below 0 -- on the composite step and on the per-axis route alike.
* **`DEC-046` (M7).** An order whose bill spent a credit, cancelled without charge -- before work,
  at the counter's refusal of the goods, returned unwashed, or the shop's fault, whatever was
  paid -- gets the credit back as a **new credit of the same face value**, linked to the original
  (`reissue_of`) and to the refund by audit. The order is never stranded.

Each move is its own `REMEDY_CREDIT` event, audit row and outbox row, in the transaction that
cancels the order: a failure anywhere writes nothing. The order read states the plan before the
press (`cancellation_money.stage == "PREVIEW"`) and what happened after (`"DONE"`).

These tests replaced round 9's first answer, a refusal (`CANCEL_AFTER_MONEY_REMEDY`,
`CANCEL_WOULD_LOSE_SPENT_CREDIT`), which was the decisions' "Reversal" option and stranded an order
whose laundry never arrived.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_domain.catalog import FulfillmentMode
from nha_trang_laundry_domain.remedies import RemedyKind
from test_prepaid_dropoff_http import CSRF, _as, _post
from test_remedy_credit_lifecycle import _accept, _customer, _order, _price, _reserve

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_remedies import _execute, _propose, _shop

ROOT = Path(__file__).resolve().parents[3]
#: `test_remedies`: the late-delivery 10% of the 110.000 ₫ settled total.
LATE_CREDIT = 11_000
DAMAGE_CREDIT = 80_000
#: 4 kg of standard wash at 25.000 ₫/kg (under the 6 kg cliff), less the 11.000 ₫ credit.
CREDITED_TOTAL = 4 * 25_000 - LATE_CREDIT
LABEL = {
    RemedyKind.LATE_DELIVERY_CREDIT: "Giảm trừ do giao trễ 11.000 ₫",
    RemedyKind.DAMAGE_COMPENSATION: "Bồi thường món bị hỏng 80.000 ₫",
}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit: the routes open their own connections and see only committed rows."""

    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_database_url())
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _one(connection: Any, sql: str, *params: object) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


def _order_row(connection: Any, order_id: UUID) -> tuple[Any, ...]:
    return _one(
        connection,
        "SELECT commercial_status, balance_status, row_version, "
        "(SELECT count(*) FROM order_refunds r WHERE r.order_id = o.id) "
        "FROM orders o WHERE o.id = %s",
        order_id,
    )


def _remedied(
    connection: Any, kind: RemedyKind | None, *, execute: bool = True
) -> tuple[UUID, Any, UUID, UUID | None]:
    """A settled, released order; when `kind` is given, a remedy proposed on it (and executed)."""

    mode = (
        FulfillmentMode.PICKUP_AND_RETURN
        if kind is RemedyKind.LATE_DELIVERY_CREDIT
        else FulfillmentMode.SELF_DROP_SELF_COLLECT
    )
    store_id, staff, order_id, incident_id = _shop(connection, mode=mode)
    credit_id: UUID | None = None
    if kind is None:
        return store_id, staff, order_id, None
    if kind is RemedyKind.LATE_DELIVERY_CREDIT:
        DeliveryLegRepository().record(
            connection,
            RecordDeliveryLegCommand(
                order_id=order_id,
                leg_kind=DeliveryLegKind.RETURN,
                outcome=DeliveryLegOutcome.SUCCEEDED,
                principal=staff,
                correlation_id=uuid4(),
                recorded_at=datetime.now(UTC),
            ),
        )
        fields: dict[str, Any] = {"attested_late_by_minutes": 150}
    elif kind is RemedyKind.DAMAGE_COMPENSATION:
        fields = {"order_line_id": "line-1", "amount_vnd": DAMAGE_CREDIT}
    else:
        fields = {}
    proposal = _propose(
        connection, store_id, incident_id, staff, kind=kind, store_fault_attested=True, **fields
    )
    if execute:
        credit_id = _execute(connection, proposal.proposal_id, staff).credit_id
    return store_id, staff, order_id, credit_id


def _publish_pricebook(connection: Any, staff: Any) -> None:
    publish_pricebook(
        connection,
        actor_id=staff.staff_user_id,
        source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
    )


def _spend_on_new_order(
    service: OperationsService, connection: Any, store_id: UUID, staff: Any, credit_id: UUID
) -> Any:
    """A new walk-in order whose accepted bill spends the credit (quote -> reserve -> order)."""

    _publish_pricebook(connection, staff)
    contact_id, request_id = _customer(connection, store_id, staff)
    quote = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    reserved = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=quote)
    accepted = _accept(service, store_id=store_id, staff=staff, quote=reserved)
    return _order(service, store_id=store_id, staff=staff, contact_id=contact_id, accepted=accepted)


def _read(client: TestClient, order_id: UUID) -> dict[str, Any]:
    answer = client.get(f"/internal/v1/orders/{order_id}")
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


def _step(client: TestClient, order_id: UUID, body: dict[str, Any]) -> Any:
    version = int(_read(client, order_id)["row_version"])
    return _post(client, f"/internal/v1/orders/{order_id}/steps", body, if_match=version)


def _credit(connection: Any, credit_id: UUID) -> tuple[Any, ...]:
    return _one(
        connection,
        "SELECT redeemed_at IS NOT NULL, voided_at IS NOT NULL, voided_with_order_id, row_version "
        "FROM remedy_credits WHERE id = %s",
        credit_id,
    )


def _events(connection: Any, credit_id: UUID) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT e.event_type, e.payload ->> 'order_id', a.action, o.event_type "
            "FROM domain_events e "
            "JOIN audit_events a ON a.aggregate_id = e.aggregate_id "
            "AND a.correlation_id = e.correlation_id "
            "JOIN outbox_events o ON o.aggregate_id = e.aggregate_id "
            "WHERE e.aggregate_id = %s AND e.event_type IN "
            "('REMEDY_CREDIT_VOIDED', 'REMEDY_CREDIT_REISSUED') "
            "AND o.event_type IN ('remedy.credit_voided.v1', 'remedy.credit_reissued.v1')",
            (credit_id,),
        )
        return [tuple(row) for row in cursor.fetchall()]


def _refund(connection: Any, order_id: UUID) -> tuple[int, int]:
    row = _one(
        connection,
        "SELECT refunded_amount_vnd, netted_remedy_vnd FROM order_refunds WHERE order_id = %s",
        order_id,
    )
    return int(row[0]), int(row[1])


def _cancel(client: TestClient, order_id: UUID, route: str, body: dict[str, Any]) -> Any:
    if route == "steps":
        return _step(client, order_id, {"step": "CANCEL", **body})
    review = _post(
        client,
        f"/internal/v1/orders/{order_id}/transition",
        {"target": "CANCELLATION_REVIEW"},
        if_match=int(_read(client, order_id)["row_version"]),
    )
    assert review.status_code == 200, review.text
    return _post(
        client,
        f"/internal/v1/orders/{order_id}/transition",
        {"target": "CANCELLED", **body},
        if_match=int(_read(client, order_id)["row_version"]),
    )


def _exported(connection: Any, store_id: UUID, order_id: UUID) -> tuple[Any, ...]:
    """The order's row of the signed day export, through the release's own SQL and shaping."""

    from nha_trang_laundry_db import exports

    day = _one(
        connection,
        "SELECT (created_at AT TIME ZONE %s)::date FROM orders WHERE id = %s",
        exports.BUSINESS_TIMEZONE,
        order_id,
    )[0]
    with connection.cursor() as cursor:
        cursor.execute(
            exports._EXPORT_SQL,
            {"store": store_id, "zone": exports.BUSINESS_TIMEZONE, "business_date": day},
        )
        fetched = [tuple(row) for row in cursor.fetchall()]
    shaped = exports._money_rows(fetched, policy=None, produced_at=datetime.now(UTC))
    [row] = [row for row in shaped if row[0] == order_id]
    assert len(row) == len(exports.EXPORT_COLUMNS)
    return row


SHOP_FAULT = {"custody_resolution": "SHOP_FAULT_NO_CHARGE"}
#: `test_remedies._shop` settles its order at 110.000 ₫.
SETTLED = 110_000


# --- DEC-045 (M3): an order a remedy paid out on --------------------------------------------------


@pytest.mark.parametrize("route", ["steps", "transition"])
@pytest.mark.parametrize(
    "kind", [RemedyKind.LATE_DELIVERY_CREDIT, RemedyKind.DAMAGE_COMPENSATION], ids=str
)
def test_an_unspent_credit_from_the_order_is_voided_with_the_cancellation(
    connection: Any, service: OperationsService, client: TestClient, kind: RemedyKind, route: str
) -> None:
    store_id, staff, order_id, credit_id = _remedied(connection, kind)
    assert credit_id is not None
    _as(staff)
    face = LATE_CREDIT if kind is RemedyKind.LATE_DELIVERY_CREDIT else DAMAGE_CREDIT
    preview = _read(client, order_id)["cancellation_money"]
    assert preview["stage"] == "PREVIEW"
    assert (preview["voided_vnd"], preview["netted_vnd"], preview["refund_vnd"]) == (
        face,
        0,
        SETTLED,
    )
    assert preview["lines_vi"] == [f"Khoản {LABEL[kind]} khách chưa dùng được huỷ cùng đơn."]

    answer = _cancel(client, order_id, route, SHOP_FAULT)
    assert answer.status_code == 200, answer.text
    assert _order_row(connection, order_id)[:2] == ("CANCELLED", "REFUNDED")
    assert _refund(connection, order_id) == (SETTLED, 0)
    spent, voided, voided_with, _version = _credit(connection, credit_id)
    assert (spent, voided, voided_with) == (False, True, order_id)
    assert _events(connection, credit_id) == [
        ("REMEDY_CREDIT_VOIDED", str(order_id), "REMEDY_CREDIT_VOID", "remedy.credit_voided.v1")
    ]
    done = _read(client, order_id)["cancellation_money"]
    assert done["stage"] == "DONE" and done["voided_vnd"] == face
    assert done["lines_vi"] == [f"Khoản {LABEL[kind]} khách chưa dùng đã được huỷ cùng đơn."]
    # The order's own credit list says so, and the counter's pick list no longer offers it.
    credits = client.get(f"/internal/v1/stores/{store_id}/orders/{order_id}/remedy-credits")
    assert credits.status_code == 200, credits.text
    [listed] = credits.json()["credits"]
    assert (listed["status"], listed["voided_at"] is not None) == ("VOIDED", True)
    picks = client.get(f"/internal/v1/stores/{store_id}/remedy-credits")
    assert picks.status_code == 200, picks.text
    assert str(credit_id) not in {item["credit_id"] for item in picks.json()["credits"]}
    # And it can never be spent.
    _publish_pricebook(connection, staff)
    _contact, request_id = _customer(connection, store_id, staff)
    quote = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    with pytest.raises(Exception) as refused:
        _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=quote)
    assert "REMEDY_CREDIT_VOIDED" in repr(refused.value) or "voided" in str(refused.value)


@pytest.mark.parametrize("route", ["steps", "transition"])
@pytest.mark.parametrize(
    "kind", [RemedyKind.LATE_DELIVERY_CREDIT, RemedyKind.DAMAGE_COMPENSATION], ids=str
)
def test_a_credit_from_the_order_spent_elsewhere_is_netted_from_the_refund(
    connection: Any, service: OperationsService, client: TestClient, kind: RemedyKind, route: str
) -> None:
    store_id, staff, order_id, credit_id = _remedied(connection, kind)
    assert credit_id is not None
    _spend_on_new_order(service, connection, store_id, staff, credit_id)
    before = _credit(connection, credit_id)
    _as(staff)
    face = LATE_CREDIT if kind is RemedyKind.LATE_DELIVERY_CREDIT else DAMAGE_CREDIT
    refund = SETTLED - face
    preview = _read(client, order_id)["cancellation_money"]
    assert (preview["stage"], preview["netted_vnd"], preview["refund_vnd"]) == (
        "PREVIEW",
        face,
        refund,
    )
    assert preview["lines_vi"] == [
        f"Trừ khoản {LABEL[kind]} khách đã dùng: hoàn {refund:,} ₫ thay vì 110.000 ₫.".replace(
            ",", "."
        )
    ]

    answer = _cancel(client, order_id, route, SHOP_FAULT)
    assert answer.status_code == 200, answer.text
    assert _order_row(connection, order_id)[:2] == ("CANCELLED", "REFUNDED")
    # What went back plus what was netted is what was paid; the spent credit is untouched.
    assert _refund(connection, order_id) == (refund, face)
    assert _credit(connection, credit_id) == before
    event = _one(
        connection,
        "SELECT payload -> 'refund' ->> 'refunded_amount_vnd', "
        "payload -> 'refund' ->> 'netted_remedy_vnd', payload -> 'remedy_credits' "
        "FROM domain_events WHERE aggregate_id = %s AND payload ->> 'target' = 'CANCELLED'",
        order_id,
    )
    assert (int(event[0]), int(event[1])) == (refund, face)
    assert event[2]["netted_credit_ids"] == [str(credit_id)]
    done = _read(client, order_id)["cancellation_money"]
    assert (done["stage"], done["refund_vnd"], done["netted_vnd"]) == ("DONE", refund, face)
    # The day's export carries the refund and its netting on the order's row (`DEC-045`).
    exported = _exported(connection, store_id, order_id)
    # paid_vnd, remaining_vnd (0 once cancelled), refund_netted_remedy_vnd; and the refund itself.
    assert exported[-3:] == (SETTLED, 0, face)
    assert exported[13] == refund
    assert done["lines_vi"] == [
        f"Đã trừ {face:,} ₫ (khoản khách đã dùng ở đơn khác): hoàn {refund:,} ₫ thay vì "
        "110.000 ₫.".replace(",", ".")
    ]


@pytest.mark.parametrize(
    "remedy",
    ["none", "free-rewash", "proposed-not-executed"],
)
def test_the_same_cancellation_goes_through_without_remedy_money(
    connection: Any, client: TestClient, remedy: str
) -> None:
    kind = {
        "none": None,
        "free-rewash": RemedyKind.FREE_REWASH,
        "proposed-not-executed": RemedyKind.DAMAGE_COMPENSATION,
    }[remedy]
    _store_id, staff, order_id, credit_id = _remedied(
        connection, kind, execute=remedy != "proposed-not-executed"
    )
    assert credit_id is None
    _as(staff)
    assert _read(client, order_id)["cancellation_money"] is None
    answer = _step(client, order_id, {"step": "CANCEL", **SHOP_FAULT})
    assert answer.status_code == 200, answer.text
    assert (answer.json()["commercial"], answer.json()["balance"]) == ("CANCELLED", "REFUNDED")
    assert _refund(connection, order_id) == (SETTLED, 0)
    assert _read(client, order_id)["cancellation_money"] is None


# --- DEC-046 (M7): an order whose bill spent a credit ---------------------------------------------


def _pay(client: TestClient, order_id: UUID, amount: int) -> None:
    version = int(_read(client, order_id)["row_version"])
    paid = _post(
        client,
        f"/internal/v1/orders/{order_id}/payments",
        {"amount_vnd": amount, "method": "TIEN_MAT"},
        if_match=version,
    )
    assert paid.status_code == 201, paid.text


@pytest.mark.parametrize(
    ("moment", "paid", "body"),
    [
        # Before anything happened to the goods: the plain cancellation (the laundry never came).
        ("created", 0, {"step": "CANCEL"}),
        # The counter refuses the goods on the counter (ORDER-STEPS-002 R2).
        ("handed-over", 0, {"step": "REJECT_INTAKE", "rejection_reason": "NOT_SERVICEABLE"}),
        # Handed back unwashed, whatever was paid.
        ("received", 0, {"step": "CANCEL", "custody_resolution": "RETURNED_UNWASHED_REFUNDED"}),
        (
            "received",
            50_000,
            {"step": "CANCEL", "custody_resolution": "RETURNED_UNWASHED_REFUNDED"},
        ),
        (
            "received",
            CREDITED_TOTAL,
            {"step": "CANCEL", "custody_resolution": "RETURNED_UNWASHED_REFUNDED"},
        ),
        # The shop's fault once washing began, whatever was paid.
        ("washing", 0, {"step": "CANCEL", "custody_resolution": "SHOP_FAULT_NO_CHARGE"}),
        ("washing", 50_000, {"step": "CANCEL", "custody_resolution": "SHOP_FAULT_NO_CHARGE"}),
        (
            "washing",
            CREDITED_TOTAL,
            {"step": "CANCEL", "custody_resolution": "SHOP_FAULT_NO_CHARGE"},
        ),
    ],
)
def test_a_credit_the_bill_spent_is_reissued_when_the_order_is_cancelled_without_charge(
    connection: Any,
    service: OperationsService,
    client: TestClient,
    moment: str,
    paid: int,
    body: dict[str, Any],
) -> None:
    store_id, staff, _source, credit_id = _remedied(connection, RemedyKind.LATE_DELIVERY_CREDIT)
    assert credit_id is not None
    order = _spend_on_new_order(service, connection, store_id, staff, credit_id)
    order_id = order.order_id
    _as(staff)
    assert _read(client, order_id)["owed_vnd"] == CREDITED_TOTAL
    if moment == "handed-over":
        handed = _post(
            client,
            f"/internal/v1/orders/{order_id}/intake-transition",
            {"target": "RECEIVED_PENDING_INSPECTION"},
            if_match=int(_read(client, order_id)["row_version"]),
        )
        assert handed.status_code == 200, handed.text
    if moment in {"received", "washing"}:
        received = _step(client, order_id, {"step": "RECEIVE", "slot_approved": True})
        assert received.status_code == 200, received.text
    if moment == "washing":
        washing = _step(client, order_id, {"step": "START_WASH"})
        assert washing.status_code == 200, washing.text
    if paid:
        _pay(client, order_id, paid)
    reissue_line = (
        f"Cấp lại cho khách khoản {LABEL[RemedyKind.LATE_DELIVERY_CREDIT]} đã dùng cho đơn này."
    )
    preview = _read(client, order_id)["cancellation_money"]
    assert (preview["stage"], preview["reissued_vnd"], preview["lines_vi"]) == (
        "PREVIEW",
        LATE_CREDIT,
        [reissue_line],
    )
    original = _one(
        connection,
        "SELECT redeemed_at, redeemed_quote_id, row_version FROM remedy_credits WHERE id = %s",
        credit_id,
    )

    answer = _step(client, order_id, body)
    assert answer.status_code == 200, answer.text
    assert answer.json()["commercial"] == "CANCELLED"
    # The cash, if any, goes back whole; nothing is netted (no credit was issued from this order).
    rows = _order_row(connection, order_id)
    if paid:
        assert rows[1] == "REFUNDED" and _refund(connection, order_id) == (paid, 0)
    else:
        assert rows[3] == 0
    # The original stays spent, exactly as it was; a new credit of its face value stands for it.
    assert (
        _one(
            connection,
            "SELECT redeemed_at, redeemed_quote_id, row_version FROM remedy_credits WHERE id = %s",
            credit_id,
        )
        == original
    )
    reissued = _one(
        connection,
        "SELECT id, amount_vnd, redeemed_at, voided_at, reissued_for_order_id, "
        "remedy_proposal_id = (SELECT remedy_proposal_id FROM remedy_credits WHERE id = %s) "
        "FROM remedy_credits WHERE reissue_of = %s",
        credit_id,
        credit_id,
    )
    new_id = reissued[0]
    assert reissued[1:] == (LATE_CREDIT, None, None, order_id, True)
    assert _events(connection, new_id) == [
        (
            "REMEDY_CREDIT_REISSUED",
            str(order_id),
            "REMEDY_CREDIT_REISSUE",
            "remedy.credit_reissued.v1",
        )
    ]
    refund_id = _one(
        connection,
        "SELECT payload ->> 'refund_id' FROM domain_events WHERE aggregate_id = %s",
        new_id,
    )[0]
    if paid:
        assert refund_id == str(
            _one(connection, "SELECT id FROM order_refunds WHERE order_id = %s", order_id)[0]
        )
    else:
        assert refund_id is None
    # The counter can pick it and spend it again.
    picks = client.get(f"/internal/v1/stores/{store_id}/remedy-credits")
    assert str(new_id) in {item["credit_id"] for item in picks.json()["credits"]}
    done = _read(client, order_id)["cancellation_money"]
    assert (done["stage"], done["reissued_vnd"]) == ("DONE", LATE_CREDIT)
    assert done["lines_vi"] == [
        f"Đã cấp lại cho khách khoản {LABEL[RemedyKind.LATE_DELIVERY_CREDIT]}."
    ]
    again = _spend_on_new_order(service, connection, store_id, staff, new_id)
    assert _read(client, again.order_id)["owed_vnd"] == CREDITED_TOTAL


def test_a_failure_while_moving_a_credit_writes_nothing(
    connection: Any,
    service: OperationsService,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomicity: the refund, the cancellation and every credit move commit together or not at
    all. A failure in the reissue after the void leaves the order, the refund and both credits
    exactly as they were."""

    from nha_trang_laundry_db.cancellation_money import write_cancellation_credit_moves

    _store, staff, order_id, credit_id = _remedied(connection, RemedyKind.DAMAGE_COMPENSATION)
    assert credit_id is not None
    original = write_cancellation_credit_moves

    def failing(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise RuntimeError("the database went away after the void")

    # Replaced where the transition calls it, after it has already voided the credit.
    monkeypatch.setattr("nha_trang_laundry_db.orders.write_cancellation_credit_moves", failing)
    before_order = _order_row(connection, order_id)
    before_credit = _credit(connection, credit_id)
    _as(staff)
    with pytest.raises(RuntimeError):
        _step(client, order_id, {"step": "CANCEL", **SHOP_FAULT})
    assert _order_row(connection, order_id) == before_order
    assert _credit(connection, credit_id) == before_credit
    assert _events(connection, credit_id) == []


def test_an_order_with_no_credit_on_its_bill_still_cancels_without_charge(
    connection: Any, service: OperationsService, client: TestClient
) -> None:
    store_id, staff, _source, _credit_id = _remedied(connection, None)
    _publish_pricebook(connection, staff)
    contact_id, request_id = _customer(connection, store_id, staff)
    quote = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    accepted = _accept(service, store_id=store_id, staff=staff, quote=quote)
    order = _order(
        service, store_id=store_id, staff=staff, contact_id=contact_id, accepted=accepted
    )
    _as(staff)
    assert (
        _step(client, order.order_id, {"step": "RECEIVE", "slot_approved": True}).status_code == 200
    )
    assert _step(client, order.order_id, {"step": "START_WASH"}).status_code == 200
    _pay(client, order.order_id, 50_000)
    assert _read(client, order.order_id)["cancellation_money"] is None
    answer = _step(client, order.order_id, {"step": "CANCEL", **SHOP_FAULT})
    assert answer.status_code == 200, answer.text
    assert (answer.json()["commercial"], answer.json()["balance"]) == ("CANCELLED", "REFUNDED")
    assert _refund(connection, order.order_id) == (50_000, 0)
