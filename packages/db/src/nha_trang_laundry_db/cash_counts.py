"""The end-of-day cash count, *đếm két*: its entries and its reads (`CASH-COUNT-009`, `DEC-049`).

The rules are `nha_trang_laundry_domain.cash_count`'s; this module stores and reads them.

* **Recording.** The opening float and the closing count are each recorded once per store per
  business day; a correction is a new entry superseding the current one, with a reason. Every
  entry is a material change: the `cash_counts` row, a `CASH_COUNT_RECORDED` /
  `CASH_COUNT_CORRECTED` domain event, an audit row and an outbox row, in one transaction -- inside
  the idempotency claim,
  so a refused request leaves nothing behind, not even a spent key, and a replay returns the first
  answer. Entries for one store and day are serialised by a transaction advisory lock, so "is there
  already a float?" and "is this still the current count?" are answered under the lock that the
  insert commits under.
* **What the books say.** `_DRAWER_SQL` sums, per shop-local day, the cash taken
  (`order_payments.method = 'TIEN_MAT'`), the cash handed back (`order_refunds.refund_method =
  'TIEN_MAT'`), the refunds with no recorded method (`DEC-048`, excluded and counted), and the Sổ
  thu chi lines marked "trả từ két" that are not voided. The payment and refund predicates are
  `collected-today-v4`'s word for word, so this drawer and the one on *Hôm nay* cannot disagree.
  PostgreSQL adds; the domain subtracts once, on integers.
* **What was recorded.** A closing count stores the expected figure, the difference and the trace
  the domain computed at that moment, with its hash and the rule version. The day's read also
  recomputes the trace from the books *now*; when that hash differs from the stored one the books
  (or the float) moved after the count, and the read says so (`changed_since_count`) rather than
  quietly showing a different difference.

Roles: any counter role records and reads today's sheet (`CASH_COUNT_ROLES`, the drawer route's
gate); the owner reads every day (`CASH_COUNT_HISTORY_ROLES`). MFA and store membership always.
No correction reason is copied into an event, audit or outbox payload: it is the shop's own words,
stored once, like a Sổ thu chi note.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain import cash_count as rules
from nha_trang_laundry_domain.cash_count import (
    CASH_COUNT_RULE_IDENTIFIER,
    CashCountError,
    CashCountKind,
    DifferenceDirection,
    DrawerMovement,
    ExpectedCash,
    ExpectedStatus,
    cash_difference,
    closing_trace,
    correction_reason,
    counted_amount,
    expected_cash,
    recording_day,
)

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.query_version import query_version, rule_source
from nha_trang_laundry_db.reports import ReportWindowError, validate_window
from nha_trang_laundry_db.settlement import BUSINESS_TIMEZONE
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: `DEC-049`: "any counter role records" -- exactly the roles the drawer figure is read by
#: (`require_operations_staff`, `SETTLEMENT_ROLES`). They read today's sheet to count against it.
CASH_COUNT_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)
#: `DEC-049`: "owner reads all" -- every day's counts and differences, on the report.
CASH_COUNT_HISTORY_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN})

#: A history read lists at most this many entries; beyond it the read says it is truncated.
HISTORY_MAX_ENTRIES: Final = 1000

#: The day's drawer, per shop-local day of a window, in one statement. The payment and refund
#: predicates are `collected-today-v4`'s; the expense predicate is Sổ thu chi's own day
#: (`spent_on`), not voided, marked "trả từ két".
_DRAWER_SQL: Final = """
    WITH days AS (
        SELECT generate_series(%(from_date)s::date, %(to_date)s::date, interval '1 day')::date
            AS day
    ), taken AS (
        SELECT (recorded_at AT TIME ZONE %(zone)s)::date AS day,
               coalesce(sum(amount_vnd), 0) AS cash, count(*) AS entries
        FROM order_payments
        WHERE store_id = %(store)s
          AND method = 'TIEN_MAT'
          AND (recorded_at AT TIME ZONE %(zone)s)::date BETWEEN %(from_date)s AND %(to_date)s
        GROUP BY 1
    ), refunded AS (
        SELECT (refunded_at AT TIME ZONE %(zone)s)::date AS day,
               coalesce(sum(refunded_amount_vnd) FILTER (WHERE refund_method = 'TIEN_MAT'), 0)
                   AS cash,
               count(*) FILTER (WHERE refund_method = 'TIEN_MAT') AS cash_entries,
               coalesce(sum(refunded_amount_vnd) FILTER (WHERE refund_method IS NULL), 0)
                   AS unknown,
               count(*) FILTER (WHERE refund_method IS NULL) AS unknown_entries
        FROM order_refunds
        WHERE store_id = %(store)s
          AND direction = 'TO_CUSTOMER'
          AND (refunded_at AT TIME ZONE %(zone)s)::date BETWEEN %(from_date)s AND %(to_date)s
        GROUP BY 1
    ), spent AS (
        SELECT spent_on AS day, coalesce(sum(amount_vnd), 0) AS amount, count(*) AS entries
        FROM expenses
        WHERE store_id = %(store)s
          AND spent_on BETWEEN %(from_date)s AND %(to_date)s
          AND paid_from_drawer
          AND voided_at IS NULL
        GROUP BY 1
    )
    SELECT days.day,
           coalesce(taken.cash, 0), coalesce(taken.entries, 0),
           coalesce(refunded.cash, 0), coalesce(refunded.cash_entries, 0),
           coalesce(refunded.unknown, 0), coalesce(refunded.unknown_entries, 0),
           coalesce(spent.amount, 0), coalesce(spent.entries, 0)
    FROM days
    LEFT JOIN taken ON taken.day = days.day
    LEFT JOIN refunded ON refunded.day = days.day
    LEFT JOIN spent ON spent.day = days.day
    ORDER BY days.day
