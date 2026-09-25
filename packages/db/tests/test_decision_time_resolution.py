"""`API-INTEGRITY-003`: at decision time the server resolves the resource an approval binds, again.

`ApprovalRepository.decide` and `attest` used to call `_require_exact_binding` and stop there. That
compares what the approver's client sent with the envelope the server stored, which proves the
client saw the envelope -- and nothing about whether the quote, the draft, the order or the export
behind it is still the content the envelope describes. Every refusal test below is built the same
way, because that is the shape of the defect: raise an envelope over real content, change the
content, then have the approver submit the stored binding *exactly*. The old code approved every one
of them.

The explicit half matters as much. Four resource types name capabilities this system has not built,
and for those the decision keeps precisely the check it had; a resource type in neither list is
refused outright rather than falling through a missing dictionary entry.

`API-INTEGRITY-004` applies the same re-resolution to the worker's execution claim; its tests are
the last section of this module.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import current_binding, seed_message_draft
from nha_trang_laundry_db.approvals import (
    DECISION_TIME_RESOLVERS,
    RESOURCE_CHANGED_SINCE_REQUEST,
    UNRESOLVABLE_AT_DECISION,
    ApprovalAttestationCommand,
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalExecutionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalResourceChangedError,
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    ActorRole,
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
)
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot
from quote_test_data import PRICED_AT, accepted_quote, ensure_store, make_quote_snapshot

NOW = datetime.now(UTC)
RENDERED = "JCS-SHA256-V1:" + "e" * 64
HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64


# --- The table is complete, and says which types keep the old check -----------------------------


def test_every_resource_type_is_either_resolved_or_declared_unresolvable() -> None:
    """A new resource type has to be placed. Nothing lands in the gap between the two tables."""

    every = set(APPROVAL_RESOURCE_TYPES.values())
    resolved = set(DECISION_TIME_RESOLVERS)
    assert resolved.isdisjoint(UNRESOLVABLE_AT_DECISION)
    assert resolved | UNRESOLVABLE_AT_DECISION == every
    # The types the server CAN resolve are all resolved. Each of these has a table or a server
    # rendering, and leaving one in the unresolvable set would quietly keep the echo-only check.
    assert resolved == {
        "MESSAGE_DRAFT",
        "EXPORT_REQUEST",
        "QUOTE_REVISION",
        "ORDER",
        "REMEDY_PROPOSAL",
    }


# --- Against PostgreSQL --------------------------------------------------------------------------


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        apply_migrations(established)
        yield established


def _member(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    staff = StaffPrincipal(uuid4(), f"resolve-{uuid4().hex}", frozenset({role}), True, uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (staff.staff_user_id, staff.oidc_subject, NOW),
        )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff.staff_user_id, store_id, staff.staff_user_id, NOW),
        )
    return staff


def _shop(connection: Any) -> UUID:
    store_id = uuid4()
    ensure_store(connection, store_id)
    return store_id


def _raise(
    connection: Any,
    *,
    action: ApprovalAction,
    store_id: UUID,
    resource_id: UUID,
    version: int,
    snapshot_hash: str,
    rendered_hash: str,
    requested_by: StaffPrincipal,
) -> UUID:
    return (
        ApprovalRepository()
        .request(
            connection,
            ApprovalRequestCommand(
                action,
                APPROVAL_RESOURCE_TYPES[action],
                resource_id,
                version,
                snapshot_hash,
                rendered_hash,
                "decision-time-resolution-v1",
                requested_by.staff_user_id,
                f"raise-{uuid4().hex}",
                uuid4(),
                store_id=store_id,
            ),
        )
        .approval_request_id
    )


def _decide(
    connection: Any,
    approval_id: UUID,
    *,
    version: int,
    snapshot_hash: str,
    rendered_hash: str,
    by: StaffPrincipal,
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
) -> Any:
    return ApprovalRepository().decide(
        connection,
        ApprovalDecisionCommand(
            approval_id,
            decision,
            version,
            snapshot_hash,
            rendered_hash,
            "HUMAN_REVIEW_COMPLETE",
            by,
            uuid4(),
        ),
    )


def _nothing_decided(connection: Any, approval_id: UUID) -> None:
    """A refused decision writes nothing: no decision row, no event, no audit, no outbox."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.status,
                   (SELECT count(*) FROM approval_decisions d WHERE d.approval_request_id = %s),
                   (SELECT count(*) FROM domain_events e
                     WHERE e.aggregate_id = %s AND e.event_type = 'APPROVAL_DECIDED'),
                   (SELECT count(*) FROM audit_events a
                     WHERE a.aggregate_id = %s AND a.action = 'APPROVAL_DECIDE'),
                   (SELECT count(*) FROM outbox_events o WHERE o.idempotency_key = %s)
            FROM approval_request_states s WHERE s.approval_request_id = %s
            """,
            (
                approval_id,
                approval_id,
                approval_id,
                f"approval:{approval_id}:decision",
                approval_id,
            ),
        )
        assert cursor.fetchone() == ("REQUESTED", 0, 0, 0, 0)


def _quote(connection: Any, store_id: UUID, by: StaffPrincipal) -> tuple[UUID, UUID, Any]:
    quote_id, order_request_id = uuid4(), uuid4()
    first = make_quote_snapshot(quote_id, 1, 100_000)
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, order_request_id, first, 0, 0, by.staff_user_id, uuid4(), PRICED_AT
        ),
    )
    return quote_id, order_request_id, first


def _reprice(
    connection: Any, store_id: UUID, quote_id: UUID, order_request_id: UUID, by: StaffPrincipal
) -> ImmutableQuoteSnapshot:
    second = make_quote_snapshot(quote_id, 2, 150_000)
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, order_request_id, second, 1, 1, by.staff_user_id, uuid4(), PRICED_AT
        ),
    )
    return second


# --- QUOTE_REVISION ------------------------------------------------------------------------------


def test_a_quote_re_priced_after_the_envelope_cannot_be_approved_with_the_stored_hashes(
    connection: Any,
) -> None:
    """The item's own example: the approver echoes the stored binding exactly, and the old code
    approved it.

    What the approver would have authorised is revision 1 at 100.000 đ, of a quote that since offers
    150.000 đ. Nothing downstream of the decision can repair that record.
    """

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    quote_id, order_request_id, first = _quote(connection, store_id, operator)
    approval_id = _raise(
        connection,
        action=ApprovalAction.PRESENT_QUOTE,
        store_id=store_id,
        resource_id=quote_id,
        version=1,
        snapshot_hash=first.document.snapshot_hash,
        rendered_hash=RENDERED,
        requested_by=operator,
    )
    _reprice(connection, store_id, quote_id, order_request_id, operator)

    with pytest.raises(ApprovalResourceChangedError) as refused:
        _decide(
            connection,
            approval_id,
            version=1,
            snapshot_hash=first.document.snapshot_hash,
            rendered_hash=RENDERED,
            by=approver,
        )
    assert refused.value.reason_code == RESOURCE_CHANGED_SINCE_REQUEST
    assert str(refused.value).startswith(f"{RESOURCE_CHANGED_SINCE_REQUEST}:")
    # Still an `ApprovalStateError`, so every route that already refuses a stale envelope refuses
    # this one the same way (409), without learning a new type.
    assert isinstance(refused.value, ApprovalStateError)
    _nothing_decided(connection, approval_id)


def test_an_unchanged_quote_is_still_approved(connection: Any) -> None:
    """The control: the check must refuse a change, not every quote envelope."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    quote_id, _, first = _quote(connection, store_id, operator)
    approval_id = _raise(
        connection,
        action=ApprovalAction.PRESENT_QUOTE,
        store_id=store_id,
        resource_id=quote_id,
        version=1,
        snapshot_hash=first.document.snapshot_hash,
        rendered_hash=RENDERED,
        requested_by=operator,
    )
    decided = _decide(
        connection,
        approval_id,
        version=1,
        snapshot_hash=first.document.snapshot_hash,
        rendered_hash=RENDERED,
        by=approver,
    )
    assert decided.status == "APPROVED"


