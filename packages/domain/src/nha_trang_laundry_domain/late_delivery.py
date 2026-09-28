"""How late a delivery was, measured from the record (`LATE-CREDIT-002`, `DEC-042`).

`DEC-004` gives a 10% credit on the next bill for a delivery more than two hours late through the
shop's fault. Until `DEC-042` a person had to notice the lateness and type the minutes. A delivery
order now carries a promise (`DEC-037`) and a recorded arrival (the succeeded `RETURN` leg), so the
lateness is a fact this module measures. It is pure: every instant comes in as an argument, and the
same inputs give the same answer on any machine, on any day.

**The deadline** is the first promise (`orders.promised_ready_at`, immutable). Only a *Hẹn lại*
made because the customer asked (`CUSTOMER_REQUEST`) moves it, to that change's new time; when the
customer asked more than once, the newest such change wins. Every other reason -- a machine, the
workload, drying weather, extra treatment, other -- is on the shop's side and does not move it:
otherwise re-promising would quietly cancel the customer's credit.

A change recorded **after** the laundry arrived is ignored. Nothing legitimate produces one (the
order route refuses a *Hẹn lại* once the laundry is ready), and one that did would be a way to move
a deadline past a delivery that already happened -- the exact manipulation `DEC-042` rules out.

**The arrival** is the earliest succeeded `RETURN` leg (`0033` allows only one, so "earliest" is a
guard, not a choice). A `PICKUP` leg is the shop collecting laundry and is never an arrival.

**Minutes late** are whole minutes, floored, and never negative: 2 h 0 min 59 s late is 120
minutes, which is not *more than* a 120-minute threshold.

**Failed attempts before the deadline** are the failed `RETURN` legs recorded at or before the
deadline. They point to the customer's side ("Giao 14:05 không gặp khách") and are shown beside the
lateness; they decide nothing here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.promise import PromiseChangeReason

#: `DEC-042`, cited by every refusal and every decision this module feeds.
LATE_DELIVERY_DECISION_REF: Final = "DEC-042"

_MINUTE: Final = timedelta(minutes=1)


class DeadlineBasis(StrEnum):
    """Where the deadline came from."""

    #: The first promise, set at *Nhận đồ* and never changed.
    FIRST_PROMISE = "FIRST_PROMISE"
    #: The newest *Hẹn lại* the customer asked for.
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"


class LateDeliveryDecision(StrEnum):
    """What a person decided about one measured late delivery (`late_delivery_decisions`)."""

    STORE_FAULT_CREDITED = "STORE_FAULT_CREDITED"
    NOT_STORE_FAULT = "NOT_STORE_FAULT"


class NotStoreFaultReason(StrEnum):
    """Why the lateness was not the shop's fault. `OTHER` needs a few words."""

    CUSTOMER_ABSENT = "CUSTOMER_ABSENT"
    CUSTOMER_WRONG_ADDRESS = "CUSTOMER_WRONG_ADDRESS"
    CUSTOMER_ASKED_LATER = "CUSTOMER_ASKED_LATER"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class PromiseMove:
    """One *Hẹn lại* as `order_promise_changes` records it."""

    new_promise_at: datetime
    reason: PromiseChangeReason
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class LegAttempt:
    """One delivery leg as `delivery_legs` records it (`PICKUP`/`RETURN`, `SUCCEEDED`/`FAILED`)."""

    leg_kind: str
    outcome: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class LateDeliveryClock:
    """The measurement. Every instant is the stored one (UTC-aware); the console shows it in
    Asia/Ho_Chi_Minh."""

    deadline: datetime
    deadline_basis: DeadlineBasis
    #: The earliest succeeded `RETURN` leg; `None` while the laundry has not arrived.
    delivered_at: datetime | None
    #: Whole minutes from the deadline to the arrival, floored, never negative; `None` while the
    #: laundry has not arrived.
    late_by_minutes: int | None
    #: The failed `RETURN` attempts recorded at or before the deadline, oldest first.
    failed_attempts_before_deadline: tuple[datetime, ...]

    def is_late_beyond(self, threshold_minutes: int) -> bool:
        """`DEC-004`'s test: late by **more than** the published threshold."""

        return self.late_by_minutes is not None and self.late_by_minutes > threshold_minutes


def late_delivery_clock(
    first_promise: datetime,
    changes: Iterable[PromiseMove],
    legs: Iterable[LegAttempt],
) -> LateDeliveryClock:
    """Measure one delivery against the deadline `DEC-042` defines. Pure and deterministic.

    Raises `ValueError` for a naive instant: a time with no zone cannot be compared with a stored
    one without guessing which zone it meant.
    """

    _require_aware(first_promise)
    moves = tuple(changes)
    attempts = tuple(legs)
    for move in moves:
        _require_aware(move.new_promise_at)
        _require_aware(move.changed_at)
    for attempt in attempts:
        _require_aware(attempt.recorded_at)

    arrivals = sorted(
        attempt.recorded_at
        for attempt in attempts
        if attempt.leg_kind == "RETURN" and attempt.outcome == "SUCCEEDED"
    )
    delivered_at = arrivals[0] if arrivals else None

    # The newest customer-requested move made before the laundry arrived. Ties on the instant are
    # broken by the later position in the ledger's own order, which is how the caller supplies it.
    deadline, basis = first_promise, DeadlineBasis.FIRST_PROMISE
    newest: datetime | None = None
    for move in moves:
        if move.reason is not PromiseChangeReason.CUSTOMER_REQUEST:
            continue
        if delivered_at is not None and move.changed_at > delivered_at:
            continue
        if newest is None or move.changed_at >= newest:
            newest = move.changed_at
            deadline, basis = move.new_promise_at, DeadlineBasis.CUSTOMER_REQUEST

    late_by: int | None = None
    if delivered_at is not None:
        late_by = max(0, (delivered_at - deadline) // _MINUTE)

    failed = tuple(
        sorted(
            attempt.recorded_at
            for attempt in attempts
            if attempt.leg_kind == "RETURN"
            and attempt.outcome == "FAILED"
            and attempt.recorded_at <= deadline
        )
    )
    return LateDeliveryClock(
        deadline=deadline,
        deadline_basis=basis,
        delivered_at=delivered_at,
        late_by_minutes=late_by,
        failed_attempts_before_deadline=failed,
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("late-delivery instants must be timezone-aware")


__all__ = [
    "LATE_DELIVERY_DECISION_REF",
    "DeadlineBasis",
    "LateDeliveryClock",
    "LateDeliveryDecision",
    "LegAttempt",
    "NotStoreFaultReason",
    "PromiseMove",
    "late_delivery_clock",
]
