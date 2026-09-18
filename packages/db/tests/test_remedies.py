"""`REMEDY-001`: an incident can reach an outcome, and four of them are refusable.

The claim of this item is that `DEC-004` stops being a paragraph in a register. So the headline
tests walk each of the four kinds from a recorded complaint to whatever the owner's figures say it
becomes, and the rest are refusals -- which is where the risk is, because this is the first path in
the system that creates money owed to a customer.

The two that matter most are the ones nobody would think to write. `LOST_ITEM` is recorded and
stopped, and **no test here asserts a loss ceiling** because asserting one would ratify a figure the
owner explicitly declined to give. And the settlement ledger is compared byte for byte across an
approved remedy, because the temptation when money is owed is to go back and edit what was paid.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
)
from nha_trang_laundry_db.configurations import ConfigurationValidationError
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.promotions import (
    publish_promotion_policy,
    read_published_promotion_program,
)
from nha_trang_laundry_db.quotes import (
    QuoteAcceptanceCommand,
    QuoteAcceptanceRepository,
    QuoteRepository,
    QuoteRevisionCommand,
)
from nha_trang_laundry_db.remedies import (
    CREDIT_EXECUTED,
    REWASH_COMMANDED,
    RemedyCreditRedemptionCommand,
    RemedyCreditRepository,
    RemedyExecutionCommand,
    RemedyProposalCommand,
    RemedyProposalRepository,
    RemedyStateError,
    publish_remedy_policy,
    read_published_remedy_policy,
    validate_remedy_policy,
)
from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    PolicyOutcome,
    ProductionStatus,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from nha_trang_laundry_domain.pricebook_import import (
    import_pricebook_csv,
    runtime_price_rules,
)
from nha_trang_laundry_domain.promotion import PromotionReason
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    accept_quote_revision,
    compose_quote_revision,
    frozen_promotion,
)
from nha_trang_laundry_domain.quotes import parse_quote_revision
from nha_trang_laundry_domain.remedies import (
    RemedyKind,
    RemedyRefusal,
    RemedyStatus,
)
from quote_test_data import accepted_quote, counter_ticket, make_quote_snapshot

ROOT = Path(__file__).resolve().parents[3]
POLICY = json.loads((ROOT / "templates" / "remedy-policy-dec-004.json").read_text(encoding="utf-8"))

#: Now, not a fixed calendar date. The approval envelope for `APPROVE_REMEDY` lives ten minutes
#: (`_OWNER_FINANCIAL`), so a fixture pinned to 2026-08-01 would raise envelopes that were already
#: expired before the owner could decide them -- the defect `quote_test_data.PRICED_AT` records.
NOW = datetime.now(UTC).replace(microsecond=0)
READY = IntakeReadiness(True, True, True, True, True, True)
#: `make_quote_snapshot` bills 100.000 d of service on `line-1` and adds a 10.000 d delivery fee.
LINE_AMOUNT = 100_000
QUOTED_TOTAL = 110_000
#: Five times that one line, from the published multiple. Spelled out rather than imported so a
#: change to either number has to be argued for here too.
DAMAGE_CEILING = 500_000
#: Ten percent of the settled total, rounded half up: 110.000 x 1000 / 10000 = 11.000 exactly.
LATE_CREDIT = 11_000


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    staff_id, assigner = uuid4(), uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (identifier, f"oidc-{identifier}", NOW),
            )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT DO NOTHING
            """,
            (staff_id, store_id, assigner, NOW),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _advance(
    connection: Any, order_id: UUID, staff: StaffPrincipal, version: int, **target: Any
) -> Any:
    return OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            order_id, version, staff, f"step-{uuid4().hex}", uuid4(), occurred_at=NOW, **target
        ),
    )


def _released_order(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
) -> tuple[UUID, int]:
    """An order walked to ACTIVE with production RELEASED, through every real transition."""

    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, fulfillment_mode=mode
    )
    order_id = (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                mode,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                NOW,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )
    version = 1
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": NOW,
            "intake_readiness": READY,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
        {"production_target": ProductionStatus.QUEUED},
        {"production_target": ProductionStatus.IN_PROCESS},
        {"production_target": ProductionStatus.QUALITY_CHECK},
        {"production_target": ProductionStatus.READY_AT_STORE},
        {"production_target": ProductionStatus.RELEASED},
    ):
        version = _advance(connection, order_id, staff, version, **step).row_version
    return order_id, version