def test_a_stale_envelope_can_still_be_rejected(connection: Any) -> None:
    """A rejection authorises nothing, so the owner can clear a stale envelope from the queue."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    quote_id, order_request_id, first = _quote(connection, store_id, operator)
    approval_id = _raise(
        connection,
        action=ApprovalAction.PRESENT_QUOTE,
        store_id=store_id,
        resource_id=quote_id,
        version=1,
        snapshot_hash=first.document.snapshot_hash,
        rendered_hash=RENDERED,
        requested_by=operator,
    )
    _reprice(connection, store_id, quote_id, order_request_id, operator)
    rejected = _decide(
        connection,
        approval_id,
        version=1,
        snapshot_hash=first.document.snapshot_hash,
        rendered_hash=RENDERED,
        by=approver,
        decision=ApprovalDecision.REJECTED,
    )
    assert rejected.status == "REJECTED"


def test_a_counter_attestation_over_a_re_priced_quote_is_refused_too(connection: Any) -> None:
    """`attest` had the same echo-only check as `decide` (`DEC-029`'s one-person door)."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    quote_id, order_request_id, first = _quote(connection, store_id, operator)

    def raise_and_attest() -> Any:
        approval_id = _raise(
            connection,
            action=ApprovalAction.SET_RANGE_PRICE,
            store_id=store_id,
            resource_id=quote_id,
            version=1,
            snapshot_hash=first.document.snapshot_hash,
            rendered_hash=RENDERED,
            requested_by=operator,
        )
        return approval_id, ApprovalRepository().attest(
            connection,
            ApprovalAttestationCommand(
                approval_request_id=approval_id,
                observed_resource_version=1,
                observed_snapshot_hash=first.document.snapshot_hash,
                observed_rendered_hash=RENDERED,
                reason_code="RANGE_PRICE_COUNTER_ATTESTED",
                principal=operator,
                correlation_id=uuid4(),
            ),
        )

    _, attested = raise_and_attest()
    assert attested.status == "APPROVED"

    stale_id = _raise(
        connection,
        action=ApprovalAction.SET_RANGE_PRICE,
        store_id=store_id,
        resource_id=quote_id,
        version=1,
        snapshot_hash=first.document.snapshot_hash,
        rendered_hash=RENDERED,
        requested_by=operator,
    )
    _reprice(connection, store_id, quote_id, order_request_id, operator)
    with pytest.raises(ApprovalResourceChangedError):
        ApprovalRepository().attest(
            connection,
            ApprovalAttestationCommand(
                approval_request_id=stale_id,
                observed_resource_version=1,
                observed_snapshot_hash=first.document.snapshot_hash,
                observed_rendered_hash=RENDERED,
                reason_code="RANGE_PRICE_COUNTER_ATTESTED",
                principal=operator,
                correlation_id=uuid4(),
            ),
        )
    _nothing_decided(connection, stale_id)


def test_an_envelope_over_a_quote_that_never_existed_cannot_be_approved(connection: Any) -> None:
    """Request time cannot resolve a `QUOTE_REVISION` (the approval precedes the revision it
    authorises, migration `0029`), so an envelope over any UUID with typed digests was accepted --
    and, until now, approvable. By the decision the revision it names must exist."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    approval_id = _raise(
        connection,
        action=ApprovalAction.PRESENT_QUOTE,
        store_id=store_id,
        resource_id=uuid4(),
        version=1,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        requested_by=operator,
    )
    with pytest.raises(ApprovalResourceChangedError):
        _decide(
            connection,
            approval_id,
            version=1,
            snapshot_hash=HASH_A,
            rendered_hash=HASH_B,
            by=approver,
        )
    _nothing_decided(connection, approval_id)


# --- MESSAGE_DRAFT -------------------------------------------------------------------------------


@pytest.mark.parametrize("review", ["EDIT", "REJECT"])
def test_a_draft_reviewed_after_the_envelope_cannot_be_approved(
    connection: Any, review: str
) -> None:
    """An EDIT moves the draft to revision 2; a REJECT leaves nothing sendable. Either way the
    envelope over revision 1 describes words that are no longer the draft."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    reviewer = _member(connection, store_id, StaffRole.OPS_APPROVER)
    draft = seed_message_draft(connection, store_id)
    binding = current_binding(connection, draft.agent_run_id)
    approval_id = _raise(
        connection,
        action=ApprovalAction.SEND_MESSAGE,
        store_id=store_id,
        resource_id=draft.agent_run_id,
        version=binding.resource_version,
        snapshot_hash=binding.snapshot_hash,
        rendered_hash=binding.rendered_hash,
        requested_by=operator,
    )
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision=review,
        principal=reviewer,
        correlation_id=uuid4(),
        reason_code="REVIEWER_CHANGED_IT" if review == "REJECT" else None,
        edited_text="Dạ, đồ của anh/chị xong rồi ạ." if review == "EDIT" else None,
    )

    with pytest.raises(ApprovalResourceChangedError):
        _decide(
            connection,
            approval_id,
            version=binding.resource_version,
            snapshot_hash=binding.snapshot_hash,
            rendered_hash=binding.rendered_hash,
            by=approver,
        )
    _nothing_decided(connection, approval_id)


