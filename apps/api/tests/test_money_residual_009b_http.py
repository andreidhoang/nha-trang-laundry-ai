"""`MONEY-RESIDUAL-009B` over HTTP against real PostgreSQL: J2, J3, J4.

* **J2.** The cancellation's own `ORDER_STATE_TRANSITIONED` event and `ORDER_STATE_TRANSITION`
  audit row name the credits it reissued (`remedy_credits.reissued_credits`): the original and the
  new credit, read back from `remedy_credits` -- never `[]` when a reissue happened. Round 9 minted
  the new credit's id only after the order's own event was written, so the list was always empty.
* **J3.** A credit chain -- X issues c1, c1 is spent on Y, Y issues c2, c2 is spent on Z -- ends the
  customer at the same total whichever order is cancelled first: cash paid, less cash refunded,
  less the face value of the credits still live. Every permutation of a two-order and a three-order
  chain is walked through the API. Where an earlier cancellation's netting was capped (the credit
  was worth more than the order's ledger), the later cancellation would give the credit back a
  second time: it is refused, `CREDIT_CHAIN_NOT_NETTED`, with nothing written.
* **J4.** The refund sheet's preview is what the press executes. The press carries the preview's
  four figures (`expected_cancellation_money`); a credit spent elsewhere between the read and the
  press changes them without changing the order's row version, and the cancellation is then
  refused 409 `CANCELLATION_MONEY_CHANGED` with a Vietnamese sentence and the figures it would now
  execute; nothing is written; the re-read preview, pressed again, goes through.
"""

from __future__ import annotations

import itertools
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
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.catalog import Unit
from nha_trang_laundry_domain.remedies import RemedyKind
from test_money_lifecycle_009_http import (
    EXPECTED_KEYS,
    SHOP_FAULT,
    _cancel,
    _pay,
    _read,
    _remedied,
    _spend_on_new_order,
    _step,
)
from test_prepaid_dropoff_http import CSRF, _as, _post

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import FixtureLine
from test_remedies import _approve, _execute, _propose, _shop


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


def _rows(connection: Any, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(row) for row in cursor.fetchall()]


def _cancellation_records(connection: Any, order_id: UUID) -> tuple[Any, Any]:
    """The cancellation's own event payload and audit details (`remedy_credits`)."""

    [(event, audit)] = _rows(
        connection,
        """
        SELECT e.payload -> 'remedy_credits', a.details -> 'remedy_credits'
        FROM domain_events e
        JOIN audit_events a
          ON a.aggregate_id = e.aggregate_id AND a.correlation_id = e.correlation_id
         AND a.action = 'ORDER_STATE_TRANSITION'
         AND a.details -> 'remedy_credits' IS NOT NULL
        WHERE e.aggregate_id = %s AND e.event_type = 'ORDER_STATE_TRANSITIONED'
          AND e.payload ->> 'target' = 'CANCELLED'
        """,
        order_id,
    )
    return event, audit


# --- J2: the cancellation names the credits it reissued ---------------------------------------


@pytest.mark.parametrize(
    ("route", "paid"),
    [("steps", 0), ("steps", 50_000), ("transition", 50_000)],
)
def test_the_cancellation_event_and_audit_name_every_reissued_credit(
    connection: Any, service: OperationsService, client: TestClient, route: str, paid: int
) -> None:
    store_id, staff, _source, credit_id = _remedied(connection, RemedyKind.LATE_DELIVERY_CREDIT)
    assert credit_id is not None
    order_id = _spend_on_new_order(service, connection, store_id, staff, credit_id).order_id
    _as(staff)
    if route == "transition" or paid:
        assert _step(client, order_id, {"step": "RECEIVE", "slot_approved": True}).status_code == (
            200
        )
    if paid:
        _pay(client, order_id, paid)
    body = (
        {}
        if route == "steps" and not paid
        else {"custody_resolution": "RETURNED_UNWASHED_REFUNDED"}
    )
    answer = _cancel(client, order_id, route, body)
    assert answer.status_code == 200, answer.text
    [(new_id,)] = _rows(
        connection, "SELECT id FROM remedy_credits WHERE reissue_of = %s", credit_id
    )
    event, audit = _cancellation_records(connection, order_id)
    named = [{"reissue_of": str(credit_id), "credit_id": str(new_id)}]
    assert event["reissued_credits"] == named
    assert audit["reissued_credits"] == named
    assert event == audit
    # The credit's own event names the same cancellation; the two records agree.
    [(reissue_order,)] = _rows(
        connection,
        "SELECT payload ->> 'order_id' FROM domain_events "
        "WHERE aggregate_id = %s AND event_type = 'REMEDY_CREDIT_REISSUED'",
        new_id,
    )
    assert reissue_order == str(order_id)


