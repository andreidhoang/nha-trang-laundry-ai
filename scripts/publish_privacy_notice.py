"""Publish the customer privacy notice as an immutable configuration version (`DEC-034`).

`CUSTOMER-001`. The shop keeps a customer list -- phone, the name a customer gives, an address, a
short note -- only under a notice that tells the customer what is kept, why, for how long and how to
ask for deletion. The notice text is `docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md`; the legal entity
and the hotline it names are read from the lines of `BUSINESS_TRUTH_INTAKE.md` the owner confirmed.
Publishing it is the owner confirming that text. The software asserts no legal basis of its own.

**Only an active OWNER_ADMIN can publish it.** `--actor-id` must be the owner's own staff id; any
other id is refused and nothing is written. A script a human runs, never a seed the API applies on
boot -- a notice that appears because a process started is a notice nobody confirmed.

Until this is run, every attempt to record a customer is refused with `PRIVACY_NOTICE_UNPUBLISHED`,
and the counter keeps serving walk-ins with a ticket. That is the intended state of a fresh
deployment. Publishing the notice already in force changes nothing; an edited text is a new version.

Usage:
    DATABASE_URL=... uv run python scripts/publish_privacy_notice.py --actor-id <owner-staff-uuid>
    uv run python scripts/publish_privacy_notice.py --dry-run     # validate and print the digest
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
from nha_trang_laundry_db.configurations import snapshot_hash
from nha_trang_laundry_db.privacy_notice import (
    PrivacyNoticeAuthorizationError,
    publish_privacy_notice,
)
from nha_trang_laundry_domain.customers import PrivacyNoticeError, notice_payload_from_documents

ROOT = _Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md"
BUSINESS_TRUTH = ROOT / "BUSINESS_TRUTH_INTAKE.md"


def build_payload(
    source: _Path = SOURCE, business_truth: _Path = BUSINESS_TRUTH
) -> dict[str, object]:
    return notice_payload_from_documents(
        notice_markdown=source.read_text(encoding="utf-8"),
        business_truth=business_truth.read_text(encoding="utf-8"),
        source=source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else source.name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-id",
        type=UUID,
        help="the OWNER_ADMIN publishing this notice; any other staff id is refused",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", type=_Path, default=SOURCE)
    parser.add_argument(
        "--dry-run", action="store_true", help="validate the documents and print the digest only"
    )
    arguments = parser.parse_args()
    if not arguments.source.is_file():
        raise SystemExit(f"{arguments.source} is missing")
    try:
        payload = build_payload(arguments.source)
    except PrivacyNoticeError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 2
    if arguments.dry_run:
        print(f"customer privacy notice is publishable: JCS-SHA256-V1:{snapshot_hash(payload)}")
        return 0
    if arguments.actor_id is None:
        raise SystemExit("--actor-id is required to publish")
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    try:
        with psycopg.connect(arguments.database_url) as connection:
            digest, created = publish_privacy_notice(
                connection, actor_id=arguments.actor_id, payload=payload
            )
    except PrivacyNoticeAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    state = "published" if created else "already in force"
    print(f"customer privacy notice {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
