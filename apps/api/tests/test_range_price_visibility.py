"""Live evidence that the owner can see the number they are being asked to authorise.

`RANGE-APPROVAL-VISIBILITY-001`, a defect fix on `RANGE-PRICE-001`.

The shipped item raised a `SET_RANGE_PRICE` envelope carrying a `rendered_hash` and persisted the
proposed amounts nowhere. The approvals queue returned the digest, the console linked through to
the quote, and the quote renders the *band* — áo dài truyền thống 80.000-240.000 ₫ — because the
revision the envelope binds is the revision before any price was chosen. So a staff member could
agree 150.000 ₫ with the customer, propose 240.000 ₫, and the owner's approval — the only
second-party control over that number — passed it through unread.

Two properties are under test here and they pull in opposite directions, which is why they are in
one file:

  * **The amounts are readable.** Proposing stores them beside the band each was checked against,
    and a read keyed to the approval returns both for anyone assigned to that shop.
  * **The stored copy authorises nothing.** `apply_range_prices` still re-derives the digest from
    the amounts the caller holds and refuses unless it equals the one the owner approved. The
    tampering tests below are the proof: with the immutability trigger forced out of the way and a
    stored amount rewritten, the display read refuses and the application path is unmoved — it
    still refuses the tampered number and still prices the approved one.

The negative cases carry the weight, as they do in `test_range_price_command.py`. A screen that can
show a number is not the property; the property is that what it shows is what the envelope binds,
and that nothing downstream trusts the copy.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
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
from nha_trang_laundry_db.approvals import ApprovalDecision, ApprovalRepository
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.quotes import QuoteStateError
from nha_trang_laundry_db.range_prices import (
    RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH,
    RangePriceProposalIntegrityError,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import ApprovalAction, FulfillmentMode, QuantityBasis, Unit
from nha_trang_laundry_domain.quote_composition import RequestedLine
from nha_trang_laundry_domain.range_prices import RangePriceChoice

ROOT = Path(__file__).resolve().parents[3]
OWNER_SEED_ID = UUID("00000000-0000-0000-0000-0000000009c1")
AO_DAI = "DC_AO_DAI_TRADITIONAL"
#: Áo dài truyền thống, published 80.000-240.000 ₫.
BAND_MINIMUM = 80_000
BAND_MAXIMUM = 240_000
#: What the staff member agreed with the customer, and the only number the owner ever approves here.
CHOSEN = 150_000
#: What a dishonest proposal would rather the owner signed. In band, so nothing but the approver
#: reading it stands between this number and the customer's bill — which is the whole item.
TAMPERED = BAND_MAXIMUM
#: The floor a republication raises áo dài to, so the stored band and the live one differ.
NARROWED_MINIMUM = 200_000


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit: `OperationsService` opens its own connection and sees only committed rows."""

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
    """The same catalogue with áo dài's floor raised, so a genuinely newer version exists."""

    source = (ROOT / "templates/services-pricebook.csv").read_text(encoding="utf-8")
    narrowed = source.replace(
        f'{AO_DAI},dry_cleaning,"Áo dài truyền thống",bộ,{BAND_MINIMUM},{BAND_MAXIMUM},',
        f'{AO_DAI},dry_cleaning,"Áo dài truyền thống",bộ,{NARROWED_MINIMUM},{BAND_MAXIMUM},',
    )
    assert narrowed != source, "the pricebook row this test edits has changed shape"
    publish_pricebook(connection, actor_id=OWNER_SEED_ID, source=narrowed.encode("utf-8"))


def _contact(connection: Any) -> UUID:
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"range-visibility-{uuid4().hex[:12]}",
        correlation_id=uuid4(),
    )
    return resolved.binding.contact_id


def _band_quote(
    service: OperationsService, *, store_id: UUID, staff: StaffPrincipal
) -> QuoteRevisionResult:
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine(AO_DAI, "1", Unit.SET, QuantityBasis.STAFF_MEASUREMENT),),
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
    idempotency_key: str | None = None,
) -> RangePriceProposalResult:
    proposal = service.propose_range_prices(
        store_id=store_id,
        quote_id=quote.quote_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        choices=(RangePriceChoice(AO_DAI, amount),),
        idempotency_key=idempotency_key or f"propose-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(proposal, RangePriceProposalResult)
    return proposal


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


def _force_stored_amount(connection: Any, approval_id: UUID, amount: int) -> None:
    """Rewrite a stored amount with the immutability trigger forced out of the way.

    No application path can do this: `range_price_proposal_amounts_protected` refuses every UPDATE
    and `reject_operational_hard_delete` refuses every DELETE, which the test below asserts
    directly. Disabling the trigger here is a deliberate simulation of the one attacker this table
    cannot stop by itself — somebody with a direct connection to the database — so that the tests
    that follow are about what the *rest* of the system does when the copy is wrong, rather than
    about how hard it is to make it wrong.
    """

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE range_price_proposal_amounts DISABLE TRIGGER "
            "range_price_proposal_amounts_protected"
        )
        cursor.execute(
            "UPDATE range_price_proposal_amounts SET proposed_amount_vnd = %s "
            "WHERE approval_id = %s",
            (amount, approval_id),
        )
        cursor.execute(
            "ALTER TABLE range_price_proposal_amounts ENABLE TRIGGER "
            "range_price_proposal_amounts_protected"
        )


