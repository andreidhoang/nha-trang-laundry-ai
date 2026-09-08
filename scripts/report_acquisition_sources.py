"""Which channel produced a paying order, counted rather than guessed.

This is the read side of `ACQUISITION-ATTRIBUTION-001`. Every order carries one
`acquisition_source`, attested by the staff member who took it and immutable afterwards, so the
question "where do our customers come from" has an answer that is a query instead of an argument.

**Read the four caveats this report prints before spending money on it.** They are printed rather
than documented because a channel table is exactly the kind of output that gets screenshotted and
acted on, and every one of them changes what the numbers mean:

    it counts orders, not profit      contribution per order and per kg are unmeasured
                                      (`SHOP-INSTRUMENT-001` is BLOCKED), so a channel that brings
                                      many small orders can outrank one that brings the shop's
                                      margin and look like the better investment
    UNKNOWN is data                   it is what the counter recorded when nobody asked. A large
                                      UNKNOWN share is a fact about the counter's habits, not a
                                      gap to be redistributed across the other rows
    a zero is not an absence          a channel with no orders may never have been tried. The
                                      report distinguishes the two rather than letting a reader
                                      infer failure from a zero
    RETURNING is the customer's word  no customer record exists (`DEC-015`), so nothing checked it

Nothing here writes. Nothing here is reachable by any agent tool.
"""

from __future__ import annotations

# Put this directory on sys.path before importing the workspace bootstrap, so the script behaves
# identically whether it is run as __main__ or loaded by file path from a test.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from datetime import date

import psycopg
import workspace_env  # noqa: F401  (import for side effect: repairs sys.path under iCloud)
from nha_trang_laundry_domain.catalog import AcquisitionSource

#: Orders whose commercial status means the shop was actually paid, as opposed to orders that
#: exist. A channel judged on orders *created* rewards whatever produces enquiries; the shop is
#: interested in what produces completed work.
SETTLED_STATUSES = ("COMPLETED",)


def collect(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    store_id: str | None,
    since: date | None,
    until: date | None,
) -> dict[str, dict[str, int]]:
    """Return, per source, how many orders exist and how many of those completed."""

    conditions: list[str] = []
    parameters: list[object] = []
    if store_id is not None:
        conditions.append("store_id = %s")
        parameters.append(store_id)
    if since is not None:
        conditions.append("created_at >= %s")
        parameters.append(since)
    if until is not None:
        # Inclusive of the whole named day, which is what a person means by "to the 30th".
        conditions.append("created_at < (%s::date + 1)")
        parameters.append(until)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT acquisition_source,
                   count(*) AS created,
                   count(*) FILTER (WHERE commercial_status = ANY(%s)) AS completed
            FROM orders
            {where}
            GROUP BY acquisition_source
            """,
            [list(SETTLED_STATUSES), *parameters],
        )
        rows = cursor.fetchall()

    # Every enum member appears, including the ones with no orders, so that "this channel produced
    # nothing" and "this channel is missing from the report" cannot be confused for one another.
    counted = {source.value: {"created": 0, "completed": 0} for source in AcquisitionSource}
    for source, created, completed in rows:
        counted[str(source)] = {"created": int(created), "completed": int(completed)}
    return counted


def render(counted: dict[str, dict[str, int]]) -> str:
    total_created = sum(entry["created"] for entry in counted.values())
    total_completed = sum(entry["completed"] for entry in counted.values())
    width = max(len(name) for name in counted)

    lines = [
        f"{'NGUỒN KHÁCH'.ljust(width)}  {'ĐƠN':>6}  {'HOÀN TẤT':>9}",
        f"{'-' * width}  {'-' * 6}  {'-' * 9}",
    ]
    ordered = sorted(counted.items(), key=lambda item: (-item[1]["completed"], item[0]))
    for name, entry in ordered:
        marker = "" if entry["created"] else "   (chưa có đơn nào)"
        lines.append(f"{name.ljust(width)}  {entry['created']:>6}  {entry['completed']:>9}{marker}")
    lines.append(f"{'-' * width}  {'-' * 6}  {'-' * 9}")
    lines.append(f"{'TỔNG'.ljust(width)}  {total_created:>6}  {total_completed:>9}")

    if total_created == 0:
        lines.append("")
        lines.append("Chưa có đơn nào trong phạm vi này. Bảng trống không có nghĩa là kênh hỏng.")

    lines.extend(
        [
            "",
            "Đọc đúng bảng này:",
            "  - Đây là SỐ ĐƠN, không phải lãi. Chưa đo được lãi trên mỗi đơn hay mỗi ký",
            "    (SHOP-INSTRUMENT-001 đang BLOCKED), nên một kênh nhiều đơn nhỏ có thể đứng trên",
            "    một kênh ít đơn nhưng nuôi cả tiệm.",
            "  - UNKNOWN là dữ liệu thật: quầy đã không hỏi. Đừng chia phần đó cho các kênh khác.",
            "  - Số 0 có thể là 'chưa từng thử', không phải 'đã thử và hỏng'.",
            "  - RETURNING là lời khách nói. Hệ thống không lưu hồ sơ khách (DEC-015) nên không",
            "    kiểm chứng được.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--since", type=date.fromisoformat, default=None)
    parser.add_argument("--until", type=date.fromisoformat, default=None)
    arguments = parser.parse_args()

    if not arguments.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    with psycopg.connect(arguments.database_url) as connection:
        counted = collect(
            connection,
            store_id=arguments.store_id,
            since=arguments.since,
            until=arguments.until,
        )
    print(render(counted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
