"""Live evidence that a published price band can be closed, and only the right way.

`RANGE-PRICE-001`. Forty-five per cent of the published catalogue was unsellable: twenty services
carry a band rather than a rate and `compose_quote_revision` refused every one of them. These tests
run the whole path against real PostgreSQL -- band, proposal, owner approval, application,
acceptance, order, settlement -- and then run each way of getting it wrong.

The negative cases carry most of the weight, for the reason `test_quote_command.py` states about its
own: a path that works is not the property under test. The property is that the server owns the
bound and nothing routes around it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.operations import (
    OperationsService,
    QuoteRevisionResult,
    RangePriceProposalResult,
    UnresolvedQuoteResult,
)
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.approvals import ApprovalDecision
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.promotions import publish_promotion_policy
from nha_trang_laundry_db.quotes import QuoteStateError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.quote_composition import (
    PROMOTION_NOT_PUBLISHED,
    PROMOTION_PUBLISHED_SINCE_APPROVAL,
    RequestedLine,
)
from nha_trang_laundry_domain.range_prices import RangePriceChoice

ROOT = Path(__file__).resolve().parents[3]
OWNER_SEED_ID = UUID("00000000-0000-0000-0000-0000000009c1")
AO_DAI = "DC_AO_DAI_TRADITIONAL"
STANDARD = "STANDARD_WASH_DRY"
#: Áo dài truyền thống, published 80.000-240.000 ₫. The number a staff member chooses in the middle
#: of it is the whole point of the item, and 150.000 ₫ is the packet's own example.
BAND_MINIMUM = 80_000
BAND_MAXIMUM = 240_000
CHOSEN = 150_000
#: The floor the republication test raises áo dài to, so 150.000 ₫ falls outside the new band.
NARROWED_MINIMUM = 200_000
#: Dry cleaning is the 40% arm of the owner's confirmed programme, so 150.000 d chosen inside the
#: published band gives 60.000 d off. Held here rather than written inline so the one arithmetic
#: fact this file asserts about a promotion is stated once, next to the band it is taken from.
DRY_CLEAN_DISCOUNT_VND = 60_000


def _publish_live_programme(connection: Any) -> None:
    """The owner starts a programme, which is one published document and nothing else.

    Anchored to the wall clock rather than to a calendar date because the shipped programme ended on
    31/08/2026: a window pinned to July 2026 would quietly stop being live and these tests would
    stop exercising the case they are named for.
    """

    payload = deepcopy(
        json.loads(
            (ROOT / "templates" / "promotion-policy-dec-002.json").read_text(encoding="utf-8")
        )
    )
    now = datetime.now(UTC)
    payload["start_at"] = (now - timedelta(days=1)).isoformat()
    payload["end_at_exclusive"] = (now + timedelta(days=30)).isoformat()
    publish_promotion_policy(connection, actor_id=OWNER_SEED_ID, payload=payload)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit, for the reason `test_quote_command.py` gives: `OperationsService` opens its own
    connection and sees only committed rows."""

    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _staff(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    return _extra_staff(connection, store_id, role)


def _extra_staff(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    """Another member of a store that already exists -- the owner who approves, for instance."""

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
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, 1)
            ON CONFLICT DO NOTHING
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


def _publish_narrowed_band(connection: Any) -> None:
    """Publish the same catalogue with áo dài's floor raised, so a genuinely new version exists.

    Only the one number changes. `import_pricebook_csv` pins the service and rule counts, so adding
    or removing a row would be refused and the test would prove something else.
    """

    source = (ROOT / "templates/services-pricebook.csv").read_text(encoding="utf-8")
    narrowed = source.replace(
        f'{AO_DAI},dry_cleaning,"Áo dài truyền thống",bộ,{BAND_MINIMUM},{BAND_MAXIMUM},',
        f'{AO_DAI},dry_cleaning,"Áo dài truyền thống",bộ,{NARROWED_MINIMUM},{BAND_MAXIMUM},',
    )
    assert narrowed != source, "the pricebook row this test edits has changed shape"
    publish_pricebook(connection, actor_id=OWNER_SEED_ID, source=narrowed.encode("utf-8"))


def _contact(connection: Any) -> UUID:
    """A binding recorded through the only legitimate source: the channel envelope path."""

    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"range-price-{uuid4().hex[:12]}",
        correlation_id=uuid4(),
    )
    return resolved.binding.contact_id


