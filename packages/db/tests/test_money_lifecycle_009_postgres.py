"""`MONEY-LIFECYCLE-009` against real PostgreSQL: the storage fee, part payments and cancellations.

Review finding **M1** (P0): the storage fee is recomputed at every read and fixed only by the
payment that settles the order. A part payment could cover part of the fee, and a later event that
lowers the recomputed fee -- a waiver, a rewash that restarts the free days, a cancellation, the
owner's disposal, the owner withdrawing the policy -- then left the ledger holding more than the
order "owed": `payment_position` raised on the order read, the board list, the waiting list and the
day's export. And a waiver that left what is owed equal to what was paid stranded the order partly
paid with nothing left to take, so the goods could never leave.

The matrix below is the review's: the fee accrued (day 25, 25.000 ₫ on a 110.000 ₫ bag), then a part
payment (a) below the quoted total and (b) covering 3.000 ₫ of the fee, then each event. For every
order, with the policy in force and again after the owner withdraws it: the order read, the board,
the waiting list, the signed export, the store report and the evening summary all succeed and agree.
Then the two stranded shapes are walked out through the counter's own commands: the waiver that
settles, and the 0 đồng settlement of a ledger that already covers everything.

`DEC-047` (2026-09-30): a hold is not such an event. It pauses the fee where it stood and RESUME
continues the count (`test_a_hold_pauses_the_fee_...` below, the verifier's reproduction).

Harness step (documented, as in `test_unclaimed_laundry.py`): the laundry's ready time is moved
back with one SQL statement (`_age_ready`); nothing else can make an order 25 days old in a test.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.daily_summary import DailySummaryRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository, PaymentStateError
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.reports import ReportKey, ReportRepository
from nha_trang_laundry_db.settlement import CollectionCommand, SettlementRepository
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_db.unclaimed import (
    UnclaimedAuthorizationError,
    UnclaimedRefused,
    UnclaimedRepository,
    WaiverCommand,
)
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    RewashReason,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import ChargeKind, PaymentMethod
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from nha_trang_laundry_domain.unclaimed import StorageFeeStatus, withdrawal_document
from test_customer_notice import notice_payload
from test_export_payments import _age_ready, _body, _ready_for, _release
from test_export_range import _request
from test_order_step_repository import TOTAL_VND, _read, _step
from test_order_step_repository import _staff as _operator
from test_sanitized_export import _approve, _local_date, _Shop
from test_unclaimed_laundry import _attempt, _shop_day
from test_unclaimed_laundry import policy_payload as storage_payload

CASH = PaymentMethod.TIEN_MAT
#: Day 25: five started days past the free twenty, 5.000 ₫ each (cap 55.000 ₫ on 110.000 ₫).
FEE_DAY_25 = 25_000
#: Day 61, capped at half the quoted total.
FEE_CAPPED = TOTAL_VND // 2
DEPOSIT = 50_000  # (a) below the quoted total
PART_OF_FEE = TOTAL_VND + 3_000  # (b) the quoted total and 3.000 ₫ of the fee


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _pay(
    connection: Any, order_id: UUID, staff: Any, amount: int, *, collected: bool = False
) -> Any:
    return PaymentRepository().record(
        connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, staff).row_version,
            amount_vnd=amount,
            method=CASH,
            transfer_seen=False,
            bank_ref_last=None,
            collected_by_customer=collected,
            principal=staff,
            correlation_id=uuid4(),
        ),
    )


def _waive(connection: Any, order_id: UUID, staff: Any) -> Any:
    return UnclaimedRepository().waive_storage_fee(
        connection,
        WaiverCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, staff).row_version,
            principal=staff,
            idempotency_key=f"waive-{uuid4().hex}",
            correlation_id=uuid4(),
            reason="khách quen",
        ),
    )


def _do(connection: Any, order_id: UUID, staff: Any, step: OrderStep, **extra: Any) -> Any:
    return _step(
        connection, order_id, staff, _read(connection, order_id, staff).row_version, step, **extra
    ).view


def _rows(connection: Any, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(row) for row in cursor.fetchall()]


#: What each event leaves the order owing and holding, with the policy in force (published) --
#: (owed, paid, balance) for the (a) deposit and the (b) part-of-the-fee payment.
EXPECTED: dict[str, dict[str, tuple[int, int, str]]] = {
    "none": {
        "a": (TOTAL_VND + FEE_DAY_25, DEPOSIT, "PARTIALLY_PAID"),
        "b": (TOTAL_VND + FEE_DAY_25, PART_OF_FEE, "PARTIALLY_PAID"),
    },
    # The waiver takes off the unpaid part; in (b) that leaves nothing owed, so it settles.
    "waiver": {
        "a": (TOTAL_VND, DEPOSIT, "PARTIALLY_PAID"),
        "b": (PART_OF_FEE, PART_OF_FEE, "PAID"),
    },
    # DEC-047 changed this row: it used to pin the hold as erasing the unpaid fee ((a) owed the
    # quoted total alone), the "hold, take the quoted total, settle" bypass of the approver-only
    # waiver. A hold pauses the fee where it stood; nothing of it is erased.
    "hold": {
        "a": (TOTAL_VND + FEE_DAY_25, DEPOSIT, "PARTIALLY_PAID"),
        "b": (TOTAL_VND + FEE_DAY_25, PART_OF_FEE, "PARTIALLY_PAID"),
    },
    "hold_then_resume": {
        "a": (TOTAL_VND + FEE_DAY_25, DEPOSIT, "PARTIALLY_PAID"),
        "b": (TOTAL_VND + FEE_DAY_25, PART_OF_FEE, "PARTIALLY_PAID"),
    },
    "rewash_in_progress": {
        "a": (TOTAL_VND, DEPOSIT, "PARTIALLY_PAID"),
        "b": (PART_OF_FEE, PART_OF_FEE, "PARTIALLY_PAID"),
    },
    # Ready again today: the free days restarted, and the paid part of the old fee is kept.
    "rewash_ready_again": {
        "a": (TOTAL_VND, DEPOSIT, "PARTIALLY_PAID"),
        "b": (PART_OF_FEE, PART_OF_FEE, "PARTIALLY_PAID"),
    },
    "cancellation_review": {
        "a": (TOTAL_VND, DEPOSIT, "PARTIALLY_PAID"),
        "b": (PART_OF_FEE, PART_OF_FEE, "PARTIALLY_PAID"),
    },
    # Cancelled, the shop's fault: everything the ledger holds goes back.
    "cancelled_shop_fault": {
        "a": (TOTAL_VND, DEPOSIT, "REFUNDED"),
        "b": (PART_OF_FEE, PART_OF_FEE, "REFUNDED"),
    },
    # Thanh lý at day 61: what was paid is kept, the rest written off; nothing is owed after.
    "disposed": {
        "a": (TOTAL_VND, DEPOSIT, "PARTIALLY_PAID"),
        "b": (PART_OF_FEE, PART_OF_FEE, "PARTIALLY_PAID"),
    },
}


def _apply(connection: Any, event: str, order_id: UUID, staff: Any, shop: _Shop) -> None:
    if event == "none":
        return
    if event == "waiver":
        _waive(connection, order_id, shop.requester)
    elif event == "hold":
        _do(connection, order_id, staff, OrderStep.HOLD)
    elif event == "hold_then_resume":
        _do(connection, order_id, staff, OrderStep.HOLD)
        _do(connection, order_id, staff, OrderStep.RESUME)
    elif event == "rewash_in_progress":
        _do(connection, order_id, staff, OrderStep.REWASH, rewash_reason=RewashReason.NOT_CLEAN)
    elif event == "rewash_ready_again":
        _do(connection, order_id, staff, OrderStep.REWASH, rewash_reason=RewashReason.NOT_CLEAN)
        _do(connection, order_id, staff, OrderStep.QUALITY_CHECK)
        _do(connection, order_id, staff, OrderStep.MARK_READY)
    elif event == "cancellation_review":
        OrderRepository().transition(
            connection,
            OrderTransitionCommand(
                order_id,
                _read(connection, order_id, staff).row_version,
                staff,
                f"review-{uuid4().hex}",
                uuid4(),
                commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW,
            ),
        )
    elif event == "cancelled_shop_fault":
        # The two other resolutions are refused on the order's own record -- the laundry was
        # received and washed -- and refusing them breaks nothing.
        for refused in (
            CustodyResolution.NOT_RECEIVED,
            CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        ):
            with pytest.raises(OrderStateError):
                _do(connection, order_id, staff, OrderStep.CANCEL, custody_resolution=refused)
        _do(
            connection,
            order_id,
            staff,
            OrderStep.CANCEL,
            custody_resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
            # GOODS-AND-DRAWER-009: money goes back (a deposit or part of the fee was paid), so
            # the cancellation says how.
            refund_method=PaymentMethod.TIEN_MAT,
        )
    elif event == "disposed":
        _age_ready(connection, order_id, 36)  # day 61
        for days_ago, hour in ((5, 9), (5, 15), (4, 10)):
            _attempt(connection, order_id, staff, at=_shop_day(days_ago, hour))
        UnclaimedRepository().dispose(
            connection,
            _disposal(connection, order_id, shop),
        )
    else:  # pragma: no cover - the table above names every event
        raise AssertionError(event)


def _disposal(connection: Any, order_id: UUID, shop: _Shop) -> Any:
    from nha_trang_laundry_db.unclaimed import DisposalCommand

    return DisposalCommand(
        order_id=order_id,
        expected_row_version=_read(connection, order_id, shop.owner).row_version,
        principal=shop.owner,
        idempotency_key=f"dispose-{uuid4().hex}",
        correlation_id=uuid4(),
    )


def _board(connection: Any, shop: _Shop, staff: Any) -> dict[UUID, Any]:
    with connection.cursor() as cursor:
        listed = OrderRepository.list_for_store(
            cursor, store_id=shop.store_id, principal=staff, limit=200
        )
    return {view.order_id: view for view in listed}


def _waiting(connection: Any, shop: _Shop, staff: Any) -> dict[UUID, Any]:
    with connection.cursor() as cursor:
        found = UnclaimedRepository.list_awaiting_pickup(
            cursor, store_id=shop.store_id, principal=staff, as_of=datetime.now(UTC)
        )
    return {row.order_id: row for row in found.orders}


def _check_every_read(
    connection: Any,
    shop: _Shop,
    staff: Any,
    cases: dict[tuple[str, str], UUID],
    expected: dict[str, dict[str, tuple[int, int, str]]] | None,
) -> dict[UUID, Any]:
    """Read every order each way the shop reads it, and require them to agree.

    `expected` is the table above while the policy is in force; after the withdrawal every fee not
    yet fixed falls to what was already paid, so what is owed is the quoted total or the ledger,
    whichever is larger -- never less than was paid.
    """

    board = _board(connection, shop, staff)
    waiting = _waiting(connection, shop, staff)
    reads: dict[UUID, Any] = {}
    for (event, part), order_id in cases.items():
        view = _read(connection, order_id, staff)
        reads[order_id] = view
        assert view.owed_vnd is not None and view.remaining_vnd is not None
        assert view.owed_vnd >= view.paid_vnd, (event, part)
        if expected is not None:
            assert (view.owed_vnd, view.paid_vnd, view.balance.value) == expected[event][part], (
                event,
                part,
            )
        elif view.balance.value != "PAID":
            assert view.owed_vnd == max(TOTAL_VND, view.paid_vnd), (event, part)
        charges = {charge.kind: charge.amount_vnd for charge in view.charges}
        assert charges[ChargeKind.QUOTED_TOTAL] + charges.get(ChargeKind.STORAGE_FEE, 0) == (
            view.owed_vnd
        )
        listed = board[order_id]
        assert (listed.owed_vnd, listed.paid_vnd, listed.remaining_vnd) == (
            view.owed_vnd,
            view.paid_vnd,
            view.remaining_vnd,
        ), (event, part)
        if order_id in waiting:
            assert waiting[order_id].remaining_vnd == view.remaining_vnd, (event, part)
    return reads


def test_no_read_breaks_and_every_read_agrees_after_each_event_that_lowers_the_fee(
    connection: psycopg.Connection[Any],
) -> None:
    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    owner = shop.owner
    publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())
    publish_storage_policy(connection, actor_id=owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    cases: dict[tuple[str, str], UUID] = {}
    try:
        for part, paid in (("a", DEPOSIT), ("b", PART_OF_FEE)):
            for event in EXPECTED:
                order_id = _ready_for(connection, shop, staff, None)
                _age_ready(connection, order_id, 25)
                assert _read(connection, order_id, staff).owed_vnd == TOTAL_VND + FEE_DAY_25
                _pay(connection, order_id, staff, paid)
                _apply(connection, event, order_id, staff, shop)
                cases[(event, part)] = order_id

        published = _check_every_read(connection, shop, staff, cases, EXPECTED)

        # The waiver that settled (b): the paid part of the fee is fixed beside the settlement,
        # as what the customer paid (`0066`'s ALREADY_PAID), and only the unpaid part was waived.
        settled = cases[("waiver", "b")]
        assert _rows(
            connection,
            "SELECT amount_vnd, basis, days_waiting, chargeable_days, policy_version_id "
            "FROM order_storage_fees WHERE order_id = %s",
            settled,
        ) == [(3_000, "ALREADY_PAID", None, None, None)]
        assert _rows(
            connection,
            "SELECT waived_amount_vnd FROM storage_fee_waivers WHERE order_id = %s",
            settled,
        ) == [(FEE_DAY_25 - 3_000,)]
        assert _rows(
            connection,
            "SELECT expected_total_vnd, paid_amount_vnd, settlement_shape FROM order_settlements "
            "WHERE order_id = %s",
            settled,
        ) == [(PART_OF_FEE, PART_OF_FEE, "EXACT_PAYMENT_PREPAID_SELF_COLLECTION")]
        # (a)'s waiver waived the whole fee; nothing of it had been paid.
        assert _rows(
            connection,
            "SELECT waived_amount_vnd FROM storage_fee_waivers WHERE order_id = %s",
            cases[("waiver", "a")],
        ) == [(FEE_DAY_25,)]
        # Disposal: what was owed then, kept and written off -- and the order read after it.
        assert _rows(
            connection,
            "SELECT owed_vnd, storage_fee_vnd, kept_vnd, written_off_vnd FROM order_disposals "
            "WHERE order_id = %s",
            cases[("disposed", "b")],
        ) == [
            (
                TOTAL_VND + FEE_CAPPED,
                FEE_CAPPED,
                PART_OF_FEE,
                TOTAL_VND + FEE_CAPPED - PART_OF_FEE,
            )
        ]
        # The refunds return exactly the ledger, fee part included.
        for part, paid in (("a", DEPOSIT), ("b", PART_OF_FEE)):
            assert _rows(
                connection,
                "SELECT refunded_amount_vnd FROM order_refunds WHERE order_id = %s",
                cases[("cancelled_shop_fault", part)],
            ) == [(paid,)]

        # The owner withdraws the policy: every fee not yet fixed falls; none below what was paid.
        publish_storage_policy(
            connection, actor_id=owner.staff_user_id, payload=withdrawal_document()
        )
        withdrawn = _check_every_read(connection, shop, staff, cases, None)
        held = withdrawn[cases[("none", "b")]]
        assert (held.owed_vnd, held.remaining_vnd) == (PART_OF_FEE, 0)
        with connection.cursor() as cursor:
            storage = UnclaimedRepository.read_order_storage(
                cursor, order_id=cases[("none", "b")], principal=staff, as_of=datetime.now(UTC)
            )
        assert (storage.fee.status, storage.fee.amount_vnd) == (
            StorageFeeStatus.ALREADY_PAID,
            3_000,
        )
        assert storage.waiver_effect is None  # nothing unpaid to waive

        # The signed export, the store report and the evening summary read the same orders.
        created = _request(connection, shop, _local_date(now), None)
        produced = _release(connection, shop, created, _approve(connection, shop, created))
        rows = _body(produced.content_csv)
        for (event, part), order_id in cases.items():
            row = rows[str(order_id)]
            view = withdrawn[order_id]
            remaining = 0 if view.commercial is CommercialOrderStatus.CANCELLED else None
            assert (row["owed_vnd"], row["paid_vnd"], row["remaining_vnd"]) == (
                str(view.owed_vnd),
                str(view.paid_vnd),
                str(view.remaining_vnd if remaining is None else remaining),
            ), (event, part)
        with connection.cursor() as cursor:
            report = ReportRepository.store_report(
                cursor,
                store_id=shop.store_id,
                principal=owner,
                policy=STANDARD_WASH_SLA,
                from_date=_local_date(now),
                to_date=_local_date(now),
                as_of=datetime.now(UTC),
            )
        figures = {figure.key: figure for figure in report.summary.figures}
        assert figures[ReportKey.MONEY_COLLECTED].numerator == sum(
            view.paid_vnd for view in published.values()
        )
        assert figures[ReportKey.MONEY_REFUNDED].numerator == DEPOSIT + PART_OF_FEE
        connection.commit()
        summary = DailySummaryRepository.read(
            connection,
            store_id=shop.store_id,
            principal=owner,
            policy=STANDARD_WASH_SLA,
            day=_local_date(now),
            as_of=datetime.now(UTC),
        )
        assert summary is not None
    finally:
        connection.rollback()
        publish_storage_policy(
            connection, actor_id=owner.staff_user_id, payload=withdrawal_document()
        )
        connection.commit()


def test_a_waiver_that_leaves_nothing_owed_settles_and_the_goods_leave(
    connection: psycopg.Connection[Any],
) -> None:
    """A2/A3 through the waiver: partial pay -> waive -> settled -> handed over -> COMPLETED.

    Both shapes the review names: the customer paid exactly the quoted total (the fee wholly
    unpaid), and the customer paid part of the fee.
    """

    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    publish_storage_policy(connection, actor_id=shop.owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    try:
        for paid, kept in ((TOTAL_VND, 0), (PART_OF_FEE, 3_000)):
            order_id = _ready_for(connection, shop, staff, None)
            _age_ready(connection, order_id, 25)
            _pay(connection, order_id, staff, paid)
            with connection.cursor() as cursor:
                before = UnclaimedRepository.read_order_storage(
                    cursor, order_id=order_id, principal=staff, as_of=datetime.now(UTC)
                )
            # A3: the sheet can say what will happen before the press.
            effect = before.waiver_effect
            assert effect is not None
            assert (effect.waived_vnd, effect.kept_vnd, effect.settles) == (
                FEE_DAY_25 - kept,
                kept,
                True,
            )
            # A payment sheet opened before the waiver is stale after it.
            stale = _read(connection, order_id, staff).row_version

            waived = _waive(connection, order_id, shop.requester).view
            connection.commit()
            assert waived.balance.value == "PAID"
            assert (waived.owed_vnd, waived.paid_vnd, waived.remaining_vnd) == (paid, paid, 0)
            assert waived.settlement_shape == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
            assert OrderStep.COLLECT in {step.step for step in waived.next_steps}
            with pytest.raises(PaymentStateError) as refused:
                PaymentRepository().record(
                    connection,
                    PaymentCommand(
                        order_id=order_id,
                        expected_row_version=stale,
                        amount_vnd=1,
                        method=CASH,
                        transfer_seen=False,
                        bank_ref_last=None,
                        collected_by_customer=False,
                        principal=staff,
                        correlation_id=uuid4(),
                    ),
                )
            assert refused.value.reason_code == "STALE_VERSION"
            connection.rollback()

            # The event, the audit row and the outbox rows came with it, in one transaction.
            assert _rows(
                connection,
                "SELECT payload ->> 'settlement_id' IS NOT NULL, payload ->> 'kept_vnd' "
                "FROM domain_events WHERE aggregate_id = %s "
                "AND event_type = 'ORDER_STORAGE_FEE_WAIVED'",
                order_id,
            ) == [(True, str(kept))]
            assert {
                row[0]
                for row in _rows(
                    connection,
                    "SELECT event_type FROM outbox_events WHERE aggregate_id = %s",
                    order_id,
                )
            } >= {"order.storage_fee_waived.v1", "order.settlement_recorded.v1"}

            collected = SettlementRepository().record_collection(
                connection,
                CollectionCommand(
                    order_id=order_id,
                    expected_row_version=waived.row_version,
                    principal=staff,
                    correlation_id=uuid4(),
                ),
            )
            done = _step(
                connection, order_id, staff, collected.row_version, OrderStep.HAND_OVER
            ).view
            assert done.commercial is CommercialOrderStatus.COMPLETED
            assert (done.owed_vnd, done.paid_vnd, done.remaining_vnd) == (paid, paid, 0)
            connection.commit()

        # A waiver below the quoted total does not settle, and waives the whole unpaid fee.
        order_id = _ready_for(connection, shop, staff, None)
        _age_ready(connection, order_id, 25)
        _pay(connection, order_id, staff, DEPOSIT)
        waived = _waive(connection, order_id, shop.requester).view
        assert (waived.balance.value, waived.remaining_vnd) == (
            "PARTIALLY_PAID",
            TOTAL_VND - DEPOSIT,
        )
        # And a second waiver is refused as already waived, not as "nothing owed".
        with pytest.raises(UnclaimedRefused, match="STORAGE_FEE_ALREADY_WAIVED"):
            _waive(connection, order_id, shop.requester)
        connection.rollback()
    finally:
        connection.rollback()
        publish_storage_policy(
            connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )
        connection.commit()


@pytest.mark.parametrize("fall", ["rewash_ready_again", "policy_withdrawn"])
def test_zero_dong_settles_a_ledger_that_covers_everything_and_the_goods_leave(
    connection: psycopg.Connection[Any], fall: str
) -> None:
    """A2 through the payment: part of the fee paid, the fee falls, 0 đồng settles, hand over.

    The fee part already paid is fixed beside the settlement with no accrual trace (`0066`), and
    the settlement is exactly the quoted total plus it (`0060`'s commit-time check).
    """

    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    publish_storage_policy(connection, actor_id=shop.owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    try:
        order_id = _ready_for(connection, shop, staff, None)
        _age_ready(connection, order_id, 25)
        _pay(connection, order_id, staff, PART_OF_FEE)
        if fall == "rewash_ready_again":
            _apply(connection, "rewash_ready_again", order_id, staff, shop)
        else:
            publish_storage_policy(
                connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
            )
        connection.commit()
        view = _read(connection, order_id, staff)
        assert (view.balance.value, view.owed_vnd, view.remaining_vnd) == (
            "PARTIALLY_PAID",
            PART_OF_FEE,
            0,
        )
        assert view.payment_may_hand_over is True
        # One đồng more is refused -- nothing is owed; 0 đồng settles and hands over.
        with pytest.raises(PaymentStateError) as refused:
            _pay(connection, order_id, staff, 1)
        assert refused.value.reason_code == "NOTHING_OWED"
        connection.rollback()
        settled = _pay(connection, order_id, staff, 0, collected=True)
        assert (settled.payment_id, settled.amount_vnd, settled.balance_status) == (None, 0, "PAID")
        assert (settled.owed_vnd, settled.paid_vnd, settled.remaining_vnd) == (
            PART_OF_FEE,
            PART_OF_FEE,
            0,
        )
        assert settled.settlement_shape == "EXACT_PAYMENT_SELF_COLLECTION"
        assert _rows(
            connection,
            "SELECT amount_vnd, basis FROM order_storage_fees WHERE order_id = %s",
            order_id,
        ) == [(3_000, "ALREADY_PAID")]
        # No money moved, so no ledger row: the ledger still sums to what was paid.
        assert _rows(
            connection,
            "SELECT count(*), sum(amount_vnd) FROM order_payments WHERE order_id = %s",
            order_id,
        ) == [(1, PART_OF_FEE)]
        done = _step(connection, order_id, staff, settled.row_version, OrderStep.HAND_OVER).view
        assert done.commercial is CommercialOrderStatus.COMPLETED
        assert (done.owed_vnd, done.paid_vnd, done.remaining_vnd) == (PART_OF_FEE, PART_OF_FEE, 0)
        connection.commit()
    finally:
        connection.rollback()
        publish_storage_policy(
            connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )
        connection.commit()


def test_the_fee_basis_admits_exactly_its_two_shapes(connection: psycopg.Connection[Any]) -> None:
    """`0066`: an ACCRUED row carries its trace; an ALREADY_PAID row carries none."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conname = 'order_storage_fees_trace_matches_basis'
            """
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            SELECT column_name, is_nullable, column_default FROM information_schema.columns
            WHERE table_name = 'order_storage_fees'
              AND column_name IN ('basis', 'days_waiting', 'chargeable_days', 'policy_version_id')
            ORDER BY column_name
            """
        )
        columns = cursor.fetchall()
    assert row is not None and "ALREADY_PAID" in row[0] and "ACCRUED" in row[0]
    assert [(name, nullable) for name, nullable, _default in columns] == [
        ("basis", "NO"),
        ("chargeable_days", "YES"),
        ("days_waiting", "YES"),
        ("policy_version_id", "YES"),
    ]
    assert "ACCRUED" in str(columns[0][2])


