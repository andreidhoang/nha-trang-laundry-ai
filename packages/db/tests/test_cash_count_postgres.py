"""`CASH-COUNT-009` (`DEC-049`) against real PostgreSQL: the end-of-day cash count, *đếm két*.

One shop-local day, driven through the real payment, cancellation and Sổ thu chi commands
(`test_drawer_postgres._the_day`):

    cash taken 210.000 (3 payments); 60.000 handed back in cash; 110.000 handed back by transfer
    (not the drawer's); 40.000 handed back before `0067` recorded how (unknown: excluded, counted);
    Sổ thu chi: 50.000 chemicals "trả từ két", 70.000 electricity by transfer (not from the
    drawer), 30.000 bags "trả từ két" then voided.

    opening float 500.000 -> expected 500.000 + 210.000 - 60.000 - 50.000 = 600.000, INCOMPLETE
    (1 refund of unknown method, 40.000, left out); counted 590.000 -> thiếu 10.000.

Every entry commits with its event, audit and outbox row or not at all; each kind is recorded once
a day and corrected only by a superseding entry with a reason; the recorded difference is
reproducible from the stored trace; the evening summary tells the owner.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db import cash_counts as cash_counts_module
from nha_trang_laundry_db.cash_counts import (
    CASH_COUNT_QUERY,
    CashCountAuthorizationError,
    CashCountRefusal,
    CashCountRepository,
    RecordCashCountCommand,
)
from nha_trang_laundry_db.daily_summary import DailySummaryRepository
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.reports import shop_today
from nha_trang_laundry_db.settlement import SettlementRepository
from nha_trang_laundry_db.shop_capture import (
    ExpenseRepository,
    RecordExpenseCommand,
    VoidExpenseCommand,
)
from nha_trang_laundry_db.transactions import commit_material_change
from nha_trang_laundry_domain.cash_count import (
    CashCountKind,
    DifferenceDirection,
    ExpectedStatus,
    replay_trace,
)
from nha_trang_laundry_domain.shop_capture import ExpenseCategory
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from test_drawer_postgres import _the_day
from test_export_payments import CASH, _received
from test_order_step_repository import _read
from test_order_step_repository import _staff as _operator
from test_reports import _person
from test_sanitized_export import _Shop

FLOAT = CashCountKind.OPENING_FLOAT
CLOSE = CashCountKind.CLOSING_COUNT


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        apply_migrations(established)
        yield established


def _today() -> date:
    return shop_today(datetime.now(UTC))


def _record(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    kind: CashCountKind,
    counted: int,
    *,
    supersedes: UUID | None = None,
    reason: str | None = None,
    key: str | None = None,
    day: date | None = None,
    today: date | None = None,
) -> Any:
    return CashCountRepository().record(
        connection,
        RecordCashCountCommand(
            store_id=store_id,
            business_day=day or _today(),
            kind=kind,
            counted_vnd=counted,
            supersedes_entry_id=supersedes,
            reason=reason,
            principal=staff,
            idempotency_key=key or f"cash-{uuid4().hex}",
            correlation_id=uuid4(),
            today=today or _today(),
        ),
    )


def _expense(
    connection: Any,
    shop: Any,
    category: ExpenseCategory,
    amount: int,
    *,
    drawer: bool,
) -> Any:
    line, _ = ExpenseRepository().record(
        connection,
        RecordExpenseCommand(
            store_id=shop.store_id,
            spent_on=_today(),
            category=category,
            amount_vnd=amount,
            note=None,
            principal=shop.owner,
            idempotency_key=f"expense-{uuid4().hex}",
            correlation_id=uuid4(),
            today=_today(),
            paid_from_drawer=drawer,
        ),
    )
    return line


def _sheet(connection: Any, store_id: UUID, staff: StaffPrincipal) -> Any:
    with connection.cursor() as cursor:
        return CashCountRepository.today(cursor, store_id=store_id, principal=staff, today=_today())


def _ledgers(connection: Any, entry_ids: list[UUID]) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT (SELECT count(*) FROM domain_events WHERE aggregate_type = 'CASH_COUNT'
                    AND aggregate_id = ANY(%(ids)s)),
                   (SELECT count(*) FROM audit_events WHERE aggregate_type = 'CASH_COUNT'
                    AND aggregate_id = ANY(%(ids)s)),
                   (SELECT count(*) FROM outbox_events WHERE aggregate_type = 'CASH_COUNT'
                    AND aggregate_id = ANY(%(ids)s))
            """,
            {"ids": entry_ids},
        )
        row = cursor.fetchone()
        return int(row[0]), int(row[1]), int(row[2])


