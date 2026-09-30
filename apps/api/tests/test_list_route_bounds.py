"""`PLATFORM-SECURITY-009` P8: a list route's page bounds are the route's, so a bad value is a 422.

Every repository that serves a list refuses a `limit` outside its bound with a `ValueError`. When
the route declared `limit: int = 100` with no bound, `?limit=0` or `?limit=5000` travelled all the
way to that check and came back through the route's domain-refusal mapping as **409 Conflict** with
the repository's English sentence -- a client error reported as a state conflict, which a client
retries or shows as "somebody else changed this". The route now states the same bound the
repository enforces (`Query(ge=1, le=N)`), so FastAPI answers 422 before any service is called.

The domain's own refusals still map to 409; nothing here changes `_raise_*_error`.

Two tests. The first walks the OpenAPI document and requires every `limit`/`offset` query parameter
on every route to carry both bounds, with `limit`'s maximum equal to the repository bound recorded
in `REPOSITORY_LIMITS` -- so a new list route without bounds, or one whose bound disagrees with its
repository, fails here. The second sends the out-of-range values through the real app with a
recording stand-in for the service and asserts 422 with the service never reached, and that the
bound values themselves still reach it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import app, current_principal
from nha_trang_laundry_db import assistant as assistant_repository
from nha_trang_laundry_db import customers as customer_repository
from nha_trang_laundry_db import late_deliveries as late_delivery_repository
from nha_trang_laundry_db import orders as order_repository
from nha_trang_laundry_db import pickup_reminders as reminder_repository
from nha_trang_laundry_db import recent_contacts as recent_contact_repository
from nha_trang_laundry_db import shadow_console as shadow_repository
from nha_trang_laundry_db import unclaimed as unclaimed_repository
from nha_trang_laundry_db.identity import MAX_SESSION_LIST, StaffPrincipal, StaffRole

#: Every route with a page parameter, and the bound its repository enforces. The literal 200s are
#: the repositories that state the bound inline (`if not 1 <= limit <= 200`): approvals.py,
#: quotes.py, incidents.py (both reads), range_prices.py, shadow_console.py (drafts, reviews,
#: exceptions), remedy credits. A new list route must be added here with its repository's bound.
REPOSITORY_LIMITS: dict[tuple[str, str], int] = {
    ("GET", "/internal/v1/sessions"): MAX_SESSION_LIST,
    ("GET", "/internal/v1/staff/{staff_user_id}/sessions"): MAX_SESSION_LIST,
    ("GET", "/internal/v1/stores/{store_id}/orders"): order_repository.MAX_BOARD_LIMIT,
    ("GET", "/internal/v1/approvals"): 200,
    ("GET", "/internal/v1/stores/{store_id}/range-price-reviews"): 200,
    ("GET", "/internal/v1/stores/{store_id}/quotes"): 200,
    # intake.py: `if not 1 <= limit <= 100`.
    ("GET", "/internal/v1/stores/{store_id}/order-requests"): 100,
    ("GET", "/internal/v1/stores/{store_id}/contacts/recent"): (
        recent_contact_repository.RECENT_CONTACT_MAX_LIMIT
    ),
    ("GET", "/internal/v1/stores/{store_id}/customers"): customer_repository.LIST_MAX_LIMIT,
    ("GET", "/internal/v1/stores/{store_id}/incidents"): 200,
    ("GET", "/internal/v1/stores/{store_id}/orders/{order_id}/incidents"): 200,
    ("GET", "/internal/v1/stores/{store_id}/remedy-credits"): 200,
    ("GET", "/internal/v1/stores/{store_id}/shadow/reviews"): 200,
    ("GET", "/internal/v1/stores/{store_id}/shadow/drafts"): 200,
    ("GET", "/internal/v1/stores/{store_id}/shadow/unknown-sends"): 200,
    ("GET", "/internal/v1/stores/{store_id}/assistant/turns"): assistant_repository.LIST_LIMIT_MAX,
    ("GET", "/internal/v1/stores/{store_id}/sla-board"): shadow_repository.SLA_BOARD_MAX_LIMIT,
    ("GET", "/internal/v1/stores/{store_id}/orders/awaiting-pickup"): (
        unclaimed_repository.LIST_MAX_LIMIT
    ),
    ("GET", "/internal/v1/stores/{store_id}/pickup-reminders"): reminder_repository.LIST_MAX_LIMIT,
    ("GET", "/internal/v1/stores/{store_id}/invoice-requests"): 200,
    ("GET", "/internal/v1/stores/{store_id}/late-deliveries"): (
        late_delivery_repository.LIST_MAX_LIMIT
    ),
}

PAGE_PARAMETER = re.compile(r"^(limit|offset)$|_(limit|offset)$")


def _page_parameters() -> list[tuple[str, str, str, dict[str, Any]]]:
    found = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                if parameter["in"] == "query" and PAGE_PARAMETER.search(parameter["name"]):
                    found.append((method.upper(), path, parameter["name"], parameter["schema"]))
    return found


def test_the_page_parameter_census_is_complete() -> None:
    """Every route with a page parameter is in the table, and nothing in the table has vanished."""

    census = {(method, path) for method, path, _, _ in _page_parameters()}
    assert census == set(REPOSITORY_LIMITS), (
        f"unlisted: {sorted(census - set(REPOSITORY_LIMITS))}; "
        f"gone: {sorted(set(REPOSITORY_LIMITS) - census)}"
    )


@pytest.mark.parametrize(
    ("method", "path", "name", "schema"),
    _page_parameters(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_page_parameter_is_bounded_at_the_route_by_its_repository_bound(
    method: str, path: str, name: str, schema: dict[str, Any]
) -> None:
    assert schema.get("type") == "integer", f"{method} {path} ?{name} is not an integer"
    if name.endswith("offset"):
        assert schema.get("minimum") == 0, f"{method} {path} ?{name} has no lower bound"
        assert "maximum" in schema, f"{method} {path} ?{name} has no upper bound"
        return
    assert schema.get("minimum") == 1, f"{method} {path} ?{name} has no lower bound of 1"
    assert schema.get("maximum") == REPOSITORY_LIMITS[(method, path)], (
        f"{method} {path} ?{name}: the route says {schema.get('maximum')}, "
        f"the repository enforces {REPOSITORY_LIMITS[(method, path)]}"
    )
    default = schema.get("default")
    assert default is None or 1 <= default <= schema["maximum"]


class _RecordingService:
    """Stands in for any service; records the call, then refuses as a repository would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            raise ValueError("stand-in service reached")

        return call


