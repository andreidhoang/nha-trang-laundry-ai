"""The end-of-day cash count, *đếm két* (`CASH-COUNT-009`, `DEC-049`).

Pure: no clock, no database, no environment. The business day, the shop's today and every figure
summed from the ledgers arrive as parameters, so a count can be recomputed from its stored trace
years later and land on the same digest.

What this module decides, and nothing else decides:

* **What the drawer should hold.** `DEC-049`: *expected = opening float + cash taken - cash
  refunded - Sổ thu chi lines marked "trả từ két"*, for one shop-local business day, in whole đồng.
  The three ledger figures are PostgreSQL's sums, passed in; the only arithmetic on money in the
  whole feature is the one line in `expected_cash` and the one in `cash_difference`, both on
  integers.
* **What it does not know, it does not guess.** No opening float recorded is not a float of 0:
  the expected figure is not produced (`FLOAT_MISSING`). Refunds written before `0067` never said
  how the money went back (`DEC-048`): they are left out, counted and summed, and the figure is
  `INCOMPLETE` while any are. Books that say more cash left the drawer than was ever in it are not
  clamped to 0: the figure is not produced (`BOOKS_BELOW_ZERO`) and the size of the gap is given.
* **The difference, as a size and a word.** Counted against expected: `EVEN`, `OVER` (*thừa*) or
  `SHORT` (*thiếu*), and the magnitude -- never a signed number, so no minus sign can reach a
  screen or a printout. An expected figure that was not produced has no difference.
* **What an entry looks like.** The opening float and the closing count are each recorded once per
  store per business day; a correction is a new entry that names the one it supersedes and says
  why. An original carries no reason; a correction must. Entries are recorded for the shop's today
  only -- a drawer cannot be counted yesterday.

Nothing here adjusts money, blames anyone or triggers anything (`DEC-049`: "nothing happens
automatically"). It produces figures a person reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.money import require_non_negative_vnd
from nha_trang_laundry_domain.shop_capture import ShopCaptureError, capture_note

#: The rule's published identifier. The repository's version label hashes this module's structure
#: and the statement that sums the ledgers beside it, so a changed rule cannot keep this name.
CASH_COUNT_RULE_IDENTIFIER: Final = "cash-count-v1"

#: A count's ceiling: a thousand million đồng, the bound a Sổ thu chi line has. A drawer holding
#: more is a slipped zero, not a count.
COUNTED_MAX_VND: Final = 1_000_000_000

#: A correction's reason, at most this many characters (the length of a Sổ thu chi note).
REASON_MAX: Final = 120


class CashCountError(ValueError):
    """A cash-count value the rules refuse. `reason_code` is the stable name."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class CashCountKind(StrEnum):
    #: The cash counted into the drawer at the start of the business day.
    OPENING_FLOAT = "OPENING_FLOAT"
    #: The cash counted in the drawer at the end of it.
    CLOSING_COUNT = "CLOSING_COUNT"


class ExpectedStatus(StrEnum):
    #: Every input is recorded: the figure is what the books say the drawer holds.
    COMPLETE = "COMPLETE"
    #: Produced, but refunds of unknown method (`DEC-048`) are left out of it: it is what the books
    #: say *apart from* those, and is marked so.
    INCOMPLETE = "INCOMPLETE"
    #: No opening float is recorded for the day. Unknown is not zero: no figure.
    FLOAT_MISSING = "FLOAT_MISSING"
    #: The books say more cash left the drawer than the float and the takings put in. A drawer
    #: cannot hold less than nothing, so something in the books is wrong: no figure.
    BOOKS_BELOW_ZERO = "BOOKS_BELOW_ZERO"


#: The statuses under which an expected figure exists.
PRODUCED: Final = frozenset({ExpectedStatus.COMPLETE, ExpectedStatus.INCOMPLETE})


class DifferenceDirection(StrEnum):
    #: Counted equals expected.
    EVEN = "EVEN"
    #: More cash in the drawer than expected: *thừa*.
    OVER = "OVER"
    #: Less cash in the drawer than expected: *thiếu*.
    SHORT = "SHORT"


def _count(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} is a non-negative whole number")
    return value


