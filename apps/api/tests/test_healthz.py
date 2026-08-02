from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_identity_service,
    get_operations_service,
)
from nha_trang_laundry_api.operations import (
    QueueRecoverySummary,
    StoredIncidentResult,
    StoredManualSendResult,
)
from nha_trang_laundry_db.approvals import StoredApproval
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentSummary
from nha_trang_laundry_db.manual_sends import StoredManualSend
from nha_trang_laundry_db.orders import StoredOrder
from nha_trang_laundry_db.quotes import QuoteSummary
from nha_trang_laundry_domain.catalog import (
    ActorRole,
    CommercialOrderStatus,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)

OWNER_ID = UUID("00000000-0000-0000-0000-000000000101")
STAFF_ID = UUID("00000000-0000-0000-0000-000000000102")
STORE_ID = UUID("00000000-0000-0000-0000-000000000103")
ORDER_ID = UUID("00000000-0000-0000-0000-000000000104")
APPROVAL_ID = UUID("00000000-0000-0000-0000-000000000105")
MANUAL_SEND_ID = UUID("00000000-0000-0000-0000-000000000106")
RECIPIENT_ID = UUID("00000000-0000-0000-0000-000000000107")
INCIDENT_ID = UUID("00000000-0000-0000-0000-000000000108")
HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64
SCOPE_HASH = "sha256:" + "c" * 64
EVIDENCE_HASH = "sha256:" + "d" * 64


class StubIdentityService:
    def create_staff(
        self, *, oidc_subject: str, display_name: str, email: str | None, actor_id: UUID
    ) -> UUID:
        assert (oidc_subject, display_name, email, actor_id) == (
            "provider-test-subject",
            "Test Staff",
            None,
            OWNER_ID,
        )
        return STAFF_ID