def _rows(connection: Any, store_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM cash_counts WHERE store_id = %s", (store_id,))
        return int(cursor.fetchone()[0])


def _attention(connection: Any, store_id: UUID, reader: StaffPrincipal) -> Any:
    return DailySummaryRepository.read(
        connection,
        store_id=store_id,
        principal=reader,
        policy=STANDARD_WASH_SLA,
        day=_today(),
        as_of=datetime.now(UTC),
    )


def _the_counted_day(connection: Any) -> tuple[Any, Any]:
    shop, staff, _ = _the_day(connection)
    _expense(connection, shop, ExpenseCategory.HOA_CHAT, 50_000, drawer=True)
    _expense(connection, shop, ExpenseCategory.DIEN, 70_000, drawer=False)
    bags = _expense(connection, shop, ExpenseCategory.TUI_NHAN, 30_000, drawer=True)
    ExpenseRepository().void(
        connection,
        VoidExpenseCommand(
            expense_id=bags.expense_id,
            expected_row_version=1,
            principal=shop.owner,
            idempotency_key=f"void-{uuid4().hex}",
            correlation_id=uuid4(),
        ),
    )
    return shop, staff


# --- the day the conformance scenario walks ---


def test_expected_is_float_plus_cash_in_minus_cash_back_minus_drawer_expenses(
    connection: Any,
) -> None:
    shop, staff = _the_counted_day(connection)

    before = _sheet(connection, shop.store_id, staff)
    assert before.opening_float is None and before.closing_count is None
    assert before.expected.status is ExpectedStatus.FLOAT_MISSING
    assert before.expected.expected_vnd is None
    moved = before.expected.movement
    assert (moved.cash_in_vnd, moved.cash_in_entries) == (210_000, 3)
    assert (moved.cash_refunded_vnd, moved.cash_refunded_entries) == (60_000, 1)
    assert (moved.unknown_refunds_vnd, moved.unknown_refunds_entries) == (40_000, 1)
    # Only the line marked "trả từ két" and not voided: not the transfer bill, not the void.
    assert (moved.drawer_expenses_vnd, moved.drawer_expenses_entries) == (50_000, 1)
    # The same drawer the Today card prints: `collected-today-v4`'s cash figures, to the đồng.
    with connection.cursor() as cursor:
        card = SettlementRepository.collected_today(cursor, store_id=shop.store_id, principal=staff)
    assert (card.cash_vnd, card.cash_count) == (moved.cash_in_vnd, moved.cash_in_entries)
    assert card.refunded_cash_vnd == moved.cash_refunded_vnd
    assert card.refunded_unknown_vnd == moved.unknown_refunds_vnd

    opening, sheet, replayed = _record(connection, shop.store_id, staff, FLOAT, 500_000)
    assert not replayed
    assert opening.kind is FLOAT and opening.counted_vnd == 500_000
    assert opening.expected_status is None and opening.trace is None
    assert sheet.expected.status is ExpectedStatus.INCOMPLETE
    assert sheet.expected.expected_vnd == 600_000

    closing, sheet, _ = _record(connection, shop.store_id, staff, CLOSE, 590_000)
    assert closing.expected_status is ExpectedStatus.INCOMPLETE
    assert (closing.expected_vnd, closing.difference_vnd) == (600_000, 10_000)
    assert closing.difference_direction is DifferenceDirection.SHORT
    assert closing.float_entry_id == opening.entry_id
    assert closing.rule_version == CASH_COUNT_QUERY.label
    assert CASH_COUNT_QUERY.label.startswith("cash-count-v1:")
    assert sheet.closing_count == closing and not sheet.changed_since_count
    # The recorded difference is reproducible from the row alone.
    assert closing.trace is not None
    assert replay_trace(dict(closing.trace)).trace_hash == closing.trace_hash
    assert closing.trace["excluded_unknown_refunds_vnd"] == 40_000

    # Each entry is a material change: its event, its audit row and its outbox row.
    assert _ledgers(connection, [opening.entry_id, closing.entry_id]) == (2, 2, 2)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_type, payload FROM domain_events WHERE aggregate_id = %s",
            (closing.entry_id,),
        )
        event_type, payload = cursor.fetchone()
    assert event_type == "CASH_COUNT_RECORDED"
    assert payload["difference_direction"] == "SHORT" and payload["difference_vnd"] == 10_000
    assert payload["expected_vnd"] == 600_000 and payload["trace_hash"] == closing.trace_hash

    # The owner's evening summary names it under "Cần chú ý", both figures, never a sign.
    summary = _attention(connection, shop.store_id, shop.owner)
    line = next(item for item in summary.rendered.lines if item.key.value == "ATTN_CASH_COUNT")
    assert line.text == (
        "- Đếm két cuối ngày thiếu 10.000đ (phải có 600.000đ, đếm được 590.000đ). "
        "Số phải có chưa tính 1 khoản hoàn chưa rõ cách hoàn (40.000đ)."
    )
    assert ("cash_count", CASH_COUNT_QUERY.label) in summary.sources
    assert summary.template_version.startswith("daily-summary-v5:")

    # And the owner's history over the day says the same.
    with connection.cursor() as cursor:
        history = CashCountRepository.history(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            from_date=_today() - timedelta(days=6),
            to_date=_today(),
            today=_today(),
        )
    (day,) = history.days
    assert day.closing_count == closing and day.opening_float == opening
    assert not history.truncated


