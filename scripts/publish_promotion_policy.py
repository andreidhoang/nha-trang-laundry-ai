"""Publish the owner's confirmed promotion programme as an immutable configuration version.

`PROMO-WIRING-001` made the quote path read its programme from a published configuration rather than
from `CURRENT_PROMOTION`, which is the right shape (invariants 4 and 11) and leaves the obvious
question: who publishes it. This does, and it is deliberately a script a human runs rather than a
seed the API applies on boot. A discount that appears because a process started is not a discount
anybody ratified -- which is precisely the mistake the constant records.

Until this is run, every quote composes at list price carrying `PROMOTION_NOT_PUBLISHED`. That is
the intended state of a fresh deployment, not a bug in it.

**The shipped document has already ended.** `templates/promotion-policy-dec-002.json` transcribes
the one programme the owner confirmed, and it ran 17/07/2026 to 31/08/2026 inclusive. Publishing it
today is still worth doing: it is what makes a quote say "chuong trinh da ket thuc" instead of
showing a bare zero, and it is what the owner edits -- dates, rate, services -- to start the next
one without a deploy. Publish a changed document and it becomes version 2; publish an identical one
and nothing happens, because republishing the same programme would make every outstanding quote
refuse its own acceptance for no reason.

The logic lives in `nha_trang_laundry_db.promotions` so that tests can reach it; this is the CLI.

Usage:
    DATABASE_URL=... uv run python scripts/publish_promotion_policy.py --actor-id <staff-uuid>
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
from nha_trang_laundry_db.promotions import publish_promotion_policy

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "promotion-policy-dec-002.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="staff user publishing this programme; attribution is not optional",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", type=_Path, default=SOURCE)
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not arguments.source.is_file():
        raise SystemExit(f"{arguments.source} is missing")

    payload = json.loads(arguments.source.read_text(encoding="utf-8"))
    with psycopg.connect(arguments.database_url) as connection:
        digest, created = publish_promotion_policy(
            connection, actor_id=arguments.actor_id, payload=payload
        )
    state = "published" if created else "already published"
    print(f"promotion policy {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
