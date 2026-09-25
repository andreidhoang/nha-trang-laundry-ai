"""`REMEDY-GARMENT-001` through the real repositories: per-garment limits and the owner's window.

The DEC-031 addendum (2026-09-25, delegated) made two rulings, and both are exercised here against
PostgreSQL:

1. A claim on a line priced per piece names its garment (`garment_index`, 1..quantity), and the
   100.000 d staff limit and the 5x ceiling are cumulative per (line, garment). Migration `0052`
   records the garment; a row written before it names none and counts against every garment.
2. An owner-only remedy envelope stays open until the end of the next business day in
   Asia/Ho_Chi_Minh instead of ten minutes. Every other action keeps its short window.

Each test fails on the code before this item: `RemedyProposalCommand` had no `garment_index`,
`remedy_proposals` had no such column, and `APPROVE_REMEDY` expired ten minutes after it was raised.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.remedies import RemedyProposalRepository, RemedyStateError
from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import ApprovalAction, Unit
from nha_trang_laundry_domain.remedies import RemedyKind, RemedyRefusal, RemedyStatus
from quote_test_data import FixtureLine
from test_remedies import NOW, _approve, _execute, _propose, _shop, _staff

SHIRTS = FixtureLine("line-0", "DC_SHIRT", Unit.ITEM, "3", 50_000, 150_000)
BAG = FixtureLine("line-1", "STANDARD_WASH_DRY", Unit.KG, "5.2", 25_000, 130_000)
DRESS = FixtureLine("line-2", "DC_EVENING_DRESS", Unit.ITEM, "1", 120_000, 120_000)
PILLOWS = FixtureLine("line-3", "BED_PILLOW", Unit.ITEM, "2", None, 140_000)
NHA_TRANG = ZoneInfo("Asia/Ho_Chi_Minh")


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
    shop: tuple[Any, ...],
    line_id: str,
    amount: int,
    garment: int | None,
    kind: RemedyKind = RemedyKind.DAMAGE_COMPENSATION,
    **fields: Any,
) -> Any:
    store_id, staff, _, incident_id = shop
    return _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=kind,
        store_fault_attested=True,
        order_line_id=line_id,
        amount_vnd=amount,
        garment_index=garment,
        **fields,
    )


def _refused(connection: Any, shop: tuple[Any, ...], *args: Any, **fields: Any) -> Any:
    with pytest.raises(RemedyStateError) as refused:
        _claim(connection, shop, *args, **fields)
    return refused.value


def _stored_garment(connection: Any, proposal_id: UUID) -> int | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT garment_index FROM remedy_proposals WHERE id = %s", (proposal_id,))
        row = cursor.fetchone()
    assert row is not None
    return None if row[0] is None else int(row[0])


def _copied_row(
    connection: Any,
    seed: UUID,
    amount: int,
    *,
    garment: int | None = None,
    status: str = "STAFF_AUTHORIZED",
) -> UUID:
    """A row that never went through `propose`, copied from one that did.

    With `garment=None` it is exactly what a database deployed before migration 0052 holds: same
    line, no garment named. With a garment it is a forged staff authorisation, the kind a pre-fix
    writer could have left, for the payment-time re-check to catch.
    """

    row_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO remedy_proposals (
                id, store_id, incident_id, order_id, kind, status, amount_vnd, direction,
                ceiling_vnd, order_line_id, policy_version_id, policy_version,
                store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                proposed_by, proposed_at, correlation_id, garment_index
            )
            SELECT %s, store_id, incident_id, order_id, 'DAMAGE_COMPENSATION', %s, %s,
                   direction, ceiling_vnd, order_line_id, policy_version_id, policy_version,
                   store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
                   proposed_by, proposed_at, %s, %s
            FROM remedy_proposals WHERE id = %s
            """,
            (row_id, status, amount, uuid4(), garment, seed),
        )
    return row_id


def _options_line(connection: Any, shop: tuple[Any, ...], line_id: str) -> Any:
    store_id, staff, _, incident_id = shop
    with connection.cursor() as cursor:
        options = RemedyProposalRepository().options(
            cursor, store_id=store_id, incident_id=incident_id, principal=staff
        )
    assert options.damage_lines is not None
    return {line.line_id: line for line in options.damage_lines}[line_id]


# --- 1. per-garment limits ------------------------------------------------------------------------


