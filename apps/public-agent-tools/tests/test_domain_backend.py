"""TOOL-BACKEND-001: the ten operations against the deterministic domain, or typed unavailability.

Every test here answers one of the item's required tests. The DB-backed ones use the shared
synthetic PostgreSQL harness (`DATABASE_URL`, reset between tests by the repository conftest);
the boundary ones need no database because the gates they exercise refuse before any backend
code runs.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nha_trang_laundry_agent_tools.auth import AgentAuthSettings, AgentRunnerTokenVerifier
from nha_trang_laundry_agent_tools.backend import (
    DomainAgentToolBackend,
    build_domain_backend,
)
from nha_trang_laundry_agent_tools.facade import (
    AgentFacadeService,
    AgentToolRefusal,
    AgentToolUnavailable,
    UnavailableAgentToolBackend,
    get_agent_facade_service,
    get_agent_verifier,
)
from nha_trang_laundry_agent_tools.main import app
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentRunnerClaims,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_db.configurations import ConfigurationRepository
from nha_trang_laundry_db.intake import CreateOrderRequestCommand, OrderRequestRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_domain.catalog import FulfillmentMode, QuantityBasis, Unit
from nha_trang_laundry_domain.pricebook_import import published_price_rules
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    compose_quote_revision,
)
from nha_trang_laundry_domain.quotes import ExactLineAmounts

ROOT = Path(__file__).resolve().parents[3]
OWNER_ID = UUID("00000000-0000-0000-0000-0000000009a1")
FREE_TEXT_MARKER = "MARKER-Chăn đặc biệt 9f8e7d6c"


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit: the backend opens its own connection and must see what this one wrote."""
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def backend() -> DomainAgentToolBackend:
    return DomainAgentToolBackend(database_url=_database_url())


def _stub_backend() -> DomainAgentToolBackend:
    """A configured backend whose database is never reached by the operation under test."""
    return DomainAgentToolBackend(database_url="postgresql://agent-tools.invalid/db")


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem.decode("ascii")


def _runner_token(
    private_key: Ed25519PrivateKey,
    *,
    order_request_id: UUID,
    capabilities: list[str] | None = None,
) -> str:
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "iss": "https://control-plane.test",
            "aud": "agent-tool-facade",
            "sub": "AGENT_RUNNER",
            "iat": now,
            "exp": now + 30,
            "jti": str(uuid4()),
            "run_id": str(uuid4()),
            "organization_id": str(uuid4()),
            "store_id": str(uuid4()),
            "channel": "INTERNAL_TEST",
            "conversation_binding_id": str(uuid4()),
            "contact_binding_id": str(uuid4()),
            "capabilities": capabilities or ["INTERNAL_SHADOW"],
            "stage": "SHADOW",
            "data_classification": "SYNTHETIC",
            "order_request_id": str(order_request_id),
        },
        private_key,
        algorithm="EdDSA",
    )


def _publish(connection: Any) -> None:
    publish_pricebook(
        connection,
        actor_id=OWNER_ID,
        source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
    )


def _claims(
    *,
    store_id: UUID,
    contact_id: UUID,
    conversation_id: UUID,
    order_request_id: UUID | None = None,
    public_code: str | None = None,
    capabilities: tuple[ReleaseCapability, ...] = (ReleaseCapability.INTERNAL_SHADOW,),
) -> AgentRunnerClaims:
    return AgentRunnerClaims(
        iss="https://control-plane.test",
        aud="agent-tool-facade",
        sub="AGENT_RUNNER",
        iat=1_784_995_200,
        exp=1_784_995_230,
        jti=uuid4(),
        run_id=uuid4(),
        organization_id=uuid4(),
        store_id=store_id,
        channel="INTERNAL_TEST",
        conversation_binding_id=conversation_id,
        contact_binding_id=contact_id,
        capabilities=capabilities,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        order_request_id=order_request_id,
        public_code=public_code,
    )


def _seed_request(
    connection: Any, *, store_id: UUID, contact_id: UUID, conversation_id: UUID
) -> UUID:
    return (
        OrderRequestRepository()
        .create(
            connection,
            CreateOrderRequestCommand(
                store_id=store_id,
                contact_binding_id=contact_id,
                conversation_binding_id=conversation_id,
                actor_id=uuid4(),
                correlation_id=uuid4(),
                created_at=datetime.now(UTC),
            ),
        )
        .order_request_id
    )


