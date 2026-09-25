"""Publish the owner's storage policy: the fee for laundry left waiting, and when it is disposed of.

`UNCLAIMED-001` (`DEC-036`). The shop's own terms: *"Lấy đồ trong 20 ngày; sau đó tính phí lưu kho;
sau 60 ngày hiện có điều khoản thanh lý."* The days are the owner's; the fee was never written down,
so `DEC-036` recommends the figures in `templates/storage-policy-dec-036.json`:

* free through day 20 after the laundry is ready;
* from day 21, 5.000 ₫ per order per started day (shop-local calendar days), capped at 50% of the
  order's quoted total;
* from day 60, the owner may dispose of it (thanh lý) once at least 3 contact attempts are recorded
  on at least 2 different days.

Publishing that document is the owner's confirmation of those figures. The receipt then prints the
rule in one line, the order page adds the fee to what is owed at pickup, and the owner's *Thanh lý*
becomes available when its conditions are met.

**Only an active OWNER_ADMIN can publish it.** `--actor-id` must be the owner's own staff id; any
other id is refused and nothing is written. It is a script a human runs, never a seed applied on
boot: a fee that appears because a process started is a fee nobody agreed to charge.

Until this is run no fee is charged and no disposal is offered; the waiting list ("Đồ chờ lấy") and
the contact attempts work regardless. That is the intended state of a fresh deployment.
`--withdraw` publishes the reversal `DEC-036` names: no fee and no disposal from then on; fees
already paid stay on the orders they were paid on. Edit the figures and publish again to change
them; an identical document already in force changes nothing.

Usage:
    DATABASE_URL=... uv run python scripts/publish_storage_policy.py --actor-id <owner-staff-uuid>
    DATABASE_URL=... uv run python scripts/publish_storage_policy.py \\
        --actor-id <owner-staff-uuid> --withdraw
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
from nha_trang_laundry_db.storage_fees import (
    StoragePolicyAuthorizationError,
    publish_storage_policy,
)
from nha_trang_laundry_domain.unclaimed import (
    StoragePolicyError,
    parse_storage_policy,
    receipt_line_vi,
    withdrawal_document,
)

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "storage-policy-dec-036.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="the OWNER_ADMIN publishing this policy; any other staff id is refused",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", type=_Path, default=SOURCE)
    parser.add_argument(
        "--withdraw",
        action="store_true",
        help="publish the withdrawal: no fee and no disposal from now on (DEC-036 reversal)",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")

    if arguments.withdraw:
        payload: dict[str, object] = withdrawal_document()
    else:
        if not arguments.source.is_file():
            raise SystemExit(f"{arguments.source} is missing")
        payload = json.loads(arguments.source.read_text(encoding="utf-8"))
        try:
            policy = parse_storage_policy(payload)
        except StoragePolicyError as error:
            print(f"refused: {error}", file=_sys.stderr)
            return 2
        print(f"receipt line: {receipt_line_vi(policy)}")
    try:
        with psycopg.connect(arguments.database_url) as connection:
            digest, created = publish_storage_policy(
                connection, actor_id=arguments.actor_id, payload=payload
            )
    except StoragePolicyAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    state = "published" if created else "already in force"
    what = "storage policy withdrawal" if arguments.withdraw else "storage policy"
    print(f"{what} {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
