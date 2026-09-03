"""Live evidence for the first command path that produces a monetary artefact.

`QUOTE-COMMAND-001`. Each test here answers one of the item's `required_evidence` keys, and the
negative cases carry most of the weight: a create-quote route that only works is not the property
under test, because the failure mode this item guards against is convenience creeping into the money
path.
"""

from __future__ import annotations

import os
from collections.abc import Generator
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
    QuotePricingUnavailable,
    QuoteRevisionResult,
    UnresolvedQuoteResult,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteStateError
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import FulfillmentMode, QuantityBasis, Unit
from nha_trang_laundry_domain.pricebook_import import (
    EXPECTED_SERVICE_COUNT,
    import_pricebook_csv,
    runtime_price_rules,
)
from nha_trang_laundry_domain.pricing import PriceLine, price_lines
from nha_trang_laundry_domain.quote_composition import RequestedLine

ROOT = Path(__file__).resolve().parents[3]
STANDARD = "STANDARD_WASH_DRY"
OWNER_ID = UUID("00000000-0000-0000-0000-0000000009a1")
# TestClient speaks to `testserver`, which is what AuthSettings trusts by default. Using a host the
# app does not trust would make every request a 400 and prove nothing about the route.
ORIGIN = "http://testserver"


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit, because these tests set up state that a *different* connection must then see.

    `OperationsService` opens its own connection, so anything this one leaves inside an uncommitted
    transaction is invisible to the code under test — and worse, an open transaction holds locks
    that block the next test's `TRUNCATE` (see the reset guard in the repository conftest). With
    autocommit, `connection.transaction()` still issues a real BEGIN/COMMIT where a block wants one.
    """
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _ensure_store(connection: Any, store_id: UUID) -> None:
    """`STORE-REGISTRY-001`: `store_id` is a foreign key, so the shop exists before anyone joins it.

    The deploy-day runbook runs `scripts/bootstrap_store.py` before assigning anyone, and this is
    the fixture standing in for that step rather than an INSERT that skips it.
    """

    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )


def _staff(connection: Any, store_id: UUID | None, role: StaffRole) -> StaffPrincipal:
    """Create a staff row, and assign it to a store only when one is given."""
    if store_id is not None:
        _ensure_store(connection, store_id)
    staff_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, OWNER_ID):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO NOTHING
                """,
                (identifier, f"oidc-{identifier}"),
            )
        if store_id is not None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, 1)
                ON CONFLICT DO NOTHING
                """,
                (staff_id, store_id, OWNER_ID),
            )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _publish(connection: Any) -> None:
    publish_pricebook(
        connection,
        actor_id=OWNER_ID,
        source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
    )


def _lines(quantity: str, service_code: str = STANDARD, unit: Unit = Unit.KG) -> tuple[Any, ...]:
    return (RequestedLine(service_code, quantity, unit, QuantityBasis.STAFF_MEASUREMENT),)


def _ledger_counts(connection: Any, quote_id: UUID) -> tuple[int, int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM quote_revisions WHERE quote_id = %s", (quote_id,))
        revisions = int(cursor.fetchone()[0])
        counts = []
        for table in ("domain_events", "audit_events", "outbox_events"):
            cursor.execute(
                f"SELECT count(*) FROM {table} "
                "WHERE aggregate_type = 'QUOTE' AND aggregate_id = %s",
                (quote_id,),
            )
            counts.append(int(cursor.fetchone()[0]))
    return (revisions, *counts)  # type: ignore[return-value]


# --- route_result_equals_direct_engine_call ----------------------------------------------------


@pytest.mark.parametrize("quantity", ["1", "5.999", "6", "6.001", "12"])
def test_the_command_path_price_equals_a_direct_engine_call(
    connection: Any, service: OperationsService, quantity: str
) -> None:
    """Whatever the command path does, the money must equal what the engine alone would say."""
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    expected = price_lines(
        runtime_price_rules(
            import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
        ),
        (PriceLine(STANDARD, quantity, Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
    )[STANDARD]

    result = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines(quantity),
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(result, QuoteRevisionResult)
    assert result.net_service_subtotal_vnd == expected.list_amount_vnd
    assert result.list_service_subtotal_vnd == expected.list_amount_vnd
    # A walk-in has no delivery job, so the fee is a resolved zero and the customer is quoted the
    # total they will actually pay. Before the delivery engine was wired in, this was None on every
    # quote the shop could produce.
    assert result.display_total_min_vnd == expected.list_amount_vnd
    assert result.display_total_max_vnd == expected.list_amount_vnd


def test_the_six_kilogram_cliff_survives_the_whole_command_path(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    totals = {}
    for quantity in ("5.999", "6"):
        result = service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=uuid4(),
            lines=_lines(quantity),
            idempotency_key=f"quote-cliff-{quantity}",
            principal=staff,
        )
        assert isinstance(result, QuoteRevisionResult)
        totals[quantity] = result.net_service_subtotal_vnd
    assert totals == {"5.999": 149_975, "6": 120_000}


# --- unresolved_policy_propagates_and_writes_nothing -------------------------------------------


def test_an_unresolved_quote_returns_reason_codes_and_writes_no_row(
    connection: Any, service: OperationsService
) -> None:
    """The whole point of a fail-closed engine is lost if a refusal still leaves a row behind."""
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)

    result = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("not-a-weight"),
        idempotency_key="quote-unresolved",
        principal=staff,
    )
    assert isinstance(result, UnresolvedQuoteResult)
    assert result.reason_codes == ("MISSING_REQUIRED_FACT",)
    with connection.cursor() as cursor:
        for table in ("quotes", "quote_revisions"):
            cursor.execute(f"SELECT count(*) FROM {table}")
            assert int(cursor.fetchone()[0]) == 0, f"{table} must stay empty"
        # Scoped to QUOTE: publishing the pricebook legitimately wrote its own outbox events, and
        # asserting an empty table would pass for the wrong reason the day that changes.
        cursor.execute("SELECT count(*) FROM outbox_events WHERE aggregate_type = 'QUOTE'")
        assert int(cursor.fetchone()[0]) == 0
        # An unresolved outcome must not consume the idempotency key either: the caller has to be
        # able to correct the weight and retry with the same key.
        cursor.execute("SELECT count(*) FROM command_idempotency_records")
        assert int(cursor.fetchone()[0]) == 0


def test_a_range_priced_service_refuses_through_the_command_path(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    result = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("1", service_code="BED_PILLOW", unit=Unit.ITEM),
        idempotency_key="quote-range",
        principal=staff,
    )
    assert isinstance(result, UnresolvedQuoteResult)
    assert result.reason_codes == ("RANGE_PRICE_REQUIRES_HUMAN",)


def test_no_published_pricebook_means_no_price(connection: Any, service: OperationsService) -> None:
    """A deployment without an approved price list refuses rather than inventing one."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    with pytest.raises(QuotePricingUnavailable):
        service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=uuid4(),
            lines=_lines("6"),
            idempotency_key="quote-no-pricebook",
            principal=staff,
        )


