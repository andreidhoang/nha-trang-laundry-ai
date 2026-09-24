"""A remedy credit is reserved by a quote and spent only by the order that uses it.

`REMEDY-001` burnt the credit in the transaction that wrote the credited quote revision. Everything
that can happen to a quote *after* that -- the customer adds a shirt and the bag is re-priced, the
price goes stale overnight, the customer walks away -- then lost the credit: the reprice composed a
fresh revision with no credit on it, and the credit row said "spent" against a revision nobody would
ever pay. `0042` rightly forbids handing a spent credit back, so the money was simply gone.

The lead's decided design, which these tests pin through the real command path:

* redeeming a credit against a quote **reserves** it: the credited revision is written, and the
  credit row is untouched;
* re-pricing that quote **carries the reservation forward** into the new revision automatically;
* an expired or abandoned quote releases it **implicitly**, because nothing was ever spent;
* the credit is **spent** in the transaction that creates the order from the accepted revision --
  and a credit reserved on two quotes can be spent by only one of them. The second conversion is
  refused with `REMEDY_CREDIT_ALREADY_REDEEMED`, and writes nothing.

And the money rule under all of it: a credit never makes a bill negative and is never quietly
shrunk. A reprice that leaves less to discount than the credit is worth *releases* the credit from
that quote -- stated on the revision as `REMEDY_CREDIT_RELEASED` -- so it stays owed in full.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from dataclasses import dataclass
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
)
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import OrderStateError
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand, QuoteStateError
from nha_trang_laundry_db.remedies import RemedyStateError
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    FulfillmentMode,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    compose_quote_revision,
)
from nha_trang_laundry_domain.remedies import (
    REMEDY_CREDIT_APPLIED,
    REMEDY_CREDIT_RELEASED,
    RemedyKind,
    RemedyRefusal,
)

# The remedy fixtures build a released, settled order and walk a proposal to an issued credit
# through every real repository. Reached by path for the reason `test_ops_board_postgres.py` gives.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import counter_ticket
from test_remedies import _execute, _propose, _shop

ROOT = Path(__file__).resolve().parents[3]
STANDARD = "STANDARD_WASH_DRY"
#: Under 6 kg standard wash is 25.000 d/kg (the owner-confirmed cliff sits at 6 kg).
PER_KG_UNDER_SIX = 25_000
LATE_CREDIT = 11_000


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


def _issued_credit(connection: Any, *, kind: RemedyKind = RemedyKind.LATE_DELIVERY_CREDIT) -> Any:
    """A store, its counter staff member, and one issued, unspent remedy credit."""

    mode = (
        FulfillmentMode.PICKUP_AND_RETURN
        if kind is RemedyKind.LATE_DELIVERY_CREDIT
        else FulfillmentMode.SELF_DROP_SELF_COLLECT
    )
    store_id, staff, order_id, incident_id = _shop(connection, mode=mode)
    if kind is RemedyKind.LATE_DELIVERY_CREDIT:
        DeliveryLegRepository().record(
            connection,
            RecordDeliveryLegCommand(
                order_id=order_id,
                leg_kind=DeliveryLegKind.RETURN,
                outcome=DeliveryLegOutcome.SUCCEEDED,
                principal=staff,
                correlation_id=uuid4(),
                recorded_at=datetime.now(UTC),
            ),
        )
        fields: dict[str, Any] = {"attested_late_by_minutes": 150}
    else:
        fields = {"order_line_id": "line-1", "amount_vnd": 80_000}
    proposal = _propose(
        connection, store_id, incident_id, staff, kind=kind, store_fault_attested=True, **fields
    )
    credit_id = _execute(connection, proposal.proposal_id, staff).credit_id
    assert credit_id is not None
    publish_pricebook(
        connection,
        actor_id=staff.staff_user_id,
        source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
    )
    return store_id, staff, credit_id


def _customer(connection: Any, store_id: UUID, staff: Any) -> tuple[UUID, UUID]:
    """A walk-in ticket and the intake request a quote is bound to, as the counter makes them."""

    contact_id = counter_ticket(connection, store_id=store_id, principal=staff)
    request_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO order_requests (
                id, store_id, contact_binding_id, conversation_binding_id, status, row_version,
                created_at
            ) VALUES (%s, %s, %s, %s, 'DRAFT', 1, %s)
            """,
            (request_id, store_id, contact_id, uuid4(), datetime.now(UTC)),
        )
    return contact_id, request_id