def _settle(connection: Any, order_id: UUID, staff: StaffPrincipal, *, collected: bool) -> None:
    SettlementRepository().record(
        connection,
        SettlementCommand(
            order_id=order_id,
            paid_amount_vnd=QUOTED_TOTAL,
            collected_by_customer=collected,
            principal=staff,
            correlation_id=uuid4(),
            attested_at=NOW,
        ),
    )


def _incident(connection: Any, store_id: UUID, order_id: UUID, staff: StaffPrincipal) -> UUID:
    return (
        IncidentRepository()
        .open_from_counter(
            connection,
            StaffIncidentOpenCommand(
                store_id=store_id,
                order_id=order_id,
                evidence_summary="Áo dài bị phai màu sau khi giặt",
                actor_id=staff.staff_user_id,
                correlation_id=uuid4(),
                opened_at=NOW,
            ),
            principal=staff,
        )
        .incident_id
    )


def _publish_policy(connection: Any, staff: StaffPrincipal, **overrides: Any) -> None:
    publish_remedy_policy(connection, actor_id=staff.staff_user_id, payload={**POLICY, **overrides})


def _shop(
    connection: Any,
    *,
    mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    settle: bool = True,
    publish: bool = True,
) -> tuple[UUID, StaffPrincipal, UUID, UUID]:
    """A store, a counter staff member, a released order, and an open incident against it."""

    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    if publish:
        _publish_policy(connection, staff)
    order_id, _ = _released_order(connection, store_id, staff, mode)
    if settle:
        _settle(
            connection,
            order_id,
            staff,
            collected=mode not in {FulfillmentMode.PICKUP_AND_RETURN, FulfillmentMode.RETURN_ONLY},
        )
    return store_id, staff, order_id, _incident(connection, store_id, order_id, staff)


def _propose(
    connection: Any, store_id: UUID, incident_id: UUID, staff: StaffPrincipal, **fields: Any
) -> Any:
    return RemedyProposalRepository().propose(
        connection,
        RemedyProposalCommand(
            store_id=store_id,
            incident_id=incident_id,
            principal=staff,
            correlation_id=uuid4(),
            proposed_at=fields.pop("proposed_at", NOW),
            **fields,
        ),
    )


def _execute(connection: Any, proposal_id: UUID, staff: StaffPrincipal, at: datetime = NOW) -> Any:
    return RemedyProposalRepository().execute(
        connection,
        RemedyExecutionCommand(
            proposal_id=proposal_id, principal=staff, correlation_id=uuid4(), executed_at=at
        ),
    )


def _approve(connection: Any, store_id: UUID, approval_id: UUID, at: datetime) -> None:
    """The owner decides the envelope, through the real maker-checker path."""

    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT resource_version, snapshot_hash, rendered_hash
            FROM approval_requests WHERE id = %s
            """,
            (approval_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    ApprovalRepository().decide(
        connection,
        ApprovalDecisionCommand(
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVED,
            observed_resource_version=int(row[0]),
            observed_snapshot_hash=str(row[1]),
            observed_rendered_hash=str(row[2]),
            reason_code="OWNER_APPROVED_REMEDY",
            principal=owner,
            correlation_id=uuid4(),
            decided_at=at,
        ),
    )


def _events(connection: Any, aggregate_id: UUID) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type FROM domain_events
            WHERE aggregate_type = 'REMEDY_PROPOSAL' AND aggregate_id = %s
            ORDER BY aggregate_version
            """,
            (aggregate_id,),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def _incident_row(connection: Any, incident_id: UUID) -> tuple[str, bool, bool]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, fault_decided, remedy_decided FROM customer_incidents WHERE id = %s",
            (incident_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return str(row[0]), bool(row[1]), bool(row[2])


# --- the four kinds, end to end -----------------------------------------------------------------


def test_a_free_rewash_reaches_an_outcome(connection: psycopg.Connection[Any]) -> None:
    """No money moves, so what is recorded is the authority, the fault finding and the window."""

    store_id, staff, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.FREE_REWASH,
        store_fault_attested=True,
    )
    assert proposal.status is RemedyStatus.STAFF_AUTHORIZED
    assert proposal.amount_vnd is None and proposal.ceiling_vnd is None
    assert proposal.window_closes_at == NOW + timedelta(days=7)
    assert _incident_row(connection, incident_id) == ("UNDER_REVIEW", True, False)

    executed = _execute(connection, proposal.proposal_id, staff)
    assert executed.event_type == REWASH_COMMANDED
    assert executed.credit_id is None
    assert _events(connection, proposal.proposal_id) == [
        "REMEDY_PROPOSAL_RECORDED",
        REWASH_COMMANDED,
    ]
    assert _incident_row(connection, incident_id) == ("CLOSED", True, True)


