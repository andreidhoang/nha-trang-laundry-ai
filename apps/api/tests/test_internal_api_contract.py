"""The served route surface and its contract may not disagree.

`AGENTS.md` makes the machine-readable contracts normative and `CLAUDE.md` says contracts win over
prose. Measured before this item, the route layer satisfied neither: 38 operations were served and
one was named anywhere in `specs/`. A route with no contract cannot be validated, cannot have an
eval case generated against it, and is still depended on by the console.

`specs/contracts/internal-api-v1.openapi.yaml` closes that, and these tests are what keep it closed.
The contract is generated from the built application, so the interesting failure is not "is the
document well-formed" but "did someone add a route and not regenerate" — which is the negative test
below.

Reading `app.routes` rather than `app.openapi()` is deliberate, for the reason
`test_staff_console_contract.py` already states in its own docstring: four routes carry
`include_in_schema=False` — `/healthz` and the whole session lifecycle — so a schema-derived
contract would omit the authentication surface while looking complete. That observation is not new
here; this module turns it into a contract rather than leaving it as a comment.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute
from nha_trang_laundry_api.main import app

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "specs/contracts/internal-api-v1.openapi.yaml"
GENERATOR = ROOT / "scripts/generate_internal_api_contract.py"


def _module(name: str, path: Path) -> Any:
    """Load a `scripts/` module by path, the way the scripts reach each other."""
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generator() -> Any:
    return _module("generate_internal_api_contract", GENERATOR)


def _bare_app() -> FastAPI:
    """A test application built the way the real one is: no docs, no redoc, no served schema.

    A default `FastAPI()` brings `/openapi.json`, `/docs` and `/redoc` as plain Starlette routes,
    which the hardened generator correctly refuses. Suppressing them here keeps these tests about
    the thing under test rather than about FastAPI's defaults.
    """
    return FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _served_operations() -> set[tuple[str, str]]:
    """Every (METHOD, path) the built application actually serves, HEAD excluded."""
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
        if method != "HEAD"
    }


def _contract_operations() -> set[tuple[str, str]]:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
    }


def test_every_served_route_has_a_contract_entry() -> None:
    """Enumerated from the app object, not grepped from source: an included router has no
    decorator in this file to find."""
    missing = _served_operations() - _contract_operations()
    assert not missing, f"served but absent from the contract: {sorted(missing)}"


def test_every_contract_entry_is_a_served_route() -> None:
    """The other direction: a contract describing a deleted route is a lie of the opposite kind."""
    stale = _contract_operations() - _served_operations()
    assert not stale, f"in the contract but no longer served: {sorted(stale)}"


def test_the_contract_covers_the_routes_fastapi_hides() -> None:
    """The four `include_in_schema=False` operations are the reason this is not `app.openapi()`.

    Three of them are the authentication surface. A contract generated from the served schema would
    omit exactly the routes whose absence matters most, and would look complete while doing it.
    """
    hidden = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and not route.include_in_schema
        for method in route.methods or ()
        if method != "HEAD"
    }
    assert hidden, "expected some routes to be excluded from the served schema"
    assert {"/internal/v1/auth/session", "/internal/v1/session"} <= {path for _, path in hidden}
    assert hidden <= _contract_operations()

    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    for method, path in hidden:
        operation = document["paths"][path][method.lower()]
        assert operation["x-documented-by-fastapi"] is False


def test_the_contract_records_the_authorization_chain() -> None:
    """A route that loses its gate should surface as a contract diff, so the gate must be in it."""
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    quotes = document["paths"]["/internal/v1/stores/{store_id}/quotes"]["post"]
    assert "require_operations_staff" in quotes["x-authorization-dependencies"]

    every_operation = [
        operation for operations in document["paths"].values() for operation in operations.values()
    ]
    assert all("x-authorization-dependencies" in operation for operation in every_operation)


def test_the_committed_contract_matches_a_fresh_generation() -> None:
    """The committed file is regenerable output, not an authored document that drifts."""
    assert CONTRACT.read_text(encoding="utf-8") == _generator().generate()


def test_a_route_added_without_regenerating_fails_the_check() -> None:
    """A new route changes the generated document.

    Stated precisely, because an adversarial review found the earlier wording overstated it: this
    proves the *generator* notices a new route. It does not invoke the check and does not assert a
    non-zero exit. `test_the_check_itself_rejects_a_diverged_surface` below is the direct proof, and
    the chain closes through `test_the_committed_contract_matches_a_fresh_generation` plus the
    unconditional call in `verify_contracts.main()`.
    """
    generator = _generator()
    before = generator.build_document(app)

    extended = _bare_app()
    for route in app.routes:
        extended.router.routes.append(route)

    @extended.get("/internal/v1/an-ungoverned-route")
    def an_ungoverned_route() -> dict[str, str]:
        """A route nobody contracted."""
        return {}

    after = generator.build_document(extended)
    assert "/internal/v1/an-ungoverned-route" not in before["paths"]
    assert "/internal/v1/an-ungoverned-route" in after["paths"]
    assert generator.render(before) != generator.render(after)


def test_generation_is_deterministic() -> None:
    """Two runs must be byte-identical, or the check would fail for reasons nobody caused."""
    generator = _generator()
    assert generator.generate() == generator.generate()


@pytest.mark.parametrize("field", ["openapi", "info", "paths"])
def test_the_contract_is_a_well_formed_openapi_document(field: str) -> None:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    assert field in document
    assert document["openapi"] == "3.1.0"


def test_the_check_itself_rejects_a_diverged_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke the real check against a surface that gained a route, and require it to raise.

    The indirect test above proves the generator sees a new route. This one proves the thing that
    actually gates the build refuses, which is a different claim and the one the item is judged on.
    """
    generator = _generator()
    extended = _bare_app()
    for route in app.routes:
        extended.router.routes.append(route)

    @extended.get("/internal/v1/a-route-nobody-contracted")
    def a_route_nobody_contracted() -> dict[str, str]:
        """Added after the contract was written."""
        return {}

    monkeypatch.setattr(generator, "load_application", lambda: extended)
    monkeypatch.setitem(sys.modules, "generate_internal_api_contract", generator)

    verify = _module("verify_contracts", ROOT / "scripts/verify_contracts.py")
    with pytest.raises(ValueError, match="does not match the served route surface"):
        verify.validate_internal_api_surface()


