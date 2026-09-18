"""The HTTP contract of the remedy surface. `REMEDY-001`.

The repository tests walk `DEC-004` from a recorded complaint to a credit against real PostgreSQL.
What those cannot see is what a console receives, and that is what this module pins.

Most of it is refusals, and the shape of a refusal is the point. A remedy that is not authorised is
a **policy answer with a number attached** -- "quá 5 lần phí giặt của món đó, tối đa 500.000 đ",
"quá 7 ngày kể từ khi nhận đồ" -- so the body carries the reason code and the figure as fields. A
console that had to parse an English sentence to render that copy in Vietnamese would make the
shop's customer-facing wording a regex, and the first refusal message somebody reworded would break
it silently.

A stub service, not a database: every defect these guard is in the route layer -- an exception arm
that was never written, a field that never reached the response -- and a test that needed PostgreSQL
would hide behind the integration flag.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import (
    StoredCreditRedemptionResult,
    StoredRemedyExecutionResult,
    StoredRemedyProposalResult,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.remedies import RemedyAuthorizationError, RemedyOptions, RemedyStateError
from nha_trang_laundry_db.store_access import StoreAccessError

STAFF_ID = UUID("00000000-0000-0000-0000-0000000004a1")
STORE_ID = UUID("00000000-0000-0000-0000-0000000004a2")
INCIDENT_ID = UUID("00000000-0000-0000-0000-0000000004a3")
ORDER_ID = UUID("00000000-0000-0000-0000-0000000004a4")
PROPOSAL_ID = UUID("00000000-0000-0000-0000-0000000004a5")
QUOTE_ID = UUID("00000000-0000-0000-0000-0000000004a6")
CREDIT_ID = UUID("00000000-0000-0000-0000-0000000004a7")
APPROVAL_ID = UUID("00000000-0000-0000-0000-0000000004a8")
HASH = "JCS-SHA256-V1:" + "a" * 64
RETURNED_AT = datetime(2026, 9, 10, 3, tzinfo=UTC)

ORIGIN = "http://testserver"
CSRF = "y" * 40


class StubService:
    """Answers whatever the test put in `outcome`, or raises whatever it put in `refusal`."""

    outcome: Any = None
    refusal: Exception | None = None

    def _answer(self, **_: Any) -> Any:
        if self.refusal is not None:
            raise self.refusal
        return self.outcome

    remedy_options = _answer
    propose_remedy = _answer
    execute_remedy = _answer
    redeem_remedy_credit = _answer


@pytest.fixture
def stub() -> StubService:
    return StubService()


@pytest.fixture
def client(stub: StubService) -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "operator-subject", frozenset({StaffRole.OPERATOR}), True
    )
    app.dependency_overrides[get_operations_service] = lambda: stub
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _headers() -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": "remedy-key-0001"}


def _proposal(**overrides: Any) -> StoredRemedyProposalResult:
    fields: dict[str, Any] = {
        "proposal_id": PROPOSAL_ID,
        "incident_id": INCIDENT_ID,
        "order_id": ORDER_ID,
        "kind": "DAMAGE_COMPENSATION",
        "status": "STAFF_AUTHORIZED",
        "outcome": "ALLOW",
        "proposal_hash": HASH,
        "policy_version": 1,
        "amount_vnd": 80_000,
        "ceiling_vnd": 500_000,
        "window_opened_at": RETURNED_AT.isoformat(),
        "window_closes_at": (RETURNED_AT + timedelta(hours=24)).isoformat(),
        "approval_id": None,
        "reason_code": None,
        "replayed": False,
    }
    fields.update(overrides)
    return StoredRemedyProposalResult(**fields)


def _propose(client: TestClient, **body: Any) -> Any:
    payload: dict[str, Any] = {"kind": "DAMAGE_COMPENSATION", "store_fault_attested": True}
    payload.update(body)
    return client.post(
        f"/internal/v1/stores/{STORE_ID}/incidents/{INCIDENT_ID}/remedy-proposals",
        headers=_headers(),
        json=payload,
    )


# --- the happy answers a console has to render ----------------------------------------------------


def test_a_proposal_returns_the_computed_ceiling_and_window(
    client: TestClient, stub: StubService
) -> None:
    """Both figures the server computed, so the console never has to derive either."""

    stub.outcome = _proposal()
    response = _propose(client, order_line_id="line-1", amount_vnd=80_000)

    assert response.status_code == 201
    body = response.json()
    assert body["ceiling_vnd"] == 500_000
    assert body["window_closes_at"] == (RETURNED_AT + timedelta(hours=24)).isoformat()
    assert body["approval_id"] is None
    assert body["status"] == "STAFF_AUTHORIZED"


def test_a_proposal_above_the_staff_ceiling_names_the_envelope_the_owner_must_decide(
    client: TestClient, stub: StubService
) -> None:
    """Staff must never learn that the owner is required after the fact -- so it is in the body."""

    stub.outcome = _proposal(
        status="OWNER_APPROVAL_REQUIRED", amount_vnd=250_000, approval_id=APPROVAL_ID
    )
    response = _propose(client, order_line_id="line-1", amount_vnd=250_000)

    assert response.status_code == 201
    assert response.json()["status"] == "OWNER_APPROVAL_REQUIRED"
    assert response.json()["approval_id"] == str(APPROVAL_ID)


def test_a_loss_is_a_recorded_outcome_and_not_an_error(
    client: TestClient, stub: StubService
) -> None:
    """`DEC-004` carries loss forward as undecided, and the record *is* the outcome for one.

    201 rather than 4xx, because the complaint was written down -- which is the whole point of the
    item. Every figure is null: there is no loss ceiling, and a body that carried one would be this
    surface ratifying a number the owner declined to give.
    """

    stub.outcome = _proposal(
        kind="LOST_ITEM",
        status="POLICY_UNRESOLVED",
        outcome="REQUIRE_HUMAN",
        amount_vnd=None,
        ceiling_vnd=None,
        window_opened_at=None,
        window_closes_at=None,
        reason_code="LOSS_POLICY_UNRESOLVED",
    )
    response = _propose(client, kind="LOST_ITEM")

    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "REQUIRE_HUMAN"
    assert body["reason_code"] == "LOSS_POLICY_UNRESOLVED"
    assert (body["amount_vnd"], body["ceiling_vnd"], body["window_closes_at"]) == (None, None, None)


def test_the_options_read_carries_everything_the_form_needs_before_anybody_types(
    client: TestClient, stub: StubService
) -> None:
    stub.outcome = RemedyOptions(
        incident_id=INCIDENT_ID,
        order_id=ORDER_ID,
        policy_published=True,
        staff_approval_ceiling_vnd=100_000,
        goods_returned_at=RETURNED_AT,
        rewash_window_closes_at=RETURNED_AT + timedelta(days=7),
        rewash_window_open=True,
        defect_window_closes_at=RETURNED_AT + timedelta(hours=24),
        defect_window_open=False,
        damage_line_ceilings_vnd={"line-1": 500_000},
        late_delivery_credit_vnd=None,
        late_delivery_threshold_minutes=120,
    )
    response = client.get(f"/internal/v1/stores/{STORE_ID}/incidents/{INCIDENT_ID}/remedy-options")

    assert response.status_code == 200
    body = response.json()
    assert body["staff_approval_ceiling_vnd"] == 100_000
    assert body["damage_line_ceilings_vnd"] == {"line-1": 500_000}
    assert body["defect_window_open"] is False
    # Null, not zero. There is no late-delivery credit to offer on an order nobody delivered, and
    # the console's house style renders an absent total as "—" rather than as 0 đ.
    assert body["late_delivery_credit_vnd"] is None
    assert body["loss_reason_code"] == "LOSS_POLICY_UNRESOLVED"


def test_an_unpublished_policy_is_reported_as_unpublished_rather_than_as_zeroes(
    client: TestClient, stub: StubService
) -> None:
    """Invariant 11 has to be visible on the surface, or the console renders an empty form."""

    stub.outcome = RemedyOptions(incident_id=INCIDENT_ID, order_id=ORDER_ID, policy_published=False)
    response = client.get(f"/internal/v1/stores/{STORE_ID}/incidents/{INCIDENT_ID}/remedy-options")

    assert response.status_code == 200
    body = response.json()
    assert body["policy_published"] is False
    assert body["staff_approval_ceiling_vnd"] is None
    assert body["damage_line_ceilings_vnd"] is None


def test_an_execution_reports_the_event_it_produced(client: TestClient, stub: StubService) -> None:
    stub.outcome = StoredRemedyExecutionResult(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        order_id=ORDER_ID,
        kind="FREE_REWASH",
        status="EXECUTED",
        event_type="REWASH_COMMANDED",
        credit_id=None,
        amount_vnd=None,
        replayed=False,
    )
    response = client.post(
        f"/internal/v1/remedy-proposals/{PROPOSAL_ID}/execution", headers=_headers()
    )

    assert response.status_code == 201
    assert response.json()["event_type"] == "REWASH_COMMANDED"
    assert response.json()["credit_id"] is None


def test_a_redemption_reports_the_revision_the_credit_landed_on(
    client: TestClient, stub: StubService
) -> None:
    stub.outcome = StoredCreditRedemptionResult(
        credit_id=CREDIT_ID,
        quote_id=QUOTE_ID,
        revision=2,
        snapshot_hash=HASH,
        credit_vnd=11_000,
        net_service_subtotal_vnd=89_000,
        display_total_vnd=99_000,
        replayed=False,
    )
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/quotes/{QUOTE_ID}/remedy-credits",
        headers=_headers(),
        json={
            "credit_id": str(CREDIT_ID),
            "expected_current_revision": 1,
            "expected_snapshot_hash": HASH,
        },
    )

    assert response.status_code == 201
    assert response.json()["revision"] == 2
    assert response.json()["credit_vnd"] == 11_000


# --- refusals, and the figures they carry ---------------------------------------------------------


def test_a_ceiling_refusal_carries_the_ceiling_as_a_field(
    client: TestClient, stub: StubService
) -> None:
    """The console says "tối đa 500.000 đ"; it must not have to parse that out of a sentence."""

    stub.refusal = RemedyStateError(
        "this remedy is not authorised",
        reason_code="REMEDY_CEILING_EXCEEDED",
        authority="DEC-004",
        ceiling_vnd=500_000,
    )
    response = _propose(client, order_line_id="line-1", amount_vnd=500_001)

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "reason_code": "REMEDY_CEILING_EXCEEDED",
        "authority": "DEC-004",
        "ceiling_vnd": 500_000,
    }


def test_a_window_refusal_carries_the_moment_the_window_closed(
    client: TestClient, stub: StubService
) -> None:
    closed_at = RETURNED_AT + timedelta(days=7)
    stub.refusal = RemedyStateError(
        "this remedy is not authorised",
        reason_code="REMEDY_WINDOW_CLOSED",
        authority="DEC-004",
        window_closes_at=closed_at,
    )
    response = _propose(client, kind="FREE_REWASH")

    assert response.status_code == 422
    assert response.json()["detail"]["window_closes_at"] == closed_at.isoformat()


def test_an_unpublished_policy_refuses_every_remedy_with_its_invariant_named(
    client: TestClient, stub: StubService
) -> None:
    stub.refusal = RemedyStateError(
        "no remedy policy is published, so no remedy may be authorised",
        reason_code="REMEDY_POLICY_UNPUBLISHED",
        authority="INVARIANT-11",
    )
    response = _propose(client, kind="FREE_REWASH")

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "reason_code": "REMEDY_POLICY_UNPUBLISHED",
        "authority": "INVARIANT-11",
    }


def test_a_second_redemption_of_one_credit_is_refused_with_its_own_code(
    client: TestClient, stub: StubService
) -> None:
    stub.refusal = RemedyStateError(
        "this credit has already been redeemed",
        reason_code="REMEDY_CREDIT_ALREADY_REDEEMED",
        authority="DEC-015",
    )
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/quotes/{QUOTE_ID}/remedy-credits",
        headers=_headers(),
        json={
            "credit_id": str(CREDIT_ID),
            "expected_current_revision": 1,
            "expected_snapshot_hash": HASH,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "REMEDY_CREDIT_ALREADY_REDEEMED"


@pytest.mark.parametrize(
    "refusal",
    [
        RemedyAuthorizationError("store access is not authorized"),
        StoreAccessError("store access is not authorized"),
    ],
)
def test_an_unassigned_staff_member_is_refused_opaquely_and_not_crashed_at(
    client: TestClient, stub: StubService, refusal: Exception
) -> None:
    """Both refusals descend from `PermissionError` and therefore `OSError`.

    An arm that forgets them returns 500 -- the defect `test_store_scope_refusals.py` exists for --
    and the message must stay the same opaque string as every other store-scoped surface, so a
    caller cannot tell "not a member of this store" from "your role cannot do this".
    """

    stub.refusal = refusal
    for response in (
        _propose(client, order_line_id="line-1", amount_vnd=1_000),
        client.get(f"/internal/v1/stores/{STORE_ID}/incidents/{INCIDENT_ID}/remedy-options"),
        client.post(f"/internal/v1/remedy-proposals/{PROPOSAL_ID}/execution", headers=_headers()),
    ):
        assert response.status_code == 403
        assert response.json() == {"detail": "operation denied"}


# --- what a client may not send -------------------------------------------------------------------


def test_the_request_has_no_ceiling_field_for_a_client_to_set(
    client: TestClient, stub: StubService
) -> None:
    """The bound is the server's. `StrictRequest` forbids unknown fields, so this is a 422.

    Without `extra="forbid"` a client could POST `ceiling_vnd` and be quietly ignored, which reads
    to whoever wrote that client exactly like the server accepting it.
    """

    stub.outcome = _proposal()
    for field in ("ceiling_vnd", "window_closes_at", "policy_version", "status"):
        response = _propose(client, order_line_id="line-1", amount_vnd=1_000, **{field: 1})
        assert response.status_code == 422


def test_a_negative_or_fractional_amount_is_refused_by_the_model(
    client: TestClient, stub: StubService
) -> None:
    """Invariant 2 at the edge of the system: money is a non-negative integer of dong.

    `2**53` is refused because `MAX_CANONICAL_INT` is the largest integer with a canonical JCS form:
    a larger one cannot survive the digest the approval binds, and accepting it here would turn a
    bad input into a 500 deeper in -- the defect `canonical.MAX_CANONICAL_INT` was introduced for.

    A numeric *string* is deliberately not in this list. Pydantic's non-strict mode coerces
    `"80000"` to 80000 losslessly, and every other money field on this surface behaves the same way;
    refusing it only here would make this field inconsistent with the rest of the API for no gain.
    """

    stub.outcome = _proposal()
    for amount in (-1, 0.5, 2**53):
        response = _propose(client, order_line_id="line-1", amount_vnd=amount)
        assert response.status_code == 422, amount


def test_an_unknown_remedy_kind_is_refused_rather_than_coerced(
    client: TestClient, stub: StubService
) -> None:
    """There are four kinds and there is deliberately no fifth."""

    stub.outcome = _proposal()
    assert _propose(client, kind="GOODWILL_GESTURE").status_code == 422


def test_the_execution_route_takes_no_amount(client: TestClient, stub: StubService) -> None:
    """Invariant 8: the owner approved a digest of a figure, so the figure cannot move afterwards.

    The route has no request model at all, so a body naming an amount reaches a function that has
    nowhere to put it. Asserted because "there is no field" is the control, and a later refactor
    that adds one would silently reopen the gap between approval and payment.
    """

    stub.outcome = StoredRemedyExecutionResult(
        proposal_id=PROPOSAL_ID,
        incident_id=INCIDENT_ID,
        order_id=ORDER_ID,
        kind="DAMAGE_COMPENSATION",
        status="EXECUTED",
        event_type="CREDIT_EXECUTED",
        credit_id=CREDIT_ID,
        amount_vnd=250_000,
        replayed=False,
    )
    response = client.post(
        f"/internal/v1/remedy-proposals/{PROPOSAL_ID}/execution",
        headers=_headers(),
        json={"amount_vnd": 999_000},
    )

    assert response.status_code == 201
    # The body was ignored because there is nowhere for it to land: the stored figure is returned.
    assert response.json()["amount_vnd"] == 250_000
