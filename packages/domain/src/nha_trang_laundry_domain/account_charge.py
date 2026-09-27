"""An account's two money moves: an order leaving on it, and a payment against it. `PAYMENT-002`.

*Giao đồ — ghi công nợ* and *Thu công nợ*, the B2B half of `DEC-035`.

The decision is `settlement.goods_may_leave` with the account as its second input; this module is
its caller for the one command that puts an order on the account, and states what else must be
true of the order first -- the same order facts the counter's payment and pickup already read.

**What the order becomes.** Its balance reads `ON_ACCOUNT`: the goods have left, and what the order
still owed is money owed *on the account*, not money collected. That is a balance state rather than
a flag, for three reasons the schema already gives:

* `ON_ACCOUNT` has been a member of `OrderBalanceStatus`, of the `orders.balance_status` CHECK
  (`0007`) and of `transition_commercial`'s settled-balance guard since the first order migration --
  the state machine was built to close an account order and never had a writer;
* the balance is what every money reader already asks. `payments.evaluate_payment` refuses a
  counter payment on it (`NOTHING_OWED`: the money is collected through the account), `CANCEL`
  refuses to guess at it (`NOT_SUPPORTED`), and takings read only the payment ledger, so an order on
  the account adds nothing to the day's money until the account is paid;
* a flag beside `UNPAID` would let two facts disagree ("unpaid, but on the account"), and `0056`'s
  rule that the balance and the ledger agree at every commit would have to learn to read both.

The account's payment moves the balance on: `ON_ACCOUNT` while any of the charge is unpaid, `PAID`
(with the settlement row whose shape `evaluate_settlement` decides, `EXACT_PAYMENT_ON_ACCOUNT`) once
the allocations cover it.

Pure: no clock, no database, no environment.
"""

from __future__ import annotations

from dataclasses import dataclass

from nha_trang_laundry_domain.accounts import (
    AccountHandoverFacts,
    AccountRefusal,
    AccountStanding,
    account_handover_refusal,
)
from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.payments import (
    BANK_REF_PATTERN,
    PAYABLE_BALANCES,
    PaymentMethod,
    PaymentRefusal,
)
from nha_trang_laundry_domain.settlement import (
    MAX_SETTLEMENT_VND,
    goods_may_leave,
    handover_refusal,
)


@dataclass(frozen=True, slots=True)
class AccountChargeAccepted:
    """The order leaves on the account: `amount_vnd`, what it still owed, is charged to it."""

    amount_vnd: int
    owed_vnd: int
    paid_before_vnd: int
    outstanding_after_vnd: int
    collected_by_customer: bool


@dataclass(frozen=True, slots=True)
class AccountChargeRefused:
    refusal: AccountRefusal

    @property
    def reason_code(self) -> str:
        return self.refusal.value


AccountChargeOutcome = AccountChargeAccepted | AccountChargeRefused


