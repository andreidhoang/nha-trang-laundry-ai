"""Live evidence that a published promotion reaches the counter, and cannot move under it.

`PROMO-WIRING-001`, `DEC-002`, `DEC-021`. The engine in `promotion.py` was complete, tested and
unreachable: every import outside its own tests was a synthetic eval or the package `__init__`. What
these tests run is the wiring, against real PostgreSQL and through `OperationsService`, because the
two things the wiring can get wrong are both things only the whole path shows.

The first is the item's "done when": the owner starts a programme by publishing a document, and the
next quote priced at the counter carries the discount. No deploy, no constant, no engineer.

The second is that the number must not move between the price being read aloud and the customer
saying yes. A promotion is keyed to `accepted_at`, which does not exist when the price is quoted, so
the quote freezes a provisional discount and acceptance re-evaluates the same published programme
against the real moment. If they disagree by one dong the acceptance is refused.

Today is 18/09/2026 and the shop's one confirmed programme ended on 31/08/2026, so the honest answer
at this counter is zero -- and one test here says so with the date, because a zero with no sentence
beside it is the same failure as drawing a null total as `0`.
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
    UnresolvedQuoteResult,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.promotions import publish_promotion_policy
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import FulfillmentMode, QuantityBasis, Unit
from nha_trang_laundry_domain.promotion import PromotionReason
from nha_trang_laundry_domain.quote_composition import (
    PROMOTION_CHANGED_SINCE_QUOTE,
    PROMOTION_NOT_PUBLISHED,
    RequestedLine,
)

ROOT = Path(__file__).resolve().parents[3]
OWNER_SEED_ID = UUID("00000000-0000-0000-0000-0000000009d1")
STANDARD = "STANDARD_WASH_DRY"
#: 6 kg of standard wash is 120.000 d at 20.000/kg -- `STD_WASH_DRY_GE6`, the far side of the 6 kg
#: cliff. The wash arm of the programme is 30%, so a live programme takes 36.000 d off it.
LIST_VND = 120_000
WASH_RATE_BPS = 3_000
LIVE_DISCOUNT_VND = 36_000
DOCUMENT: dict[str, Any] = json.loads(
    (ROOT / "templates" / "promotion-policy-dec-002.json").read_text(encoding="utf-8")
)


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


def _publish_pricebook(connection: Any) -> None:
    publish_pricebook(
        connection,
        actor_id=OWNER_SEED_ID,
        source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
    )


def _publish_live_programme(connection: Any, *, ends_in: timedelta = timedelta(days=30)) -> None:
    """The owner starts a programme covering right now, by publishing a document and nothing else.

    The window is anchored to the wall clock rather than to a fixed calendar date because the
    shipped programme has already ended: a test pinned to July 2026 would quietly stop exercising a
    live programme the moment the clock passed it, which is exactly the failure this item is about.
    """

    now = datetime.now(UTC)
    payload = deepcopy(DOCUMENT)
    payload["start_at"] = (now - timedelta(days=1)).isoformat()
    payload["end_at_exclusive"] = (now + ends_in).isoformat()
    publish_promotion_policy(connection, actor_id=OWNER_SEED_ID, payload=payload)


def _publish_shipped_programme(connection: Any) -> None:
    """The one programme the owner actually confirmed: 17/07/2026 to 31/08/2026 inclusive, over."""

    publish_promotion_policy(connection, actor_id=OWNER_SEED_ID, payload=DOCUMENT)


def _quote(
    service: OperationsService, *, store_id: UUID, staff: StaffPrincipal
) -> QuoteRevisionResult:
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine(STANDARD, "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    return priced


def _accept(
    service: OperationsService,
    *,
    store_id: UUID,
    staff: StaffPrincipal,
    quote: QuoteRevisionResult,
) -> QuoteRevisionResult | UnresolvedQuoteResult:
    return service.accept_quote(
        store_id=store_id,
        quote_id=quote.quote_id,
        expected_current_revision=quote.revision,
        expected_snapshot_hash=quote.snapshot_hash,
        idempotency_key=f"accept-{uuid4().hex}",
        principal=staff,
    )


# --- the owner runs a promotion without a deploy -------------------------------------------------


def test_the_owner_publishes_a_programme_and_the_next_quote_carries_its_discount(
    connection: Any, service: OperationsService
) -> None:
    """The item's "done when", end to end and through the real command path.

    Nothing is deployed between the first quote and the second. The only thing that changes is a
    published `PROMOTION_POLICY` version, and 120.000 d becomes 84.000 d because the document says
    30% on `STANDARD_WASH_DRY`.
    """

    _publish_pricebook(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)

    before = _quote(service, store_id=store_id, staff=staff)
    assert before.net_service_subtotal_vnd == LIST_VND
    assert before.promotion is None
    assert PROMOTION_NOT_PUBLISHED in before.reason_codes

    _publish_live_programme(connection)

    after = _quote(service, store_id=store_id, staff=staff)
    assert after.list_service_subtotal_vnd == LIST_VND
    assert after.net_service_subtotal_vnd == LIST_VND - LIVE_DISCOUNT_VND
    assert after.display_total_min_vnd == LIST_VND - LIVE_DISCOUNT_VND
    assert PROMOTION_NOT_PUBLISHED not in after.reason_codes
    assert PromotionReason.PROMOTION_APPLIED.value in after.reason_codes

    promotion = after.promotion
    assert promotion is not None
    assert promotion.policy_code == "PROMO_WET30_DRY40_20260717_20260831"
    assert promotion.configuration_version == 1
    assert promotion.discount_amount_vnd == LIVE_DISCOUNT_VND
    assert promotion.rate_bps == (WASH_RATE_BPS,)
    assert promotion.inside_interval is True
    # Provisional, and the response says so: eligibility is keyed to `accepted_at` and nobody has
    # accepted anything. The console renders that rather than presenting the discount as settled.
    assert promotion.status == "PROVISIONAL"
    assert promotion.eligibility_resolved is False


def test_accepting_inside_the_window_finalises_the_same_number_to_the_dong(
    connection: Any, service: OperationsService
) -> None:
    """`DEC-021`: the customer agreed to a price read aloud, and this is where it survives.

    The re-evaluation at acceptance uses the server's own `accepted_at`, which falls inside the
    window, so eligibility resolves and the discount finalises unchanged at 36.000 d.
    """

    _publish_pricebook(connection)
    _publish_live_programme(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = _quote(service, store_id=store_id, staff=staff)

    accepted = _accept(service, store_id=store_id, staff=staff, quote=priced)
    assert isinstance(accepted, QuoteRevisionResult)
    assert accepted.status == "ACCEPTED_FINAL"
    assert accepted.net_service_subtotal_vnd == LIST_VND - LIVE_DISCOUNT_VND
    assert accepted.display_total_min_vnd == LIST_VND - LIVE_DISCOUNT_VND
    assert accepted.promotion is not None
    assert accepted.promotion.discount_amount_vnd == LIVE_DISCOUNT_VND
    # The frozen figure on the accepted revision is the quoted one, read back rather than
    # recomputed: same programme, same version, same dong.
    assert priced.promotion is not None
    assert accepted.promotion.policy_code == priced.promotion.policy_code
    assert accepted.promotion.configuration_version == priced.promotion.configuration_version


def test_a_programme_published_between_the_quote_and_the_handshake_refuses_the_acceptance(
    connection: Any, service: OperationsService
) -> None:
    """The failure the freeze exists to prevent, arriving in the direction nobody expects.

    The customer was read 120.000 d. A programme is published while they are deciding, so the till
    would now compute 84.000 d. That is still a price they never agreed to, so the acceptance is
    refused and a person prices the bag again -- which is the only way the customer gets to hear the
    lower number and say yes to it.

    Nothing is written: `_AcceptanceUnresolved` rolls the idempotency claim back with it, so the
    retry after re-pricing reaches the engine rather than replaying this refusal.
    """

    _publish_pricebook(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = _quote(service, store_id=store_id, staff=staff)
    assert priced.net_service_subtotal_vnd == LIST_VND

    _publish_live_programme(connection)

    refused = _accept(service, store_id=store_id, staff=staff, quote=priced)
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == (PROMOTION_CHANGED_SINCE_QUOTE,)


def test_a_programme_republished_between_the_quote_and_the_handshake_refuses_too(
    connection: Any, service: OperationsService
) -> None:
    """Re-verifying against a different document is not re-verification.

    The second document ends the programme a day earlier and is otherwise identical, so the
    arithmetic still comes to 36.000 d -- and the acceptance is refused anyway, on version identity,
    before any arithmetic happens. Invariant 8's reading of "the content the approval bound" covers
    the programme as much as it covers the lines, and a number agreeing across two documents is a
    coincidence rather than a re-verification.
    """

    _publish_pricebook(connection)
    _publish_live_programme(connection, ends_in=timedelta(days=30))
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = _quote(service, store_id=store_id, staff=staff)
    assert priced.promotion is not None
    assert priced.promotion.configuration_version == 1

    _publish_live_programme(connection, ends_in=timedelta(days=29))

    refused = _accept(service, store_id=store_id, staff=staff, quote=priced)
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == (PROMOTION_CHANGED_SINCE_QUOTE,)


# --- today's honest answer, with its reason ------------------------------------------------------


def test_the_shipped_programme_has_ended_so_the_quote_says_the_interval_did_not_the_engine(
    connection: Any, service: OperationsService
) -> None:
    """18/09/2026, and the shop's one confirmed programme ended on 31/08/2026.

    The discount is zero, as it was before this item shipped -- but for a different and stateable
    reason. `PROMOTION_OUTSIDE_INTERVAL` says a programme ran and has ended; the deleted
    `PROMOTION_NOT_EVALUATED` said nothing had been assessed. The end date rides on the response so
    a console can render the sentence instead of the bare zero.
    """

    _publish_pricebook(connection)
    _publish_shipped_programme(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)

    priced = _quote(service, store_id=store_id, staff=staff)
    assert priced.net_service_subtotal_vnd == LIST_VND
    assert priced.display_total_min_vnd == LIST_VND
    assert PromotionReason.PROMOTION_OUTSIDE_INTERVAL.value in priced.reason_codes
    assert PROMOTION_NOT_PUBLISHED not in priced.reason_codes

    promotion = priced.promotion
    assert promotion is not None
    assert promotion.discount_amount_vnd == 0
    assert promotion.inside_interval is False
    # 01/09/2026 00:00 +07:00 is 31/08/2026 17:00 UTC: the exclusive bound, whose day before is the
    # last day the programme covered.
    assert promotion.interval_end_at_exclusive == "2026-08-31T17:00:00.000000Z"


def test_an_expired_programme_still_accepts_because_the_number_did_not_move(
    connection: Any, service: OperationsService
) -> None:
    """Today's case through the handshake. The quote showed 120.000 d because the programme had
    ended, the re-evaluation finds the same zero, so nothing moved and the bag is taken. A refusal
    here would mean the shop could not accept a single quote until somebody retired the document.
    """

    _publish_pricebook(connection)
    _publish_shipped_programme(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = _quote(service, store_id=store_id, staff=staff)

    accepted = _accept(service, store_id=store_id, staff=staff, quote=priced)
    assert isinstance(accepted, QuoteRevisionResult)
    assert accepted.status == "ACCEPTED_FINAL"
    assert accepted.display_total_min_vnd == LIST_VND
    assert PromotionReason.PROMOTION_OUTSIDE_INTERVAL.value in accepted.reason_codes


# --- PROMO-FIX-001: what the console is actually served, and what acceptance may not waive -------


def test_the_accepted_revision_the_console_reads_says_the_eligibility_is_settled(
    connection: Any, service: OperationsService
) -> None:
    """The frozen trace is the only promotion state a console ever sees, so it has to be true.

    `_promotion_mapping` reads it off the stored revision and recomputes nothing, deliberately. That
    made the defect total: an accepted revision inherited the quote's trace, so the counter saw
    `PROMOTION PROVISIONAL` and "khuyến mãi chưa chốt" on a bag that had been handed over, paid for
    and recorded with a resolved eligibility event -- for ever, because the row is immutable.

    The money is identical on both revisions, which is the point: nothing here is a re-price. What
    changes is the statement, from "not yet decided" to what acceptance decided.
    """

    _publish_pricebook(connection)
    _publish_live_programme(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = _quote(service, store_id=store_id, staff=staff)
    assert priced.promotion is not None
    assert priced.promotion.status == "PROVISIONAL"
    assert priced.promotion.eligibility_resolved is False
    assert "PROMOTION_ELIGIBILITY_UNRESOLVED" in priced.reason_codes

    accepted = _accept(service, store_id=store_id, staff=staff, quote=priced)
    assert isinstance(accepted, QuoteRevisionResult)
    assert accepted.promotion is not None
    assert accepted.promotion.status == "ELIGIBLE"
    assert accepted.promotion.eligibility_resolved is True
    assert accepted.promotion.discount_amount_vnd == LIVE_DISCOUNT_VND
    assert "PROMOTION_ELIGIBILITY_UNRESOLVED" not in accepted.reason_codes
    assert PromotionReason.PROMOTION_APPLIED.value in accepted.reason_codes
    # The facts acceptance did not change are still there. Only the promotion is re-stated.
    assert "TAX_TREATMENT_UNVERIFIED" in accepted.reason_codes


def test_a_service_the_owner_left_unconfirmed_is_sold_at_list_price_not_refused(
    connection: Any, service: OperationsService
) -> None:
    """The trading test, through the real command path and against real PostgreSQL.

    `IRON_SUIT` is `HUMAN_CONFIRM` in the owner's published document -- the storefront sign does not
    mention ironing, and the owner recorded that rather than guessing. The promotion therefore does
    not apply to the line, the customer is charged the published 150.000 d, and the bag is taken.

    Both halves matter and the second is the one that was wrong. `PROMO-WIRING-001` put
    `APPLY_PROMOTION` in `required_approvals` here, and `PROMO-FIX-001` then refused acceptance
    while it was outstanding -- which nothing in this system can ever discharge, because no route
    reads an `ApplyPromotion` envelope and re-quoting reproduces the same tuple from the same
    document. Publishing a promotion would have made every unconfirmed service unsellable for the
    programme's whole life, which is a worse defect than the bypass it replaced: it stops the shop
    trading, and a shop that met it would keep those garments off the system entirely.
    """

    _publish_pricebook(connection)
    _publish_live_programme(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)

    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine("IRON_SUIT", "1", Unit.ITEM, QuantityBasis.STAFF_MEASUREMENT),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    # Nothing is demanded of anybody: this is the assertion whose opposite closed the shop.
    assert priced.required_approvals == ()
    assert PromotionReason.PROMOTION_TARGET_REQUIRES_HUMAN.value in priced.reason_codes
    assert priced.net_service_subtotal_vnd == priced.list_service_subtotal_vnd

    accepted = _accept(service, store_id=store_id, staff=staff, quote=priced)
    assert isinstance(accepted, QuoteRevisionResult)
    assert accepted.status == "ACCEPTED_FINAL"
    assert accepted.display_total_min_vnd == priced.display_total_min_vnd
    assert PromotionReason.PROMOTION_TARGET_REQUIRES_HUMAN.value in accepted.reason_codes
    assert accepted.required_approvals == ()

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM quote_revisions WHERE quote_id = %s", (priced.quote_id,)
        )
        row = cursor.fetchone()
    # Two revisions: the priced one and the accepted one. A sold bag, not a blocked counter.
    assert row is not None and row[0] == 2
