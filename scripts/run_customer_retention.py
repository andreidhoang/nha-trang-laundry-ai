"""Erase every customer record with no order for 24 months (`DEC-034`), keeping the orders.

`CUSTOMER-001`. The published privacy notice tells a customer the shop deletes their details after
24 months without an order; this is the job that makes the sentence true. It mirrors the existing
retention runs in shape -- an owner runs it, each disposal is its own audited transaction, a run
reports what it did -- and differs in what it disposes of: a customer record is *redacted*, not
purged. Every personal column is nulled and the row stays, so orders keep their customer key and
the money keeps its history (`DEC-008`'s financial schedule governs orders, not a name and number).

A record is due when it, every order and intake taken for it, and every order under a ticket or
binding linked to it are all older than the cutoff -- and it has no open order at all, whatever its
age. A record that became active between the selection and its erasure is skipped, never erased.

Only an active `OWNER_ADMIN` may run it (`--actor-id`); each erasure is audited with that owner as
actor and reason `RETENTION`. `--dry-run` lists how many are due and writes nothing. `--as-of`
judges at another instant (ISO 8601, timezone required) -- for a rehearsal, never to backdate.

Usage:
    DATABASE_URL=... uv run python scripts/run_customer_retention.py --actor-id <owner> [--dry-run]
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
from nha_trang_laundry_db.customers import RETENTION_BATCH_MAX, run_customer_retention
from nha_trang_laundry_db.store_access import StoreAccessError


def _instant(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError("--as-of needs a timezone, e.g. 2026-09-25T03:00:00+07:00")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", required=True, type=UUID, help="the OWNER_ADMIN running it")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--as-of", type=_instant, default=None)
    parser.add_argument("--batch", type=int, default=RETENTION_BATCH_MAX)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    as_of = arguments.as_of or datetime.now(UTC)
    try:
        with psycopg.connect(arguments.database_url, autocommit=True) as connection:
            outcome = run_customer_retention(
                connection,
                actor_id=arguments.actor_id,
                as_of=as_of,
                batch=arguments.batch,
                dry_run=arguments.dry_run,
            )
    except StoreAccessError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    verb = "due (dry run, nothing written)" if arguments.dry_run else "erased"
    print(
        f"customer retention as of {outcome.as_of.isoformat()} (cutoff "
        f"{outcome.cutoff.isoformat()}): {len(outcome.erased)} {verb}"
        + ("; more are due -- run again" if outcome.more_due else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
