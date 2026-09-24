"""One complaint, one set of figures: `DEC-004`'s ceilings are per item and per order, not per form.

`REMEDY-001` checked the 5x ceiling and the 100.000 d staff limit against the amount on *one*
proposal. Nothing stopped a second proposal against the same incident, and nothing summed the first
into the second, so the lead reproduced this on real PostgreSQL: three proposals of 80.000 d on one
incident, each individually under the staff limit, all `STAFF_AUTHORIZED`, all executed -- three
credits and 240.000 d that no owner ever saw. Splitting a claim is the cheapest way round a
per-request limit and the first one anybody at a counter would find.

The rule these tests pin, as the lead decided it:

1. A proposal against an incident that is no longer `OPEN` or `UNDER_REVIEW` is refused.
2. `DAMAGE_COMPENSATION`: both figures apply **cumulatively to the order line**. What earlier
   proposals on the same (order, line) have already committed -- every proposal that can still pay
   or already paid -- is added to the new amount before either figure is compared.
3. `LATE_DELIVERY_CREDIT`: at most one live-or-paid proposal per order.
4. Two simultaneous proposals must not both read "nothing committed yet": the sum is taken under a
   row lock on the order, inside the transaction that inserts.

"Can still pay" has a precise meaning here, because `RemedyStatus` has no terminal non-paying
member. A proposal that needed the owner is dead once its `APPROVE_REMEDY` envelope reaches a
terminal state that is not approval -- `REJECTED`, `EXPIRED` or `CANCELLED`, none of which any
transition leaves -- because `execute` requires an `APPROVED` envelope. Everything else counts.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
)
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.remedies import (
    CREDIT_EXECUTED,
    RemedyProposalCommand,
    RemedyProposalRepository,
    RemedyStateError,
)
from nha_trang_laundry_domain.catalog import FulfillmentMode
from nha_trang_laundry_domain.remedies import RemedyKind, RemedyRefusal, RemedyStatus
from test_remedies import (
    DAMAGE_CEILING,
    NOW,
    _approve,
    _execute,
    _incident,
    _incident_row,
    _propose,
    _shop,
    _staff,
)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """The same non-autocommit fixture `test_remedies.py` uses.

    Non-autocommit matters to the concurrency test below: `propose` never commits the caller's
    connection, so a proposal written here stays open, holding its locks, until the test commits.
    """

    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _damage(
    connection: Any, store_id: UUID, incident_id: UUID, staff: Any, amount: int, **fields: Any
) -> Any:
    return _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=amount,
        **fields,
    )


def _credits(connection: Any, order_id: UUID) -> list[int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT amount_vnd FROM remedy_credits WHERE issued_from_order_id = %s ORDER BY 1",
            (order_id,),
        )
        return [int(row[0]) for row in cursor.fetchall()]


def _reject(connection: Any, store_id: UUID, approval_id: UUID) -> None:
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT resource_version, snapshot_hash, rendered_hash FROM approval_requests "
            "WHERE id = %s",
            (approval_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    ApprovalRepository().decide(
        connection,
        ApprovalDecisionCommand(
            approval_request_id=approval_id,
            decision=ApprovalDecision.REJECTED,
            observed_resource_version=int(row[0]),
            observed_snapshot_hash=str(row[1]),
            observed_rendered_hash=str(row[2]),
            reason_code="OWNER_REJECTED_REMEDY",
            principal=owner,
            correlation_id=uuid4(),
            decided_at=NOW + timedelta(minutes=1),
        ),
    )


# --- the reproduction, and the split -------------------------------------------------------------


def test_three_eighty_thousand_proposals_on_one_complaint_mint_one_staff_credit(
    connection: psycopg.Connection[Any],
) -> None:
    """The lead's reproduction, verbatim: 3 x 80.000 d on one incident.

    Before the fix all three were `STAFF_AUTHORIZED` and all three executed. Now the first is inside
    the staff limit (0 + 80.000 <= 100.000), and the second and third are not (80.000 + 80.000 and
    160.000 + 80.000 are both above it), so they need the owner and cannot be executed without one.
    """

    store_id, staff, order_id, incident_id = _shop(connection)
    proposals = [_damage(connection, store_id, incident_id, staff, 80_000) for _ in range(3)]
    assert [p.status for p in proposals] == [
        RemedyStatus.STAFF_AUTHORIZED,
        RemedyStatus.OWNER_APPROVAL_REQUIRED,
        RemedyStatus.OWNER_APPROVAL_REQUIRED,
    ]

    assert _execute(connection, proposals[0].proposal_id, staff).event_type == CREDIT_EXECUTED
    for later in proposals[1:]:
        with pytest.raises(RemedyStateError) as refused:
            _execute(connection, later.proposal_id, staff)
        assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"

    # One credit, 80.000 d. Not three and not 240.000 d.
    assert _credits(connection, order_id) == [80_000]


def test_a_claim_split_four_ways_under_the_staff_limit_still_needs_the_owner(
    connection: psycopg.Connection[Any],
) -> None:
    """4 x 100.000 d against one line: the first is staff-authorised, every later one is not.

    100.000 d is exactly the staff limit and inclusive, so the first proposal alone is staff work.
    From the second on the committed total is above it -- 200.000, 300.000, 400.000 -- and every one
    of those is inside the 500.000 d ceiling, so the answer is "the owner decides", not a refusal.
    """

    store_id, staff, _, incident_id = _shop(connection)
    statuses = [_damage(connection, store_id, incident_id, staff, 100_000).status for _ in range(4)]
    assert statuses == [
        RemedyStatus.STAFF_AUTHORIZED,
        RemedyStatus.OWNER_APPROVAL_REQUIRED,
        RemedyStatus.OWNER_APPROVAL_REQUIRED,
        RemedyStatus.OWNER_APPROVAL_REQUIRED,
    ]


def test_the_cumulative_ceiling_holds_even_when_every_proposal_is_owner_approved(
    connection: psycopg.Connection[Any],
) -> None:
    """The owner approving each proposal does not lift the 5x cap on the item.

    `DEC-004` caps compensation for one item at five times what the shop charged to clean it. Owner
    approval is the answer to the *staff* limit; it was never a way past the item's own ceiling. So
    300.000 d approved and paid, then a second complaint on the same order and line: 250.000 d would
    bring the line to 550.000 d and is refused naming the 500.000 d ceiling and what is already
    committed, and 200.000 d -- exactly the remainder -- is allowed, for the owner to decide.
    """

    store_id, staff, order_id, incident_id = _shop(connection)
    first = _damage(connection, store_id, incident_id, staff, 300_000)
    assert first.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert first.approval_id is not None
    _approve(connection, store_id, first.approval_id, NOW + timedelta(minutes=1))
    _execute(connection, first.proposal_id, staff, NOW + timedelta(minutes=2))

    # A second complaint about the same garment. The first incident is closed by its execution, so
    # the ceiling has to be per line rather than per incident or this is a fresh 500.000 d.
    second_incident = _incident(connection, store_id, order_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        _damage(connection, store_id, second_incident, staff, 250_000)
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert refused.value.ceiling_vnd == DAMAGE_CEILING
    assert refused.value.committed_vnd == 300_000

    remainder = _damage(connection, store_id, second_incident, staff, 200_000)
    assert remainder.status is RemedyStatus.OWNER_APPROVAL_REQUIRED

    # The line is now full to the dong, whoever would approve the next one.
    with pytest.raises(RemedyStateError) as full:
        _damage(connection, store_id, second_incident, staff, 1)
    assert full.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert full.value.committed_vnd == DAMAGE_CEILING


def test_an_owner_rejection_releases_what_that_proposal_had_committed(
    connection: psycopg.Connection[Any],
) -> None:
    """The terminal non-paying state lives on the envelope, and it is honoured.

    A rejected `APPROVE_REMEDY` envelope can never be approved again and `execute` requires an
    approved one, so the proposal it guards can never pay. Counting it would let one "no" from the
    owner permanently shrink what the customer could ever be offered for that item.
    """

    store_id, staff, _, incident_id = _shop(connection)
    refused_by_owner = _damage(connection, store_id, incident_id, staff, 300_000)
    assert refused_by_owner.approval_id is not None
    _reject(connection, store_id, refused_by_owner.approval_id)

    # 300.000 + 300.000 would be over the ceiling if the rejected one still counted.
    again = _damage(connection, store_id, incident_id, staff, 300_000)
    assert again.status is RemedyStatus.OWNER_APPROVAL_REQUIRED


# --- the incident and the order -----------------------------------------------------------------


def test_a_proposal_against_a_closed_incident_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    """An incident that reached its outcome is not a place to hang another one."""

    store_id, staff, _, incident_id = _shop(connection)
    first = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.FREE_REWASH,
        store_fault_attested=True,
    )
    _execute(connection, first.proposal_id, staff)
    assert _incident_row(connection, incident_id)[0] == "CLOSED"

    with pytest.raises(RemedyStateError) as refused:
        _damage(connection, store_id, incident_id, staff, 10_000)
    assert refused.value.reason_code == "REMEDY_INCIDENT_NOT_OPEN"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM remedy_proposals WHERE incident_id = %s", (incident_id,)
        )
        counted = cursor.fetchone()
    assert counted is not None and counted[0] == 1


def test_a_second_late_delivery_credit_on_one_order_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    """One late delivery, one 10% credit -- however many incidents are opened about it."""

    store_id, staff, order_id, incident_id = _shop(
        connection, mode=FulfillmentMode.PICKUP_AND_RETURN
    )
    DeliveryLegRepository().record(
        connection,
        RecordDeliveryLegCommand(
            order_id=order_id,
            leg_kind=DeliveryLegKind.RETURN,
            outcome=DeliveryLegOutcome.SUCCEEDED,
            principal=staff,
            correlation_id=uuid4(),
            recorded_at=NOW,
        ),
    )
    late = dict(
        kind=RemedyKind.LATE_DELIVERY_CREDIT,
        store_fault_attested=True,
        attested_late_by_minutes=150,
    )
    first = _propose(connection, store_id, incident_id, staff, **late)
    assert first.status is RemedyStatus.STAFF_AUTHORIZED

    # Same incident, not yet executed.
    with pytest.raises(RemedyStateError) as same_incident:
        _propose(connection, store_id, incident_id, staff, **late)
    assert same_incident.value.reason_code == (
        RemedyRefusal.REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED.value
    )

    # And a second incident about the same order, after the first credit was paid.
    _execute(connection, first.proposal_id, staff)
    second_incident = _incident(connection, store_id, order_id, staff)
    with pytest.raises(RemedyStateError) as second:
        _propose(connection, store_id, second_incident, staff, **late)
    assert second.value.reason_code == (
        RemedyRefusal.REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED.value
    )
    assert _credits(connection, order_id) == [11_000]


# --- the positive control -------------------------------------------------------------------------


def test_a_single_legitimate_damage_proposal_still_works_end_to_end(
    connection: psycopg.Connection[Any],
) -> None:
    """The fix must not break the case the item exists for: one 80.000 d claim, staff-authorised."""

    store_id, staff, order_id, incident_id = _shop(connection)
    proposal = _damage(connection, store_id, incident_id, staff, 80_000)
    assert proposal.status is RemedyStatus.STAFF_AUTHORIZED
    assert (proposal.amount_vnd, proposal.ceiling_vnd) == (80_000, DAMAGE_CEILING)
    executed = _execute(connection, proposal.proposal_id, staff)
    assert executed.event_type == CREDIT_EXECUTED and executed.amount_vnd == 80_000
    assert _credits(connection, order_id) == [80_000]
    assert _incident_row(connection, incident_id) == ("CLOSED", True, True)


# --- concurrency ---------------------------------------------------------------------------------


def _wait_for_lock_waiter(monitor: Any, *, timeout_s: float = 5.0) -> bool:
    """Until another backend is blocked on a lock, or the timeout. Returns whether one was seen."""

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with monitor.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) FROM pg_stat_activity
                WHERE datname = current_database() AND wait_event_type = 'Lock'
                """
            )
            row = cursor.fetchone()
        if row is not None and int(row[0]) > 0:
            return True
        time.sleep(0.05)
    return False