def _invoke(
    backend: DomainAgentToolBackend,
    operation: AgentToolOperation,
    *,
    arguments: Mapping[str, Any],
    claims: AgentRunnerClaims,
    path_parameters: Mapping[str, str] | None = None,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> tuple[int, dict[str, Any]]:
    return AgentFacadeService(backend).invoke(
        operation=operation,
        arguments=arguments,
        path_parameters=path_parameters or {},
        idempotency_key=idempotency_key,
        if_match=if_match,
        claims=claims,
        trace_id=f"tr_{uuid4().hex}",
    )


# --- default wiring stays unavailable ----------------------------------------


def test_default_wiring_remains_the_unavailable_backend() -> None:
    """The facade's own dependency is the unavailable backend; opting in is explicit."""
    service = get_agent_facade_service()
    claims = _claims(store_id=uuid4(), contact_id=uuid4(), conversation_id=uuid4())
    with pytest.raises(AgentToolUnavailable):
        service.invoke(
            operation=AgentToolOperation.CATALOG_RESOLVE,
            arguments={"query": "giặt chăn", "locale": "vi-VN"},
            path_parameters={},
            idempotency_key=None,
            if_match=None,
            claims=claims,
            trace_id="tr_default_wiring_check",
        )
    # The builder assembles nothing without an explicit database URL.
    assert isinstance(build_domain_backend(), UnavailableAgentToolBackend)
    assert isinstance(build_domain_backend(database_url=None), UnavailableAgentToolBackend)
    assert isinstance(
        build_domain_backend(database_url="postgresql://agent-tools.invalid/db"),
        DomainAgentToolBackend,
    )


def test_operations_without_honest_backing_return_typed_unavailability() -> None:
    """messageDraftCreate, publicOrderStatusGet and incidentOpen never fabricate a success."""
    backend = _stub_backend()
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    request_id = uuid4()
    claims = _claims(
        store_id=store_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        order_request_id=request_id,
        public_code="a" * 24,
    )
    with pytest.raises(AgentToolUnavailable, match="no backing store"):
        _invoke(
            backend,
            AgentToolOperation.MESSAGE_DRAFT_CREATE,
            arguments={"message_kind": "LIST_PRICE_INFO", "locale": "vi-VN"},
            claims=claims,
            path_parameters={"order_request_id": str(request_id)},
            idempotency_key="agent-test-draft-0001",
        )
    with pytest.raises(AgentToolUnavailable, match="no read model"):
        _invoke(
            backend,
            AgentToolOperation.PUBLIC_ORDER_STATUS_GET,
            arguments={},
            claims=claims,
            path_parameters={"public_code": "a" * 24},
        )
    with pytest.raises(AgentToolUnavailable, match="without inventing data"):
        _invoke(
            backend,
            AgentToolOperation.INCIDENT_OPEN,
            arguments={
                "allegation_type": "DAMAGE",
                "statement": "Quần áo bị rách.",
                "source_provider_message_ids": ["msg-1"],
            },
            claims=claims,
            path_parameters={"order_request_id": str(request_id)},
            idempotency_key="agent-test-incident-01",
        )


def test_bound_path_refusal_is_indistinguishable_from_authorization_refusal() -> None:
    """A cross-request path and a capability denial produce the same 403 POLICY_DENIED."""
    private_key, public_key = _key_pair()
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public_key,
        )
    )
    # The real backend is deliberately wired in: both refusals must happen at the gates,
    # before any backend code runs, so its database is never touched.
    app.dependency_overrides[get_agent_verifier] = lambda: verifier
    app.dependency_overrides[get_agent_facade_service] = lambda: AgentFacadeService(_stub_backend())
    bound, other = uuid4(), uuid4()
    quote_arguments = {
        "lines": [
            {
                "service_code": "STANDARD_WASH_DRY",
                "quantity_basis": "CUSTOMER_ESTIMATE",
                "quantity": "3.0",
                "unit": "KG",
            }
        ],
        "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
    }
    try:
        with TestClient(app) as client:
            cross_request = client.post(
                f"/agent/v1/order-requests/{other}/quotes:estimate",
                headers={
                    "Authorization": f"Bearer {_runner_token(private_key, order_request_id=bound)}",
                    "Idempotency-Key": "agent-test-idor-00001",
                    "If-Match": '"1"',
                },
                json=quote_arguments,
            )
            capability_denied = client.post(
                f"/agent/v1/order-requests/{bound}/quotes:estimate",
                headers={
                    "Authorization": (
                        "Bearer "
                        + _runner_token(
                            private_key,
                            order_request_id=bound,
                            capabilities=["PUBLIC_FAQ"],
                        )
                    ),
                    "Idempotency-Key": "agent-test-authz-0001",
                    "If-Match": '"1"',
                },
                json=quote_arguments,
            )
    finally:
        app.dependency_overrides.clear()

    assert cross_request.status_code == 403
    assert capability_denied.status_code == 403

    def comparable(body: dict[str, Any]) -> dict[str, Any]:
        # The trace id differs per request by design; everything else must be identical,
        # or the difference between the two refusals becomes an oracle.
        return {key: value for key, value in body.items() if key != "trace_id"}

    assert comparable(cross_request.json()) == comparable(capability_denied.json())
    assert cross_request.json()["error"]["code"] == "POLICY_DENIED"


