"""`RANGE-COUNTER-ATTEST-001`: one staff member alone closes a published band, under `DEC-029`.

`DEC-029` (2026-09-25, option B): the staff member on duty chooses the exact price inside a band the
owner published, under the same counter attestation `DEC-021` uses to finalise a quote --
attributed, immutable, no second signature, owner-reviewable. The server still refuses every figure
outside the band.

The decision request said the change was one line in `APPROVAL_POLICIES`. Measured, it was not
enough on its own: `_authorize_decision` refuses a requester deciding their own envelope for *every*
action, whatever the policy's obligations say, so on a one-person shift the envelope could be raised
and never decided. These tests hold the whole counter path to the ruling, with nobody else in the
shop:

* one `OPERATOR`, no owner assigned, prices an áo dài at 150.000 ₫, and it sells;
* 79.999 ₫ and 240.001 ₫ are still refused, with nothing written;
* the name against the number is on rows the database will not let anyone rewrite;
* the owner can read afterwards who chose which price -- over HTTP as well as in the service;
* the two-party path is untouched for every action that still needs the owner.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import (
    OperationsService,
    QuoteRevisionResult,
    RangePriceProposalResult,
    UnresolvedQuoteResult,
)
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.approvals import (
    ApprovalAttestationCommand,
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
)
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.quote_composition import RequestedLine
from nha_trang_laundry_domain.range_prices import RangePriceChoice

ROOT = Path(__file__).resolve().parents[3]
OWNER_SEED_ID = UUID("00000000-0000-0000-0000-0000000009c1")
AO_DAI = "DC_AO_DAI_TRADITIONAL"
#: Áo dài truyền thống, published 80.000-240.000 ₫, and the packet's own example inside it.
BAND_MINIMUM = 80_000
BAND_MAXIMUM = 240_000
CHOSEN = 150_000
CSRF = "z" * 40
ORIGIN = "http://testserver"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit: `OperationsService` opens its own connection and sees only committed rows."""

    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_database_url())
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _shop(connection: Any) -> UUID:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng một người trực",
        created_by=None,
        correlation_id=uuid4(),
    )
    return store_id


def _member(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    staff_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, OWNER_SEED_ID):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO NOTHING
                """,
                (identifier, f"oidc-{identifier}"),
            )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (uuid4(), staff_id, role.value),
        )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, 1)
            """,
            (staff_id, store_id, OWNER_SEED_ID),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _publish(connection: Any) -> None:
    publish_pricebook(
        connection,
        actor_id=OWNER_SEED_ID,
        source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
    )


def _contact(connection: Any) -> UUID:
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"range-counter-{uuid4().hex[:12]}",
        correlation_id=uuid4(),
    )
    return resolved.binding.contact_id


def _band_quote(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    bound_order_request_id: UUID | None = None,
) -> QuoteRevisionResult:
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=bound_order_request_id or uuid4(),
        lines=(RequestedLine(AO_DAI, "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
        present_range_as_band=True,
    )
    assert isinstance(priced, QuoteRevisionResult)
    assert priced.finality == "RANGE"
    return priced


def _propose(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    quote: QuoteRevisionResult,
    amount: int = CHOSEN,
) -> RangePriceProposalResult | UnresolvedQuoteResult:
    return service.propose_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=(RangePriceChoice(AO_DAI, amount),),
        idempotency_key=f"propose-{uuid4().hex}",
        principal=staff,
    )


def _apply(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    quote: QuoteRevisionResult,
    proposal: RangePriceProposalResult,
    amount: int = CHOSEN,
) -> QuoteRevisionResult | UnresolvedQuoteResult:
    return service.apply_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        approval_id=proposal.approval.approval_request_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=(RangePriceChoice(AO_DAI, amount),),
        idempotency_key=f"apply-{uuid4().hex}",
        principal=staff,
    )


