"""`DEC-031` through the real repositories: the per-piece fee, loss and a refunded order.

Every test here writes through `RemedyProposalRepository` against PostgreSQL. The order under test
is priced the way the counter prices one: three shirts at the published 50.000 d on one line, and
5,2 kg of standard wash at 25.000 d/kg on another -- a per-piece line and a bag by weight on one
bill, which is the case the old line-wide ceiling got wrong.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.configurations import ConfigurationRepository
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import CONFIG_TYPE as PRICEBOOK_CONFIG_TYPE
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.remedies import (
    CREDIT_EXECUTED,
    RemedyProposalRepository,
    RemedyStateError,
)
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    Unit,
)
from nha_trang_laundry_domain.quotes import ConfigurationSnapshotReference
from nha_trang_laundry_domain.remedies import RemedyKind, RemedyRefusal, RemedyStatus
from quote_test_data import FixtureLine
from test_remedies import (
    NOW,
    _advance,
    _approve,
    _execute,
    _incident,
    _propose,
    _shop,
)

ROOT = Path(__file__).resolve().parents[3]

SHIRTS = FixtureLine("line-0", "DC_SHIRT", Unit.ITEM, "3", 50_000, 150_000)
BAG = FixtureLine("line-1", "STANDARD_WASH_DRY", Unit.KG, "5.2", 25_000, 130_000)
#: Two pillows closed inside their band: the snapshot records no unit price for a closed band.
PILLOWS = FixtureLine("line-2", "BED_PILLOW", Unit.ITEM, "2", None, 140_000)
#: Ten knitted tops ironed at 15.000 d: a line-wide cap of 750.000 d, a per-piece one of 75.000.
IRONING = FixtureLine("line-3", "IRON_KNIT", Unit.ITEM, "10", 15_000, 150_000)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _claim(
    connection: Any,
    store_id: UUID,
    incident_id: UUID,
    staff: Any,
    kind: RemedyKind,
    line_id: str,
    amount: int,
    **fields: Any,
) -> Any:
    return _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=kind,
        store_fault_attested=True,
        order_line_id=line_id,
        amount_vnd=amount,
        **fields,
    )


def _options(connection: Any, store_id: UUID, incident_id: UUID, staff: Any) -> Any:
    with connection.cursor() as cursor:
        return RemedyProposalRepository().options(
            cursor, store_id=store_id, incident_id=incident_id, principal=staff
        )


# --- 1. the item fee per piece ------------------------------------------------------------------


def test_a_per_piece_line_caps_each_item_at_five_times_its_unit_price(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS, BAG))
    with pytest.raises(RemedyStateError) as refused:
        _claim(
            connection,
            store_id,
            incident_id,
            staff,
            RemedyKind.DAMAGE_COMPENSATION,
            "line-0",
            250_001,
            garment_index=1,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    # 5 x 50.000 d, the price of one shirt -- not 5 x the 150.000 d line.
    assert refused.value.ceiling_vnd == 250_000

    at_ceiling = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        250_000,
        garment_index=1,
    )
    assert at_ceiling.ceiling_vnd == 250_000
    assert at_ceiling.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert at_ceiling.owner_reasons == ("ABOVE_STAFF_LIMIT",)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT ceiling_vnd FROM remedy_proposals WHERE id = %s", (at_ceiling.proposal_id,)
        )
        stored = cursor.fetchone()
    assert stored is not None and int(stored[0]) == 250_000


def test_positive_control_an_ordinary_damage_on_a_kg_bag_is_still_staff_authorised(
    connection: psycopg.Connection[Any],
) -> None:
    """80.000 d on the bag: under the bag's 650.000 d ceiling and the 100.000 d staff limit."""

    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS, BAG))
    proposal = _claim(
        connection, store_id, incident_id, staff, RemedyKind.DAMAGE_COMPENSATION, "line-1", 80_000
    )
    assert proposal.status is RemedyStatus.STAFF_AUTHORIZED
    assert proposal.approval_id is None
    assert proposal.ceiling_vnd == 650_000
    assert proposal.owner_reasons == ()
    executed = _execute(connection, proposal.proposal_id, staff)
    assert executed.event_type == CREDIT_EXECUTED and executed.amount_vnd == 80_000