def test_damage_compensation_inside_the_staff_ceiling_reaches_an_outcome(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, order_id, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=80_000,
    )
    assert proposal.status is RemedyStatus.STAFF_AUTHORIZED
    assert (proposal.amount_vnd, proposal.ceiling_vnd) == (80_000, DAMAGE_CEILING)

    executed = _execute(connection, proposal.proposal_id, staff)
    assert executed.event_type == CREDIT_EXECUTED
    assert executed.credit_id is not None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT amount_vnd, direction, bearer_contact_id, redeemed_at
            FROM remedy_credits WHERE id = %s
            """,
            (executed.credit_id,),
        )
        row = cursor.fetchone()
        cursor.execute("SELECT bound_contact_id FROM orders WHERE id = %s", (order_id,))
        bound = cursor.fetchone()
    assert row is not None and bound is not None
    # Non-negative amount, and the sign in the direction. Invariant 2.
    assert (int(row[0]), str(row[1]), row[3]) == (80_000, "CREDIT", None)
    # Issued against the ticket the order already carried, because `DEC-015` refuses to build a
    # customer to attach it to.
    assert row[2] == bound[0]


def test_a_late_delivery_credit_is_ten_percent_the_server_computed(
    connection: psycopg.Connection[Any],
) -> None:
    """Staff attest the fault and the lateness; the amount is not theirs to type."""

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
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.LATE_DELIVERY_CREDIT,
        store_fault_attested=True,
        attested_late_by_minutes=150,
    )
    assert proposal.amount_vnd == LATE_CREDIT
    assert proposal.status is RemedyStatus.STAFF_AUTHORIZED
    executed = _execute(connection, proposal.proposal_id, staff)
    assert executed.event_type == CREDIT_EXECUTED and executed.amount_vnd == LATE_CREDIT


def test_a_lost_item_is_recorded_and_refuses(connection: psycopg.Connection[Any]) -> None:
    """`DEC-004` carries loss forward as unresolved, so the record exists and nothing is paid.

    Deliberately asserts no ceiling, no window and no amount. There is no figure for loss, and a
    test that pinned one would be this repository ratifying a number the owner declined to give.
    """

    store_id, staff, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.LOST_ITEM,
        store_fault_attested=True,
    )
    assert proposal.outcome is PolicyOutcome.REQUIRE_HUMAN
    assert proposal.reason_code == RemedyRefusal.LOSS_POLICY_UNRESOLVED.value
    assert proposal.status is RemedyStatus.POLICY_UNRESOLVED
    assert (proposal.amount_vnd, proposal.ceiling_vnd, proposal.window_closes_at) == (
        None,
        None,
        None,
    )
    # The complaint is written down -- that is the outcome for a loss -- and the incident stays open
    # for review rather than being closed by a remedy nobody decided.
    assert _incident_row(connection, incident_id) == ("UNDER_REVIEW", True, False)

    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, proposal.proposal_id, staff)
    assert refused.value.reason_code == RemedyRefusal.LOSS_POLICY_UNRESOLVED.value
    assert refused.value.ceiling_vnd is None


# --- the escalation boundary, to the dong --------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        # The owner said staff may approve *up to* 100.000 d. At the figure itself, staff may.
        (100_000, RemedyStatus.STAFF_AUTHORIZED),
        # One dong above it, the owner must.
        (100_001, RemedyStatus.OWNER_APPROVAL_REQUIRED),
    ],
)
def test_the_staff_ceiling_is_inclusive_and_one_dong_above_it_needs_the_owner(
    connection: psycopg.Connection[Any], amount: int, expected: RemedyStatus
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=amount,
    )
    assert proposal.status is expected
    assert (proposal.approval_id is not None) == (expected is RemedyStatus.OWNER_APPROVAL_REQUIRED)


def test_a_remedy_above_the_staff_ceiling_cannot_be_executed_until_the_owner_approves(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=250_000,
    )
    assert proposal.approval_id is not None
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, proposal.proposal_id, staff)
    assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"

    _approve(connection, store_id, proposal.approval_id, NOW + timedelta(minutes=1))
    executed = _execute(connection, proposal.proposal_id, staff, NOW + timedelta(minutes=2))
    assert executed.event_type == CREDIT_EXECUTED and executed.amount_vnd == 250_000


def test_a_proposal_above_five_times_the_line_is_refused_with_the_ceiling_named(
    connection: psycopg.Connection[Any],
) -> None:
    """Refused, not truncated. Staff are told the bound so they can tell the customer."""

    store_id, staff, _, incident_id = _shop(connection)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=DAMAGE_CEILING + 1,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    # 5 x the 100.000 d this line was actually charged at.
    assert refused.value.ceiling_vnd == DAMAGE_CEILING
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM remedy_proposals")
        counted = cursor.fetchone()
    assert counted is not None and counted[0] == 0


# --- windows -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "allowed"),
    [
        # Inclusive at the far end: the owner published seven days, so the seventh day is inside.
        (7, True),
        (8, False),
    ],
)
def test_the_rewash_window_runs_seven_days_from_the_handover(
    connection: psycopg.Connection[Any], days: int, allowed: bool
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    request = dict(
        kind=RemedyKind.FREE_REWASH,
        store_fault_attested=True,
        proposed_at=NOW + timedelta(days=days),
    )
    if allowed:
        assert (
            _propose(connection, store_id, incident_id, staff, **request).status
            is RemedyStatus.STAFF_AUTHORIZED
        )
        return
    with pytest.raises(RemedyStateError) as refused:
        _propose(connection, store_id, incident_id, staff, **request)
    assert refused.value.reason_code == RemedyRefusal.REMEDY_WINDOW_CLOSED.value
    assert refused.value.window_closes_at == NOW + timedelta(days=7)


def test_the_defect_window_runs_twenty_four_hours_from_the_handover(
    connection: psycopg.Connection[Any],
) -> None:
    """A damage claim is a visible-defect report, and the owner gave it a day."""

    store_id, staff, _, incident_id = _shop(connection)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=10_000,
            proposed_at=NOW + timedelta(hours=25),
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_WINDOW_CLOSED.value
    assert refused.value.window_closes_at == NOW + timedelta(hours=24)


def test_an_order_with_no_recorded_handover_refuses_rather_than_measuring_from_now(
    connection: psycopg.Connection[Any],
) -> None:
    """Unknown means stop. `now` is not a substitute for a measurement nobody took."""

    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    _publish_policy(connection, staff)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff
    )
    order_id = (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                NOW,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )
    incident_id = _incident(connection, store_id, order_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.FREE_REWASH,
            store_fault_attested=True,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_WINDOW_EVIDENCE_MISSING.value


# --- fail closed ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        RemedyKind.FREE_REWASH,
        RemedyKind.DAMAGE_COMPENSATION,
        RemedyKind.LATE_DELIVERY_CREDIT,
        RemedyKind.LOST_ITEM,
    ],
)
def test_with_no_published_policy_every_remedy_fails_closed(
    connection: psycopg.Connection[Any], kind: RemedyKind
) -> None:
    """Invariant 11, including for the kinds that move no money: the windows are figures too."""

    store_id, staff, _, incident_id = _shop(connection, publish=False)
    extra: dict[str, Any] = {}
    if kind is RemedyKind.DAMAGE_COMPENSATION:
        extra = {"order_line_id": "line-1", "amount_vnd": 10_000}
    elif kind is RemedyKind.LATE_DELIVERY_CREDIT:
        extra = {"attested_late_by_minutes": 150}
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection, store_id, incident_id, staff, kind=kind, store_fault_attested=True, **extra
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_POLICY_UNPUBLISHED.value
    assert refused.value.authority == "INVARIANT-11"


def test_a_malformed_policy_is_refused_at_publication(
    connection: psycopg.Connection[Any],
) -> None:
    """A document that parses but loses a figure must fail where somebody can still fix it."""

    with pytest.raises(ConfigurationValidationError):
        validate_remedy_policy({**POLICY, "staff_approval_ceiling_vnd": "100000"})
    with pytest.raises(ConfigurationValidationError):
        validate_remedy_policy({key: value for key, value in POLICY.items() if key != "schema"})


def test_the_published_policy_is_read_back_with_its_provenance(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    with connection.cursor() as cursor:
        assert read_published_remedy_policy(cursor) is None
    _publish_policy(connection, staff)
    # Publishing the identical document again is a no-op, not a second version: every proposal
    # citing version 1 would otherwise start looking out of date for no reason.
    _publish_policy(connection, staff)
    with connection.cursor() as cursor:
        published = read_published_remedy_policy(cursor)
    assert published is not None
    assert published.version == 1
    assert published.policy.staff_approval_ceiling_vnd == 100_000
    assert published.policy.damage_compensation_multiple == 5
    assert published.policy.free_rewash_window_days == 7


# --- the settlement ledger is not the place a remedy is recorded ---------------------------------


def test_the_settlement_ledger_is_byte_identical_across_an_approved_remedy(
    connection: psycopg.Connection[Any],
) -> None:
    """An approved remedy creates a forward obligation; it does not rewrite what was paid.

    The ledger is append-only and `reject_ledger_mutation()` would refuse an edit anyway, so this
    test is not about whether the write would succeed -- it is about whether anything in this path
    tries. The whole row is compared, not just the amount.
    """

    store_id, staff, order_id, incident_id = _shop(connection)

    def ledger() -> list[tuple[Any, ...]]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_jsonb(s) FROM order_settlements s WHERE s.order_id = %s", (order_id,)
            )
            return [tuple(row) for row in cursor.fetchall()]

    before = ledger()
    assert before, "the fixture must have recorded a settlement to compare"

    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=300_000,
    )
    assert proposal.approval_id is not None
    _approve(connection, store_id, proposal.approval_id, NOW + timedelta(minutes=1))
    _execute(connection, proposal.proposal_id, staff, NOW + timedelta(minutes=2))

    assert ledger() == before


# --- the credit, and its single use ---------------------------------------------------------------


def _next_quote(connection: Any, store_id: UUID, staff: StaffPrincipal) -> tuple[UUID, int, str]:
    """A fresh open quote for this shop: the "next bill" a credit lands on."""

    request_id, quote_id = uuid4(), uuid4()
    contact_id = counter_ticket(connection, store_id=store_id, principal=staff)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO order_requests (
                id, store_id, contact_binding_id, conversation_binding_id, status, row_version,
                created_at
            ) VALUES (%s, %s, %s, %s, 'DRAFT', 1, %s)
            """,
            (request_id, store_id, contact_id, uuid4(), NOW),
        )
    snapshot = make_quote_snapshot(quote_id, 1)
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, request_id, snapshot, 0, 0, staff.staff_user_id, uuid4(), NOW
        ),
    )
    return quote_id, 1, snapshot.document.snapshot_hash