def test_each_shirt_has_its_own_staff_limit_and_a_second_claim_on_one_is_cumulative(
    connection: psycopg.Connection[Any],
) -> None:
    """100.000 d on shirt #1 and 100.000 d on shirt #2: both staff's. 1 d more on #1: owner's."""

    shop = _shop(connection, lines=(SHIRTS,))
    first = _claim(connection, shop, "line-0", 100_000, 1)
    second = _claim(connection, shop, "line-0", 100_000, 2)
    assert first.status is second.status is RemedyStatus.STAFF_AUTHORIZED
    assert (first.approval_id, second.approval_id) == (None, None)
    assert (_stored_garment(connection, first.proposal_id), second.garment_index) == (1, 2)
    again = _claim(connection, shop, "line-0", 1, 1)
    assert again.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert again.owner_reasons == ("ABOVE_STAFF_LIMIT",)
    line = _options_line(connection, shop, "line-0")
    assert (line.garments, line.garment_committed_vnd, line.committed_vnd) == (
        3,
        (100_001, 100_000, 0),
        200_001,
    )


def test_each_shirt_has_its_own_ceiling(connection: psycopg.Connection[Any]) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    _claim(connection, shop, "line-0", 200_000, 2)
    over = _refused(connection, shop, "line-0", 50_001, 2)
    assert over.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert (over.ceiling_vnd, over.committed_vnd) == (250_000, 200_000)
    # Shirt #1 is untouched by shirt #2's 200.000 d.
    full = _claim(connection, shop, "line-0", 250_000, 1)
    assert full.status is RemedyStatus.OWNER_APPROVAL_REQUIRED