def test_a_count_over_the_books_is_thua_and_an_even_count_is_not_a_line(connection: Any) -> None:
    shop, staff = _the_counted_day(connection)
    _record(connection, shop.store_id, staff, FLOAT, 500_000)
    over, _, _ = _record(connection, shop.store_id, staff, CLOSE, 605_000)
    assert (over.difference_direction, over.difference_vnd) == (DifferenceDirection.OVER, 5_000)
    text = _attention(connection, shop.store_id, shop.owner).rendered.text
    assert "Đếm két cuối ngày thừa 5.000đ (phải có 600.000đ, đếm được 605.000đ)." in text

    even, _, _ = _record(
        connection,
        shop.store_id,
        staff,
        CLOSE,
        600_000,
        supersedes=over.entry_id,
        reason="đếm sót tờ 5.000",
    )
    assert (even.difference_direction, even.difference_vnd) == (DifferenceDirection.EVEN, 0)
    summary = _attention(connection, shop.store_id, shop.owner)
    assert not [line for line in summary.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]


# --- once a day; a correction supersedes, with a reason ---


def test_each_kind_is_recorded_once_and_corrected_only_by_a_superseding_entry(
    connection: Any,
) -> None:
    shop, staff = _the_counted_day(connection)
    first, _, _ = _record(connection, shop.store_id, staff, FLOAT, 400_000)

    with pytest.raises(CashCountRefusal) as again:
        _record(connection, shop.store_id, staff, FLOAT, 500_000)
    assert (again.value.reason_code, again.value.conflict) == ("CASH_COUNT_ALREADY_RECORDED", True)
    with pytest.raises(CashCountRefusal) as no_reason:
        _record(connection, shop.store_id, staff, FLOAT, 500_000, supersedes=first.entry_id)
    assert no_reason.value.reason_code == "CASH_COUNT_REASON_REQUIRED"
    with pytest.raises(CashCountRefusal) as reason_on_first:
        _record(connection, shop.store_id, staff, CLOSE, 1, reason="lần đầu")
    assert reason_on_first.value.reason_code == "CASH_COUNT_REASON_NOT_EXPECTED"
    with pytest.raises(CashCountRefusal) as unknown_target:
        _record(connection, shop.store_id, staff, FLOAT, 500_000, supersedes=uuid4(), reason="x")
    assert unknown_target.value.reason_code == "CASH_COUNT_STALE"
    with pytest.raises(CashCountRefusal) as nothing_to_correct:
        _record(connection, shop.store_id, staff, CLOSE, 1, supersedes=first.entry_id, reason="x")
    assert nothing_to_correct.value.reason_code == "CASH_COUNT_STALE"
    assert _rows(connection, shop.store_id) == 1

    fixed, sheet, _ = _record(
        connection,
        shop.store_id,
        staff,
        FLOAT,
        500_000,
        supersedes=first.entry_id,
        reason="  gõ nhầm  400   ",
    )
    assert fixed.supersedes_id == first.entry_id
    assert fixed.correction_reason == "gõ nhầm 400"
    assert sheet.opening_float == fixed
    assert [(entry.counted_vnd, entry.superseded) for entry in sheet.entries] == [
        (400_000, True),
        (500_000, False),
    ]
    # The wrong figure stays readable; the old entry can no longer be the one corrected.
    with pytest.raises(CashCountRefusal) as stale:
        _record(
            connection, shop.store_id, staff, FLOAT, 450_000, supersedes=first.entry_id, reason="x"
        )
    assert stale.value.reason_code == "CASH_COUNT_STALE"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_type, payload::text FROM domain_events WHERE aggregate_id = %s",
            (fixed.entry_id,),
        )
        event_type, payload = cursor.fetchone()
        cursor.execute(
            "SELECT action, details::text FROM audit_events WHERE aggregate_id = %s",
            (fixed.entry_id,),
        )
        action, details = cursor.fetchone()
    assert (event_type, action) == ("CASH_COUNT_CORRECTED", "CASH_COUNT_CORRECT")
    # The shop's words about its drawer are stored once, never copied into a ledger payload.
    assert "gõ nhầm" not in payload and "gõ nhầm" not in details
    assert _ledgers(connection, [first.entry_id, fixed.entry_id]) == (2, 2, 2)