"""

#: The rule's published version: the identifier, the domain rule's structure, the statement and
#: the day boundary. It travels with every figure and is stored on every closing count.
CASH_COUNT_QUERY: Final = query_version(
    CASH_COUNT_RULE_IDENTIFIER, rule_source(rules), _DRAWER_SQL, BUSINESS_TIMEZONE
)

_ENTRY_COLUMNS: Final = """
    c.id, c.business_day, c.kind, c.counted_vnd, c.supersedes_id, c.correction_reason,
    c.expected_status, c.expected_vnd, c.difference_vnd, c.difference_direction,
    c.float_entry_id, c.trace, c.trace_hash, c.rule_version, c.recorded_by, u.display_name,
    c.recorded_at,
    EXISTS (SELECT 1 FROM cash_counts n WHERE n.supersedes_id = c.id) AS superseded
"""


class CashCountAuthorizationError(PermissionError):
    """Wrong role, no MFA, or not a member of the store. One opaque refusal for all three."""


class CashCountRefusal(ValueError):
    """A named refusal: `reason_code` is stable and the console words it.

    `conflict` marks the refusals that are about the day's state rather than the value typed --
    an entry already recorded, a correction of an entry that is no longer the current one -- which
    the API answers 409 so the console re-reads the sheet.
    """

    def __init__(self, reason_code: str, message: str, *, conflict: bool = False) -> None:
        self.reason_code = reason_code
        self.conflict = conflict
        super().__init__(message)


def _refusal(error: CashCountError) -> CashCountRefusal:
    return CashCountRefusal(error.reason_code, str(error))


def _require(principal: StaffPrincipal, roles: frozenset[StaffRole]) -> None:
    if not principal.roles & roles or not principal.mfa_verified:
        raise CashCountAuthorizationError("the cash count requires another role with MFA")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


@dataclass(frozen=True, slots=True)
class CashCountEntry:
    entry_id: UUID
    business_day: date
    kind: CashCountKind
    counted_vnd: int
    supersedes_id: UUID | None
    correction_reason: str | None
    #: A closing count's record of the books at the count; `None` on an opening float.
    expected_status: ExpectedStatus | None
    expected_vnd: int | None
    difference_vnd: int | None
    difference_direction: DifferenceDirection | None
    float_entry_id: UUID | None
    trace: dict[str, object] | None
    trace_hash: str | None
    rule_version: str | None
    recorded_by: UUID
    recorded_by_name: str | None
    recorded_at: datetime
    #: A later correction replaced this entry; the current entry is the one nothing supersedes.
    superseded: bool


@dataclass(frozen=True, slots=True)
class CashCountDay:
    store_id: UUID
    business_day: date
    #: The current entries (nothing supersedes them), or `None` when not recorded.
    opening_float: CashCountEntry | None
    closing_count: CashCountEntry | None
    #: Every entry of the day, oldest first, corrections and the entries they replaced included.
    entries: tuple[CashCountEntry, ...]
    #: What the books say the drawer should hold now, from the current float.
    expected: ExpectedCash
    #: A closing count is recorded and the books (or the float) say something else now than they
    #: did when it was recorded.
    changed_since_count: bool
    query_version: str


@dataclass(frozen=True, slots=True)
class CashCountHistory:
    store_id: UUID
    from_date: date
    to_date: date
    #: The days of the window that have any entry, oldest first.
    days: tuple[CashCountDay, ...]
    truncated: bool
    query_version: str


@dataclass(frozen=True)
class RecordCashCountCommand:
    store_id: UUID
    business_day: date
    kind: CashCountKind
    counted_vnd: int
    #: The current entry this one corrects, or `None` for the day's first entry of its kind.
    supersedes_entry_id: UUID | None
    reason: str | None
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    #: The shop's today, passed in: an entry is for today only.
    today: date
    occurred_at: datetime | None = None


def _entry(row: Any) -> CashCountEntry:
    trace = row[11]
    if isinstance(trace, str):  # pragma: no cover - psycopg decodes JSONB to a dict
        trace = json.loads(trace)
    return CashCountEntry(
        entry_id=_uuid(row[0]),
        business_day=row[1],
        kind=CashCountKind(str(row[2])),
        counted_vnd=int(row[3]),
        supersedes_id=None if row[4] is None else _uuid(row[4]),
        correction_reason=None if row[5] is None else str(row[5]),
        expected_status=None if row[6] is None else ExpectedStatus(str(row[6])),
        expected_vnd=None if row[7] is None else int(row[7]),
        difference_vnd=None if row[8] is None else int(row[8]),
        difference_direction=None if row[9] is None else DifferenceDirection(str(row[9])),
        float_entry_id=None if row[10] is None else _uuid(row[10]),
        trace=trace,
        trace_hash=None if row[12] is None else str(row[12]),
        rule_version=None if row[13] is None else str(row[13]),
        recorded_by=_uuid(row[14]),
        recorded_by_name=None if row[15] is None else str(row[15]),
        recorded_at=row[16],
        superseded=bool(row[17]),
    )


def drawer_movements(
    cursor: Any, *, store_id: UUID, from_date: date, to_date: date
) -> dict[date, DrawerMovement]:
    """Every day of a window's drawer movement, summed by PostgreSQL. No membership check here:
    callers check before they read."""
    cursor.execute(
        _DRAWER_SQL,
        {"store": store_id, "zone": BUSINESS_TIMEZONE, "from_date": from_date, "to_date": to_date},
    )
    return {
        row[0]: DrawerMovement(
            cash_in_vnd=int(row[1]),
            cash_in_entries=int(row[2]),
            cash_refunded_vnd=int(row[3]),
            cash_refunded_entries=int(row[4]),
            unknown_refunds_vnd=int(row[5]),
            unknown_refunds_entries=int(row[6]),
            drawer_expenses_vnd=int(row[7]),
            drawer_expenses_entries=int(row[8]),
        )
        for row in cursor.fetchall()
    }


def _entries(
    cursor: Any, *, store_id: UUID, from_date: date, to_date: date, limit: int
) -> list[CashCountEntry]:
    cursor.execute(
        f"""
        SELECT {_ENTRY_COLUMNS}
        FROM cash_counts c
        LEFT JOIN staff_users u ON u.id = c.recorded_by
        WHERE c.store_id = %s AND c.business_day BETWEEN %s AND %s
        ORDER BY c.business_day, c.recorded_at, c.id
        LIMIT %s
        """,
        (store_id, from_date, to_date, limit),
    )
    return [_entry(row) for row in cursor.fetchall()]


def _current(entries: list[CashCountEntry], kind: CashCountKind) -> CashCountEntry | None:
    found = [entry for entry in entries if entry.kind is kind and not entry.superseded]
    return found[-1] if found else None


def _day(
    store_id: UUID, day: date, entries: list[CashCountEntry], movement: DrawerMovement
) -> CashCountDay:
    opening = _current(entries, CashCountKind.OPENING_FLOAT)
    closing = _current(entries, CashCountKind.CLOSING_COUNT)
    expected = expected_cash(None if opening is None else opening.counted_vnd, movement)
    changed = False
    if closing is not None:
        now = closing_trace(
            business_day=day,
            counted_vnd=closing.counted_vnd,
            expected=expected,
            difference=cash_difference(closing.counted_vnd, expected),
        )
        changed = now.trace_hash != closing.trace_hash
    return CashCountDay(
        store_id=store_id,
        business_day=day,
        opening_float=opening,
        closing_count=closing,
        entries=tuple(entries),
        expected=expected,
        changed_since_count=changed,
        query_version=CASH_COUNT_QUERY.label,
    )


def read_day(cursor: Any, *, store_id: UUID, day: date) -> CashCountDay:
    """One day's sheet. No role or membership check: callers check before they read."""
    entries = _entries(
        cursor, store_id=store_id, from_date=day, to_date=day, limit=HISTORY_MAX_ENTRIES
    )
    movement = drawer_movements(cursor, store_id=store_id, from_date=day, to_date=day)[day]
    return _day(store_id, day, entries, movement)