def test_a_per_piece_line_with_no_recorded_unit_price_goes_to_the_owner(
    connection: psycopg.Connection[Any],
) -> None:
    """Two pillows in a closed band: no recorded number is one pillow's fee, so nothing is divided
    out of the 140.000 d and no staff member may authorise even 10.000 d on it."""

    store_id, staff, _, incident_id = _shop(connection, lines=(PILLOWS,))
    proposal = _claim(
        connection, store_id, incident_id, staff, RemedyKind.DAMAGE_COMPENSATION, "line-2", 10_000
    )
    assert proposal.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert proposal.owner_reasons == ("ITEM_FEE_NOT_RECORDED",)
    assert proposal.ceiling_vnd == 700_000


# --- 2. loss -----------------------------------------------------------------------------------


def test_every_loss_claim_needs_the_owner(connection: psycopg.Connection[Any]) -> None:
    """A lost shirt, 20.000 d of compensation: staff record and propose, only the owner pays."""

    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS, BAG))
    proposal = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.LOST_ITEM,
        "line-0",
        20_000,
        garment_index=1,
    )
    assert proposal.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert proposal.owner_reasons == ("LOSS_CLAIM",)
    assert proposal.ceiling_vnd == 250_000
    assert proposal.window_closes_at == NOW + timedelta(hours=24)
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, proposal.proposal_id, staff)
    assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"
    assert proposal.approval_id is not None
    _approve(connection, store_id, proposal.approval_id, NOW + timedelta(minutes=1))
    executed = _execute(connection, proposal.proposal_id, staff, NOW + timedelta(minutes=2))
    assert executed.event_type == CREDIT_EXECUTED and executed.amount_vnd == 20_000


def test_a_loss_outside_the_24_hour_window_is_refused(connection: psycopg.Connection[Any]) -> None:
    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS,))
    with pytest.raises(RemedyStateError) as late:
        _claim(
            connection,
            store_id,
            incident_id,
            staff,
            RemedyKind.LOST_ITEM,
            "line-0",
            20_000,
            proposed_at=NOW + timedelta(hours=25),
        )
    assert late.value.reason_code == RemedyRefusal.REMEDY_WINDOW_CLOSED.value
    assert late.value.window_closes_at == NOW + timedelta(hours=24)


def test_loss_and_damage_on_one_line_share_its_ceiling(connection: psycopg.Connection[Any]) -> None:
    """On the bag, which has one ceiling (650.000 d): 600.000 d of damage leaves 50.000 d for loss.

    Moved from the three-shirt line when the founder ruled the ceiling is per item: that line now
    carries 750.000 d in total, so the old figures no longer reached its ceiling. The sharing this
    test exists for is unchanged, and the bag states it with one ceiling and no pieces.
    """

    store_id, staff, _, incident_id = _shop(connection, lines=(BAG,))
    _claim(
        connection, store_id, incident_id, staff, RemedyKind.DAMAGE_COMPENSATION, "line-1", 600_000
    )
    with pytest.raises(RemedyStateError) as refused:
        _claim(connection, store_id, incident_id, staff, RemedyKind.LOST_ITEM, "line-1", 50_001)
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert (refused.value.ceiling_vnd, refused.value.committed_vnd) == (650_000, 600_000)


def test_a_loss_already_on_an_item_counts_against_later_damage(
    connection: psycopg.Connection[Any],
) -> None:
    """The other order: a loss proposed first is money the line already carries.

    A 20.000 d loss waits for the owner; 80.000 d of damage on the same bag after it takes the line
    to 100.000 d -- still inside what staff may approve -- and one dong more is the owner's. The
    options read shows the loss in what the line carries, so the form predicts the same.
    """

    store_id, staff, _, incident_id = _shop(connection, lines=(BAG,))
    _claim(connection, store_id, incident_id, staff, RemedyKind.LOST_ITEM, "line-1", 20_000)
    [line] = _options(connection, store_id, incident_id, staff).damage_lines
    assert line.committed_vnd == 20_000
    over_staff = _claim(
        connection, store_id, incident_id, staff, RemedyKind.DAMAGE_COMPENSATION, "line-1", 80_001
    )
    assert over_staff.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert over_staff.owner_reasons == ("ABOVE_STAFF_LIMIT",)
    with pytest.raises(RemedyStateError) as refused:
        _claim(
            connection,
            store_id,
            incident_id,
            staff,
            RemedyKind.DAMAGE_COMPENSATION,
            "line-1",
            550_000,
        )
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert (refused.value.ceiling_vnd, refused.value.committed_vnd) == (650_000, 100_001)