def _price(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: Any,
    request_id: UUID,
    kg: str,
    quote_id: UUID | None = None,
    after: QuoteRevisionResult | Any | None = None,
) -> QuoteRevisionResult:
    """Price (or re-price) a bag of standard wash through the real command path."""

    revision = 0 if after is None else after.revision
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=request_id,
        lines=(RequestedLine(STANDARD, kg, Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
        quote_id=quote_id,
        expected_current_revision=revision,
        expected_row_version=revision,
    )
    assert isinstance(priced, QuoteRevisionResult), priced
    return priced


def _reserve(
    service: OperationsService, *, store_id: UUID, staff: Any, credit_id: UUID, quote: Any
) -> Any:
    return service.redeem_remedy_credit(
        store_id=store_id,
        quote_id=quote.quote_id,
        credit_id=credit_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        idempotency_key=f"redeem-{uuid4().hex}",
        principal=staff,
    )


def _accept(service: OperationsService, *, store_id: UUID, staff: Any, quote: Any) -> Any:
    accepted = service.accept_quote(
        store_id=store_id,
        quote_id=quote.quote_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        idempotency_key=f"accept-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(accepted, QuoteRevisionResult), accepted
    return accepted


def _order(
    service: OperationsService, *, store_id: UUID, staff: Any, contact_id: UUID, accepted: Any
) -> Any:
    return service.create_order(
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


def _credit_row(connection: Any, credit_id: UUID) -> tuple[Any, Any, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT redeemed_at, redeemed_quote_id, redeemed_quote_revision "
            "FROM remedy_credits WHERE id = %s",
            (credit_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def _spent_on(connection: Any, credit_id: UUID) -> tuple[UUID, int] | None:
    redeemed_at, quote_id, revision = _credit_row(connection, credit_id)
    if redeemed_at is None:
        return None
    return UUID(str(quote_id)), int(revision)


# --- the defect ----------------------------------------------------------------------------------


def test_repricing_a_quote_carries_its_reserved_credit_into_the_new_revision(
    connection: Any, service: OperationsService
) -> None:
    """The reviewer's reproduction, through the counter's real command path.

    4 kg is 100.000 d; the 11.000 d credit makes it 89.000 d. The customer adds a kilo and the bag
    is re-priced: 5 kg is 125.000 d, and the credit must still be on it -- 114.000 d. Before the fix
    the reprice composed a revision with no credit on it (125.000 d) while the credit row already
    said it had been spent on revision 2.
    """

    store_id, staff, credit_id = _issued_credit(connection)
    contact_id, request_id = _customer(connection, store_id, staff)
    first = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    assert first.display_total_min_vnd == 4 * PER_KG_UNDER_SIX

    credited = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=first)
    assert credited.display_total_vnd == 4 * PER_KG_UNDER_SIX - LATE_CREDIT
    # Reserved, not spent: nothing about this quote has become an order yet.
    assert _spent_on(connection, credit_id) is None

    repriced = _price(
        service,
        store_id=store_id,
        staff=staff,
        request_id=request_id,
        kg="5",
        quote_id=first.quote_id,
        after=credited,
    )
    assert repriced.display_total_min_vnd == 5 * PER_KG_UNDER_SIX - LATE_CREDIT
    assert REMEDY_CREDIT_APPLIED in repriced.reason_codes
    assert _spent_on(connection, credit_id) is None

    accepted = _accept(service, store_id=store_id, staff=staff, quote=repriced)
    assert accepted.display_total_min_vnd == 5 * PER_KG_UNDER_SIX - LATE_CREDIT
    _order(service, store_id=store_id, staff=staff, contact_id=contact_id, accepted=accepted)
    # Spent exactly when, and exactly where, the order was created.
    assert _spent_on(connection, credit_id) == (first.quote_id, accepted.revision)


def test_an_expired_quote_releases_its_credit_to_the_next_one(
    connection: Any, service: OperationsService
) -> None:
    """Nothing was spent on a quote that never became an order, so nothing has to be handed back.

    The first quote is priced two days ago -- `QUOTE_VALIDITY` is one day -- and the credit is
    reserved on it. It cannot be accepted. A fresh quote the next visit takes the same credit, and
    the order made from *that* one spends it.
    """

    store_id, staff, credit_id = _issued_credit(connection)
    _, stale_request = _customer(connection, store_id, staff)
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, version FROM configuration_versions "
            "WHERE config_type = 'PRICEBOOK' AND lifecycle = 'PUBLISHED'"
        )
        published = cursor.fetchone()
    assert published is not None
    stale_id = uuid4()
    long_ago = datetime.now(UTC) - timedelta(days=2)
    composed = compose_quote_revision(
        quote_id=stale_id,
        revision=1,
        rules=runtime_price_rules(pricebook),
        requested=(RequestedLine(STANDARD, "4", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
        pricebook=PricebookProvenance(
            UUID(str(published[0])), int(published[1]), pricebook.manifest.canonical_snapshot_hash
        ),
        priced_at=long_ago,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
    )
    assert isinstance(composed, ComposedQuote)
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, stale_request, composed.snapshot, 0, 0, staff.staff_user_id, uuid4(), long_ago
        ),
    )
    stale = _reserve(
        service,
        store_id=store_id,
        staff=staff,
        credit_id=credit_id,
        quote=_QuoteRef(stale_id, 1, composed.snapshot.document.snapshot_hash),
    )
    with pytest.raises(QuoteStateError, match="QUOTE_EXPIRED"):
        _accept(service, store_id=store_id, staff=staff, quote=stale)
    assert _spent_on(connection, credit_id) is None

    contact_id, request_id = _customer(connection, store_id, staff)
    fresh = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    credited = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=fresh)
    accepted = _accept(service, store_id=store_id, staff=staff, quote=credited)
    assert accepted.display_total_min_vnd == 4 * PER_KG_UNDER_SIX - LATE_CREDIT
    _order(service, store_id=store_id, staff=staff, contact_id=contact_id, accepted=accepted)
    assert _spent_on(connection, credit_id) == (fresh.quote_id, accepted.revision)


def test_a_credit_reserved_on_two_quotes_is_spent_by_only_one_order(
    connection: Any, service: OperationsService
) -> None:
    """The bearer instrument is presented twice; the till lets it be spent once.

    Both quotes may carry it while nothing is final -- that is what "reserved" means. The first
    order to be created spends it. The second is refused with the credit's own reason code and
    writes nothing: no order, and the quote stays open. Re-pricing that quote then releases the
    spent credit from it, stated on the revision, and the bag sells at its full price.
    """

    store_id, staff, credit_id = _issued_credit(connection)
    contact_a, request_a = _customer(connection, store_id, staff)
    contact_b, request_b = _customer(connection, store_id, staff)
    quote_a = _price(service, store_id=store_id, staff=staff, request_id=request_a, kg="4")
    quote_b = _price(service, store_id=store_id, staff=staff, request_id=request_b, kg="3")
    on_a = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=quote_a)
    on_b = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=quote_b)
    accepted_a = _accept(service, store_id=store_id, staff=staff, quote=on_a)
    accepted_b = _accept(service, store_id=store_id, staff=staff, quote=on_b)

    _order(service, store_id=store_id, staff=staff, contact_id=contact_a, accepted=accepted_a)
    assert _spent_on(connection, credit_id) == (quote_a.quote_id, accepted_a.revision)

    with pytest.raises(OrderStateError, match=RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED.value):
        _order(service, store_id=store_id, staff=staff, contact_id=contact_b, accepted=accepted_b)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM orders WHERE current_quote_id = %s", (quote_b.quote_id,)
        )
        orders = cursor.fetchone()
        cursor.execute("SELECT lifecycle FROM quotes WHERE id = %s", (quote_b.quote_id,))
        lifecycle = cursor.fetchone()
    assert orders is not None and orders[0] == 0
    assert lifecycle is not None and lifecycle[0] == "OPEN"
    # Still spent on A, once.
    assert _spent_on(connection, credit_id) == (quote_a.quote_id, accepted_a.revision)

    # The way out at the counter: price the bag again. The spent credit is released from it, the
    # revision says so, and the customer pays the full 75.000 d.
    repriced = _price(
        service,
        store_id=store_id,
        staff=staff,
        request_id=request_b,
        kg="3",
        quote_id=quote_b.quote_id,
        after=accepted_b,
    )
    assert repriced.display_total_min_vnd == 3 * PER_KG_UNDER_SIX
    assert REMEDY_CREDIT_RELEASED in repriced.reason_codes
    assert REMEDY_CREDIT_APPLIED not in repriced.reason_codes