def _bare_envelope(
    connection: Any,
    *,
    action: ApprovalAction,
    store_id: UUID,
    quote: QuoteRevisionResult,
    requested_by: StaffPrincipal,
) -> Any:
    """An envelope raised directly, without the proposal command around it."""

    return ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            action,
            APPROVAL_RESOURCE_TYPES[action],
            quote.quote_id,
            quote.revision,
            quote.snapshot_hash,
            "JCS-SHA256-V1:" + "0" * 64,
            "range-price-published-band-v1",
            requested_by.staff_user_id,
            f"bare-{uuid4().hex}",
            uuid4(),
            store_id=store_id,
        ),
    )


def _attest(connection: Any, envelope: Any, quote: QuoteRevisionResult, who: StaffPrincipal) -> Any:
    return ApprovalRepository().attest(
        connection,
        ApprovalAttestationCommand(
            approval_request_id=envelope.approval_request_id,
            observed_resource_version=quote.revision,
            observed_snapshot_hash=quote.snapshot_hash,
            observed_rendered_hash="JCS-SHA256-V1:" + "0" * 64,
            reason_code="RANGE_PRICE_COUNTER_ATTESTED",
            principal=who,
            correlation_id=uuid4(),
        ),
    )


def _rows(connection: Any, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


# --- a_price_inside_the_band_needs_no_owner ------------------------------------------------------


def test_one_staff_member_alone_prices_an_ao_dai_inside_the_band_and_sells_it(
    connection: Any, service: OperationsService
) -> None:
    """The ruling's reason for existing: a one-person shift, nobody else assigned to the shop.

    Under `_OWNER_FINANCIAL` this stopped at the proposal -- the envelope needed an `OWNER_ADMIN`
    who was not the proposer, inside ten minutes, and there was no such person.
    """

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    contact_id = _contact(connection)
    request = service.create_order_request(
        store_id=store_id,
        contact_binding_id=contact_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=staff,
    )
    banded = _band_quote(
        service, store_id=store_id, staff=staff, bound_order_request_id=request.order_request_id
    )

    before = datetime.now(UTC)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    # Attested in the same command that chose the number: nothing is waiting for anyone.
    assert proposal.approval.status == "APPROVED"
    assert proposal.approval.required_role.value == "OPERATOR"
    # `DEC-021`'s thirty minutes, not the owner's ten.
    assert proposal.approval.expires_at >= before + timedelta(minutes=29)
    assert proposal.approval.expires_at <= datetime.now(UTC) + timedelta(minutes=30)

    closed = _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert isinstance(closed, QuoteRevisionResult)
    assert closed.finality == "APPROVED_EXACT"
    assert closed.display_total_min_vnd == closed.display_total_max_vnd == CHOSEN

    accepted = service.accept_quote(
        store_id=store_id,
        quote_id=closed.quote_id,
        expected_current_revision=closed.revision,
        expected_snapshot_hash=closed.snapshot_hash,
        idempotency_key=f"accept-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(accepted, QuoteRevisionResult)
    assert accepted.status == "ACCEPTED_FINAL"
    order = service.create_order(
        store_id=store_id,
        bound_contact_id=contact_id,
        quote_id=accepted.quote_id,
        quote_revision=accepted.revision,
        quote_snapshot_hash=accepted.snapshot_hash,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        accepted_at=datetime.now(UTC),
        acquisition_source=AcquisitionSource.WALK_IN,
        idempotency_key=f"order-{uuid4().hex}",
        principal=staff,
    )
    version = order.row_version
    for intake in (IntakeStatus.RECEIVED_PENDING_INSPECTION, IntakeStatus.ACCEPTED):
        version = service.transition_intake(
            order_id=order.order_id,
            target=intake,
            expected_row_version=version,
            idempotency_key=f"step-{uuid4().hex}",
            principal=staff,
            slot_approved=True,
        ).row_version
    for commercial in (
        CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
        CommercialOrderStatus.CONFIRMED,
        CommercialOrderStatus.ACTIVE,
    ):
        version = service.transition_commercial(
            order_id=order.order_id,
            target=commercial,
            expected_row_version=version,
            idempotency_key=f"step-{uuid4().hex}",
            principal=staff,
        ).row_version
    for production in (
        ProductionStatus.QUEUED,
        ProductionStatus.IN_PROCESS,
        ProductionStatus.QUALITY_CHECK,
        ProductionStatus.READY_AT_STORE,
    ):
        version = service.transition_production(
            order_id=order.order_id,
            target=production,
            expected_row_version=version,
            idempotency_key=f"step-{uuid4().hex}",
            principal=staff,
        ).row_version

    settled = service.record_settlement(
        order_id=order.order_id,
        paid_amount_vnd=CHOSEN,
        collected_by_customer=True,
        idempotency_key=f"settle-{uuid4().hex}",
        principal=staff,
    )
    assert settled.expected_total_vnd == settled.paid_amount_vnd == CHOSEN
    assert settled.balance_status == "PAID"


def test_the_counter_flow_works_over_http_for_one_operator(
    connection: Any, client: TestClient
) -> None:
    """The same two presses the console makes, through the served routes."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(
        OperationsService(AuthSettings(database_url=_database_url())),
        store_id=store_id,
        staff=staff,
    )
    app.dependency_overrides[current_principal] = lambda: staff
    body = {
        "expected_current_revision": banded.revision,
        "expected_snapshot_hash": banded.snapshot_hash,
        "choices": [{"service_code": AO_DAI, "amount_vnd": CHOSEN}],
    }
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF}
    proposed = client.post(
        f"/internal/v1/stores/{store_id}/quotes/{banded.quote_id}/range-prices",
        headers={**headers, "Idempotency-Key": f"http-{uuid4().hex}"},
        json=body,
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["status"] == "APPROVED"
    assert proposed.json()["required_role"] == "OPERATOR"

    applied = client.post(
        f"/internal/v1/stores/{store_id}/quotes/{banded.quote_id}/range-prices/"
        f"{proposed.json()['approval_request_id']}",
        headers={**headers, "Idempotency-Key": f"http-{uuid4().hex}"},
        json=body,
    )
    assert applied.status_code == 201, applied.text
    assert applied.json()["finality"] == "APPROVED_EXACT"
    assert applied.json()["display_total_min_vnd"] == CHOSEN


# --- a_price_outside_the_band_is_still_refused ---------------------------------------------------


@pytest.mark.parametrize("amount", [BAND_MINIMUM - 1, BAND_MAXIMUM + 1, 250_000])
def test_a_price_outside_the_band_is_still_refused_with_nobody_else_on_shift(
    connection: Any, service: OperationsService, amount: int
) -> None:
    """Attestation replaced the second signature, not the bound. Nothing is raised or decided."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)

    refused = _propose(service, store_id=store_id, staff=staff, quote=banded, amount=amount)
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)
    assert _rows(
        connection,
        "SELECT count(*) FROM approval_requests WHERE resource_id = %s",
        banded.quote_id,
    ) == [(0,)]
    assert _rows(
        connection, "SELECT count(*) FROM quote_revisions WHERE quote_id = %s", banded.quote_id
    ) == [(1,)]


def test_both_ends_of_the_band_are_accepted_by_the_counter(
    connection: Any, service: OperationsService
) -> None:
    """The positive control for the refusal above: the bound is inclusive at both ends."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    for amount in (BAND_MINIMUM, BAND_MAXIMUM):
        banded = _band_quote(service, store_id=store_id, staff=staff)
        proposal = _propose(service, store_id=store_id, staff=staff, quote=banded, amount=amount)
        assert isinstance(proposal, RangePriceProposalResult)
        closed = _apply(
            service,
            store_id=store_id,
            staff=staff,
            quote=banded,
            proposal=proposal,
            amount=amount,
        )
        assert isinstance(closed, QuoteRevisionResult)
        assert closed.display_total_min_vnd == amount


def test_an_attested_price_cannot_be_applied_as_a_different_number(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 8 survives the ruling: the attestation binds a digest of 150.000 ₫, so applying
    160.000 ₫ -- also in band -- under it is refused and writes nothing."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)

    from nha_trang_laundry_db.quotes import QuoteStateError

    with pytest.raises(QuoteStateError):
        _apply(
            service,
            store_id=store_id,
            staff=staff,
            quote=banded,
            proposal=proposal,
            amount=160_000,
        )
    assert _rows(
        connection, "SELECT count(*) FROM quote_revisions WHERE quote_id = %s", banded.quote_id
    ) == [(1,)]


# --- the_choosing_staff_member_is_on_the_immutable_record ----------------------------------------


def test_the_choosing_staff_member_is_on_the_immutable_record(
    connection: Any, service: OperationsService
) -> None:
    """Attribution is the whole control now, so every row naming the chooser must be permanent."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    closed = _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert isinstance(closed, QuoteRevisionResult)
    approval_id = proposal.approval.approval_request_id

    assert _rows(
        connection, "SELECT requested_by FROM approval_requests WHERE id = %s", approval_id
    ) == [(staff.staff_user_id,)]
    assert _rows(
        connection,
        """
        SELECT decision, decided_by, reason_code, decision_type
        FROM approval_decisions WHERE approval_request_id = %s
        """,
        approval_id,
    ) == [("APPROVED", staff.staff_user_id, "RANGE_PRICE_COUNTER_ATTESTED", "SET_RANGE_PRICE")]
    assert _rows(
        connection,
        """
        SELECT p.proposed_by, a.proposed_amount_vnd, a.band_minimum_vnd, a.band_maximum_vnd
        FROM range_price_proposals p
        JOIN range_price_proposal_amounts a ON a.approval_id = p.approval_id
        WHERE p.approval_id = %s
        """,
        approval_id,
    ) == [(staff.staff_user_id, CHOSEN, BAND_MINIMUM, BAND_MAXIMUM)]
    assert _rows(
        connection,
        """
        SELECT action, actor_id FROM audit_events
        WHERE aggregate_type = 'APPROVAL' AND aggregate_id = %s
        ORDER BY action
        """,
        approval_id,
    ) == [
        ("APPROVAL_DECIDE", staff.staff_user_id),
        ("APPROVAL_REQUEST", staff.staff_user_id),
    ]
    # The quote revision that carries the price names who wrote it, and which attestation.
    assert _rows(
        connection,
        "SELECT created_by, approval_id FROM quote_revisions WHERE quote_id = %s AND revision = %s",
        closed.quote_id,
        closed.revision,
    ) == [(staff.staff_user_id, approval_id)]

    # Immutable: the name against the number cannot be swapped for another afterwards.
    other = _member(connection, store_id, StaffRole.OPERATOR)
    for statement in (
        "UPDATE approval_decisions SET decided_by = %s WHERE approval_request_id = %s",
        "UPDATE range_price_proposals SET proposed_by = %s WHERE approval_id = %s",
        "UPDATE approval_requests SET requested_by = %s WHERE id = %s",
    ):
        with pytest.raises(psycopg.Error), connection.transaction():
            connection.execute(statement, (other.staff_user_id, approval_id))


def test_the_owner_can_review_who_chose_which_price(
    connection: Any, service: OperationsService, client: TestClient
) -> None:
    """`DEC-021`'s third control, owner review, over the existing read -- after the fact."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    assert isinstance(
        _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal),
        QuoteRevisionResult,
    )

    record = service.read_range_price_proposal(
        approval_id=proposal.approval.approval_request_id, principal=owner
    )
    assert record is not None
    assert record.proposed_by == staff.staff_user_id
    assert record.lines[0].proposed_amount_vnd == CHOSEN

    app.dependency_overrides[current_principal] = lambda: owner
    read = client.get(
        f"/internal/v1/approvals/{proposal.approval.approval_request_id}/range-price-proposal"
    )
    assert read.status_code == 200, read.text
    assert read.json()["proposed_by"] == str(staff.staff_user_id)
    assert read.json()["lines"][0]["proposed_amount_vnd"] == CHOSEN


def test_a_counter_attestation_never_waits_in_the_owners_queue(
    connection: Any, service: OperationsService
) -> None:
    """There is nothing for the owner to decide, so nothing is put in front of them to decide."""

    _publish(connection)
    store_id = _shop(connection)
    staff = _member(connection, store_id, StaffRole.OPERATOR)
    owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)

    queued = service.list_pending_approvals(principal=owner, limit=100)
    assert proposal.approval.approval_request_id not in {
        item.approval_request_id for item in queued
    }


# --- the attestation is narrow -------------------------------------------------------------------


def test_only_the_staff_member_who_chose_the_price_can_attest_it(
    connection: Any, service: OperationsService
) -> None:
    """An attestation is the chooser's own statement. A colleague cannot make it on their behalf,
    or the name on the record would not be the person who chose the number."""

    _publish(connection)
    store_id = _shop(connection)
    chooser = _member(connection, store_id, StaffRole.OPERATOR)
    colleague = _member(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=chooser)
    envelope = _bare_envelope(
        connection,
        action=ApprovalAction.SET_RANGE_PRICE,
        store_id=store_id,
        quote=banded,
        requested_by=chooser,
    )
    assert envelope.status == "REQUESTED"

    with pytest.raises(ApprovalAuthorizationError):
        _attest(connection, envelope, banded, colleague)
    assert _attest(connection, envelope, banded, chooser).status == "APPROVED"


def test_an_owner_financial_envelope_cannot_be_self_attested(
    connection: Any, service: OperationsService
) -> None:
    """`DEC-029` moved one action. `APPLY_PROMOTION` still needs a second person, and the counter
    attestation refuses it even for an owner -- the capability stored on the envelope decides."""

    _publish(connection)
    store_id = _shop(connection)
    owner = _member(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=owner)
    envelope = _bare_envelope(
        connection,
        action=ApprovalAction.APPLY_PROMOTION,
        store_id=store_id,
        quote=banded,
        requested_by=owner,
    )
    assert envelope.required_role.value == "OWNER_ADMIN"

    with pytest.raises(ApprovalAuthorizationError):
        _attest(connection, envelope, banded, owner)
    # And the two-party path still refuses the requester, unchanged by this item.
    with pytest.raises(ApprovalAuthorizationError):
        ApprovalRepository().decide(
            connection,
            ApprovalDecisionCommand(
                approval_request_id=envelope.approval_request_id,
                decision=ApprovalDecision.APPROVED,
                observed_resource_version=banded.revision,
                observed_snapshot_hash=banded.snapshot_hash,
                observed_rendered_hash="JCS-SHA256-V1:" + "0" * 64,
                reason_code="SELF_APPROVAL_ATTEMPT",
                principal=owner,
                correlation_id=uuid4(),
            ),
        )


def test_another_stores_member_cannot_attest_this_shops_envelope(
    connection: Any, service: OperationsService
) -> None:
    """Membership comes from the envelope's own row, never from the caller."""

    _publish(connection)
    store_id = _shop(connection)
    chooser = _member(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=chooser)
    envelope = _bare_envelope(
        connection,
        action=ApprovalAction.SET_RANGE_PRICE,
        store_id=store_id,
        quote=banded,
        requested_by=chooser,
    )
    elsewhere = _member(connection, _shop(connection), StaffRole.OWNER_ADMIN)
    with pytest.raises(ApprovalAuthorizationError):
        _attest(connection, envelope, banded, elsewhere)