# --- ORDER ---------------------------------------------------------------------------------------


def test_an_order_that_moved_on_after_the_envelope_cannot_be_approved(connection: Any) -> None:
    """Every transition bumps the order's row version, and the envelope binds the version the
    requester saw. Cancelling an order in a state the owner was never shown is a different act."""

    store_id = _shop(connection)
    owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    second_owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=owner
    )
    order = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            owner,
            f"order-{uuid4().hex}",
            uuid4(),
            NOW,
            AcquisitionSource.WALK_IN,
        ),
    )

    def envelope() -> UUID:
        return _raise(
            connection,
            action=ApprovalAction.CANCEL_ACTIVE_ORDER,
            store_id=store_id,
            resource_id=order.order_id,
            version=1,
            snapshot_hash=quote.document.snapshot_hash,
            rendered_hash=RENDERED,
            requested_by=owner,
        )

    current, stale = envelope(), envelope()
    approved = _decide(
        connection,
        current,
        version=1,
        snapshot_hash=quote.document.snapshot_hash,
        rendered_hash=RENDERED,
        by=second_owner,
    )
    assert approved.status == "APPROVED"

    OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            order.order_id,
            1,
            owner,
            f"transition-{uuid4().hex}",
            uuid4(),
            commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
            occurred_at=NOW,
        ),
    )
    with pytest.raises(ApprovalResourceChangedError):
        _decide(
            connection,
            stale,
            version=1,
            snapshot_hash=quote.document.snapshot_hash,
            rendered_hash=RENDERED,
            by=second_owner,
        )
    _nothing_decided(connection, stale)


