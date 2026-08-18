"""Generate the machine-readable contract for the internal HTTP surface.

The contract is derived from the **built application object**, never from the source. Grepping
decorators misses routes added by a router include and miscounts overloaded paths; enumerating
`app.routes` is the only method that reports what is actually served.

It is deliberately not `app.openapi()`. FastAPI omits any route carrying `include_in_schema=False`
from its own document, and on this application that hides four served operations — three of which
are the authentication surface (`POST /internal/v1/auth/session`, `POST /internal/v1/auth/logout`,
`GET /internal/v1/session`). A contract that documents everything except the login route would be
worse than none, because it would look complete.

For each operation the contract records the authorization dependency chain by function name. That is
the security-relevant fact a reviewer needs and the one most likely to change by accident: a route
that loses `require_operations_staff` shows up here as a diff.

`scripts/verify_contracts.py` regenerates this document in memory on every run and fails when it
differs from the committed file, so the contract cannot drift from the surface it describes and a
new route cannot land undocumented.

An earlier version of this generator silently skipped every `app.routes` entry that was not an
`APIRoute`, which made "every served operation" false in two ways an adversarial review proved
empirically: a `@app.websocket(...)` route landed completely uncontracted, and the application
already serves one `Mount` -- the `/staff` static PWA, unauthenticated -- that the contract never
mentioned. Silently discarding a served route is exactly the ungoverned surface this item exists to
close, so unknown route types are now a hard failure and mounts are disclosed in the contract
against an explicit allowlist.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from typing import Any

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
import yaml
from fastapi.routing import APIRoute

ROOT = _Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "specs/contracts/internal-api-v1.openapi.yaml"

#: `route.methods` may include HEAD alongside GET on some Starlette versions; one contract entry per
#: verb is enough and HEAD is never independently authorized. Measured on this application no
#: APIRoute carries HEAD at all and a HEAD request returns 405, so this is a guard, not a filter.
DERIVED_METHODS = frozenset({"HEAD"})

#: Served entries that are deliberately not HTTP operations. Each one is named, disclosed in the
#: contract, and justified here -- the allowlist is what keeps "unknown route type" a failure rather
#: than a silent skip.
ALLOWED_MOUNTS: dict[str, str] = {
    "/staff": (
        "Static staff PWA assets served by StaticFiles (name=staff-pwa). Not an API operation and "
        "deliberately unauthenticated: it serves the console shell, and every datum the console "
        "shows comes from an authorized /internal/v1 call made after it loads."
    ),
}

DESCRIPTION = (
    "Generated from the built FastAPI application by "
    "scripts/generate_internal_api_contract.py. It covers EVERY served operation, including "
    "those FastAPI excludes from its own schema via include_in_schema=False - on this "
    "application that is four operations, three of them the authentication surface. Do not "
    "hand-edit: scripts/verify_contracts.py regenerates this document and fails on any "
    "difference, which is what keeps a new route from landing without a contract entry. "
    "Non-operation entries the application serves - today only the /staff static mount - are "
    "listed under x-served-mounts rather than omitted; an unrecognized route type is a hard "
    "generation failure, not a silent skip."
)


class UngovernedRouteError(RuntimeError):
    """Raised when the application serves something this contract cannot describe.

    Failing here is the point. The alternative, skipping what we do not recognize, is how a served
    WebSocket or a mounted sub-application ends up outside every check while the contract still
    claims to cover everything.
    """


def _summary(route: APIRoute) -> str:
    """The endpoint's first docstring line, or its function name when it has no docstring."""
    doc = (route.endpoint.__doc__ or "").strip()
    if not doc:
        return route.name
    first = doc.splitlines()[0].strip()
    return first or route.name


def _dependencies(route: APIRoute) -> list[str]:
    """The gate chain by function name, in declaration order.

    Order is preserved rather than sorted because it is the order the gates run in, and a
    reordering is a change a reviewer should see.
    """
    names: list[str] = []
    for dependency in route.dependant.dependencies:
        call = getattr(dependency, "call", None)
        name = getattr(call, "__name__", None)
        if name is not None:
            names.append(name)
    return names


def _parameters(route: APIRoute) -> list[dict[str, Any]]:
    return [
        {
            "name": parameter.name,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        for parameter in sorted(route.dependant.path_params, key=lambda p: p.name)
    ]


def build_document(app: Any) -> dict[str, Any]:
    """Render the contract for every operation the application serves."""
    paths: dict[str, dict[str, Any]] = {}
    mounts: dict[str, str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            path = getattr(route, "path", None)
            justification = ALLOWED_MOUNTS.get(path) if isinstance(path, str) else None
            if justification is None:
                raise UngovernedRouteError(
                    f"{type(route).__name__} at {path!r} is served but this contract cannot "
                    "describe it. Add it to ALLOWED_MOUNTS with a justification, or give it an "
                    "operation entry. Skipping it silently is what this check exists to prevent."
                )
            mounts[path] = justification
            continue
        for method in sorted((route.methods or set()) - DERIVED_METHODS):
            operation: dict[str, Any] = {
                "operationId": route.name,
                "summary": _summary(route),
                "x-authorization-dependencies": _dependencies(route),
                "x-documented-by-fastapi": bool(route.include_in_schema),
                "responses": {str(route.status_code or 200): {"description": "Success response."}},
            }
            parameters = _parameters(route)
            if parameters:
                operation["parameters"] = parameters
            paths.setdefault(route.path, {})[method.lower()] = operation

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Nha Trang Laundry internal API",
            "version": "v1",
            "description": DESCRIPTION,
        },
        "paths": {path: dict(sorted(ops.items())) for path, ops in sorted(paths.items())},
        "x-served-mounts": dict(sorted(mounts.items())),
    }


def render(document: dict[str, Any]) -> str:
    """Serialize deterministically: our key order, our path order, no timestamps."""
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


def load_application() -> Any:
    from nha_trang_laundry_api.main import app

    return app


def generate() -> str:
    return render(build_document(load_application()))


def main() -> None:
    rendered = generate()
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(rendered, encoding="utf-8")
    document = yaml.safe_load(rendered)
    operations = sum(len(ops) for ops in document["paths"].values())
    undocumented = sum(
        1
        for ops in document["paths"].values()
        for operation in ops.values()
        if not operation["x-documented-by-fastapi"]
    )
    print(f"Wrote {CONTRACT_PATH.relative_to(ROOT)}")
    print(
        f"{operations} served operations across {len(document['paths'])} paths; "
        f"{undocumented} are absent from app.openapi() and invisible without this contract."
    )


if __name__ == "__main__":
    main()
