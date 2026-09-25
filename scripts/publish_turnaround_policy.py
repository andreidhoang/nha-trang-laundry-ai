"""Publish the owner's turnaround policy -- the promised-ready time ("hẹn trả") rules.

`PROMISE-001` (`DEC-037`). Every order taken at the counter gets a promised-ready time at *Nhận đồ*,
counted in opening hours from the owner-confirmed sheets:

* `templates/service-sla.csv` -- standard clothing 8 hours; shoes, curtains, blankets 24 or 48 hours
  (staff choice, 48 when silent); special items set by staff; the fastest (express) turnaround;
* `templates/business-calendar-rules.csv` -- open 08:00-20:00, closed 30/4 and 1/5, six Tết days and
  up to two ad-hoc days a year;
* `templates/services-pricebook.csv` -- which service belongs to which of those rules;
* `--tet-dates` -- this year's six Tết days, which the calendar sheet says must be entered year by
  year. A promise that would run through late January or February of a year without its Tết days
  published is not computed; the counter asks the staff member to set it.

**Only an active OWNER_ADMIN can publish it.** `--actor-id` must be the owner's own staff id; any
other id is refused and nothing is written. It is a script a human runs, never a seed applied on
boot: a promise to customers that appears because a process started is a promise nobody made.

Until this is run, orders carry no promise and nothing refuses -- the counter works as before. That
is the intended state of a fresh deployment. `--withdraw` publishes the reversal `DEC-037` names:
orders taken afterwards carry no promise again. An identical document already in force changes
nothing.

Usage:
    DATABASE_URL=... uv run python scripts/publish_turnaround_policy.py \\
        --actor-id <owner-staff-uuid> --tet-dates 2027-02-05,2027-02-06,...(six days)
    DATABASE_URL=... uv run python scripts/publish_turnaround_policy.py \\
        --actor-id <owner-staff-uuid> --withdraw
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from datetime import date
from hashlib import sha256
from uuid import UUID

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.promise_policy import (
    TurnaroundPolicyAuthorizationError,
    publish_turnaround_policy,
    withdrawal_document,
)
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv
from nha_trang_laundry_domain.promise import TurnaroundPolicyError
from nha_trang_laundry_domain.turnaround_source import build_turnaround_policy

ROOT = _Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def _dates(text: str) -> list[date]:
    try:
        return [date.fromisoformat(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"dates are YYYY-MM-DD, comma-separated: {error}"
        ) from error


def build_document(
    templates: _Path, *, tet_dates: list[date], adhoc_dates: list[date]
) -> dict[str, object]:
    """The document the owner publishes, built from the three sheets and the dates given."""

    pricebook = (templates / "services-pricebook.csv").read_bytes()
    book = import_pricebook_csv(pricebook)
    return build_turnaround_policy(
        sla_csv=(templates / "service-sla.csv").read_text(encoding="utf-8"),
        calendar_csv=(templates / "business-calendar-rules.csv").read_text(encoding="utf-8"),
        services=[(service.code, service.category) for service in book.services],
        tet_dates=tet_dates,
        adhoc_dates=adhoc_dates,
        pricebook_sha256=sha256(pricebook).hexdigest(),
    )


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
    parser.add_argument(
        "--tet-dates",
        type=_dates,
        help="this year's six Tết closing days, YYYY-MM-DD, comma-separated",
    )
    parser.add_argument(
        "--closed-dates",
        type=_dates,
        default=[],
        help="the owner's ad-hoc closed days (at most two a year), YYYY-MM-DD, comma-separated",
    )
    parser.add_argument("--templates", type=_Path, default=TEMPLATES)
    parser.add_argument(
        "--withdraw",
        action="store_true",
        help="publish the withdrawal: orders taken afterwards carry no promise (DEC-037 reversal)",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")

    if arguments.withdraw:
        if arguments.tet_dates or arguments.closed_dates:
            raise SystemExit("--withdraw takes no dates")
        payload: dict[str, object] = withdrawal_document()
    else:
        if not arguments.tet_dates:
            raise SystemExit("--tet-dates is required: the six Tết days of this year")
        try:
            payload = build_document(
                arguments.templates,
                tet_dates=arguments.tet_dates,
                adhoc_dates=arguments.closed_dates,
            )
        except TurnaroundPolicyError as error:
            print(f"refused: {error}", file=_sys.stderr)
            return 2
    try:
        with psycopg.connect(arguments.database_url) as connection:
            digest, created = publish_turnaround_policy(
                connection, actor_id=arguments.actor_id, payload=payload
            )
    except TurnaroundPolicyAuthorizationError as error:
        print(f"refused: {error}", file=_sys.stderr)
        return 3
    state = "published" if created else "already in force"
    what = "turnaround policy withdrawal" if arguments.withdraw else "turnaround policy"
    print(f"{what} {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
