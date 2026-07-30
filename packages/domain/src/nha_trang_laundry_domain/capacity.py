"""Deterministic R1 capacity advisory policy with zero automatic slot authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nha_trang_laundry_domain.catalog import PolicyOutcome


class CapacityAdvisory(StrEnum):
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CapacityResult:
    advisory: CapacityAdvisory
    requested_ready_at: datetime | None
    slot_confirmed: bool
    required_approval: str
    promised_ready_at_store: datetime | None
    policy_outcome: PolicyOutcome
    auto_confirmable_capacity_kg_per_day: int


def evaluate_r1_capacity(*, requested_ready_at: datetime | None) -> CapacityResult:
    """Return a read-only advisory; R1 never creates a reservation or exact promise."""
    if requested_ready_at is not None and (
        requested_ready_at.tzinfo is None or requested_ready_at.utcoffset() is None
    ):
        raise ValueError("requested ready time must be timezone-aware")
    return CapacityResult(
        advisory=CapacityAdvisory.UNKNOWN,
        requested_ready_at=requested_ready_at,
        slot_confirmed=False,
        required_approval="SLOT_CONFIRMATION",
        promised_ready_at_store=None,
        policy_outcome=PolicyOutcome.REQUIRE_HUMAN,
        auto_confirmable_capacity_kg_per_day=0,
    )


__all__ = ["CapacityAdvisory", "CapacityResult", "evaluate_r1_capacity"]
