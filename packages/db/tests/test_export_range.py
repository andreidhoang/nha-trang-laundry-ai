"""`EXPORT-RANGE-001` (FR-RPT-003): an export names a window of the shop's days.

Three properties, and the order is their weight:

* **the window is inside what the owner signs.** Both dates are in `snapshot_hash` and in the
  rendered statement, so an approval of one window cannot release another -- a later one, a longer
  one, or the same first day alone -- and an envelope carrying one window's digests cannot even be
  raised against another window's request;
* **one day is exactly what it was.** A one-day request stores the pre-window row (no last day),
  binds the pre-window digests, runs the pre-window SQL under the pre-window query version, and
  replays under a key first used before windows existed. `test_migration_0054_populated.py` proves
  the same for a request and an envelope written before migration `0054` existed at all;
* **the cut is the one-day cut, repeated.** A window holds exactly the orders opened on its days in
  shop-local time: the first instant of the first day is in, the instant before it is out, the last
  instant of the last day is in, the instant after it is out.

The 92-day bound is the spec's, checked before anything is written and repeated as a CHECK.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.approvals import (
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.exports import (
    BUSINESS_TIMEZONE,
    EXPORT_COLUMNS,
    EXPORT_HEADER_KEYS,
    EXPORT_MAX_WINDOW_DAYS,
    EXPORT_QUERY,
    EXPORT_WINDOW_QUERY,
    ExportDataset,
    ExportExecutionCommand,
    ExportRequestCommand,
    ExportRequestFacts,
    ExportStateError,
    ExportWindowError,
    ExportWindowFacts,
    SanitizedExportRepository,
    export_window,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import ApprovalAction
from quote_test_data import accepted_quote
from test_sanitized_export import _approve, _Shop

ZONE = ZoneInfo(BUSINESS_TIMEZONE)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


STORE = UUID("11111111-2222-4333-8444-555555555555")
#: The one-day digests the pre-window code (`exports.py` at `028538d`) produced for `STORE` and
#: 16 September 2026, computed by loading that file and calling its own `_statement`.
PRE_WINDOW_SNAPSHOT = (
    "JCS-SHA256-V1:0039afa140f6e70799a7e91ea99fa05472957bd3d37e68a4f67a4d254ee641a7"
)
PRE_WINDOW_RENDERED = (
    "JCS-SHA256-V1:f30aeb77bb3d260a821fc95c456af52073e427aa27590aac24a44145ef2ba330"
)


def _request(
    connection: Any,
    shop: _Shop,
    first: date,
    last: date | None,
    *,
    key: str | None = None,
) -> Any:
    return SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=first,
            business_date_to=last,
            principal=shop.requester,
            correlation_id=uuid4(),
            idempotency_key=key or f"export-{uuid4().hex}",
            requested_at=shop.now,
        ),
    )


def _release(connection: Any, shop: _Shop, created: Any, approval_id: UUID) -> Any:
    return SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
        ),
    )


def _local_midnight(day: date) -> datetime:
    """The first instant of a shop-local day, as an aware UTC timestamp."""
    return datetime(day.year, day.month, day.day, tzinfo=ZONE).astimezone(UTC)


def _orders_created_at(connection: Any, shop: _Shop, moments: list[datetime]) -> list[UUID]:
    """Real order rows opened at exactly these instants, reusing one real accepted quote.

    Inserted directly, the way `test_ops_board._bulk_orders` does, because the property under test
    is which `created_at` values the export's WHERE clause admits -- and driving an order through
    the transition chain stamps `created_at` with the fixture's clock rather than a chosen instant.
    The rows keep every foreign key and CHECK the table has.
    """
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=shop.store_id, principal=shop.owner
    )
    identifiers = [uuid4() for _ in moments]
    with connection.transaction(), connection.cursor() as cursor:
        for identifier, moment in zip(identifiers, moments, strict=True):
            cursor.execute(
                """
                INSERT INTO orders (
                    id, store_id, bound_contact_id, current_quote_id, current_quote_revision,
                    current_quote_snapshot_hash, commercial_status, intake_status,
                    production_status, fulfillment_mode, balance_status,
                    customer_final_quote_accepted_at, production_accepted_at, row_version,
                    created_at, acquisition_source
                ) VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE', 'ACCEPTED', 'IN_PROCESS',
                          'SELF_DROP_SELF_COLLECT', 'UNPAID', %s, %s, 1, %s, 'WALK_IN')
                """,
                (
                    identifier,
                    shop.store_id,
                    contact_id,
                    quote_id,
                    revision,
                    quote.document.snapshot_hash,
                    moment,
                    moment,
                    moment,
                ),
            )
    return identifiers


def _file_parts(content: str) -> tuple[dict[str, str], str, list[str]]:
    lines = content.splitlines()
    count = len(EXPORT_HEADER_KEYS)
    header_rows = dict(line.split(",", 1) for line in lines[:count])
    assert list(header_rows) == list(EXPORT_HEADER_KEYS)
    return header_rows, lines[count], lines[count + 1 :]


# --- pure: the window and what it binds ---------------------------------------------------------


def test_the_window_query_version_is_pinned_to_the_rule_it_names() -> None:
    """A window is a new rule, so it has its own published identifier, pinned both halves.

    The one-day version is pinned unchanged in `test_sanitized_export.py`: rewriting the one-day SQL
    as a range would have moved that label and orphaned every one-day approval already signed.
    """
    assert EXPORT_WINDOW_QUERY.identifier == "store-window-orders-export-v1"
    assert EXPORT_WINDOW_QUERY.label == "store-window-orders-export-v1:b0ae2bdf3725ab24"
    assert EXPORT_WINDOW_QUERY.label != EXPORT_QUERY.label


def test_one_day_normalises_to_the_pre_window_shape_and_binds_the_pre_window_digest() -> None:
    """`from == to` and "no `to`" are the same request, and it is the request that existed before.

    The digest is recomputed here from a plain mapping with the three pre-window keys, not from the
    module's own dataclass, so a fourth field slipped into `ExportRequestFacts` -- even a `None` --
    fails this test rather than silently moving the hash of every one-day request ever written.

    Both digests are also pinned as literals: `PRE_WINDOW_SNAPSHOT` and `PRE_WINDOW_RENDERED` were
    computed by the pre-window module (`exports.py` at `028538d`) for this store and this day. A
    one-day export after this item binds exactly what it bound before it, so an envelope raised
    under the old code is still decidable and still releases.
    """
    from nha_trang_laundry_db.exports import _facts, _statement

    day = date(2026, 9, 16)
    assert export_window(day, None) == (day, None)
    assert export_window(day, day) == (day, None)

    legacy = canonical_document(
        {"dataset": "STORE_DAY_ORDERS_V1", "store_id": str(STORE), "business_date": "2026-09-16"}
    )
    facts = ExportRequestFacts(
        dataset="STORE_DAY_ORDERS_V1", store_id=STORE, business_date="2026-09-16"
    )
    assert canonical_document(facts).snapshot_hash == legacy.snapshot_hash
    one_day = _facts("STORE_DAY_ORDERS_V1", STORE, *export_window(day, day))
    assert canonical_document(one_day).snapshot_hash == PRE_WINDOW_SNAPSHOT
    assert canonical_document(_statement(one_day)).snapshot_hash == PRE_WINDOW_RENDERED


def test_both_dates_are_inside_what_the_approval_binds() -> None:
    """Approving January cannot release February: every window is a different document.

    Snapshot and rendered digests both, over a later window, a longer one with the same first day,
    and the first day alone -- the three substitutions an approval must not survive.
    """
    from nha_trang_laundry_db.exports import _facts, _statement

    first = date(2026, 1, 1)
    documents = {
        "january week 1": _facts("STORE_DAY_ORDERS_V1", STORE, first, date(2026, 1, 7)),
        "january week 2": _facts("STORE_DAY_ORDERS_V1", STORE, date(2026, 1, 8), date(2026, 1, 14)),
        "one day longer": _facts("STORE_DAY_ORDERS_V1", STORE, first, date(2026, 1, 8)),
        "first day alone": _facts("STORE_DAY_ORDERS_V1", STORE, first, None),
    }
    snapshots = {canonical_document(facts).snapshot_hash for facts in documents.values()}
    rendered = {canonical_document(_statement(facts)).snapshot_hash for facts in documents.values()}
    assert len(snapshots) == len(documents)
    assert len(rendered) == len(documents)

    week = _statement(documents["january week 1"])
    assert isinstance(week.facts, ExportWindowFacts)
    assert week.query_version == EXPORT_WINDOW_QUERY.label
    # Both ends and the count are in the words the owner signs, and so is the boundary.
    assert "từ 2026-01-01 đến hết 2026-01-07 (7 ngày" in week.statement_vi
    assert "không phải theo lúc thu tiền" in week.statement_vi
    # The one-day document keeps its own rule and its own sentence.
    day = _statement(documents["first day alone"])
    assert day.query_version == EXPORT_QUERY.label
    assert day.statement_vi.startswith("Xuất bản sao hồ sơ của chính cửa hàng cho ngày 2026-01-01 ")


def test_a_window_is_at_most_92_days_counted_inclusively_and_never_reversed() -> None:
    first = date(2026, 1, 1)
    longest = first + timedelta(days=EXPORT_MAX_WINDOW_DAYS - 1)
    assert EXPORT_MAX_WINDOW_DAYS == 92
    assert export_window(first, longest) == (first, longest)

    with pytest.raises(ExportWindowError) as too_long:
        export_window(first, longest + timedelta(days=1))
    assert too_long.value.reason_code == "EXPORT_WINDOW_TOO_LONG"

    with pytest.raises(ExportWindowError) as reversed_:
        export_window(first, first - timedelta(days=1))
    assert reversed_.value.reason_code == "EXPORT_WINDOW_REVERSED"


# --- against PostgreSQL -------------------------------------------------------------------------


def test_a_refused_window_writes_nothing_and_the_database_refuses_one_too(
    connection: psycopg.Connection[Any],
) -> None:
    """Nothing is clipped or half-written, and the CHECK holds for any path around the code."""
    shop = _Shop(connection, datetime.now(UTC))
    first = date(2026, 1, 1)
    key = f"export-{uuid4().hex}"
    with pytest.raises(ExportWindowError):
        _request(connection, shop, first, first + timedelta(days=92), key=key)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM export_requests WHERE store_id = %s", (shop.store_id,))
        assert cursor.fetchone() == (0,)
    # The key was not burnt by the refusal: the corrected window goes through under it.
    corrected = _request(connection, shop, first, first + timedelta(days=91), key=key)
    assert corrected.window_days == 92
    assert corrected.replayed is False

    for last in (first, first - timedelta(days=1), first + timedelta(days=92)):
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO export_requests (
                    id, store_id, dataset, business_date, business_date_to, row_version,
                    snapshot_hash, rendered_hash, requested_by_staff_id, requested_at
                ) VALUES (%s, %s, 'STORE_DAY_ORDERS_V1', %s, %s, 1, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    shop.store_id,
                    first,
                    last,
                    "JCS-SHA256-V1:" + "a" * 64,
                    "JCS-SHA256-V1:" + "b" * 64,
                    shop.requester.staff_user_id,
                    shop.now,
                ),
            )


def test_a_one_day_request_is_stored_and_replayed_exactly_as_before_windows(
    connection: psycopg.Connection[Any],
) -> None:
    """`from == to` writes the pre-window row and replays a key first sent without a `to`.

    And the other half of idempotency: the same key with a different window is a conflict, never a
    replay of the first window's request.
    """
    shop = _Shop(connection, datetime.now(UTC))
    day = date(2026, 9, 16)
    key = f"export-{uuid4().hex}"

    first = _request(connection, shop, day, None, key=key)
    again = _request(connection, shop, day, day, key=key)
    assert again.replayed is True
    assert again.export_request_id == first.export_request_id
    assert (first.business_date, first.business_date_to, first.window_days) == (day, day, 1)
    assert first.query_version == EXPORT_QUERY.label
    assert (
        first.snapshot_hash
        == canonical_document(
            {
                "dataset": "STORE_DAY_ORDERS_V1",
                "store_id": str(shop.store_id),
                "business_date": "2026-09-16",
            }
        ).snapshot_hash
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT business_date, business_date_to FROM export_requests WHERE id = %s",
            (first.export_request_id,),
        )
        assert cursor.fetchone() == (day, None)

    with pytest.raises(IdempotencyConflictError):
        _request(connection, shop, day, day + timedelta(days=6), key=key)


def test_an_approval_for_one_window_cannot_release_another(
    connection: psycopg.Connection[Any],
) -> None:
    """The headline property of this item, at the release and at the envelope.

    Week 1 is approved. Week 2's request is released against week 1's approval: refused by name,
    nothing written. An envelope for week 2 carrying week 1's digests: refused before any owner
    sees it. Then week 1 releases under its own approval, once.
    """
    shop = _Shop(connection, datetime.now(UTC))
    week_1 = _request(connection, shop, date(2026, 1, 1), date(2026, 1, 7))
    week_2 = _request(connection, shop, date(2026, 1, 8), date(2026, 1, 14))
    longer = _request(connection, shop, date(2026, 1, 1), date(2026, 1, 8))
    assert week_1.window_days == 7
    assert week_1.query_version == EXPORT_WINDOW_QUERY.label
    assert "từ 2026-01-01 đến hết 2026-01-07 (7 ngày" in week_1.statement_vi
    assert week_1.snapshot_hash not in (week_2.snapshot_hash, longer.snapshot_hash)
    approval_id = _approve(connection, shop, week_1)

    for other in (week_2, longer):
        with pytest.raises(ExportStateError) as raised:
            _release(connection, shop, other, approval_id)
        assert raised.value.reason_code == "EXPORT_APPROVAL_NOT_BOUND"

    with pytest.raises(ApprovalStateError):
        ApprovalRepository().request(
            connection,
            ApprovalRequestCommand(
                ApprovalAction.EXPORT_SANITIZED_DATA,
                APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA],
                week_2.export_request_id,
                1,
                week_1.snapshot_hash,
                week_1.rendered_hash,
                week_1.policy_version,
                shop.requester.staff_user_id,
                f"approval-{uuid4().hex}",
                uuid4(),
                shop.now,
                store_id=shop.store_id,
            ),
        )

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports WHERE store_id = %s", (shop.store_id,))
        assert cursor.fetchone() == (0,)

    produced = _release(connection, shop, week_1, approval_id)
    assert produced.business_date == date(2026, 1, 1)
    assert produced.business_date_to == date(2026, 1, 7)
    assert produced.query_version == EXPORT_WINDOW_QUERY.label
    with pytest.raises(ExportStateError) as twice:
        _release(connection, shop, week_1, approval_id)
    assert twice.value.reason_code == "EXPORT_ALREADY_PRODUCED"


def test_a_window_holds_exactly_the_orders_opened_on_its_days_cut_at_shop_local_midnight(
    connection: psycopg.Connection[Any],
) -> None:
    """The one-day cut, repeated across the window, with its header rows and its trail.

    Five orders: one a microsecond before the first shop-local day, one at its first instant, one
    in the middle, one at the last microsecond of the last day, one at the first instant after it.
    The window is 1 to 3 September; exactly the middle three are in the file, in `created_at` order,
    and the header rows name the window rule and both days.
    """
    shop = _Shop(connection, datetime.now(UTC))
    first, last = date(2026, 9, 1), date(2026, 9, 3)
    start = _local_midnight(first)
    end = _local_midnight(last + timedelta(days=1))
    micro = timedelta(microseconds=1)
    before, at_start, middle, at_end, after = _orders_created_at(
        connection,
        shop,
        [start - micro, start, start + timedelta(days=1, hours=5), end - micro, end],
    )
    created = _request(connection, shop, first, last)
    approval_id = _approve(connection, shop, created)

    produced = _release(connection, shop, created, approval_id)

    header_rows, columns, body = _file_parts(produced.content_csv)
    assert header_rows == {
        "export_query_version": EXPORT_WINDOW_QUERY.label,
        "business_date_from": "2026-09-01",
        "business_date_to": "2026-09-03",
        "business_timezone": "Asia/Ho_Chi_Minh",
        "day_boundary": "orders.created_at",
    }
    assert columns == ",".join(EXPORT_COLUMNS)
    assert produced.row_count == 3
    assert [line.split(",", 1)[0] for line in body] == [str(at_start), str(middle), str(at_end)]
    assert str(before) not in produced.content_csv
    assert str(after) not in produced.content_csv

    # A one-day export of the window's first day is the one-day rule and holds only that day.
    day = _request(connection, shop, first, first)
    day_approval = _approve(connection, shop, day)
    one_day = _release(connection, shop, day, day_approval)
    assert one_day.query_version == EXPORT_QUERY.label
    assert one_day.row_count == 1
    assert str(at_start) in one_day.content_csv

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.query_version, d.row_count, a.details
            FROM data_exports d
            JOIN audit_events a ON a.aggregate_type = 'DATA_EXPORT' AND a.aggregate_id = d.id
            WHERE d.id = %s
            """,
            (produced.export_id,),
        )
        record = cursor.fetchone()
        cursor.execute(
            """
            SELECT details FROM audit_events
            WHERE aggregate_type = 'EXPORT_REQUEST' AND aggregate_id = %s
            """,
            (created.export_request_id,),
        )
        requested = cursor.fetchone()
    assert record is not None
    assert record[0] == EXPORT_WINDOW_QUERY.label
    assert record[1] == 3
    assert record[2]["approval_request_id"] == str(approval_id)
    assert requested is not None
    assert requested[0]["business_date"] == "2026-09-01"
    assert requested[0]["business_date_to"] == "2026-09-03"


def test_the_approval_disclosure_names_both_ends_of_the_window(
    connection: psycopg.Connection[Any],
) -> None:
    """What `#/approvals` puts above the approve control: the window, its length, its own rule."""
    shop = _Shop(connection, datetime.now(UTC))
    created = _request(connection, shop, date(2026, 8, 1), date(2026, 8, 31))
    approval_id = _approve(connection, shop, created)

    with connection.cursor() as cursor:
        disclosed = SanitizedExportRepository.read_for_approval(
            cursor, approval_id=approval_id, principal=shop.owner
        )
    assert disclosed is not None
    assert disclosed.business_date == date(2026, 8, 1)
    assert disclosed.business_date_to == date(2026, 8, 31)
    assert disclosed.window_days == 31
    assert disclosed.query_version == EXPORT_WINDOW_QUERY.label
    assert disclosed.rendered_hash == created.rendered_hash
    assert "từ 2026-08-01 đến hết 2026-08-31 (31 ngày" in disclosed.statement_vi
