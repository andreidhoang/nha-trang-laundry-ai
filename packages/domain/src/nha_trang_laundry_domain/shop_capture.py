"""Measuring the shop inside taps staff already make (`SHOP-CAPTURE-001`, `DEC-038`).

Pure: no clock, no database, no environment. Every instant and every "today" is a parameter.

What this module decides, and nothing else decides:

* **Which machines a wash can start on.** A wash cycle runs from *Bắt đầu giặt* to *Giặt xong,
  kiểm tra đồ*, and the machine asked for at the first tap is the one the load goes into: a washer,
  the dry-cleaner or the shoe machine. A dryer or an ironing table is on the machine list and is
  never offered there.
* **When a production move opens or closes a cycle.** Into `IN_PROCESS` from anywhere else opens
  one (the repository opens it only when none is open, so a hold or an exception resumed where it
  stopped continues the same cycle); into `QUALITY_CHECK` closes it. A `REWASH` step moves the
  order back into `IN_PROCESS` after the cycle closed at quality check, so it opens a new cycle --
  a second load really is washed.
* **The vehicle the owner's rule suggests** (`BUSINESS_TRUTH_INTAKE.md` §1, ĐÃ XÁC NHẬN): under
  20 kg a motorbike, from exactly 20 kg a car. A weight that is not known -- any line priced by the
  piece, or no line at all -- suggests nothing: unknown is not zero.
* **What a trip cost looks like** and **what an expense looks like**: the bounds, the note length,
  and a note that looks like a phone number refused, so a customer's number is not typed into the
  shop's books.
* **Margin, only when complete** (`FR-RPT-002`): a month shows a figure only when its spending has
  every core category -- electricity, water, chemicals, wages and rent. Otherwise it is
  `INCOMPLETE` with the missing list, and the remainder is never produced, let alone called profit.
* **The rounding of a per-order average**, the one division of money this slice makes: half up to
  the whole đồng, on integers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import ProductionStatus
from nha_trang_laundry_domain.money import require_non_negative_vnd


class ShopCaptureError(ValueError):
    """A capture value the shop's rules refuse. `reason_code` is the stable name."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


# --- machines and cycles ----------------------------------------------------------------------


class MachineCategory(StrEnum):
    """`templates/machine-master.csv`'s `category` column, plus `other` for what the owner adds."""

    WASHER = "washer"
    DRYER = "dryer"
    DRY_CLEANER = "dry_cleaner"
    SHOE_WASHER_DRYER = "shoe_washer_dryer"
    VACUUM_IRONING_TABLE = "vacuum_ironing_table"
    BOILER_IRON_SET = "boiler_iron_set"
    OTHER = "other"


#: The machines a load goes into at *Bắt đầu giặt*.
CYCLE_START_CATEGORIES: Final = frozenset(
    {MachineCategory.WASHER, MachineCategory.DRY_CLEANER, MachineCategory.SHOE_WASHER_DRYER}
)

#: The machine code painted on the machine: `WASH-01`. Upper case, digits and hyphens.
MACHINE_CODE: Final = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")
MACHINE_NAME_MAX: Final = 60


def starts_cycle(category: MachineCategory) -> bool:
    """Whether a wash can be started on a machine of this category."""
    return category in CYCLE_START_CATEGORIES


def machine_code(value: str) -> str:
    """A machine code as stored, or `MACHINE_CODE_INVALID`."""
    code = value.strip().upper()
    if not MACHINE_CODE.fullmatch(code):
        raise ShopCaptureError(
            "MACHINE_CODE_INVALID", "a machine code is 1-32 letters, digits or hyphens"
        )
    return code


def machine_name(value: str) -> str:
    """A machine's display name as stored, or `MACHINE_NAME_INVALID`."""
    name = " ".join(value.split())
    if not 1 <= len(name) <= MACHINE_NAME_MAX or _has_control(name):
        raise ShopCaptureError(
            "MACHINE_NAME_INVALID", f"a machine name is 1-{MACHINE_NAME_MAX} characters"
        )
    return name