# --- J3: credit chains --------------------------------------------------------------------------


def _released_and_paid(client: TestClient, order_id: UUID) -> None:
    """Washed, paid in full and released through the counter's own steps; still ACTIVE."""

    for step in ("RECEIVE", "START_WASH", "QUALITY_CHECK", "MARK_READY"):
        extra = {"slot_approved": True} if step == "RECEIVE" else {}
        answer = _step(client, order_id, {"step": step, **extra})
        assert answer.status_code == 200, (step, answer.text)
    remaining = int(_read(client, order_id)["remaining_vnd"])
    if remaining:
        _pay(client, order_id, remaining)
    released = _step(client, order_id, {"step": "RELEASE"})
    assert released.status_code == 200, released.text


def _damage_credit(
    connection: Any, store_id: UUID, staff: Any, order_id: UUID, amount: int
) -> UUID:
    """An incident on a released order and a damage compensation executed on its first line --
    through the owner's approval when the counter's authority does not reach."""

    now = datetime.now(UTC)
    incident_id = (
        IncidentRepository()
        .open_from_counter(
            connection,
            StaffIncidentOpenCommand(
                store_id=store_id,
                order_id=order_id,
                evidence_summary="Áo bị phai màu sau khi giặt",
                actor_id=staff.staff_user_id,
                correlation_id=uuid4(),
                opened_at=now,
            ),
            principal=staff,
        )
        .incident_id
    )
    [(line_id,)] = _rows(
        connection,
        """
        SELECT line ->> 'line_id'
        FROM orders o
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        CROSS JOIN LATERAL jsonb_array_elements(r.snapshot -> 'lines') AS line
        WHERE o.id = %s
        LIMIT 1
        """,
        order_id,
    )
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id=line_id,
        amount_vnd=amount,
        proposed_at=now,
    )
    if proposal.approval_id is not None:
        _approve(connection, store_id, proposal.approval_id, datetime.now(UTC))
    credit = _execute(connection, proposal.proposal_id, staff, at=datetime.now(UTC)).credit_id
    assert credit is not None
    return UUID(str(credit))


def _customer_total(connection: Any, orders: tuple[UUID, ...]) -> tuple[int, int, int]:
    """Cash paid on the chain's orders, cash refunded, and the face value of the credits issued
    from them that are still live (neither spent nor voided)."""

    ids = list(orders)
    [(paid,)] = _rows(
        connection,
        "SELECT coalesce(sum(amount_vnd), 0) FROM order_payments WHERE order_id = ANY(%s)",
        ids,
    )
    [(refunded,)] = _rows(
        connection,
        "SELECT coalesce(sum(refunded_amount_vnd), 0) FROM order_refunds WHERE order_id = ANY(%s)",
        ids,
    )
    [(live,)] = _rows(
        connection,
        "SELECT coalesce(sum(amount_vnd), 0) FROM remedy_credits "
        "WHERE issued_from_order_id = ANY(%s) AND redeemed_at IS NULL AND voided_at IS NULL",
        ids,
    )
    return int(paid), int(refunded), int(live)


def _cancel_any(client: TestClient, order_id: UUID) -> Any:
    """Cancel without charge from wherever the order stands, as the counter would."""

    view = _read(client, order_id)
    resolution = (
        {}
        if view["intake"] in {"AWAITING_HANDOFF"} and view["production"] == "NOT_STARTED"
        else SHOP_FAULT
    )
    return _step(client, order_id, {"step": "CANCEL", **resolution})