class StubOperationsService:
    def list_orders(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[StoredOrder, ...]:
        assert store_id == STORE_ID
        assert principal.staff_user_id == OWNER_ID
        assert limit == 100
        return (
            StoredOrder(
                ORDER_ID,
                STORE_ID,
                CommercialOrderStatus.REQUESTED,
                IntakeStatus.AWAITING_HANDOFF,
                ProductionStatus.NOT_STARTED,
                OrderBalanceStatus.UNPAID,
                1,
            ),
        )

    def list_pending_approvals(self, *, limit: int) -> tuple[StoredApproval, ...]:
        assert limit == 100
        return (
            StoredApproval(
                APPROVAL_ID,
                "REQUESTED",
                "JCS-SHA256-V1:" + "a" * 64,
                ActorRole.OPS_APPROVER,
                datetime(2026, 8, 1, tzinfo=UTC),
            ),
        )

    def decide_approval(self, **values: object) -> StoredApproval:
        assert values["reason_code"] == "HUMAN_REVIEW_COMPLETE"
        assert values["note"] == "Verified exact preview"
        return StoredApproval(
            APPROVAL_ID,
            "APPROVED",
            HASH_A,
            ActorRole.OPS_APPROVER,
            datetime(2026, 8, 1, tzinfo=UTC),
        )

    def list_quotes(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[QuoteSummary, ...]:
        assert (store_id, principal.staff_user_id, limit) == (STORE_ID, OWNER_ID, 100)
        return (
            QuoteSummary(
                APPROVAL_ID,
                1,
                1,
                "APPROVED_EXACT",
                "ACCEPTED_FINAL",
                HASH_A,
                125_000,
                125_000,
                datetime(2026, 8, 2, tzinfo=UTC),
            ),
        )

    def prepare_manual_send(self, **values: object) -> StoredManualSendResult:
        assert values["channel"] == "INTERNAL_TEST"
        return StoredManualSendResult(
            StoredManualSend(
                MANUAL_SEND_ID,
                APPROVAL_ID,
                "APPROVED_FOR_MANUAL_SEND",
                RECIPIENT_ID,
                HASH_B,
            ),
            1,
            False,
        )

    def attest_manual_send(self, **values: object) -> StoredManualSendResult:
        assert values["expected_envelope_row_version"] == 1
        return StoredManualSendResult(
            StoredManualSend(
                MANUAL_SEND_ID,
                APPROVAL_ID,
                "MANUAL_SEND_RECORDED",
                RECIPIENT_ID,
                HASH_B,
            ),
            2,
            False,
        )

    def open_incident(self, **values: object) -> StoredIncidentResult:
        assert values["store_id"] == STORE_ID
        return StoredIncidentResult(INCIDENT_ID, "OPEN", False, False, False)

    def list_incidents(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[IncidentSummary, ...]:
        assert (store_id, principal.staff_user_id, limit) == (STORE_ID, OWNER_ID, 100)
        return (
            IncidentSummary(
                INCIDENT_ID,
                STORE_ID,
                ORDER_ID,
                "SERVICE_QUALITY",
                "OPEN",
                False,
                False,
                datetime(2026, 8, 1, tzinfo=UTC),
            ),
        )

    def queue_recovery_summary(self, *, principal: StaffPrincipal) -> QueueRecoverySummary:
        assert principal.staff_user_id == OWNER_ID
        return QueueRecoverySummary(1, 2, 0, 3, 4, 5, 0, 6)


def test_healthz_returns_ok() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert UUID(response.headers["X-Correlation-ID"])


def test_api_preserves_canonical_correlation_and_replaces_malformed_input() -> None:
    expected = "00000000-0000-0000-0000-000000000901"

    propagated = TestClient(app).get("/healthz", headers={"X-Correlation-ID": expected})
    replaced = TestClient(app).get(
        "/healthz", headers={"X-Correlation-ID": "not-a-canonical-correlation"}
    )

    assert propagated.headers["X-Correlation-ID"] == expected
    assert replaced.headers["X-Correlation-ID"] != "not-a-canonical-correlation"
    assert UUID(replaced.headers["X-Correlation-ID"])


def test_staff_pwa_shell_is_served_without_public_customer_controls() -> None:
    response = TestClient(app).get("/staff/")

    assert response.status_code == 200
    assert "Bảng vận hành" in response.text
    assert "Không cho duyệt mù" in response.text
    assert "Gửi thủ công" in response.text
    assert "Khôi phục hàng đợi" in response.text

    script = TestClient(app).get("/staff/app.js").text
    worker = TestClient(app).get("/staff/sw.js").text
    assert "navigator.onLine" in script
    assert "indexedDB" not in script
    assert "sync.register" not in script
    assert 'method !== "GET"' in worker


def test_staff_session_fails_closed_without_identity_configuration() -> None:
    response = TestClient(app).post(
        "/internal/v1/auth/session",
        headers={
            "Authorization": "Bearer untrusted-token",
            "Origin": "http://testserver",
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "staff identity unavailable"}


def test_owner_can_create_named_staff_through_server_authorization() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-test-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_identity_service] = StubIdentityService
    try:
        response = TestClient(app).post(
            "/internal/v1/staff",
            json={"oidc_subject": "provider-test-subject", "display_name": "Test Staff"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {"staff_user_id": str(STAFF_ID)}


def test_non_owner_cannot_create_staff_or_assign_roles() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "operator-test-subject", frozenset({StaffRole.OPERATOR}), False
    )
    app.dependency_overrides[get_identity_service] = StubIdentityService
    try:
        create_response = TestClient(app).post(
            "/internal/v1/staff",
            json={"oidc_subject": "provider-test-subject", "display_name": "Test Staff"},
        )
        role_response = TestClient(app).post(
            f"/internal/v1/staff/{STAFF_ID}/roles", json={"role": "DRIVER"}
        )
    finally:
        app.dependency_overrides.clear()

    assert create_response.status_code == 403
    assert role_response.status_code == 403


def test_order_board_is_staff_scoped_and_driver_is_denied() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-test-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    try:
        allowed = TestClient(app).get(f"/internal/v1/stores/{STORE_ID}/orders")
        app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
            STAFF_ID, "driver-test-subject", frozenset({StaffRole.DRIVER}), False
        )
        denied = TestClient(app).post(
            f"/internal/v1/orders/{ORDER_ID}/transition",
            headers={"Idempotency-Key": "driver-attempt", "If-Match": '"1"'},
            json={"target": "STORE_CONFIRMATION_PENDING"},
        )
    finally:
        app.dependency_overrides.clear()

    assert allowed.status_code == 200
    assert allowed.json()[0]["order_id"] == str(ORDER_ID)
    assert denied.status_code == 403


def test_approval_boundary_rejects_policy_fields_and_requires_mfa() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-test-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    request = {
        "action": "SEND_MESSAGE",
        "resource_type": "MESSAGE_DRAFT",
        "resource_id": str(ORDER_ID),
        "resource_version": 1,
        "snapshot_hash": "JCS-SHA256-V1:" + "a" * 64,
        "rendered_hash": "JCS-SHA256-V1:" + "b" * 64,
        "policy_version": "approval-policy-v1",
        "required_role": "PUBLIC_AGENT",
    }
    try:
        tampered = TestClient(app).post(
            "/internal/v1/approvals",
            headers={"Idempotency-Key": "approval-attempt"},
            json=request,
        )
        app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
            STAFF_ID,
            "approver-without-mfa",
            frozenset({StaffRole.OPS_APPROVER}),
            False,
        )
        no_mfa = TestClient(app).get("/internal/v1/approvals")
    finally:
        app.dependency_overrides.clear()

    assert tampered.status_code == 422
    assert no_mfa.status_code == 403


def test_staff_shadow_workflow_is_typed_hash_bound_and_observation_only() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-test-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    client = TestClient(app)
    prepare_body = {
        "observed_resource_version": 1,
        "observed_snapshot_hash": HASH_A,
        "observed_rendered_hash": HASH_B,
        "recipient_binding_id": str(RECIPIENT_ID),
        "channel": "INTERNAL_TEST",
    }
    try:
        quotes = client.get(f"/internal/v1/stores/{STORE_ID}/quotes")
        incidents = client.get(f"/internal/v1/stores/{STORE_ID}/incidents")
        queue = client.get("/internal/v1/queue-recovery")
        authority_injection = client.post(
            f"/internal/v1/approvals/{APPROVAL_ID}/manual-send",
            headers={"Idempotency-Key": "manual-authority-injection"},
            json={**prepare_body, "deployment_stage": "AUTONOMOUS"},
        )
        prepared = client.post(
            f"/internal/v1/approvals/{APPROVAL_ID}/manual-send",
            headers={"Idempotency-Key": "manual-prepare"},
            json=prepare_body,
        )
        missing_version = client.post(
            f"/internal/v1/manual-sends/{MANUAL_SEND_ID}/attest",
            headers={"Idempotency-Key": "manual-attest-missing-version"},
            json={
                "observed_resource_version": 1,
                "exact_rendered_hash": HASH_B,
                "sent_at": "2026-08-01T03:00:00Z",
            },
        )
        attested = client.post(
            f"/internal/v1/manual-sends/{MANUAL_SEND_ID}/attest",
            headers={"Idempotency-Key": "manual-attest", "If-Match": '"1"'},
            json={
                "observed_resource_version": 1,
                "exact_rendered_hash": HASH_B,
                "sent_at": "2026-08-01T03:00:00Z",
            },
        )
        opened = client.post(
            f"/internal/v1/stores/{STORE_ID}/incidents",
            headers={"Idempotency-Key": "incident-open"},
            json={
                "order_id": str(ORDER_ID),
                "contact_scope_hash": SCOPE_HASH,
                "evidence_summary_hash": EVIDENCE_HASH,
            },
        )
        missing_reason = client.post(
            f"/internal/v1/approvals/{APPROVAL_ID}/decisions",
            headers={"Idempotency-Key": "approval-no-reason"},
            json={
                "decision": "APPROVED",
                "resource_version": 1,
                "snapshot_hash": HASH_A,
                "rendered_hash": HASH_B,
            },
        )
        decided = client.post(
            f"/internal/v1/approvals/{APPROVAL_ID}/decisions",
            headers={"Idempotency-Key": "approval-with-reason"},
            json={
                "decision": "APPROVED",
                "reason_code": "HUMAN_REVIEW_COMPLETE",
                "note": "Verified exact preview",
                "resource_version": 1,
                "snapshot_hash": HASH_A,
                "rendered_hash": HASH_B,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert quotes.status_code == 200
    assert quotes.json()[0]["snapshot_hash"] == HASH_A
    assert incidents.status_code == 200
    assert incidents.json()[0]["fault_decided"] is False
    assert queue.status_code == 200
    assert queue.json()["replay_available"] is False
    assert authority_injection.status_code == 422
    assert prepared.status_code == 201
    assert prepared.json()["status"] == "APPROVED_FOR_MANUAL_SEND"
    assert missing_version.status_code == 428
    assert attested.status_code == 200
    assert attested.json()["row_version"] == 2
    assert opened.status_code == 201
    assert opened.json()["remedy_decided"] is False
    assert missing_reason.status_code == 422
    assert decided.status_code == 200


def test_staff_mutations_require_mfa_before_service_dispatch() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "operator-without-mfa", frozenset({StaffRole.OPERATOR}), False
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    try:
        manual = TestClient(app).post(
            f"/internal/v1/approvals/{APPROVAL_ID}/manual-send",
            headers={"Idempotency-Key": "no-mfa-manual"},
            json={
                "observed_resource_version": 1,
                "observed_snapshot_hash": HASH_A,
                "observed_rendered_hash": HASH_B,
                "recipient_binding_id": str(RECIPIENT_ID),
                "channel": "INTERNAL_TEST",
            },
        )
        incident = TestClient(app).post(
            f"/internal/v1/stores/{STORE_ID}/incidents",
            headers={"Idempotency-Key": "no-mfa-incident"},
            json={
                "order_id": str(ORDER_ID),
                "contact_scope_hash": SCOPE_HASH,
                "evidence_summary_hash": EVIDENCE_HASH,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert manual.status_code == 403
    assert incident.status_code == 403