# --- REMEDY_PROPOSAL -----------------------------------------------------------------------------


def test_an_owner_remedy_envelope_over_no_proposal_cannot_be_approved(connection: Any) -> None:
    """Like a quote, a remedy proposal is written after its envelope, so request time cannot see
    it. An `APPROVE_REMEDY` raised by hand over a UUID with typed digests was approvable."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    approval_id = _raise(
        connection,
        action=ApprovalAction.APPROVE_REMEDY,
        store_id=store_id,
        resource_id=uuid4(),
        version=1,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        requested_by=operator,
    )
    with pytest.raises(ApprovalResourceChangedError):
        _decide(
            connection,
            approval_id,
            version=1,
            snapshot_hash=HASH_A,
            rendered_hash=HASH_B,
            by=owner,
        )
    _nothing_decided(connection, approval_id)


# --- The explicit rest ---------------------------------------------------------------------------


def test_an_unbuilt_resource_type_keeps_exactly_the_echo_check_it_had(connection: Any) -> None:
    """`SLOT_PROPOSAL` has no table and no rendering: nothing to resolve, so nothing is invented.

    The observed binding must still equal the stored envelope -- that check is unchanged -- and an
    exact echo is still approved, because there is genuinely nothing else to compare it with.
    """

    assert "SLOT_PROPOSAL" in UNRESOLVABLE_AT_DECISION
    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)

    def envelope() -> UUID:
        return _raise(
            connection,
            action=ApprovalAction.CONFIRM_SLOT,
            store_id=store_id,
            resource_id=uuid4(),
            version=1,
            snapshot_hash=HASH_A,
            rendered_hash=HASH_B,
            requested_by=operator,
        )

    mismatched = envelope()
    with pytest.raises(ApprovalStateError, match="resource version or hash is stale"):
        _decide(
            connection,
            mismatched,
            version=1,
            snapshot_hash=HASH_B,
            rendered_hash=HASH_B,
            by=approver,
        )
    echoed = _decide(
        connection, envelope(), version=1, snapshot_hash=HASH_A, rendered_hash=HASH_B, by=approver
    )
    assert echoed.status == "APPROVED"


def test_a_resource_type_the_server_does_not_know_is_refused_not_waved_through(
    connection: Any,
) -> None:
    """Fail closed. `build_approval_envelope` refuses such a type, so the row is written directly --
    which is the only way one could exist, and exactly the row nobody should be able to approve."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    approval_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO approval_requests (
                id, action, resource_type, resource_id, resource_version, snapshot_hash,
                rendered_hash, policy_version, required_role, reason_codes, obligations,
                execution_capability, requested_by, requested_at, expires_at, envelope,
                envelope_hash, store_id
            ) VALUES (
                %s, 'CONFIRM_SLOT', 'MYSTERY_RESOURCE', %s, 1, %s, %s, 'policy-v1',
                'OPS_APPROVER', '[]'::jsonb, '[]'::jsonb, 'HUMAN_APPROVED_ACTION', %s, %s, %s,
                '{}'::jsonb, %s, %s
            )
            """,
            (
                approval_id,
                uuid4(),
                HASH_A,
                HASH_B,
                operator.staff_user_id,
                NOW,
                NOW + timedelta(minutes=15),
                f"JCS-SHA256-V1:{uuid4().hex * 2}",
                store_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO approval_request_states (
                approval_request_id, status, row_version, updated_at
            ) VALUES (%s, 'REQUESTED', 1, %s)
            """,
            (approval_id, NOW),
        )

    with pytest.raises(ApprovalStateError, match="cannot be verified") as refused:
        _decide(
            connection,
            approval_id,
            version=1,
            snapshot_hash=HASH_A,
            rendered_hash=HASH_B,
            by=approver,
        )
    # Not "changed": nothing is known to have changed. The server cannot identify the resource.
    assert not isinstance(refused.value, ApprovalResourceChangedError)
    _nothing_decided(connection, approval_id)


