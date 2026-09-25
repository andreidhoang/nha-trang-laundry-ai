"""Publish the owner's account terms (điều khoản công nợ) as an immutable configuration version.

`PAYMENT-002` (`DEC-035`, the B2B half). Account customers -- a hotel, a homestay or a spa taking
laundry unpaid and settling monthly -- run on terms the owner decided on 2026-09-25: the calendar
month as the statement, payment due by the 15th of the next month, a limit in VND the owner types
for each account (3.000.000 ₫ recommended, never enforced), and new orders paid at the counter while
a statement is overdue unless the owner lifts the block. Publishing
`templates/account-terms-dec-035.json` is the owner's confirmation that the shop offers credit on
those terms; the document must state exactly `DEC-035`'s values or it is refused.

**Only an active OWNER_ADMIN can publish it.** `--actor-id` must be the owner's own staff id; any
other id is refused and nothing is written. It is a script a human runs, never a seed applied on
boot: credit terms that appear because a process started are terms nobody offered.

Until this is run, no account is opened and no order leaves on one: both refuse
`ACCOUNT_TERMS_UNPUBLISHED`, and the customer's page says "Chủ tiệm cần công bố điều khoản công nợ".
That is the intended state of a fresh deployment. An identical document already in force changes
nothing.

Usage:
    DATABASE_URL=... uv run python scripts/publish_account_terms.py --actor-id <owner-staff-uuid>
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import json
import os
from uuid import UUID

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.account_terms import (
    AccountTermsAuthorizationError,
    publish_account_terms,
)
from nha_trang_laundry_domain.accounts import AccountTermsError

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "account-terms-dec-035.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="the OWNER_ADMIN publishing these terms; any other staff id is refused",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", type=_Path, default=SOURCE)
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not arguments.source.is_file():
        raise SystemExit(f"{arguments.source} is missing")

    payload = json.loads(arguments.source.read_text(encoding="utf-8"))
    try:
        with psycopg.connect(arguments.database_url) as connection:
            digest, created = publish_account_terms(
                connection, actor_id=arguments.actor_id, payload=payload
            )
    except AccountTermsError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 2
    except AccountTermsAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    state = "published" if created else "already in force"
    print(f"account terms {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