def test_the_published_catalog_is_what_the_console_picker_offers(
    connection: Any, service: OperationsService
) -> None:
    """The picker serves the approved names from the same digest-checked payload that prices."""
    _publish(connection)
    services = service.list_published_services()
    assert len(services) == EXPECTED_SERVICE_COUNT
    standard = next(item for item in services if item.code == STANDARD)
    assert standard.display_name  # a name the operator reads, not the code they used to memorize
    assert standard.unit is Unit.KG
    assert all(item.display_name and item.category for item in services)


def test_no_published_pricebook_means_no_catalog(service: OperationsService) -> None:
    """The picker refuses the way the pricing path does — no degraded fallback list."""
    with pytest.raises(QuotePricingUnavailable):
        service.list_published_services()


def test_the_catalog_route_serves_the_published_services(
    connection: Any, service: OperationsService
) -> None:
    """Role-gated like the pricing surface, and readable without store membership: the catalog
    is deployment-global configuration, so membership is not a precondition."""
    _publish(connection)
    staff = _staff(connection, None, StaffRole.OPERATOR)
    app.dependency_overrides[current_principal] = lambda: staff
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.get("/internal/v1/pricebook/services", headers={"Origin": ORIGIN})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert len(body) == EXPECTED_SERVICE_COUNT
    assert {item["code"] for item in body} >= {STANDARD}
    assert all(set(item) == {"code", "display_name", "category", "unit"} for item in body)


