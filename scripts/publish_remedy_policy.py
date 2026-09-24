"""Publish the owner's ratified `DEC-004` figures as an immutable configuration version.

`REMEDY-001` made the remedy path read its ceilings, windows and rates from a published
configuration rather than from constants, which is the right shape (invariant 11) and leaves the
obvious question: who publishes it. This does, and it is deliberately a script a human runs rather
than a seed the API applies on boot. A liability ceiling that appears because a process started is
not a ceiling anybody ratified -- which is precisely the mistake `CURRENT_PROMOTION` records.

Until this is run, every remedy request fails closed with `REMEDY_POLICY_UNPUBLISHED`. That is the
intended state of a fresh deployment, not a bug in it.

The logic lives in `nha_trang_laundry_db.remedies` so that tests can reach it; this is the CLI.

Usage:
    DATABASE_URL=... uv run python scripts/publish_remedy_policy.py --actor-id <staff-uuid>
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
from nha_trang_laundry_db.remedies import publish_remedy_policy

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "remedy-policy-dec-004.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="staff user publishing this policy; attribution is not optional",
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
        digest, created = publish_remedy_policy(
            connection, actor_id=arguments.actor_id, payload=payload
        )
    state = "published" if created else "already in force"
    print(f"remedy policy {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
