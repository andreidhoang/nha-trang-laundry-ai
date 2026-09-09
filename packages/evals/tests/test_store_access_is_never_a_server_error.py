"""A store-membership refusal is 403 across the whole surface, never a 500.

`StoreAccessError` descends from `PermissionError`, so it descends from `OSError` and NOT from
`ValueError`. A route that catches `ValueError` and its own domain error still lets this one escape,
and FastAPI turns an uncaught exception into a 500. A 500 tells a client to retry.

It shipped twice. `create_order` returned 500 for a staff member who held the right role but had no
`staff_store_assignments` row -- the single most common condition in the system -- and
`record_settlement` did the same on the one route that moves money, found by execution on
2026-09-09: HTTP 500 from an OPERATOR with no assignment, where 403 was intended.

**The first version of this test tried to enumerate the routes that can raise it, and that cannot
be done this way.** It scanned `operations.py` function bodies for a membership marker, which finds
only the service methods that check membership *themselves*. `require_store_membership` is called
from deep inside the repositories -- `orders.py`, `approvals.py`, `assistant.py`,
`delivery_legs.py`, `intake.py` and more -- so a route like `create_order`, whose check happens
two layers down in
`OrderRepository.create`, was invisible to it. It covered six of at least nine and read as if it
covered all of them, which is worse than not having it.

So the invariant moved to where it can be stated soundly: the application answers this exception
once, for every route, whether or not the route's own body mentions it. That is what this test
pins. The per-route catches stay -- they produce the same status and document where a refusal is
expected -- but nothing depends on a future route remembering to add one.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _answer(message: str) -> tuple[int, bytes]:
    """What the application answers when a membership refusal reaches the top of a route."""

    from nha_trang_laundry_api.main import app
    from nha_trang_laundry_db.store_access import StoreAccessError
    from starlette.requests import Request
    from starlette.responses import Response

    handler = app.exception_handlers.get(StoreAccessError)
    assert handler is not None, (
        "no handler is registered for StoreAccessError, so any route that does not catch it "
        "returns 500 -- which tells the client to retry a write it was refused"
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = handler(request, StoreAccessError(message))
    assert isinstance(response, Response), "the handler must answer synchronously"
    return response.status_code, bytes(response.body)


def test_the_application_answers_a_membership_refusal_with_403_everywhere() -> None:
    from fastapi import status
    from nha_trang_laundry_api.main import AUTHORIZATION_DENIED

    code, body = _answer("not a member of that store")
    assert code == status.HTTP_403_FORBIDDEN
    assert AUTHORIZATION_DENIED.encode() in body


def test_the_refusal_says_nothing_about_whether_the_resource_exists() -> None:
    """The same opaque string as every other store-scoped refusal.

    A distinguishable message would turn the handler into an oracle for which order and store ids
    exist, which is the thing `_require_order_store_membership` is written to avoid: it returns
    quietly for a missing order so that "not yours" and "not there" are one answer.
    """

    from nha_trang_laundry_api.main import AUTHORIZATION_DENIED

    _, body = _answer("store 123 has no such order")
    assert b"123" not in body
    assert b"no such order" not in body
    assert AUTHORIZATION_DENIED.encode() in body


def test_the_routes_that_already_catch_it_still_do() -> None:
    """Belt and braces, and a record of where a refusal is expected rather than incidental.

    This is deliberately not the invariant -- the handler above is. It is a floor: if a future
    change deletes the route-level catches wholesale, that is worth noticing even though the
    behaviour would not change.
    """

    main = (ROOT / "apps/api/src/nha_trang_laundry_api/main.py").read_text(encoding="utf-8")
    assert main.count("StoreAccessError") >= 8, (
        "the routes that name a membership refusal directly have thinned out; the global handler "
        "still answers, but check that was intended"
    )