def test_payment_counts_a_paid_loss_against_the_item_ceiling(
    connection: psycopg.Connection[Any],
) -> None:
    """The second line: a staff-authorised damage row that never went through `propose` -- forged
    here as a pre-fix writer could have -- is not paid past the ceiling a paid loss already used."""

    store_id, staff, _, incident_id = _shop(connection, lines=(BAG,))
    loss = _claim(connection, store_id, incident_id, staff, RemedyKind.LOST_ITEM, "line-1", 600_000)
    assert loss.approval_id is not None
    _approve(connection, store_id, loss.approval_id, NOW + timedelta(minutes=1))
    _execute(connection, loss.proposal_id, staff, NOW + timedelta(minutes=2))
    forged = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO remedy_proposals (
                id, store_id, incident_id, order_id, kind, status, amount_vnd, direction,
                ceiling_vnd, order_line_id, policy_version_id, policy_version,
                store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                proposed_by, proposed_at, correlation_id
            )
            SELECT %s, store_id, incident_id, order_id, 'DAMAGE_COMPENSATION', 'STAFF_AUTHORIZED',
                   60000, direction, ceiling_vnd, order_line_id, policy_version_id,
                   policy_version, store_fault_attested, window_opened_at, window_closes_at,
                   proposal_hash, proposed_by, proposed_at, %s
            FROM remedy_proposals WHERE id = %s
            """,
            (forged, uuid4(), loss.proposal_id),
        )
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, forged, staff, NOW + timedelta(minutes=3))
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert (refused.value.ceiling_vnd, refused.value.committed_vnd) == (650_000, 600_000)


def test_several_pieces_on_one_line_each_carry_their_own_ceiling(
    connection: psycopg.Connection[Any],
) -> None:
    """The founder's per-item ruling, end to end: three shirts at 50.000 d.

    One claim of 250.000 d on shirt #1 goes to the owner; a second of 250.000 d on shirt #2 goes to
    the owner too -- the line holds 750.000 d -- and both are paid once approved. A single claim of
    300.000 d is refused: no one shirt can be owed that. Shirt #1 is then full, and shirt #3 still
    has its own 250.000 d, which takes the line to exactly 750.000 d.

    Updated for the DEC-031 addendum (`REMEDY-GARMENT-001`): each claim now names its shirt, and a
    claim past the full shirt is refused against *that shirt's* ceiling -- which on an undiscounted
    line is reached before the line's, because the line is the shirts together.
    """

    store_id, staff, order_id, incident_id = _shop(connection, lines=(SHIRTS,))
    with pytest.raises(RemedyStateError) as single:
        _claim(
            connection,
            store_id,
            incident_id,
            staff,
            RemedyKind.DAMAGE_COMPENSATION,
            "line-0",
            300_000,
            garment_index=1,
        )
    assert single.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert single.value.ceiling_vnd == 250_000

    # Both proposed on the complaint before either is carried out (carrying one out closes it).
    proposals = [
        _claim(
            connection, store_id, incident_id, staff, kind, "line-0", 250_000, garment_index=garment
        )
        for kind, garment in ((RemedyKind.DAMAGE_COMPENSATION, 1), (RemedyKind.LOST_ITEM, 2))
    ]
    paid = []
    for minute, proposal in zip((1, 3), proposals, strict=True):
        assert proposal.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
        assert proposal.ceiling_vnd == 250_000
        assert proposal.approval_id is not None
        _approve(connection, store_id, proposal.approval_id, NOW + timedelta(minutes=minute))
        executed = _execute(
            connection, proposal.proposal_id, staff, NOW + timedelta(minutes=minute + 1)
        )
        paid.append(executed.amount_vnd)
    # Both paid: the payment-time check binds the line's 750.000 d, not one shirt's 250.000 d.
    assert paid == [250_000, 250_000]

    # A new complaint about the same order: the line's total is per order line, across incidents.
    incident_id = _incident(connection, store_id, order_id, staff)

    [line] = _options(connection, store_id, incident_id, staff).damage_lines
    assert (line.pieces, line.ceiling_vnd, line.line_ceiling_vnd, line.committed_vnd) == (
        3,
        250_000,
        750_000,
        500_000,
    )
    assert (line.garments, line.garment_committed_vnd) == (3, (250_000, 250_000, 0))
    with pytest.raises(RemedyStateError) as past:
        _claim(
            connection,
            store_id,
            incident_id,
            staff,
            RemedyKind.DAMAGE_COMPENSATION,
            "line-0",
            250_001,
            garment_index=3,
        )
    assert past.value.ceiling_vnd == 250_000
    # Shirt #1 is full: one dong more on it is refused with its ceiling and what it carries.
    with pytest.raises(RemedyStateError) as shirt_full:
        _claim(
            connection,
            store_id,
            incident_id,
            staff,
            RemedyKind.DAMAGE_COMPENSATION,
            "line-0",
            1,
            garment_index=1,
        )
    assert shirt_full.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert (shirt_full.value.ceiling_vnd, shirt_full.value.committed_vnd) == (250_000, 250_000)
    # The third shirt takes the line to exactly 750.000 d, inclusive; one dong more is refused.
    third = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        250_000,
        garment_index=3,
    )
    assert third.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    for garment in (1, 2, 3):
        with pytest.raises(RemedyStateError) as line_full:
            _claim(
                connection,
                store_id,
                incident_id,
                staff,
                RemedyKind.DAMAGE_COMPENSATION,
                "line-0",
                1,
                garment_index=garment,
            )
        assert line_full.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
        assert line_full.value.ceiling_vnd == 250_000, garment
    [line] = _options(connection, store_id, incident_id, staff).damage_lines
    assert line.committed_vnd == 750_000 == line.line_ceiling_vnd


def test_the_staff_limit_is_per_garment_and_cumulative_on_each(
    connection: psycopg.Connection[Any],
) -> None:
    """60.000 d on shirt #1, then 50.000 d on shirt #2: each is its own item, so both are staff's.

    This test used to be `..._stays_per_line_when_a_claim_is_split_across_pieces` and asserted the
    second claim went to the owner: the staff limit was the line's. The DEC-031 addendum
    (`REMEDY-GARMENT-001`) reversed exactly that -- `DEC-004` says 100.000 d *per item*, and shirt
    #2 has had nothing paid on it. What the old test protected is kept, on the garment it belongs
    to: a second claim on shirt #1 is cumulative with the first and 110.000 d is the owner's.
    """

    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS,))
    first = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        60_000,
        garment_index=1,
    )
    assert first.status is RemedyStatus.STAFF_AUTHORIZED
    assert first.garment_index == 1
    other_shirt = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        50_000,
        garment_index=2,
    )
    assert other_shirt.status is RemedyStatus.STAFF_AUTHORIZED
    assert other_shirt.approval_id is None and other_shirt.garment_index == 2
    same_shirt = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        50_000,
        garment_index=1,
    )
    assert same_shirt.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert same_shirt.owner_reasons == ("ABOVE_STAFF_LIMIT",)


def test_the_schema_refuses_a_staff_authorised_loss(connection: psycopg.Connection[Any]) -> None:
    """Migration 0051 states rule 2 in the database, so no writer can skip it."""

    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS,))
    seed = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        10_000,
        garment_index=1,
    )
    # A savepoint, so the refused INSERT rolls back alone and the test's transaction stays usable.
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO remedy_proposals (
                id, store_id, incident_id, order_id, kind, status, amount_vnd, direction,
                ceiling_vnd, order_line_id, policy_version_id, policy_version,
                store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                proposed_by, proposed_at, correlation_id
            )
            SELECT %s, store_id, incident_id, order_id, 'LOST_ITEM', 'STAFF_AUTHORIZED',
                   amount_vnd, direction, ceiling_vnd, order_line_id, policy_version_id,
                   policy_version, store_fault_attested, window_opened_at, window_closes_at,
                   proposal_hash, proposed_by, proposed_at, %s
            FROM remedy_proposals WHERE id = %s
            """,
            (uuid4(), uuid4(), seed.proposal_id),
        )


