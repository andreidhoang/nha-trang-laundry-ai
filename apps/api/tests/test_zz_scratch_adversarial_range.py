"""SCRATCH adversarial review tests for RANGE-PRICE-001. Deleted after the review."""

from __future__ import annotations

import csv
import os
from collections.abc import Generator
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
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    FulfillmentMode,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.quote_composition import RequestedLine
from nha_trang_laundry_domain.range_prices import RangePriceChoice

ROOT = Path(__file__).resolve().parents[3]
OWNER_SEED_ID = UUID("00000000-0000-0000-0000-0000000009c1")

UNITS = {
    "kg": Unit.KG,
    "cái": Unit.ITEM,
    "đôi": Unit.PAIR,
    "bộ": Unit.SET,
    "con": Unit.ANIMAL_PLUSH_ITEM,
    "m2": Unit.M2,
    "trường hợp": Unit.CASE,
}


def _range_services() -> list[tuple[str, Unit, int, int]]:
    rows = list(csv.DictReader((ROOT / "templates/services-pricebook.csv").open(encoding="utf-8")))
    return [
        (r["service_id"], UNITS[r["unit"]], int(r["min_price_vnd"]), int(r["max_price_vnd"]))
        for r in rows
        if r["min_price_vnd"] != r["max_price_vnd"]
    ]


RANGE_SERVICES = _range_services()


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL required")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _staff(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    StoreRepository.create(
        connection, store_id=store_id, name="Scratch", created_by=None, correlation_id=uuid4()
    )
    return _extra_staff(connection, store_id, role)


def _extra_staff(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    staff_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, OWNER_SEED_ID):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'NV', 'ACTIVE', CURRENT_TIMESTAMP)
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


def _publish(connection: Any, source: bytes | None = None) -> None:
    publish_pricebook(
        connection,
        actor_id=OWNER_SEED_ID,
        source=source or (ROOT / "templates/services-pricebook.csv").read_bytes(),
    )


def _contact(connection: Any) -> UUID:
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"scratch-{uuid4().hex[:12]}",
        correlation_id=uuid4(),
    )
    return resolved.binding.contact_id


def _band_quote(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    lines: tuple[RequestedLine, ...],
    bound: UUID | None = None,
) -> QuoteRevisionResult:
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=bound or uuid4(),
        lines=lines,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"q-{uuid4().hex}",
        principal=staff,
        present_range_as_band=True,
    )
    assert isinstance(priced, QuoteRevisionResult), priced
    return priced


def _propose(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    quote: QuoteRevisionResult,
    choices: tuple[RangePriceChoice, ...],
) -> RangePriceProposalResult | UnresolvedQuoteResult:
    return service.propose_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=choices,
        idempotency_key=f"p-{uuid4().hex}",
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
        idempotency_key=f"d-{uuid4().hex}",
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
    choices: tuple[RangePriceChoice, ...],
) -> QuoteRevisionResult | UnresolvedQuoteResult:
    return service.apply_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        approval_id=proposal.approval.approval_request_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=choices,
        idempotency_key=f"a-{uuid4().hex}",
        principal=staff,
    )


@pytest.fixture
def world(connection: Any, service: OperationsService) -> dict[str, Any]:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    return {"store_id": store_id, "staff": staff, "owner": owner}


# --------------------------------------------------------------------------------------------
# 1. Boundary sweep across all twenty range services.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code,unit,low,high", RANGE_SERVICES, ids=[s[0] for s in RANGE_SERVICES])
def test_band_boundaries_for_every_range_service(
    connection: Any, service: OperationsService, world: dict[str, Any], code, unit, low, high
) -> None:
    store_id, staff = world["store_id"], world["staff"]
    quote = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(RequestedLine(code, "1", unit, QuantityBasis.STAFF_MEASUREMENT),),
    )
    assert quote.finality == "RANGE"
    assert (quote.display_total_min_vnd, quote.display_total_max_vnd) == (low, high)

    for amount in (low, high):
        ok = _propose(
            service,
            store_id=store_id,
            staff=staff,
            quote=quote,
            choices=(RangePriceChoice(code, amount),),
        )
        assert isinstance(ok, RangePriceProposalResult), f"{code} {amount} should be in band: {ok}"

    for amount in (low - 1, high + 1):
        bad = _propose(
            service,
            store_id=store_id,
            staff=staff,
            quote=quote,
            choices=(RangePriceChoice(code, amount),),
        )
        assert isinstance(bad, UnresolvedQuoteResult), f"{code} {amount} should be refused"
        assert bad.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)