def test_the_database_itself_keeps_one_original_a_linear_chain_and_no_edits(
    connection: Any,
) -> None:
    shop, staff = _the_counted_day(connection)
    first, _, _ = _record(connection, shop.store_id, staff, FLOAT, 400_000)

    def insert(**overrides: Any) -> None:
        values = {
            "id": uuid4(),
            "store_id": shop.store_id,
            "business_day": _today(),
            "kind": "OPENING_FLOAT",
            "counted_vnd": 1,
            "supersedes_id": None,
            "correction_reason": None,
            "recorded_by": staff.staff_user_id,
            "recorded_at": datetime.now(UTC),
            **overrides,
        }
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO cash_counts ({', '.join(values)}) "
                f"VALUES ({', '.join(['%s'] * len(values))})",
                tuple(values.values()),
            )

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert()
    with pytest.raises(psycopg.errors.RaiseException, match="same store, day and kind"):
        insert(kind="CLOSING_COUNT", supersedes_id=first.entry_id, correction_reason="x")
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(supersedes_id=first.entry_id)  # a correction without a reason
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(kind="CLOSING_COUNT")  # a closing count that recorded nothing of the books
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(counted_vnd=-1)
    second = uuid4()
    insert(id=second, supersedes_id=first.entry_id, correction_reason="x")
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(supersedes_id=first.entry_id, correction_reason="fork")
    with (
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE cash_counts SET counted_vnd = 0 WHERE id = %s", (second,))
    with (
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM cash_counts WHERE id = %s", (first.entry_id,))


def test_entries_are_for_the_shops_today_only(connection: Any) -> None:
    shop, staff = _the_counted_day(connection)
    for day in (_today() - timedelta(days=1), _today() + timedelta(days=1)):
        with pytest.raises(CashCountRefusal) as refused:
            _record(connection, shop.store_id, staff, FLOAT, 1, day=day)
        assert refused.value.reason_code == "CASH_COUNT_DAY_NOT_TODAY"
    with pytest.raises(CashCountRefusal) as too_large:
        _record(connection, shop.store_id, staff, FLOAT, 1_000_000_001)
    assert too_large.value.reason_code == "CASH_COUNT_AMOUNT_TOO_LARGE"
    assert _rows(connection, shop.store_id) == 0


# --- idempotency and atomicity ---


def test_a_replay_returns_the_first_entry_and_a_changed_payload_conflicts(connection: Any) -> None:
    shop, staff = _the_counted_day(connection)
    # A refused request leaves nothing behind -- not even its key.
    with pytest.raises(CashCountRefusal):
        _record(connection, shop.store_id, staff, FLOAT, 1, key="k-float", day=date(2000, 1, 1))
    first, _, replayed = _record(connection, shop.store_id, staff, FLOAT, 500_000, key="k-float")
    assert not replayed
    again, _, replayed = _record(connection, shop.store_id, staff, FLOAT, 500_000, key="k-float")
    assert replayed and again.entry_id == first.entry_id
    # Even the next day: the answer is the first one, not a refusal for a day that has passed.
    tomorrow = _today() + timedelta(days=1)
    late, _, replayed = _record(
        connection, shop.store_id, staff, FLOAT, 500_000, key="k-float", today=tomorrow
    )
    assert replayed and late.entry_id == first.entry_id
    with pytest.raises(IdempotencyConflictError):
        _record(connection, shop.store_id, staff, FLOAT, 500_001, key="k-float")
    assert _rows(connection, shop.store_id) == 1
    assert _ledgers(connection, [first.entry_id]) == (1, 1, 1)


def test_an_entry_commits_with_its_ledgers_or_not_at_all(
    connection: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    shop, staff = _the_counted_day(connection)
    real = commit_material_change

    def failing(connection: Any, change: Any, mutate: Any) -> None:
        def mutate_then_fail(cursor: Any) -> None:
            mutate(cursor)
            raise RuntimeError("the outbox is down")

        real(connection, change, mutate_then_fail)

    monkeypatch.setattr(cash_counts_module, "commit_material_change", failing)
    with pytest.raises(RuntimeError, match="outbox is down"):
        _record(connection, shop.store_id, staff, FLOAT, 500_000, key="k-atomic")
    monkeypatch.setattr(cash_counts_module, "commit_material_change", real)
    assert _rows(connection, shop.store_id) == 0
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE scope = %s",
            (f"store:{shop.store_id}:cash-count",),
        )
        assert cursor.fetchone()[0] == 0
    entry, _, replayed = _record(connection, shop.store_id, staff, FLOAT, 500_000, key="k-atomic")
    assert not replayed and _ledgers(connection, [entry.entry_id]) == (1, 1, 1)


# --- unknown is not zero ---


def test_no_float_means_no_expected_figure_and_the_owner_is_told(connection: Any) -> None:
    shop, staff = _the_counted_day(connection)
    closing, sheet, _ = _record(connection, shop.store_id, staff, CLOSE, 300_000)
    assert closing.expected_status is ExpectedStatus.FLOAT_MISSING
    assert closing.expected_vnd is None and closing.difference_vnd is None
    assert closing.difference_direction is None and closing.float_entry_id is None
    assert sheet.expected.expected_vnd is None
    text = _attention(connection, shop.store_id, shop.owner).rendered.text
    assert "Đếm két cuối ngày được 300.000đ nhưng chưa so được: chưa ghi tiền đầu ngày." in text
    # A float recorded afterwards does not rewrite the count: the sheet says the books moved.
    _record(connection, shop.store_id, staff, FLOAT, 500_000)
    after = _sheet(connection, shop.store_id, staff)
    assert after.closing_count == closing
    assert after.changed_since_count
    assert after.expected.expected_vnd == 600_000
    assert after.expected.status is ExpectedStatus.INCOMPLETE
    # ...and the owner's summary says the same as the sheet and Báo cáo: "chưa ghi tiền đầu ngày"
    # was true at the count, is false now, and is never said as if it were still true. The figure
    # now still leaves out the refund of unknown method, and says so as Đếm két does.
    text = _attention(connection, shop.store_id, shop.owner).rendered.text
    assert "nhưng chưa so được: chưa ghi tiền đầu ngày." not in text
    assert (
        "- Đếm két cuối ngày được 300.000đ; lúc đếm chưa ghi tiền đầu ngày nên chưa so được. "
        "Sổ đã thay đổi sau lúc đếm: bây giờ két phải có 600.000đ, chưa tính 1 khoản hoàn chưa "
        "rõ cách hoàn (40.000đ) nên số này chưa đầy đủ." in text
    )


def test_books_that_say_the_drawer_is_below_nothing_produce_no_figure(connection: Any) -> None:
    shop, staff = _the_counted_day(connection)
    _record(connection, shop.store_id, staff, FLOAT, 0)
    _expense(connection, shop, ExpenseCategory.SUA_CHUA, 200_000, drawer=True)
    closing, _, _ = _record(connection, shop.store_id, staff, CLOSE, 0)
    # 0 + 210.000 - 60.000 - 250.000 = 100.000 below nothing.
    assert closing.expected_status is ExpectedStatus.BOOKS_BELOW_ZERO
    assert closing.expected_vnd is None and closing.difference_vnd is None
    assert closing.trace is not None and closing.trace["books_over_vnd"] == 100_000
    text = _attention(connection, shop.store_id, shop.owner).rendered.text
    assert "sổ ghi tiền ra khỏi két nhiều hơn tiền vào 100.000đ." in text


def test_books_that_move_after_the_count_are_said_and_the_recorded_difference_stays(
    connection: Any,
) -> None:
    shop, staff = _the_counted_day(connection)
    _record(connection, shop.store_id, staff, FLOAT, 500_000)
    closing, _, _ = _record(connection, shop.store_id, staff, CLOSE, 600_000)
    assert closing.difference_direction is DifferenceDirection.EVEN
    _expense(connection, shop, ExpenseCategory.KHAC, 10_000, drawer=True)

    sheet = _sheet(connection, shop.store_id, staff)
    assert sheet.changed_since_count
    assert sheet.expected.expected_vnd == 590_000
    assert sheet.closing_count == closing  # recorded, never recomputed into the row
    # Even at the count, but the books no longer agree: the summary says so (it used to stay
    # silent, as if the drawer were still known to be even), as Báo cáo does.
    summary = _attention(connection, shop.store_id, shop.owner)
    (line,) = [line for line in summary.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]
    assert line.text == (
        "- Lúc đếm, két cuối ngày khớp (phải có 600.000đ, đếm được 600.000đ). "
        "Số phải có lúc đó chưa tính 1 khoản hoàn chưa rõ cách hoàn (40.000đ). "
        "Sổ đã thay đổi sau lúc đếm: bây giờ két phải có 590.000đ, chưa tính 1 khoản hoàn chưa "
        "rõ cách hoàn (40.000đ) nên số này chưa đầy đủ."
    )

    recount, sheet, _ = _record(
        connection,
        shop.store_id,
        staff,
        CLOSE,
        600_000,
        supersedes=closing.entry_id,
        reason="chủ ghi thêm khoản chi từ két",
    )
    assert (recount.difference_direction, recount.difference_vnd) == (
        DifferenceDirection.OVER,
        10_000,
    )
    assert not sheet.changed_since_count
    # The re-count is the current one and the books agree with it: said plainly again.
    summary = _attention(connection, shop.store_id, shop.owner)
    (line,) = [line for line in summary.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]
    assert line.text.startswith("- Đếm két cuối ngày thừa 10.000đ (phải có 590.000đ")
    assert "Sổ đã thay đổi" not in line.text


def test_a_short_count_then_a_forgotten_drawer_expense_is_never_still_read_as_short(
    connection: Any,
) -> None:
    """Thiếu 10.000 at the count; then the owner records the 10.000 that left the drawer for
    chemicals and was forgotten. The recorded difference stays (nothing is rewritten) and the
    summary says the books moved and what they say now -- the same answer as the sheet."""
    shop, staff = _the_counted_day(connection)
    _record(connection, shop.store_id, staff, FLOAT, 500_000)
    closing, _, _ = _record(connection, shop.store_id, staff, CLOSE, 590_000)
    assert (closing.difference_direction, closing.difference_vnd) == (
        DifferenceDirection.SHORT,
        10_000,
    )
    before = _attention(connection, shop.store_id, shop.owner)
    (line,) = [line for line in before.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]
    assert line.text.startswith("- Đếm két cuối ngày thiếu 10.000đ (phải có 600.000đ")
    assert "Sổ đã thay đổi" not in line.text
    assert dict(line.figures)["cash_changed_since_count"] == 0

    _expense(connection, shop, ExpenseCategory.HOA_CHAT, 10_000, drawer=True)
    sheet = _sheet(connection, shop.store_id, staff)
    assert sheet.changed_since_count and sheet.expected.expected_vnd == 590_000
    after = _attention(connection, shop.store_id, shop.owner)
    (line,) = [line for line in after.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]
    assert line.text.startswith(
        "- Lúc đếm, két cuối ngày thiếu 10.000đ (phải có 600.000đ, đếm được 590.000đ)."
    )
    assert sheet.expected.status is ExpectedStatus.INCOMPLETE
    # The figure now still leaves out the 40.000 refund of unknown method: said and marked, never
    # left to read as final beside a "lúc đó" that names the exclusion only for the count.
    assert line.text.endswith(
        "Sổ đã thay đổi sau lúc đếm: bây giờ két phải có 590.000đ, chưa tính 1 khoản hoàn chưa "
        "rõ cách hoàn (40.000đ) nên số này chưa đầy đủ."
    )
    assert dict(line.figures)["cash_changed_since_count"] == 1
    assert dict(line.figures)["cash_expected_now_vnd"] == 590_000
    assert dict(line.figures)["cash_expected_now_status"] == "INCOMPLETE"
    assert dict(line.figures)["cash_excluded_unknown_now_entries"] == 1
    assert dict(line.figures)["cash_excluded_unknown_now_vnd"] == 40_000
    assert dict(line.figures)["cash_difference_vnd"] == 10_000  # the recorded one, unchanged


def test_books_that_move_below_nothing_after_the_count_are_said_with_the_amount(
    connection: Any,
) -> None:
    """Verification round 3 of 9b: thiếu 10.000 at the count; then a mistyped 1.000.000 drawer
    expense puts the books below nothing. Đếm két says by how much and to check Sổ thu chi; the
    owner's summary says the same, never a bare "bây giờ chưa tính được số phải có"."""
    shop, staff = _the_counted_day(connection)
    _record(connection, shop.store_id, staff, FLOAT, 500_000)
    _record(connection, shop.store_id, staff, CLOSE, 590_000)
    _expense(connection, shop, ExpenseCategory.SUA_CHUA, 1_000_000, drawer=True)
    sheet = _sheet(connection, shop.store_id, staff)
    assert sheet.changed_since_count
    assert sheet.expected.status is ExpectedStatus.BOOKS_BELOW_ZERO
    assert sheet.expected.books_over_vnd == 400_000
    summary = _attention(connection, shop.store_id, shop.owner)
    (line,) = [line for line in summary.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]
    assert line.text == (
        "- Lúc đếm, két cuối ngày thiếu 10.000đ (phải có 600.000đ, đếm được 590.000đ). "
        "Số phải có lúc đó chưa tính 1 khoản hoàn chưa rõ cách hoàn (40.000đ). "
        "Sổ đã thay đổi sau lúc đếm: bây giờ sổ ghi tiền ra khỏi két nhiều hơn tiền vào "
        "400.000đ nên chưa tính được số phải có. Kiểm tra Sổ thu chi."
    )
    figures = dict(line.figures)
    assert figures["cash_expected_now_status"] == "BOOKS_BELOW_ZERO"
    assert figures["cash_books_over_now_vnd"] == 400_000
    assert figures["cash_expected_now_vnd"] is None
    assert figures["cash_difference_vnd"] == 10_000  # the recorded one, unchanged


def test_a_count_with_no_float_and_books_that_moved_says_the_float_is_still_missing(
    connection: Any,
) -> None:
    """Verification round 3 of 9b: a count with no float, then another drawer movement. The figure
    for now still cannot be produced because the float is still not recorded -- said so, as Đếm
    két says it, never a bare "bây giờ chưa tính được số phải có"."""
    shop, staff = _the_counted_day(connection)
    _record(connection, shop.store_id, staff, CLOSE, 300_000)
    _expense(connection, shop, ExpenseCategory.KHAC, 10_000, drawer=True)
    sheet = _sheet(connection, shop.store_id, staff)
    assert sheet.changed_since_count
    assert sheet.expected.status is ExpectedStatus.FLOAT_MISSING
    summary = _attention(connection, shop.store_id, shop.owner)
    (line,) = [line for line in summary.rendered.lines if line.key.value == "ATTN_CASH_COUNT"]
    assert line.text == (
        "- Đếm két cuối ngày được 300.000đ; lúc đếm chưa ghi tiền đầu ngày nên chưa so được. "
        "Sổ đã thay đổi sau lúc đếm: bây giờ vẫn chưa ghi tiền đầu ngày nên chưa tính được số "
        "phải có."
    )
    figures = dict(line.figures)
    assert figures["cash_expected_now_status"] == "FLOAT_MISSING"
    assert figures["cash_books_over_now_vnd"] is None


# --- roles ---


def test_counter_roles_record_the_owner_reads_every_day_and_nobody_else(connection: Any) -> None:
    shop, _ = _the_counted_day(connection)
    store = shop.store_id
    approver = _person(connection, store, frozenset({StaffRole.OPS_APPROVER}))
    operator = _person(connection, store, frozenset({StaffRole.OPERATOR}))
    auditor = _person(connection, store, frozenset({StaffRole.AUDITOR}))
    accountant = _person(connection, store, frozenset({StaffRole.ACCOUNTANT}))
    no_mfa = _person(connection, store, frozenset({StaffRole.OPERATOR}), mfa=False)
    stranger = _person(connection, None, frozenset({StaffRole.OWNER_ADMIN}))

    _record(connection, store, operator, FLOAT, 500_000)
    _record(connection, store, approver, CLOSE, 600_000)
    for reader in (operator, approver, shop.owner):
        assert _sheet(connection, store, reader).closing_count is not None
    for refused in (auditor, accountant, no_mfa, stranger):
        with pytest.raises(CashCountAuthorizationError):
            _sheet(connection, store, refused)
        with pytest.raises(CashCountAuthorizationError):
            _record(connection, store, refused, FLOAT, 1)
    for refused in (operator, approver, auditor, accountant, stranger):
        with pytest.raises(CashCountAuthorizationError), connection.cursor() as cursor:
            CashCountRepository.history(
                cursor,
                store_id=store,
                principal=refused,
                from_date=_today(),
                to_date=_today(),
                today=_today(),
            )
    for window, code in (
        ((_today(), _today() - timedelta(days=1)), "REPORT_WINDOW_REVERSED"),
        ((_today(), _today() + timedelta(days=1)), "REPORT_WINDOW_IN_FUTURE"),
        ((_today() - timedelta(days=92), _today()), "REPORT_WINDOW_TOO_LONG"),
    ):
        with pytest.raises(CashCountRefusal) as bad, connection.cursor() as cursor:
            CashCountRepository.history(
                cursor,
                store_id=store,
                principal=shop.owner,
                from_date=window[0],
                to_date=window[1],
                today=_today(),
            )
        assert bad.value.reason_code == code

    # A summary reader who may not read the counts is not told "nothing needs attention".
    summary = _attention(connection, store, approver)
    keys = [line.key.value for line in summary.rendered.lines]
    assert "ATTN_CASH_COUNT" not in keys and "ATTN_NONE" not in keys
    assert any(
        omission.key.value == "ATTN_CASH_COUNT" and omission.reason.value == "ROLE_NOT_PERMITTED"
        for omission in summary.rendered.omitted
    )


def test_another_stores_entries_are_never_in_this_stores_sheet(connection: Any) -> None:
    shop, staff = _the_counted_day(connection)
    other = _Shop(connection, datetime.now(UTC))
    other_staff = _person(connection, other.store_id, frozenset({StaffRole.OPERATOR}))
    _record(connection, other.store_id, other_staff, FLOAT, 123_000)
    sheet = _sheet(connection, shop.store_id, staff)
    assert sheet.opening_float is None and sheet.entries == ()
    other_sheet = _sheet(connection, other.store_id, other_staff)
    assert other_sheet.expected.movement.cash_in_vnd == 0
    with pytest.raises(CashCountAuthorizationError):
        _record(connection, other.store_id, staff, FLOAT, 1)


# --- Sổ thu chi's "Trả từ két" ---


def test_an_expense_says_whether_it_came_from_the_drawer_and_never_changes_its_mind(
    connection: Any,
) -> None:
    shop, _ = _the_counted_day(connection)
    plain = _expense(connection, shop, ExpenseCategory.NUOC, 10_000, drawer=False)
    drawer = _expense(connection, shop, ExpenseCategory.NUOC, 20_000, drawer=True)
    assert (plain.paid_from_drawer, drawer.paid_from_drawer) == (False, True)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="voided once"),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE expenses SET paid_from_drawer = TRUE, voided_at = now(),"
            " voided_by = recorded_by, row_version = 2 WHERE id = %s",
            (plain.expense_id,),
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT payload FROM domain_events WHERE aggregate_id = %s", (drawer.expense_id,)
        )
        assert cursor.fetchone()[0]["paid_from_drawer"] is True