def evaluate_account_charge(
    *,
    commercial: CommercialOrderStatus,
    production: ProductionStatus,
    balance: OrderBalanceStatus,
    fulfillment_mode: FulfillmentMode,
    self_collection_recorded: bool,
    owed_vnd: int | None,
    paid_vnd: int,
    collected_by_customer: bool,
    standing: AccountStanding | None,
) -> AccountChargeOutcome:
    """Decide whether this order's finished goods leave now, with what it owes put on the account.

    Every input is a stored fact read under the account's and the order's row locks, except
    `collected_by_customer`, the staff member's word that the customer takes the bag now. `owed_vnd`
    is `payments.owed_total` of the order's charges (`None` when the quote presents no single
    total) and `paid_vnd` the payment ledger's sum, by PostgreSQL.

    * A customer who collects at the counter takes the goods in the same press: the charge *is*
      the handover (`ACCOUNT_CHARGE_IS_THE_HANDOVER` without the tick). A delivery order is charged
      before the courier takes it back, and never "collected at the counter".
    * The laundry must be finished (`settlement.handover_refusal`), as for any handover.
    * The account decides the rest, through `goods_may_leave(balance, account)`.
    """

    if commercial is not CommercialOrderStatus.ACTIVE:
        return AccountChargeRefused(AccountRefusal.ORDER_NOT_ACTIVE)
    if standing is None:
        return AccountChargeRefused(AccountRefusal.NOT_AN_ACCOUNT_CUSTOMER)
    if balance not in PAYABLE_BALANCES:
        return AccountChargeRefused(AccountRefusal.NOTHING_OWED)
    if owed_vnd is None:
        return AccountChargeRefused(AccountRefusal.NO_PRESENTABLE_TOTAL)
    if not _whole(owed_vnd) or not _whole(paid_vnd) or paid_vnd > owed_vnd:
        raise ValueError("the order's money does not add up")
    remaining = owed_vnd - paid_vnd
    if remaining == 0:
        return AccountChargeRefused(AccountRefusal.NOTHING_OWED)
    returns = fulfillment_mode in MODES_EXPECTING_RETURN
    if returns and collected_by_customer:
        return AccountChargeRefused(AccountRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER)
    if not returns and not collected_by_customer:
        return AccountChargeRefused(AccountRefusal.ACCOUNT_CHARGE_IS_THE_HANDOVER)
    if self_collection_recorded:
        return AccountChargeRefused(AccountRefusal.ALREADY_COLLECTED)
    if handover_refusal(production) is not None:
        return AccountChargeRefused(AccountRefusal.GOODS_NOT_READY_FOR_HANDOVER)
    facts = AccountHandoverFacts(standing=standing, order_remaining_vnd=remaining)
    if not goods_may_leave(balance, facts):
        refusal = account_handover_refusal(facts)
        if refusal is None:  # pragma: no cover - goods_may_leave said no only through the account
            raise AssertionError("goods_may_leave refused an order the account admits")
        return AccountChargeRefused(refusal)
    return AccountChargeAccepted(
        amount_vnd=remaining,
        owed_vnd=owed_vnd,
        paid_before_vnd=paid_vnd,
        outstanding_after_vnd=standing.outstanding_vnd + remaining,
        collected_by_customer=collected_by_customer,
    )


def account_payment_refusal(
    *,
    outstanding_vnd: int,
    amount_vnd: int,
    method: PaymentMethod,
    transfer_seen: bool,
    bank_ref_last: str | None,
) -> PaymentRefusal | None:
    """Whether a payment against the account may be recorded. `None` means it may.

    The counter's rules for one order (`payments.evaluate_payment`), stated over the account's
    outstanding total -- the account's charges less its payments, summed by PostgreSQL under the
    account's row lock -- instead of one order's remaining amount: 1 đồng up to what the account
    owes; more is refused `OVERPAYMENT_REFUSED` (the counter gives change, no credit is created); a
    transfer only once seen; the bank reference only on a transfer, 2 to 12 letters or digits
    (`bank_ref_last` already normalised by `payments.normalise_bank_ref`). A suspended account still
    takes payments: what it owes stays owed.
    """

    if not _whole(outstanding_vnd):
        raise ValueError("the outstanding total is a non-negative whole number of đồng")
    if outstanding_vnd == 0:
        return PaymentRefusal.NOTHING_OWED
    if not _whole(amount_vnd) or amount_vnd < 1:
        return PaymentRefusal.PAYMENT_AMOUNT_INVALID
    if method is PaymentMethod.CHUYEN_KHOAN and not transfer_seen:
        return PaymentRefusal.TRANSFER_NOT_SEEN
    if bank_ref_last is not None and (
        method is not PaymentMethod.CHUYEN_KHOAN or not BANK_REF_PATTERN.fullmatch(bank_ref_last)
    ):
        return PaymentRefusal.BANK_REF_INVALID
    if amount_vnd > outstanding_vnd:
        return PaymentRefusal.OVERPAYMENT_REFUSED
    return None


def _whole(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SETTLEMENT_VND
    )


__all__ = [
    "AccountChargeAccepted",
    "AccountChargeOutcome",
    "AccountChargeRefused",
    "account_payment_refusal",
    "evaluate_account_charge",
]