# --- 3. a refunded order ------------------------------------------------------------------------


def _refunded_delivery(connection: Any, lines: tuple[FixtureLine, ...]) -> tuple[Any, ...]:
    """A delivered, paid order cancelled `SHOP_FAULT_NO_CHARGE` and refunded in full."""

    store_id, staff, order_id, incident_id = _shop(
        connection, mode=FulfillmentMode.PICKUP_AND_RETURN, lines=lines
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
    return store_id, staff, order_id, incident_id


def _refund(connection: Any, order_id: UUID, staff: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    version = _advance(
        connection,
        order_id,
        staff,
        int(row[0]),
        commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW,
    ).row_version
    cancelled = _advance(
        connection,
        order_id,
        staff,
        version,
        commercial_target=CommercialOrderStatus.CANCELLED,
        custody_resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
    )
    assert cancelled.balance.value == "REFUNDED"


@pytest.mark.parametrize("kind", [RemedyKind.DAMAGE_COMPENSATION, RemedyKind.LOST_ITEM])
def test_compensation_on_a_refunded_order_needs_the_owner(
    connection: psycopg.Connection[Any], kind: RemedyKind
) -> None:
    """10.000 d on the bag of a refunded order: owner-only, capped against the *quoted* bag fee."""

    store_id, staff, order_id, incident_id = _refunded_delivery(connection, (SHIRTS, BAG))
    _refund(connection, order_id, staff)
    proposal = _claim(connection, store_id, incident_id, staff, kind, "line-1", 10_000)
    assert proposal.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert "ORDER_REFUNDED" in proposal.owner_reasons
    assert proposal.ceiling_vnd == 650_000
    options = _options(connection, store_id, incident_id, staff)
    assert options.order_refunded is True
    # And the late-delivery credit stays unavailable on the refunded bill.
    assert options.late_delivery_credit_vnd is None


def test_a_staff_authorised_proposal_cannot_be_paid_once_the_order_is_refunded(
    connection: psycopg.Connection[Any],
) -> None:
    """Proposed before the refund, executed after: the refund moves it to the owner."""

    store_id, staff, order_id, incident_id = _refunded_delivery(connection, (SHIRTS, BAG))
    proposal = _claim(
        connection, store_id, incident_id, staff, RemedyKind.DAMAGE_COMPENSATION, "line-1", 10_000
    )
    assert proposal.status is RemedyStatus.STAFF_AUTHORIZED
    _refund(connection, order_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, proposal.proposal_id, staff)
    assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"


def test_a_proposal_written_under_the_line_wide_rule_is_not_paid_above_the_per_piece_cap(
    connection: psycopg.Connection[Any],
) -> None:
    """A row a deployed database may hold from before DEC-031: 90.000 d staff-authorised against
    ten 15.000 d tops, checked then against 750.000 d. Per piece the cap is 75.000 d, and payment
    applies the lower of the two -- the ruling only ever lowers exposure."""

    store_id, staff, _, incident_id = _shop(connection, lines=(IRONING,))
    seed = _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-3",
        1_000,
        garment_index=1,
    )
    legacy = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO remedy_proposals (
                id, store_id, incident_id, order_id, kind, status, amount_vnd, direction,
                ceiling_vnd, order_line_id, policy_version_id, policy_version,
                store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                proposed_by, proposed_at, correlation_id
            )
            SELECT %s, store_id, incident_id, order_id, kind, 'STAFF_AUTHORIZED', 90000,
                   direction, 750000, order_line_id, policy_version_id, policy_version,
                   store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                   proposed_by, proposed_at, %s
            FROM remedy_proposals WHERE id = %s
            """,
            (legacy, uuid4(), seed.proposal_id),
        )
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, legacy, staff)
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert refused.value.ceiling_vnd == 75_000


# --- 4. what the counter sees --------------------------------------------------------------------


def _published_pricebook_reference(connection: Any, staff: Any) -> ConfigurationSnapshotReference:
    publish_pricebook(
        connection,
        actor_id=staff.staff_user_id,
        source=(ROOT / "templates" / "services-pricebook.csv").read_bytes(),
    )
    with connection.cursor() as cursor:
        published = ConfigurationRepository.latest_published(cursor, PRICEBOOK_CONFIG_TYPE)
    assert published is not None
    return ConfigurationSnapshotReference(
        "PRICEBOOK",
        published.version_id,
        published.version,
        f"JCS-SHA256-V1:{published.snapshot_hash}",
    )


def test_the_counter_sees_the_item_and_what_it_already_carries(
    connection: psycopg.Connection[Any],
) -> None:
    """Per damageable line: the service by name, the fee basis, the ceiling, what is committed, and
    whether any amount on it goes to the owner -- the same numbers `propose` will decide with."""

    store_id, staff, _, incident_id = _shop(connection, lines=(SHIRTS,))
    # This first order cites a pricebook version that does not exist, so no name can be resolved
    # for it: the read says "no name" rather than borrowing today's catalogue, and the console
    # then shows the code.
    unnamed = _options(connection, store_id, incident_id, staff).damage_lines
    assert [(line.service_code, line.service_name) for line in unnamed] == [("DC_SHIRT", None)]

    # A real published version, cited by the next order: the names come from *that* version.
    reference = _published_pricebook_reference(connection, staff)
    store_id, staff, _, incident_id = _shop(
        connection, lines=(SHIRTS, BAG, PILLOWS), pricebook=reference
    )
    _claim(
        connection,
        store_id,
        incident_id,
        staff,
        RemedyKind.DAMAGE_COMPENSATION,
        "line-0",
        30_000,
        garment_index=1,
    )
    options = _options(connection, store_id, incident_id, staff)
    assert options.loss_requires_owner is True
    by_line = {line.line_id: line for line in options.damage_lines}
    assert set(by_line) == {"line-0", "line-1", "line-2"}

    shirts = by_line["line-0"]
    assert (shirts.service_code, shirts.service_name) == ("DC_SHIRT", "Áo sơ mi")
    assert (shirts.item_fee_basis, shirts.item_fee_vnd, shirts.ceiling_vnd) == (
        "UNIT",
        50_000,
        250_000,
    )
    assert shirts.committed_vnd == 30_000
    assert shirts.owner_always == ()
    # Per item: one claim up to 250.000 d, the three shirts together up to 750.000 d.
    assert (shirts.pieces, shirts.line_ceiling_vnd) == (3, 750_000)
    # Per garment (`REMEDY-GARMENT-001`): the 30.000 d was shirt #1's, and only shirt #1's.
    assert (shirts.garments, shirts.garment_committed_vnd, shirts.line_level_committed_vnd) == (
        3,
        (30_000, 0, 0),
        0,
    )

    bag = by_line["line-1"]
    assert bag.service_name == "Giặt sấy tiêu chuẩn"
    assert (bag.item_fee_basis, bag.item_fee_vnd, bag.ceiling_vnd, bag.committed_vnd) == (
        "BAG",
        130_000,
        650_000,
        0,
    )
    assert (bag.pieces, bag.line_ceiling_vnd) == (1, 650_000)
    # A bag has no garment identity: one claimable whole, no per-garment figures.
    assert (bag.garments, bag.garment_committed_vnd) == (None, ())

    pillows = by_line["line-2"]
    assert pillows.service_name == "Gối"
    assert pillows.item_fee_basis == "NOT_RECORDED"
    assert pillows.owner_always == ("ITEM_FEE_NOT_RECORDED",)
    assert (pillows.pieces, pillows.line_ceiling_vnd) == (1, 700_000)
    assert (pillows.garments, pillows.garment_committed_vnd) == (None, ())
    # The legacy map is kept for callers that read it: one claim's ceiling per line.
    assert options.damage_line_ceilings_vnd == {
        "line-0": 250_000,
        "line-1": 650_000,
        "line-2": 700_000,
    }