# --- the shop-local day boundary ---


def test_the_drawer_day_turns_at_the_shops_midnight_not_utcs(connection: Any) -> None:
    """A cash payment at 23:59:59 in Nha Trang is that day's drawer; one at 00:00:00 is the next
    day's -- 17:00 UTC, not 00:00 UTC. A drawer expense counts on its own `spent_on` day."""
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    day = _today() - timedelta(days=3)
    late = datetime(day.year, day.month, day.day, 16, 59, 59, tzinfo=UTC)  # 23:59:59 +07:00
    turn = late + timedelta(seconds=1)  # 00:00:00 +07:00, the next shop day
    for at, amount in ((late, 30_000), (turn, 70_000)):
        order_id = _received(connection, shop, staff)
        PaymentRepository().record(
            connection,
            PaymentCommand(
                order_id=order_id,
                expected_row_version=_read(connection, order_id, staff).row_version,
                amount_vnd=amount,
                method=CASH,
                transfer_seen=False,
                bank_ref_last=None,
                collected_by_customer=False,
                principal=staff,
                correlation_id=uuid4(),
                recorded_at=at,
            ),
        )
    ExpenseRepository().record(
        connection,
        RecordExpenseCommand(
            store_id=shop.store_id,
            spent_on=day,
            category=ExpenseCategory.HOA_CHAT,
            amount_vnd=5_000,
            note=None,
            principal=shop.owner,
            idempotency_key=f"expense-{uuid4().hex}",
            correlation_id=uuid4(),
            today=_today(),
            paid_from_drawer=True,
        ),
    )
    with connection.cursor() as cursor:
        moved = cash_counts_module.drawer_movements(
            cursor, store_id=shop.store_id, from_date=day, to_date=day + timedelta(days=1)
        )
    first, second = moved[day], moved[day + timedelta(days=1)]
    assert (first.cash_in_vnd, first.cash_in_entries) == (30_000, 1)
    assert (second.cash_in_vnd, second.cash_in_entries) == (70_000, 1)
    assert (first.drawer_expenses_vnd, second.drawer_expenses_vnd) == (5_000, 0)