def _chain(
    connection: Any, service: OperationsService, client: TestClient, length: int, *, capped: bool
) -> tuple[tuple[UUID, ...], int]:
    """A credit chain of `length` orders; returns the orders (X, Y[, Z]) and the cash taken.

    Uncapped: X (110.000 paid) issues c1 = the late-delivery 11.000 on Y; Y (89.000 paid) issues
    c2 = 30.000 on Z. Capped: X is a 20.000 shirt (30.000 paid) whose damage credit c1 = 80.000 is
    spent on Y; Y (20.000 paid) issues c2 = 30.000 on Z -- each credit worth more than the ledger of
    the order it came from, so cancelling that order first nets only part of it.
    """

    if capped:
        shirt = FixtureLine("line-1", "DC_SHIRT", Unit.ITEM, "1", 20_000, 20_000)
        store_id, staff, x_id, incident_id = _shop(connection, lines=(shirt,))
        proposal = _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=80_000,
        )
        c1 = _execute(connection, proposal.proposal_id, staff).credit_id
    else:
        store_id, staff, x_id, c1 = _remedied(connection, RemedyKind.LATE_DELIVERY_CREDIT)
    assert c1 is not None
    _as(staff)
    y_id = _spend_on_new_order(service, connection, store_id, staff, UUID(str(c1))).order_id
    if length == 2:
        return (x_id, y_id), 0
    _released_and_paid(client, y_id)
    c2 = _damage_credit(connection, store_id, staff, y_id, 30_000)
    z_id = _spend_on_new_order(service, connection, store_id, staff, c2).order_id
    return (x_id, y_id, z_id), 0


def _run(
    connection: Any,
    service: OperationsService,
    client: TestClient,
    length: int,
    capped: bool,
    order: tuple[int, ...],
) -> tuple[str, Any]:
    orders, _ = _chain(connection, service, client, length, capped=capped)
    for index in order:
        answer = _cancel_any(client, orders[index])
        if answer.status_code != 200:
            assert answer.status_code == 409, answer.text
            detail = answer.json()["detail"]
            assert detail["reason_code"] == "CREDIT_CHAIN_NOT_NETTED"
            assert "trả khách hai lần" in detail["message_vi"]
            # Nothing was written: the refused order stands as it was.
            assert _read(client, orders[index])["commercial"] != "CANCELLED"
            return "refused", "XYZ"[index]
    paid, refunded, live = _customer_total(connection, orders)
    return "ok", paid - refunded - live


@pytest.mark.parametrize("order", list(itertools.permutations(range(2))), ids=str)
def test_a_two_order_chain_ends_at_the_same_total_whichever_is_cancelled_first(
    connection: Any, service: OperationsService, client: TestClient, order: tuple[int, ...]
) -> None:
    assert _run(connection, service, client, 2, False, order) == ("ok", 0)


@pytest.mark.parametrize(
    ("order", "expected"),
    [((0, 1), ("refused", "Y")), ((1, 0), ("ok", 0))],
    ids=str,
)
def test_a_two_order_chain_whose_first_netting_was_capped_refuses_the_second_cancellation(
    connection: Any,
    service: OperationsService,
    client: TestClient,
    order: tuple[int, ...],
    expected: tuple[str, Any],
) -> None:
    assert _run(connection, service, client, 2, True, order) == expected


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))), ids=str)
def test_a_three_order_chain_ends_at_the_same_total_in_every_order(
    connection: Any, service: OperationsService, client: TestClient, order: tuple[int, ...]
) -> None:
    assert _run(connection, service, client, 3, False, order) == ("ok", 0)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        # X first nets only 30.000 of c1 (80.000): Y may no longer reissue it.
        ((0, 1, 2), ("refused", "Y")),
        ((0, 2, 1), ("refused", "Y")),
        # Y first nets only its 20.000 of c2 (30.000): Z may no longer reissue it.
        ((1, 0, 2), ("refused", "Z")),
        ((1, 2, 0), ("refused", "Z")),
        ((2, 0, 1), ("refused", "Y")),
        # From the end of the chain back to its start, nothing is ever netted short.
        ((2, 1, 0), ("ok", 0)),
    ],
    ids=str,
)
def test_a_capped_three_order_chain_refuses_exactly_the_cancellations_that_would_pay_twice(
    connection: Any,
    service: OperationsService,
    client: TestClient,
    order: tuple[int, ...],
    expected: tuple[str, Any],
) -> None:
    assert _run(connection, service, client, 3, True, order) == expected


