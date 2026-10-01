"""Round 9b (J6b): legacy invoice data where one order sits on two live requests is flagged.

Before `INVOICE-TRUTH-009` nothing stopped an order from having its own invoice request while the
account month it was charged to had one too: the month's request was issued as a whole, with no
snapshot. `0068` then backfilled that month's snapshot with every order charged to it -- including
the ones with a request of their own -- so the same order now stands on two live requests, and a
bookkeeper reading either would invoice it a second time. Nothing in the data is wrong to change
(the invoices exist), so the read says so on both: `ON_ANOTHER_REQUEST`, naming the other request,
on the request and in the download.

The legacy rows are written the way the pre-`0068` repository wrote them (`test_migration_0068_
populated.py`'s helpers); the database is migrated to head, and only the current code reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from nha_trang_laundry_db.invoice_requests import EXPORT_COLUMNS
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.accounts import statement_month
from test_customer_accounts import Shop
from test_invoice_requests import _read
from test_invoice_truth import FLAG_COLUMN, _export
from test_migration_0068_populated import (
    _create_as_before,
    _issue_as_before,
    _lines,
    migrations_before,
    scratch_database,
)

__all__ = ["migrations_before", "scratch_database"]


def _account_id(connection: Any, customer_id: Any) -> Any:
    row = connection.execute(
        "SELECT id FROM customer_accounts WHERE customer_id = %s", (customer_id,)
    ).fetchone()
    assert row is not None
    return row[0]


def test_an_order_on_its_own_request_and_on_a_backfilled_month_is_flagged_on_both(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        apply_migrations(connection, migrations_before)
        shop = Shop(connection)
        closer = shop.owner.staff_user_id
        homestay = shop.customer()
        shop.open(homestay, 5_000_000)
        own_issued, own_open, month_only = (shop.ready_order(homestay) for _ in range(3))
        for order_id in (own_issued, own_open, month_only):
            shop.charge(order_id)
        # Before 0068: two orders asked for their own invoices, one of them issued; the month
        # was requested and issued as a whole.
        issued_request = _create_as_before(shop, order_id=own_issued)
        _issue_as_before(connection, issued_request, "11", closer)
        open_request = _create_as_before(shop, order_id=own_open)
        month_request = _create_as_before(
            shop,
            account_id=_account_id(connection, homestay),
            month=statement_month(datetime.now(UTC)),
        )
        _issue_as_before(connection, month_request, "12", closer)

        apply_migrations(connection)

        # 0068's backfill fixed the month at every order charged to it.
        assert {row[0] for row in _lines(connection, month_request)} == {
            own_issued,
            own_open,
            month_only,
        }
        month = _read(shop, month_request)
        issued = _read(shop, issued_request)
        opened = _read(shop, open_request)
        assert month.flags == ("ON_ANOTHER_REQUEST",)
        assert month.other_request_codes == (issued.request_code, opened.request_code)
        assert issued.flags == ("ON_ANOTHER_REQUEST",)
        assert issued.other_request_codes == (month.request_code,)
        # An open request is flagged too: issuing it as it stands would invoice the order twice.
        assert opened.flags == ("ON_ANOTHER_REQUEST",)
        assert opened.other_request_codes == (month.request_code,)

        # The bookkeeper's download lists the open one and both issued ones, each with the sentence.
        rows = _export(shop)
        flag_at = EXPORT_COLUMNS.index(FLAG_COLUMN)
        flagged = {row[0]: row[flag_at] for row in rows if row[flag_at]}
        assert set(flagged) >= {month.request_code, issued.request_code, opened.request_code}
        assert issued.request_code in flagged[month.request_code]
        assert "không xuất hóa đơn hai lần" in flagged[issued.request_code]
