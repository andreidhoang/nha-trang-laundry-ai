"""GOODS-AND-DRAWER-009 (review M2) against real PostgreSQL: no door lets unpaid goods out.

The review's failure, in its own words: "Unpaid delivery order -> RELEASE -> RETURN succeeded;
a later payment is recorded as prepaid-delivery for goods already delivered". Here the matrix the
fix promises is driven through the real repositories, on orders brought to the shelf by the real
steps and paid by the real payment and account-charge commands:

    {counter, pickup, delivery, pickup + delivery}
      x {unpaid, deposit, paid in full, charged to the customer's account}
      x {RELEASE (step), RELEASED (per-axis route), RETURN succeeded, RETURN failed}

A door that lets the goods out does so; one that refuses names the reason and writes nothing -- no
order row version, no leg, no event. Then the review's exact story end to end, the account customer
who has not been charged yet, and laundry still in the machine.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegError,
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRefused,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.orders import (
    OrderGoodsMayNotLeaveError,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_RETURN,
    FulfillmentMode,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from test_customer_accounts import Shop, connection  # noqa: F401  (the fixture, re-exported)
from test_order_step_repository import _read, _step

MODES = (
    FulfillmentMode.SELF_DROP_SELF_COLLECT,
    FulfillmentMode.PICKUP_ONLY,
    FulfillmentMode.RETURN_ONLY,
    FulfillmentMode.PICKUP_AND_RETURN,
)
MONEY = ("unpaid", "deposit", "paid", "account")
LEAVES = {"paid", "account"}
DOORS = ("RELEASE", "RELEASED", "RETURN_SUCCEEDED", "RETURN_FAILED")


def _pay(shop: Shop, order_id: UUID, amount: int, *, collected: bool = False) -> None:
    version = _read(shop.connection, order_id, shop.counter).row_version
    PaymentRepository().record(
        shop.connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=version,
            amount_vnd=amount,
            method=PaymentMethod.TIEN_MAT,
            transfer_seen=False,
            bank_ref_last=None,
            collected_by_customer=collected,
            principal=shop.counter,
            correlation_id=uuid4(),
        ),
    )


def _on_the_shelf(shop: Shop, customer_id: UUID, mode: FulfillmentMode, money: str) -> UUID:
    """A finished order in `mode`, its money in the named state, by the counter's own commands."""

    order_id = shop.ready_order(customer_id, mode)
    remaining = _read(shop.connection, order_id, shop.counter).remaining_vnd
    assert remaining and remaining > 1
    if money == "deposit":
        _pay(shop, order_id, remaining // 2)
    elif money == "paid":
        _pay(shop, order_id, remaining)
    elif money == "account":
        shop.charge(order_id, collected=mode not in MODES_EXPECTING_RETURN)
    return order_id


def _snapshot(shop: Shop, order_id: UUID) -> tuple[Any, ...]:
    with shop.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT o.row_version, o.production_status, o.balance_status,
                   o.required_delivery_legs_succeeded,
                   (SELECT count(*) FROM delivery_legs l WHERE l.order_id = o.id),
                   (SELECT count(*) FROM domain_events e WHERE e.aggregate_id = o.id),
                   (SELECT count(*) FROM outbox_events x WHERE x.aggregate_id = o.id)
            FROM orders o WHERE o.id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


def _open_door(shop: Shop, order_id: UUID, door: str) -> None:
    """Try to let the goods out by one door. Raises whatever the repository refuses with."""

    version = _read(shop.connection, order_id, shop.counter).row_version
    if door == "RELEASE":
        _step(shop.connection, order_id, shop.counter, version, OrderStep.RELEASE)
    elif door == "RELEASED":
        OrderRepository().transition(
            shop.connection,
            OrderTransitionCommand(
                order_id,
                version,
                shop.counter,
                f"move-{uuid4().hex}",
                uuid4(),
                production_target=ProductionStatus.RELEASED,
            ),
        )
    else:
        outcome = (
            DeliveryLegOutcome.SUCCEEDED
            if door == "RETURN_SUCCEEDED"
            else DeliveryLegOutcome.FAILED
        )
        DeliveryLegRepository().record(
            shop.connection,
            RecordDeliveryLegCommand(
                order_id, DeliveryLegKind.RETURN, outcome, shop.counter, uuid4()
            ),
        )


@pytest.mark.parametrize("mode", MODES, ids=[mode.value for mode in MODES])
@pytest.mark.parametrize("money", MONEY)
def test_every_door_obeys_the_money_rule_for_every_mode(
    connection: Any,  # noqa: F811
    mode: FulfillmentMode,
    money: str,
) -> None:
    shop = Shop(connection)
    customer = shop.customer()
    shop.open(customer, 5_000_000)
    returns = mode in MODES_EXPECTING_RETURN
    for door in DOORS:
        order_id = _on_the_shelf(shop, customer, mode, money)
        before = _snapshot(shop, order_id)
        leg = door.startswith("RETURN")
        if leg and not returns:
            # Counter and pickup-only orders have no return trip at all, paid or not.
            with pytest.raises(DeliveryLegError, match=r"no delivery|no return leg") as caught:
                _open_door(shop, order_id, door)
            assert not isinstance(caught.value, DeliveryLegRefused)
            assert _snapshot(shop, order_id) == before
            continue
        if money in LEAVES:
            _open_door(shop, order_id, door)
            after = _snapshot(shop, order_id)
            if leg:
                assert after[4] == before[4] + 1, (door, after)
                assert after[3] is (door == "RETURN_SUCCEEDED")
            else:
                assert after[1] == "RELEASED", (door, after)
            continue
        if leg:
            with pytest.raises(DeliveryLegRefused) as refused:
                _open_door(shop, order_id, door)
            assert refused.value.reason_code == "DELIVERY_REQUIRES_PAYMENT"
            assert refused.value.decision == "DEC-023"
        else:
            with pytest.raises(OrderGoodsMayNotLeaveError) as refused_release:
                _open_door(shop, order_id, door)
            assert refused_release.value.reason_code == "RELEASE_REQUIRES_PAYMENT"
            assert refused_release.value.decision == "DEC-035"
        # Refused whole: the order, its legs, its events and its outbox are exactly as they were.
        assert _snapshot(shop, order_id) == before, (door, money)


def test_the_review_case_is_refused_then_goes_right_once_the_counter_takes_the_money(
    connection: Any,  # noqa: F811
) -> None:
    """Unpaid delivery -> RELEASE refused -> RETURN refused -> pay -> RELEASE -> RETURN -> close.

    The payment is recorded while the goods are still on the shelf, so "prepaid delivery" on the
    settlement is now true: the money came before the goods left.
    """

    shop = Shop(connection)
    customer = shop.customer()
    order_id = shop.ready_order(customer, FulfillmentMode.PICKUP_AND_RETURN)
    view = _read(connection, order_id, shop.counter)
    assert [item.step for item in view.next_steps if item.primary] == [OrderStep.TAKE_PAYMENT]
    assert not {OrderStep.RELEASE, OrderStep.DELIVERY_RETURN} & {i.step for i in view.next_steps}
    with pytest.raises(OrderGoodsMayNotLeaveError):
        _open_door(shop, order_id, "RELEASE")
    with pytest.raises(DeliveryLegRefused):
        _open_door(shop, order_id, "RETURN_SUCCEEDED")

    _pay(shop, order_id, view.remaining_vnd or 0)
    paid = _read(connection, order_id, shop.counter)
    assert paid.settlement_shape == "EXACT_PAYMENT_PREPAID_DELIVERY"
    assert [item.step for item in paid.next_steps if item.primary] == [OrderStep.RELEASE]
    _open_door(shop, order_id, "RELEASE")
    _open_door(shop, order_id, "RETURN_FAILED")
    _open_door(shop, order_id, "RETURN_SUCCEEDED")
    done = _step(
        connection,
        order_id,
        shop.counter,
        _read(connection, order_id, shop.counter).row_version,
        OrderStep.COMPLETE,
    ).view
    assert (done.commercial, done.balance) == ("COMPLETED", "PAID")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT (SELECT max(recorded_at) FROM order_payments WHERE order_id = %(o)s)
                 < (SELECT min(recorded_at) FROM delivery_legs
                    WHERE order_id = %(o)s AND leg_kind = 'RETURN')
            """,
            {"o": order_id},
        )
        assert cursor.fetchone() == (True,)


@pytest.mark.parametrize(
    "mode", [FulfillmentMode.RETURN_ONLY, FulfillmentMode.SELF_DROP_SELF_COLLECT]
)
def test_an_account_customer_leaves_by_the_account_charge_never_by_release_alone(
    connection: Any,  # noqa: F811
    mode: FulfillmentMode,
) -> None:
    """PAYMENT-002's half of `goods_may_leave` is the account charge: it re-decides the limit under
    the account's lock and writes the charge row that makes the balance `ON_ACCOUNT`. RELEASE on an
    account customer's uncharged order would let the goods out with no charge recorded, so it is
    refused like any unpaid order, and the charge is the way forward."""

    shop = Shop(connection)
    customer = shop.customer()
    shop.open(customer, 5_000_000)
    order_id = shop.ready_order(customer, mode)
    with pytest.raises(OrderGoodsMayNotLeaveError):
        _open_door(shop, order_id, "RELEASE")
    shop.charge(order_id, collected=mode not in MODES_EXPECTING_RETURN)
    _open_door(shop, order_id, "RELEASE")
    assert _read(connection, order_id, shop.counter).production == "RELEASED"


@pytest.mark.parametrize("money", ["unpaid", "paid"])
def test_laundry_still_in_the_machine_never_goes_out_with_the_courier(
    connection: Any,  # noqa: F811
    money: str,
) -> None:
    """The leg route used to accept a return on any ACTIVE order, washed or not."""

    shop = Shop(connection)
    customer = shop.customer()
    order_id = shop.ready_order(customer, FulfillmentMode.RETURN_ONLY)
    if money == "paid":
        _pay(shop, order_id, _read(connection, order_id, shop.counter).remaining_vnd or 0)
    view = _read(connection, order_id, shop.counter)
    _step(
        connection,
        order_id,
        shop.counter,
        view.row_version,
        OrderStep.REWASH,
        rewash_reason=next(
            iter(
                next(
                    item for item in view.next_steps if item.step is OrderStep.REWASH
                ).rewash_reasons
            )
        ),
    )
    assert _read(connection, order_id, shop.counter).production == "IN_PROCESS"
    before = _snapshot(shop, order_id)
    for door in ("RETURN_SUCCEEDED", "RETURN_FAILED"):
        with pytest.raises(DeliveryLegRefused) as refused:
            _open_door(shop, order_id, door)
        assert refused.value.reason_code == "GOODS_NOT_READY_FOR_HANDOVER"
    with pytest.raises(OrderStateError) as release:
        _open_door(shop, order_id, "RELEASED")
    assert not isinstance(release.value, OrderGoodsMayNotLeaveError)
    assert _snapshot(shop, order_id) == before