def test_two_simultaneous_proposals_cannot_both_read_nothing_committed(
    connection: psycopg.Connection[Any],
) -> None:
    """Rule 4. Two counters, one garment, the same second.

    Two *different* incidents on the same order, so nothing about the incident row can serialise
    them: only a lock on what the ceiling is a ceiling *of* -- the order -- can. Proposal A is
    written on this connection and left uncommitted, holding whatever it locked. Proposal B runs on
    a second connection while A is open. Without the lock, B reads "nothing committed", A is
    invisible to it, and both are staff-authorised: 160.000 d with no owner. With it, B waits for
    A, re-reads under the lock, sees 80.000 d already committed, and refuses rather than record a
    staff authorisation its own pre-check no longer supports.
    """

    database_url = os.environ["DATABASE_URL"]
    store_id, staff, order_id, incident_a = _shop(connection)
    incident_b = _incident(connection, store_id, order_id, staff)
    connection.commit()

    outcome: dict[str, Any] = {}

    def propose_b() -> None:
        with psycopg.connect(database_url) as other:
            try:
                outcome["b"] = RemedyProposalRepository().propose(
                    other,
                    RemedyProposalCommand(
                        store_id=store_id,
                        incident_id=incident_b,
                        kind=RemedyKind.DAMAGE_COMPENSATION,
                        store_fault_attested=True,
                        principal=staff,
                        correlation_id=uuid4(),
                        order_line_id="line-1",
                        amount_vnd=80_000,
                        proposed_at=NOW,
                    ),
                )
            except RemedyStateError as error:
                outcome["b_error"] = error

    a = _damage(connection, store_id, incident_a, staff, 80_000)
    assert a.status is RemedyStatus.STAFF_AUTHORIZED
    # A's transaction is still open here: `propose` never commits the caller's connection.
    worker = threading.Thread(target=propose_b)
    with psycopg.connect(database_url, autocommit=True) as monitor:
        worker.start()
        saw_waiter = _wait_for_lock_waiter(monitor)
        connection.commit()
        worker.join(timeout=30)
    assert not worker.is_alive()

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, count(*), sum(amount_vnd) FROM remedy_proposals
            WHERE order_id = %s GROUP BY status
            """,
            (order_id,),
        )
        by_status = {str(row[0]): (int(row[1]), int(row[2])) for row in cursor.fetchall()}
    # Exactly one staff authorisation on the line, whatever happened to B.
    assert by_status.get("STAFF_AUTHORIZED") == (1, 80_000), by_status
    if "b" in outcome:
        assert outcome["b"].status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    else:
        assert outcome["b_error"].reason_code == "STALE_VERSION"
    # And B really did wait for A rather than finishing first: the serialisation is the mechanism,
    # not an accident of timing.
    assert saw_waiter


# --- execution re-checks what proposal promised ---------------------------------------------------


def _forge_legacy_proposal(
    connection: Any, *, store_id: UUID, incident_id: UUID, order_id: UUID, staff: Any, amount: int
) -> UUID:
    """A staff-authorised proposal row written the way the pre-fix code could have written it.

    Every column the table demands, copied from a real proposal on the same incident, and nothing
    that `propose` would have checked. It stands for the rows a deployed database may already hold,
    and for any future writer that forgets the rule.
    """

    proposal_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO remedy_proposals (
                id, store_id, incident_id, order_id, kind, status, amount_vnd, direction,
                ceiling_vnd, order_line_id, policy_version_id, policy_version,
                store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                proposed_by, proposed_at, correlation_id
            )
            SELECT %s, store_id, incident_id, order_id, kind, 'STAFF_AUTHORIZED', %s, direction,
                   ceiling_vnd, order_line_id, policy_version_id, policy_version,
                   store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                   proposed_by, proposed_at, %s
            FROM remedy_proposals WHERE incident_id = %s AND order_id = %s
            LIMIT 1
            """,
            (proposal_id, amount, uuid4(), incident_id, order_id),
        )
    return proposal_id


def test_execution_refuses_a_staff_authorised_row_that_breaks_the_cumulative_limit(
    connection: psycopg.Connection[Any],
) -> None:
    """Defence in depth for rows written before this fix, or by any path that skips `propose`.

    Two staff-authorised 80.000 d rows on one line cannot both be paid without the owner: the second
    execution would put 160.000 d of staff-only compensation on the item.
    """

    store_id, staff, order_id, incident_id = _shop(connection)
    real = _damage(connection, store_id, incident_id, staff, 80_000)
    legacy = _forge_legacy_proposal(
        connection,
        store_id=store_id,
        incident_id=incident_id,
        order_id=order_id,
        staff=staff,
        amount=80_000,
    )
    _execute(connection, real.proposal_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, legacy, staff)
    assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"
    assert _credits(connection, order_id) == [80_000]
