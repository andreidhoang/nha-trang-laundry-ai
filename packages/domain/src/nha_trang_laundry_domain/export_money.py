"""What one order's money columns say in the owner's export. `EXPORT-PAYMENTS-001`.

`PAYMENT-001` (`DEC-035`) moved the counter's money onto the append-only `order_payments` ledger:
a deposit, part payments, each `TIEN_MAT` or `CHUYEN_KHOAN`. `order_settlements` still gets its one
row, but only when an order becomes paid in full. The export read the settlement alone, so a partly
paid order left the building with empty money cells -- a file that said nobody had paid anything on
an order the drawer and the bank both held money for.

The ledger's sums are PostgreSQL's (`paid_cash_vnd`, `paid_transfer_vnd`, `paid_vnd`), passed in.
What is owed and what remains are this module's, through the same `payments.payment_position` the
order read uses, so the file and the order page cannot disagree about an open order.

One rule is the export's own and is stated here rather than implied: **a cancelled order has 0
remaining.** `evaluate_payment` refuses every payment on an order that is not `ACTIVE`, and a
cancelled order never becomes active again, so nothing more will ever be taken on it. Writing
`owed - paid` there -- a cancelled deposit order would read "70.000 đ còn lại" -- would put a debt
in the owner's accountant's hands that nobody owes.

Pure: no clock, no database, no environment. Integer đồng only.
"""

from __future__ import annotations

from dataclasses import dataclass

from nha_trang_laundry_domain.catalog import CommercialOrderStatus
from nha_trang_laundry_domain.payments import owed_charges, payment_position
from nha_trang_laundry_domain.settlement import QuotedTotal


@dataclass(frozen=True, slots=True)
class ExportedMoney:
    """The export's ledger-derived money cells for one order.

    `owed_vnd` and `remaining_vnd` are `None` -- an empty cell, never 0 -- when the order's quote
    presents no single total (a range, an unresolved delivery fee): unknown is not zero. The one
    exception is a cancelled order's `remaining_vnd`, which is known: 0.
    """

    owed_vnd: int | None
    paid_cash_vnd: int
    paid_transfer_vnd: int
    paid_vnd: int
    remaining_vnd: int | None


def exported_money(
    *,
    commercial: CommercialOrderStatus,
    quoted: QuotedTotal,
    paid_cash_vnd: int,
    paid_transfer_vnd: int,
    paid_vnd: int,
) -> ExportedMoney:
    """The money cells for one exported order, from the SQL sums of its payment ledger.

    Raises `ValueError` on a contradiction rather than writing it: a sum that is not a whole
    non-negative number of đồng, a split by method that does not add up to the ledger's total
    (there are exactly two methods), or payments exceeding what is owed (`payment_position`). The
    database's `0056` triggers make each of these unreachable; a file that carried one anyway would
    be a signed document with a wrong number in it, so none is produced.
    """

    for amount in (paid_cash_vnd, paid_transfer_vnd, paid_vnd):
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError("a ledger sum must be a non-negative whole number of đồng")
    if paid_cash_vnd + paid_transfer_vnd != paid_vnd:
        raise ValueError("the cash and transfer sums do not add up to the ledger's total")
    position = payment_position(owed_charges(quoted), paid_vnd)
    remaining = 0 if commercial is CommercialOrderStatus.CANCELLED else position.remaining_vnd
    return ExportedMoney(
        owed_vnd=position.owed_vnd,
        paid_cash_vnd=paid_cash_vnd,
        paid_transfer_vnd=paid_transfer_vnd,
        paid_vnd=paid_vnd,
        remaining_vnd=remaining,
    )


__all__ = ["ExportedMoney", "exported_money"]
