"""No model may say where a customer came from. Asserted, because the packet says not to assume it.

`ACQUISITION-ATTRIBUTION-001` records one fact per order: what the customer said when a staff
member asked how they found the shop. That fact is **reported speech**, and its only value comes
from having been asked. A model that inferred it -- from a message body, from a channel, from the
time of day -- would produce a column that looks exactly like the attested one and means nothing,
and the shop would spend money against it without ever being able to tell the difference.

That is a stronger reason for a boundary than most: a wrong balance is discovered when the till is
counted, but a fabricated attribution is never discovered at all. So the boundary is checked
mechanically here rather than trusted, in the same shape as
`test_settlement_is_unreachable_by_agents.py`:

  * the hash-pinned agent tool contract mentions no acquisition source anywhere, in any operation,
    parameter or schema;
  * neither the agent-facing Tool Facade nor the agent runtime worker names it in their source;
  * the route that writes it is a staff route behind an operations role with MFA.

These read the contract and the source rather than exercising a request, because the claim is about
what does not exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "specs/contracts/agent-tools-v1.openapi.yaml"
AGENT_APP = ROOT / "apps/public-agent-tools/src"
WORKER_APP = ROOT / "apps/worker/src"

#: The field and the enum, as they are spelled on the wire and in the database.
FORBIDDEN_NAMES = ("acquisition_source", "AcquisitionSource", "acquisitionSource")


def sources(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_the_agent_tool_contract_never_mentions_an_acquisition_source() -> None:
    """A whole-text read, not a schema walk.

    The point is that the string does not occur -- not that no *operation* declares it. A
    description, an example, or an enum buried in a component would all be routes by which a model
    learns the vocabulary and starts producing plausible values for it.
    """

    contract = CONTRACT.read_text(encoding="utf-8")
    found = [name for name in FORBIDDEN_NAMES if name in contract]
    assert not found, (
        f"the hash-pinned agent tool contract names {found}. Where a customer came from is "
        "attested by a staff member who asked; it is not a field an agent may see, fill or repeat."
    )


def test_the_agent_facing_application_never_names_it() -> None:
    offenders = {
        str(path.relative_to(ROOT)): [
            name for name in FORBIDDEN_NAMES if name in path.read_text(encoding="utf-8")
        ]
        for path in sources(AGENT_APP)
    }
    named = {path: names for path, names in offenders.items() if names}
    assert not named, (
        f"the Tool Facade names an acquisition source: {named}. The facade is the agent's entire "
        "surface, and this field is not on it."
    )


def test_the_agent_runtime_worker_never_names_it() -> None:
    """The other side of the same boundary: the worker is what actually runs a model."""

    offenders = {
        str(path.relative_to(ROOT)): [
            name for name in FORBIDDEN_NAMES if name in path.read_text(encoding="utf-8")
        ]
        for path in sources(WORKER_APP)
    }
    named = {path: names for path, names in offenders.items() if names}
    assert not named, f"the agent runtime worker names an acquisition source: {named}."


def test_the_route_that_writes_it_is_a_staff_route_behind_an_operations_role() -> None:
    """Read from the built app rather than the source, so a decorator change cannot slip past."""

    from nha_trang_laundry_api.main import OrderCreateRequest, app, require_operations_staff

    assert "acquisition_source" in OrderCreateRequest.model_fields, (
        "order creation must carry the attested source; without it the field can only ever be "
        "filled by something other than the person who asked"
    )
    assert OrderCreateRequest.model_fields["acquisition_source"].is_required(), (
        "the field must have no default. A default would let an out-of-date client record UNKNOWN "
        "silently, which is indistinguishable from a counter that did not ask and is not the same "
        "fact."
    )

    routes: list[Any] = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/internal/v1/stores/{store_id}/orders"
        and "POST" in getattr(route, "methods", set())
    ]
    assert len(routes) == 1, "the order-create route must exist exactly once"
    route = routes[0]
    assert str(route.path).startswith("/internal/"), "order creation is not a public surface"
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert require_operations_staff in dependencies, (
        "the order-create route must require an operations role with MFA"
    )
