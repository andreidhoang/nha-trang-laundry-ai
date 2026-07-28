from datetime import UTC, datetime, timedelta

import pytest
from nha_trang_laundry_domain.consent import (
    ConsentPolicyError,
    OptOutDisposition,
    OptOutRegistry,
    SuppressionState,
    evaluate_opt_out,
    marketing_send_allowed,
    normalize_provider_command,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def registry(
    *, expires_at: datetime | None = None, patterns: tuple[str, ...] | None = None
) -> OptOutRegistry:
    return OptOutRegistry(
        "opt-out-v1",
        ("STOP", "DỪNG"),
        patterns or (r"không\s+nhắn\s+nữa[.!]?",),
        NOW - timedelta(days=1),
        expires_at or NOW + timedelta(days=1),
    )


@pytest.mark.parametrize("message", ["STOP", " stop ", "/STOP", "DỪNG", "dừng"])
def test_exact_stop_variants_withdraw_before_model(message: str) -> None:
    assert evaluate_opt_out(message, registry=registry(), now=NOW) is OptOutDisposition.WITHDRAW


def test_ambiguous_opt_out_blocks_pending_human_review() -> None:
    assert (
        evaluate_opt_out("Không   nhắn nữa!", registry=registry(), now=NOW)
        is OptOutDisposition.PENDING_REVIEW_BLOCKED
    )


def test_missing_or_stale_registry_fails_closed_for_marketing() -> None:
    assert (
        evaluate_opt_out("hello", registry=None, now=NOW)
        is OptOutDisposition.POLICY_UNAVAILABLE_BLOCKED
    )
    assert (
        evaluate_opt_out("hello", registry=registry(expires_at=NOW - timedelta(seconds=1)), now=NOW)
        is OptOutDisposition.POLICY_UNAVAILABLE_BLOCKED
    )


def test_invalid_published_regex_is_rejected_not_model_interpreted() -> None:
    with pytest.raises(ConsentPolicyError, match="pattern is invalid"):
        evaluate_opt_out("hello", registry=registry(patterns=("(",)), now=NOW)


def test_marketing_requires_active_consent_and_clear_suppression() -> None:
    assert marketing_send_allowed(consent_active=True, suppression=SuppressionState.CLEAR)
    for suppression in (
        SuppressionState.SUPPRESSED,
        SuppressionState.PENDING_REVIEW_BLOCKED,
        SuppressionState.UNKNOWN_BLOCKED,
    ):
        assert not marketing_send_allowed(consent_active=True, suppression=suppression)
    assert not marketing_send_allowed(consent_active=False, suppression=SuppressionState.CLEAR)


def test_provider_command_normalization_does_not_fuzz_semantics() -> None:
    assert normalize_provider_command(" /DỪNG \n") == "dừng"
    assert normalize_provider_command("STOP PLEASE") == "stop please"
