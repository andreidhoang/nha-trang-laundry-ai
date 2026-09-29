"""`MONEY-LIFECYCLE-009` over HTTP against real PostgreSQL: a cancellation never pays a remedy twice
(review M3) and never loses a remedy credit the bill spent (review M7).

A cancellation in this system never charges the customer: the two `DEC-024` resolutions that end a
paid order refund the whole ledger, and `NOT_RECEIVED` takes nothing. So:

* **M3.** An order a money remedy was executed for -- a late-delivery 10% or a damage compensation,
  its credit spent since or not -- cancelled "lỗi tiệm, không thu tiền" gave the customer the whole
  bill back *and* kept the credit. No decided rule nets the two, and no decision voids an issued
  credit, so the cancellation is refused by name (`CANCEL_AFTER_MONEY_REMEDY`), on the composite
  step and on the per-axis route, and writes nothing. The way forward is the one the order already
  has: withdraw the cancellation and finish the order.
* **M7.** An order whose bill spent a credit, cancelled without charge -- before work, at the
  counter's refusal of the goods, returned unwashed, or the shop's fault, whatever was paid -- lost
  the credit silently: `0042` forbids handing a spent credit back and no decision reissues one. It
  is refused by name (`CANCEL_WOULD_LOSE_SPENT_CREDIT`) and writes nothing.

Every refusal is a 422 `{"outcome": "REQUIRE_HUMAN", "reason_codes": [...], "reason_vi": ...}`,
where `reason_vi` names the credit by what it was for and its amount. The controls: the same
cancellations with no remedy money, a free rewash (moves no money), and a remedy proposed but not
carried out all go through as before.
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


def _assert_refused(answer: Any, codes: list[str], *credits: str) -> None:
    assert answer.status_code == 422, answer.text
    detail = answer.json()["detail"]
    assert detail["outcome"] == "REQUIRE_HUMAN"
    assert detail["reason_codes"] == codes
    text = detail["reason_vi"]
    assert text.startswith("Không huỷ được") and "báo chủ tiệm" in text
    for credit in credits:
        assert credit in text, (credit, text)


# --- M3: an order a money remedy paid out on ------------------------------------------------------


@pytest.mark.parametrize("route", ["steps", "transition"])
@pytest.mark.parametrize("spent", [False, True], ids=["credit-unspent", "credit-spent"])
@pytest.mark.parametrize(
    "kind", [RemedyKind.LATE_DELIVERY_CREDIT, RemedyKind.DAMAGE_COMPENSATION], ids=str
)
def test_cancelling_an_order_a_remedy_paid_out_on_is_refused_and_writes_nothing(
    connection: Any,
    service: OperationsService,
    client: TestClient,
    kind: RemedyKind,
    spent: bool,
    route: str,
) -> None:
    store_id, staff, order_id, credit_id = _remedied(connection, kind)
    assert credit_id is not None
    if spent:
        _spend_on_new_order(service, connection, store_id, staff, credit_id)
    credit_before = _one(
        connection, "SELECT redeemed_at, row_version FROM remedy_credits WHERE id = %s", credit_id
    )
    _as(staff)
    shop_fault = {"custody_resolution": "SHOP_FAULT_NO_CHARGE"}
    if route == "steps":
        before = _order_row(connection, order_id)
        answer = _step(client, order_id, {"step": "CANCEL", **shop_fault})
    else:
        review = _post(
            client,
            f"/internal/v1/orders/{order_id}/transition",
            {"target": "CANCELLATION_REVIEW"},
            if_match=int(_read(client, order_id)["row_version"]),
        )
        assert review.status_code == 200, review.text
        before = _order_row(connection, order_id)
        answer = _post(
            client,
            f"/internal/v1/orders/{order_id}/transition",
            {"target": "CANCELLED", **shop_fault},
            if_match=int(before[2]),
        )
    _assert_refused(answer, ["CANCEL_AFTER_MONEY_REMEDY"], LABEL[kind])
    # Nothing written: no refund, the balance and the version as they were, the credit untouched.
    after = _order_row(connection, order_id)
    assert after == before and after[1] == "PAID" and after[3] == 0
    assert (
        _one(
            connection,
            "SELECT redeemed_at, row_version FROM remedy_credits WHERE id = %s",
            credit_id,
        )
        == credit_before
    )
    if route == "transition":
        # The way forward the order already has: withdraw the cancellation, finish the order.
        reopened = _step(client, order_id, {"step": "REOPEN"})
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["commercial"] == "ACTIVE"


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
    answer = _step(
        client, order_id, {"step": "CANCEL", "custody_resolution": "SHOP_FAULT_NO_CHARGE"}
    )
    assert answer.status_code == 200, answer.text
    assert (answer.json()["commercial"], answer.json()["balance"]) == ("CANCELLED", "REFUNDED")
    assert _order_row(connection, order_id)[3] == 1


# --- M7: an order whose bill spent a credit -------------------------------------------------------


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
        # Before anything happened to the goods: the plain cancellation.
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
def test_cancelling_an_order_whose_bill_spent_a_credit_is_refused_and_writes_nothing(
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
    before = _order_row(connection, order_id)
    spent_before = _one(
        connection,
        "SELECT redeemed_at, redeemed_quote_id, row_version FROM remedy_credits WHERE id = %s",
        credit_id,
    )

    answer = _step(client, order_id, body)
    _assert_refused(
        answer, ["CANCEL_WOULD_LOSE_SPENT_CREDIT"], LABEL[RemedyKind.LATE_DELIVERY_CREDIT]
    )
    assert _order_row(connection, order_id) == before and before[3] == 0
    assert (
        _one(
            connection,
            "SELECT redeemed_at, redeemed_quote_id, row_version FROM remedy_credits WHERE id = %s",
            credit_id,
        )
        == spent_before
    )


def test_an_order_with_no_credit_on_its_bill_still_cancels_without_charge(
    connection: Any, service: OperationsService, client: TestClient
) -> None:
    store_id, staff, _source, _credit = _remedied(connection, None)
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
    answer = _step(
        client, order.order_id, {"step": "CANCEL", "custody_resolution": "SHOP_FAULT_NO_CHARGE"}
    )
    assert answer.status_code == 200, answer.text
    assert (answer.json()["commercial"], answer.json()["balance"]) == ("CANCELLED", "REFUNDED")