# --- the amounts are readable --------------------------------------------------------------------


def test_the_owner_can_read_the_exact_amount_and_the_band_it_was_checked_against(
    connection: Any, service: OperationsService
) -> None:
    """The defect, inverted. Before this item the read below did not exist and no other one
    returned the number: the queue held a digest and the quote screen held the band."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    record = service.read_range_price_proposal(
        approval_id=proposal.approval.approval_request_id, principal=owner
    )
    assert record is not None
    assert record.quote_id == banded.quote_id
    assert record.revision == banded.revision
    assert record.proposed_by == staff.staff_user_id
    # The digest the approver will hand back is the digest of the content they just read. Equality
    # here is not what authorises anything -- it is what makes the display honest.
    assert record.rendered_hash == proposal.rendered_hash
    assert len(record.lines) == 1
    line = record.lines[0]
    assert line.service_code == AO_DAI
    assert line.proposed_amount_vnd == CHOSEN
    assert (line.band_minimum_vnd, line.band_maximum_vnd) == (BAND_MINIMUM, BAND_MAXIMUM)


def test_the_stored_band_is_the_revisions_own_and_not_the_newest_published_one(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 4. The interval shown to the approver has to be the one that authorised the
    amount, which is the one the customer was read — not whichever version is live when they look.

    Without this the screen would say "80.000-240.000 ₫, đề nghị 150.000 ₫" today and
    "200.000-240.000 ₫, đề nghị 150.000 ₫" after a republication, and the second reads as a staff
    member proposing below the floor when nothing of the sort happened.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    _publish_narrowed_band(connection)

    record = service.read_range_price_proposal(
        approval_id=proposal.approval.approval_request_id, principal=owner
    )
    assert record is not None
    assert record.lines[0].band_minimum_vnd == BAND_MINIMUM
    assert record.lines[0].proposed_amount_vnd == CHOSEN


def test_the_queue_says_which_action_an_envelope_authorises(
    connection: Any, service: OperationsService
) -> None:
    """Four actions share the `QUOTE_REVISION` resource type, and the console's decision to show
    or withhold the approve control now turns on which one this is. The type alone cannot say."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    queued = service.list_pending_approvals(principal=owner, limit=100)
    mine = [
        item for item in queued if item.approval_request_id == proposal.approval.approval_request_id
    ]
    assert len(mine) == 1
    assert mine[0].action == ApprovalAction.SET_RANGE_PRICE.value
    assert mine[0].resource_type == "QUOTE_REVISION"


def test_a_replayed_proposal_does_not_write_a_second_set_of_amounts(
    connection: Any, service: OperationsService
) -> None:
    """The envelope is idempotent, so the amounts under it must be too. A second row for one
    approval would give an approver two numbers and no way to know which is authorised."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    key = f"propose-{uuid4().hex}"

    first = _propose(service, store_id=store_id, staff=staff, quote=banded, idempotency_key=key)
    second = _propose(service, store_id=store_id, staff=staff, quote=banded, idempotency_key=key)
    assert second.approval.approval_request_id == first.approval.approval_request_id
    assert second.approval.replayed

    record = service.read_range_price_proposal(
        approval_id=first.approval.approval_request_id, principal=owner
    )
    assert record is not None
    assert len(record.lines) == 1
    assert record.lines[0].proposed_amount_vnd == CHOSEN


# --- who may read them ---------------------------------------------------------------------------


def test_a_member_of_another_store_cannot_read_this_shops_proposal(
    connection: Any, service: OperationsService
) -> None:
    """Membership comes from the stored row, so naming somebody else's approval teaches nothing."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    elsewhere = _staff(connection, uuid4(), StaffRole.OWNER_ADMIN)
    with pytest.raises(StoreAccessError):
        service.read_range_price_proposal(
            approval_id=proposal.approval.approval_request_id, principal=elsewhere
        )


