from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_identity_service,
    get_operations_service,
)
from nha_trang_laundry_db.approvals import StoredApproval
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.orders import StoredOrder
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


def test_staff_session_fails_closed_without_identity_configuration() -> None:
    response = TestClient(app).post(
        "/internal/v1/auth/session", headers={"Authorization": "Bearer untrusted-token"}
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