def test_a_credit_never_makes_a_bill_negative_and_is_never_shrunk_to_fit(
    connection: Any, service: OperationsService
) -> None:
    """An 80.000 d damage credit on a 100.000 d bag, re-priced down to 50.000 d.

    Applying all of it would make the service total negative (invariant 2); applying 50.000 d of it
    and spending the credit would cancel 30.000 d of a debt nobody decided to cancel. So the reprice
    releases the credit from this quote: 50.000 d is the bill, the revision says why no credit is on
    it, and the full 80.000 d is still owed and can be reserved on a bill it fits.
    """

    store_id, staff, credit_id = _issued_credit(connection, kind=RemedyKind.DAMAGE_COMPENSATION)
    contact_id, request_id = _customer(connection, store_id, staff)
    first = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    credited = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=first)
    assert credited.display_total_vnd == 4 * PER_KG_UNDER_SIX - 80_000

    smaller = _price(
        service,
        store_id=store_id,
        staff=staff,
        request_id=request_id,
        kg="2",
        quote_id=first.quote_id,
        after=credited,
    )
    assert smaller.display_total_min_vnd == 2 * PER_KG_UNDER_SIX
    assert smaller.net_service_subtotal_vnd >= 0
    assert REMEDY_CREDIT_RELEASED in smaller.reason_codes
    assert REMEDY_CREDIT_APPLIED not in smaller.reason_codes
    assert _spent_on(connection, credit_id) is None

    # Released, not lost: the same credit lands in full on a bill it fits.
    bigger = _price(
        service,
        store_id=store_id,
        staff=staff,
        request_id=request_id,
        kg="4",
        quote_id=first.quote_id,
        after=smaller,
    )
    again = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=bigger)
    assert again.credit_vnd == 80_000
    accepted = _accept(service, store_id=store_id, staff=staff, quote=again)
    assert accepted.display_total_min_vnd == 4 * PER_KG_UNDER_SIX - 80_000
    _order(service, store_id=store_id, staff=staff, contact_id=contact_id, accepted=accepted)
    assert _spent_on(connection, credit_id) == (first.quote_id, accepted.revision)


def test_the_same_credit_cannot_discount_one_quote_twice(
    connection: Any, service: OperationsService
) -> None:
    store_id, staff, credit_id = _issued_credit(connection)
    _, request_id = _customer(connection, store_id, staff)
    first = _price(service, store_id=store_id, staff=staff, request_id=request_id, kg="4")
    credited = _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=first)
    with pytest.raises(RemedyStateError) as refused:
        _reserve(service, store_id=store_id, staff=staff, credit_id=credit_id, quote=credited)
    assert refused.value.reason_code == RemedyRefusal.REMEDY_CREDIT_ALREADY_ON_QUOTE.value


@dataclass(frozen=True)
class _QuoteRef:
    """The three fields `_reserve` and `_accept` read, for a quote written outside the service."""

    quote_id: UUID
    revision: int
    snapshot_hash: str
