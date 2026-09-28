"""Route-level authorization, declared once per gate and read by the contract (`AUTHZ-MATRIX-001`).

Two layers decide every staff request, and this module is the outer one:

1. **The route gate** (here): role set + MFA, evaluated on the principal the session produced. It
   answers "may this kind of person call this operation at all", before any row is read.
2. **The repository** (`packages/db`): re-reads roles from the database where it matters, checks
   store membership and object ownership, and is the only path to the data. It answers "may this
   person touch this row".

The route gate is not the authority -- the repository is -- but it is the layer a reviewer reads,
and before this module it existed as fifteen hand-copied functions whose role sets could only be
found by opening each one. A gate is now a value: its roles are data, the internal API contract
records them per operation (`scripts/generate_internal_api_contract.py`), and a privilege change on
any route is a reviewed diff in `specs/contracts/internal-api-v1.openapi.yaml`.

Every route is exactly one of:

- ``GATED``: carries a `RouteGate`. The default and the expectation.
- ``SESSION``: any signed-in staff member; listed in `SESSION_ROUTES` with the reason the repository
  (or self-service scope) is sufficient.
- ``PUBLIC``: no session; listed in `PUBLIC_ROUTES` with the reason.

A route in none of those fails `apps/api/tests/test_route_authorization.py`, so a new route cannot
ship on "signed in" alone by omission.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole


class RouteAccess(StrEnum):
    PUBLIC = "PUBLIC"
    SESSION = "SESSION"
    GATED = "GATED"


@dataclass(frozen=True, slots=True)
class RouteGate:
    """Who may call a route: any one of `roles`, on a session that proved MFA.

    MFA is not optional per gate. Every gated operation reads customer data, moves money or changes
    authority, which `SECURITY_RELIABILITY_SPEC_V1` §5.1 puts behind MFA; a gate without it would be
    a place the rule silently stopped applying.
    """

    name: str
    roles: frozenset[StaffRole]
    reason_code: str

    def __post_init__(self) -> None:
        if not self.name.startswith("require_"):
            raise ValueError("a route gate is named require_<what>")
        if not self.roles:
            raise ValueError("a route gate admits at least one role")
        if not self.reason_code.isupper():
            raise ValueError("a route gate's reason code is an upper-case constant")

    def admits(self, principal: StaffPrincipal) -> bool:
        return bool(principal.roles & self.roles) and principal.mfa_verified


#: Every gate the application built, by name.
GATES: dict[str, RouteGate] = {}
#: The same gates keyed by the dependency *object* that enforces them. Classification looks up a
#: route's dependencies here by identity: an unrelated function that merely happens to be called
#: `require_owner` enforces nothing and must not be recorded as a gate.
_ENFORCERS: dict[int, tuple[Callable[..., Any], RouteGate]] = {}


def register_gate(gate: RouteGate, enforcer: Callable[..., Any]) -> RouteGate:
    """Record `gate` and the dependency that enforces it; a name is never reused for other rules."""

    existing = GATES.setdefault(gate.name, gate)
    if existing != gate:
        raise ValueError(f"route gate {gate.name} is declared twice with different rules")
    _ENFORCERS[id(enforcer)] = (enforcer, gate)
    return gate


def gate_enforced_by(dependency: object) -> RouteGate | None:
    entry = _ENFORCERS.get(id(dependency))
    return entry[1] if entry is not None and entry[0] is dependency else None


#: Routes any signed-in staff member may call. Each reason names what bounds the answer instead.
SESSION_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/internal/v1/session"): "Self-service: the caller's own session and roles.",
    ("POST", "/internal/v1/auth/logout"): "Self-service: ends the caller's own session.",
    ("GET", "/internal/v1/sessions"): "Self-service: the caller's own live sessions.",
    ("POST", "/internal/v1/sessions/{session_id}/revoke"): (
        "Self-service for the caller's own session; another person's needs OWNER_ADMIN, re-read "
        "from the database by IdentityRepository.revoke_session."
    ),
    ("GET", "/internal/v1/stores"): (
        "The caller's own store assignments; StoreRepository lists memberships of the caller only."
    ),
    ("GET", "/internal/v1/stores/{store_id}/orders"): (
        "OrderRepository._require_order_read (OWNER_ADMIN, OPS_APPROVER, OPERATOR, AUDITOR) and "
        "store membership."
    ),
    ("GET", "/internal/v1/orders/{order_id}"): (
        "OrderRepository._require_order_read and membership of the order's store."
    ),
    ("GET", "/internal/v1/orders/{order_id}/vietqr"): (
        "VIETQR-001: the order read's rule (OrderRepository._require_order_read and membership of "
        "the order's store); the QR restates that read's remaining balance and the shop's own "
        "published account."
    ),
    ("GET", "/internal/v1/orders/{order_id}/promise"): (
        "The promise repository applies the order-read roles and store membership."
    ),
    ("GET", "/internal/v1/stores/{store_id}/remedy-credits"): (
        "remedy_reads.STORE_CREDIT_READ_ROLES, MFA and store membership."
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/reviews"): (
        "shadow_console.SHADOW_READ_ROLES and store membership."
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/drafts"): (
        "shadow_console.SHADOW_READ_ROLES and store membership."
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/unknown-sends"): (
        "shadow_console.SHADOW_READ_ROLES, MFA and store membership."
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}"): (
        "shadow_console.SHADOW_READ_ROLES and store membership."
    ),
    ("GET", "/internal/v1/stores/{store_id}/sla-board"): (
        "shadow_console.SHADOW_READ_ROLES and store membership, as the Shadow list routes."
    ),
}

#: Routes served with no staff session at all.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): "Liveness probe; returns no data.",
    ("GET", "/readyz"): "Readiness probe; returns dependency status only, no business data.",
    ("POST", "/internal/v1/auth/session"): (
        "The sign-in exchange itself: authenticated by the OIDC ID token it carries, rate-limited "
        "per source, one session per token."
    ),
}


class UnclassifiedRouteError(RuntimeError):
    """A served route whose access is not declared, or a declaration that matches nothing."""


def classify(
    method: str, path: str, dependencies: Sequence[Callable[..., Any]]
) -> dict[str, object]:
    """The access record the contract carries for one operation.

    `dependencies` are the route's top-level dependency callables. Exactly one registered gate
    enforcer makes it ``GATED``; otherwise the route must be named in `SESSION_ROUTES` (and require
    a session) or in `PUBLIC_ROUTES` (and not require one). Anything else raises -- "signed in" is
    never the answer by default.
    """

    key = (method.upper(), path)
    gates = [gate for gate in map(gate_enforced_by, dependencies) if gate is not None]
    names = [getattr(dependency, "__name__", "") for dependency in dependencies]
    if len(gates) > 1:
        raise UnclassifiedRouteError(f"{method} {path} carries more than one route gate")
    if gates:
        if key in SESSION_ROUTES or key in PUBLIC_ROUTES:
            raise UnclassifiedRouteError(f"{method} {path} is gated and also allow-listed")
        [gate] = gates
        return {
            "access": RouteAccess.GATED.value,
            "gate": gate.name,
            "roles": sorted(role.value for role in gate.roles),
            "mfa_required": True,
        }
    if key in SESSION_ROUTES:
        if "current_principal" not in names:
            raise UnclassifiedRouteError(f"{method} {path} is listed as SESSION but needs none")
        return {"access": RouteAccess.SESSION.value, "bounded_by": SESSION_ROUTES[key]}
    if key in PUBLIC_ROUTES:
        if "current_principal" in names:
            raise UnclassifiedRouteError(f"{method} {path} is listed as PUBLIC but needs a session")
        return {"access": RouteAccess.PUBLIC.value, "bounded_by": PUBLIC_ROUTES[key]}
    raise UnclassifiedRouteError(
        f"{method} {path} has no route gate and is not allow-listed. Give it a staff_gate(...) in "
        "main.py, or add it to SESSION_ROUTES / PUBLIC_ROUTES in authorization.py with the reason "
        "the repository alone bounds what it returns."
    )


def require_allowlists_served(served: set[tuple[str, str]]) -> None:
    """An allow-list entry for a route that no longer exists is a hole waiting for a new route."""

    stale = sorted((set(SESSION_ROUTES) | set(PUBLIC_ROUTES)) - served)
    if stale:
        raise UnclassifiedRouteError(f"allow-listed but not served: {stale}")


__all__ = [
    "GATES",
    "PUBLIC_ROUTES",
    "SESSION_ROUTES",
    "RouteAccess",
    "RouteGate",
    "UnclassifiedRouteError",
    "classify",
    "gate_enforced_by",
    "register_gate",
    "require_allowlists_served",
]