class CycleEffect(StrEnum):
    #: Open a cycle if the order has none open.
    OPEN = "OPEN"
    #: Close the order's open cycle, if it has one.
    CLOSE = "CLOSE"
    NONE = "NONE"


def cycle_effect(before: ProductionStatus, after: ProductionStatus) -> CycleEffect:
    """What one production move does to the order's wash cycle.

    Only production moves reach here; a commercial or intake move never touches a cycle.
    """
    if after is ProductionStatus.IN_PROCESS and before is not ProductionStatus.IN_PROCESS:
        return CycleEffect.OPEN
    if after is ProductionStatus.QUALITY_CHECK and before is not ProductionStatus.QUALITY_CHECK:
        return CycleEffect.CLOSE
    return CycleEffect.NONE


# --- delivery trips ---------------------------------------------------------------------------


class Vehicle(StrEnum):
    XE_MAY = "XE_MAY"
    O_TO = "O_TO"
    #: Grab, Ahamove, a hired van: the shop paid someone else's vehicle.
    THUE_NGOAI = "THUE_NGOAI"


#: `BUSINESS_TRUTH_INTAKE.md` §1: "đơn dưới 20kg đi xe máy; đơn từ đúng 20kg trở lên đi ô tô".
CAR_FROM_KG: Final = Decimal(20)

#: One trip's cost ceiling: ten million đồng. A figure above it is a typo, not a trip.
TRIP_COST_MAX_VND: Final = 10_000_000
#: Kilometres, one decimal, at most 999.9 -- `NUMERIC(6,1)`.
TRIP_KM_MAX: Final = Decimal("999.9")
NOTE_MAX: Final = 120

_KM_TEXT: Final = re.compile(r"^\d{1,3}(?:[.,]\d)?$")


class WeightBasis(StrEnum):
    #: Every line is priced by the kilogram and every weight was taken by staff.
    MEASURED = "MEASURED"
    #: Every line is priced by the kilogram but at least one weight is the customer's estimate.
    ESTIMATED = "ESTIMATED"
    #: A line is priced by the piece (a blanket, a pair of shoes) or there is no line: the weight
    #: of the bag is not known.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class WeighedLine:
    unit: str
    quantity: str
    customer_estimate: bool


@dataclass(frozen=True, slots=True)
class VehicleSuggestion:
    weight_kg: Decimal | None
    basis: WeightBasis
    vehicle: Vehicle | None


def suggest_vehicle(lines: tuple[WeighedLine, ...]) -> VehicleSuggestion:
    """The owner's vehicle rule applied to an order's quoted lines.

    The weight is the sum of the kilogram lines, in exact decimals. Any line priced by another unit
    makes the weight unknown, and an unknown weight suggests no vehicle.
    """
    if not lines or any(line.unit != "KG" for line in lines):
        return VehicleSuggestion(None, WeightBasis.UNKNOWN, None)
    total = Decimal(0)
    for line in lines:
        try:
            quantity = Decimal(line.quantity)
        except InvalidOperation:
            return VehicleSuggestion(None, WeightBasis.UNKNOWN, None)
        if not quantity.is_finite() or quantity < 0:
            return VehicleSuggestion(None, WeightBasis.UNKNOWN, None)
        total += quantity
    basis = (
        WeightBasis.ESTIMATED
        if any(line.customer_estimate for line in lines)
        else WeightBasis.MEASURED
    )
    return VehicleSuggestion(total, basis, Vehicle.O_TO if total >= CAR_FROM_KG else Vehicle.XE_MAY)