# --- The worker's execution claim (`API-INTEGRITY-004`) ------------------------------------------
#
# `claim_execution` compared the worker's observed binding with the stored envelope and nothing
# else, exactly as `decide` did before `API-INTEGRITY-003`. It is the last gate before a provider,
# and the gap there is wider than at the decision: an approval can sit APPROVED until it expires
# while the quote is re-priced or a reviewer rewrites the draft. Each refusal below is built the
# same way as the decision tests above -- approve real content, change it, then claim with the
# stored binding *exactly* -- and before this item every one of them was claimed.

POLICY = "decision-time-resolution-v1"
NOTHING_CLAIMED = ("APPROVED", 0, 0, 0, 0)
CLAIMED_ONCE = ("EXECUTING", 1, 1, 1, 1)


def _claim(
    connection: Any, approval_id: UUID, *, version: int, snapshot_hash: str, rendered_hash: str
) -> Any:
    return ApprovalRepository().claim_execution(
        connection,
        ApprovalExecutionCommand(
            approval_id,
            ActorRole.OUTBOX_WORKER,
            version,
            snapshot_hash,
            rendered_hash,
            POLICY,
            uuid4(),
        ),
    )


def _claim_footprint(connection: Any, approval_id: UUID) -> tuple[object, ...]:
    """The state a claim leaves, and every row a claim writes -- execution, event, audit, outbox."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.status,
                   (SELECT count(*) FROM approval_executions x WHERE x.approval_request_id = %s),
                   (SELECT count(*) FROM domain_events e
                     WHERE e.aggregate_id = %s AND e.event_type = 'APPROVAL_EXECUTION_CLAIMED'),
                   (SELECT count(*) FROM audit_events a
                     WHERE a.aggregate_id = %s AND a.action = 'APPROVAL_EXECUTION_CLAIM'),
                   (SELECT count(*) FROM outbox_events o WHERE o.idempotency_key = %s)
            FROM approval_request_states s WHERE s.approval_request_id = %s
            """,
            (
                approval_id,
                approval_id,
                approval_id,
                f"approval:{approval_id}:execution-claimed",
                approval_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


class _ApprovedQuote:
    """A PRESENT_QUOTE envelope over revision 1 of a real quote, approved by a second person."""

    def __init__(self, connection: Any) -> None:
        self.store_id = _shop(connection)
        self.operator = _member(connection, self.store_id, StaffRole.OPERATOR)
        approver = _member(connection, self.store_id, StaffRole.OPS_APPROVER)
        self.quote_id, self.order_request_id, self.first = _quote(
            connection, self.store_id, self.operator
        )
        self.approval_id = _raise(
            connection,
            action=ApprovalAction.PRESENT_QUOTE,
            store_id=self.store_id,
            resource_id=self.quote_id,
            version=1,
            snapshot_hash=self.first.document.snapshot_hash,
            rendered_hash=RENDERED,
            requested_by=self.operator,
        )
        approved = _decide(
            connection,
            self.approval_id,
            version=1,
            snapshot_hash=self.first.document.snapshot_hash,
            rendered_hash=RENDERED,
            by=approver,
        )
        assert approved.status == "APPROVED"

    def claim(self, connection: Any) -> Any:
        """Claim with the stored binding exactly, as a worker handed this envelope would."""
        return _claim(
            connection,
            self.approval_id,
            version=1,
            snapshot_hash=self.first.document.snapshot_hash,
            rendered_hash=RENDERED,
        )


def test_a_quote_re_priced_after_approval_cannot_be_claimed_with_the_stored_hashes(
    connection: Any,
) -> None:
    """Approved at 100.000 đ; the quote now offers 150.000 đ. The worker echoes the envelope."""

    quote = _ApprovedQuote(connection)
    _reprice(connection, quote.store_id, quote.quote_id, quote.order_request_id, quote.operator)

    with pytest.raises(ApprovalResourceChangedError) as refused:
        quote.claim(connection)
    assert str(refused.value).startswith(f"{RESOURCE_CHANGED_SINCE_REQUEST}:")
    # Still an `ApprovalStateError`: every caller that refuses a stale claim refuses this one.
    assert isinstance(refused.value, ApprovalStateError)
    assert _claim_footprint(connection, quote.approval_id) == NOTHING_CLAIMED


def test_an_unchanged_approved_quote_is_still_claimed_once(connection: Any) -> None:
    """The control: the check refuses a change, not every quote claim -- and one-time use holds."""

    quote = _ApprovedQuote(connection)
    assert quote.claim(connection).status == "EXECUTING"
    assert _claim_footprint(connection, quote.approval_id) == CLAIMED_ONCE
    with pytest.raises(ApprovalStateError, match="not executable"):
        quote.claim(connection)
    assert _claim_footprint(connection, quote.approval_id) == CLAIMED_ONCE


class _ApprovedDraft:
    """A SEND_MESSAGE envelope over a real draft's server-computed binding, approved."""

    def __init__(self, connection: Any) -> None:
        store_id = _shop(connection)
        operator = _member(connection, store_id, StaffRole.OPERATOR)
        approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
        self.reviewer = _member(connection, store_id, StaffRole.OPS_APPROVER)
        draft = seed_message_draft(connection, store_id)
        self.agent_run_id = draft.agent_run_id
        self.binding = current_binding(connection, draft.agent_run_id)
        self.approval_id = _raise(
            connection,
            action=ApprovalAction.SEND_MESSAGE,
            store_id=store_id,
            resource_id=draft.agent_run_id,
            version=self.binding.resource_version,
            snapshot_hash=self.binding.snapshot_hash,
            rendered_hash=self.binding.rendered_hash,
            requested_by=operator,
        )
        approved = _decide(
            connection,
            self.approval_id,
            version=self.binding.resource_version,
            snapshot_hash=self.binding.snapshot_hash,
            rendered_hash=self.binding.rendered_hash,
            by=approver,
        )
        assert approved.status == "APPROVED"

    def claim(self, connection: Any) -> Any:
        return _claim(
            connection,
            self.approval_id,
            version=self.binding.resource_version,
            snapshot_hash=self.binding.snapshot_hash,
            rendered_hash=self.binding.rendered_hash,
        )


@pytest.mark.parametrize("review", ["EDIT", "REJECT"])
def test_a_draft_reviewed_after_approval_cannot_be_claimed_with_the_stored_hashes(
    connection: Any, review: str
) -> None:
    """The send path's version of the defect. An EDIT after approval makes the approved words not
    the draft's words; a REJECT leaves nothing sendable. The worker must claim neither."""

    draft = _ApprovedDraft(connection)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision=review,
        principal=draft.reviewer,
        correlation_id=uuid4(),
        reason_code="REVIEWER_CHANGED_IT" if review == "REJECT" else None,
        edited_text="Dạ, đồ của anh/chị xong rồi ạ." if review == "EDIT" else None,
    )

    with pytest.raises(ApprovalResourceChangedError):
        draft.claim(connection)
    assert _claim_footprint(connection, draft.approval_id) == NOTHING_CLAIMED


