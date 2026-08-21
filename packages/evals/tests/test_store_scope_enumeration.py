"""Every internal route is classified for store scope, and the classification is checked.

`STORE-SCOPING-001` enumerated store-scoped routes by URL shape — every path containing
`/stores/{store_id}/`. `POST /internal/v1/orders/{order_id}/transition` is keyed by `order_id`, so
it was not in that list and went unfixed: any principal with an operations role and MFA could drive
any order's commercial state in any store. That is a cross-store *write*, and it was found by a
person reading the route table against the repository, not by a test.

That is the finding this module exists for. Hand enumeration by URL shape will miss the next route
keyed by something other than `store_id` too, so the enumeration is made mechanical here:

  * every `/internal/` route must appear in `ROUTE_SCOPE` — a new route fails this module until
    somebody states what its store scope is, which is a decision rather than an oversight;
  * every route classified `STORE_SCOPED` must name a repository method that really calls
    `require_store_membership`, verified by reading the source, so deleting the call fails the
    build;
  * the two routes that are deliberately not membership-checked are named here with the item or
    decision that owns them, so they cannot be silently ratified by absence.

The check reads source rather than behaviour on purpose. A behavioural test proves one path is
guarded on the day it is written; this proves the guard is still written down in every method that
claims one, which is the property that decays.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from nha_trang_laundry_api.main import app

ROOT = Path(__file__).resolve().parents[3]
DB_SOURCE = ROOT / "packages/db/src/nha_trang_laundry_db"
MEMBERSHIP_CALL = "require_store_membership"
# Everywhere application SQL lives. Scripts are included because the demo seed writes assignments.
SOURCE_ROOTS_WITH_SQL = (DB_SOURCE, ROOT / "apps", ROOT / "scripts")

Classification = Literal["STORE_SCOPED", "GLOBAL_BY_DESIGN", "NOT_STORE_DATA", "KNOWN_GAP"]


@dataclass(frozen=True)
class RouteScope:
    """What this route does about store membership, and where that is enforced or recorded."""

    classification: Classification
    # (module stem, ClassName.method) whose source must contain the membership call.
    enforced_in: tuple[str, str] | None
    note: str


def store_scoped(module: str, method: str) -> RouteScope:
    return RouteScope("STORE_SCOPED", (module, method), "membership enforced in the repository")


#: Every `/internal/` route, and what it does about store scope. Keyed by (method, path).
ROUTE_SCOPE: dict[tuple[str, str], RouteScope] = {
    # --- store-scoped: the repository must require membership -------------------------------
    ("POST", "/internal/v1/stores/{store_id}/orders"): store_scoped(
        "orders", "OrderRepository.create"
    ),
    ("GET", "/internal/v1/stores/{store_id}/orders"): store_scoped(
        "orders", "OrderRepository.list_for_store"
    ),
    ("POST", "/internal/v1/orders/{order_id}/transition"): store_scoped(
        "orders", "OrderRepository.transition"
    ),
    ("POST", "/internal/v1/orders/{order_id}/settlement"): store_scoped(
        "settlement", "SettlementRepository.record"
    ),
    ("GET", "/internal/v1/stores/{store_id}/settlements/today"): store_scoped(
        "settlement", "SettlementRepository.collected_today"
    ),
    ("POST", "/internal/v1/stores/{store_id}/quotes"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.create_quote before any pricing work, and "
        "outside the idempotency wrapper so a revoked member cannot replay a key; asserted "
        "behaviourally in apps/api/tests/test_quote_command.py",
    ),
    ("GET", "/internal/v1/stores/{store_id}/quotes"): store_scoped(
        "quotes", "QuoteRepository.list_for_store"
    ),
    ("POST", "/internal/v1/stores/{store_id}/incidents"): store_scoped(
        "incidents", "IncidentRepository.open"
    ),
    ("GET", "/internal/v1/stores/{store_id}/incidents"): store_scoped(
        "incidents", "IncidentRepository.list_for_store"
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/drafts"): store_scoped(
        "shadow_console", "ShadowConsoleRepository.list_pending_drafts"
    ),
    ("GET", "/internal/v1/stores/{store_id}/shadow/reviews"): store_scoped(
        "shadow_console", "ShadowConsoleRepository.list_reviewed_drafts"
    ),
    ("POST", "/internal/v1/stores/{store_id}/assistant/turns"): store_scoped(
        "assistant", "AssistantTurnRepository.record_turn"
    ),
    ("GET", "/internal/v1/stores/{store_id}/assistant/turns"): store_scoped(
        "assistant", "AssistantTurnRepository.list_recent"
    ),
    ("GET", "/internal/v1/stores/{store_id}/assistant/turns/{turn_id}/stream"): store_scoped(
        "assistant", "AssistantTurnRepository.get_scoped"
    ),
    ("POST", "/internal/v1/stores/{store_id}/order-requests"): RouteScope(
        "STORE_SCOPED",
        None,
        "membership enforced in OperationsService.create_order_request before any write, and "
        "outside the idempotency wrapper so a revoked member cannot replay a key; asserted "
        "behaviourally in apps/api/tests/test_intake_order_requests.py",
    ),
    ("GET", "/internal/v1/stores/{store_id}/order-requests"): store_scoped(
        "intake", "OrderRequestRepository.list_for_store"
    ),
    ("GET", "/internal/v1/stores/{store_id}/order-requests/{order_request_id}"): store_scoped(
        "intake", "OrderRequestRepository.get_for_store"
    ),
    # --- deliberately not membership-checked, each owned by something ------------------------
    ("GET", "/internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}"): RouteScope(
        "KNOWN_GAP",
        None,
        "Requires membership of the *path* store and then filters only on aggregate_id, so a "
        "member of store A can read store B's audit trail by supplying its aggregate identifier. "
        "Named in context/tasks/TASK-store-scoping-002.md as out of scope for that item and "
        "scoped to its own; recorded here so the enumeration does not report it as compliant.",
    ),
    ("GET", "/internal/v1/shadow/unknown-sends"): RouteScope(
        "GLOBAL_BY_DESIGN",
        None,
        "The unknown-send queue is global and gated by role alone. A send whose outcome is "
        "unknown has no confirmed recipient and therefore no reliable store, and leaving one "
        "unreconciled is the failure this queue exists to prevent — so scoping it by membership "
        "would hide work rather than protect data. It exposes provider, message kind, attempt "
        "number and reconciliation state, and no customer content. Classified deliberately.",
    ),
    ("POST", "/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile"): RouteScope(
        "GLOBAL_BY_DESIGN",
        None,
        "The write side of the global queue above, restricted to operations staff with MFA. "
        "Same reasoning: a receipt with no confirmed recipient has no store to scope to.",
    ),
    ("POST", "/internal/v1/shadow/drafts/{agent_run_id}/decision"): RouteScope(
        "STORE_SCOPED",
        ("shadow_console", "ShadowConsoleRepository.decide_draft"),
        "keyed by agent_run_id; the repository resolves the run's store and requires membership",
    ),
    # --- not store data ----------------------------------------------------------------------
    ("POST", "/internal/v1/auth/session"): RouteScope(
        "NOT_STORE_DATA", None, "identity exchange; establishes who the caller is"
    ),
    ("POST", "/internal/v1/auth/logout"): RouteScope(
        "NOT_STORE_DATA", None, "ends the caller's own session"
    ),
    ("GET", "/internal/v1/session"): RouteScope(
        "NOT_STORE_DATA", None, "the caller's own principal"
    ),
    ("POST", "/internal/v1/sessions/{session_id}/revoke"): RouteScope(
        "NOT_STORE_DATA", None, "own session, or any session for OWNER_ADMIN"
    ),
    ("GET", "/internal/v1/stores"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "answers 'which stores am I in' and is derived from the caller's own assignments, so "
        "membership is the result rather than a precondition",
    ),
    ("POST", "/internal/v1/staff"): RouteScope(
        "NOT_STORE_DATA", None, "staff administration, OWNER_ADMIN only"
    ),
    ("POST", "/internal/v1/staff/{staff_user_id}/roles"): RouteScope(
        "NOT_STORE_DATA", None, "staff administration, OWNER_ADMIN only"
    ),
    ("POST", "/internal/v1/staff/{staff_user_id}/disable"): RouteScope(
        "NOT_STORE_DATA", None, "staff administration, OWNER_ADMIN only"
    ),
    ("POST", "/internal/v1/staff/{staff_user_id}/stores/{store_id}"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "STORE-ASSIGNMENT-001. Grants membership rather than consuming it, so requiring the "
        "grantor to already be a member would make the first assignment in a new deployment "
        "impossible and force the hand-written INSERT this item removed. Owner-gated at the route "
        "and re-checked against the database in the repository; the answer to 'may an owner assign "
        "into a store they are not a member of' is yes, and it is asserted in "
        "packages/db/tests/test_store_assignment.py so a later change to it fails a test.",
    ),
    ("DELETE", "/internal/v1/staff/{staff_user_id}/stores/{store_id}"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "STORE-ASSIGNMENT-001. The revoke side of the grant above, owner-gated the same way and "
        "audited the same way; membership is the thing being removed, not a precondition.",
    ),
    ("POST", "/internal/v1/approvals"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "approval envelopes are keyed by resource_type/resource_id and carry no store column; "
        "scoping them is part of the approval surface's own item, not silently assumed here",
    ),
    ("GET", "/internal/v1/approvals"): RouteScope(
        "NOT_STORE_DATA", None, "pending approval queue, gated by approval role with MFA"
    ),
    ("POST", "/internal/v1/approvals/{approval_id}/decisions"): RouteScope(
        "NOT_STORE_DATA", None, "decision on an approval envelope; see above"
    ),
    ("POST", "/internal/v1/approvals/{approval_id}/manual-send"): RouteScope(
        "NOT_STORE_DATA", None, "manual send envelope bound to an approval; see above"
    ),
    ("POST", "/internal/v1/manual-sends/{manual_send_id}/attest"): RouteScope(
        "NOT_STORE_DATA", None, "attestation against a manual send envelope; see above"
    ),
    ("GET", "/internal/v1/queue-recovery"): RouteScope(
        "NOT_STORE_DATA", None, "process-wide queue counters; contains no customer data"
    ),
    ("GET", "/internal/v1/pricebook/services"): RouteScope(
        "NOT_STORE_DATA",
        None,
        "the published pricebook is deployment-global configuration, not store data; the read "
        "carries no store column and no customer data, and is role-gated like the pricing "
        "surface it feeds",
    ),
}


def internal_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/internal/"):
            continue
        for verb in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            found.add((verb, path))
    return found


def method_source(module: str, qualified: str) -> ast.FunctionDef:
    """Return the AST of `Class.method` in `packages/db/.../{module}.py`."""
    class_name, _, method_name = qualified.partition(".")
    tree = ast.parse((DB_SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{module}.py has no method {qualified}")


def called_names(node: ast.AST) -> set[str]:
    """Every simple name this node calls, whether bare, `self.x`, or `Class.x`."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        if isinstance(inner.func, ast.Name):
            names.add(inner.func.id)
        elif isinstance(inner.func, ast.Attribute):
            names.add(inner.func.attr)
    return names