class CashCountRepository:
    """Record an entry; read today's sheet; read the owner's history."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    @staticmethod
    def today(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, today: date
    ) -> CashCountDay:
        """Today's sheet for the counter: entries, what the books say now, the recorded count."""
        _require(principal, CASH_COUNT_ROLES)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=CashCountAuthorizationError,
        )
        return read_day(cursor, store_id=store_id, day=today)

    @staticmethod
    def history(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        from_date: date,
        to_date: date,
        today: date,
    ) -> CashCountHistory:
        """The owner's read: every day of a report window that has an entry."""
        _require(principal, CASH_COUNT_HISTORY_ROLES)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=CashCountAuthorizationError,
        )
        try:
            validate_window(from_date, to_date, today=today)
        except ReportWindowError as error:
            raise CashCountRefusal(error.reason_code, str(error)) from error
        entries = _entries(
            cursor,
            store_id=store_id,
            from_date=from_date,
            to_date=to_date,
            limit=HISTORY_MAX_ENTRIES + 1,
        )
        truncated = len(entries) > HISTORY_MAX_ENTRIES
        entries = entries[:HISTORY_MAX_ENTRIES]
        movements = drawer_movements(
            cursor, store_id=store_id, from_date=from_date, to_date=to_date
        )
        by_day: dict[date, list[CashCountEntry]] = {}
        for entry in entries:
            by_day.setdefault(entry.business_day, []).append(entry)
        return CashCountHistory(
            store_id=store_id,
            from_date=from_date,
            to_date=to_date,
            days=tuple(_day(store_id, day, by_day[day], movements[day]) for day in sorted(by_day)),
            truncated=truncated,
            query_version=CASH_COUNT_QUERY.label,
        )

    def record(
        self, connection: Any, command: RecordCashCountCommand
    ) -> tuple[CashCountEntry, CashCountDay, bool]:
        """Record a float, a count or a correction of either, with its event, audit and outbox."""
        _require(command.principal, CASH_COUNT_ROLES)
        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=CashCountAuthorizationError,
            )
        moment = command.occurred_at or datetime.now(UTC)
        entry_id = uuid4()

        def record_once() -> dict[str, object]:
            # Validated inside the claim: a refusal rolls the claim back with everything else, and a
            # replay of an answered request returns its answer even on a later day.
            try:
                day = recording_day(command.business_day, today=command.today)
                counted = counted_amount(command.counted_vnd)
                reason = correction_reason(
                    command.reason, correcting=command.supersedes_entry_id is not None
                )
            except CashCountError as error:
                raise _refusal(error) from error

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"cash_count:{command.store_id}:{day.isoformat()}",),
                )
                entries = _entries(
                    cursor,
                    store_id=command.store_id,
                    from_date=day,
                    to_date=day,
                    limit=HISTORY_MAX_ENTRIES,
                )
                current = _current(entries, command.kind)
                if command.supersedes_entry_id is None and current is not None:
                    raise CashCountRefusal(
                        "CASH_COUNT_ALREADY_RECORDED",
                        "this entry is already recorded today; correct it instead",
                        conflict=True,
                    )
                if command.supersedes_entry_id is not None and (
                    current is None or current.entry_id != command.supersedes_entry_id
                ):
                    raise CashCountRefusal(
                        "CASH_COUNT_STALE",
                        "the entry being corrected is no longer the current one; read again",
                        conflict=True,
                    )
                snapshot: dict[str, Any] = {}
                if command.kind is CashCountKind.CLOSING_COUNT:
                    opening = _current(entries, CashCountKind.OPENING_FLOAT)
                    movement = drawer_movements(
                        cursor, store_id=command.store_id, from_date=day, to_date=day
                    )[day]
                    expected = expected_cash(
                        None if opening is None else opening.counted_vnd, movement
                    )
                    difference = cash_difference(counted, expected)
                    trace = closing_trace(
                        business_day=day,
                        counted_vnd=counted,
                        expected=expected,
                        difference=difference,
                    )
                    snapshot = {
                        "expected_status": expected.status.value,
                        "expected_vnd": expected.expected_vnd,
                        "difference_vnd": None if difference is None else difference.amount_vnd,
                        "difference_direction": (
                            None if difference is None else difference.direction.value
                        ),
                        "float_entry_id": None if opening is None else opening.entry_id,
                        "trace": trace.document,
                        "trace_hash": trace.trace_hash,
                        "rule_version": CASH_COUNT_QUERY.label,
                    }

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO cash_counts (
                        id, store_id, business_day, kind, counted_vnd, supersedes_id,
                        correction_reason, expected_status, expected_vnd, difference_vnd,
                        difference_direction, float_entry_id, trace, trace_hash, rule_version,
                        recorded_by, recorded_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s
                    )
                    """,
                    (
                        entry_id,
                        command.store_id,
                        day,
                        command.kind.value,
                        counted,
                        command.supersedes_entry_id,
                        reason,
                        snapshot.get("expected_status"),
                        snapshot.get("expected_vnd"),
                        snapshot.get("difference_vnd"),
                        snapshot.get("difference_direction"),
                        snapshot.get("float_entry_id"),
                        None
                        if "trace" not in snapshot
                        else json.dumps(snapshot["trace"], sort_keys=True),
                        snapshot.get("trace_hash"),
                        snapshot.get("rule_version"),
                        command.principal.staff_user_id,
                        moment,
                    ),
                )

            correcting = command.supersedes_entry_id is not None
            payload: dict[str, object] = {
                "entry_id": str(entry_id),
                "store_id": str(command.store_id),
                "business_day": day.isoformat(),
                "kind": command.kind.value,
                "counted_vnd": counted,
                "supersedes_id": (
                    None
                    if command.supersedes_entry_id is None
                    else str(command.supersedes_entry_id)
                ),
            }
            if snapshot:
                payload.update(
                    {
                        "expected_status": snapshot["expected_status"],
                        "expected_vnd": snapshot["expected_vnd"],
                        "difference_vnd": snapshot["difference_vnd"],
                        "difference_direction": snapshot["difference_direction"],
                        "trace_hash": snapshot["trace_hash"],
                    }
                )
            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="CASH_COUNT",
                    aggregate_id=entry_id,
                    aggregate_version=1,
                    event_type="CASH_COUNT_CORRECTED" if correcting else "CASH_COUNT_RECORDED",
                    event_payload=payload,
                    audit_action="CASH_COUNT_CORRECT" if correcting else "CASH_COUNT_RECORD",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "shop.cash_count_recorded.v1",
                            {
                                "entry_id": str(entry_id),
                                "store_id": str(command.store_id),
                                "business_day": day.isoformat(),
                                "kind": command.kind.value,
                            },
                            f"cash-count:{entry_id}:recorded",
                        ),
                    ),
                    occurred_at=moment,
                    audit_details={
                        "business_day": day.isoformat(),
                        "kind": command.kind.value,
                        "counted_vnd": counted,
                        "correction": correcting,
                        **(
                            {
                                "difference_vnd": snapshot["difference_vnd"],
                                "difference_direction": snapshot["difference_direction"],
                            }
                            if snapshot
                            else {}
                        ),
                    },
                ),
                mutation,
            )
            return {"entry_id": str(entry_id)}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"store:{command.store_id}:cash-count",
                command.idempotency_key,
                {
                    "business_day": command.business_day.isoformat(),
                    "kind": command.kind.value,
                    "counted_vnd": command.counted_vnd,
                    "supersedes": (
                        None
                        if command.supersedes_entry_id is None
                        else str(command.supersedes_entry_id)
                    ),
                    "reason": command.reason,
                },
                moment,
            ),
            record_once,
        )
        recorded_id = _uuid(result.response["entry_id"])
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_ENTRY_COLUMNS}
                FROM cash_counts c LEFT JOIN staff_users u ON u.id = c.recorded_by
                WHERE c.id = %s
                """,
                (recorded_id,),
            )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - rows are never deleted
                raise CashCountRefusal("CASH_COUNT_MISSING", "the entry is missing")
            entry = _entry(row)
            sheet = read_day(cursor, store_id=command.store_id, day=entry.business_day)
        return entry, sheet, result.replayed


__all__ = [
    "CASH_COUNT_HISTORY_ROLES",
    "CASH_COUNT_QUERY",
    "CASH_COUNT_ROLES",
    "HISTORY_MAX_ENTRIES",
    "CashCountAuthorizationError",
    "CashCountDay",
    "CashCountEntry",
    "CashCountHistory",
    "CashCountRefusal",
    "CashCountRepository",
    "RecordCashCountCommand",
    "drawer_movements",
    "read_day",
]