def test_a_role_that_cannot_approve_cannot_read_what_is_being_approved(
    connection: Any, service: OperationsService
) -> None:
    """The same two roles `require_approval_staff` admits, re-checked in the service.

    An operator who may propose is not thereby entitled to read back every proposal in the shop;
    this read exists for the approver, and the route is the approver's route.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    with pytest.raises(StoreAccessError):
        service.read_range_price_proposal(
            approval_id=proposal.approval.approval_request_id, principal=staff
        )


def test_an_approval_with_no_stored_amounts_reads_as_absent_rather_than_as_empty(
    connection: Any, service: OperationsService
) -> None:
    """The fail-closed residue. An envelope raised by some path that never recorded amounts -- or
    a crash between the two writes -- must be unreadable, because the console's rule is "no
    amounts, no approve button" and an empty answer would have to be spelled the same way.

    Built here through a bare `ApprovalRepository.request`, which is exactly what a
    `SET_RANGE_PRICE` envelope without the second write looks like.
    """

    from nha_trang_laundry_db.approvals import ApprovalRequestCommand
    from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)

    stored = ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.SET_RANGE_PRICE,
            APPROVAL_RESOURCE_TYPES[ApprovalAction.SET_RANGE_PRICE],
            banded.quote_id,
            banded.revision,
            banded.snapshot_hash,
            "JCS-SHA256-V1:" + "0" * 64,
            "range-price-published-band-v1",
            staff.staff_user_id,
            f"bare-{uuid4().hex}",
            uuid4(),
            store_id=store_id,
        ),
    )

    assert (
        service.read_range_price_proposal(approval_id=stored.approval_request_id, principal=owner)
        is None
    )


# --- the stored copy authorises nothing ----------------------------------------------------------


def test_the_stored_amounts_are_immutable_and_undeletable(
    connection: Any, service: OperationsService
) -> None:
    """The first line of defence, in the schema rather than in a repository.

    An editable proposal would make invariant 8 a comment: the owner reads one number, somebody
    rewrites it, and the approval that was given for the first is applied to the second. Both
    statements are the database's own, so no later code path can route around them.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    approval_id = proposal.approval.approval_request_id

    for statement, parameters in (
        (
            "UPDATE range_price_proposal_amounts SET proposed_amount_vnd = %s "
            "WHERE approval_id = %s",
            (TAMPERED, approval_id),
        ),
        ("DELETE FROM range_price_proposal_amounts WHERE approval_id = %s", (approval_id,)),
        ("UPDATE range_price_proposals SET revision = 99 WHERE approval_id = %s", (approval_id,)),
        ("DELETE FROM range_price_proposals WHERE approval_id = %s", (approval_id,)),
    ):
        with (
            pytest.raises(psycopg.errors.RaiseException),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, parameters)


def test_an_amount_outside_the_stored_band_cannot_be_written_at_all(
    connection: Any, service: OperationsService
) -> None:
    """The CHECK, tested directly. The server already refuses an out-of-band amount before any
    envelope exists; this is the same rule in the schema, so a row that would display an
    unauthorised number to an approver is not a row this database holds."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
                INSERT INTO range_price_proposal_amounts (
                    approval_id, service_code, band_minimum_vnd, band_maximum_vnd,
                    proposed_amount_vnd
                ) VALUES (%s, 'DC_AO_LONG_THU', %s, %s, %s)
                """,
            (
                proposal.approval.approval_request_id,
                BAND_MINIMUM,
                BAND_MAXIMUM,
                BAND_MAXIMUM + 1,
            ),
        )


def test_a_tampered_stored_amount_is_withheld_instead_of_shown(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 8, from the display side.

    With the trigger forced out of the way and 150.000 ₫ rewritten to 240.000 ₫, the read
    re-derives the digest from the stored rows, finds it is not the one the envelope binds, and
    refuses. It does *not* return the amount with a warning: a number on an approval screen is read
    as the number being approved, and there is no caveat that makes a wrong one safe.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)

    _force_stored_amount(connection, proposal.approval.approval_request_id, TAMPERED)

    with pytest.raises(RangePriceProposalIntegrityError) as refused:
        service.read_range_price_proposal(
            approval_id=proposal.approval.approval_request_id, principal=owner
        )
    assert str(refused.value) == RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH


