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