# --- DEC-047: a hold pauses the fee; it never erases it -------------------------------------------


def _age_hold(connection: Any, order_id: UUID, days: int) -> None:
    """Harness step, as `_age_ready`: the hold (and the ready time) `days` days earlier -- the
    order has now been on hold for `days` days."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE orders
            SET production_ready_at = production_ready_at - make_interval(days => %s),
                production_accepted_at = production_accepted_at - make_interval(days => %s),
                row_version = row_version + 1
            WHERE id = %s
            """,
            (days, days, order_id),
        )
        # The hold row is append-only (its one update is its end), so the harness moves it with
        # the guard set aside for this one statement, as a restore would.
        cursor.execute(
            "ALTER TABLE order_storage_holds DISABLE TRIGGER order_storage_holds_protected"
        )
        cursor.execute(
            "UPDATE order_storage_holds SET held_at = held_at - make_interval(days => %s) "
            "WHERE order_id = %s AND resumed_at IS NULL",
            (days, order_id),
        )
        cursor.execute(
            "ALTER TABLE order_storage_holds ENABLE TRIGGER order_storage_holds_protected"
        )


def _fee(connection: Any, order_id: UUID, staff: Any) -> Any:
    with connection.cursor() as cursor:
        return UnclaimedRepository.read_order_storage(
            cursor, order_id=order_id, principal=staff, as_of=datetime.now(UTC)
        )