@dataclass(frozen=True, slots=True)
class DrawerMovement:
    """One business day's cash through the drawer, as the ledgers record it.

    Every amount is PostgreSQL's sum over a BIGINT column and every count its `count(*)`; nothing
    here adds them. `unknown_refunds_*` are the refunds with no recorded method, which the expected
    figure excludes rather than nets.
    """

    cash_in_vnd: int
    cash_in_entries: int
    cash_refunded_vnd: int
    cash_refunded_entries: int
    unknown_refunds_vnd: int
    unknown_refunds_entries: int
    drawer_expenses_vnd: int
    drawer_expenses_entries: int

    def __post_init__(self) -> None:
        for name in (
            "cash_in_vnd",
            "cash_refunded_vnd",
            "unknown_refunds_vnd",
            "drawer_expenses_vnd",
        ):
            require_non_negative_vnd(getattr(self, name))
        for name in (
            "cash_in_entries",
            "cash_refunded_entries",
            "unknown_refunds_entries",
            "drawer_expenses_entries",
        ):
            _count(getattr(self, name), name)


#: A day the ledgers recorded nothing for.
NO_MOVEMENT: Final = DrawerMovement(0, 0, 0, 0, 0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class ExpectedCash:
    status: ExpectedStatus
    #: Produced only under `PRODUCED`; `None` otherwise -- never a stand-in 0.
    expected_vnd: int | None
    #: The opening float the figure started from, or `None` when none is recorded.
    float_vnd: int | None
    movement: DrawerMovement
    #: `BOOKS_BELOW_ZERO` only: how far below nothing the books put the drawer.
    books_over_vnd: int | None = None

    @property
    def produced(self) -> bool:
        return self.status in PRODUCED


def expected_cash(float_vnd: int | None, movement: DrawerMovement) -> ExpectedCash:
    """`DEC-049`: float + cash taken - cash refunded - drawer expenses, or why there is none.

    The refunds of unknown method are not in the sum whatever happens; they only decide whether a
    produced figure is `COMPLETE` or `INCOMPLETE`.
    """
    if float_vnd is None:
        return ExpectedCash(ExpectedStatus.FLOAT_MISSING, None, None, movement)
    require_non_negative_vnd(float_vnd)
    held = (
        float_vnd + movement.cash_in_vnd - movement.cash_refunded_vnd - movement.drawer_expenses_vnd
    )
    if held < 0:
        return ExpectedCash(ExpectedStatus.BOOKS_BELOW_ZERO, None, float_vnd, movement, -held)
    status = (
        ExpectedStatus.INCOMPLETE if movement.unknown_refunds_entries else ExpectedStatus.COMPLETE
    )
    return ExpectedCash(status, held, float_vnd, movement)


@dataclass(frozen=True, slots=True)
class CashDifference:
    #: The size of the gap, never signed.
    amount_vnd: int
    direction: DifferenceDirection


def cash_difference(counted_vnd: int, expected: ExpectedCash) -> CashDifference | None:
    """Counted against expected, as a magnitude and a word; `None` when no figure was produced."""
    require_non_negative_vnd(counted_vnd)
    if expected.expected_vnd is None:
        return None
    gap = counted_vnd - expected.expected_vnd
    if gap == 0:
        return CashDifference(0, DifferenceDirection.EVEN)
    return CashDifference(
        abs(gap), DifferenceDirection.OVER if gap > 0 else DifferenceDirection.SHORT
    )


# --- what an entry may be ---


def counted_amount(value: int) -> int:
    """A counted amount as recorded: whole đồng, 0 included (an empty drawer is a count)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CashCountError("CASH_COUNT_AMOUNT_INVALID", "a count is a whole number of đồng")
    if value < 0:
        raise CashCountError("CASH_COUNT_AMOUNT_INVALID", "a count is not below 0 đồng")
    if value > COUNTED_MAX_VND:
        raise CashCountError(
            "CASH_COUNT_AMOUNT_TOO_LARGE", "a count is at most 1.000.000.000 đồng; check the figure"
        )
    return value


def recording_day(asked: date, *, today: date) -> date:
    """The business day an entry is for: the shop's today, and no other.

    The caller names the day it means, so a sheet left open across midnight records nothing for a
    day it never showed: a drawer counted now is today's drawer.
    """
    if asked != today:
        raise CashCountError(
            "CASH_COUNT_DAY_NOT_TODAY", "a drawer is counted on the shop's today only"
        )
    return asked


def correction_reason(value: str | None, *, correcting: bool) -> str | None:
    """The reason an entry carries: none on an original, one on a correction.

    The reason is the shop's own words about its drawer -- "đếm sót tờ 50.000" -- and, like a Sổ
    thu chi note, is refused if it looks like a phone number.
    """
    try:
        reason = capture_note(value)
    except ShopCaptureError as error:
        code = (
            "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"
            if error.reason_code == "NOTE_LOOKS_LIKE_PHONE"
            else "CASH_COUNT_REASON_INVALID"
        )
        raise CashCountError(code, str(error)) from error
    if correcting and reason is None:
        raise CashCountError(
            "CASH_COUNT_REASON_REQUIRED", "a correction says why the first figure was wrong"
        )
    if not correcting and reason is not None:
        raise CashCountError(
            "CASH_COUNT_REASON_NOT_EXPECTED", "a first entry carries no correction reason"
        )
    return reason


# --- the trace a closing count stores ---


@dataclass(frozen=True, slots=True)
class CountTrace:
    """Everything a closing count's figures came from, and its digest.

    `document` is plain JSON (strings and integers only); `trace_hash` is its RFC 8785 digest, so
    the stored row can be re-derived and checked: `closing_trace` over the same inputs returns the
    same hash, byte for byte.
    """

    document: dict[str, object]
    trace_hash: str


def closing_trace(
    *,
    business_day: date,
    counted_vnd: int,
    expected: ExpectedCash,
    difference: CashDifference | None,
) -> CountTrace:
    """The closing count's calculation, as a document and its hash."""
    movement = expected.movement
    document: dict[str, object] = {
        "rule": CASH_COUNT_RULE_IDENTIFIER,
        "business_day": business_day.isoformat(),
        "float_vnd": expected.float_vnd,
        "cash_in_vnd": movement.cash_in_vnd,
        "cash_in_entries": movement.cash_in_entries,
        "cash_refunded_vnd": movement.cash_refunded_vnd,
        "cash_refunded_entries": movement.cash_refunded_entries,
        "drawer_expenses_vnd": movement.drawer_expenses_vnd,
        "drawer_expenses_entries": movement.drawer_expenses_entries,
        "excluded_unknown_refunds_vnd": movement.unknown_refunds_vnd,
        "excluded_unknown_refunds_entries": movement.unknown_refunds_entries,
        "status": expected.status.value,
        "expected_vnd": expected.expected_vnd,
        "books_over_vnd": expected.books_over_vnd,
        "counted_vnd": counted_vnd,
        "difference_vnd": None if difference is None else difference.amount_vnd,
        "difference_direction": None if difference is None else difference.direction.value,
    }
    return CountTrace(document, canonical_document(document).snapshot_hash)


def replay_trace(document: dict[str, object]) -> CountTrace:
    """Recompute a stored trace from its own inputs: the check that a count is reproducible."""

    def number(key: str) -> int:
        value = document[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} is not a whole number in the stored trace")
        return value

    if document.get("rule") != CASH_COUNT_RULE_IDENTIFIER:
        raise ValueError("the stored trace was written under another rule")
    float_value = document.get("float_vnd")
    if float_value is not None and (
        isinstance(float_value, bool) or not isinstance(float_value, int)
    ):
        raise ValueError("float_vnd is not a whole number in the stored trace")
    movement = DrawerMovement(
        cash_in_vnd=number("cash_in_vnd"),
        cash_in_entries=number("cash_in_entries"),
        cash_refunded_vnd=number("cash_refunded_vnd"),
        cash_refunded_entries=number("cash_refunded_entries"),
        unknown_refunds_vnd=number("excluded_unknown_refunds_vnd"),
        unknown_refunds_entries=number("excluded_unknown_refunds_entries"),
        drawer_expenses_vnd=number("drawer_expenses_vnd"),
        drawer_expenses_entries=number("drawer_expenses_entries"),
    )
    expected = expected_cash(float_value, movement)
    counted = number("counted_vnd")
    return closing_trace(
        business_day=date.fromisoformat(str(document["business_day"])),
        counted_vnd=counted,
        expected=expected,
        difference=cash_difference(counted, expected),
    )


__all__ = [
    "CASH_COUNT_RULE_IDENTIFIER",
    "COUNTED_MAX_VND",
    "NO_MOVEMENT",
    "PRODUCED",
    "REASON_MAX",
    "CashCountError",
    "CashCountKind",
    "CashDifference",
    "CountTrace",
    "DifferenceDirection",
    "DrawerMovement",
    "ExpectedCash",
    "ExpectedStatus",
    "cash_difference",
    "closing_trace",
    "correction_reason",
    "counted_amount",
    "expected_cash",
    "recording_day",
    "replay_trace",
]