def test_an_unrecognized_route_type_is_a_hard_failure() -> None:
    """A served WebSocket must break generation rather than be skipped.

    An adversarial review added a real `@app.websocket(...)` route to the application and found the
    contract byte-identical and every check green: the route was served and completely ungoverned.
    Silently skipping what we cannot describe is the exact failure this item exists to prevent, so
    an unknown route type now raises.
    """
    generator = _generator()
    extended = _bare_app()
    for route in app.routes:
        extended.router.routes.append(route)

    @extended.websocket("/internal/v1/a-socket-nobody-contracted")
    async def a_socket_nobody_contracted() -> None:  # pragma: no cover - never connected
        return None

    with pytest.raises(generator.UngovernedRouteError, match="a-socket-nobody-contracted"):
        generator.build_document(extended)


def test_the_static_mount_is_disclosed_rather_than_dropped() -> None:
    """The application already serves one non-operation entry; the contract must name it.

    `/staff` is the console shell, served by StaticFiles and deliberately unauthenticated. Before
    this was disclosed the contract said it covered every served operation while omitting it.
    """
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    mounts = document["x-served-mounts"]
    assert "/staff" in mounts
    assert "unauthenticated" in mounts["/staff"]

    served_mount_paths = {
        path
        for route in app.routes
        if not isinstance(route, APIRoute)
        for path in [getattr(route, "path", None)]
        if isinstance(path, str)
    }
    assert served_mount_paths == set(mounts), (
        "every non-APIRoute the app serves must be disclosed and justified in the contract"
    )
