from datetime import datetime

import pytest
from nha_trang_laundry_domain.capacity import CapacityAdvisory, evaluate_r1_capacity
from nha_trang_laundry_domain.catalog import PolicyOutcome


def test_r1_capacity_is_advisory_only_and_requires_slot_confirmation() -> None:
    result = evaluate_r1_capacity(
        requested_ready_at=datetime.fromisoformat("2026-08-01T18:00:00+07:00")
    )

    assert result.advisory is CapacityAdvisory.UNKNOWN
    assert result.slot_confirmed is False
    assert result.required_approval == "SLOT_CONFIRMATION"
    assert result.promised_ready_at_store is None
    assert result.policy_outcome is PolicyOutcome.REQUIRE_HUMAN
    assert result.auto_confirmable_capacity_kg_per_day == 0


def test_r1_capacity_rejects_naive_requested_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_r1_capacity(requested_ready_at=datetime(2026, 8, 1, 18))