def guard_names(module: str) -> frozenset[str]:
    """Names that amount to a membership check in this module, resolved transitively.

    A repository may call `require_store_membership` directly, or through a local helper that does.
    Following the indirection matters: `ShadowConsoleRepository` guards its methods through
    `_require_store_access`, and a checker that only looked for the direct call would report two
    correctly guarded methods as unguarded — which it did, before this existed.

    The fixpoint is deliberately confined to one module. A helper defined elsewhere would not be
    resolved, and that is the conservative direction to fail in.
    """
    tree = ast.parse((DB_SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    functions: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node

    guards = {MEMBERSHIP_CALL}
    changed = True
    while changed:
        changed = False
        for name, function in functions.items():
            if name not in guards and called_names(function) & guards:
                guards.add(name)
                changed = True
    return frozenset(guards)


def calls_membership_check(node: ast.AST, guards: frozenset[str] | None = None) -> bool:
    return bool(called_names(node) & (guards or frozenset({MEMBERSHIP_CALL})))


def test_every_internal_route_is_classified_for_store_scope() -> None:
    """A new route must be classified before it can ship.

    This is the check that would have caught `STORE-SCOPING-002` at the time the route was added:
    the transition route existed, was store-scoped in effect, and nothing forced anyone to say so.
    """
    live = internal_routes()
    classified = set(ROUTE_SCOPE)
    unclassified = live - classified
    stale = classified - live
    assert not unclassified, (
        "these routes have no store-scope classification; add them to ROUTE_SCOPE and state "
        f"whether membership applies: {sorted(unclassified)}"
    )
    assert not stale, f"ROUTE_SCOPE names routes that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize(
    ("route", "scope"),
    [
        (route, scope)
        for route, scope in sorted(ROUTE_SCOPE.items())
        if scope.classification == "STORE_SCOPED" and scope.enforced_in is not None
    ],
)
def test_store_scoped_routes_enforce_membership_in_their_repository(
    route: tuple[str, str], scope: RouteScope
) -> None:
    assert scope.enforced_in is not None
    module, qualified = scope.enforced_in
    assert calls_membership_check(method_source(module, qualified), guard_names(module)), (
        f"{route[0]} {route[1]} is classified STORE_SCOPED but {module}.{qualified} does not call "
        f"{MEMBERSHIP_CALL}. A route is a place a check can be forgotten; the repository is the "
        "only path to the data, so the check belongs there."
    )


def test_the_transition_route_is_the_one_this_item_fixed() -> None:
    """Named explicitly, so a revert is a failing test rather than a silent regression.

    Rollback of this item restores a cross-store write path. That is a security regression, and it
    is recorded as one in the evidence for `STORE-SCOPING-002`.
    """
    assert calls_membership_check(
        method_source("orders", "OrderRepository.transition"), guard_names("orders")
    )


def test_the_enumeration_fails_when_a_membership_check_is_removed() -> None:
    """Prove the check has teeth, by deleting it from a copy of the source and re-running.

    A test that only ever sees compliant source cannot distinguish "the check is present" from
    "the checker is broken". This removes the call from the real `transition` source in memory and
    requires the detector to notice.
    """
    original = method_source("orders", "OrderRepository.transition")
    assert calls_membership_check(original)

    class _StripMembership(ast.NodeTransformer):
        def visit_Expr(self, node: ast.Expr) -> ast.Expr | None:
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == MEMBERSHIP_CALL
            ):
                return None
            return node

    stripped = _StripMembership().visit(ast.parse(ast.unparse(original)))
    assert not calls_membership_check(stripped), (
        "the detector reported a membership call in source it had just been removed from, so it "
        "would not fail if someone deleted the real one"
    )


