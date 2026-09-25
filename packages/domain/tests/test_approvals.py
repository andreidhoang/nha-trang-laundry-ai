from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from nha_trang_laundry_domain.approvals import (
    APPROVAL_RESOURCE_TYPES,
    ApprovalEnvelopeError,
    ImmutableApprovalEnvelope,
    build_approval_envelope,
)
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000301")
RESOURCE_ID = UUID("00000000-0000-0000-0000-000000000302")
REQUESTER_ID = UUID("00000000-0000-0000-0000-000000000303")
NOW = datetime(2026, 8, 1, tzinfo=UTC)
HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64


def envelope(
    action: ApprovalAction = ApprovalAction.SEND_MESSAGE,
) -> ImmutableApprovalEnvelope:
    return build_approval_envelope(
        approval_request_id=REQUEST_ID,
        action=action,
        resource_type=APPROVAL_RESOURCE_TYPES[action],
        resource_id=RESOURCE_ID,
        resource_version=7,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        policy_version="approval-policy-v1",
        requested_by=REQUESTER_ID,
        requested_at=NOW,
    )


def test_server_derives_role_ttl_reasons_and_obligations() -> None:
    result = envelope()

    assert result.data.required_role is ActorRole.OPS_APPROVER
    assert result.data.expires_at == NOW + timedelta(minutes=30)
    assert result.data.reason_codes == ("CUSTOMER_FACING_COMMITMENT",)
    assert result.data.obligations == (
        "RECHECK_RESOURCE_VERSION",
        "RECHECK_POLICY_AT_EXECUTION",
    )
    assert result.document.snapshot_hash.startswith("JCS-SHA256-V1:")


def test_financial_and_policy_actions_require_owner_and_short_ttl() -> None:
    # `SET_RANGE_PRICE` left this list on 2026-09-25 under `DEC-029` (option B): choosing inside a
    # band the owner published is the counter attestation `DEC-021` uses, pinned by the test below.
    # Every action still listed here keeps the owner, MFA and the ten-minute window.
    for action in (
        ApprovalAction.APPLY_PROMOTION,
        ApprovalAction.CANCEL_ACTIVE_ORDER,
        ApprovalAction.PUBLISH_POLICY,
    ):
        result = envelope(action)
        assert result.data.required_role is ActorRole.OWNER_ADMIN
        assert result.data.expires_at == NOW + timedelta(minutes=10)
        assert "SEPARATION_OF_DUTY" in result.data.obligations


def test_a_price_inside_a_published_band_is_a_counter_attestation() -> None:
    """`DEC-029`, option B: the staff member on duty chooses, under `DEC-021`'s attestation.

    Same policy as finalising a quote -- `OPERATOR`, thirty minutes, no separation of duty and no
    step-up MFA -- because the band the owner published is the authorisation and choosing inside it
    is using it. Reversal is the one line in `APPROVAL_POLICIES`, and this test is what fails then.
    """

    result = envelope(ApprovalAction.SET_RANGE_PRICE)
    finalize = envelope(ApprovalAction.FINALIZE_QUOTE)
    assert result.data.required_role is ActorRole.OPERATOR
    assert result.data.expires_at == NOW + timedelta(minutes=30)
    assert result.data.execution_capability == "STAFF_ATTESTED_ACTION"
    assert "SEPARATION_OF_DUTY" not in result.data.obligations
    assert "MFA_REQUIRED" not in result.data.obligations
    assert (
        result.data.required_role,
        result.data.obligations,
        result.data.execution_capability,
    ) == (
        finalize.data.required_role,
        finalize.data.obligations,
        finalize.data.execution_capability,
    )


def test_any_bound_hash_or_version_edit_changes_approval_hash() -> None:
    original = envelope()
    edited = build_approval_envelope(
        approval_request_id=REQUEST_ID,
        action=ApprovalAction.SEND_MESSAGE,
        resource_type="MESSAGE_DRAFT",
        resource_id=RESOURCE_ID,
        resource_version=8,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        policy_version="approval-policy-v1",
        requested_by=REQUESTER_ID,
        requested_at=NOW,
    )
    assert edited.document.snapshot_hash != original.document.snapshot_hash


@pytest.mark.parametrize(
    ("snapshot_hash", "rendered_hash", "resource_version"),
    [("sha256:" + "a" * 64, HASH_B, 7), (HASH_A, "", 7), (HASH_A, HASH_B, 0)],
)
def test_invalid_approval_bindings_fail_closed(
    snapshot_hash: str, rendered_hash: str, resource_version: int
) -> None:
    with pytest.raises(ApprovalEnvelopeError, match="VALIDATION_ERROR"):
        build_approval_envelope(
            approval_request_id=REQUEST_ID,
            action=ApprovalAction.PRESENT_QUOTE,
            resource_type="QUOTE_REVISION",
            resource_id=RESOURCE_ID,
            resource_version=resource_version,
            snapshot_hash=snapshot_hash,
            rendered_hash=rendered_hash,
            policy_version="approval-policy-v1",
            requested_by=REQUESTER_ID,
            requested_at=NOW,
        )
