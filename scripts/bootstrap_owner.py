"""Bind the first named OWNER_ADMIN to an already verified OIDC subject."""

from __future__ import annotations

import argparse
import os
from uuid import uuid4

import psycopg
from nha_trang_laundry_db.identity import IdentityRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oidc-subject", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--email")
    arguments = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required to bootstrap the first owner")

    with psycopg.connect(database_url) as connection:
        staff_id = IdentityRepository().bootstrap_owner(
            connection,
            oidc_subject=arguments.oidc_subject,
            display_name=arguments.display_name,
            email=arguments.email,
            correlation_id=uuid4(),
        )
    print(f"OWNER_ADMIN binding verified for staff ID {staff_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