def test_every_read_of_the_assignment_table_excludes_revoked_rows() -> None:
    """`STORE-ASSIGNMENT-001` made revocation soft, which is only correct if every reader filters.

    A revoked assignment is still a row. If one query anywhere forgets `revoked_at IS NULL`, a
    revoked staff member keeps access on exactly that path and nowhere else — the quietest possible
    security failure, and the same shape as the defect `STORE-SCOPING-002` closed: a check present
    in most places and missing in one.

    So the constraint is mechanical rather than remembered. Every SQL statement in the source that
    reads `staff_store_assignments` must also mention `revoked_at`, whether to filter it (reads) or
    to set it (the revoke itself). Adding a query without one fails here.
    """
    offenders: list[str] = []
    for root in SOURCE_ROOTS_WITH_SQL:
        for module in sorted(root.rglob("*.py")):
            # Application source only. A test may legitimately count every row including revoked
            # ones — proving a replay wrote no duplicate needs exactly that — and a test is not an
            # access path. What must never forget the filter is code that answers a request.
            if "tests" in module.parts:
                continue
            for statement in _assignment_reads(module.read_text(encoding="utf-8")):
                if "revoked_at" not in statement:
                    offenders.append(f"{module.name}: {' '.join(statement.split())[:110]}")
    assert not offenders, (
        "these statements read staff_store_assignments without filtering revoked_at, so a revoked "
        "assignment would still count as membership there:\n  " + "\n  ".join(offenders)
    )


