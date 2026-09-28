"""`AUTHZ-MATRIX-001`: every served route declares who may call it, and the declarations hold.

The internal API contract records each operation's access (`x-authorization`), and
`scripts/verify_contracts.py` fails when that drifts from the served application. These tests hold
the rules the records must obey, so a privilege change is both visible and bounded.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from nha_trang_laundry_api.authorization import (
    GATES,
    PUBLIC_ROUTES,
    SESSION_ROUTES,
    RouteGate,
    UnclassifiedRouteError,
    classify,
    gate_enforced_by,
    register_gate,
    require_allowlists_served,
)
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_operations_service,
    require_operations_staff,
    require_owner,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole


def _operations() -> list[tuple[str, str, dict[str, object]]]:
    records: list[tuple[str, str, dict[str, object]]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        dependencies = [d.call for d in route.dependant.dependencies if d.call is not None]
        for method in sorted((route.methods or set()) - {"HEAD"}):
            records.append((method, route.path, classify(method, route.path, dependencies)))
    return records


def test_every_served_route_declares_its_access() -> None:
    operations = _operations()
    require_allowlists_served({(method, path) for method, path, _ in operations})
    assert operations
    assert {record["access"] for _, _, record in operations} == {"GATED", "SESSION", "PUBLIC"}


def test_staff_routes_are_gated_unless_named_with_a_reason() -> None:
    """The allow-lists are short on purpose; growing them is a reviewed decision, not a default."""

    for reason in (*SESSION_ROUTES.values(), *PUBLIC_ROUTES.values()):
        assert len(reason) > 20
    assert all(path.startswith("/internal/v1/") for _, path in SESSION_ROUTES)
    # No write is callable on a bare session except ending one's own session(s).
    assert {key for key in SESSION_ROUTES if key[0] != "GET"} == {
        ("POST", "/internal/v1/auth/logout"),
        ("POST", "/internal/v1/sessions/{session_id}/revoke"),
    }


def test_only_the_owner_manages_identity_and_authority() -> None:
    staff_writes = [
        record
        for method, path, record in _operations()
        if path.startswith("/internal/v1/staff") and method != "GET"
    ]
    assert len(staff_writes) >= 6
    assert all(record["roles"] == ["OWNER_ADMIN"] for record in staff_writes)


def test_no_route_gate_admits_a_driver_yet() -> None:
    """`SECURITY_RELIABILITY_SPEC_V1` §6.2: a driver sees only assigned route data.

    No driver-scoped read exists yet, so no gate may admit `DRIVER`: a role-wide grant would show a
    driver every order in the store. When the assigned-route surface is built, it gets its own gate
    with object scoping, and this test names it.
    """

    assert all(StaffRole.DRIVER not in gate.roles for gate in GATES.values())


def test_every_gate_requires_mfa() -> None:
    principal = StaffPrincipal(uuid4(), "subject", frozenset(StaffRole), mfa_verified=False)
    assert GATES
    assert not any(gate.admits(principal) for gate in GATES.values())


def test_a_gate_admits_exactly_its_roles() -> None:
    gate = GATES["require_approval_staff"]
    for role in StaffRole:
        principal = StaffPrincipal(uuid4(), "subject", frozenset({role}), mfa_verified=True)
        assert gate.admits(principal) is (role in gate.roles)


def test_an_undeclared_session_route_fails_classification() -> None:
    with pytest.raises(UnclassifiedRouteError, match="not allow-listed"):
        classify("GET", "/internal/v1/new-thing", [current_principal, get_operations_service])
    with pytest.raises(UnclassifiedRouteError, match="not served"):
        require_allowlists_served(set())


@pytest.mark.parametrize(
    ("dependencies", "message"),
    [
        ([require_owner, require_operations_staff], "more than one"),
        ([current_principal], "PUBLIC but needs a session"),
    ],
)
def test_contradictory_declarations_fail(dependencies: list[Any], message: str) -> None:
    with pytest.raises(UnclassifiedRouteError, match=message):
        classify("GET", "/healthz", dependencies)


def test_a_look_alike_is_not_a_gate() -> None:
    """Only the registered enforcer counts; a function sharing its name enforces nothing."""

    def require_owner(principal: Any) -> Any:
        return principal

    assert gate_enforced_by(require_owner) is None
    with pytest.raises(UnclassifiedRouteError, match="not allow-listed"):
        classify("POST", "/internal/v1/new-thing", [require_owner, current_principal])


def test_a_gate_name_cannot_be_reused_for_different_rules() -> None:
    existing = GATES["require_owner"]
    assert register_gate(existing, require_owner) is existing
    with pytest.raises(ValueError, match="declared twice"):
        register_gate(
            RouteGate("require_owner", frozenset(StaffRole), "OWNER_ROLE_REQUIRED"), require_owner
        )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("owner", frozenset({StaffRole.OWNER_ADMIN}), "CODE"), "require_"),
        (("require_x", frozenset(), "CODE"), "at least one role"),
        (("require_x", frozenset({StaffRole.OWNER_ADMIN}), "code"), "upper-case"),
    ],
)
def test_a_malformed_gate_is_refused(arguments: tuple[Any, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RouteGate(*arguments)