def test_a_tampered_stored_amount_cannot_change_what_the_approval_authorises(
    connection: Any, service: OperationsService
) -> None:
    """The property the whole design turns on, stated as a test.

    `RANGE-PRICE-001` deliberately re-derives the rendered digest from the amounts the caller is
    holding rather than comparing it against anything stored, and this item did not weaken that.
    So with the stored copy rewritten to 240.000 ₫ behind everyone's back:

      * applying 240.000 ₫ is refused — the envelope binds the digest of 150.000 ₫, and the stored
        row has no say in it;
      * applying 150.000 ₫ still succeeds and still prices at 150.000 ₫ — the owner's approval is
        unaffected by what happened to the copy.

    If the stored amounts were ever made the thing the hash is checked against, the first of those
    would pass and the second would fail. That is the regression this test exists to catch.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    _approve(service, proposal=proposal, owner=owner)

    _force_stored_amount(connection, proposal.approval.approval_request_id, TAMPERED)

    with pytest.raises(QuoteStateError):
        _apply(
            service,
            store_id=store_id,
            staff=staff,
            quote=banded,
            proposal=proposal,
            amount=TAMPERED,
        )

    closed = _apply(service, store_id=store_id, staff=staff, quote=banded, proposal=proposal)
    assert isinstance(closed, QuoteRevisionResult)
    assert closed.display_total_min_vnd == closed.display_total_max_vnd == CHOSEN


def test_editing_the_line_after_the_proposal_leaves_the_stored_amounts_bound_to_the_old_revision(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 8 by construction rather than by a rule anybody wrote.

    A second proposal on the same revision raises a second envelope with its own amounts, and each
    read answers about its own. What must never happen is one envelope's amounts being readable as
    another's — an amount approved for one garment must not be reusable on another.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    owner = _extra_staff(connection, store_id, StaffRole.OWNER_ADMIN)
    banded = _band_quote(service, store_id=store_id, staff=staff)

    first = _propose(service, store_id=store_id, staff=staff, quote=banded, amount=CHOSEN)
    second = _propose(service, store_id=store_id, staff=staff, quote=banded, amount=BAND_MINIMUM)
    assert second.approval.approval_request_id != first.approval.approval_request_id

    read_first = service.read_range_price_proposal(
        approval_id=first.approval.approval_request_id, principal=owner
    )
    read_second = service.read_range_price_proposal(
        approval_id=second.approval.approval_request_id, principal=owner
    )
    assert read_first is not None and read_second is not None
    assert read_first.lines[0].proposed_amount_vnd == CHOSEN
    assert read_second.lines[0].proposed_amount_vnd == BAND_MINIMUM
    assert read_first.rendered_hash != read_second.rendered_hash


def test_the_proposal_is_recorded_with_its_event_audit_and_outbox_rows(
    connection: Any, service: OperationsService
) -> None:
    """Invariant 5. The amounts are an attestation about money, so they arrive with the ledger
    records every material change carries — including the amounts themselves in the domain event,
    because an event log that recorded only a digest leaves an auditor asking the same question
    the owner was asking."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    proposal = _propose(service, store_id=store_id, staff=staff, quote=banded)
    approval_id = proposal.approval.approval_request_id

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_type, payload FROM domain_events "
            "WHERE aggregate_type = 'RANGE_PRICE_PROPOSAL' AND aggregate_id = %s",
            (approval_id,),
        )
        events = cursor.fetchall()
        cursor.execute(
            "SELECT action FROM audit_events "
            "WHERE aggregate_type = 'RANGE_PRICE_PROPOSAL' AND aggregate_id = %s",
            (approval_id,),
        )
        audits = cursor.fetchall()
        cursor.execute(
            "SELECT event_type FROM outbox_events "
            "WHERE aggregate_type = 'RANGE_PRICE_PROPOSAL' AND aggregate_id = %s",
            (approval_id,),
        )
        outbox = cursor.fetchall()

    assert [str(row[0]) for row in events] == ["RANGE_PRICE_PROPOSAL_RECORDED"]
    assert events[0][1]["amounts"] == [{"service_code": AO_DAI, "proposed_amount_vnd": CHOSEN}]
    assert [str(row[0]) for row in audits] == ["RANGE_PRICE_PROPOSE"]
    assert [str(row[0]) for row in outbox] == ["range_price.proposal_recorded.v1"]


def test_nothing_is_recorded_when_the_amount_is_refused(
    connection: Any, service: OperationsService
) -> None:
    """A refusal costs nothing, and that includes leaving no display record behind. A row for an
    amount no envelope was ever raised for would be a number an approver could be shown.

    The clock is read before and after so this is an assertion about *this* proposal rather than
    about the table being globally empty.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    banded = _band_quote(service, store_id=store_id, staff=staff)
    before = datetime.now(UTC)

    refused = service.propose_range_prices(
        store_id=store_id,
        quote_id=banded.quote_id,
        expected_current_revision=banded.revision,
        expected_snapshot_hash=banded.snapshot_hash,
        choices=(RangePriceChoice(AO_DAI, BAND_MAXIMUM + 1),),
        idempotency_key=f"propose-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM range_price_proposals WHERE quote_id = %s AND proposed_at >= %s",
            (banded.quote_id, before),
        )
        assert int(cursor.fetchone()[0]) == 0