def test_a_claim_on_several_shirts_must_name_one_and_one_that_exists(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    unnamed = _refused(connection, shop, "line-0", 10_000, None)
    assert unnamed.reason_code == RemedyRefusal.REMEDY_GARMENT_REQUIRED.value
    assert (unnamed.garments, unnamed.authority) == (3, "DEC-031")
    for garment in (0, 4):
        outside = _refused(connection, shop, "line-0", 10_000, garment)
        assert outside.reason_code == RemedyRefusal.REMEDY_GARMENT_OUT_OF_RANGE.value
        assert outside.garments == 3
    # Nothing was written for any of them.
    assert _options_line(connection, shop, "line-0").committed_vnd == 0


def test_a_single_garment_line_is_recorded_as_garment_one(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(DRESS,))
    unnamed = _claim(connection, shop, "line-2", 10_000, None)
    assert unnamed.garment_index == 1
    assert _stored_garment(connection, unnamed.proposal_id) == 1


@pytest.mark.parametrize(("line", "line_id"), [(BAG, "line-1"), (PILLOWS, "line-3")])
def test_a_line_with_no_garment_identity_refuses_a_garment_and_keeps_the_line_rule(
    connection: psycopg.Connection[Any], line: FixtureLine, line_id: str
) -> None:
    shop = _shop(connection, lines=(line,))
    refused = _refused(connection, shop, line_id, 10_000, 1)
    assert refused.reason_code == RemedyRefusal.REMEDY_GARMENT_NOT_APPLICABLE.value
    whole = _claim(connection, shop, line_id, 10_000, None)
    assert whole.garment_index is None
    assert _stored_garment(connection, whole.proposal_id) is None
    option = _options_line(connection, shop, line_id)
    assert (option.garments, option.garment_committed_vnd) == (None, ())


def test_a_loss_on_a_shirt_with_headroom_still_goes_to_the_owner(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    loss = _claim(connection, shop, "line-0", 1_000, 3, RemedyKind.LOST_ITEM)
    assert loss.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert loss.owner_reasons == ("LOSS_CLAIM",)
    assert loss.garment_index == 3


# --- 2. rows written before migration 0052 ------------------------------------------------------


def test_a_line_level_proposal_counts_against_every_shirt(
    connection: psycopg.Connection[Any],
) -> None:
    """60.000 d recorded line-level, before garments could be named. Nothing says which shirt it
    was, so 50.000 d on any shirt takes that shirt past the staff limit, and the form says so."""

    shop = _shop(connection, lines=(SHIRTS,))
    seed = _claim(connection, shop, "line-0", 1, 1)
    _copied_row(connection, seed.proposal_id, 60_000)
    line = _options_line(connection, shop, "line-0")
    assert line.line_level_committed_vnd == 60_000
    assert line.garment_committed_vnd == (60_001, 60_000, 60_000)
    for garment in (2, 3):
        over = _claim(connection, shop, "line-0", 50_000, garment)
        assert over.status is RemedyStatus.OWNER_APPROVAL_REQUIRED, garment
    # Shirt #1 carries the line-level 60.000 d and its own 1 d: 39.999 d more is inclusive at the
    # staff limit, and still staff's.
    at_limit = _claim(connection, shop, "line-0", 39_999, 1)
    assert at_limit.status is RemedyStatus.STAFF_AUTHORIZED


def test_a_line_level_proposal_uses_every_shirts_ceiling(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    seed = _claim(connection, shop, "line-0", 1, 1)
    _copied_row(connection, seed.proposal_id, 200_000)
    over = _refused(connection, shop, "line-0", 50_001, 3)
    assert over.reason_code == RemedyRefusal.REMEDY_CEILING_EXCEEDED.value
    assert (over.ceiling_vnd, over.committed_vnd) == (250_000, 200_000)
    at = _claim(connection, shop, "line-0", 50_000, 3)
    assert at.status is RemedyStatus.OWNER_APPROVAL_REQUIRED


def test_the_schema_records_the_garment_immutably_and_only_for_an_item(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    proposal = _claim(connection, shop, "line-0", 10_000, 2)
    with (
        pytest.raises(psycopg.errors.RaiseException),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            UPDATE remedy_proposals
            SET garment_index = 3, row_version = row_version + 1, status = 'EXECUTED'
            WHERE id = %s
            """,
            (proposal.proposal_id,),
        )
    copy = """
        INSERT INTO remedy_proposals (
            id, store_id, incident_id, order_id, kind, status, amount_vnd, direction,
            ceiling_vnd, order_line_id, policy_version_id, policy_version, store_fault_attested,
            window_opened_at, window_closes_at, proposal_hash, proposed_by, proposed_at,
            correlation_id, garment_index
        )
        SELECT gen_random_uuid(), store_id, incident_id, order_id, {kind}, status, {amount},
               {direction}, {ceiling}, {line}, policy_version_id, policy_version,
               store_fault_attested, window_opened_at, window_closes_at, proposal_hash,
               proposed_by, proposed_at, gen_random_uuid(), {garment}
        FROM remedy_proposals WHERE id = %s
    """
    for statement, constraint in (
        # A position is 1-based.
        (
            copy.format(
                kind="kind",
                amount="amount_vnd",
                direction="direction",
                ceiling="ceiling_vnd",
                line="order_line_id",
                garment="0",
            ),
            "remedy_proposals_garment_is_a_position",
        ),
        # A rewash is about the order, not one garment on it.
        (
            copy.format(
                kind="'FREE_REWASH'",
                amount="NULL",
                direction="NULL",
                ceiling="NULL",
                line="NULL",
                garment="1",
            ),
            "remedy_proposals_garment_needs_an_item",
        ),
    ):
        with (
            pytest.raises(psycopg.errors.CheckViolation) as violated,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, (proposal.proposal_id,))
        assert violated.value.diag.constraint_name == constraint
    assert _stored_garment(connection, proposal.proposal_id) == 2


# --- 3. payment re-checks per garment ------------------------------------------------------------


def test_payment_holds_staff_alone_to_the_limit_on_each_shirt(
    connection: psycopg.Connection[Any],
) -> None:
    """Two forged 60.000 d staff rows on shirt #1: the second is not paid without the owner. The
    same 60.000 d on shirt #2 is paid -- it is another item."""

    shop = _shop(connection, lines=(SHIRTS,))
    _, staff, _, _ = shop
    seed = _claim(connection, shop, "line-0", 1, 3)
    shirt_one = _claim(connection, shop, "line-0", 60_000, 1)
    shirt_two = _claim(connection, shop, "line-0", 60_000, 2)
    one_again = _copied_row(connection, seed.proposal_id, 60_000, garment=1)
    _execute(connection, shirt_one.proposal_id, staff)
    _execute(connection, shirt_two.proposal_id, staff)
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, one_again, staff)
    assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"


def test_a_line_level_staff_row_is_paid_against_the_shirt_that_leaves_it_least_room(
    connection: psycopg.Connection[Any],
) -> None:
    """A 60.000 d line-level staff row, still unpaid. Shirts #2 and #3 each took 40.000 d by staff
    since -- 100.000 d with the line-level row counted, inclusive -- and were paid. The line-level
    row is then paid too: no one shirt carries more than 100.000 d by staff alone. The pre-addendum
    rule would have summed the whole line (140.000 d) and refused it."""

    shop = _shop(connection, lines=(SHIRTS,))
    _, staff, _, _ = shop
    seed = _claim(connection, shop, "line-0", 1, 1)
    legacy = _copied_row(connection, seed.proposal_id, 59_999)
    two = _claim(connection, shop, "line-0", 40_000, 2)
    three = _claim(connection, shop, "line-0", 40_000, 3)
    assert two.status is three.status is RemedyStatus.STAFF_AUTHORIZED
    _execute(connection, two.proposal_id, staff)
    _execute(connection, three.proposal_id, staff)
    paid = _execute(connection, legacy, staff)
    assert paid.amount_vnd == 59_999


# --- 4. the owner's window ------------------------------------------------------------------------


def _loss_envelope(connection: Any, requested_at: datetime) -> tuple[Any, ...]:
    shop = _shop(connection, lines=(SHIRTS,))
    loss = _claim(
        connection, shop, "line-0", 20_000, 1, RemedyKind.LOST_ITEM, proposed_at=requested_at
    )
    assert loss.approval_id is not None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT expires_at FROM approval_requests WHERE id = %s", (loss.approval_id,)
        )
        row = cursor.fetchone()
    assert row is not None
    return shop, loss, row[0]


def test_an_owner_remedy_envelope_is_still_decidable_three_hours_later(
    connection: psycopg.Connection[Any],
) -> None:
    """A loss proposed while the owner is out: approved three hours later, and paid."""

    shop, loss, expires_at = _loss_envelope(connection, NOW)
    assert expires_at - NOW > timedelta(hours=24)
    store_id, staff, _, _ = shop
    _approve(connection, store_id, loss.approval_id, NOW + timedelta(hours=3))
    paid = _execute(connection, loss.proposal_id, staff, NOW + timedelta(hours=3, minutes=5))
    assert paid.amount_vnd == 20_000


def test_an_owner_remedy_envelope_still_expires_after_its_window(
    connection: psycopg.Connection[Any],
) -> None:
    """The window is longer, not open-ended: at its close the decision is an expiry."""

    shop, loss, expires_at = _loss_envelope(connection, NOW)
    store_id, staff, _, _ = shop
    with pytest.raises(ApprovalStateError, match="expired"):
        _approve(connection, store_id, loss.approval_id, expires_at)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM approval_request_states WHERE approval_request_id = %s",
            (loss.approval_id,),
        )
        state = cursor.fetchone()
    assert state is not None and state[0] == "EXPIRED"
    with pytest.raises(RemedyStateError) as refused:
        _execute(connection, loss.proposal_id, staff, expires_at + timedelta(minutes=1))
    assert refused.value.reason_code == "REMEDY_APPROVAL_REQUIRED"


def test_the_window_closes_at_midnight_in_nha_trang_after_the_next_business_day(
    connection: psycopg.Connection[Any],
) -> None:
    requested_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    _, _, expires_at = _loss_envelope(connection, requested_at)
    local = expires_at.astimezone(NHA_TRANG)
    assert (local.hour, local.minute, local.second) == (0, 0, 0)
    assert (local.date() - requested_at.astimezone(NHA_TRANG).date()).days == 2


def test_a_non_remedy_owner_envelope_still_expires_after_ten_minutes(
    connection: psycopg.Connection[Any],
) -> None:
    """Cancelling an active order is an owner action too, and its window did not move."""

    store_id, staff, order_id, _ = _shop(connection, lines=(SHIRTS,))
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_quote_snapshot_hash FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    stored = ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.CANCEL_ACTIVE_ORDER,
            APPROVAL_RESOURCE_TYPES[ApprovalAction.CANCEL_ACTIVE_ORDER],
            order_id,
            1,
            str(row[0]),
            "JCS-SHA256-V1:" + "c" * 64,
            "order-policy-v1",
            staff.staff_user_id,
            f"cancel-{uuid4().hex}",
            uuid4(),
            store_id=store_id,
            requested_at=NOW,
        ),
    )
    assert stored.expires_at == NOW + timedelta(minutes=10)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    with pytest.raises(ApprovalStateError, match="expired"):
        ApprovalRepository().decide(
            connection,
            ApprovalDecisionCommand(
                approval_request_id=stored.approval_request_id,
                decision=ApprovalDecision.APPROVED,
                observed_resource_version=1,
                observed_snapshot_hash=str(row[0]),
                observed_rendered_hash="JCS-SHA256-V1:" + "c" * 64,
                reason_code="OWNER_APPROVED_CANCEL",
                principal=owner,
                correlation_id=uuid4(),
                decided_at=NOW + timedelta(minutes=10),
            ),
        )