def _band_quote(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    bound_order_request_id: UUID,
    service_code: str = AO_DAI,
    unit: Unit = Unit.SET,
    quantity: str = "1",
) -> QuoteRevisionResult:
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=bound_order_request_id,
        lines=(RequestedLine(service_code, quantity, unit, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
        present_range_as_band=True,
    )
    assert isinstance(priced, QuoteRevisionResult)
    return priced


def _propose(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    quote: QuoteRevisionResult,
    amount: int = CHOSEN,
    service_code: str = AO_DAI,
) -> RangePriceProposalResult | UnresolvedQuoteResult:
    return service.propose_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=(RangePriceChoice(service_code, amount),),
        idempotency_key=f"propose-{uuid4().hex}",
        principal=staff,
    )


def _approve(
    service: OperationsService, *, proposal: RangePriceProposalResult, owner: StaffPrincipal
) -> None:
    decided = service.decide_approval(
        approval_id=proposal.approval.approval_request_id,
        decision=ApprovalDecision.APPROVED,
        resource_version=proposal.resource_version,
        snapshot_hash=proposal.snapshot_hash,
        rendered_hash=proposal.rendered_hash,
        reason_code="RANGE_PRICE_IN_PUBLISHED_BAND",
        note=None,
        idempotency_key=f"decide-{uuid4().hex}",
        principal=owner,
    )
    assert decided.status == "APPROVED"


def _apply(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    quote: QuoteRevisionResult,
    proposal: RangePriceProposalResult,
    amount: int = CHOSEN,
    service_code: str = AO_DAI,
) -> QuoteRevisionResult | UnresolvedQuoteResult:
    return service.apply_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        approval_id=proposal.approval.approval_request_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=(RangePriceChoice(service_code, amount),),
        idempotency_key=f"apply-{uuid4().hex}",
        principal=staff,
    )


def _advance(
    service: OperationsService,
    order_id: UUID,
    staff: StaffPrincipal,
    version: int,
    step: dict[str, Any],
) -> int:
    """One intake or commercial transition, through the service the console calls."""

    if "intake_target" in step:
        stored = service.transition_intake(
            order_id=order_id,
            target=step["intake_target"],
            expected_row_version=version,
            idempotency_key=f"step-{uuid4().hex}",
            principal=staff,
            slot_approved=True,
        )
    else:
        stored = service.transition_commercial(
            order_id=order_id,
            target=step["commercial_target"],
            expected_row_version=version,
            idempotency_key=f"step-{uuid4().hex}",
            principal=staff,
        )
    return stored.row_version