def _issued_credit(connection: Any) -> tuple[UUID, UUID, StaffPrincipal, int]:
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
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.LATE_DELIVERY_CREDIT,
        store_fault_attested=True,
        attested_late_by_minutes=150,
    )
    executed = _execute(connection, proposal.proposal_id, staff)
    assert executed.credit_id is not None
    return store_id, executed.credit_id, staff, LATE_CREDIT


def test_a_credit_lands_on_the_next_quote_and_is_redeemable_exactly_once(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, credit_id, staff, amount = _issued_credit(connection)
    quote_id, revision, snapshot_hash = _next_quote(connection, store_id, staff)

    redeemed = RemedyCreditRepository().redeem(
        connection,
        RemedyCreditRedemptionCommand(
            store_id=store_id,
            quote_id=quote_id,
            credit_id=credit_id,
            expected_current_revision=revision,
            expected_snapshot_hash=snapshot_hash,
            principal=staff,
            correlation_id=uuid4(),
            redeemed_at=NOW,
        ),
    )
    # 100.000 d of service less the 11.000 d credit, plus the 10.000 d delivery fee.
    assert redeemed.credit_vnd == amount
    assert redeemed.net_service_subtotal_vnd == LINE_AMOUNT - amount
    assert redeemed.display_total_vnd == LINE_AMOUNT - amount + 10_000
    assert redeemed.revision == 2

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT discount_amount_min_vnd, net_service_subtotal_min_vnd
            FROM quote_revisions WHERE quote_id = %s AND revision = 2
            """,
            (quote_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    # The order-level credit became a line-level discount, which is what the CHECK on
    # `quote_revisions` and `_validate_adjustments` both require.
    assert (int(row[0]), int(row[1])) == (amount, LINE_AMOUNT - amount)

    second_quote_id, second_revision, second_hash = _next_quote(connection, store_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        RemedyCreditRepository().redeem(
            connection,
            RemedyCreditRedemptionCommand(
                store_id=store_id,
                quote_id=second_quote_id,
                credit_id=credit_id,
                expected_current_revision=second_revision,
                expected_snapshot_hash=second_hash,
                principal=staff,
                correlation_id=uuid4(),
                redeemed_at=NOW,
            ),
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM quote_revisions WHERE quote_id = %s", (second_quote_id,)
        )
        counted = cursor.fetchone()
    # The refused redemption wrote nothing: the second quote still has only its first revision.
    assert counted is not None and counted[0] == 1


def test_a_credit_larger_than_the_next_bill_is_refused_rather_than_capped(
    connection: psycopg.Connection[Any],
) -> None:
    """Capping would cancel part of a debt the shop owes without anybody deciding to."""

    store_id, staff, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        # Inside the 5x ceiling and larger than the 100.000 d of service on the next bill.
        amount_vnd=400_000,
    )
    assert proposal.approval_id is not None
    _approve(connection, store_id, proposal.approval_id, NOW + timedelta(minutes=1))
    credit_id = _execute(
        connection, proposal.proposal_id, staff, NOW + timedelta(minutes=2)
    ).credit_id
    assert credit_id is not None

    quote_id, revision, snapshot_hash = _next_quote(connection, store_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        RemedyCreditRepository().redeem(
            connection,
            RemedyCreditRedemptionCommand(
                store_id=store_id,
                quote_id=quote_id,
                credit_id=credit_id,
                expected_current_revision=revision,
                expected_snapshot_hash=snapshot_hash,
                principal=staff,
                correlation_id=uuid4(),
                redeemed_at=NOW,
            ),
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE.value
    with connection.cursor() as cursor:
        cursor.execute("SELECT redeemed_at FROM remedy_credits WHERE id = %s", (credit_id,))
        unspent = cursor.fetchone()
    # Still owed in full.
    assert unspent is not None and unspent[0] is None


# --- refusals that protect the figures ------------------------------------------------------------


def test_a_remedy_without_a_store_fault_finding_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.FREE_REWASH,
            store_fault_attested=False,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_STORE_FAULT_NOT_ATTESTED.value


def test_a_late_delivery_credit_on_an_order_nobody_delivered_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    """A contradiction the record can check: the customer collected at the counter."""

    store_id, staff, _, incident_id = _shop(connection)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=150,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_DELIVERY_NOT_RECORDED.value


def test_lateness_below_the_published_threshold_is_refused_with_the_threshold_named(
    connection: psycopg.Connection[Any],
) -> None:
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
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            # Two hours exactly. The owner said "more than two hours".
            attested_late_by_minutes=120,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_LATENESS_BELOW_THRESHOLD.value
    assert refused.value.threshold_minutes == 120


def test_staff_may_not_type_an_amount_for_a_credit_the_server_computes(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, _, incident_id = _shop(connection, mode=FulfillmentMode.PICKUP_AND_RETURN)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=150,
            amount_vnd=999_000,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE.value


def test_damage_against_a_line_the_order_does_not_have_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    with pytest.raises(RemedyStateError) as refused:
        _propose(
            connection,
            store_id,
            incident_id,
            staff,
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-99",
            amount_vnd=10_000,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_LINE_NOT_PRICED.value


def test_a_member_of_another_store_cannot_propose_a_remedy(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, _, _, incident_id = _shop(connection)
    other_store = uuid4()
    StoreRepository.create(
        connection,
        store_id=other_store,
        name="Cửa hàng khác",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    outsider = _staff(connection, other_store, StaffRole.OPERATOR)
    with pytest.raises(PermissionError):
        _propose(
            connection,
            store_id,
            incident_id,
            outsider,
            kind=RemedyKind.FREE_REWASH,
            store_fault_attested=True,
        )


# --- what the form must know before anybody types anything ----------------------------------------


def test_the_options_read_names_the_ceiling_the_window_and_the_owner_threshold(
    connection: psycopg.Connection[Any],
) -> None:
    """Staff must never discover that the owner is required after filling the form in."""

    store_id, staff, _, incident_id = _shop(connection)
    with connection.cursor() as cursor:
        options = RemedyProposalRepository().options(
            cursor, store_id=store_id, incident_id=incident_id, principal=staff
        )
    assert options.policy_published
    assert options.staff_approval_ceiling_vnd == 100_000
    assert options.damage_line_ceilings_vnd == {"line-1": DAMAGE_CEILING}
    assert options.goods_returned_at == NOW
    assert options.rewash_window_closes_at == NOW + timedelta(days=7)
    assert options.rewash_window_open is True
    assert options.defect_window_closes_at == NOW + timedelta(hours=24)
    # No delivery happened, so there is no late-delivery credit to offer and the form must say so
    # rather than showing a zero.
    assert options.late_delivery_credit_vnd is None
    assert options.loss_reason_code == RemedyRefusal.LOSS_POLICY_UNRESOLVED.value


def test_the_options_read_fails_closed_with_no_published_policy(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, _, incident_id = _shop(connection, publish=False)
    with connection.cursor() as cursor:
        options = RemedyProposalRepository().options(
            cursor, store_id=store_id, incident_id=incident_id, principal=staff
        )
    assert options.policy_published is False
    assert options.staff_approval_ceiling_vnd is None
    assert options.damage_line_ceilings_vnd is None


#: 6 kg of standard wash on the far side of the `STD_WASH_DRY_GE6` cliff, at 20.000 d/kg.
PROMOTED_LIST_VND = 120_000
#: 30% of it, which is what the owner's confirmed document discounts `STANDARD_WASH_DRY` by.
PROMOTED_DISCOUNT_VND = 36_000


def _publish_live_programme(connection: Any, actor_id: UUID) -> Any:
    """The owner's confirmed document with its window moved over today, published for real.

    The window is moved rather than the document rewritten: `stacking_allowed: false` is the clause
    under test and it is the owner's, not this test's. The shipped window (17/07 - 31/08/2026) has
    already closed, and an expired programme never reaches the stacking question at all -- the
    engine answers `PROMOTION_OUTSIDE_INTERVAL` first -- so a test pinned to it would assert
    nothing.
    """

    payload = json.loads((ROOT / "templates" / "promotion-policy-dec-002.json").read_text("utf-8"))
    payload["start_at"] = (NOW - timedelta(days=1)).isoformat()
    payload["end_at_exclusive"] = (NOW + timedelta(days=30)).isoformat()
    publish_promotion_policy(connection, actor_id=actor_id, payload=payload)
    with connection.cursor() as cursor:
        published = read_published_promotion_program(cursor)
    assert published is not None
    assert published.program.policy.stacking_allowed is False
    return published


def _promoted_quote(
    connection: Any, store_id: UUID, staff: StaffPrincipal, published: Any
) -> tuple[UUID, Any, UUID]:
    """An open quote priced under a live programme, bound to a real customer, ready to be credited.

    Bound through a counter ticket and an `order_requests` row because the end of this test creates
    an order, and `OrderRepository.create` walks that chain to check the order's customer is the
    customer the quote was priced for.
    """

    contact_id = counter_ticket(connection, store_id=store_id, principal=staff)
    request_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO order_requests (
                id, store_id, contact_binding_id, conversation_binding_id, status, row_version,
                created_at
            ) VALUES (%s, %s, %s, %s, 'SUBMITTED', 1, %s)
            """,
            (request_id, store_id, contact_id, uuid4(), NOW),
        )
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    quote_id = uuid4()
    composition = compose_quote_revision(
        quote_id=quote_id,
        revision=1,
        rules=runtime_price_rules(pricebook),
        requested=(
            RequestedLine("STANDARD_WASH_DRY", "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),
        ),
        pricebook=PricebookProvenance(uuid4(), 1, pricebook.manifest.canonical_snapshot_hash),
        priced_at=NOW,
        # Walk-in, so the delivery fee resolves to zero and the display total exists. A quote whose
        # transport needs a human cannot be accepted at all, which would refuse this test for a
        # reason that has nothing to do with what it is about.
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        promotion=published,
    )
    assert isinstance(composition, ComposedQuote)
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, request_id, composition.snapshot, 0, 0, staff.staff_user_id, uuid4(), NOW
        ),
    )
    return quote_id, composition.snapshot, contact_id


