"""Drive the staff console in a real browser that types, with the API stubbed.

Not part of the default suite, and Playwright is deliberately not a repository dependency — see
``apps/web/README.md`` on why a second package tree is not free here. Run it explicitly::

    uv run --with playwright python scripts/verify_console_interaction.py

It exists because the defects it covers are invisible to everything else in the repository, and one
of them shipped. The earlier browser verification drove the quote builder with Playwright's
``page.fill()``, which sets a field's value in one shot. A human types. Typing rebuilt the form on
every keystroke, so ``STANDARD_WASH_DRY`` entered character by character produced ``S`` — the
flagship screen was unusable by an operator, and a check that never typed certified it as working.

A source-text test cannot see focus, a caret position, or whether a form survived a 401. This can.
Every assertion here was confirmed to fail against the code as it was before the fix.
"""

from __future__ import annotations

import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

WEB = Path("/Users/danghuyhoang/Desktop/nha-trang-laundry-ai/apps/web")
PORT = 8912
STORE = "11111111-2222-4333-8444-555555555555"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'  ok  ' if ok else ' FAIL '} {name}" + (f"  — {detail}" if detail else ""))


handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
handler.log_message = lambda *a, **k: None  # type: ignore[assignment]


class Quiet(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


server = Quiet(("127.0.0.1", PORT), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

SESSION_OK = {
    "staff_user_id": "00000000-0000-4000-8000-0000000000aa",
    "roles": ["OWNER_ADMIN"],
    "mfa_verified": True,
}

state = {"authenticated": True}

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_cookies(
        [{"name": "staff_csrf", "value": "c" * 40, "url": f"http://localhost:{PORT}"}]
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route_api(route):
        url = route.request.url
        if not state["authenticated"]:
            route.fulfill(
                status=401,
                content_type="application/json",
                body=json.dumps({"detail": "invalid staff session"}),
            )
            return
        if url.endswith("/internal/v1/session"):
            body = SESSION_OK
        elif url.endswith("/internal/v1/stores"):
            body = {"store_ids": [STORE]}
        else:
            body = []
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/internal/**", route_api)
    page.goto(f"http://localhost:{PORT}/#/quotes", wait_until="networkidle")
    page.wait_for_timeout(1200)

    print("=" * 74)
    print("1. TYPING — the defect page.fill() cannot see")
    print("=" * 74)

    code = page.locator("#quote-line-0-code")
    code.click()
    page.keyboard.type("STANDARD_WASH_DRY", delay=12)
    page.wait_for_timeout(250)

    check(
        "the whole code survives being typed one character at a time",
        code.input_value() == "STANDARD_WASH_DRY",
        repr(code.input_value()),
    )
    check(
        "focus is still in the field after 17 keystrokes",
        page.evaluate("document.activeElement?.id") == "quote-line-0-code",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    # Caret preservation: correcting a character mid-string must not jump to the end.
    code.click()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowRight")
    page.keyboard.type("X", delay=12)
    page.wait_for_timeout(150)
    check(
        "a character typed mid-string lands mid-string, not at the end",
        code.input_value() == "SXTANDARD_WASH_DRY",
        repr(code.input_value()),
    )

    print()
    print("=" * 74)
    print("2. THE 6KG NOTICE — must follow the typed weight without stealing focus")
    print("=" * 74)

    qty = page.locator("#quote-line-0-qty")
    qty.click()
    page.keyboard.type("3", delay=12)
    page.wait_for_timeout(200)
    check("no cliff notice at 3 kg", "Gần ngưỡng 6kg" not in page.content())

    page.keyboard.press("Backspace")
    page.keyboard.type("5.9", delay=12)
    page.wait_for_timeout(250)
    check("the cliff notice appears at 5.9 kg", "Gần ngưỡng 6kg" in page.content())
    check(
        "and focus stayed in the quantity field while it appeared",
        page.evaluate("document.activeElement?.id") == "quote-line-0-qty",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )
    check("the typed quantity is intact", qty.input_value() == "5.9", repr(qty.input_value()))

    print()
    print("=" * 74)
    print("3. SESSION ENDS MID-FORM — the operator's work must survive")
    print("=" * 74)

    page.locator("#quote-order-request").click()
    page.keyboard.type("9f1c7c3e-2f4a-4b6d-8c1e-7a5b3d9e0f21", delay=4)
    page.wait_for_timeout(150)

    # Every subsequent call now 401s, exactly as an eight-hour idle timeout would.
    state["authenticated"] = False
    page.locator("button[type=submit]").first.click()
    page.wait_for_timeout(1200)

    body = page.content()
    check(
        "a banner announces the session ended",
        "Phiên đăng nhập đã kết thúc" in body,
    )
    check(
        "and it says the typed input was kept",
        "vẫn còn trên màn hình" in body,
    )
    typed_request = page.locator("#quote-order-request").input_value()
    check(
        "the order request the operator typed is still there",
        typed_request == "9f1c7c3e-2f4a-4b6d-8c1e-7a5b3d9e0f21",
        repr(typed_request),
    )
    check(
        "the service code is still there",
        page.locator("#quote-line-0-code").input_value() == "SXTANDARD_WASH_DRY",
    )
    check(
        "the quantity is still there",
        page.locator("#quote-line-0-qty").input_value() == "5.9",
    )
    check(
        "the app bar no longer claims the operator is signed in",
        "Chưa đăng nhập" in body,
    )

    check("no uncaught page errors throughout", not errors, "; ".join(errors[:3]))
    browser.close()

server.shutdown()
print()
print("=" * 74)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print(f"  - {name}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