def test_a_hold_pauses_the_fee_and_holding_then_paying_the_quoted_total_does_not_settle(
    connection: psycopg.Connection[Any],
) -> None:
    """The verifier's reproduction: 25 days on the shelf (25.000 ₫ accrued), HOLD, take 110.000 ₫.

    Before DEC-047 the hold erased the fee, 110.000 ₫ read PAID, and after RESUME the fee was gone
    with no fee row fixed. Now the fee stands through the hold and ten days on it, the quoted total
    leaves it owed, and RESUME continues the count from day 25 -- not from day 35.
    """

    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    publish_storage_policy(connection, actor_id=shop.owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    try:
        order_id = _ready_for(connection, shop, staff, None)
        _age_ready(connection, order_id, 25)
        assert _read(connection, order_id, staff).owed_vnd == TOTAL_VND + FEE_DAY_25
        held = _do(connection, order_id, staff, OrderStep.HOLD)
        assert held.owed_vnd == TOTAL_VND + FEE_DAY_25
        assert _rows(
            connection,
            "SELECT resumed_at IS NULL FROM order_storage_holds WHERE order_id = %s",
            order_id,
        ) == [(True,)]
        _age_hold(connection, order_id, 10)  # ten days on hold: day 35 on the shelf
        storage = _fee(connection, order_id, staff)
        assert (storage.fee.status, storage.fee.amount_vnd) == (
            StorageFeeStatus.PAUSED,
            FEE_DAY_25,
        )
        assert storage.fee.fee is not None and storage.fee.fee.days_waiting == 25
        paid = _pay(connection, order_id, staff, TOTAL_VND)
        assert (paid.balance_status, paid.remaining_vnd) == ("PARTIALLY_PAID", FEE_DAY_25)
        assert _rows(
            connection, "SELECT count(*) FROM order_storage_fees WHERE order_id = %s", order_id
        ) == [(0,)]
        resumed = _do(connection, order_id, staff, OrderStep.RESUME)
        assert (resumed.owed_vnd, resumed.paid_vnd, resumed.balance.value) == (
            TOTAL_VND + FEE_DAY_25,
            TOTAL_VND,
            "PARTIALLY_PAID",
        )
        # The hold is ended by RESUME: ten shop days, from the hold's day to today.
        assert _rows(
            connection,
            "SELECT (resumed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date "
            "- (held_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date FROM order_storage_holds "
            "WHERE order_id = %s",
            order_id,
        ) == [(10,)]
        # The board and the waiting list read the same resumed figure.
        assert _board(connection, shop, staff)[order_id].remaining_vnd == FEE_DAY_25
        assert _waiting(connection, shop, staff)[order_id].remaining_vnd == FEE_DAY_25
        # Paying the rest settles it and fixes the fee accrued before the hold, with its trace.
        settled = _pay(connection, order_id, staff, FEE_DAY_25)
        assert settled.balance_status == "PAID"
        assert _rows(
            connection,
            "SELECT amount_vnd, basis, days_waiting, chargeable_days FROM order_storage_fees "
            "WHERE order_id = %s",
            order_id,
        ) == [(FEE_DAY_25, "ACCRUED", 25, 5)]
    finally:
        connection.rollback()
        publish_storage_policy(
            connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )
        connection.commit()


def test_a_paused_fee_is_waived_only_by_the_approver_and_then_settles(
    connection: psycopg.Connection[Any],
) -> None:
    """The one legal way to take the fee off a held order is the waiver, by an approver."""

    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    publish_storage_policy(connection, actor_id=shop.owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    try:
        order_id = _ready_for(connection, shop, staff, None)
        _age_ready(connection, order_id, 25)
        _do(connection, order_id, staff, OrderStep.HOLD)
        _pay(connection, order_id, staff, TOTAL_VND)
        effect = _fee(connection, order_id, staff).waiver_effect
        assert effect is not None and (effect.waived_vnd, effect.settles) == (FEE_DAY_25, True)
        # An operator may not: refused before anything is read or written.
        with pytest.raises(UnclaimedAuthorizationError):
            _waive(connection, order_id, staff)
        waived = _waive(connection, order_id, shop.requester).view
        assert (waived.balance.value, waived.owed_vnd, waived.remaining_vnd) == (
            "PAID",
            TOTAL_VND,
            0,
        )
        assert _rows(
            connection,
            "SELECT waived_amount_vnd, days_waiting FROM storage_fee_waivers WHERE order_id = %s",
            order_id,
        ) == [(FEE_DAY_25, 25)]
    finally:
        connection.rollback()
        publish_storage_policy(
            connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )
        connection.commit()


@pytest.mark.parametrize("fall", ["rewash_ready_again", "policy_withdrawn", "cancellation_review"])
def test_a_hold_does_not_protect_the_unpaid_fee_from_the_events_that_do_lower_it(
    connection: psycopg.Connection[Any], fall: str
) -> None:
    """DEC-047 keeps the other rules: a rewash restarts the free days, a withdrawn policy stops
    accrual, a cancellation stops it -- and in each the paid part stays owed-for."""

    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    publish_storage_policy(connection, actor_id=shop.owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    try:
        order_id = _ready_for(connection, shop, staff, None)
        _age_ready(connection, order_id, 25)
        _pay(connection, order_id, staff, PART_OF_FEE)
        _do(connection, order_id, staff, OrderStep.HOLD)
        assert _read(connection, order_id, staff).owed_vnd == TOTAL_VND + FEE_DAY_25
        if fall == "rewash_ready_again":
            _do(connection, order_id, staff, OrderStep.RESUME)
            _apply(connection, "rewash_ready_again", order_id, staff, shop)
            # The hold ended at RESUME and began before the new ready time: it no longer counts.
            assert _rows(
                connection,
                "SELECT h.resumed_at IS NOT NULL, h.held_at < o.production_ready_at "
                "FROM order_storage_holds h JOIN orders o ON o.id = h.order_id "
                "WHERE h.order_id = %s",
                order_id,
            ) == [(True, True)]
        elif fall == "policy_withdrawn":
            publish_storage_policy(
                connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
            )
        else:
            _apply(connection, "cancellation_review", order_id, staff, shop)
        view = _read(connection, order_id, staff)
        assert (view.owed_vnd, view.paid_vnd, view.remaining_vnd) == (PART_OF_FEE, PART_OF_FEE, 0)
    finally:
        connection.rollback()
        publish_storage_policy(
            connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )
        connection.commit()


def test_a_hold_record_admits_one_open_hold_and_only_its_end(
    connection: psycopg.Connection[Any],
) -> None:
    """`0066`: one open hold per order; the one update a hold admits is its end, once."""

    now = datetime.now(UTC)
    shop = _Shop(connection, now)
    staff = _operator(connection, shop.store_id)
    try:
        order_id = _ready_for(connection, shop, staff, None)
        _do(connection, order_id, staff, OrderStep.HOLD)
        [(hold_id,)] = _rows(
            connection, "SELECT id FROM order_storage_holds WHERE order_id = %s", order_id
        )
        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(
                "INSERT INTO order_storage_holds (id, order_id, store_id, held_at) "
                "VALUES (%s, %s, %s, now())",
                (uuid4(), order_id, shop.store_id),
            )
        with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
            connection.execute(
                "UPDATE order_storage_holds SET held_at = now() WHERE id = %s", (hold_id,)
            )
        with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
            connection.execute("DELETE FROM order_storage_holds WHERE id = %s", (hold_id,))
        _do(connection, order_id, staff, OrderStep.RESUME)
        with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
            connection.execute(
                "UPDATE order_storage_holds SET resumed_at = now() WHERE id = %s", (hold_id,)
            )
        # A hold of laundry still being washed writes no hold record at all.
        washing = _ready_for(connection, shop, staff, None)
        _do(connection, washing, staff, OrderStep.REWASH, rewash_reason=RewashReason.NOT_CLEAN)
        _do(connection, washing, staff, OrderStep.HOLD)
        assert _rows(
            connection, "SELECT count(*) FROM order_storage_holds WHERE order_id = %s", washing
        ) == [(0,)]
    finally:
        connection.rollback()
