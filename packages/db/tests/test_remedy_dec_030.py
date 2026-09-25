"""`DEC-030` through the real repositories: the promotion applies, the credit waits unspent.

`test_remedies.py::test_a_credit_refused_over_a_non_stacking_programme_survives_and_spends_later`
is the original order -- programme first, credit presented after, refused before anything moves.
This is the reverse order, the one `compose_quote_revision` used to get backwards: the credit is
reserved on a quote while no programme is running, the owner then publishes a
`stacking_allowed: false` programme, and the bag is priced again. Before `DEC-030` was implemented
the reprice withheld the programme and applied the credit (120.000 - 11.000 = 109.000 d). The ruling
charges the promoted 84.000 d and keeps the 11.000 d owed.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand
from nha_trang_laundry_db.remedies import (
    RemedyCreditRedemptionCommand,
    RemedyCreditRepository,
    read_reserved_remedy_credits,
)
from nha_trang_laundry_domain.catalog import FulfillmentMode, QuantityBasis, Unit
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.promotion import PromotionReason
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    compose_quote_revision,
    released_remedy_credit_ids,
    reserved_remedy_credits,
)
from nha_trang_laundry_domain.remedies import REMEDY_CREDIT_RELEASED
from test_remedies import (
    LATE_CREDIT,
    LINE_AMOUNT,
    NOW,
    PROMOTED_DISCOUNT_VND,
    PROMOTED_LIST_VND,
    ROOT,
    _issued_credit,
    _next_quote,
    _promoted_quote,
    _publish_live_programme,
)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _credit_redeemed_at(connection: Any, credit_id: Any) -> Any:
    with connection.cursor() as cursor:
        cursor.execute("SELECT redeemed_at FROM remedy_credits WHERE id = %s", (credit_id,))
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def test_a_credit_reserved_before_a_non_stacking_programme_waits_and_the_promotion_applies(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, credit_id, staff, credit_vnd = _issued_credit(connection)
    assert credit_vnd == LATE_CREDIT

    # 1. Priced with no programme running, and the credit reserved on it: 120.000 - 11.000.
    quote_id, plain, _ = _promoted_quote(connection, store_id, staff, None)
    assert plain.data.totals.display_total_min_vnd == PROMOTED_LIST_VND
    reserved = RemedyCreditRepository().redeem(
        connection,
        RemedyCreditRedemptionCommand(
            store_id=store_id,
            quote_id=quote_id,
            credit_id=credit_id,
            expected_current_revision=1,
            expected_snapshot_hash=plain.document.snapshot_hash,
            principal=staff,
            correlation_id=uuid4(),
            redeemed_at=NOW,
        ),
    )
    assert reserved.display_total_vnd == PROMOTED_LIST_VND - LATE_CREDIT

    # 2. The owner publishes the non-stacking programme, and the counter prices the bag again,
    # carrying what the replaced revision reserved -- exactly as `create_quote` does.
    published = _publish_live_programme(connection, staff.staff_user_id)
    with connection.cursor() as cursor:
        carried = read_reserved_remedy_credits(
            cursor, store_id=store_id, quote_id=quote_id, revision=2
        )
        container = QuoteRepository.find_container_by_id(cursor, store_id, quote_id)
    assert container is not None
    assert [credit.credit_id for credit in carried.credits] == [credit_id]
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    composition = compose_quote_revision(
        quote_id=quote_id,
        revision=3,
        rules=runtime_price_rules(pricebook),
        requested=(
            RequestedLine("STANDARD_WASH_DRY", "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),
        ),
        pricebook=PricebookProvenance(uuid4(), 1, pricebook.manifest.canonical_snapshot_hash),
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        promotion=published,
        remedy_credits=carried.credits,
        spent_remedy_credit_ids=carried.spent_ids,
    )
    assert isinstance(composition, ComposedQuote), composition
    snapshot = composition.snapshot
    # Written through the repository, whose guard refuses a revision that drops a reserved credit
    # without stating it: the release statement is what lets this revision exist.
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id,
            container.bound_order_request_id,
            snapshot,
            2,
            container.row_version,
            staff.staff_user_id,
            uuid4(),
            NOW,
        ),
    )

    # The customer's bill is the promoted price, and the credit is not on it.
    assert snapshot.data.totals.display_total_min_vnd == PROMOTED_LIST_VND - PROMOTED_DISCOUNT_VND
    assert PromotionReason.PROMOTION_APPLIED.value in snapshot.data.reason_codes
    assert PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN.value not in snapshot.data.reason_codes
    assert reserved_remedy_credits(snapshot) == ()
    assert released_remedy_credit_ids(snapshot) == {credit_id}
    assert REMEDY_CREDIT_RELEASED in snapshot.data.reason_codes

    # Unspent, and still owed: it lands in full on the next bill that carries no such programme.
    assert _credit_redeemed_at(connection, credit_id) is None
    next_quote_id, next_revision, next_hash = _next_quote(connection, store_id, staff)
    later = RemedyCreditRepository().redeem(
        connection,
        RemedyCreditRedemptionCommand(
            store_id=store_id,
            quote_id=next_quote_id,
            credit_id=credit_id,
            expected_current_revision=next_revision,
            expected_snapshot_hash=next_hash,
            principal=staff,
            correlation_id=uuid4(),
            redeemed_at=NOW,
        ),
    )
    assert later.credit_vnd == LATE_CREDIT
    assert later.net_service_subtotal_vnd == LINE_AMOUNT - LATE_CREDIT