def test_an_unchanged_approved_draft_is_still_claimed(connection: Any) -> None:
    draft = _ApprovedDraft(connection)
    assert draft.claim(connection).status == "EXECUTING"
    assert _claim_footprint(connection, draft.approval_id) == CLAIMED_ONCE


def test_an_unbuilt_resource_type_keeps_exactly_the_echo_check_at_the_claim(
    connection: Any,
) -> None:
    """`SLOT_PROPOSAL` has nothing to resolve, at the claim as at the decision: a mismatched echo is
    refused as stale, as it always was, and an exact echo is claimed."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approver = _member(connection, store_id, StaffRole.OPS_APPROVER)
    approval_id = _raise(
        connection,
        action=ApprovalAction.CONFIRM_SLOT,
        store_id=store_id,
        resource_id=uuid4(),
        version=1,
        snapshot_hash=HASH_A,
        rendered_hash=HASH_B,
        requested_by=operator,
    )
    _decide(
        connection, approval_id, version=1, snapshot_hash=HASH_A, rendered_hash=HASH_B, by=approver
    )
    with pytest.raises(ApprovalStateError, match="resource version or hash is stale"):
        _claim(connection, approval_id, version=1, snapshot_hash=HASH_B, rendered_hash=HASH_B)
    assert _claim_footprint(connection, approval_id) == NOTHING_CLAIMED
    claimed = _claim(connection, approval_id, version=1, snapshot_hash=HASH_A, rendered_hash=HASH_B)
    assert claimed.status == "EXECUTING"


def test_a_resource_type_the_server_does_not_know_is_not_claimed(connection: Any) -> None:
    """Fail closed at the claim too. An APPROVED row over a type no resolver knows -- written
    directly, because nothing in this repository could write it -- is refused and left APPROVED."""

    store_id = _shop(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    approval_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO approval_requests (
                id, action, resource_type, resource_id, resource_version, snapshot_hash,
                rendered_hash, policy_version, required_role, reason_codes, obligations,
                execution_capability, requested_by, requested_at, expires_at, envelope,
                envelope_hash, store_id
            ) VALUES (
                %s, 'CONFIRM_SLOT', 'MYSTERY_RESOURCE', %s, 1, %s, %s, %s,
                'OPS_APPROVER', '[]'::jsonb, '[]'::jsonb, 'HUMAN_APPROVED_ACTION', %s, %s, %s,
                '{}'::jsonb, %s, %s
            )
            """,
            (
                approval_id,
                uuid4(),
                HASH_A,
                HASH_B,
                POLICY,
                operator.staff_user_id,
                NOW,
                NOW + timedelta(minutes=15),
                f"JCS-SHA256-V1:{uuid4().hex * 2}",
                store_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO approval_request_states (
                approval_request_id, status, row_version, updated_at
            ) VALUES (%s, 'APPROVED', 1, %s)
            """,
            (approval_id, NOW),
        )

    with pytest.raises(ApprovalStateError, match="cannot be verified") as refused:
        _claim(connection, approval_id, version=1, snapshot_hash=HASH_A, rendered_hash=HASH_B)
    assert not isinstance(refused.value, ApprovalResourceChangedError)
    assert _claim_footprint(connection, approval_id) == NOTHING_CLAIMED