# --- catalog ------------------------------------------------------------------


def test_catalog_resolve_is_deterministic_exact_alias_substring_and_miss(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    _publish(connection)
    claims = _claims(store_id=uuid4(), contact_id=uuid4(), conversation_id=uuid4())

    def resolve(query: str) -> list[dict[str, Any]]:
        status, payload = _invoke(
            backend,
            AgentToolOperation.CATALOG_RESOLVE,
            arguments={"query": query, "locale": "vi-VN"},
            claims=claims,
        )
        assert status == 200
        decision = payload["decision"]
        # The real digest of the published pricebook, not an invented one.
        assert decision["snapshot_hash"].startswith("sha256:")
        assert decision["policy_version"] == "synthetic-internal-v1"
        return cast(list[dict[str, Any]], payload["data"]["candidates"])

    exact = resolve("STANDARD_WASH_DRY")
    assert [candidate["service_code"] for candidate in exact] == ["STANDARD_WASH_DRY"]
    assert exact[0]["confidence_band"] == "HIGH"

    alias = resolve("STD_WASH_DRY_LT6")
    assert [candidate["service_code"] for candidate in alias] == ["STANDARD_WASH_DRY"]
    assert alias[0]["confidence_band"] == "HIGH"

    ambiguous = resolve("IRON")
    assert len(ambiguous) > 1
    assert all(candidate["confidence_band"] == "LOW" for candidate in ambiguous)
    assert all(candidate["clarification_key"] == "AMBIGUOUS_SERVICE" for candidate in ambiguous)

    single = resolve("HANDBAG")
    assert [candidate["service_code"] for candidate in single] == ["LEATHER_HANDBAG"]
    assert single[0]["confidence_band"] == "MEDIUM"

    assert resolve("dịch vụ không tồn tại") == []


def test_catalog_resolve_without_a_published_pricebook_fails_closed(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    claims = _claims(store_id=uuid4(), contact_id=uuid4(), conversation_id=uuid4())
    with pytest.raises(AgentToolUnavailable, match="no published pricebook"):
        _invoke(
            backend,
            AgentToolOperation.CATALOG_RESOLVE,
            arguments={"query": "STANDARD_WASH_DRY", "locale": "vi-VN"},
            claims=claims,
        )


# --- intake -------------------------------------------------------------------


def test_order_request_create_persists_a_bound_draft(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    claims = _claims(store_id=store_id, contact_id=contact_id, conversation_id=conversation_id)
    status, payload = _invoke(
        backend,
        AgentToolOperation.ORDER_REQUEST_CREATE,
        arguments={
            "customer_intent": f"Giặt chăn, {FREE_TEXT_MARKER}",
            "locale": "vi-VN",
            "source_provider_message_ids": ["msg-1", "msg-2"],
        },
        claims=claims,
        idempotency_key="agent-test-create-0001",
    )
    assert status == 201
    data = payload["data"]
    assert data["status"] == "DRAFT" and data["row_version"] == 1

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT store_id, contact_binding_id, conversation_binding_id, status
            FROM order_requests WHERE id = %s
            """,
            (data["order_request_id"],),
        )
        assert cursor.fetchone() == (store_id, contact_id, conversation_id, "DRAFT")
        cursor.execute(
            """
            SELECT event_type FROM domain_events
            WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s
            """,
            (data["order_request_id"],),
        )
        assert [row[0] for row in cursor.fetchall()] == ["ORDER_REQUEST_DRAFT_CREATED"]


def test_record_customer_facts_bumps_version_under_if_match_and_stores_types_only(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    request_id = _seed_request(
        connection, store_id=store_id, contact_id=contact_id, conversation_id=conversation_id
    )
    claims = _claims(
        store_id=store_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        order_request_id=request_id,
    )
    arguments = {
        "facts": [
            {
                "fact_type": "SERVICE_TEXT",
                "service_text": FREE_TEXT_MARKER,
                "source_provider_message_id": "msg-1",
            },
            {
                "fact_type": "CUSTOMER_ADDRESS_TEXT",
                "address_text": f"Số 1 đường Biển, {FREE_TEXT_MARKER}",
                "source_provider_message_id": "msg-2",
            },
        ]
    }
    status, payload = _invoke(
        backend,
        AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        arguments=arguments,
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
        idempotency_key="agent-test-facts-00001",
        if_match='"1"',
    )
    assert status == 200
    data = payload["data"]
    assert data["row_version"] == 2 and data["accepted_fact_count"] == 2

    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM order_requests WHERE id = %s", (request_id,))
        assert int(cursor.fetchone()[0]) == 2
        cursor.execute(
            """
            SELECT payload::text FROM domain_events
            WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s
                AND event_type = 'ORDER_REQUEST_CUSTOMER_FACTS_RECORDED'
            """,
            (request_id,),
        )
        event_payload = str(cursor.fetchone()[0])
        assert FREE_TEXT_MARKER not in event_payload
        assert "service_text" not in event_payload
        assert "address_text" not in event_payload
        assert "CUSTOMER_ADDRESS_TEXT" in event_payload

    # A stale If-Match is refused and moves nothing.
    with pytest.raises(AgentToolRefusal) as refusal:
        _invoke(
            backend,
            AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
            arguments=arguments,
            claims=claims,
            path_parameters={"order_request_id": str(request_id)},
            idempotency_key="agent-test-facts-00002",
            if_match='"1"',
        )
    assert refusal.value.code == "STALE_VERSION"
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM order_requests WHERE id = %s", (request_id,))
        assert int(cursor.fetchone()[0]) == 2

    # Replaying the original key returns the original result and writes nothing new.
    replay_status, replay = _invoke(
        backend,
        AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        arguments=arguments,
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
        idempotency_key="agent-test-facts-00001",
        if_match='"1"',
    )
    assert replay_status == 200 and replay["data"]["row_version"] == 2
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM order_requests WHERE id = %s", (request_id,))
        assert int(cursor.fetchone()[0]) == 2


# --- quote ---------------------------------------------------------------------


def _direct_engine_composition(connection: Any, quantity: str) -> ComposedQuote:
    with connection.cursor() as cursor:
        published = ConfigurationRepository.latest_published(cursor, "PRICEBOOK")
        assert published is not None
        payload = ConfigurationRepository.get_published(cursor, published.version_id)
        assert payload is not None
    composition = compose_quote_revision(
        quote_id=uuid4(),
        revision=1,
        rules=published_price_rules(payload),
        requested=(
            RequestedLine("STANDARD_WASH_DRY", quantity, Unit.KG, QuantityBasis.CUSTOMER_ESTIMATE),
        ),
        pricebook=PricebookProvenance(
            published.version_id,
            published.version,
            f"JCS-SHA256-V1:{published.snapshot_hash}",
        ),
        priced_at=datetime.now(UTC),
        # The same mode the facade call in this test sends, because the point of the comparison is
        # that the two paths agree on the number. A different mode here would compare two quotes of
        # two different orders and prove nothing.
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
    )
    assert isinstance(composition, ComposedQuote)
    return composition


def test_quote_estimate_through_the_facade_equals_a_direct_engine_call(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    _publish(connection)
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    request_id = _seed_request(
        connection, store_id=store_id, contact_id=contact_id, conversation_id=conversation_id
    )
    claims = _claims(
        store_id=store_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        order_request_id=request_id,
    )
    status, payload = _invoke(
        backend,
        AgentToolOperation.QUOTE_ESTIMATE,
        arguments={
            "lines": [
                {
                    "service_code": "STANDARD_WASH_DRY",
                    "quantity_basis": "CUSTOMER_ESTIMATE",
                    "quantity": "6",
                    "unit": "KG",
                }
            ],
            "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
        },
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
        idempotency_key="agent-test-quote-00001",
        if_match='"1"',
    )
    assert status == 200
    data = payload["data"]
    direct = _direct_engine_composition(connection, "6")
    direct_totals = direct.snapshot.data.totals
    assert data["list_service_subtotal_vnd"] == direct_totals.list_service_subtotal_max_vnd
    assert data["net_service_subtotal_vnd"] == direct_totals.net_service_subtotal_max_vnd
    assert data["discount_amount_vnd"] == direct_totals.discount_amount_max_vnd
    # Compared against the engine rather than pinned to None. This asserted `is None` back when
    # the composer never called `evaluate_delivery` and every quote lacked a fee; the parity this
    # test is named for is what actually matters, and it holds at whatever value the engine returns.
    assert data["delivery_fee_vnd"] == direct_totals.delivery_fee_vnd
    assert data["display_total_vnd"] == direct_totals.display_total_max_vnd
    assert payload["decision"]["outcome"] == "REQUIRE_HUMAN"
    assert payload["decision"]["reason_codes"] == list(direct.snapshot.data.reason_codes)

    # Same price lines, checked against the persisted immutable revision.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT snapshot FROM quote_revisions WHERE quote_id = %s AND revision = 1",
            (data["quote_id"],),
        )
        stored = cursor.fetchone()[0]
    direct_lines = direct.snapshot.data.lines
    assert len(stored["lines"]) == len(direct_lines) == 1
    assert stored["lines"][0]["service_code"] == direct_lines[0].service_code
    direct_amounts = direct_lines[0].amounts
    # The facade path may only compose exact revisions; a range line is a composition defect.
    assert isinstance(direct_amounts, ExactLineAmounts)
    assert stored["lines"][0]["amounts"]["unit_price_vnd"] == direct_amounts.unit_price_vnd
    assert stored["lines"][0]["amounts"]["net_amount_vnd"] == direct_amounts.net_amount_vnd


def test_an_unresolved_quote_estimate_carries_the_engines_refusal_and_writes_nothing(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    _publish(connection)
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    request_id = _seed_request(
        connection, store_id=store_id, contact_id=contact_id, conversation_id=conversation_id
    )
    claims = _claims(
        store_id=store_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        order_request_id=request_id,
    )
    with pytest.raises(AgentToolRefusal) as refusal:
        _invoke(
            backend,
            AgentToolOperation.QUOTE_ESTIMATE,
            arguments={
                "lines": [
                    {
                        "service_code": "BED_PILLOW",
                        "quantity_basis": "CUSTOMER_ESTIMATE",
                        "quantity": "1",
                        "unit": "ITEM",
                    }
                ],
                "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
            },
            claims=claims,
            path_parameters={"order_request_id": str(request_id)},
            idempotency_key="agent-test-quote-00002",
            if_match='"1"',
        )
    assert refusal.value.code == "RANGE_PRICE_REQUIRES_HUMAN"
    assert refusal.value.reason_codes == ("RANGE_PRICE_REQUIRES_HUMAN",)
    with connection.cursor() as cursor:
        for table in ("quotes", "quote_revisions"):
            cursor.execute(f"SELECT count(*) FROM {table}")
            assert int(cursor.fetchone()[0]) == 0, f"{table} must stay empty"
        # The refusal must not consume the idempotency key either.
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE scope LIKE 'agent-%'"
        )
        assert int(cursor.fetchone()[0]) == 0


# --- delivery and capacity ------------------------------------------------------


def test_delivery_evaluate_carries_the_engines_unresolved_outcome_verbatim() -> None:
    from nha_trang_laundry_domain.catalog import FulfillmentMode
    from nha_trang_laundry_domain.delivery import evaluate_delivery

    backend = _stub_backend()
    request_id = uuid4()
    claims = _claims(
        store_id=uuid4(),
        contact_id=uuid4(),
        conversation_id=uuid4(),
        order_request_id=request_id,
    )
    status, payload = _invoke(
        backend,
        AgentToolOperation.DELIVERY_EVALUATE,
        arguments={"fulfillment_mode": "PICKUP_AND_RETURN", "planned_transport_weight_kg": "5"},
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
    )
    assert status == 200
    direct = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=None,
        planned_transport_weight_kg="5",
    )
    assert payload["decision"]["outcome"] == direct.overall_outcome.value == "REQUIRE_HUMAN"
    assert payload["decision"]["reason_codes"] == [reason.value for reason in direct.reason_codes]
    assert "DELIVERY_DISTANCE_UNVERIFIED" in payload["decision"]["reason_codes"]
    assert "DELIVERY_FEE_REQUIRES_HUMAN" in payload["decision"]["reason_codes"]
    data = payload["data"]
    assert data["distance_status"] == "UNVERIFIED"
    assert data["fee_status"] == "REQUIRES_HUMAN"
    assert data["delivery_fee_vnd"] is None
    direct_vehicle = direct.vehicle_recommendation
    assert direct_vehicle is not None  # a 5 kg planned load resolves to a real recommendation
    assert data["vehicle_recommendation"] == direct_vehicle.value
    assert data["requires_human"] is True


def test_delivery_evaluate_self_drop_is_the_engines_allow() -> None:
    backend = _stub_backend()
    request_id = uuid4()
    claims = _claims(
        store_id=uuid4(),
        contact_id=uuid4(),
        conversation_id=uuid4(),
        order_request_id=request_id,
    )
    status, payload = _invoke(
        backend,
        AgentToolOperation.DELIVERY_EVALUATE,
        arguments={"fulfillment_mode": "SELF_DROP_SELF_COLLECT"},
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
    )
    assert status == 200
    assert payload["decision"]["outcome"] == "ALLOW"
    assert payload["data"] == {
        "distance_status": "NOT_REQUIRED",
        "distance_km": None,
        "fee_status": "NOT_REQUIRED",
        "delivery_fee_vnd": 0,
        "vehicle_recommendation": "NOT_REQUIRED",
        "requires_human": False,
    }


def test_capacity_check_is_a_non_binding_advisory() -> None:
    backend = _stub_backend()
    request_id = uuid4()
    claims = _claims(
        store_id=uuid4(),
        contact_id=uuid4(),
        conversation_id=uuid4(),
        order_request_id=request_id,
    )
    status, payload = _invoke(
        backend,
        AgentToolOperation.CAPACITY_CHECK,
        arguments={"requested_ready_at": "2026-08-20T10:00:00+07:00"},
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
    )
    assert status == 200
    assert payload["data"] == {
        "advisory": "UNKNOWN",
        "advisory_ready_window_start": None,
        "advisory_ready_window_end": None,
        "slot_confirmed": False,
        "required_approval": "SLOT_CONFIRMATION",
    }
    assert payload["decision"]["outcome"] == "REQUIRE_HUMAN"
    assert payload["decision"]["obligations"] == ["SLOT_CONFIRMATION"]


# --- approval -------------------------------------------------------------------


def _quote_for_approval(
    connection: Any, backend: DomainAgentToolBackend
) -> tuple[AgentRunnerClaims, UUID, dict[str, Any]]:
    _publish(connection)
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    request_id = _seed_request(
        connection, store_id=store_id, contact_id=contact_id, conversation_id=conversation_id
    )
    claims = _claims(
        store_id=store_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        order_request_id=request_id,
    )
    status, payload = _invoke(
        backend,
        AgentToolOperation.QUOTE_ESTIMATE,
        arguments={
            "lines": [
                {
                    "service_code": "STANDARD_WASH_DRY",
                    "quantity_basis": "CUSTOMER_ESTIMATE",
                    "quantity": "6",
                    "unit": "KG",
                }
            ],
            "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
        },
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
        idempotency_key=f"agent-test-quote-{uuid4().hex[:8]}",
        if_match='"1"',
    )
    assert status == 200
    return claims, request_id, payload["data"]


def test_approval_request_create_binds_the_real_quote_revision(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    claims, request_id, quote = _quote_for_approval(connection, backend)
    status, payload = _invoke(
        backend,
        AgentToolOperation.APPROVAL_REQUEST_CREATE,
        arguments={
            "action": "PRESENT_QUOTE",
            "resource_type": "QUOTE_REVISION",
            "resource_id": quote["quote_revision_id"],
            "resource_version": quote["revision"],
            "snapshot_hash": quote["snapshot_hash"],
            "rendered_hash": f"sha256:{'b' * 64}",
        },
        claims=claims,
        path_parameters={"order_request_id": str(request_id)},
        idempotency_key="agent-test-approval-01",
    )
    assert status == 201
    data = payload["data"]
    # Server-derived policy, verbatim from the domain's approval policy for PRESENT_QUOTE.
    assert data["status"] == "PENDING"
    assert data["required_role"] == "OPS_APPROVER"
    assert data["reason_codes"] == ["CUSTOMER_FACING_COMMITMENT"]
    assert data["obligations"] == ["RECHECK_RESOURCE_VERSION", "RECHECK_POLICY_AT_EXECUTION"]
    assert data["execution_capability"] == "HUMAN_APPROVED_ACTION"
    with connection.cursor() as cursor:
        cursor.execute("SELECT actor_type FROM audit_events WHERE aggregate_type = 'APPROVAL'")
        assert cursor.fetchone()[0] == "AGENT_RUNNER"


def test_approval_request_create_refuses_an_unbound_resource_like_an_authorization_failure(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    from nha_trang_laundry_agent_tools.auth import AgentAuthorizationError

    claims, request_id, quote = _quote_for_approval(connection, backend)
    with pytest.raises(AgentAuthorizationError):
        _invoke(
            backend,
            AgentToolOperation.APPROVAL_REQUEST_CREATE,
            arguments={
                "action": "PRESENT_QUOTE",
                "resource_type": "QUOTE_REVISION",
                "resource_id": str(uuid4()),
                "resource_version": quote["revision"],
                "snapshot_hash": quote["snapshot_hash"],
                "rendered_hash": f"sha256:{'b' * 64}",
            },
            claims=claims,
            path_parameters={"order_request_id": str(request_id)},
            idempotency_key="agent-test-approval-02",
        )


def test_approval_request_create_fails_closed_for_unverifiable_actions(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    claims, request_id, quote = _quote_for_approval(connection, backend)
    with pytest.raises(AgentToolUnavailable, match="no verifiable resource store"):
        _invoke(
            backend,
            AgentToolOperation.APPROVAL_REQUEST_CREATE,
            arguments={
                "action": "SEND_MESSAGE",
                "resource_type": "MESSAGE_DRAFT",
                "resource_id": str(uuid4()),
                "resource_version": 1,
                "snapshot_hash": quote["snapshot_hash"],
                "rendered_hash": f"sha256:{'b' * 64}",
            },
            claims=claims,
            path_parameters={"order_request_id": str(request_id)},
            idempotency_key="agent-test-approval-03",
        )


# --- nothing persisted contains what must never be persisted ---------------------


def test_no_persisted_artifact_contains_customer_free_text(
    connection: Any, backend: DomainAgentToolBackend
) -> None:
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    claims = _claims(store_id=store_id, contact_id=contact_id, conversation_id=conversation_id)
    _, created = _invoke(
        backend,
        AgentToolOperation.ORDER_REQUEST_CREATE,
        arguments={
            "customer_intent": FREE_TEXT_MARKER,
            "locale": "vi-VN",
            "source_provider_message_ids": ["msg-1"],
        },
        claims=claims,
        idempotency_key="agent-test-pii-create-1",
    )
    request_id = UUID(created["data"]["order_request_id"])
    bound_claims = _claims(
        store_id=store_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        order_request_id=request_id,
    )
    _invoke(
        backend,
        AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        arguments={
            "facts": [
                {
                    "fact_type": "SERVICE_TEXT",
                    "service_text": FREE_TEXT_MARKER,
                    "source_provider_message_id": "msg-1",
                }
            ]
        },
        claims=bound_claims,
        path_parameters={"order_request_id": str(request_id)},
        idempotency_key="agent-test-pii-facts-1",
        if_match='"1"',
    )

    with connection.cursor() as cursor:
        for table, column in (
            ("domain_events", "payload"),
            ("audit_events", "details"),
            ("outbox_events", "payload"),
            ("command_idempotency_records", "response"),
        ):
            cursor.execute(f"SELECT {column}::text FROM {table}")
            for row in cursor.fetchall():
                assert FREE_TEXT_MARKER not in str(row[0]), f"{table}.{column} leaked free text"
        cursor.execute("SELECT * FROM order_requests WHERE id = %s", (request_id,))
        row = cursor.fetchone()
        assert row is not None
        assert all(FREE_TEXT_MARKER not in str(value) for value in row)
