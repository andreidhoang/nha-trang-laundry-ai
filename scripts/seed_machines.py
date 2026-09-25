"""Register the shop's machines from the owner-confirmed machine master (`SHOP-CAPTURE-001`).

`DEC-038`: the machines in `templates/machine-master.csv` -- confirmed present and active by the
owner on 2026-07-27 -- become the list *Bắt đầu giặt* asks "Máy nào?" from. The owner adds, renames
or retires one afterwards in the console (Thêm → Máy giặt, sấy).

**Idempotent.** A machine is matched by its code (`WASH-01`). One the store already has -- seeded
before, or added by the owner under the same code -- is left exactly as it is, so re-running never
undoes a rename and never brings back a retired machine. Each machine registered is a material
change: the row, a `MACHINE_REGISTERED` event, an audit row and an outbox row, together, attributed
to the owner named by `--actor-id`. Anyone but an active owner assigned to the store is refused and
nothing is written.

Usage:
    DATABASE_URL=... uv run python scripts/seed_machines.py \
        --store-id <store-uuid> --actor-id <owner-staff-uuid>
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from uuid import UUID

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.shop_capture import (
    ShopCaptureAuthorizationError,
    machine_seeds_from_master,
    seed_machines,
)

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "machine-master.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", required=True, type=UUID)
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="the OWNER_ADMIN registering the machines; any other staff id is refused",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", type=_Path, default=SOURCE)
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not arguments.source.is_file():
        raise SystemExit(f"{arguments.source} is missing")

    seeds = machine_seeds_from_master(arguments.source.read_text(encoding="utf-8"))
    try:
        with psycopg.connect(arguments.database_url) as connection:
            created, present = seed_machines(
                connection,
                store_id=arguments.store_id,
                actor_id=arguments.actor_id,
                seeds=seeds,
            )
    except ShopCaptureAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    print(f"machines: {created} registered, {present} already present (of {len(seeds)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
