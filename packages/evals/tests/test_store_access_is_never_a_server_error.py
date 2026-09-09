"""Every route whose service can refuse for store membership must answer 403, not 500.

`StoreAccessError` descends from `PermissionError`, so it descends from `OSError` and NOT from
`ValueError`. A route that catches `ValueError` and its own domain error still lets this one
escape, and FastAPI turns an uncaught exception into a 500. A 500 tells a client to retry.

This has now shipped twice. `create_order` returned 500 for a staff member who held the right role
but had no `staff_store_assignments` row -- the single most common condition in the system -- and
`record_settlement` did the same on the one route that moves money, found by execution on
2026-09-09: HTTP 500 from an OPERATOR with no assignment, where 403 was intended.

Both were one missing exception type in one tuple, and neither was visible to a test that exercised
the happy path. So the invariant is checked structurally instead: enumerate the service methods
that can raise it, find the routes that call them, and require each of those routes to name it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OPERATIONS = ROOT / "apps/api/src/nha_trang_laundry_api/operations.py"
MAIN = ROOT / "apps/api/src/nha_trang_laundry_api/main.py"

#: How a membership refusal is raised. Both spellings appear: the shared helper, and the direct
#: call that passes `error=StoreAccessError`.
MEMBERSHIP_MARKERS = ("require_store_membership", "_require_order_store_membership")


def _functions(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    return {
        node.name: (ast.get_source_segment(source, node) or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


def test_every_route_that_can_be_refused_for_store_membership_answers_403() -> None:
    operations = OPERATIONS.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")

    raisers = {
        name
        for name, body in _functions(operations).items()
        if any(marker in body for marker in MEMBERSHIP_MARKERS)
    }
    assert raisers, "no service method performs a store-membership check; the sweep is misreading"

    uncaught: dict[str, list[str]] = {}
    covered = 0
    for name, body in _functions(main).items():
        called = sorted(method for method in raisers if f"service.{method}(" in body)
        if not called:
            continue
        covered += 1
        if "StoreAccessError" not in body:
            uncaught[name] = called

    assert covered, "no route calls a membership-checking service method; the sweep is misreading"
    assert not uncaught, (
        "these routes can raise StoreAccessError and do not catch it, so a staff member with the "
        f"right role but no assignment to that store gets a 500 instead of a 403: {uncaught}"
    )