def _revision_count(connection: Any, quote_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM quote_revisions WHERE quote_id = %s", (quote_id,))
        return int(cursor.fetchone()[0])


def _approval_count(connection: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM approval_requests WHERE action = %s",
            (ApprovalAction.SET_RANGE_PRICE.value,),
        )
        return int(cursor.fetchone()[0])


# --- the whole path ----------------------------------------------------------------------------


def test_an_ao_dai_is_banded_priced_approved_accepted_ordered_and_settled(
    connection: Any, service: OperationsService
) -> None:
    """The item's reason for existing, end to end against the real database.

    Áo dài truyền thống at 150.000 ₫: the band is stored and shown, a staff member chooses inside
    it, the owner approves that exact content, the amount becomes the price, the customer agrees,
    the order is created and the counter takes 150.000 ₫ through the existing exact-payment path.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    contact_id = _contact(connection)
    request = service.create_order_request(
        store_id=store_id,
        contact_binding_id=contact_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=staff,
    )

    banded = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        bound_order_request_id=request.order_request_id,
    )
    assert banded.finality == "RANGE"
    assert (banded.display_total_min_vnd, banded.display_total_max_vnd) == (
        BAND_MINIMUM,
        BAND_MAXIMUM,
    )
    assert "RANGE_PRICE_REQUIRES_HUMAN" in banded.reason_codes

    # The console renders the bound from this read, not from a band it was sent.
    view = service.read_quote(store_id=store_id, quote_id=banded.quote_id, principal=staff)
    assert [line.price_kind for line in view.lines] == ["RANGE"]
    assert (view.lines[0].band_minimum_vnd, view.lines[0].band_maximum_vnd) == (
        BAND_MINIMUM,
        BAND_MAXIMUM,
    )
    assert view.lines[0].net_amount_vnd is None

    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    assert proposal.approval.status == "REQUESTED"
    assert proposal.approval.required_role.value == "OWNER_ADMIN"
    _approve(service, proposal=proposal, owner=owner)

    closed = _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert isinstance(closed, QuoteRevisionResult)
    assert closed.finality == "APPROVED_EXACT"
    assert closed.status == "APPROVED"
    assert closed.revision == banded.revision + 1
    assert closed.display_total_min_vnd == closed.display_total_max_vnd == CHOSEN
    assert "RANGE_PRICE_REQUIRES_HUMAN" not in closed.reason_codes

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT approval_id FROM quote_revisions WHERE quote_id = %s AND revision = %s",
            (closed.quote_id, closed.revision),
        )
        stored_approval = cursor.fetchone()[0]
    assert UUID(str(stored_approval)) == proposal.approval.approval_request_id

    closed_view = service.read_quote(store_id=store_id, quote_id=banded.quote_id, principal=staff)
    assert closed_view.lines[0].price_kind == "EXACT"
    assert closed_view.lines[0].net_amount_vnd == CHOSEN

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
    assert accepted.finality == "APPROVED_EXACT"
    assert accepted.display_total_min_vnd == CHOSEN

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
    # The laundry is taken in and the order is confirmed, through the same transitions
    # `SETTLEMENT-001` walks. Nothing here is specific to a range price; it is what has to be true
    # before a counter can take money for any order.
    version = order.row_version
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {"intake_target": IntakeStatus.ACCEPTED},
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
    ):
        version = _advance(service, order.order_id, staff, version, step)

    settlement = service.record_settlement(
        order_id=order.order_id,
        paid_amount_vnd=CHOSEN,
        collected_by_customer=True,
        idempotency_key=f"settle-{uuid4().hex}",
        principal=staff,
    )
    assert settlement.expected_total_vnd == CHOSEN
    assert settlement.paid_amount_vnd == CHOSEN
    assert settlement.balance_status == "PAID"


# --- the server owns the bound -------------------------------------------------------------------


@pytest.mark.parametrize("amount", [250_000, 79_999])
def test_an_out_of_band_amount_is_refused_and_writes_nothing(
    connection: Any, service: OperationsService, amount: int
) -> None:
    """250.000 ₫ and 79.999 ₫ are outside 80.000-240.000 ₫. Neither produces an approval to decide,
    a revision to accept, or anything for a later replay to resurrect."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    before = _approval_count(connection)

    refused = _propose(service, store_id=store_id, staff=staff, quote=banded, amount=amount)
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)
    assert _approval_count(connection) == before
    assert _revision_count(connection, banded.quote_id) == 1


def test_an_amount_for_an_exactly_priced_service_is_refused(
    connection: Any, service: OperationsService
) -> None:
    """`STD_WASH_DRY_LT6` is published at a rate, not a band. Naming it here would be a staff
    member overwriting a published price rather than choosing inside one."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    refused = _propose(
        service,
        store_id=store_id,
        staff=staff,
        quote=banded,
        service_code="STD_WASH_DRY_LT6",
        amount=100_000,
    )
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_NOT_APPLICABLE",)


def test_a_quote_with_no_band_cannot_be_range_priced(
    connection: Any, service: OperationsService
) -> None:
    """Six kilograms of ordinary washing has a price already. There is nothing to choose."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine(STANDARD, "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    refused = _propose(service, store_id=store_id, staff=staff, quote=priced, service_code=STANDARD)
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_NOT_APPLICABLE",)


def test_a_range_service_is_still_refused_when_no_band_was_asked_for(
    connection: Any, service: OperationsService
) -> None:
    """The default did not change. A caller that wants an exact price and names a band-priced
    service is told a human has to close it, exactly as before this item."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    refused = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine(AO_DAI, "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_REQUIRES_HUMAN",)


# --- what the approval binds ----------------------------------------------------------------------


def test_an_unapproved_proposal_cannot_be_applied(
    connection: Any, service: OperationsService
) -> None:
    """A raised envelope is a request, not a permission. Nothing is written until a
    second person decides it."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    with pytest.raises(QuoteStateError):
        _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert _revision_count(connection, banded.quote_id) == 1