def trip_km(value: str | None) -> Decimal | None:
    """Kilometres as typed ("4,5" or "4.5"), one decimal at most, or `TRIP_KM_INVALID`."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not _KM_TEXT.fullmatch(text):
        raise ShopCaptureError("TRIP_KM_INVALID", "kilometres are a number with one decimal")
    km = Decimal(text.replace(",", "."))
    if km > TRIP_KM_MAX:
        raise ShopCaptureError("TRIP_KM_INVALID", "a trip is at most 999.9 km")
    return km


@dataclass(frozen=True, slots=True)
class TripCost:
    vehicle: Vehicle | None
    km: Decimal | None
    cost_vnd: int | None
    note: str | None

    @property
    def empty(self) -> bool:
        return self.vehicle is None and self.km is None and self.cost_vnd is None and not self.note


def trip_cost(
    *,
    vehicle: Vehicle | None,
    km: str | None,
    cost_vnd: int | None,
    note: str | None,
) -> TripCost:
    """One trip's cost as recorded. Every field is optional; what is given must be sane."""
    if cost_vnd is not None:
        require_non_negative_vnd(cost_vnd)
        if cost_vnd > TRIP_COST_MAX_VND:
            raise ShopCaptureError(
                "TRIP_COST_TOO_LARGE", "a trip costs at most 10.000.000 đồng; check the figure"
            )
    return TripCost(vehicle=vehicle, km=trip_km(km), cost_vnd=cost_vnd, note=capture_note(note))


# --- Sổ thu chi -------------------------------------------------------------------------------


class ExpenseCategory(StrEnum):
    DIEN = "DIEN"
    NUOC = "NUOC"
    HOA_CHAT = "HOA_CHAT"
    TUI_NHAN = "TUI_NHAN"
    LUONG = "LUONG"
    MAT_BANG = "MAT_BANG"
    SUA_CHUA = "SUA_CHUA"
    XANG_XE = "XANG_XE"
    KHAC = "KHAC"


#: `DEC-038`: a month's margin is shown only when its spending has all five.
CORE_MARGIN_CATEGORIES: Final = (
    ExpenseCategory.DIEN,
    ExpenseCategory.NUOC,
    ExpenseCategory.HOA_CHAT,
    ExpenseCategory.LUONG,
    ExpenseCategory.MAT_BANG,
)

#: One line's ceiling: a thousand million đồng. Rent for a year fits; a slipped zero does not.
EXPENSE_MAX_VND: Final = 1_000_000_000
#: How far back a line may be dated: a year and a day, so last year's same month can be completed.
EXPENSE_MAX_AGE_DAYS: Final = 366


@dataclass(frozen=True, slots=True)
class Expense:
    spent_on: date
    category: ExpenseCategory
    amount_vnd: int
    note: str | None


def expense(
    *, spent_on: date, category: ExpenseCategory, amount_vnd: int, note: str | None, today: date
) -> Expense:
    """One line of Sổ thu chi, or the reason it is refused. `today` is the shop's, passed in."""
    require_non_negative_vnd(amount_vnd)
    if amount_vnd == 0:
        raise ShopCaptureError("EXPENSE_AMOUNT_REQUIRED", "an expense is more than 0 đồng")
    if amount_vnd > EXPENSE_MAX_VND:
        raise ShopCaptureError(
            "EXPENSE_AMOUNT_TOO_LARGE", "one line is at most 1.000.000.000 đồng; check the figure"
        )
    if spent_on > today:
        raise ShopCaptureError("EXPENSE_DATE_IN_FUTURE", "an expense is dated today or earlier")
    if spent_on < today - timedelta(days=EXPENSE_MAX_AGE_DAYS):
        raise ShopCaptureError(
            "EXPENSE_DATE_TOO_OLD", "an expense is dated within the last 366 days"
        )
    return Expense(
        spent_on=spent_on, category=category, amount_vnd=amount_vnd, note=capture_note(note)
    )


