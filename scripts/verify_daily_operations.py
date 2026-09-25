"""One shop day, clicked through in a real browser against a running stack.

Three verification scripts now sit beside each other and each proves something the others cannot:

    verify_counter_transaction.py   the whole day as API calls -- the deterministic core, no browser
    verify_console_interaction.py   the console in a browser with the API stubbed -- typing, focus,
                                    caret, and what survives a 401
    this script                     the console in a browser against a *real* API and a *real*
                                    database, signed in as a real role

The gap this one closes is the one that keeps producing defects. Both of the others pass while a
staff member cannot actually work: the API script never renders a screen, and the stubbed browser
check signs in as nobody in particular and invents every response. Writing this found two defects on
its first run, both invisible to the rest of the suite and to every contract test:

  * the quote id was displayed shortened and could not be copied, while the order form the counter
    must paste it into demands it verbatim. The seal beside it had a copy button; the id did not, so
    the one hand-off the order flow requires was half supported.
  * every RBAC-disabled control was silently re-armed. `syncNetworkAffordance` re-applies after each
    render and owned the `disabled` attribute outright, so being online cleared what `gated()` had
    set. An AUDITOR was shown a live "Tạo đơn" sitting directly above the sentence
    explaining why they may not create an order. The server refused with 403 throughout --
    they may not create an order. The server refused with 403 throughout -- authorization was never
    at risk -- but the interface was lying about it, and a third write control was not gated at all.

Neither is reachable without signing in, as a specific role, against a server that answers for real.

Run it against the demo stack, or against any host serving the console and an identity provider that
mints demo subjects::

    uv run --with playwright python scripts/verify_daily_operations.py \
        --base-url http://127.0.0.1:8100 --idp-url http://127.0.0.1:9101

**Two scenarios, because the shop sells two things.** The first is a walk-in who collects: ticket,
quote, acceptance, order, intake, production, settlement, COMPLETED. The second is the service sold
to hotels and homestays, and it exercises what the first cannot -- a delivery fee inside the quoted
total (8 kg at the 6 kg tier plus the 2-6km band, checked as 170.000 ₫), a price bound to its
fulfilment mode so an order claiming a different one is refused, a failed delivery leg followed by a
successful one, and a completion that turns on that leg rather than on self-collection.

Between them it also checks what the shop must refuse -- a customer code never issued, a seal that
does not match, a stale row version -- and what a read-only role sees.

It trades: it issues two tickets, prices two bags, takes the money twice and closes both orders.
Point it at a shop that is meant to receive real orders and it will add them.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import sys
import urllib.request

from console_recording import Recorder, add_arguments, viewport, watch_render_defects
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:8100")
parser.add_argument("--idp-url", default="http://127.0.0.1:9101")
parser.add_argument("--store-id", default="11111111-2222-4333-8444-555555555555")
parser.add_argument("--subject", default="demo-owner")
parser.add_argument("--auditor-subject", default="demo-auditor")
parser.add_argument("--shots", default="", help="directory for screenshots; omitted means none")
add_arguments(parser)
arguments = parser.parse_args()

BASE = arguments.base_url.rstrip("/")
CONSOLE = f"{BASE}/staff/"
IDP = arguments.idp_url.rstrip("/")
STORE = arguments.store_id
SHOTS = pathlib.Path(arguments.shots) if arguments.shots else None
if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)

PASS: list[str] = []
FAIL: list[str] = []
REC = Recorder(arguments.video, arguments.slow_mo, "ca-ngay-cua-hang")


def ok(name: str, cond: bool, detail: object = "") -> None:
    (PASS if cond else FAIL).append(name)
    REC.check(name, bool(cond))
    print(
        f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""), flush=True
    )


def note(text: str) -> None:
    print(f"  ··   {text}", flush=True)


def head(n: int, title: str) -> None:
    print(f"\n{'=' * 78}\n{n}. {title}\n{'=' * 78}", flush=True)
    REC.section(f"{n}. {title}")


def token(subject: str) -> str:
    with urllib.request.urlopen(f"{IDP}/token?sub={subject}") as response:
        return json.load(response)["id_token"]


def shot(page, name: str) -> None:
    """Screenshots are opt-in: this runs in CI where a filesystem write is not always wanted."""
    if SHOTS:
        page.screenshot(path=str(SHOTS / name), full_page=True)


def _browser_launch_options() -> dict[str, object]:
    """Which browser to drive, chosen the same way `verify_console_interaction.py` chooses it.

    Real Chrome by default, because the console is opened in a real browser and some of what these
    scripts catch is browser behaviour rather than DOM shape. `CONSOLE_BROWSER_CHANNEL=chromium`
    selects Playwright's bundled build; `CONSOLE_BROWSER_PATH` names a binary outright and takes
    precedence over both.

    This was added to `verify_console_interaction.py` and not to the two scripts that drive a *real*
    API, so those two raised "Chromium distribution 'chrome' is not found" in every container this
    repository is worked on in. The consequence was not a missing convenience: it is why every
    evidence record in `evidence/delivery-loop/` had to say the browser run was against a stub, and
    why the packets' "Done when" browser condition went unmet for five items. One override in one
    file is the difference between a check that exists and a check that runs.
    """

    executable = os.environ.get("CONSOLE_BROWSER_PATH", "")
    if executable:
        return {"executable_path": executable}
    channel = os.environ.get("CONSOLE_BROWSER_CHANNEL", "chrome")
    if channel == "chromium":
        return {}
    return {"channel": channel}


with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        **_browser_launch_options(),  # type: ignore[arg-type]
        **REC.launch_options(),  # type: ignore[arg-type]
    )
    ctx = browser.new_context(
        viewport=viewport(),
        permissions=["clipboard-read", "clipboard-write"],
        **REC.context_options(),  # type: ignore[arg-type]
    )
    render_defects = watch_render_defects(ctx)
    page = REC.film(ctx, ctx.new_page(), "chu-cua-hang")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    api_calls = []

    def _record(resp):
        if "/internal/v1/" in resp.url:
            try:
                body = resp.text()[:400]
            except Exception:
                body = "<unreadable>"
            api_calls.append(
                (resp.request.method, resp.status, resp.url.split("/internal")[1], body)
            )

    page.on("response", _record)
    #: What the console *sent* on each write, so the walk can show that the order was built from
    #: the server's own answers and not from anything typed or pasted.
    sent_bodies: list[tuple[str, str, str]] = []
    page.on(
        "request",
        lambda req: (
            sent_bodies.append((req.method, req.url.split("/internal")[-1], req.post_data or ""))
            if "/internal/v1/" in req.url and req.method != "GET"
            else None
        ),
    )
    page.on(
        "console",
        lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
    )

    # Everything the walk carries from one screen to the next, bound before any section runs.
    # Each of these used to be created inside the branch that filled it, so a screen that
    # misbehaved raised NameError here rather than failing its own check -- and the run that
    # matters most is exactly the run where a screen misbehaves. On the defaults the walk keeps
    # going and each dependent step fails on its own terms, which is the report this exists for.
    ticket_ref = ""
    quote_id = ""
    seal = ""
    accepted_revision = "1"
    order_id = ""
    request_bodies = []
    page.on(
        "request",
        lambda r: (
            request_bodies.append((r.url, r.post_data or ""))
            if r.method == "POST" and "/internal/v1/" in r.url
            else None
        ),
    )

    def last_request_body(fragment):
        found = [body for url, body in request_bodies if fragment in url]
        return found[-1] if found else None

    head(1, "MỞ BẢNG — what a staff member sees before signing in")
    page.goto(CONSOLE, wait_until="networkidle")
    page.wait_for_timeout(800)
    shot(page, "01-signed-out.png")
    ok("the console loads without being signed in", "Chưa đăng nhập" in page.content())
    ok(
        "and it does not silently sign anyone in",
        "Bảng vận hành không tự đăng nhập" in page.content(),
    )
    signin_button = page.locator("a.button", has_text="Tới trang đăng nhập")
    if signin_button.count():
        note(
            f"sign-in button points at {signin_button.first.get_attribute('href')} "
            "(Caddy serves this in the real stack; this local run exchanges the token directly)"
        )

    head(2, "ĐĂNG NHẬP — token exchanged for a session cookie, as the sign-in page does")
    tok = token(arguments.subject)
    result = page.evaluate(
        """async (t) => {
        const r = await fetch('/internal/v1/auth/session', {
            method: 'POST', credentials: 'include',
            headers: {'Authorization': 'Bearer ' + t}
        });
        return {status: r.status, body: await r.text()};
    }""",
        tok,
    )
    ok(
        "the API accepts the identity provider's token",
        result["status"] in (200, 201),
        f"HTTP {result['status']} {result['body'][:120]}",
    )
    # Scope the session to the store this run trades in, the way the app bar's picker does. The
    # console auto-selects only when the account is assigned to exactly ONE store
    # (`session.js:140-144`), so on a deployment with a second branch -- which this business now has
    # -- every store-scoped screen renders "Chưa chọn cửa hàng" and the counter has no controls at
    # all. The script used to depend on the single-store case without saying so, and started failing
    # the day a second store existed.
    page.evaluate("(id) => localStorage.setItem('staff_store_id', id)", STORE)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)
    shot(page, "02-signed-in.png")
    ok("the console now shows a signed-in session", "Chưa đăng nhập" not in page.content())
    body = page.content()
    ok(
        "and names the role it granted",
        "OWNER" in body or "Chủ" in body,
        "role visible in app bar",
    )

    head(3, "HÔM NAY — the owner's morning board")
    page.goto(f"{CONSOLE}#/", wait_until="networkidle")
    page.wait_for_timeout(1500)
    shot(page, "03-today.png")
    body = page.content()
    ok("the morning board renders", "screen" in body and len(body) > 2000)
    ok(
        "takings lead the screen and are named as money collected, not revenue",
        "Đã thu tại quầy" in body,
        "",
    )
    print("\n  --- what the board says ---")
    for line in (page.locator("main").first.inner_text() or "").splitlines():
        if line.strip():
            print(f"      {line.strip()[:110]}")

    def last_call(method: str, suffix: str):
        """The latest API response for a write whose path ends with `suffix`."""
        hits = [c for c in api_calls if c[0] == method and c[2].split("?")[0].endswith(suffix)]
        return hits[-1] if hits else None

    def response_json(call) -> dict:
        try:
            return json.loads(call[3]) if call else {}
        except (ValueError, TypeError):
            return {}

    def press(locator, *suffixes: str) -> list[dict]:
        """Click, and return what the server answered to each write the press caused, in order.

        The response listener above cannot read a body once the page has navigated away -- and
        the flow's last press navigates to the new order -- so the answers are awaited here.
        """
        with contextlib.ExitStack() as stack:
            waits = [
                stack.enter_context(
                    page.expect_response(
                        lambda r, sfx=sfx: (
                            r.request.method == "POST" and r.url.split("?")[0].endswith(sfx)
                        ),
                        timeout=20000,
                    )
                )
                for sfx in suffixes
            ]
            locator.click()
        answers = []
        for wait in waits:
            try:
                response = wait.value
                answers.append({"status": response.status, "json": response.json()})
            except Exception as error:  # a refusal body or a timeout: reported, not raised
                answers.append({"status": 0, "json": {}, "error": str(error)[:120]})
        return answers

    head(4, "NHẬN ĐỒ — a walk-in arrives with a bag of laundry")
    page.goto(f"{CONSOLE}#/", wait_until="networkidle")
    page.wait_for_timeout(1000)
    page.locator("a[href='#/new']").locator("visible=true").first.click()
    page.wait_for_timeout(1500)
    shot(page, "04-intake-empty.png")

    ticket_btn = page.locator("#new-walk-in")
    ok(
        "the counter can issue a ticket without typing anything about the customer",
        ticket_btn.count() == 1
        and "Khách vãng lai — phát phiếu" in (ticket_btn.inner_text() or ""),
    )
    ticket_answer, intake_answer = press(ticket_btn.first, "/counter-tickets", "/order-requests")
    try:
        page.wait_for_selector("#new-ticket", timeout=15000)
    except Exception:
        page.wait_for_timeout(1500)
    ticket = ticket_answer["json"]
    intake = intake_answer["json"]
    ticket_ref = str(ticket.get("ticket_id", ""))
    hero = (
        (page.locator("#new-ticket").inner_text() or "").strip()
        if page.locator("#new-ticket").count()
        else ""
    )
    print(f"      counter says: {hero[:120]!r}")
    ok(
        "one press issued the ticket and opened the intake for it",
        len(ticket_ref) == 36 and intake.get("contact_binding_id") == ticket_ref,
        f"ticket {ticket_ref} intake bound to {intake.get('contact_binding_id')}",
    )
    ok(
        "and the counter is told the number to say out loud, big",
        f"Phiếu {ticket.get('ticket_number')}" in hero.replace("\n", " "),
        repr(hero[:80]),
    )
    ok("and no identifier is printed for the counter to copy", ticket_ref[:8] not in hero)
    shot(page, "05-ticket-issued.png")

    head(5, "BÁO GIÁ — pricing the bag, including the 6 kg cliff")
    page.locator("#new-add-line").click()
    page.wait_for_timeout(600)
    services = page.locator("#new-picker [data-code]")
    names = [n.strip() for n in services.all_inner_texts()]
    ok("the service is picked from the published pricebook, not typed", services.count() > 10)
    print(
        f"      pricebook offers {services.count()} services: "
        + ", ".join(n.splitlines()[0][:28] for n in names[:4])
        + "…"
    )
    wash = next((i for i, n in enumerate(names) if "Giặt sấy" in n), 0)
    wash_code = services.nth(wash).get_attribute("data-code")
    services.nth(wash).click()
    page.wait_for_timeout(600)
    shot(page, "07-quote-line.png")
    ok(
        "picking the service leaves the cursor in its quantity: no tap to type the weight",
        page.evaluate("document.activeElement?.id") == "new-line-0-qty",
        page.evaluate("document.activeElement?.id"),
    )
    page.keyboard.type("5.9", delay=8)
    page.wait_for_timeout(600)
    ok(
        "typing a weight below 6 kg raises the cliff warning",
        "Gần ngưỡng 6kg" in page.content(),
        "the confirmed pricing rule is surfaced at the counter",
    )
    shot(page, "08-quote-cliff.png")

    qty = page.locator("#new-line-0-qty")
    qty.fill("")
    qty.click()
    page.keyboard.type("6.4", delay=8)
    page.wait_for_timeout(500)
    print(f"      pricing '{names[wash].splitlines()[0][:40]}' ({wash_code}) at 6.4 kg")
    (priced_answer,) = press(page.locator("#new-price"), "/quotes")
    try:
        page.wait_for_selector("#new-receipt .receipt", timeout=15000)
    except Exception:
        page.wait_for_timeout(2500)
    page.wait_for_timeout(800)
    shot(page, "09-quote-result.png")
    result_text = (
        page.locator("#new-receipt").inner_text() if page.locator("#new-receipt").count() else ""
    )
    ok(
        "the server returns a price, and the receipt shows it",
        "128.000" in result_text.replace("\u00a0", " "),
        [line_ for line_ in result_text.splitlines() if "₫" in line_][:3],
    )
    priced = priced_answer["json"]
    quote_id = str(priced.get("quote_id", ""))
    print("\n  --- the receipt as the counter reads it ---")
    for line in result_text.splitlines():
        if line.strip():
            print(f"      {line.strip()[:110]}")

    head(6, "XÁC NHẬN — where they heard of us, then one press: agreed, and the order exists")
    page.locator("#new-next").click()
    page.wait_for_timeout(900)
    ok(
        "the confirmation rests on 'Chưa biết' until somebody asks",
        page.locator("#new-source button[aria-pressed='true']").get_attribute("data-value")
        == "UNKNOWN",
    )
    page.locator("#new-source button[data-value='WALK_IN']").click()
    shot(page, "10-confirm.png")
    accept_answer, order_answer = press(page.locator("#new-confirm"), "/acceptance", "/orders")
    try:
        page.wait_for_url("**/#/orders/*", timeout=15000)
    except Exception:
        page.wait_for_timeout(2500)
    page.wait_for_timeout(1500)
    shot(page, "12-order-created.png")
    accepted = accept_answer["json"]
    created = order_answer["json"]
    order_id = str(created.get("order_id", ""))
    seal = str(accepted.get("snapshot_hash", ""))
    accepted_revision = str(accepted.get("revision", ""))
    print(f"      this walk's order: {order_id or '(not captured)'}")
    ok(
        "the customer's agreement is recorded as the final price, before the order",
        accepted.get("status") == "ACCEPTED_FINAL",
        f"r{accepted_revision} {accepted.get('status')}",
    )
    ok(
        "the order is created from the accepted quote and opens by itself",
        bool(order_id) and page.url.endswith(f"#/orders/{order_id}"),
        page.url,
    )
    order_sent = next(
        (
            json.loads(b)
            for m, u, b in reversed(sent_bodies)
            if m == "POST" and u.endswith("/orders")
        ),
        {},
    )
    ok(
        "every value the order needs came from the server's answers — nothing typed or pasted",
        order_sent.get("bound_contact_id") == ticket_ref
        and order_sent.get("quote_id") == quote_id
        and str(order_sent.get("quote_revision")) == accepted_revision
        and order_sent.get("quote_snapshot_hash") == seal
        and order_sent.get("acquisition_source") == "WALK_IN"
        and bool(order_sent.get("customer_final_quote_accepted_at")),
        json.dumps(order_sent)[:200],
    )
    if not order_id:
        for m, s_, u, b in api_calls[-8:]:
            print(f"      API {m} {u} -> {s_} {b[:160]}")

    # CONSOLE-REDESIGN-002: the walk no longer moves three dropdown axes on #/orders. It opens the
    # order's own page and presses the next step the server offers (ORDER-STEPS-001), one real
    # event per press, exactly as the counter does. What each check proves is unchanged: the order
    # is received with the operator's slot attestation, washed, paid the exact total and closed.

    def primary_step():
        node = page.locator(".action-bar--v2 button[data-step]")
        return node.first.get_attribute("data-step") if node.count() else None

    def open_order_page(oid):
        page.goto(f"{CONSOLE}#/orders/{oid}", wait_until="networkidle")
        page.wait_for_timeout(1500)

    def last_post(fragment):
        calls = [c for c in api_calls if c[0] == "POST" and fragment in c[2]]
        return calls[-1] if calls else None

    def step_control(step):
        """The page's control for `step`: in the action bar, or under "Khác"."""
        bar = page.locator(f".action-bar--v2 button[data-step={step}]")
        if bar.count():
            return bar.first
        # PAYMENT-001: "Thu tiền" that is not the big button sits on the money card.
        card = page.locator(f".order__money button[data-step={step}]")
        if card.count():
            return card.first
        more = page.locator("button[data-more-steps]")
        if more.count():
            more.first.click()
            page.wait_for_timeout(400)
        found = page.locator(f"dialog[open] button[data-step={step}]")
        return found.first if found.count() else None

    def press_step(step, label, machine="first"):
        """A composite step with nothing to attest: one press, one `POST /steps`.

        SHOP-CAPTURE-001: Bắt đầu giặt first asks "Máy nào?". `machine="first"` presses the first
        machine offered (the most recently used), `machine="skip"` presses "Bỏ qua"; either way
        one `POST /steps` follows.
        """
        control = step_control(step)
        if control is None:
            ok(label, False, f"the page offers no {step}; the primary step is {primary_step()}")
            return False
        before = len(api_calls)
        control.click()
        if step == "START_WASH":
            page.wait_for_timeout(700)
            offered = page.locator("dialog[open] button[data-machine-code]")
            ok(
                "Bắt đầu giặt asks which machine, with the machines as big buttons and Bỏ qua",
                offered.count() >= 1
                and page.locator("dialog[open] button[data-machine-skip]").count() == 1,
                f"{offered.count()} machines offered",
            )
            if machine == "skip":
                page.locator("dialog[open] button[data-machine-skip]").first.click()
            elif offered.count():
                offered.first.click()
        page.wait_for_timeout(1800)
        call = last_post("/steps")
        good = call is not None and len(api_calls) > before and 200 <= call[1] < 300
        ok(label, good, f"HTTP {call[1]} {call[3][:110]}" if call and not good else "")
        return good

    def receive(label):
        """Nhận đồ: the sheet asks for the slot, and the press is shut until it is ticked."""
        control = step_control("RECEIVE")
        if control is None:
            ok(label, False, f"the page offers no RECEIVE; the primary step is {primary_step()}")
            return False
        control.click()
        page.wait_for_timeout(500)
        submit = page.locator("#receive-submit")
        ok(
            "Nhận đồ asks for the operator's word on the slot, and is shut until it is given",
            submit.count() == 1 and submit.is_disabled(),
        )
        page.locator("#receive-slot").check()
        submit.click()
        page.wait_for_timeout(1800)
        call = last_post("/steps")
        good = (
            call is not None
            and 200 <= call[1] < 300
            and '"slot_approved":true' in (last_request_body("/steps") or "")
        )
        ok(label, good, "" if good else f"HTTP {call[1]} {call[3][:110]}" if call else "no call")
        return good

    def pay(label):
        """Thu tiền (PAYMENT-001, DEC-035): the sheet prefills what remains; record it in cash."""
        control = step_control("TAKE_PAYMENT")
        if control is None:
            ok(label, False, f"the page offers no TAKE_PAYMENT; the primary is {primary_step()}")
            return ""
        control.click()
        page.wait_for_timeout(600)
        due = page.locator("dialog[open] .money-hero__amount").first.inner_text()
        ok(
            "the sheet shows what remains, prefilled, with a deposit one tap away",
            "₫" in due and page.locator("#payment-edit").count() == 1,
            due,
        )
        page.locator("#payment-submit").click()
        page.wait_for_timeout(2000)
        call = last_post("/payments")
        ok(
            label,
            call is not None and 200 <= call[1] < 300,
            f"HTTP {call[1]} {call[3][:120]}" if call else "no payment call was made",
        )
        return (
            page.locator("dialog[open]").first.inner_text()
            if page.locator("dialog[open]").count()
            else ""
        )

    head(9, "NHẬN ĐỒ — the bag is taken in, on the order's own page")
    page.goto(f"{CONSOLE}#/orders", wait_until="networkidle")
    page.wait_for_timeout(1400)
    card_link = page.locator(f"a[href*='#/orders/{order_id}']")
    ok(
        "the order list links through to this order's own page",
        card_link.count() >= 1,
        f"{card_link.count()} links to {order_id}",
    )
    open_order_page(order_id)
    shot(page, "13-order-created.png")
    ok(
        "a new order's page offers Nhận đồ as the one next step",
        primary_step() == "RECEIVE",
        primary_step(),
    )
    receive("the bag is received and accepted, with the slot attested")
    shot(page, "14-received.png")

    head(10, "SẢN XUẤT — the machines, one press per real event")
    for step, label in [
        ("START_WASH", "in the machine"),
        ("QUALITY_CHECK", "washed and being checked"),
        ("MARK_READY", "ready at the counter"),
    ]:
        ok(f"the page offers {step} next", primary_step() == step, primary_step())
        press_step(step, f"production: {label}")
    shot(page, "15-ready.png")

    head(11, "TẤT TOÁN — the money")
    ok(
        "a ready walk-in's next step is to take the money",
        primary_step() == "TAKE_PAYMENT",
        primary_step(),
    )
    said = pay("the payment is recorded")
    shot(page, "16-settled.png")
    ok(
        "the sheet says the order is paid in full and offers the handover straight away",
        "Đã trả đủ" in said
        and page.locator("dialog[open] button[data-step=HAND_OVER]").count() == 1,
        said[:140],
    )

    head(12, "HOÀN TẤT — the bag goes to the customer and the order closes")
    handover = page.locator("dialog[open] button[data-step=HAND_OVER]")
    if handover.count():
        handover.first.click()
        page.wait_for_timeout(2000)
    call = last_post("/steps")
    ok(
        "the order reaches COMPLETED",
        call is not None and 200 <= call[1] < 300 and '"commercial":"COMPLETED"' in call[3],
        f"HTTP {call[1]} {call[3][:120]}" if call else "no call was made",
    )
    open_order_page(order_id)
    ok(
        "and its page says it is closed, with nothing left to press",
        "Đơn đã đóng" in page.locator("main").first.inner_text()
        and page.locator("button[data-step]").count() == 0,
    )
    shot(page, "17-completed.png")

    head(14, "CÁC MÀN CÒN LẠI — every other screen a staff member can open")
    for route, name in [
        ("#/approvals", "Duyệt"),
        ("#/exceptions", "Ngoại lệ"),
        ("#/incidents", "Khiếu nại"),
        ("#/shadow", "Bản nháp AI"),
        ("#/staff", "Nhân sự"),
        ("#/system", "Hệ thống"),
        ("#/gaps", "Chưa hỗ trợ"),
        ("#/assistant", "Trợ lý"),
    ]:
        page.goto(f"{CONSOLE}{route}", wait_until="networkidle")
        page.wait_for_timeout(1200)
        text = page.locator("main").first.inner_text() or ""
        broke = "Không tải được" in text or len(text.strip()) < 40
        ok(
            f"{name} ({route}) opens and says something",
            not broke,
            text.strip().splitlines()[0][:80] if text.strip() else "empty",
        )
        shot(page, f"18-{route.lstrip('#/').replace('/', '-')}.png")

    head(15, "NHỮNG LẦN PHẢI TỪ CHỐI — what must not be possible at a counter")

    # The console no longer has a form where somebody could paste a customer code, a quote id or a
    # seal: Nhận đồ carries them. What these refusals prove is the server's, so they are asked of
    # the server directly, through the same session and CSRF as the console.
    def api_post(path: str, body: dict) -> dict:
        return page.evaluate(
            """async ({path, body}) => {
            const jar = document.cookie.split('; ');
            const csrf = jar.find(c => c.startsWith('staff_csrf='))?.split('=')[1];
            const r = await fetch(path, {method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf || '',
                          'Idempotency-Key': 'daily-' + Math.random(), 'Origin': location.origin},
                body: JSON.stringify(body)});
            return {status: r.status, body: (await r.text()).slice(0, 200)};
        }""",
            {"path": path, "body": body},
        )

    # 1. An unknown customer code is refused, never quietly turned into a customer -- typed where a
    # person really would type it, under "Nhập mã thủ công".
    page.goto(f"{CONSOLE}#/new", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.locator("#new-channel-toggle").click()
    page.locator("#new-contact").fill("00000000-0000-4000-8000-000000000999")
    page.locator("#new-contact-submit").click()
    page.wait_for_timeout(1800)
    last = last_call("POST", "/order-requests")
    ok(
        "a customer code the shop never issued is refused, not created",
        bool(last) and last[1] >= 400 and "CONTACT_BINDING_UNKNOWN" in last[3],
        f"HTTP {last[1]} {last[3][:110]}" if last else "no call",
    )
    unknown_order = api_post(
        f"/internal/v1/stores/{STORE}/orders",
        {
            "bound_contact_id": "00000000-0000-4000-8000-000000000999",
            "quote_id": quote_id or "00000000-0000-4000-8000-000000000001",
            "quote_revision": 2,
            "quote_snapshot_hash": seal or ("JCS-SHA256-V1:" + "0" * 64),
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "customer_final_quote_accepted_at": "2026-09-09T02:00:00+00:00",
            "acquisition_source": "WALK_IN",
        },
    )
    ok(
        "and an order naming a customer the quote was not written for is refused too",
        unknown_order["status"] >= 400,
        f"HTTP {unknown_order['status']} {unknown_order['body'][:110]}",
    )

    # 2. The agreement this morning's order spent cannot be spent again, whatever seal is
    # offered. The quote is already CONVERTED and the single-shot guard refuses first; the seal
    # guard is proven on an OPEN quote by
    # test_an_order_citing_a_seal_the_customer_never_agreed_is_refused.
    again = api_post(
        f"/internal/v1/stores/{STORE}/orders",
        {
            "bound_contact_id": ticket_ref,
            "quote_id": quote_id,
            "quote_revision": int(accepted_revision or 2),
            "quote_snapshot_hash": "JCS-SHA256-V1:" + "b" * 64,
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "customer_final_quote_accepted_at": "2026-09-09T02:00:00+00:00",
            "acquisition_source": "WALK_IN",
        },
    )
    ok(
        "an agreement already turned into an order cannot be ordered twice, even re-sealed",
        again["status"] == 409 and "already been converted" in again["body"],
        f"HTTP {again['status']} {again['body'][:110]}",
    )

    # 3. A stale row version is refused — two staff on one order at the same time.
    stale = page.evaluate(
        """async (store) => {
        const url = `/internal/v1/stores/${store}/orders`;
        const list = await (await fetch(url, {credentials:'include'})).json();
        const o = (list.items || list)[0];
        const jar = document.cookie.split('; ');
        const csrf = jar.find(c => c.startsWith('staff_csrf='))?.split('=')[1];
        const r = await fetch(`/internal/v1/orders/${o.order_id}/intake-transition`, {
            method: 'POST', credentials: 'include',
            headers: {'Content-Type':'application/json','If-Match':'1',
                      'Idempotency-Key':'stale-'+Math.random(),
                      'X-CSRF-Token':csrf||'','Origin':location.origin},
            body: JSON.stringify({target:'ACCEPTED'})
        });
        return {status: r.status, body: (await r.text()).slice(0,140)};
    }""",
        STORE,
    )
    ok(
        "an order changed by someone else meanwhile is refused, not overwritten",
        stale["status"] >= 400,
        f"HTTP {stale['status']} {stale['body']}",
    )

    head(16, "PHÂN QUYỀN — the auditor may look and may not touch")
    # An open order for the auditor to look at, made by the owner through the same routes the
    # counter uses (a finished order has no step left to show refused).
    auditor_order = page.evaluate(
        """async (store) => {
        const jar = document.cookie.split('; ');
        const csrf = jar.find(c => c.startsWith('staff_csrf='))?.split('=')[1] || '';
        const post = async (path, body) => (await fetch(path, {method: 'POST',
            credentials: 'include', headers: {'Content-Type': 'application/json',
            'X-CSRF-Token': csrf, 'Idempotency-Key': 'daily-' + crypto.randomUUID()},
            body: JSON.stringify(body)})).json();
        const t = await post(`/internal/v1/stores/${store}/counter-tickets`, {});
        const rq = await post(`/internal/v1/stores/${store}/order-requests`,
            {contact_binding_id: t.ticket_id});
        const q = await post(`/internal/v1/stores/${store}/quotes`, {
            bound_order_request_id: rq.order_request_id, fulfillment_mode: 'SELF_DROP_SELF_COLLECT',
            lines: [{service_code: 'STANDARD_WASH_DRY', quantity: '7', unit: 'KG',
                     quantity_basis: 'STAFF_MEASUREMENT'}]});
        const a = await post(`/internal/v1/stores/${store}/quotes/${q.quote_id}/acceptance`,
            {expected_current_revision: q.revision, expected_snapshot_hash: q.snapshot_hash});
        const o = await post(`/internal/v1/stores/${store}/orders`, {
            bound_contact_id: rq.contact_binding_id, quote_id: q.quote_id,
            quote_revision: a.revision, quote_snapshot_hash: a.snapshot_hash,
            fulfillment_mode: 'SELF_DROP_SELF_COLLECT',
            customer_final_quote_accepted_at: new Date().toISOString(),
            acquisition_source: 'WALK_IN'});
        return o.order_id || '';
    }""",
        STORE,
    )
    ok("an open order exists for the auditor to look at", bool(auditor_order), auditor_order)
    actx = browser.new_context(
        viewport=viewport(),
        **REC.context_options(),  # type: ignore[arg-type]
    )
    auditor_defects = watch_render_defects(actx)
    apage = REC.film(actx, actx.new_page(), "kiem-toan-vien")
    apage.goto(CONSOLE, wait_until="networkidle")
    atok = token(arguments.auditor_subject)
    r = apage.evaluate(
        """async (t) => {
        const r = await fetch('/internal/v1/auth/session', {method:'POST', credentials:'include',
            headers:{'Authorization':'Bearer '+t}});
        return {status:r.status, body:await r.text()};
    }""",
        atok,
    )
    ok("the auditor can sign in", r["status"] == 200, r["body"][:90])
    # The auditor's context needs the same scoping as the owner's, and for the same reason: with
    # more than one assigned store nothing is auto-selected, and every store-scoped screen renders
    # "Chưa chọn cửa hàng" — which is a correct screen, and not the one this section is about.
    apage.evaluate("(id) => localStorage.setItem('staff_store_id', id)", STORE)
    apage.reload(wait_until="networkidle")
    apage.wait_for_timeout(1000)
    apage.goto(f"{CONSOLE}#/orders", wait_until="networkidle")
    apage.wait_for_timeout(1500)
    shot(apage, "19-auditor.png")
    atext = apage.locator("main").first.inner_text() or ""
    # Writes only. "Tìm theo số phiếu" (ORDER-LOOKUP-001) is the one read form on this screen,
    # and an auditor is entitled to it; it is marked `data-intent="read"` and asserted usable
    # below, so excluding it here narrows nothing the check was about.
    # CONSOLE-REDESIGN-001 + 002: the list holds no write form any more (orders are created on
    # Nhận đồ; steps live on the order page). Its one write entry is the header's "Nhận đồ",
    # which an auditor must meet disabled with the reason -- that and any other write control
    # (a submit that is not a read, or a control `gated()` marked denied) are what is checked.
    submits = apage.locator(
        "main button[type=submit]:not([data-intent='read']), main button[data-denied]"
    )
    reads = apage.locator("button[type=submit][data-intent='read']")
    print(f"      the auditor's submit buttons ({submits.count()}):")
    for i in range(submits.count()):
        b = submits.nth(i)
        print(
            f"        · {b.inner_text()!r} disabled={b.is_disabled()} "
            f"aria-disabled={b.get_attribute('aria-disabled')}"
        )
    sess = apage.evaluate("""async () => {
        const r = await fetch('/internal/v1/session', {credentials:'include'});
        return {status: r.status, body: await r.text()};
    }""")
    print(f"      what the console reads about itself: {sess}")
    hints = apage.locator("form.form p.hint").all_text_contents()
    print(
        "      gated() hints on the forms: " + str([h[:60] for h in hints if "Vai trò" in h]) + ""
    )
    write = apage.evaluate(
        """async (store) => {
        const jar = document.cookie.split('; ');
        const csrf = jar.find(c => c.startsWith('staff_csrf='))?.split('=')[1];
        const r = await fetch(`/internal/v1/stores/${store}/orders`, {
            method:'POST', credentials:'include',
            headers:{'Content-Type':'application/json','Idempotency-Key':'aud-'+Math.random(),
                     'X-CSRF-Token':csrf||'','Origin':location.origin},
            body: JSON.stringify({bound_contact_id:'00000000-0000-4000-8000-000000000001',
                quote_id:'00000000-0000-4000-8000-000000000002', quote_revision:1,
                quote_snapshot_hash:'JCS-SHA256-V1:'+'a'.repeat(64),
                fulfillment_mode:'SELF_DROP_SELF_COLLECT',
                customer_final_quote_accepted_at:'2026-09-09T02:00:00+00:00',
                acquisition_source:'WALK_IN'})});
        return {status:r.status, body:(await r.text()).slice(0,140)};
    }""",
        STORE,
    )
    ok(
        "the SERVER refuses an auditor's write regardless of what the screen offers",
        write["status"] in (401, 403),
        f"HTTP {write['status']} {write['body']}",
    )
    disabled = (
        all(submits.nth(i).is_disabled() for i in range(submits.count()))
        if submits.count()
        else True
    )
    ok(
        "the auditor is told they may not write, and the write controls are disabled",
        submits.count() > 0 and disabled,
        f"{submits.count()} write controls, all disabled={disabled}",
    )
    ok(
        "and the auditor can still look an order up by its ticket, which is a read",
        reads.count() == 1 and not reads.first.is_disabled(),
        f"{reads.count()} read control(s)",
    )
    hints = apage.locator("main p.hint").all_text_contents()
    print("      what the auditor's order screen actually says:")
    for line in atext.splitlines()[:14]:
        if line.strip():
            print(f"        | {line.strip()[:120]}")
    explained = len([h for h in hints if "Vai trò được phép" in h])
    ok(
        "and the screen says why rather than just hiding the controls",
        submits.count() > 0 and explained >= submits.count(),
        f"{explained} of {submits.count()} write controls explain the refusal",
    )
    # CONSOLE-REDESIGN-002: the order's steps live on its own page now, so that is where an auditor
    # must meet them -- visible, disabled, with the reason beside them, never hidden.
    apage.goto(f"{CONSOLE}#/orders/{auditor_order}", wait_until="networkidle")
    apage.wait_for_timeout(1600)
    shot(apage, "19b-auditor-order.png")
    steps_ = apage.locator("button[data-step]")
    live_steps = apage.locator("button[data-step]:not([disabled])")
    step_hints = [
        h
        for h in apage.locator(".action-bar--v2 p.hint").all_text_contents()
        if "Vai trò được phép" in h
    ]
    ok(
        "on an order's page the auditor sees the next step, disabled, with the reason beside it",
        steps_.count() >= 1 and live_steps.count() == 0 and len(step_hints) >= 1,
        f"{steps_.count()} step controls, {live_steps.count()} live, {len(step_hints)} reasons",
    )
    actx.close()

    head(17, "GIAO TẬN NƠI — the other half of the trade, priced and delivered")
    # The walk above is a walk-in who collects. This is the service the shop sells to hotels and
    # homestays, and it exercises what that one cannot: a delivery fee inside the quoted total, a
    # fulfilment mode the price is bound to, delivery legs, and a completion that turns on a
    # successful leg rather than on self-collection.
    page.goto(f"{CONSOLE}#/new", wait_until="networkidle")
    page.wait_for_timeout(1200)
    (d_ticket_answer,) = press(page.locator("#new-walk-in"), "/counter-tickets")
    page.wait_for_timeout(1500)
    d_ticket = str(d_ticket_answer["json"].get("ticket_id", ""))

    page.locator("#new-mode [data-value='PICKUP_AND_RETURN']").click()
    page.wait_for_timeout(500)
    distance = page.locator("#new-distance")
    ok("choosing delivery reveals the measured-distance field", distance.is_visible())
    distance.fill("3500")
    page.locator("#new-add-line").click()
    page.wait_for_timeout(500)
    d_services = page.locator("#new-picker [data-code]")
    d_names = [n.strip() for n in d_services.all_inner_texts()]
    d_wash = next((i for i, n in enumerate(d_names) if "Giặt sấy" in n), 0)
    d_services.nth(d_wash).click()
    page.wait_for_timeout(500)
    page.keyboard.type("8", delay=8)
    page.wait_for_timeout(300)
    (d_priced,) = press(page.locator("#new-price"), "/quotes")
    try:
        page.wait_for_selector("#new-receipt .receipt", timeout=15000)
    except Exception:
        page.wait_for_timeout(2500)
    page.wait_for_timeout(800)
    shot(page, "20-delivery-quote.png")
    d_text = (
        page.locator("#new-receipt").inner_text() if page.locator("#new-receipt").count() else ""
    )
    # 8 kg at the 6 kg-and-over tier is 160.000, and 3.5 km is the flat 10.000 band.
    ok(
        "the total is the service price plus the published 2-6km fee, computed by the server",
        "170.000" in d_text.replace("\u00a0", " "),
        [line_.strip() for line_ in d_text.splitlines() if "₫" in line_][:3],
    )
    d_quote_id = str(d_priced["json"].get("quote_id", ""))

    head(18, "GIÁ NÀO THÌ ĐƠN ẤY — a price computed for delivery cannot become a walk-in order")
    # The flow sends the mode the quote was priced under, read back from the quote itself
    # (READ-ENRICH-001), so the console cannot produce this mismatch any more. The server's refusal
    # is what makes that safe, so it is still proven -- directly, after the acceptance is recorded.
    d_accept = page.evaluate(
        """async ({store, quote}) => {
        const q = await (await fetch(`/internal/v1/stores/${store}/quotes/${quote}`,
            {credentials: 'include'})).json();
        return q;
    }""",
        {"store": STORE, "quote": d_quote_id},
    )

    def api_post_delivery(body: dict) -> dict:
        return page.evaluate(
            """async ({path, body}) => {
            const jar = document.cookie.split('; ');
            const csrf = jar.find(c => c.startsWith('staff_csrf='))?.split('=')[1];
            const r = await fetch(path, {method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf || '',
                          'Idempotency-Key': 'daily-' + Math.random(), 'Origin': location.origin},
                body: JSON.stringify(body)});
            const text = await r.text();
            let parsed = null;
            try { parsed = JSON.parse(text); } catch (e) { parsed = null; }
            return {status: r.status, body: text.slice(0, 300), json: parsed};
        }""",
            {"path": f"/internal/v1/stores/{STORE}/{body.pop('_route')}", "body": body},
        )

    accepted_d = api_post_delivery(
        {
            "_route": f"quotes/{d_quote_id}/acceptance",
            "expected_current_revision": d_accept.get("revision"),
            "expected_snapshot_hash": d_accept.get("snapshot_hash"),
        }
    )
    accepted_d_body = (accepted_d["json"] or {}) if accepted_d["status"] < 300 else {}
    accepted_read = page.evaluate(
        """async ({store, quote, rev}) => (await (await fetch(
            `/internal/v1/stores/${store}/quotes/${quote}?revision=${rev}`,
            {credentials: 'include'})).json())""",
        {"store": STORE, "quote": d_quote_id, "rev": accepted_d_body.get("revision", 0)},
    )
    mismatch = api_post_delivery(
        {
            "_route": "orders",
            "bound_contact_id": d_ticket,
            "quote_id": d_quote_id,
            "quote_revision": accepted_d_body.get("revision"),
            "quote_snapshot_hash": accepted_d_body.get("snapshot_hash"),
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "customer_final_quote_accepted_at": accepted_read.get("customer_accepted_at"),
            "acquisition_source": "WALK_IN",
        }
    )
    ok(
        "an order whose fulfilment mode contradicts the price it cites is refused",
        mismatch["status"] >= 400,
        f"HTTP {mismatch['status']} {mismatch['body'][:140]}",
    )
    ok(
        "the quote read names the mode it was priced under, so the console never has to ask",
        accepted_read.get("fulfillment_mode") == "PICKUP_AND_RETURN",
        accepted_read.get("fulfillment_mode"),
    )

    # And the matching order, from the screen. The acceptance above is recorded, so this is also
    # the resume path Báo giá's "Tiếp tục" takes: the flow reads the quote back (contact, intake and
    # mode from READ-ENRICH-001), lands on the confirmation, and only the order is left to press.
    page.goto("about:blank")
    page.goto(f"{CONSOLE}#/new?quote={d_quote_id}", wait_until="networkidle")
    page.wait_for_timeout(2000)
    ok(
        "a quote accepted earlier resumes at the confirmation with nothing to retype",
        page.locator("#new-confirm").count() == 1
        and (page.locator("#new-confirm").inner_text() or "").strip() == "Tạo đơn",
        page.locator("#new-confirm").inner_text()
        if page.locator("#new-confirm").count()
        else page.url,
    )
    page.locator("#new-source button[data-value='PARTNER_FRONT_DESK']").click()
    (made,) = press(page.locator("#new-confirm"), "/orders")
    try:
        page.wait_for_url("**/#/orders/*", timeout=15000)
    except Exception:
        page.wait_for_timeout(2500)
    ok(
        "and the matching order is created, with no second acceptance",
        200 <= made["status"] < 300
        and len(
            [
                m
                for m, u, _b in sent_bodies
                if m == "POST" and u.endswith(f"{d_quote_id}/acceptance")
            ]
        )
        == 1,  # the one recorded above; the flow did not send its own
        f"HTTP {made['status']} {json.dumps(made['json'])[:160]}",
    )
    delivery_id = str(made["json"].get("order_id", "")) if 200 <= made["status"] < 300 else ""

    head(
        19,
        "CHẶNG GIAO — the bag is fetched, washed, paid for, and a failed trip precedes a good one",
    )
    open_order_page(delivery_id)
    receive("delivery: the driver brings the bag in and it is accepted")

    def record_leg(step, outcome, label):
        control = step_control(step)
        if control is None:
            ok(label, False, f"the page offers no {step}; the primary step is {primary_step()}")
            return ""
        control.click()
        page.wait_for_timeout(500)
        page.locator(f"dialog[open] button[data-leg-outcome={outcome}]").first.click()
        page.wait_for_timeout(1800)
        legs = last_post("delivery-legs")
        ok(
            label,
            legs is not None and 200 <= legs[1] < 300,
            f"HTTP {legs[1]} {legs[3][:110]}" if legs else "no call was made",
        )
        said_ = (
            page.locator("dialog[open]").first.inner_text()
            if page.locator("dialog[open]").count()
            else ""
        )
        return said_

    def close_sheet():
        done = page.locator("dialog[open] button", has_text="Xong")
        if done.count():
            done.first.click()
            page.wait_for_timeout(500)

    record_leg(
        "DELIVERY_PICKUP", "SUCCEEDED", "delivery: the pickup trip is recorded, no money in it"
    )
    close_sheet()
    for step, label in [
        ("START_WASH", "washing"),
        ("QUALITY_CHECK", "checked"),
        ("MARK_READY", "ready"),
    ]:
        # The delivery bag goes in without a machine chosen: "Bỏ qua" is always allowed.
        press_step(step, f"delivery: {label}", machine="skip")

    head(20, "TIỀN TRƯỚC KHI ĐỒ RỜI TIỆM — DEC-023, then the trip that closes the order")
    ok(
        "a ready delivery's next step is the prepayment, before the laundry leaves",
        primary_step() == "TAKE_PAYMENT",
        primary_step(),
    )
    said = pay("the delivered total is settled at the counter")
    ok(
        "and the sheet says the order closes only on a successful delivery, not a pickup",
        "chuyến giao thành công" in said,
        said[:160],
    )
    close_sheet()
    main_text = page.locator("main").first.inner_text()
    ok(
        "the page now says it is paid, and no payment is offered a second time",
        "Đã thu đủ tiền" in main_text
        and page.locator("button[data-step=TAKE_PAYMENT]").count() == 0,
    )
    shot(page, "21-delivery-settled.png")
    press_step("RELEASE", "delivery: handed to the courier")
    said = record_leg(
        "DELIVERY_RETURN", "FAILED", "a failed delivery attempt is recorded rather than hidden"
    )
    ok(
        "and a failed trip does not close the order",
        "không thành công" in said and page.locator("dialog[open] button[data-step]").count() == 0,
        said[:120],
    )
    close_sheet()
    said = record_leg("DELIVERY_RETURN", "SUCCEEDED", "and so is the successful second attempt")
    closing = page.locator("dialog[open] button[data-step=COMPLETE]")
    ok("the successful trip offers to close the order right away", closing.count() == 1, said[:120])
    if closing.count():
        closing.first.click()
        page.wait_for_timeout(2000)
    call = last_post("/steps")
    ok(
        "the delivery order closes on its successful leg",
        call is not None and 200 <= call[1] < 300 and '"commercial":"COMPLETED"' in call[3],
        f"HTTP {call[1]} {call[3][:120]}" if call else "no call was made",
    )
    shot(page, "22-delivery-completed.png")

    head(21, "HIỂN THỊ — no screen printed a structure, NaN or an undefined amount")
    ok(
        "no screen opened in this walk rendered [object Object], NaN or 'undefined ₫'",
        not render_defects and not auditor_defects,
        (render_defects + auditor_defects)[:4],
    )

    print(f"\n{'=' * 78}\nRESULT: {len(PASS)} ok, {len(FAIL)} failed\n{'=' * 78}")
    for f in FAIL:
        print(f"  - {f}")
    REC.finish()
    ctx.close()
    for film in REC.save():
        print(f"  video: {film}")
    browser.close()
    if errors:
        print("\n  page errors:", errors[:5])

print()
if FAIL:
    print(f"{len(FAIL)} of {len(PASS) + len(FAIL)} checks failed.")
sys.exit(1 if FAIL else 0)