def _assignment_reads(source: str) -> list[str]:
    """String literals that *read* the assignment table.

    Reads are the risk, so only `FROM` and `JOIN` count. An `INSERT` legitimately omits the column —
    a new assignment is active, and `revoked_at` defaults to NULL — and prose that happens to name
    the table is not a query. Both were false positives on the first version of this check.
    """
    reads: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        collapsed = " ".join(text.split()).lower()
        if "from staff_store_assignments" in collapsed or "join staff_store_assignments" in (
            collapsed
        ):
            reads.append(text)
    return reads


def test_known_gaps_and_global_routes_each_carry_a_reason() -> None:
    """Absence of a check must be a recorded decision, never an accident nobody noticed."""
    deliberate = {
        route: scope
        for route, scope in ROUTE_SCOPE.items()
        if scope.classification in {"KNOWN_GAP", "GLOBAL_BY_DESIGN"}
    }
    assert deliberate, "the two known cases must stay classified rather than quietly enforced"
    for route, scope in deliberate.items():
        assert len(scope.note) > 80, f"{route} is unscoped with no stated reason"

    audit = ROUTE_SCOPE[("GET", "/internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}")]
    assert audit.classification == "KNOWN_GAP"
    assert "aggregate_id" in audit.note

    unknown_sends = ROUTE_SCOPE[("GET", "/internal/v1/shadow/unknown-sends")]
    assert unknown_sends.classification == "GLOBAL_BY_DESIGN"
    assert "no confirmed recipient" in unknown_sends.note
