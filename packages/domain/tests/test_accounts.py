"""`PAYMENT-002` (`DEC-035`, B2B half): the account rules, as pure functions.

Limit arithmetic at its edge (equal to the limit leaves; one đồng over does not), the limit that
was never typed, the overdue block around the 15th in the shop's time zone, the owner's lift and its
end, the oldest-first allocation, the month a moment belongs to, and `goods_may_leave` with the
account as its second input.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.account_charge import (
    AccountChargeAccepted,
    AccountChargeRefused,
    account_payment_refusal,
    evaluate_account_charge,
)
from nha_trang_laundry_domain.accounts import (
    MAX_LIFT_DAYS,
    RECOMMENDED_STARTING_LIMIT_VND,
    AccountHandoverFacts,
    AccountRefusal,
    AccountRuleError,
    AccountStanding,
    AccountStatus,
    AccountTermsError,
    OpenCharge,
    account_handover_refusal,
    allocate_oldest_first,
    clean_lift_reason,
    latest_due_month,
    lift_until_instant,
    month_has_ended,
    month_start_instant,
    parse_account_terms,
    parse_month,
    statement_due_on,
    statement_month,
    validate_limit,
)
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.payments import PaymentMethod, PaymentRefusal
from nha_trang_laundry_domain.settlement import (
    GOODS_MAY_LEAVE_BALANCES,
    HANDOVER_READY_PRODUCTION,
    QuotedTotal,
    SettlementAccepted,
    SettlementNotSupported,
    SettlementShape,
    evaluate_settlement,
    goods_may_leave,
)

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=HCM)
ROOT = Path(__file__).resolve().parents[3]


def standing(**overrides: object) -> AccountStanding:
    base = AccountStanding(
        status=AccountStatus.ACTIVE,
        credit_limit_vnd=3_000_000,
        outstanding_vnd=0,
        overdue_vnd=0,
        overdue_block_lifted_until=None,
        as_of=NOW,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --- the limit ------------------------------------------------------------------------------------


def test_equal_to_the_limit_leaves_and_one_dong_over_does_not() -> None:
    at_limit = AccountHandoverFacts(standing(outstanding_vnd=2_890_000), 110_000)
    over = AccountHandoverFacts(standing(outstanding_vnd=2_890_001), 110_000)
    assert account_handover_refusal(at_limit) is None
    assert account_handover_refusal(over) is AccountRefusal.ACCOUNT_LIMIT_EXCEEDED


@given(
    limit=st.integers(min_value=1, max_value=10**9),
    outstanding=st.integers(min_value=0, max_value=10**9),
    remaining=st.integers(min_value=0, max_value=10**9),
)
def test_the_limit_rule_is_outstanding_plus_the_order_at_most_the_limit(
    limit: int, outstanding: int, remaining: int
) -> None:
    refusal = account_handover_refusal(
        AccountHandoverFacts(
            standing(credit_limit_vnd=limit, outstanding_vnd=outstanding), remaining
        )
    )
    assert (refusal is None) == (outstanding + remaining <= limit)


def test_a_limit_never_typed_is_refused_by_name_and_no_default_is_assumed() -> None:
    facts = AccountHandoverFacts(standing(credit_limit_vnd=None), 1)
    assert account_handover_refusal(facts) is AccountRefusal.ACCOUNT_LIMIT_UNSET
    # The recommendation is a hint only: an account at exactly the recommended figure with no limit
    # typed is still refused.
    facts = AccountHandoverFacts(standing(credit_limit_vnd=None), RECOMMENDED_STARTING_LIMIT_VND)
    assert account_handover_refusal(facts) is AccountRefusal.ACCOUNT_LIMIT_UNSET
    assert standing(credit_limit_vnd=None).available_vnd is None


def test_a_suspended_account_takes_nothing_new() -> None:
    facts = AccountHandoverFacts(standing(status=AccountStatus.SUSPENDED), 1)
    assert account_handover_refusal(facts) is AccountRefusal.ACCOUNT_SUSPENDED


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100", 9_007_199_254_740_992])
def test_a_limit_is_a_whole_number_of_dong_from_one(value: object) -> None:
    with pytest.raises(AccountRuleError) as caught:
        validate_limit(value)
    assert caught.value.code is AccountRefusal.ACCOUNT_LIMIT_INVALID


def test_a_limit_may_be_left_untyped_or_typed() -> None:
    assert validate_limit(None) is None
    assert validate_limit(1) == 1
    assert validate_limit(3_000_000) == 3_000_000


# --- statements, due dates and the overdue block -----------------------------------------------


def test_the_statement_month_is_the_shops_local_month() -> None:
    last_second = datetime(2026, 9, 30, 23, 59, 59, tzinfo=HCM)
    assert statement_month(last_second) == date(2026, 9, 1)
    assert statement_month(last_second + timedelta(seconds=1)) == date(2026, 10, 1)
    # 17:00 UTC on 30 September is already 1 October in Nha Trang.
    assert statement_month(datetime(2026, 9, 30, 17, 0, tzinfo=UTC)) == date(2026, 10, 1)


def test_due_on_the_fifteenth_of_the_next_month_across_the_year() -> None:
    assert statement_due_on(date(2026, 9, 1)) == date(2026, 10, 15)
    assert statement_due_on(date(2026, 12, 1)) == date(2027, 1, 15)
    assert statement_due_on(date(2027, 1, 1)) == date(2027, 2, 15)


def test_the_fifteenth_is_on_time_and_the_sixteenth_is_overdue() -> None:
    # On 15 October the September statement is due today: the latest month past due is August.
    assert latest_due_month(date(2026, 10, 15)) == date(2026, 8, 1)
    assert latest_due_month(date(2026, 10, 16)) == date(2026, 9, 1)
    assert latest_due_month(date(2027, 1, 16)) == date(2026, 12, 1)
    assert latest_due_month(date(2027, 1, 1)) == date(2026, 11, 1)


def test_an_overdue_statement_blocks_until_the_owner_lifts_it() -> None:
    overdue = standing(overdue_vnd=110_000, outstanding_vnd=110_000)
    assert account_handover_refusal(AccountHandoverFacts(overdue, 1)) is (
        AccountRefusal.ACCOUNT_OVERDUE
    )
    until = NOW + timedelta(days=3)
    lifted = replace(overdue, overdue_block_lifted_until=until)
    assert account_handover_refusal(AccountHandoverFacts(lifted, 1)) is None
    # The lift ends at its instant, exclusive: at the instant itself the block is back.
    ended = replace(lifted, as_of=until)
    assert account_handover_refusal(AccountHandoverFacts(ended, 1)) is (
        AccountRefusal.ACCOUNT_OVERDUE
    )


def test_a_lift_does_not_lift_the_limit() -> None:
    lifted = standing(
        overdue_vnd=1,
        outstanding_vnd=3_000_000,
        overdue_block_lifted_until=NOW + timedelta(days=1),
    )
    assert account_handover_refusal(AccountHandoverFacts(lifted, 1)) is (
        AccountRefusal.ACCOUNT_LIMIT_EXCEEDED
    )


def test_a_lift_runs_through_a_local_day_up_to_a_statement_cycle_ahead() -> None:
    today = date(2026, 10, 20)
    end = lift_until_instant(date(2026, 10, 25), today=today)
    assert end == datetime(2026, 10, 26, 0, 0, tzinfo=HCM)
    assert lift_until_instant(today, today=today) == datetime(2026, 10, 21, tzinfo=HCM)
    lift_until_instant(today + timedelta(days=MAX_LIFT_DAYS), today=today)
    for bad in (today - timedelta(days=1), today + timedelta(days=MAX_LIFT_DAYS + 1)):
        with pytest.raises(AccountRuleError) as caught:
            lift_until_instant(bad, today=today)
        assert caught.value.code is AccountRefusal.LIFT_EXPIRY_INVALID


@pytest.mark.parametrize("reason", [None, "", "  ", "ok", "x" * 201])
def test_a_lift_needs_the_owners_reason(reason: str | None) -> None:
    with pytest.raises(AccountRuleError) as caught:
        clean_lift_reason(reason)
    assert caught.value.code is AccountRefusal.LIFT_REASON_REQUIRED


def test_a_reason_is_kept_as_typed_with_spaces_collapsed() -> None:
    assert clean_lift_reason("  Khách hẹn   trả 20/10 ") == "Khách hẹn trả 20/10"


def test_a_month_is_frozen_only_once_it_has_ended_locally() -> None:
    september = date(2026, 9, 1)
    assert not month_has_ended(september, datetime(2026, 9, 30, 23, 59, tzinfo=HCM))
    assert month_has_ended(september, datetime(2026, 10, 1, 0, 0, tzinfo=HCM))
    assert month_start_instant(date(2026, 10, 1)) == datetime(2026, 9, 30, 17, 0, tzinfo=UTC)


@pytest.mark.parametrize("text", ["2026-13", "2026-9", "26-09", "1999-01", "abc"])
def test_a_month_is_named_yyyy_mm(text: str) -> None:
    with pytest.raises(AccountRuleError) as caught:
        parse_month(text)
    assert caught.value.code is AccountRefusal.MONTH_INVALID


def test_parse_month() -> None:
    assert parse_month(" 2026-09 ") == date(2026, 9, 1)


# --- allocation, oldest first ---------------------------------------------------------------------


def test_a_payment_reaches_the_oldest_orders_first() -> None:
    first, second, third = (
        OpenCharge(uuid4(), uuid4(), amount) for amount in (110_000, 90_000, 50_000)
    )
    split = allocate_oldest_first(150_000, (first, second, third))
    assert [(item.order_id, item.amount_vnd, item.settles) for item in split] == [
        (first.order_id, 110_000, True),
        (second.order_id, 40_000, False),
    ]


@given(
    remaining=st.lists(st.integers(min_value=1, max_value=10**7), min_size=1, max_size=12),
    data=st.data(),
)
def test_an_allocation_is_exact_and_ordered(remaining: list[int], data: st.DataObject) -> None:
    charges = tuple(OpenCharge(index, index, amount) for index, amount in enumerate(remaining))
    amount = data.draw(st.integers(min_value=1, max_value=sum(remaining)))
    split = allocate_oldest_first(amount, charges)
    assert sum(item.amount_vnd for item in split) == amount
    assert [item.order_id for item in split] == list(range(len(split)))
    # Every order before the last reached is paid in full.
    assert all(item.settles for item in split[:-1])
    assert all(item.amount_vnd <= remaining[item.order_id] for item in split)


def test_more_than_the_account_owes_is_a_contradiction_here() -> None:
    with pytest.raises(ValueError):
        allocate_oldest_first(2, (OpenCharge(1, 1, 1),))


def test_the_account_payment_rules_are_the_counters() -> None:
    def ask(amount: int, **kwargs: object) -> PaymentRefusal | None:
        defaults: dict[str, object] = {
            "outstanding_vnd": 200_000,
            "method": PaymentMethod.TIEN_MAT,
            "transfer_seen": False,
            "bank_ref_last": None,
        }
        defaults.update(kwargs)
        return account_payment_refusal(amount_vnd=amount, **defaults)  # type: ignore[arg-type]

    assert ask(200_000) is None
    assert ask(200_001) is PaymentRefusal.OVERPAYMENT_REFUSED
    assert ask(0) is PaymentRefusal.PAYMENT_AMOUNT_INVALID
    assert ask(1, outstanding_vnd=0) is PaymentRefusal.NOTHING_OWED
    assert ask(1, method=PaymentMethod.CHUYEN_KHOAN) is PaymentRefusal.TRANSFER_NOT_SEEN
    assert (
        ask(1, method=PaymentMethod.CHUYEN_KHOAN, transfer_seen=True, bank_ref_last="FT26") is None
    )
    assert ask(1, bank_ref_last="FT26") is PaymentRefusal.BANK_REF_INVALID


# --- goods_may_leave with the account as its second input ----------------------------------------


def test_goods_may_leave_reads_the_account_only_for_money_still_owed() -> None:
    fine = AccountHandoverFacts(standing(), 110_000)
    blocked = AccountHandoverFacts(standing(credit_limit_vnd=None), 110_000)
    for balance in (OrderBalanceStatus.UNPAID, OrderBalanceStatus.PARTIALLY_PAID):
        assert not goods_may_leave(balance)
        assert goods_may_leave(balance, fine)
        assert not goods_may_leave(balance, blocked)
    # A balance the account already admitted, and a paid one, leave without asking again.
    assert {OrderBalanceStatus.PAID, OrderBalanceStatus.ON_ACCOUNT} == GOODS_MAY_LEAVE_BALANCES
    assert goods_may_leave(OrderBalanceStatus.ON_ACCOUNT)
    assert goods_may_leave(OrderBalanceStatus.PAID, blocked)
    assert not goods_may_leave(OrderBalanceStatus.REFUNDED, fine)
    assert not goods_may_leave(OrderBalanceStatus.OVERPAID, fine)


def _charge(**overrides: object) -> AccountChargeAccepted | AccountChargeRefused:
    arguments: dict[str, object] = {
        "commercial": CommercialOrderStatus.ACTIVE,
        "production": ProductionStatus.READY_AT_STORE,
        "balance": OrderBalanceStatus.UNPAID,
        "fulfillment_mode": FulfillmentMode.SELF_DROP_SELF_COLLECT,
        "self_collection_recorded": False,
        "owed_vnd": 110_000,
        "paid_vnd": 0,
        "collected_by_customer": True,
        "standing": standing(),
    }
    arguments.update(overrides)
    return evaluate_account_charge(**arguments)  # type: ignore[arg-type]


def _refused(outcome: object) -> AccountRefusal | None:
    return outcome.refusal if isinstance(outcome, AccountChargeRefused) else None


def test_an_account_order_leaves_with_what_it_still_owes_on_the_account() -> None:
    outcome = _charge(balance=OrderBalanceStatus.PARTIALLY_PAID, paid_vnd=50_000)
    assert outcome == AccountChargeAccepted(
        amount_vnd=60_000,
        owed_vnd=110_000,
        paid_before_vnd=50_000,
        outstanding_after_vnd=60_000,
        collected_by_customer=True,
    )


def test_the_charge_refuses_by_name() -> None:
    assert _refused(_charge(standing=None)) is AccountRefusal.NOT_AN_ACCOUNT_CUSTOMER
    assert _refused(_charge(commercial=CommercialOrderStatus.COMPLETED)) is (
        AccountRefusal.ORDER_NOT_ACTIVE
    )
    assert _refused(_charge(balance=OrderBalanceStatus.PAID)) is AccountRefusal.NOTHING_OWED
    assert _refused(_charge(balance=OrderBalanceStatus.ON_ACCOUNT)) is AccountRefusal.NOTHING_OWED
    assert _refused(_charge(owed_vnd=None)) is AccountRefusal.NO_PRESENTABLE_TOTAL
    assert _refused(_charge(owed_vnd=0)) is AccountRefusal.NOTHING_OWED
    assert _refused(_charge(production=ProductionStatus.IN_PROCESS)) is (
        AccountRefusal.GOODS_NOT_READY_FOR_HANDOVER
    )
    assert _refused(_charge(collected_by_customer=False)) is (
        AccountRefusal.ACCOUNT_CHARGE_IS_THE_HANDOVER
    )
    assert (
        _refused(
            _charge(fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN, collected_by_customer=True)
        )
        is AccountRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER
    )
    assert _refused(_charge(standing=standing(credit_limit_vnd=None))) is (
        AccountRefusal.ACCOUNT_LIMIT_UNSET
    )
    assert _refused(_charge(standing=standing(outstanding_vnd=2_890_001))) is (
        AccountRefusal.ACCOUNT_LIMIT_EXCEEDED
    )
    assert _refused(_charge(standing=standing(overdue_vnd=1, outstanding_vnd=1))) is (
        AccountRefusal.ACCOUNT_OVERDUE
    )


def test_a_delivery_order_is_charged_before_the_courier_returns_it() -> None:
    outcome = _charge(
        fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN,
        collected_by_customer=False,
        production=ProductionStatus.RELEASED,
    )
    assert isinstance(outcome, AccountChargeAccepted)
    assert outcome.collected_by_customer is False


def test_the_finished_states_are_the_settlements() -> None:
    for production in ProductionStatus:
        outcome = _charge(production=production)
        assert (production in HANDOVER_READY_PRODUCTION) == isinstance(
            outcome, AccountChargeAccepted
        )


# --- the settlement written when the account pays an order off -----------------------------------


def test_the_account_settlement_is_the_exact_total_and_names_no_collector() -> None:
    quoted = QuotedTotal(110_000, 110_000)
    accepted = evaluate_settlement(
        quoted=quoted,
        tendered_vnd=110_000,
        collected_by_customer=False,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        on_account=True,
    )
    assert accepted == SettlementAccepted(SettlementShape.EXACT_PAYMENT_ON_ACCOUNT, 110_000)
    for tendered, collected in ((109_999, False), (110_000, True)):
        refused = evaluate_settlement(
            quoted=quoted,
            tendered_vnd=tendered,
            collected_by_customer=collected,
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            on_account=True,
        )
        assert isinstance(refused, SettlementNotSupported)


# --- the published terms --------------------------------------------------------------------------


def _terms() -> dict[str, object]:
    return dict(json.loads((ROOT / "templates/account-terms-dec-035.json").read_text("utf-8")))


def test_the_template_states_dec_035() -> None:
    terms = parse_account_terms(_terms())
    assert terms.due_day_of_next_month == 15
    assert terms.recommended_starting_limit_vnd == RECOMMENDED_STARTING_LIMIT_VND


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("due_day_of_next_month", 20),
        ("statement_period", "WEEK"),
        ("timezone", "UTC"),
        ("overdue_blocks_unpaid_handover", False),
        ("decision_ref", "DEC-010"),
        ("recommended_starting_limit_vnd", 5_000_000),
        ("text_vi", "không có ngày"),
    ],
)
def test_any_other_terms_are_a_different_decision(key: str, value: object) -> None:
    document = _terms()
    document[key] = value
    with pytest.raises(AccountTermsError):
        parse_account_terms(document)