# --------------------------------------------------------------------------------------------
# 2. The band scales with quantity; the per-unit figure is not a legal line amount.
# --------------------------------------------------------------------------------------------


def test_quantity_scaled_band_rejects_the_per_unit_minimum(
    connection: Any, service: OperationsService, world: dict[str, Any]
) -> None:
    store_id, staff, owner = world["store_id"], world["staff"], world["owner"]
    code = "DC_FUR_COAT"  # 200.000 - 400.000 per cái
    quote = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(RequestedLine(code, "3", Unit.ITEM, QuantityBasis.STAFF_MEASUREMENT),),
    )
    assert (quote.display_total_min_vnd, quote.display_total_max_vnd) == (600_000, 1_200_000)

    per_unit = _propose(
        service,
        store_id=store_id,
        staff=staff,
        quote=quote,
        choices=(RangePriceChoice(code, 200_000),),
    )
    assert isinstance(per_unit, UnresolvedQuoteResult)
    assert per_unit.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)

    for amount in (599_999, 1_200_001):
        bad = _propose(
            service,
            store_id=store_id,
            staff=staff,
            quote=quote,
            choices=(RangePriceChoice(code, amount),),
        )
        assert isinstance(bad, UnresolvedQuoteResult)

    choices = (RangePriceChoice(code, 600_000),)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=quote, choices=choices)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)
    applied = _apply(
        service,
        store_id=store_id,
        staff=staff,
        quote=quote,
        proposal=proposal,
        choices=choices,
    )
    assert isinstance(applied, QuoteRevisionResult), applied
    assert applied.finality == "APPROVED_EXACT"
    assert applied.net_service_subtotal_vnd == 600_000
    assert (applied.display_total_min_vnd, applied.display_total_max_vnd) == (600_000, 600_000)


# --------------------------------------------------------------------------------------------
# 3. Fractional kg on a range-priced kg service.
# --------------------------------------------------------------------------------------------


