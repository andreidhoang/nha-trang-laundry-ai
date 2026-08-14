"""Publish the owner-confirmed pricebook as an immutable configuration version.

`QUOTE-COMMAND-001` made the runtime read prices from a published configuration rather than from a
file, which is the right shape and leaves an obvious question: who publishes it. This does, and it
is deliberately a script a human runs rather than something the API does on startup. A price list
that appears because a process booted is not an approved price list.

The logic lives in `nha_trang_laundry_db.pricebook` so that tests can reach it; this is the CLI.

Usage:
    DATABASE_URL=... uv run python scripts/publish_pricebook.py --actor-id <staff-uuid>
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
from nha_trang_laundry_db.pricebook import publish_pricebook

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "services-pricebook.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="staff user publishing this pricebook; attribution is not optional",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", type=_Path, default=SOURCE)
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not arguments.source.is_file():
        raise SystemExit(f"{arguments.source} is missing")

    with psycopg.connect(arguments.database_url) as connection:
        digest, created = publish_pricebook(
            connection, actor_id=arguments.actor_id, source=arguments.source.read_bytes()
        )
    state = "published" if created else "already published"
    print(f"pricebook {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
