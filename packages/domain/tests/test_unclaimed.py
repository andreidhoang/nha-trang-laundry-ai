"""`UNCLAIMED-001` (`DEC-036`): the storage fee, the disposal rule, and the notes, as tables."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import (
    OrderStep,
    QuoteReadinessFacts,
    StepFacts,
    next_steps,
)
from nha_trang_laundry_domain.orders import (
    OrderState,
    OrderTransitionError,
    transition_commercial,
)
from nha_trang_laundry_domain.payments import (
    ChargeKind,
    PaymentAccepted,
    PaymentMethod,
    charge_amount,
    evaluate_payment,
    owed_charges,
)
from nha_trang_laundry_domain.settlement import QuotedTotal
from nha_trang_laundry_domain.unclaimed import (
    DisposalRefusal,
    OrderStorageFee,
    StorageClock,
    StorageFeeStatus,
    StoragePause,
    StoragePolicy,
    StoragePolicyError,
    awaiting_pickup,
    clean_note,
    days_waiting,
    disposal_money,
    disposal_rule_vi,
    disposal_verdict,
    order_storage_fee,
    parse_storage_policy,
    receipt_line_vi,
    storage_fee,
    validate_storage_document,
    withdrawal_document,
)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "templates" / "storage-policy-dec-036.json"

POLICY = StoragePolicy(
    free_days=20,
    fee_per_started_day_vnd=5_000,
    fee_cap_percent=50,
    disposal_from_day=60,
    disposal_min_attempts=3,
    disposal_min_attempt_days=2,
)
VN = timedelta(hours=7)
#: Ready at 10:00 on 1 September 2026, shop time.
READY = datetime(2026, 9, 1, 10, 0) - VN
READY = READY.replace(tzinfo=UTC)


def day(n: int, hour: int = 12) -> datetime:
    """Noon (shop time) on the n-th calendar day after the ready day."""

    return (datetime(2026, 9, 1, hour, 0) + timedelta(days=n) - VN).replace(tzinfo=UTC)


# --- the fee table --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "quoted", "expected"),
    [
        (0, 120_000, 0),
        (1, 120_000, 0),
        (20, 120_000, 0),  # day 20 is still free
        (21, 120_000, 5_000),  # day 21: one started day
        (22, 120_000, 10_000),
        (32, 120_000, 60_000),  # 12 days = 60.000 = exactly half of 120.000
        (33, 120_000, 60_000),  # capped
        (60, 120_000, 60_000),
        (26, 60_000, 30_000),  # DEC-036's own example: a 60.000 bag never owes more than 30.000
        (27, 60_000, 30_000),
        (21, 9_999, 4_999),  # the cap rounds down, in the customer's favour
        (21, 0, 0),
    ],
)
def test_the_fee_table(n: int, quoted: int, expected: int) -> None:
    fee = storage_fee(POLICY, ready_at=READY, as_of=day(n), quoted_total_vnd=quoted)
    assert fee.days_waiting == n
    assert fee.amount_vnd == expected
    assert fee.chargeable_days == max(0, n - 20)


def test_days_are_shop_local_calendar_days_not_24_hour_periods() -> None:
    late = (datetime(2026, 9, 1, 19, 55) - VN).replace(tzinfo=UTC)
    # 00:05 the next shop day is one day waited, though only ten minutes passed.
    just_after_midnight = (datetime(2026, 9, 2, 0, 5) - VN).replace(tzinfo=UTC)
    assert days_waiting(late, just_after_midnight) == 1
    # 23:59 on day 21 shop time is still day 21; 00:00 is day 22.
    day21_end = (datetime(2026, 9, 22, 23, 59) - VN).replace(tzinfo=UTC)
    day22_start = (datetime(2026, 9, 23, 0, 0) - VN).replace(tzinfo=UTC)
    assert (
        storage_fee(POLICY, ready_at=late, as_of=day21_end, quoted_total_vnd=120_000).amount_vnd
        == 5_000
    )
    assert (
        storage_fee(POLICY, ready_at=late, as_of=day22_start, quoted_total_vnd=120_000).amount_vnd
        == 10_000
    )
    # A clock that reads earlier than the ready time is day 0, never negative.
    assert days_waiting(READY, READY - timedelta(days=3)) == 0
    with pytest.raises(ValueError, match="timezone"):
        days_waiting(datetime(2026, 9, 1), READY)


@given(
    n=st.integers(min_value=0, max_value=400),
    quoted=st.integers(min_value=0, max_value=50_000_000),
)
def test_the_fee_is_never_more_than_the_cap_and_never_moves_backwards(n: int, quoted: int) -> None:
    today = storage_fee(POLICY, ready_at=READY, as_of=day(n), quoted_total_vnd=quoted)
    tomorrow = storage_fee(POLICY, ready_at=READY, as_of=day(n + 1), quoted_total_vnd=quoted)
    assert isinstance(today.amount_vnd, int)
    assert 0 <= today.amount_vnd <= quoted * 50 // 100
    assert today.amount_vnd <= tomorrow.amount_vnd
    assert today.amount_vnd % 5_000 == 0 or today.amount_vnd == quoted * 50 // 100


def test_the_quoted_total_must_be_whole_dong() -> None:
    for bad in (1.5, True, -1):
        with pytest.raises(ValueError):
            storage_fee(POLICY, ready_at=READY, as_of=day(30), quoted_total_vnd=bad)  # type: ignore[arg-type]


# --- one order's fee ------------------------------------------------------------------------------


def fee_for(**overrides: object) -> OrderStorageFee:
    arguments: dict[str, object] = {
        # DEC-047: whether the count runs, and the hold bookkeeping; waiting and never held.
        "clock": StorageClock.RUNNING,
        "pause": StoragePause(paused_at=None, paused_days=0),
        "ready_at": READY,
        "as_of": day(25),
        "quoted_total_vnd": 120_000,
        "waived": False,
        "settled": False,
        "fixed_vnd": None,
        # MONEY-LIFECYCLE-009: the ledger's sum is an input now; nothing paid, as before.
        "paid_vnd": 0,
    }
    arguments.update(overrides)
    policy = arguments.pop("policy", POLICY)
    return order_storage_fee(policy, **arguments)  # type: ignore[arg-type]


def test_order_fee_states() -> None:
    accruing = fee_for()
    assert (accruing.status, accruing.amount_vnd) == (StorageFeeStatus.ACCRUING, 25_000)
    assert fee_for(as_of=day(20)).status is StorageFeeStatus.FREE_PERIOD
    assert fee_for(as_of=day(20)).amount_vnd == 0
    # Before publication (or after a withdrawal) nothing is charged, however long it waited.
    unpublished = fee_for(policy=None, as_of=day(300))
    assert (unpublished.status, unpublished.amount_vnd) == (StorageFeeStatus.POLICY_UNPUBLISHED, 0)
    # A waiver stops it for good.
    assert (fee_for(waived=True).status, fee_for(waived=True).amount_vnd) == (
        StorageFeeStatus.WAIVED,
        0,
    )
    # Paid in full: the fee is what was fixed then, whatever the clock or the policy says now.
    assert (
        fee_for(settled=True, fixed_vnd=15_000, policy=None, as_of=day(90)).amount_vnd
    ) == 15_000
    assert fee_for(settled=True, fixed_vnd=15_000).status is StorageFeeStatus.FIXED
    assert fee_for(settled=True, as_of=day(90)).amount_vnd == 0
    # Not waiting for the customer (a delivery order, in the machine, collected): nothing.
    assert fee_for(clock=StorageClock.STOPPED).status is StorageFeeStatus.NOT_WAITING
    assert fee_for(ready_at=None).status is StorageFeeStatus.NOT_WAITING
    # No single total, no cap to measure against: no fee (fail closed).
    assert fee_for(quoted_total_vnd=None).status is StorageFeeStatus.NO_SINGLE_TOTAL


def test_the_fee_is_a_second_charge_and_changes_no_payment_rule() -> None:
    quoted = QuotedTotal(120_000, 120_000)
    assert owed_charges(quoted) == owed_charges(quoted, storage_fee_vnd=0)
    charges = owed_charges(quoted, storage_fee_vnd=25_000)
    assert charges is not None
    assert [charge.kind for charge in charges] == [ChargeKind.QUOTED_TOTAL, ChargeKind.STORAGE_FEE]
    assert charge_amount(charges, ChargeKind.STORAGE_FEE) == 25_000
    assert charge_amount(owed_charges(quoted), ChargeKind.STORAGE_FEE) == 0
    # Cash at pickup including the fee: 145.000 settles the order; 120.000 does not.
    common = dict(
        commercial=CommercialOrderStatus.ACTIVE,
        balance=OrderBalanceStatus.UNPAID,
        charges=charges,
        paid_vnd=0,
        method=PaymentMethod.TIEN_MAT,
        transfer_seen=False,
        bank_ref_last=None,
    )
    full = evaluate_payment(amount_vnd=145_000, collected_by_customer=True, **common)  # type: ignore[arg-type]
    assert isinstance(full, PaymentAccepted) and full.completes and full.owed_vnd == 145_000
    part = evaluate_payment(amount_vnd=120_000, collected_by_customer=False, **common)  # type: ignore[arg-type]
    assert isinstance(part, PaymentAccepted) and part.remaining_after_vnd == 25_000
    with pytest.raises(ValueError):
        owed_charges(quoted, storage_fee_vnd=-1)


# --- disposal -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "attempt_days", "expected"),
    [
        (60, (58, 59, 59), ()),  # day 60, three attempts on two days: legal
        (59, (58, 59, 59), (DisposalRefusal.DISPOSAL_TOO_EARLY,)),
        (60, (58, 59), (DisposalRefusal.CONTACT_ATTEMPTS_TOO_FEW,)),
        (60, (59, 59, 59), (DisposalRefusal.CONTACT_DAYS_TOO_FEW,)),
        (
            10,
            (),
            (
                DisposalRefusal.DISPOSAL_TOO_EARLY,
                DisposalRefusal.CONTACT_ATTEMPTS_TOO_FEW,
                DisposalRefusal.CONTACT_DAYS_TOO_FEW,
            ),
        ),
        (200, (1, 30, 61), ()),
    ],
)
def test_the_disposal_table(
    n: int, attempt_days: tuple[int, ...], expected: tuple[DisposalRefusal, ...]
) -> None:
    verdict = disposal_verdict(
        POLICY,
        awaiting=True,
        ready_at=READY,
        as_of=day(n),
        attempt_times=[day(d) for d in attempt_days],
    )
    assert verdict.refusals == expected
    assert verdict.allowed is (expected == ())
    assert verdict.eligible_on == date(2026, 10, 31)  # 1 September + 60 shop days


def test_disposal_needs_the_policy_and_the_waiting_state() -> None:
    times = [day(50), day(51), day(52)]
    unpublished = disposal_verdict(
        None, awaiting=True, ready_at=READY, as_of=day(90), attempt_times=times
    )
    assert unpublished.refusals == (DisposalRefusal.STORAGE_POLICY_UNPUBLISHED,)
    collected = disposal_verdict(
        POLICY, awaiting=False, ready_at=READY, as_of=day(90), attempt_times=times
    )
    assert collected.refusals == (DisposalRefusal.NOT_AWAITING_PICKUP,)


def test_attempts_before_the_laundry_was_last_ready_do_not_count() -> None:
    # Rewashed and ready again on day 5: the three calls on days 1-2 were about other laundry.
    verdict = disposal_verdict(
        POLICY,
        awaiting=True,
        ready_at=day(5),
        as_of=day(70),
        attempt_times=[day(1), day(2), day(2), day(40)],
    )
    assert verdict.attempts_counted == 1
    assert DisposalRefusal.CONTACT_ATTEMPTS_TOO_FEW in verdict.refusals


def test_disposal_attempt_days_are_shop_local() -> None:
    # 23:30 and 00:30 shop time are two different days though an hour apart.
    first = (datetime(2026, 10, 20, 23, 30) - VN).replace(tzinfo=UTC)
    second = (datetime(2026, 10, 21, 0, 30) - VN).replace(tzinfo=UTC)
    verdict = disposal_verdict(
        POLICY, awaiting=True, ready_at=READY, as_of=day(61), attempt_times=[first, first, second]
    )
    assert verdict.attempt_days == 2 and verdict.allowed


@pytest.mark.parametrize(
    ("owed", "paid", "kept", "written_off"),
    [(120_000, 0, 0, 120_000), (180_000, 50_000, 50_000, 130_000), (60_000, 60_000, 60_000, 0)],
)
def test_disposal_money_keeps_what_was_paid_and_writes_off_the_rest(
    owed: int, paid: int, kept: int, written_off: int
) -> None:
    money = disposal_money(owed_vnd=owed, paid_vnd=paid)
    assert (money.kept_vnd, money.written_off_vnd) == (kept, written_off)
    assert money.kept_vnd + money.written_off_vnd == money.owed_vnd
    with pytest.raises(ValueError):
        disposal_money(owed_vnd=10, paid_vnd=11)


# --- the custody resolution -----------------------------------------------------------------------


def waiting_state(balance: OrderBalanceStatus, *, collected: bool = False) -> OrderState:
    return OrderState(
        CommercialOrderStatus.CANCELLATION_REVIEW,
        IntakeStatus.ACCEPTED,
        ProductionStatus.READY_AT_STORE,
        FulfillmentMode.SELF_DROP_SELF_COLLECT,
        balance,
        False,
        collected,
        READY,
    )


@pytest.mark.parametrize(
    "balance",
    [OrderBalanceStatus.UNPAID, OrderBalanceStatus.PARTIALLY_PAID, OrderBalanceStatus.PAID],
)
def test_disposal_keeps_the_balance_as_the_ledger_holds_it(balance: OrderBalanceStatus) -> None:
    closed = transition_commercial(
        waiting_state(balance),
        CommercialOrderStatus.CANCELLED,
        cancellation_approved=True,
        custody_and_financial_resolution_recorded=True,
        custody_resolution=CustodyResolution.UNCLAIMED_DISPOSED,
        unclaimed_disposal_verified=True,
    )
    assert closed.commercial is CommercialOrderStatus.CANCELLED
    assert closed.balance is balance  # never REFUNDED: money already paid is kept


def test_only_the_owners_route_may_record_a_disposal() -> None:
    with pytest.raises(OrderTransitionError, match="DEC-036"):
        transition_commercial(
            waiting_state(OrderBalanceStatus.UNPAID),
            CommercialOrderStatus.CANCELLED,
            cancellation_approved=True,
            custody_and_financial_resolution_recorded=True,
            custody_resolution=CustodyResolution.UNCLAIMED_DISPOSED,
        )
    with pytest.raises(OrderTransitionError, match="still on the shelf"):
        transition_commercial(
            waiting_state(OrderBalanceStatus.PAID, collected=True),
            CommercialOrderStatus.CANCELLED,
            cancellation_approved=True,
            custody_and_financial_resolution_recorded=True,
            custody_resolution=CustodyResolution.UNCLAIMED_DISPOSED,
            unclaimed_disposal_verified=True,
        )


def test_the_cancel_step_never_offers_disposal() -> None:
    active = OrderState(
        CommercialOrderStatus.ACTIVE,
        IntakeStatus.ACCEPTED,
        ProductionStatus.READY_AT_STORE,
        FulfillmentMode.SELF_DROP_SELF_COLLECT,
        OrderBalanceStatus.UNPAID,
        False,
        False,
        READY,
    )
    facts = StepFacts(
        state=active,
        quote_readiness=QuoteReadinessFacts(True, True, True, True),
        quoted_total=QuotedTotal(120_000, 120_000),
        settlement_shape=None,
        pickup_leg_succeeded=False,
    )
    cancel = [step for step in next_steps(facts) if step.step is OrderStep.CANCEL]
    assert cancel, "an active order can be cancelled through review"
    assert CustodyResolution.UNCLAIMED_DISPOSED not in cancel[0].custody_resolutions


def test_awaiting_pickup_is_four_facts() -> None:
    base = dict(
        commercial=CommercialOrderStatus.ACTIVE,
        production=ProductionStatus.READY_AT_STORE,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        self_collection_recorded=False,
    )
    assert awaiting_pickup(**base)  # type: ignore[arg-type]
    assert awaiting_pickup(**{**base, "fulfillment_mode": FulfillmentMode.PICKUP_ONLY})  # type: ignore[arg-type]
    for change in (
        {"commercial": CommercialOrderStatus.CANCELLATION_REVIEW},
        {"production": ProductionStatus.RELEASED},
        {"production": ProductionStatus.QUALITY_CHECK},
        {"fulfillment_mode": FulfillmentMode.PICKUP_AND_RETURN},
        {"fulfillment_mode": FulfillmentMode.RETURN_ONLY},
        {"self_collection_recorded": True},
    ):
        assert not awaiting_pickup(**{**base, **change})  # type: ignore[arg-type]


# --- notes ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "gọi 0905123456",
        "số mới 0905 123 456",
        "+84 905.123.456",
        "sdt 090-512-3456",
    ],
)
def test_a_note_refuses_anything_that_looks_like_a_phone_number(text: str) -> None:
    with pytest.raises(ValueError, match="NOTE_LOOKS_LIKE_PHONE"):
        clean_note(text)


def test_a_note_keeps_what_happened() -> None:
    assert clean_note("  hẹn   chiều mai qua  ") == "hẹn chiều mai qua"
    assert clean_note("phiếu 17, 2 túi") == "phiếu 17, 2 túi"
    assert clean_note("   ") is None
    assert clean_note(None) is None
    with pytest.raises(ValueError, match="NOTE_REQUIRED"):
        clean_note(" ", required=True)
    with pytest.raises(ValueError, match="NOTE_TOO_LONG"):
        clean_note("a" * 121)
    assert clean_note("a" * 120) == "a" * 120


# --- the published document -----------------------------------------------------------------------


def test_the_template_is_the_decision_record_figures() -> None:
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    policy = parse_storage_policy(payload)
    assert policy == POLICY
    validate_storage_document(payload)
    validate_storage_document(withdrawal_document())


def test_the_receipt_line_and_the_rule_are_built_from_the_figures() -> None:
    assert receipt_line_vi(POLICY) == (
        "Lấy đồ trong 20 ngày kể từ khi đồ xong; từ ngày 21 phí lưu kho 5.000 ₫/ngày "
        "(tối đa 50% tiền giặt); sau 60 ngày tiệm có thể thanh lý."
    )
    rule = disposal_rule_vi(POLICY)
    assert "ngày thứ 60" in rule and "ít nhất 3 lần" in rule and "2 ngày khác nhau" in rule
    assert "Tiền khách đã trả giữ nguyên; tiền còn nợ được xoá." in rule


@pytest.mark.parametrize(
    "change",
    [
        {"policy_version": "storage-policy-v2"},
        {"decision_ref": "DEC-035"},
        {"timezone": "UTC"},
        {"fee_per_started_day_vnd": 0},
        {"fee_per_started_day_vnd": 5000.0},
        {"fee_cap_percent_of_quoted_total": 101},
        {"free_days_after_ready": True},
        {"disposal_from_day": 20},
        {"disposal_min_contact_days": 4},
        {"extra": 1},
    ],
)
def test_a_document_that_is_not_the_policy_is_refused(change: dict[str, object]) -> None:
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    payload.update(change)
    with pytest.raises(StoragePolicyError):
        parse_storage_policy(payload)
    with pytest.raises(StoragePolicyError):
        validate_storage_document({**withdrawal_document(), "free_days_after_ready": 20})