def test_fractional_kilogram_band(
    connection: Any, service: OperationsService, world: dict[str, Any]
) -> None:
    store_id, staff, owner = world["store_id"], world["staff"], world["owner"]
    code = "OTHER_SOFT_CARPET"  # 30.000 - 50.000 / kg
    quote = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(RequestedLine(code, "2.5", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
    )
    assert (quote.display_total_min_vnd, quote.display_total_max_vnd) == (75_000, 125_000)
    for amount, want_ok in ((74_999, False), (75_000, True), (125_000, True), (125_001, False)):
        got = _propose(
            service,
            store_id=store_id,
            staff=staff,
            quote=quote,
            choices=(RangePriceChoice(code, amount),),
        )
        assert isinstance(got, RangePriceProposalResult) is want_ok, (amount, got)

    # A quantity that forces rounding of both ends.
    odd = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(RequestedLine(code, "0.333", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
    )
    assert (odd.display_total_min_vnd, odd.display_total_max_vnd) == (9_990, 16_650)
    assert isinstance(odd.display_total_min_vnd, int)


# --------------------------------------------------------------------------------------------
# 4. Mixed exact + range revision.
# --------------------------------------------------------------------------------------------


def test_mixed_exact_and_range_lines(
    connection: Any, service: OperationsService, world: dict[str, Any]
) -> None:
    store_id, staff, owner = world["store_id"], world["staff"], world["owner"]
    quote = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(
            RequestedLine("DC_AO_DAI_TRADITIONAL", "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),
            RequestedLine("STANDARD_WASH_DRY", "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),
        ),
    )
    # 6 kg at the >=6 tier: 6 * 20.000 = 120.000, plus the ao dai band.
    assert (quote.display_total_min_vnd, quote.display_total_max_vnd) == (200_000, 360_000)

    # An amount for the exactly-priced line is refused.
    bad = _propose(
        service,
        store_id=store_id,
        staff=staff,
        quote=quote,
        choices=(RangePriceChoice("STANDARD_WASH_DRY", 100_000),),
    )
    assert isinstance(bad, UnresolvedQuoteResult)
    assert bad.reason_codes == ("RANGE_PRICE_NOT_APPLICABLE",)

    choices = (RangePriceChoice("DC_AO_DAI_TRADITIONAL", 150_000),)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=quote, choices=choices)
    assert isinstance(proposal, RangePriceProposalResult)
    _approve(service, proposal=proposal, owner=owner)
    applied = _apply(
        service, store_id=store_id, staff=staff, quote=quote, proposal=proposal, choices=choices
    )
    assert isinstance(applied, QuoteRevisionResult), applied
    assert applied.net_service_subtotal_vnd == 270_000
    assert (applied.display_total_min_vnd, applied.display_total_max_vnd) == (270_000, 270_000)


# --------------------------------------------------------------------------------------------
# 5. The scalar subtotal fields on a RANGE revision.
# --------------------------------------------------------------------------------------------


def test_band_revision_reports_the_band_maximum_as_a_scalar_subtotal(
    connection: Any, service: OperationsService, world: dict[str, Any]
) -> None:
    store_id, staff = world["store_id"], world["staff"]
    quote = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(
            RequestedLine("DC_AO_DAI_TRADITIONAL", "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),
        ),
    )
    assert quote.finality == "RANGE"
    # This is the API's answer to "what is the net service subtotal" for a revision where nobody
    # has chosen a price. It is the top of the band.
    assert quote.net_service_subtotal_vnd == 240_000
    assert quote.list_service_subtotal_vnd == 240_000


# --------------------------------------------------------------------------------------------
# 6. Pricebook version N vs N+1.
# --------------------------------------------------------------------------------------------


def test_amount_is_checked_against_the_revisions_own_pricebook_version(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    code = "DC_AO_DAI_TRADITIONAL"
    quote = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(RequestedLine(code, "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),),
    )
    assert (quote.display_total_min_vnd, quote.display_total_max_vnd) == (80_000, 240_000)

    source = (ROOT / "templates/services-pricebook.csv").read_text(encoding="utf-8")
    narrowed = source.replace(
        f'{code},dry_cleaning,"Áo dài truyền thống",bộ,80000,240000,',
        f'{code},dry_cleaning,"Áo dài truyền thống",bộ,200000,240000,',
    )
    assert narrowed != source
    _publish(connection, narrowed.encode("utf-8"))

    # 150.000 is inside version N's band and outside version N+1's. The customer was read N.
    choices = (RangePriceChoice(code, 150_000),)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=quote, choices=choices)
    assert isinstance(proposal, RangePriceProposalResult), proposal
    _approve(service, proposal=proposal, owner=owner)
    applied = _apply(
        service, store_id=store_id, staff=staff, quote=quote, proposal=proposal, choices=choices
    )
    assert isinstance(applied, QuoteRevisionResult), applied
    assert applied.net_service_subtotal_vnd == 150_000

    # And an amount only version N+1 would allow but N would not: 250.000 is outside both.
    quote2 = _band_quote(
        service,
        store_id=store_id,
        staff=staff,
        lines=(RequestedLine(code, "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),),
    )
    # The new quote is priced against N+1, so its band starts at 200.000.
    assert (quote2.display_total_min_vnd, quote2.display_total_max_vnd) == (200_000, 240_000)
    refused = _propose(
        service,
        store_id=store_id,
        staff=staff,
        quote=quote2,
        choices=(RangePriceChoice(code, 150_000),),
    )
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)


# --------------------------------------------------------------------------------------------
# 7. The 6 kg cliff is untouched.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quantity,expected",
    [("5.999", 149_975), ("6", 120_000), ("6.001", 120_020), ("1", 25_000), ("0.5", 25_000)],
)
def test_six_kg_cliff_is_undisturbed(
    connection: Any, service: OperationsService, world: dict[str, Any], quantity, expected
) -> None:
    store_id, staff = world["store_id"], world["staff"]
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(
            RequestedLine("STANDARD_WASH_DRY", quantity, Unit.KG, QuantityBasis.STAFF_MEASUREMENT),
        ),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"q-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult), priced
    assert priced.net_service_subtotal_vnd == expected
