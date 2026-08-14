"""No agent-reachable path may write a balance. Asserted, because the packet says not to assume it.

`SETTLEMENT-001` adds the first command that moves money in this system, and `AGENTS.md` is
categorical: the model never computes money, decides state, or selects a customer. Settlement is a
staff console command and nothing else.

"Nothing else" is easy to believe and easy to stop being true — someone adds an eleventh tool, or
wires the facade to a repository that happens to expose more than it should. So the boundary is
checked mechanically here rather than trusted:

  * the agent tool contract still declares exactly the ten operations it was hash-pinned with, and
    none of them is a settlement;
  * the agent-facing application imports nothing from the settlement modules;
  * the settlement route is a staff route, gated by an operations role with MFA.

These read the contract and the source rather than exercising a request, because the claim is about
what does not exist. A behavioural test can only demonstrate that the paths somebody thought of are
refused.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "specs/contracts/agent-tools-v1.openapi.yaml"
AGENT_APP = ROOT / "apps/public-agent-tools/src"
WORKER_APP = ROOT / "apps/worker/src"

#: The ten operations, verbatim. The contract is hash-pinned; this is a second statement of the
#: same fact in a place a reviewer of a settlement change will actually look.
AGENT_OPERATIONS = frozenset(
    {
        "catalogResolve",
        "orderRequestCreate",
        "orderRequestRecordCustomerFacts",
        "quoteEstimate",
        "deliveryEvaluate",
        "capacityCheck",
        "messageDraftCreate",
        "publicOrderStatusGet",
        "incidentOpen",
        "approvalRequestCreate",
    }
)

SETTLEMENT_MODULES = ("settlement", "nha_trang_laundry_db.settlement")


def declared_operations() -> set[str]:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return {
        operation["operationId"]
        for path in document["paths"].values()
        for key, operation in path.items()
        if key in {"get", "post", "put", "patch", "delete"} and "operationId" in operation
    }


def imported_modules(root: Path) -> set[str]:
    found: set[str] = set()
    for module in root.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
                found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_agent_tool_contract_still_declares_exactly_ten_operations() -> None:
    declared = declared_operations()
    assert declared == AGENT_OPERATIONS, (
        "the agent tool surface changed. It is hash-pinned and settlement is not on it; adding an "
        f"operation is a decision, not a refactor. Difference: {declared ^ AGENT_OPERATIONS}"
    )


def test_no_declared_agent_operation_settles_anything() -> None:
    """Named separately from the count, so growing the set cannot smuggle one in."""
    settling = {
        operation
        for operation in declared_operations()
        if any(word in operation.lower() for word in ("settle", "payment", "paid", "balance"))
    }
    assert not settling, f"an agent operation names a money-moving act: {sorted(settling)}"


def test_the_agent_facing_application_cannot_reach_the_settlement_repository() -> None:
    """The Tool Facade is the agent's entire surface. It must not import settlement at all."""
    imports = imported_modules(AGENT_APP)
    reachable = {name for name in imports if any(part in name for part in SETTLEMENT_MODULES)}
    assert not reachable, (
        f"the agent tool application imports settlement code: {sorted(reachable)}. A balance is "
        "written by a staff member at a counter, never by a model."
    )


def test_the_agent_runtime_worker_cannot_reach_the_settlement_repository() -> None:
    """The worker runs the agent cycle, so it is the other side of the same boundary."""
    imports = imported_modules(WORKER_APP)
    reachable = {name for name in imports if any(part in name for part in SETTLEMENT_MODULES)}
    assert not reachable, (
        f"the agent worker imports settlement code: {sorted(reachable)}. Settlement has no "
        "automated caller, and an agent run must not be able to acquire one."
    )


def test_the_settlement_route_is_a_staff_route_behind_an_operations_role() -> None:
    """Read from the built app rather than the source, so a decorator change cannot slip past."""
    from nha_trang_laundry_api.main import app, require_operations_staff

    routes: list[Any] = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/internal/v1/orders/{order_id}/settlement"
    ]
    assert len(routes) == 1, "the settlement route must exist exactly once"
    route = routes[0]
    assert route.methods == {"POST"}
    assert str(route.path).startswith("/internal/"), "settlement is not a public surface"
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert require_operations_staff in dependencies, (
        "the settlement route must require an operations role with MFA"
    )