def month_bounds(month: str) -> tuple[date, date]:
    """`YYYY-MM` as its first and last day, or `MONTH_INVALID`."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", month.strip())
    if match is None:
        raise ShopCaptureError("MONTH_INVALID", "a month is written YYYY-MM")
    year, number = int(match.group(1)), int(match.group(2))
    if not 2000 <= year <= 2999 or not 1 <= number <= 12:
        raise ShopCaptureError("MONTH_INVALID", "a month is written YYYY-MM")
    first = date(year, number, 1)
    following = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    return first, following - timedelta(days=1)


def months_touching(from_date: date, to_date: date) -> tuple[date, ...]:
    """The first day of every calendar month a window of days touches, oldest first."""
    months: list[date] = []
    cursor = date(from_date.year, from_date.month, 1)
    while cursor <= to_date:
        months.append(cursor)
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    return tuple(months)


class MarginStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class MonthMargin:
    status: MarginStatus
    #: The core categories with no line in the month, in `CORE_MARGIN_CATEGORIES` order.
    missing: tuple[ExpenseCategory, ...]
    #: `COMPLETE` only: the size of net takings minus spending, and whether it is money left (`IN`)
    #: or more spent than taken (`OUT`). Said as a direction, never as a minus sign.
    amount_vnd: int | None
    direction: str | None


def missing_core_categories(
    recorded_categories: frozenset[ExpenseCategory],
) -> tuple[ExpenseCategory, ...]:
    """The core categories a month has no line in, in `CORE_MARGIN_CATEGORIES` order."""
    return tuple(
        category for category in CORE_MARGIN_CATEGORIES if category not in recorded_categories
    )


def month_margin(
    *,
    recorded_categories: frozenset[ExpenseCategory],
    collected_vnd: int,
    refunded_vnd: int,
    spending_vnd: int,
) -> MonthMargin:
    """`FR-RPT-002` / `DEC-038`: the month's takings less its recorded spending, only when complete.

    The three amounts are PostgreSQL's sums, passed in. When any core category has no line the
    figure is not produced at all -- an `INCOMPLETE` month has no amount, so no reader can mistake
    the remainder of a price for what the shop kept.
    """
    for value in (collected_vnd, refunded_vnd, spending_vnd):
        require_non_negative_vnd(value)
    missing = missing_core_categories(recorded_categories)
    if missing:
        return MonthMargin(MarginStatus.INCOMPLETE, missing, None, None)
    remainder = collected_vnd - refunded_vnd - spending_vnd
    return MonthMargin(
        MarginStatus.COMPLETE,
        (),
        abs(remainder),
        "IN" if remainder >= 0 else "OUT",
    )


def per_order_vnd(total_vnd: int, orders: int) -> int | None:
    """A total spread over orders, rounded half up to the whole đồng; `None` over no orders."""
    require_non_negative_vnd(total_vnd)
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise ValueError("an order count is a non-negative integer")
    if orders == 0:
        return None
    return (2 * total_vnd + orders) // (2 * orders)


# --- notes --------------------------------------------------------------------------------------

#: Nine or more digits, allowing the spaces, dots and hyphens people type inside a phone number.
_PHONE_LIKE: Final = re.compile(r"\d(?:[\s.\-]?\d){8,}")


def capture_note(value: str | None) -> str | None:
    """A short free-text note about a cost, or the reason it is refused.

    A note that looks like a phone number is refused (`NOTE_LOOKS_LIKE_PHONE`): these notes are the
    shop's books, not a contact list, and a number typed here would escape every privacy rule the
    customer record keeps.
    """
    if value is None:
        return None
    note = " ".join(value.split())
    if not note:
        return None
    if len(note) > NOTE_MAX or _has_control(note):
        raise ShopCaptureError("NOTE_INVALID", f"a note is at most {NOTE_MAX} characters")
    if _PHONE_LIKE.search(note):
        raise ShopCaptureError("NOTE_LOOKS_LIKE_PHONE", "a note must not carry a phone number")
    return note


def _has_control(text: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


__all__ = [
    "CAR_FROM_KG",
    "CORE_MARGIN_CATEGORIES",
    "CYCLE_START_CATEGORIES",
    "EXPENSE_MAX_AGE_DAYS",
    "EXPENSE_MAX_VND",
    "NOTE_MAX",
    "TRIP_COST_MAX_VND",
    "CycleEffect",
    "Expense",
    "ExpenseCategory",
    "MachineCategory",
    "MarginStatus",
    "MonthMargin",
    "ShopCaptureError",
    "TripCost",
    "Vehicle",
    "VehicleSuggestion",
    "WeighedLine",
    "WeightBasis",
    "capture_note",
    "cycle_effect",
    "expense",
    "machine_code",
    "machine_name",
    "missing_core_categories",
    "month_bounds",
    "month_margin",
    "months_touching",
    "per_order_vnd",
    "starts_cycle",
    "suggest_vehicle",
    "trip_cost",
    "trip_km",
]
