"""Create the store a deployment works in, once, before anyone is assigned to it."""

from __future__ import annotations

# Put this directory on sys.path before importing the workspace bootstrap, so the script
# behaves identically whether it is run as __main__ or loaded by file path from a test.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from uuid import UUID, uuid4

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.stores import StoreRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="what the people who work there call it")
    parser.add_argument(
        "--store-id",
        help="an existing identifier to register; omit to mint one",
    )
    parser.add_argument(
        "--actor-id",
        help="the staff UUID of the person creating it, from scripts/bootstrap_owner.py",
    )
    arguments = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required to bootstrap a store")

    store_id = UUID(arguments.store_id) if arguments.store_id else uuid4()
    actor_id = UUID(arguments.actor_id) if arguments.actor_id else None

    with psycopg.connect(database_url) as connection:
        stored = StoreRepository.create(
            connection,
            store_id=store_id,
            name=arguments.name,
            created_by=actor_id,
            correlation_id=uuid4(),
        )
    if stored.created:
        print(f"store {stored.store_id} created as {stored.name!r}")
    else:
        print(f"store {stored.store_id} already exists as {stored.name!r}; nothing changed")
    print("assign staff to it from the console; OWNER_ADMIN is not implicitly a member.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
