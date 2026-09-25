from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nha_trang_laundry_domain.approvals import (
    APPROVAL_POLICIES,
    APPROVAL_RESOURCE_TYPES,
    BUSINESS_TIMEZONE,
    ApprovalEnvelopeError,
    ApprovalWindow,
    ImmutableApprovalEnvelope,
    approval_expires_at,
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


def _at(requested_at: datetime, action: ApprovalAction) -> datetime:
    return build_approval_envelope(
        approval_request_id=REQUEST_ID,
        action=action,
        resource_type=APPROVAL_RESOURCE_TYPES[action],
        resource_id=RESOURCE_ID,
        resource_version=1,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        policy_version="approval-policy-v1",
        requested_by=REQUESTER_ID,
        requested_at=requested_at,
    ).data.expires_at


@pytest.mark.parametrize(
    ("requested_at", "expires_at"),
    [
        # 10:00 Thursday in Nha Trang -> midnight at the end of Friday (17:00 UTC Friday).
        (datetime(2026, 9, 24, 3, 0, tzinfo=UTC), datetime(2026, 9, 25, 17, 0, tzinfo=UTC)),
        # 23:59 Thursday local -> still the end of Friday: 24 hours and a minute.
        (datetime(2026, 9, 24, 16, 59, tzinfo=UTC), datetime(2026, 9, 25, 17, 0, tzinfo=UTC)),
        # 00:00 Friday local is Friday's business day -> the end of Saturday: the 48-hour bound.
        (datetime(2026, 9, 24, 17, 0, tzinfo=UTC), datetime(2026, 9, 26, 17, 0, tzinfo=UTC)),
        # 06:00 Friday local is still Friday there although it is Thursday in UTC.
        (datetime(2026, 9, 24, 23, 0, tzinfo=UTC), datetime(2026, 9, 26, 17, 0, tzinfo=UTC)),
    ],
)
def test_an_owner_remedy_envelope_stays_open_until_the_end_of_the_next_business_day(
    requested_at: datetime, expires_at: datetime
) -> None:
    """The DEC-031 addendum: an owner-only remedy envelope is not a counter-time decision.

    Since `DEC-031` every loss and every compensation on a refunded order needs one, and a
    ten-minute window meant an owner who was out let the proposal die. It now closes at midnight in
    Asia/Ho_Chi_Minh at the end of the business day after the one it was raised on. The owner, MFA
    and separation of duty are unchanged; only the time to answer is longer.
    """

    result = build_approval_envelope(
        approval_request_id=REQUEST_ID,
        action=ApprovalAction.APPROVE_REMEDY,
        resource_type=APPROVAL_RESOURCE_TYPES[ApprovalAction.APPROVE_REMEDY],
        resource_id=RESOURCE_ID,
        resource_version=1,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        policy_version="approval-policy-v1",
        requested_by=REQUESTER_ID,
        requested_at=requested_at,
    )
    assert result.data.expires_at == expires_at
    assert result.data.required_role is ActorRole.OWNER_ADMIN
    assert result.data.obligations == (
        "MFA_REQUIRED",
        "SEPARATION_OF_DUTY",
        "RECHECK_RESOURCE_VERSION",
    )
    assert result.data.execution_capability == "OWNER_APPROVED_ACTION"
    # The instant is written into the hashed envelope, in UTC, like every other.
    assert b'"expires_at":"' + expires_at.strftime("%Y-%m-%dT%H:%M:%S.000000Z").encode() in (
        result.document.canonical_json
    )


def test_counter_time_and_other_owner_actions_keep_their_short_windows() -> None:
    """The longer window is the remedy action's alone. Every other action is exactly as it was."""

    requested_at = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    expected = {
        ApprovalAction.PRESENT_QUOTE: timedelta(minutes=30),
        ApprovalAction.FINALIZE_QUOTE: timedelta(minutes=30),
        ApprovalAction.SEND_MESSAGE: timedelta(minutes=30),
        ApprovalAction.CONFIRM_SLOT: timedelta(minutes=15),
        ApprovalAction.ACCEPT_ORDER: timedelta(minutes=15),
        ApprovalAction.SET_RANGE_PRICE: timedelta(minutes=30),
        ApprovalAction.SET_DELIVERY_FEE: timedelta(minutes=10),
        ApprovalAction.APPLY_PROMOTION: timedelta(minutes=10),
        ApprovalAction.CANCEL_ACTIVE_ORDER: timedelta(minutes=10),
        ApprovalAction.APPROVE_B2B_TERMS: timedelta(minutes=10),
        ApprovalAction.PUBLISH_POLICY: timedelta(minutes=10),
        ApprovalAction.EXPORT_SANITIZED_DATA: timedelta(minutes=10),
    }
    assert set(expected) | {ApprovalAction.APPROVE_REMEDY} == set(APPROVAL_POLICIES)
    for action, ttl in expected.items():
        assert APPROVAL_POLICIES[action].window is ApprovalWindow.FIXED, action
        assert _at(requested_at, action) == requested_at + ttl, action
    assert APPROVAL_POLICIES[ApprovalAction.APPROVE_REMEDY].window is (
        ApprovalWindow.END_OF_NEXT_BUSINESS_DAY
    )


@given(
    requested_at=st.datetimes(
        min_value=datetime(2026, 1, 1),  # hypothesis bounds are naive
        max_value=datetime(2035, 12, 31),
        timezones=st.just(UTC),
    )
)
@settings(max_examples=300, deadline=None)
def test_the_remedy_window_is_always_between_one_and_two_days_and_ends_at_local_midnight(
    requested_at: datetime,
) -> None:
    expires_at = _at(requested_at, ApprovalAction.APPROVE_REMEDY)
    assert timedelta(hours=24) < expires_at - requested_at <= timedelta(hours=48)
    local = expires_at.astimezone(BUSINESS_TIMEZONE)
    assert (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0)
    assert local.date() == requested_at.astimezone(BUSINESS_TIMEZONE).date() + timedelta(days=2)


def test_the_window_is_a_pure_function_of_the_request_instant() -> None:
    """Reproducible from the recorded `requested_at` alone, with a naive instant refused."""

    policy = APPROVAL_POLICIES[ApprovalAction.APPROVE_REMEDY]
    requested_at = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    assert approval_expires_at(policy, requested_at) == approval_expires_at(policy, requested_at)
    with pytest.raises(ApprovalEnvelopeError):
        approval_expires_at(policy, datetime(2026, 9, 24, 3, 0))


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