def test_a_credit_beats_a_programme_that_forbids_stacking_and_the_sale_still_completes(
    connection: psycopg.Connection[Any],
) -> None:
    """`DEC-004` against the programme's own document, end to end, through every real write.

    This is the dead end PROMO-FIX-002 left and PROMO-FIX-003 closed. A quote priced under a live
    `stacking_allowed: false` programme carried a 36.000 d discount; the customer then presented a
    remedy credit; `RemedyCreditRepository.redeem` burnt the credit and wrote the credited revision
    in one transaction; and `accept_quote_revision` then refused the sale because the two
    instruments could not compound. The credit was spent, the order could not be created, and
    nothing in this system can un-burn a credit.

    The rule that closes it: **when a remedy credit and a non-stacking promotion meet, the credit
    wins.** The credit is a debt this shop owes this customer for a past failure of its own; the
    programme is an offer the shop chose to make. So the promotion is withdrawn where the two meet
    -- inside the composition the redemption writes, before any new total is read to anybody -- and
    the sale completes.

    The arithmetic, which is what "the credit wins" costs the shop:

    - 120.000 d of standard wash, 36.000 d off under the programme, 84.000 d at quote time.
    - the credit is 11.000 d, issued by `DEC-004` for a late return on an earlier order.
    - the promotion comes back out and the credit applies to the restored 120.000 d: 11.000 d off,
      **109.000 d to pay**, exactly what this customer would owe with no programme running.

    What must be true at the end is the whole point: the credit is burnt exactly once, the revision
    says why it carries no programme discount, and an order exists.
    """

    store_id, credit_id, staff, credit_vnd = _issued_credit(connection)
    assert credit_vnd == LATE_CREDIT
    published = _publish_live_programme(connection, staff.staff_user_id)
    quote_id, priced, contact_id = _promoted_quote(connection, store_id, staff, published)

    assert priced.data.totals.discount_amount_max_vnd == PROMOTED_DISCOUNT_VND
    assert priced.data.totals.display_total_max_vnd == PROMOTED_LIST_VND - PROMOTED_DISCOUNT_VND

    redeemed = RemedyCreditRepository().redeem(
        connection,
        RemedyCreditRedemptionCommand(
            store_id=store_id,
            quote_id=quote_id,
            credit_id=credit_id,
            expected_current_revision=1,
            expected_snapshot_hash=priced.document.snapshot_hash,
            principal=staff,
            correlation_id=uuid4(),
            redeemed_at=NOW,
        ),
    )
    # The credit applied and the programme did not: 120.000 - 11.000, not 84.000 - 11.000.
    assert redeemed.credit_vnd == LATE_CREDIT
    assert redeemed.net_service_subtotal_vnd == PROMOTED_LIST_VND - LATE_CREDIT
    assert redeemed.display_total_vnd == PROMOTED_LIST_VND - LATE_CREDIT

    with connection.cursor() as cursor:
        stored = QuoteRepository.get_revision(cursor, quote_id, 2)
    assert stored is not None
    credited = parse_quote_revision(json.loads(stored.document.canonical_json))
    assert PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN.value in credited.data.reason_codes
    assert PromotionReason.PROMOTION_APPLIED.value not in credited.data.reason_codes
    # The frozen trace agrees with the money rather than still claiming 36.000 d came off.
    withheld = frozen_promotion(credited)
    assert withheld is not None and withheld.discount_amount_vnd == 0

    accepted_at = NOW + timedelta(minutes=2)
    composition = accept_quote_revision(
        priced=credited, revision=3, promotion=published, accepted_at=accepted_at
    )
    assert isinstance(composition, ComposedQuote), "the credited quote has to be sellable"
    final = composition.snapshot
    assert final.data.totals.display_total_max_vnd == PROMOTED_LIST_VND - LATE_CREDIT
    assert PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN.value in final.data.reason_codes

    QuoteAcceptanceRepository().record(
        connection,
        QuoteAcceptanceCommand(
            store_id=store_id,
            quote_id=quote_id,
            accepted_revision=2,
            accepted_snapshot_hash=stored.document.snapshot_hash,
            final_revision=3,
            display_total_vnd=PROMOTED_LIST_VND - LATE_CREDIT,
            accepted_by=staff.staff_user_id,
            correlation_id=uuid4(),
            policy_version="quote-acceptance-dec-021-v1",
            accepted_at=accepted_at,
        ),
    )
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, uuid4(), final, 2, 2, staff.staff_user_id, uuid4(), accepted_at
        ),
    )
    order_id = (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                3,
                final.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                accepted_at,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT redeemed_at, redeemed_quote_id, redeemed_quote_revision FROM remedy_credits "
            "WHERE id = %s",
            (credit_id,),
        )
        burnt = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM orders WHERE id = %s", (order_id,))
        counted = cursor.fetchone()
    assert burnt is not None
    assert burnt[0] is not None and UUID(str(burnt[1])) == quote_id and int(burnt[2]) == 2
    assert counted is not None and counted[0] == 1

    # Exactly once. A second presentation of the same credit is refused, and nothing is written.
    second_quote_id, second_priced, _ = _promoted_quote(connection, store_id, staff, published)
    with pytest.raises(RemedyStateError) as refused:
        RemedyCreditRepository().redeem(
            connection,
            RemedyCreditRedemptionCommand(
                store_id=store_id,
                quote_id=second_quote_id,
                credit_id=credit_id,
                expected_current_revision=1,
                expected_snapshot_hash=second_priced.document.snapshot_hash,
                principal=staff,
                correlation_id=uuid4(),
                redeemed_at=NOW,
            ),
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value