def test_a_published_pricebook_cannot_be_edited_in_place(connection: Any) -> None:
    """The first defence against a changed price is that the edit does not succeed at all."""
    _publish(connection)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="immutable"),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            UPDATE configuration_versions
            SET payload = jsonb_set(payload, '{tiers,1,unit_price_vnd}', '1'::jsonb)
            WHERE config_type = 'PRICEBOOK'
            """
        )


def test_a_pricebook_whose_payload_disagrees_with_its_digest_prices_nothing(
    connection: Any, service: OperationsService
) -> None:
    """And the second defence, for the case the first one cannot cover.

    `configuration_versions` is immutable after publication, so the row above cannot be edited. It
    can still be *inserted* wrong — by a migration, a restore, or a direct write — and the runtime
    must not price against a payload that no longer hashes to the digest recorded beside it. This
    inserts exactly that inconsistency and requires a refusal rather than a price.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO configuration_versions (
                id, config_type, version, lifecycle, payload, snapshot_hash, created_by,
                created_at, published_by, published_at
            ) VALUES (
                %s, 'PRICEBOOK', 1, 'PUBLISHED', %s::jsonb, %s, %s,
                CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP
            )
            """,
            (uuid4(), '{"services": []}', "0" * 64, OWNER_ID, OWNER_ID),
        )
    with pytest.raises(QuotePricingUnavailable):
        service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=uuid4(),
            lines=_lines("6"),
            idempotency_key="quote-mismatched-digest",
            principal=staff,
        )


# --- snapshot_hash_recomputes_from_stored_payload ----------------------------------------------


def test_the_stored_snapshot_recomputes_to_its_persisted_hash(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    result = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("6"),
        idempotency_key="quote-hash",
        principal=staff,
    )
    assert isinstance(result, QuoteRevisionResult)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT snapshot, snapshot_hash FROM quote_revisions WHERE quote_id = %s",
            (result.quote_id,),
        )
        payload, stored_hash = cursor.fetchone()
    assert stored_hash == result.snapshot_hash
    # Recomputed from the stored JSON alone, with nothing from this process carried over.
    assert canonical_document(payload, exclude_volatile=False).snapshot_hash == stored_hash
    # And the repository's own reader agrees, which is the path the rest of the system uses.
    with connection.cursor() as cursor:
        stored = QuoteRepository.get_revision(cursor, result.quote_id, 1)
    assert stored is not None and stored.document.snapshot_hash == stored_hash


# --- append_only_revisions_under_concurrency ---------------------------------------------------


def test_a_correction_is_a_new_revision_and_a_stale_one_is_refused(
    connection: Any, service: OperationsService
) -> None:
    """Revision 2 supersedes revision 1; a second writer holding revision 0 must lose."""
    _publish(connection)
    store_id = uuid4()
    request_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)

    first = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=request_id,
        lines=_lines("5"),
        idempotency_key="quote-rev-1",
        principal=staff,
    )
    assert isinstance(first, QuoteRevisionResult) and first.revision == 1

    second = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=request_id,
        lines=_lines("7"),
        idempotency_key="quote-rev-2",
        principal=staff,
        quote_id=first.quote_id,
        expected_current_revision=1,
        expected_row_version=1,
    )
    assert isinstance(second, QuoteRevisionResult) and second.revision == 2

    with pytest.raises(QuoteStateError):
        service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=request_id,
            lines=_lines("9"),
            idempotency_key="quote-rev-stale",
            principal=staff,
            quote_id=first.quote_id,
            expected_current_revision=1,
            expected_row_version=1,
        )

    # Revision 1 is untouched: a correction adds, it never edits.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT revision FROM quote_revisions WHERE quote_id = %s ORDER BY revision",
            (first.quote_id,),
        )
        assert [row[0] for row in cursor.fetchall()] == [1, 2]
        cursor.execute("SELECT current_revision FROM quotes WHERE id = %s", (first.quote_id,))
        assert int(cursor.fetchone()[0]) == 2


def test_a_second_quote_for_one_order_request_is_a_conflict_not_a_crash(
    connection: Any, service: OperationsService
) -> None:
    """Found live in the demo stack, where it surfaced as a 500.

    `quotes` is UNIQUE (store_id, bound_order_request_id): one order request has one quote
    container, and further pricing is a revision of it. Asking to open a second container is a
    business conflict the caller can act on — but the driver's `UniqueViolation` was escaping
    untranslated, so the API answered "Internal Server Error" to a request that was merely wrong.
    """
    _publish(connection)
    store_id = uuid4()
    request_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    first = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=request_id,
        lines=_lines("6"),
        idempotency_key="quote-conflict-1",
        principal=staff,
    )
    assert isinstance(first, QuoteRevisionResult)

    with pytest.raises(QuoteStateError, match="already has a quote"):
        service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=request_id,
            lines=_lines("7"),
            idempotency_key="quote-conflict-2",
            principal=staff,
        )
    # And over HTTP that has to be a 409, not a 500.
    app.dependency_overrides[current_principal] = lambda: staff
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/internal/v1/stores/{store_id}/quotes",
                json={
                    "bound_order_request_id": str(request_id),
                    "lines": [
                        {
                            "service_code": STANDARD,
                            "quantity": "8",
                            "unit": "KG",
                            "quantity_basis": "STAFF_MEASUREMENT",
                        }
                    ],
                    "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
                },
                headers={"Idempotency-Key": "quote-conflict-http", "Origin": ORIGIN},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409


def test_replaying_an_idempotency_key_returns_the_original_and_writes_once(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    request_id = uuid4()

    def submit() -> QuoteRevisionResult | UnresolvedQuoteResult:
        return service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=request_id,
            lines=_lines("6"),
            idempotency_key="quote-replay",
            principal=staff,
        )

    first = submit()
    second = submit()
    assert isinstance(first, QuoteRevisionResult) and isinstance(second, QuoteRevisionResult)
    assert second.replayed and not first.replayed
    assert second.snapshot_hash == first.snapshot_hash
    assert _ledger_counts(connection, first.quote_id)[0] == 1


# --- atomic_row_event_audit_outbox -------------------------------------------------------------


def test_row_event_audit_and_outbox_all_exist_after_a_success(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    result = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("6"),
        idempotency_key="quote-atomic",
        principal=staff,
    )
    assert isinstance(result, QuoteRevisionResult)
    assert _ledger_counts(connection, result.quote_id) == (1, 1, 1, 1)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action, actor_type, actor_id FROM audit_events WHERE aggregate_id = %s",
            (result.quote_id,),
        )
        assert cursor.fetchone() == ("QUOTE_CALCULATE", "STAFF", staff.staff_user_id)


def test_nothing_is_written_when_the_revision_is_refused(
    connection: Any, service: OperationsService
) -> None:
    """A refused compare-and-swap must leave no event, no audit row and no outbox entry."""
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    first = service.create_quote(
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("6"),
        idempotency_key="quote-atomic-fail-1",
        principal=staff,
    )
    assert isinstance(first, QuoteRevisionResult)
    before = _ledger_counts(connection, first.quote_id)

    with pytest.raises(QuoteStateError):
        service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=uuid4(),
            lines=_lines("8"),
            idempotency_key="quote-atomic-fail-2",
            principal=staff,
            quote_id=first.quote_id,
            expected_current_revision=5,
            expected_row_version=5,
        )
    assert _ledger_counts(connection, first.quote_id) == before


# --- store_scoping_and_rbac_indistinguishable_refusal ------------------------------------------


def test_a_staff_member_from_another_store_cannot_price_in_this_one(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    outsider = _staff(connection, uuid4(), StaffRole.OPERATOR)
    with pytest.raises(StoreAccessError):
        service.create_quote(
            fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
            store_id=store_id,
            bound_order_request_id=uuid4(),
            lines=_lines("6"),
            idempotency_key="quote-outsider",
            principal=outsider,
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM quotes")
        assert int(cursor.fetchone()[0]) == 0


def test_membership_and_role_refusals_are_indistinguishable_over_http(
    connection: Any, service: OperationsService
) -> None:
    """Probing a store identifier must teach a caller nothing about which stores exist.

    An auditor refused for their role and an operator refused for their store have to produce the
    same status and the same body, or the difference between them becomes an oracle.
    """
    _publish(connection)
    store_id = uuid4()
    outsider = _staff(connection, uuid4(), StaffRole.OPERATOR)
    auditor = _staff(connection, store_id, StaffRole.AUDITOR)
    body = {
        "bound_order_request_id": str(uuid4()),
        "lines": [
            {
                "service_code": STANDARD,
                "quantity": "6",
                "unit": "KG",
                "quantity_basis": "STAFF_MEASUREMENT",
            }
        ],
        "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
    }
    responses = {}
    for label, principal in (("membership", outsider), ("role", auditor)):
        app.dependency_overrides[current_principal] = lambda principal=principal: principal
        app.dependency_overrides[get_operations_service] = lambda: service
        try:
            with TestClient(app) as client:
                responses[label] = client.post(
                    f"/internal/v1/stores/{store_id}/quotes",
                    json=body,
                    headers={"Idempotency-Key": f"quote-http-{label}", "Origin": ORIGIN},
                )
        finally:
            app.dependency_overrides.clear()

    assert responses["membership"].status_code == 403
    assert responses["role"].status_code == 403
    assert responses["membership"].json() == responses["role"].json()


def test_an_unresolved_quote_is_a_422_carrying_the_engines_reason_codes(
    connection: Any, service: OperationsService
) -> None:
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    app.dependency_overrides[current_principal] = lambda: staff
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/internal/v1/stores/{store_id}/quotes",
                json={
                    "bound_order_request_id": str(uuid4()),
                    "lines": [
                        {
                            "service_code": STANDARD,
                            "quantity": "1 bao tải to",
                            "unit": "KG",
                            "quantity_basis": "CUSTOMER_ESTIMATE",
                        }
                    ],
                    "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
                },
                headers={
                    "Idempotency-Key": "quote-http-unresolved",
                    "Origin": ORIGIN,
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "outcome": "REQUIRE_HUMAN",
        "reason_codes": ["MISSING_REQUIRED_FACT"],
    }


def test_a_priced_quote_is_created_and_listed_over_http(
    connection: Any, service: OperationsService
) -> None:
    """The console's actual round trip: price a garment, then see it in the store's quote list."""
    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    app.dependency_overrides[current_principal] = lambda: staff
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(app) as client:
            created = client.post(
                f"/internal/v1/stores/{store_id}/quotes",
                json={
                    "bound_order_request_id": str(uuid4()),
                    "lines": [
                        {
                            "service_code": STANDARD,
                            "quantity": "6",
                            "unit": "KG",
                            "quantity_basis": "STAFF_MEASUREMENT",
                        }
                    ],
                    "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
                },
                headers={
                    "Idempotency-Key": "quote-http-created",
                    "Origin": ORIGIN,
                },
            )
            listed = client.get(f"/internal/v1/stores/{store_id}/quotes?limit=10")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    payload = created.json()
    assert payload["net_service_subtotal_vnd"] == 120_000
    # The whole point of wiring the delivery engine in: a walk-in quote reaches the console with the
    # amount the customer pays, and without a reason code claiming the fee is unresolved when the
    # engine resolved it to zero.
    assert payload["display_total_min_vnd"] == 120_000
    assert payload["reason_codes"] == [
        "PROMOTION_NOT_EVALUATED",
        "SELF_SERVICE_NO_DELIVERY_JOB",
        "TAX_TREATMENT_UNVERIFIED",
    ]
    assert listed.status_code == 200
    assert [item["quote_id"] for item in listed.json()] == [payload["quote_id"]]


def test_a_named_staff_member_accepts_a_quote_and_the_attestation_is_recorded(
    connection: Any, service: OperationsService
) -> None:
    """`DEC-021`: the customer agreed, a named person says so, and an order rests on that."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("6"),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    assert priced.finality == "ESTIMATE"

    accepted = service.accept_quote(
        store_id=store_id,
        quote_id=priced.quote_id,
        expected_current_revision=priced.revision,
        expected_snapshot_hash=priced.snapshot_hash,
        idempotency_key=f"accept-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(accepted, QuoteRevisionResult)
    assert accepted.finality == "APPROVED_EXACT"
    assert accepted.status == "ACCEPTED_FINAL"
    assert accepted.revision == priced.revision + 1
    # Derived, not re-priced: the customer pays exactly what they were read.
    assert accepted.display_total_min_vnd == priced.display_total_min_vnd
    assert accepted.net_service_subtotal_vnd == priced.net_service_subtotal_vnd

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT accepted_by, accepted_revision, final_revision, accepted_snapshot_hash
            FROM quote_acceptances WHERE quote_id = %s
            """,
            (priced.quote_id,),
        )
        rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == staff.staff_user_id
    assert (rows[0][1], rows[0][2]) == (priced.revision, accepted.revision)
    assert rows[0][3] == priced.snapshot_hash


def test_a_quote_priced_from_the_customers_own_estimate_cannot_be_accepted(
    connection: Any, service: OperationsService
) -> None:
    """An exact price may not rest on a quantity nobody weighed, and the refusal says which fact."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine(STANDARD, "6", Unit.KG, QuantityBasis.CUSTOMER_ESTIMATE),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)

    refused = service.accept_quote(
        store_id=store_id,
        quote_id=priced.quote_id,
        expected_current_revision=priced.revision,
        expected_snapshot_hash=priced.snapshot_hash,
        idempotency_key=f"accept-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(refused, UnresolvedQuoteResult)
    assert refused.reason_codes == ("QUOTE_QUANTITY_NOT_MEASURED",)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM quote_acceptances WHERE quote_id = %s", (priced.quote_id,)
        )
        assert cursor.fetchone()[0] == 0


def test_accepting_a_quote_twice_is_a_conflict_not_a_second_attestation(
    connection: Any, service: OperationsService
) -> None:
    """Two attestations leave no answer to "who chốt this", which is the point of recording."""

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("6"),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    service.accept_quote(
        store_id=store_id,
        quote_id=priced.quote_id,
        expected_current_revision=priced.revision,
        expected_snapshot_hash=priced.snapshot_hash,
        idempotency_key=f"accept-{uuid4().hex}",
        principal=staff,
    )
    with pytest.raises(QuoteStateError):
        # The revision moved to 2, so the second attempt names a revision that is no longer current.
        service.accept_quote(
            store_id=store_id,
            quote_id=priced.quote_id,
            expected_current_revision=priced.revision,
            expected_snapshot_hash=priced.snapshot_hash,
            idempotency_key=f"accept-{uuid4().hex}",
            principal=staff,
        )


def _priced_quote(connection: Any, service: OperationsService, staff: Any, store_id: UUID) -> Any:
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=_lines("6"),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    return priced


def test_a_replayed_acceptance_returns_the_stored_attestation(
    connection: Any, service: OperationsService
) -> None:
    """The route demanded an Idempotency-Key and threw it away. Found 2026-08-29.

    The header was accepted and never reached `self._idempotency.execute`, so nothing was claimed
    and a retry re-executed from scratch -- tripping the `expected_current_revision` guard and
    telling the operator "quote moved since it was read; read it to the customer again" when their
    own timed-out first attempt is what moved it. At a counter with a customer waiting, that reads
    as the system losing the agreement it just recorded.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = _priced_quote(connection, service, staff, store_id)
    key = f"accept-{uuid4().hex}"

    first = service.accept_quote(
        store_id=store_id,
        quote_id=priced.quote_id,
        expected_current_revision=priced.revision,
        expected_snapshot_hash=priced.snapshot_hash,
        idempotency_key=key,
        principal=staff,
    )
    replay = service.accept_quote(
        store_id=store_id,
        quote_id=priced.quote_id,
        expected_current_revision=priced.revision,
        expected_snapshot_hash=priced.snapshot_hash,
        idempotency_key=key,
        principal=staff,
    )

    assert isinstance(first, QuoteRevisionResult)
    assert isinstance(replay, QuoteRevisionResult)
    assert not first.replayed
    assert replay.replayed
    assert replay.revision == first.revision
    assert replay.snapshot_hash == first.snapshot_hash
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM quote_acceptances WHERE quote_id = %s", (priced.quote_id,)
        )
        assert cursor.fetchone()[0] == 1


def test_an_unresolved_acceptance_does_not_consume_the_key(
    connection: Any, service: OperationsService
) -> None:
    """A refusal writes nothing, so the operator's key must survive it.

    The customer estimated the weight; the engine refuses. Staff put the bag on the scale and press
    chốt again -- with the same key, because that is what a retry is. If the refusal had claimed
    the key, the corrected acceptance would replay the refusal forever.
    """

    _publish(connection)
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    priced = service.create_quote(
        store_id=store_id,
        bound_order_request_id=uuid4(),
        lines=(RequestedLine(STANDARD, "6", Unit.KG, QuantityBasis.CUSTOMER_ESTIMATE),),
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        idempotency_key=f"quote-{uuid4().hex}",
        principal=staff,
    )
    assert isinstance(priced, QuoteRevisionResult)
    key = f"accept-{uuid4().hex}"

    refused = service.accept_quote(
        store_id=store_id,
        quote_id=priced.quote_id,
        expected_current_revision=priced.revision,
        expected_snapshot_hash=priced.snapshot_hash,
        idempotency_key=key,
        principal=staff,
    )

    assert isinstance(refused, UnresolvedQuoteResult)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE idempotency_key = %s", (key,)
        )
        assert cursor.fetchone()[0] == 0
