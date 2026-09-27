"""Close a month for every account: freeze its statement as the statement query reads it.

`PAYMENT-002` (`DEC-035`). An account customer's statement is the calendar month in the shop's
time zone -- opening balance, charges, payments, closing balance, due by the 15th of the next month
-- and is always readable live from the ledgers. At month close the owner freezes it, so the
figures a customer was sent are kept exactly, with the query version that produced them.

**Only an active OWNER_ADMIN can run it.** The month must have ended in `Asia/Ho_Chi_Minh`
(`MONTH_NOT_ENDED` otherwise). An account already frozen for the month is left as it is, so running
it twice changes nothing. Each frozen statement commits with its event, audit and outbox rows.

The overdue block does not wait for this script: whether money charged in a month is still unpaid
after the 15th is read from the ledgers themselves, so a month the owner forgot to close still
blocks. Freezing is the record, not the rule.

Usage:
    DATABASE_URL=... uv run python scripts/close_account_statements.py \\
        --actor-id <owner-staff-uuid> --month 2026-09 [--store-id <uuid>]
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from datetime import UTC, datetime
from uuid import UUID

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.accounts import freeze_statements
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.accounts import AccountRuleError, month_label, parse_month


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--actor-id", required=True, type=UUID, help="the OWNER_ADMIN closing it")
    parser.add_argument("--month", required=True, help="the month to close, YYYY-MM")
    parser.add_argument("--store-id", type=UUID, default=None, help="one store only")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    try:
        month = parse_month(arguments.month)
        with psycopg.connect(arguments.database_url, autocommit=True) as connection:
            outcomes = freeze_statements(
                connection,
                actor_id=arguments.actor_id,
                month=month,
                now=datetime.now(UTC),
                store_id=arguments.store_id,
            )
    except AccountRuleError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 2
    except StoreAccessError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    frozen = sum(1 for item in outcomes if item.created)
    print(
        f"{month_label(month)}: {frozen} statement(s) frozen, "
        f"{len(outcomes) - frozen} already frozen"
    )
    for item in outcomes:
        state = "frozen" if item.created else "unchanged"
        print(
            f"  {item.account_id} {state}: closing {item.figures.closing_vnd} VND, "
            f"due {item.figures.due_on.isoformat()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