def test_the_preview_says_the_cancellation_would_be_refused(
    connection: Any, service: OperationsService, client: TestClient
) -> None:
    (x_id, y_id), _ = _chain(connection, service, client, 2, capped=True)
    assert _cancel_any(client, x_id).status_code == 200
    preview = _read(client, y_id)["cancellation_money"]
    assert preview["stage"] == "PREVIEW"
    assert preview["refusal"] == "CREDIT_CHAIN_NOT_NETTED"
    assert len(preview["lines_vi"]) == 1 and "báo chủ tiệm" in preview["lines_vi"][0]


# --- J4: the press carries the preview ---------------------------------------------------------


def _figures(preview: dict[str, Any]) -> dict[str, int]:
    return {key: int(preview[key]) for key in EXPECTED_KEYS}


@pytest.mark.parametrize("route", ["steps", "transition"])
def test_a_credit_spent_under_the_sheet_refuses_the_press_and_the_fresh_preview_goes_through(
    connection: Any, service: OperationsService, client: TestClient, route: str
) -> None:
    store_id, staff, order_id, credit_id = _remedied(connection, RemedyKind.DAMAGE_COMPENSATION)
    assert credit_id is not None
    _as(staff)
    shown = _read(client, order_id)
    stale = _figures(shown["cancellation_money"])
    assert stale == {
        "refund_vnd": 110_000,
        "netted_vnd": 0,
        "voided_vnd": 80_000,
        "reissued_vnd": 0,
    }
    # Another phone spends the credit on a new order: this order's row version does not move.
    _spend_on_new_order(service, connection, store_id, staff, credit_id)
    assert _read(client, order_id)["row_version"] == shown["row_version"]
    before = _rows(
        connection,
        "SELECT commercial_status, balance_status, row_version FROM orders WHERE id = %s",
        order_id,
    )
    if route == "steps":
        refused = _post(
            client,
            f"/internal/v1/orders/{order_id}/steps",
            {
                "step": "CANCEL",
                **SHOP_FAULT,
                "refund_method": "TIEN_MAT",
                "expected_cancellation_money": stale,
            },
            if_match=int(shown["row_version"]),
        )
    else:
        review = _post(
            client,
            f"/internal/v1/orders/{order_id}/transition",
            {"target": "CANCELLATION_REVIEW"},
            if_match=int(shown["row_version"]),
        )
        assert review.status_code == 200, review.text
        before = _rows(
            connection,
            "SELECT commercial_status, balance_status, row_version FROM orders WHERE id = %s",
            order_id,
        )
        refused = _post(
            client,
            f"/internal/v1/orders/{order_id}/transition",
            {
                "target": "CANCELLED",
                **SHOP_FAULT,
                "refund_method": "TIEN_MAT",
                "expected_cancellation_money": stale,
            },
            if_match=int(_read(client, order_id)["row_version"]),
        )
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["reason_code"] == "CANCELLATION_MONEY_CHANGED"
    assert detail["message_vi"].startswith("Số tiền hoàn hoặc khoản giảm trừ của đơn vừa thay đổi")
    fresh = {"refund_vnd": 30_000, "netted_vnd": 80_000, "voided_vnd": 0, "reissued_vnd": 0}
    assert detail["cancellation_money"] == fresh
    # Nothing was written: no refund, no void, the order as it was.
    assert (
        _rows(
            connection,
            "SELECT commercial_status, balance_status, row_version FROM orders WHERE id = %s",
            order_id,
        )
        == before
    )
    assert _rows(
        connection, "SELECT count(*) FROM order_refunds WHERE order_id = %s", order_id
    ) == [(0,)]
    # The re-read preview is the fresh figure, and pressing it goes through.
    assert _figures(_read(client, order_id)["cancellation_money"]) == fresh
    answer = (
        _cancel(client, order_id, route, SHOP_FAULT)
        if route == "steps"
        else _post(
            client,
            f"/internal/v1/orders/{order_id}/transition",
            {
                "target": "CANCELLED",
                **SHOP_FAULT,
                "refund_method": "TIEN_MAT",
                "expected_cancellation_money": fresh,
            },
            if_match=int(_read(client, order_id)["row_version"]),
        )
    )
    assert answer.status_code == 200, answer.text
    assert _rows(
        connection,
        "SELECT refunded_amount_vnd, netted_remedy_vnd FROM order_refunds WHERE order_id = %s",
        order_id,
    ) == [(30_000, 80_000)]