def test_a_different_amount_cannot_be_applied_under_an_approval_for_this_one(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 8. The owner approved a digest of 150.000 ₫; 160.000 ₫ hashes to something else,
    and it is in band, so the bound alone would have let it through."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)

    with pytest.raises(QuoteStateError):
        _apply(
            service,
            store_id=store_id,
            staff=staff,
            quote=banded,
            proposal=proposal,
            amount=160_000,
        )
    assert _revision_count(connection, banded.quote_id) == 1


def test_editing_a_line_after_approval_invalidates_the_approval(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 8, in the shape a counter actually meets it: the owner approves 150.000 ₫ for one
    áo dài, then the customer adds a second garment. The approval named revision 1 and its digest;
    repricing makes revision 2 current, and the approved amount is not carried across to it."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    bound_request = uuid4()
    banded = _band_quote(
        service, store_id=store_id, staff=staff, bound_order_request_id=bound_request
    )
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)

    repriced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=bound_request,
        lines=(
            RequestedLine(AO_DAI, "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),
            RequestedLine("DC_SUIT", "1", Unit.ITEM, QuantityBasis.STAFF_MEASUREMENT),
        ),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
        present_range_as_band=True,
        quote_id=banded.quote_id,
        expected_current_revision=banded.revision,
        expected_row_version=banded.row_version,
    )
    assert isinstance(repriced, QuoteRevisionResult)
    assert repriced.revision == 2

    # The stale binding: revision 1 and its digest.
    with pytest.raises(QuoteStateError):
        _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    # And the approval does not transfer to the new revision either, because it names the old one.
    with pytest.raises(QuoteStateError):
        _apply(service, store_id=store_id, staff=staff, quote=repriced, proposal=proposal)
    assert _revision_count(connection, banded.quote_id) == 2


def test_an_approval_cannot_close_the_same_band_twice(
    connection: Any, service: OperationsService
) -> None:
    """One envelope authorises one amount on one revision. Applying it moves the quote past the
    revision it named, so a replay with a fresh key finds nothing to act on."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)
    assert isinstance(
        _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal),
        QuoteRevisionResult,
    )
    with pytest.raises(QuoteStateError):
        _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert _revision_count(connection, banded.quote_id) == 2


def test_the_staff_member_who_proposed_a_price_cannot_approve_it(
    connection: Any, service: OperationsService
) -> None:
    """`SET_RANGE_PRICE` maps to `_OWNER_FINANCIAL`, whose obligations include SEPARATION_OF_DUTY.
    This item did not retune that table and this test is what proves it did not."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    with pytest.raises(PermissionError):
        _approve(service, proposal=proposal, owner=staff)


def test_an_operator_cannot_approve_a_financial_action(
    connection: Any, service: OperationsService
) -> None:
    """Who may approve a financial action is owner policy, raised for decision in
    `docs/DECISION_REQUEST_RANGE_PRICE_AUTHORITY_2026-09.md` and not decided here."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    other = _extra_staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    with pytest.raises(PermissionError):
        _approve(service, proposal=proposal, owner=other)


def test_another_stores_member_can_neither_propose_nor_read(
    connection: Any, service: OperationsService
) -> None:
    """Membership is checked before anything is read, as on every other quote command."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    stranger = _staff(connection, uuid4(), StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    with pytest.raises(PermissionError):
        _propose(service, store_id=store_id, staff=stranger, quote=banded)
    with pytest.raises(PermissionError):
        service.read_quote(store_id=store_id, quote_id=banded.quote_id, principal=stranger)


def test_a_band_republished_narrower_does_not_move_the_price_already_offered(
    connection: Any, service: OperationsService
) -> None:
    """The band that authorises an amount is the one the customer was shown.

    This is `RANGE_PRICE_PRICEBOOK_MISMATCH` in its live form. The owner approves 150.000 ₫ against
    a revision priced from a pricebook publishing 80.000-240.000 ₫, and the pricebook is then
    republished with that band narrowed to 200.000-240.000 ₫. 150.000 ₫ is outside the *new* band
    and inside the one the customer was read, and the customer was read the old one -- so the amount
    still applies. A path that re-read the current pricebook would refuse it, and the shop would
    have to go back to a customer who had already agreed.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    assert banded.display_total_min_vnd == BAND_MINIMUM
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)

    _publish_narrowed_band(connection)
    closed = _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert isinstance(closed, QuoteRevisionResult)
    assert closed.display_total_min_vnd == CHOSEN

    # And the new band is what a quote priced *after* the republication gets, so the test is not
    # passing because the republication did nothing.
    reissued = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    assert reissued.display_total_min_vnd == NARROWED_MINIMUM


# --- PROMO-FIX-001: a programme published while the band was open -------------------------------


def test_a_programme_published_after_the_band_was_approved_sends_the_price_back(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 8, through the real command path, on the side a matching hash does not cover.

    The band is quoted and the amount approved while nothing is published, so the owner signed a
    `SET_RANGE_PRICE` envelope for 150.000 d with no promotion in view. The owner then starts a
    programme -- by publishing a document, which is the whole of what starting one takes -- and the
    staff member presses apply. Dry cleaning is the 40% arm, so applying it would turn that same
    signature into an authorisation to charge 90.000 d, a 60.000 d move, while the envelope's bound
    `rendered_hash` and revision both still matched perfectly. The binding would hold textually and
    mean nothing.

    So the apply refuses and nothing is written. `PROMO-FIX-001` decided the opposite -- that the
    programme applies, because a band is an authorisation rather than a price -- and the reasoning
    was sound about the customer, who had been read no number, but missed the owner, who had signed
    one.

    The refusal costs a re-quote, a proposal and an approval, which the test below spends and which
    the shop already makes for every range-priced garment. That is what separates this from an
    unconfirmed promotion target, where no screen that could answer exists at all.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    quote = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    assert PROMOTION_NOT_PUBLISHED in quote.reason_codes
    assert quote.promotion is None

    proposal = _propose(service, store_id=store_id, staff=staff, quote=quote)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)
    _publish_live_programme(connection)

    refused = _apply(service, store_id=store_id, staff=staff, quote=quote, proposal=proposal)
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == (PROMOTION_PUBLISHED_SINCE_APPROVAL,)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM quote_revisions WHERE quote_id = %s", (quote.quote_id,)
        )
        row = cursor.fetchone()
    # One revision: the band. The refusal wrote nothing, so there is no closed price carrying a
    # discount the owner never authorised.
    assert row is not None and row[0] == 1


def test_re_proposing_the_band_under_the_new_programme_prices_it_and_sells(
    connection: Any, service: OperationsService
) -> None:
    """The discharge path the refusal above depends on, walked with real presses.

    The bag is quoted again now that the programme is published, so the band revision cites it and
    the owner approves an amount against a document that already shows it. The close then applies
    40% of the 150.000 d chosen inside the published 80.000-240.000 band: 60.000 d off, 90.000 d to
    pay, with the programme named on the revision that took the money.

    And the closed revision is still acceptable. `accept_quote_revision` re-verifies the same
    programme version against the real `accepted_at`, which is the rule that guards every other
    quote, so the customer agrees to the discounted number rather than to a band.
    """

    _publish(connection)
    _publish_live_programme(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    quote = _band_quote(service, store_id=store_id, staff=staff, bound_order_request_id=uuid4())
    assert PROMOTION_NOT_PUBLISHED not in quote.reason_codes

    proposal = _propose(service, store_id=store_id, staff=staff, quote=quote)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)
    closed = _apply(service, store_id=store_id, staff=staff, quote=quote, proposal=proposal)
    assert isinstance(closed, QuoteRevisionResult)

    assert closed.list_service_subtotal_vnd == CHOSEN
    assert closed.net_service_subtotal_vnd == CHOSEN - DRY_CLEAN_DISCOUNT_VND
    assert closed.display_total_min_vnd == CHOSEN - DRY_CLEAN_DISCOUNT_VND
    assert closed.promotion is not None
    assert closed.promotion.policy_code == "PROMO_WET30_DRY40_20260717_20260831"
    assert closed.promotion.configuration_version == 1
    assert closed.promotion.discount_amount_vnd == DRY_CLEAN_DISCOUNT_VND

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
    assert accepted.display_total_min_vnd == CHOSEN - DRY_CLEAN_DISCOUNT_VND
    assert accepted.promotion is not None
    assert accepted.promotion.eligibility_resolved is True
    assert accepted.promotion.status == "ELIGIBLE"
