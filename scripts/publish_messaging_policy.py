"""Publish the owner's transactional messaging policy as an immutable configuration version.

`CONSENT-TRANSACTIONAL-001` (`DEC-033`) made every service message -- including a staff member's
manual send -- rest on a basis the server can prove: the customer wrote on that channel recently,
or has an open order. Which bases count and the hour values are the owner's to confirm, because
together they are the shop's grounds for sending service messages under Vietnamese personal-data
rules. Publishing `templates/transactional-messaging-policy-dec-033.json` is that confirmation, and
the document says so in both languages.

**Only an active OWNER_ADMIN can publish it.** `--actor-id` must be the owner's own staff id; any
other id is refused and nothing is written. This is deliberately a script a human runs, never a
seed the API applies on boot: grounds that appear because a process started are grounds nobody
confirmed.

Until this is run, every service send is refused with `MESSAGING_POLICY_UNPUBLISHED` -- the
manual send panel says "Chủ tiệm chưa công bố chính sách tin dịch vụ". That is the intended state of
a fresh deployment, not a bug in it. Edit the document (hours, bases) and publish again to change
it; an identical document already in force changes nothing.

Usage:
    DATABASE_URL=... uv run python scripts/publish_messaging_policy.py --actor-id <owner-staff-uuid>
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
from nha_trang_laundry_db.service_messaging import (
    MessagingPolicyAuthorizationError,
    publish_messaging_policy,
)

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates" / "transactional-messaging-policy-dec-033.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-id",
        required=True,
        type=UUID,
        help="the OWNER_ADMIN publishing this policy; any other staff id is refused",
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
            digest, created = publish_messaging_policy(
                connection, actor_id=arguments.actor_id, payload=payload
            )
    except MessagingPolicyAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    state = "published" if created else "already in force"
    print(f"transactional messaging policy {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