def test_a_press_without_the_preview_is_refused_when_remedy_money_moves(
    connection: Any, client: TestClient
) -> None:
    _store, staff, order_id, credit_id = _remedied(connection, RemedyKind.DAMAGE_COMPENSATION)
    assert credit_id is not None
    _as(staff)
    refused = _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {"step": "CANCEL", **SHOP_FAULT, "refund_method": "TIEN_MAT"},
        if_match=int(_read(client, order_id)["row_version"]),
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason_code"] == "CANCELLATION_MONEY_CHANGED"
    assert refused.json()["detail"]["cancellation_money"]["voided_vnd"] == 80_000
    assert _rows(
        connection, "SELECT voided_at IS NULL FROM remedy_credits WHERE id = %s", credit_id
    ) == [(True,)]


def test_figures_are_refused_on_a_step_that_does_not_cancel_and_matched_when_nothing_moves(
    connection: Any, client: TestClient
) -> None:
    _store, staff, order_id, _credit = _remedied(connection, None)
    _as(staff)
    version = int(_read(client, order_id)["row_version"])
    figures = {"refund_vnd": 110_000, "netted_vnd": 0, "voided_vnd": 0, "reissued_vnd": 0}
    wrong_step = _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {"step": "HOLD", "expected_cancellation_money": figures},
        if_match=version,
    )
    assert wrong_step.status_code == 422, wrong_step.text
    # No remedy money: the figures are optional, and when sent they must be the refund.
    moved = _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {
            "step": "CANCEL",
            **SHOP_FAULT,
            "refund_method": "TIEN_MAT",
            "expected_cancellation_money": {**figures, "refund_vnd": 100_000},
        },
        if_match=version,
    )
    assert moved.status_code == 409 and moved.json()["detail"]["cancellation_money"] == figures
    answer = _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {
            "step": "CANCEL",
            **SHOP_FAULT,
            "refund_method": "TIEN_MAT",
            "expected_cancellation_money": figures,
        },
        if_match=version,
    )
    assert answer.status_code == 200, answer.text


def test_the_figures_are_part_of_the_cancellation_key(connection: Any, client: TestClient) -> None:
    _store, staff, order_id, _credit = _remedied(connection, RemedyKind.DAMAGE_COMPENSATION)
    _as(staff)
    version = int(_read(client, order_id)["row_version"])
    figures = _figures(_read(client, order_id)["cancellation_money"])
    key = f"cancel-{uuid4().hex}"
    body = {"step": "CANCEL", **SHOP_FAULT, "refund_method": "TIEN_MAT"}
    path = f"/internal/v1/orders/{order_id}/steps"
    first = _post(
        client, path, {**body, "expected_cancellation_money": figures}, key=key, if_match=version
    )
    assert first.status_code == 200, first.text
    again = _post(
        client, path, {**body, "expected_cancellation_money": figures}, key=key, if_match=version
    )
    assert again.status_code == 200 and again.json()["replayed"] is True
    other = {**figures, "voided_vnd": figures["voided_vnd"] - 1}
    conflict = _post(
        client, path, {**body, "expected_cancellation_money": other}, key=key, if_match=version
    )
    assert conflict.status_code == 409 and conflict.json()["detail"] == "IDEMPOTENCY_CONFLICT"
