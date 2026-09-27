"""`EXPORT-PAYMENTS-001`: what one order's money cells say in the owner's export.

The ledger's sums arrive from PostgreSQL; this table pins what the domain makes of them -- what is
owed, what remains -- for every shape an exported order can be in, including the export's own rule
that a cancelled order has nothing remaining.
"""

from __future__ import annotations

import pytest
from nha_trang_laundry_domain.catalog import CommercialOrderStatus
from nha_trang_laundry_domain.export_money import ExportedMoney, exported_money
from nha_trang_laundry_domain.settlement import QuotedTotal

ACTIVE = CommercialOrderStatus.ACTIVE
CANCELLED = CommercialOrderStatus.CANCELLED
COMPLETED = CommercialOrderStatus.COMPLETED
EXACT = QuotedTotal(110_000, 110_000)
RANGE = QuotedTotal(100_000, 140_000)
UNPRICED = QuotedTotal(None, None)


@pytest.mark.parametrize(
    ("commercial", "quoted", "cash", "transfer", "paid", "expected"),
    [
        # Nothing taken yet: 0 paid (an empty ledger is a fact), everything remains.
        (ACTIVE, EXACT, 0, 0, 0, ExportedMoney(110_000, 0, 0, 0, 110_000)),
        # A 50.000 đ deposit by transfer: the rest remains.
        (ACTIVE, EXACT, 0, 50_000, 50_000, ExportedMoney(110_000, 0, 50_000, 50_000, 60_000)),
        # Deposit by transfer, the rest in cash: nothing remains, the split is kept.
        (
            COMPLETED,
            EXACT,
            60_000,
            50_000,
            110_000,
            ExportedMoney(110_000, 60_000, 50_000, 110_000, 0),
        ),
        # A cancelled order after a deposit (refunded elsewhere): 0 remains, never 60.000 đ.
        (CANCELLED, EXACT, 0, 50_000, 50_000, ExportedMoney(110_000, 0, 50_000, 50_000, 0)),
        # Cancelled with nothing taken: nothing is owed on it either.
        (CANCELLED, EXACT, 0, 0, 0, ExportedMoney(110_000, 0, 0, 0, 0)),
        # A range or an unresolved fee has no single total: owed and remaining are unknown, not 0.
        (ACTIVE, RANGE, 0, 0, 0, ExportedMoney(None, 0, 0, 0, None)),
        (ACTIVE, UNPRICED, 0, 0, 0, ExportedMoney(None, 0, 0, 0, None)),
        # ...except on a cancelled order, where what remains is known to be nothing.
        (CANCELLED, RANGE, 0, 0, 0, ExportedMoney(None, 0, 0, 0, 0)),
        # A bill a credit covered in full: 0 owed, 0 paid, 0 remaining.
        (COMPLETED, QuotedTotal(0, 0), 0, 0, 0, ExportedMoney(0, 0, 0, 0, 0)),
    ],
)
def test_the_money_cells_of_an_exported_order(
    commercial: CommercialOrderStatus,
    quoted: QuotedTotal,
    cash: int,
    transfer: int,
    paid: int,
    expected: ExportedMoney,
) -> None:
    assert (
        exported_money(
            commercial=commercial,
            quoted=quoted,
            paid_cash_vnd=cash,
            paid_transfer_vnd=transfer,
            paid_vnd=paid,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("cash", "transfer", "paid"),
    [
        # The split does not add up to the ledger's total (there are exactly two methods).
        (10_000, 20_000, 40_000),
        # Payments above what is owed: the ledger refuses this, so reading it is a contradiction.
        (0, 120_000, 120_000),
        # Not a whole non-negative number of đồng.
        (-1, 1, 0),
        (True, 0, 1),
    ],
)
def test_a_contradiction_is_refused_rather_than_written(
    cash: int, transfer: int, paid: int
) -> None:
    with pytest.raises(ValueError):
        exported_money(
            commercial=ACTIVE,
            quoted=EXACT,
            paid_cash_vnd=cash,
            paid_transfer_vnd=transfer,
            paid_vnd=paid,
        )


# --- round 7 wave 2 integration: the storage fee and the account ---------------------------------


@pytest.mark.parametrize(
    ("commercial", "cash", "transfer", "paid", "fee", "expected"),
    [
        # UNCLAIMED-001: a fee-bearing order settled at pickup -- the fee is owed, so the payments
        # equal what is owed and nothing remains (without the fee the file refused itself).
        (
            COMPLETED,
            135_000,
            0,
            135_000,
            25_000,
            ExportedMoney(135_000, 135_000, 0, 135_000, 0),
        ),
        # Still on the shelf, a 50.000 đ deposit taken: the accrued fee is owed on top.
        (ACTIVE, 50_000, 0, 50_000, 25_000, ExportedMoney(135_000, 50_000, 0, 50_000, 85_000)),
        # PAYMENT-002: an order on the account is owed, not paid -- nothing in the ledger yet...
        (COMPLETED, 0, 0, 0, 0, ExportedMoney(110_000, 0, 0, 0, 110_000)),
        # ...then part of an account payment by transfer reached it: that part is paid.
        (COMPLETED, 0, 40_000, 40_000, 0, ExportedMoney(110_000, 0, 40_000, 40_000, 70_000)),
        # An account order that waited past the free days: the fee fixed with its charge is owed.
        (COMPLETED, 0, 0, 0, 25_000, ExportedMoney(135_000, 0, 0, 0, 135_000)),
        # A disposed-of order (thanh lý) closes CANCELLED: what was paid is kept, nothing remains.
        (CANCELLED, 30_000, 0, 30_000, 25_000, ExportedMoney(135_000, 30_000, 0, 30_000, 0)),
    ],
)
def test_the_storage_fee_is_owed_and_an_account_order_is_owed_not_paid(
    commercial: CommercialOrderStatus,
    cash: int,
    transfer: int,
    paid: int,
    fee: int,
    expected: ExportedMoney,
) -> None:
    assert (
        exported_money(
            commercial=commercial,
            quoted=EXACT,
            paid_cash_vnd=cash,
            paid_transfer_vnd=transfer,
            paid_vnd=paid,
            storage_fee_vnd=fee,
        )
        == expected
    )


def test_payments_above_the_total_and_the_fee_are_still_refused() -> None:
    with pytest.raises(ValueError):
        exported_money(
            commercial=COMPLETED,
            quoted=EXACT,
            paid_cash_vnd=140_000,
            paid_transfer_vnd=0,
            paid_vnd=140_000,
            storage_fee_vnd=25_000,
        )