def _service_dependencies(route: APIRoute) -> list[Any]:
    return [
        dependency.call
        for dependency in route.dependant.dependencies
        if dependency.name == "service" and dependency.call is not None
    ]


def _route(method: str, path: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or ()):
            return route
    raise AssertionError(f"no route {method} {path}")


@pytest.fixture
def owner_client() -> Iterator[TestClient]:
    principal = StaffPrincipal(uuid4(), f"ps009-{uuid4().hex}", frozenset(StaffRole), True, uuid4())
    app.dependency_overrides[current_principal] = lambda: principal
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), path)


@pytest.mark.parametrize(("method", "path"), sorted(REPOSITORY_LIMITS))
def test_an_out_of_range_page_is_a_422_before_the_service_and_the_bound_itself_is_served(
    owner_client: TestClient, method: str, path: str
) -> None:
    bound = REPOSITORY_LIMITS[(method, path)]
    stand_in = _RecordingService()
    for dependency in _service_dependencies(_route(method, path)):
        app.dependency_overrides[dependency] = lambda: stand_in
    url = _concrete(path)

    for refused in (0, -1, bound + 1, 10**9):
        response = owner_client.request(method, url, params={"limit": refused})
        assert response.status_code == 422, (refused, response.status_code, response.text)
        assert stand_in.calls == [], f"?limit={refused} reached the service"

    for accepted in (1, bound):
        stand_in.calls.clear()
        owner_client.request(method, url, params={"limit": accepted})
        # By keyword where the route passes it so; the session reads pass it positionally, last.
        limits = [
            kwargs.get("limit", args[-1] if args else None) for _, args, kwargs in stand_in.calls
        ]
        assert accepted in limits, (accepted, stand_in.calls)
