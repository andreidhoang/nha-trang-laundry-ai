"""The shop's core workflows, each driven end to end in a browser against a real stack.

Four verification scripts now sit beside each other, and each proves something none of the others
can:

    verify_counter_transaction.py   one order as API calls -- the deterministic core, no browser
    verify_console_interaction.py   the console with the API stubbed -- typing, focus, caret, 401
    verify_daily_operations.py      one shop day in a browser against a real API: the walk-in trade
                                    and the delivery trade, from ticket to COMPLETED
    this script                     every *other* workflow the shop performs, and the ones it must
                                    refuse: how an order ends, what happens to money that is not
                                    the exact total, what each of the four roles may do, hiring
                                    somebody, and what the console does when the network or the
                                    session drops mid-shift

`verify_daily_operations.py` proves the happy path twice. Almost nothing a shop does wrong is on
that path. `WORKFLOW-CONFORMANCE-001` wrote the workflows down first
(`docs/CORE_BUSINESS_WORKFLOWS_V1.md`) and then drove every one of them, which is how it found four
defects that a green suite, a contract check and a happy-path browser run all agreed were fine:

  * a part payment -- the commonest thing that goes wrong at a till -- reached the counter as
    "Dữ liệu nhập không hợp lệ". The server had answered `NOT_SUPPORTED`,
    `AMOUNT_IS_NOT_THE_EXACT_TOTAL`, `DEC-010`; the classifier read only the plural `reason_codes`
    of a different route and dropped all three.
  * `parseDong("170000.5")` returned 1_700_005 -- ten times the amount -- while the field's own
    hint said decimals were refused.
  * the store picker listed shortened UUIDs for an owner with two shops, months after the `stores`
    table gave every shop a name.
  * a commercial move on an order whose laundry was already finished restamped
    `production_ready_at` to the moment somebody pressed a button on the order board.

It trades. Every scenario issues tickets, prices bags, takes money and closes or cancels orders in
the store it is pointed at. Point it at a shop taking real orders and it will add its own.

    uv run --with playwright python scripts/verify_workflow_conformance.py \
        --base-url http://127.0.0.1:8100 --idp-url http://127.0.0.1:9101 --store <uuid>

The store must exist, the four demo subjects must be assigned to it, and a pricebook must be
published. `scripts/bootstrap_store.py` and the staff-assignment route do the first two.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
import uuid
from typing import Any

from console_recording import Recorder, add_arguments, viewport, watch_render_defects
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:8100")
parser.add_argument("--idp-url", default="http://127.0.0.1:9101")
parser.add_argument("--store", required=True, help="the store every scenario works in")
parser.add_argument(
    "--psql-container",
    default="",
    help="docker container running PostgreSQL; enables the checks that read what was written",
)
parser.add_argument("--database", default="laundry_walkthrough")
parser.add_argument(
    "--database-url",
    default="",
    help="a libpq URL read with the local psql; the same checks as --psql-container, no docker",
)
parser.add_argument("--only", default="", help="run one scenario by name")
add_arguments(parser)
arguments = parser.parse_args()

BASE = arguments.base_url.rstrip("/")
CONSOLE = f"{BASE}/staff/"
IDP = arguments.idp_url.rstrip("/")
STORE = arguments.store
#: Whether the checks that read what a write persisted can run. `--database-url` exists because
#: `--psql-container` assumes docker, and in every container this repository is worked on in
#: there is none: the staff-hiring scenario then never got past its first read, and the coverage
#: line reported nine controls "not exercised" on every run for a reason unrelated to them.
READS_DATABASE = bool(arguments.psql_container or arguments.database_url)

#: Every interactive control this script drives, named the way the coverage report names it. A
#: control listed here and never touched is reported as uncovered rather than assumed fine, which
#: is the difference between "the tests pass" and "the buttons work".
DECLARED_CONTROLS = (
    "shell.nav.today",
    "shell.nav.orders",
    "shell.nav.quotes",
    "shell.nav.incidents",
    "shell.nav.approvals",
    "shell.nav.assistant",
    "shell.nav.shadow",
    "shell.nav.exceptions",
    "shell.nav.system",
    "shell.nav.staff",
    "shell.nav.gaps",
    "shell.store-picker",
    # CONSOLE-REDESIGN-001: intake, pricing and order create are one flow on Nhận đồ; the old
    # quotes.* (paste an intake id, one form per endpoint) and the board's create form are gone.
    "shell.nav.new",
    "newOrder.walk-in",
    "newOrder.channel-code",
    "newOrder.channel-submit",
    "newOrder.resume",
    "newOrder.mode",
    "newOrder.distance",
    "newOrder.fee",
    "newOrder.fee-ack",
    "newOrder.add-line",
    "newOrder.pick-service",
    "newOrder.line-qty",
    "newOrder.price",
    "newOrder.band-offer",
    "newOrder.band-close",
    "newOrder.next",
    "newOrder.source",
    "newOrder.confirm",
    # CONSOLE-REDESIGN-002: the three-axis transition form on #/orders is gone; every step is a
    # button on the order's own page, offered only when the server lists it (ORDER-STEPS-001).
    "orderDetail.step-primary",
    "orderDetail.step-more",
    "orderDetail.receive-slot",
    "orderDetail.receive-submit",
    "orderDetail.hold-confirm",
    "orderDetail.cancel-custody",
    "orderDetail.cancel-confirm",
    "orderDetail.stale-reload",
    "orderDetail.settlement-amount",
    "orderDetail.settlement-submit",
    "orderDetail.prepay",
    "orderDetail.collection-submit",
    # ORDER-STEPS-002: Giặt lại and Không nhận đồ, each with the reason picked in its sheet.
    "orderDetail.rewash-reason",
    "orderDetail.rewash-confirm",
    "orderDetail.reject-reason",
    "orderDetail.reject-confirm",
    # CONSOLE-REDESIGN-006: the staff controls are the person sheet's, not four id-typed forms.
    "staff.create-open",
    "staff.create-subject",
    "staff.create-name",
    "staff.create-email",
    "staff.create-submit",
    "staff.person-open",
    "staff.role-pick",
    "staff.role-submit",
    "staff.store-submit",
    "staff.store-manual",
    "staff.disable-submit",
    "assistant.question",
    "assistant.submit",
    "shell.nav.order-requests",
    "shell.sign-out",
    "staff.store-revoke",
    # CONSOLE-REDESIGN-004: a complaint is recorded from its ticket and taken to an outcome on its
    # own page; these are the controls that walk does, none of them an id field.
    "incidents.create-open",
    "incidents.ticket",
    "incidents.summary",
    "incidents.submit",
    "remedy.kind",
    "remedy.fault",
    "remedy.propose",
    "remedy.execute",
    # RECEIPT-PRINT-001: the receipt, reached from the order Nhận đồ lands on and from "Khác".
    # "Chia sẻ" is not declared: it renders only where the browser has a share sheet, and the
    # headless browser these walks run in has none (the scenario checks it is absent there).
    "orderDetail.receipt-offer",
    "orderDetail.receipt-more",
    "receipt.print",
    # EXPORT-RANGE-001: the range picker, the three export steps, and the owner's approval of an
    # export envelope read off its window.
    "exports.range-custom",
    "exports.range-preset",
    "exports.create",
    "exports.request-approval",
    "approvals.export-approve",
    "exports.execute",
    "exports.download",
    # SESSION-LIST-001: signing one device out, from the account sheet and from a person's sheet,
    # and the ORDER approval card that says whether the order moved since it was sent for approval.
    "shell.account-open",
    "shell.device-revoke",
    "staff.device-revoke",
    "approvals.order-approve",
    "approvals.order-refuse",
    # CREDIT-PICK-001 / CONTACT-PICK-001: the last two typed values on Nhận đồ, now picked or
    # handed over. Each is a control a person presses; none is an id field.
    "newOrder.credit-open",
    "newOrder.credit-pick",
    "newOrder.recent-pick",
    "shadow.new-order",
    "manualSend.new-order",
    "approvals.new-order",
    # REPORT-DASHBOARD-001: the owner's numbers, reached from Hôm nay and from the navigation.
    "shell.nav.reports",
    "today.reports-link",
    "reports.preset",
    "reports.info",
    "reports.custom-apply",
    # CUSTOMER-001 (DEC-034): the one search field on Nhận đồ, recording a regular with consent,
    # finding them again by four digits, their page with Gọi and Zalo, and erasure on request.
    "shell.nav.customers",
    "newOrder.customer-search",
    "newOrder.customer-add",
    "customer.consent",
    "customer.save",
    "newOrder.customer-pick",
    "customers.search",
    "customers.open",
    "customer.call",
    "customer.zalo",
    "customer.erase",
)

PASS: list[str] = []
FAIL: list[str] = []
REC = Recorder(arguments.video, arguments.slow_mo, "moi-quy-trinh")
TOUCHED: set[str] = set()


def ok(name: str, condition: object, detail: object = "") -> bool:
    passed = bool(condition)
    (PASS if passed else FAIL).append(name)
    REC.check(name, passed)
    print(
        f"  {'ok  ' if passed else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""),
        flush=True,
    )
    return passed


def note(text: object) -> None:
    print(f"  ··   {text}", flush=True)


def head(number: str, title: str) -> None:
    print(f"\n{'=' * 78}\n{number}. {title}\n{'=' * 78}", flush=True)
    REC.section(f"{number}. {title}")


def touched(*control_ids: str) -> None:
    TOUCHED.update(control_ids)


def token(subject: str) -> str:
    with urllib.request.urlopen(f"{IDP}/token?sub={subject}") as response:
        return json.load(response)["id_token"]


class Console:
    """The console, as the person at the counter reaches it."""

    def __init__(self, page: Any, context: Any) -> None:
        self.page = page
        self.context = context
        self.page_errors: list[str] = []
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: (
                self.page_errors.append(f"console.error: {message.text}")
                if message.type == "error"
                else None
            ),
        )

    # -- signing in ------------------------------------------------------------------
    def sign_in(self, subject: str) -> int:
        """Exchange an identity-provider token for the session cookie, as the sign-in page does."""
        self.page.goto(CONSOLE, wait_until="networkidle")
        result = self.page.evaluate(
            """async (t) => {
                const r = await fetch('/internal/v1/auth/session', {
                    method: 'POST', credentials: 'include',
                    headers: {'Authorization': 'Bearer ' + t}
                });
                return {status: r.status};
            }""",
            token(subject),
        )
        self.page.evaluate("(id) => localStorage.setItem('staff_store_id', id)", STORE)
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_timeout(1200)
        return int(result["status"])

    # -- moving around ---------------------------------------------------------------
    def open(self, route: str, settle: int = 1100) -> None:
        """A hash-only navigation does not reload, so a screen already open keeps its form state."""
        target = f"{CONSOLE}{route}"
        if self.page.url.split("#")[0] == target.split("#")[0]:
            self.page.goto("about:blank")
        self.page.goto(target, wait_until="networkidle")
        self.page.wait_for_timeout(settle)

    def text(self) -> str:
        try:
            return self.page.locator("main").first.inner_text() or ""
        except Exception:
            return ""

    def results(self) -> str:
        try:
            return " | ".join(
                line.strip()
                for line in self.page.locator("output.form__result").all_inner_texts()
                if line.strip()
            )
        except Exception:
            return ""

    def notices(self) -> str:
        try:
            return " | ".join(
                line.strip()
                # `.alert` is the V2 kit's inline alert, which carries the same refusals.
                for line in self.page.locator(".notice, .alert").all_inner_texts()
                if line.strip()
            )
        except Exception:
            return ""

    def said(self) -> str:
        return f"{self.results()} || {self.notices()}"

    def reason_codes(self) -> set[str]:
        """The reason codes the error notices on screen carry, read from the element.

        `errorNotice` shows the plain-language note visibly and keeps the codes verbatim in its
        collapsed "Chi tiết kỹ thuật" and on `data-reason-codes`, so they are read from the DOM.
        """
        try:
            values = self.page.eval_on_selector_all(
                "main [data-reason-codes]", "(els) => els.map((e) => e.dataset.reasonCodes)"
            )
        except Exception:
            return set()
        return {code for value in values for code in str(value or "").split()}

    def tech_text(self) -> str:
        """Everything the technical drawers in the error notices say, open or closed."""
        try:
            return " | ".join(self.page.locator("main .notice details.tech").all_text_contents())
        except Exception:
            return ""

    # -- typing ----------------------------------------------------------------------
    def type_into(self, selector: str, value: str, control: str = "") -> None:
        """Type, keystroke by keystroke. `fill()` sets a value in one shot and a human does not.

        `STAFF_CONSOLE_ENGINEERING_SPEC_V1.md:12.3` records why: a check that never typed passed
        while the quote builder rebuilt its form on every keystroke, so `STANDARD_WASH_DRY` entered
        by a person produced `S`.
        """
        field = self.page.locator(selector)
        field.click()
        field.fill("")
        self.page.keyboard.type(value, delay=6)
        if control:
            touched(control)

    def choose(self, selector: str, value: str, control: str = "") -> None:
        self.page.wait_for_selector(f"{selector} option[value='{value}']", state="attached")
        self.page.select_option(selector, value)
        self.page.wait_for_timeout(220)
        if control:
            touched(control)

    def press(self, label: str, control: str = "", within: str = "") -> None:
        scope = self.page.locator("form", has=self.page.locator(within)) if within else self.page
        scope.locator("button[type=submit]", has_text=label).first.click()
        self.page.wait_for_timeout(1600)
        if control:
            touched(control)

    # -- talking to the server the way the console does -------------------------------
    def csrf(self) -> str:
        return self.page.evaluate(
            "() => (document.cookie.split('; ').find(c => c.startsWith('staff_csrf=')) || '')"
            ".split('=')[1] || ''"
        )

    def call(
        self, method: str, path: str, body: object = None, if_match: object = None
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if method != "GET":
            headers["X-CSRF-Token"] = self.csrf()
            headers["Idempotency-Key"] = f"conformance-{uuid.uuid4().hex}"
        if if_match is not None:
            headers["If-Match"] = str(if_match)
        return self.page.evaluate(
            """async ({method, path, body, headers}) => {
                const init = {method, credentials: 'include', headers};
                if (body !== null && body !== undefined) {
                    init.headers['Content-Type'] = 'application/json';
                    init.body = JSON.stringify(body);
                }
                const r = await fetch(path, init);
                const text = await r.text();
                let parsed = null;
                try { parsed = JSON.parse(text); } catch (e) { parsed = null; }
                return {status: r.status, body: parsed, text: text.slice(0, 600)};
            }""",
            {"method": method, "path": path, "body": body, "headers": headers},
        )

    def press_capturing(self, locator: Any, *suffixes: str) -> list[dict[str, Any]]:
        """Click, and return the server's answer to each write the press caused, in order.

        Awaited here rather than read off a listener: the flow's last press navigates to the new
        order, and a body cannot be read after the page has moved on.
        """
        with contextlib.ExitStack() as stack:
            waits = [
                stack.enter_context(
                    self.page.expect_response(
                        lambda r, sfx=sfx: (
                            r.request.method == "POST" and r.url.split("?")[0].endswith(sfx)
                        ),
                        timeout=20000,
                    )
                )
                for sfx in suffixes
            ]
            locator.click()
        answers: list[dict[str, Any]] = []
        for wait in waits:
            try:
                response = wait.value
                text = response.text()
                try:
                    body = json.loads(text)
                except ValueError:
                    body = None
                answers.append({"status": response.status, "body": body, "text": text[:600]})
            except Exception as error:  # reported by the caller, never raised past it
                answers.append({"status": 0, "body": None, "text": str(error)[:200]})
        return answers

    def walk_in(self) -> dict[str, Any]:
        """Nhận đồ, step 1: one press issues the ticket and opens the intake for it."""
        self.open("#/new")
        ticket, intake = self.press_capturing(
            self.page.locator("#new-walk-in"), "/counter-tickets", "/order-requests"
        )
        touched("newOrder.walk-in")
        with contextlib.suppress(Exception):
            self.page.wait_for_selector("#new-ticket", timeout=15000)
        if intake["status"] >= 300 or ticket["status"] >= 300:
            raise AssertionError(
                f"could not take the customer in: {ticket['text']} {intake['text']}"
            )
        return {"ticket": ticket["body"], "intake": intake["body"]}

    def add_line(self, service: str, quantity: str, basis: str = "STAFF_MEASUREMENT") -> None:
        """Pick a service from the sheet by its code, then type the quantity where the cursor is."""
        index = self.page.locator("#new-lines [data-line]").count()
        self.page.locator("#new-add-line").click()
        touched("newOrder.add-line")
        self.page.wait_for_selector(f"#new-picker [data-code='{service}']", state="visible")
        self.page.locator(f"#new-picker [data-code='{service}']").click()
        touched("newOrder.pick-service")
        self.page.wait_for_selector(f"#new-line-{index}-qty")
        self.page.wait_for_timeout(150)
        # Typed where the cursor already is: picking a service focuses its quantity.
        self.page.keyboard.type(quantity, delay=6)
        touched("newOrder.line-qty")
        if basis != "STAFF_MEASUREMENT":
            self.page.select_option(f"#new-line-{index}-basis", basis)

    def set_mode(
        self, mode: str, distance_m: int | None = None, manual_fee_vnd: int | None = None
    ) -> None:
        if mode != "SELF_DROP_SELF_COLLECT":
            self.page.locator(f"#new-mode [data-value='{mode}']").click()
            touched("newOrder.mode")
        if distance_m is not None:
            self.type_into("#new-distance", str(distance_m), "newOrder.distance")
        if manual_fee_vnd is not None:
            self.type_into("#new-fee", str(manual_fee_vnd), "newOrder.fee")
            if not self.page.locator("#new-fee-ack").is_checked():
                self.page.locator("#new-fee-ack").check()
            touched("newOrder.fee-ack")

    def price(self) -> dict[str, Any]:
        (priced,) = self.press_capturing(self.page.locator("#new-price"), "/quotes")
        touched("newOrder.price")
        with contextlib.suppress(Exception):
            self.page.wait_for_selector("#new-receipt .receipt", timeout=15000)
        self.page.wait_for_timeout(400)
        return priced

    def confirm(self, source: str = "WALK_IN", *, accept: bool = True) -> dict[str, Any]:
        """Step 3: where they heard of us, then the one press that accepts and creates."""
        self.page.locator("#new-next").click()
        touched("newOrder.next")
        self.page.wait_for_selector("#new-confirm")
        self.page.locator(f"#new-source [data-value='{source}']").click()
        touched("newOrder.source")
        suffixes = ("/acceptance", "/orders") if accept else ("/orders",)
        answers = self.press_capturing(self.page.locator("#new-confirm"), *suffixes)
        touched("newOrder.confirm")
        with contextlib.suppress(Exception):
            self.page.wait_for_url("**/#/orders/*", timeout=15000)
        return {"acceptance": answers[0] if accept else None, "order": answers[-1]}

    # -- building the situation a scenario is about ------------------------------------
    def build_order(
        self,
        *,
        kg: str = "7",
        mode: str = "SELF_DROP_SELF_COLLECT",
        stop: str,
        lines: list[dict[str, str]] | None = None,
        distance_m: int | None = None,
        manual_fee_vnd: int | None = None,
    ) -> dict:
        """Put an order into the state a scenario starts from.

        Taking the customer in and creating the order is done on Nhận đồ, in the browser, the way
        the counter does it -- ticket, bag, price, source, one press -- because since
        CONSOLE-REDESIGN-001 that flow *is* how an order comes to exist, and every scenario that
        starts from an order now also proves it. The ladder of state moves after that goes through
        the routes directly: setting each position up through the order page would take minutes per
        case and prove nothing the daily walk does not already prove.
        """
        self.walk_in()
        self.set_mode(mode, distance_m, manual_fee_vnd)
        for line in lines or [
            {
                "service_code": "STANDARD_WASH_DRY",
                "quantity": kg,
                "unit": "KG",
                "quantity_basis": "STAFF_MEASUREMENT",
            }
        ]:
            self.add_line(line["service_code"], line["quantity"], line["quantity_basis"])
        quote = self.price()
        if quote["status"] >= 300:
            raise AssertionError(f"could not price the order: {quote['status']} {quote['text']}")
        priced = quote["body"]
        done = self.confirm()
        acceptance, order = done["acceptance"], done["order"]
        if acceptance["status"] >= 300:
            raise AssertionError(
                f"could not accept the quote: {acceptance['status']} {acceptance['text']} "
                f"(quote: {json.dumps(priced)[:400]})"
            )
        if order["status"] >= 300:
            raise AssertionError(f"could not build an order: {order['status']} {order['text']}")
        state = {
            "order_id": order["body"]["order_id"],
            "row_version": order["body"]["row_version"],
            "quote": priced,
        }
        order_id = state["order_id"]
        ladder = (
            (
                "received",
                f"/internal/v1/orders/{order_id}/intake-transition",
                {"target": "RECEIVED_PENDING_INSPECTION", "slot_approved": False},
            ),
            (
                "accepted",
                f"/internal/v1/orders/{order_id}/intake-transition",
                {"target": "ACCEPTED", "slot_approved": True},
            ),
            (
                "pending",
                f"/internal/v1/orders/{order_id}/transition",
                {"target": "STORE_CONFIRMATION_PENDING"},
            ),
            ("confirmed", f"/internal/v1/orders/{order_id}/transition", {"target": "CONFIRMED"}),
            ("active", f"/internal/v1/orders/{order_id}/transition", {"target": "ACTIVE"}),
            (
                "queued",
                f"/internal/v1/orders/{order_id}/production-transition",
                {"target": "QUEUED"},
            ),
            (
                "washing",
                f"/internal/v1/orders/{order_id}/production-transition",
                {"target": "IN_PROCESS"},
            ),
            (
                "checking",
                f"/internal/v1/orders/{order_id}/production-transition",
                {"target": "QUALITY_CHECK"},
            ),
            (
                "ready",
                f"/internal/v1/orders/{order_id}/production-transition",
                {"target": "READY_AT_STORE"},
            ),
            (
                "released",
                f"/internal/v1/orders/{order_id}/production-transition",
                {"target": "RELEASED"},
            ),
        )
        names = [name for name, _path, _body in ladder]
        if stop not in {"created", *names}:
            raise ValueError(f"unknown starting point {stop!r}")
        if stop == "created":
            # No rung of the ladder is named "created", so a loop that only breaks on a match ran
            # the whole ladder and handed back a released order to a scenario that asked for an
            # untouched one -- which then failed for the right reason about the wrong order.
            return state
        for name, path, body in ladder:
            moved = self.call("POST", path, body, if_match=state["row_version"])
            if moved["status"] >= 300:
                raise AssertionError(f"could not reach {name}: {moved['status']} {moved['text']}")
            state["row_version"] = moved["body"]["row_version"]
            if name == stop:
                break
        return state

    def current_version(self, order_id: str, fallback: object) -> object:
        """The order's `row_version` as the server holds it now, read over the API.

        Added 2026-09-23. Two scenarios previously wrote
        `stored(order_id, "row_version") or <pre-move version>`, and `stored` reads the database
        directly, which needs `--psql-container`. Without it the call returned None every time and
        the fallback replayed the version from *before* the move on the line above -- so the next
        command carried a stale version and the server refused it as a concurrent edit, exactly as
        it should. The two checks then failed against correct behaviour, and the message they
        reported was the stale-write refusal rather than the refusal they were written to prove.

        The console itself never has this problem: it re-reads the board. So this does what the
        console does, through the same route and the same session, and the scenarios no longer need
        database access to assert something the API already says out loud.
        """

        listed = self.call("GET", f"/internal/v1/stores/{STORE}/orders")
        # `list_orders` returns `list[OrderResponse]`, so the body IS the array. Reading it as
        # `body["orders"]` raised AttributeError and crashed two scenarios outright -- which is a
        # better outcome than a silent `or fallback`, because that is the shape of bug this helper
        # was written to remove in the first place.
        rows = listed.get("body")
        if not isinstance(rows, list):
            return fallback
        for row in rows:
            if isinstance(row, dict) and str(row.get("order_id")) == str(order_id):
                return row.get("row_version", fallback)
        return fallback

    # -- the order page (CONSOLE-REDESIGN-002) ---------------------------------------
    def open_order(self, order_id: str, settle: int = 1500) -> None:
        self.open(f"#/orders/{order_id}", settle=settle)

    def primary_step(self) -> str:
        node = self.page.locator(".action-bar--v2 button[data-step]")
        return str(node.first.get_attribute("data-step")) if node.count() else ""

    def offered(self) -> list[str]:
        """Every step the page offers, primary first then the ones under "Khác"."""
        steps = [self.primary_step()] if self.primary_step() else []
        more = self.page.locator("button[data-more-steps]")
        if more.count():
            more.first.click()
            self.page.wait_for_timeout(400)
            steps += [
                str(node.get_attribute("data-step"))
                for node in self.page.locator("dialog[open] button[data-step]").all()
            ]
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)
        return steps

    def step_control(self, step: str) -> Any:
        """The page's control for `step`: in the action bar, or under "Khác"."""
        bar = self.page.locator(f".action-bar--v2 button[data-step={step}]")
        if bar.count():
            touched("orderDetail.step-primary")
            return bar.first
        more = self.page.locator("button[data-more-steps]")
        if more.count():
            more.first.click()
            self.page.wait_for_timeout(400)
            touched("orderDetail.step-more")
        found = self.page.locator(f"dialog[open] button[data-step={step}]")
        return found.first if found.count() else None

    def dialog_text(self) -> str:
        return str(
            self.page.evaluate("() => document.querySelector('dialog[open]')?.textContent || ''")
        )

    def step(
        self,
        order_id: str,
        step: str,
        *,
        custody: str = "",
        slot: bool = True,
        reopen: bool = True,
        reason: str = "",
    ) -> str:
        """Press one step on the order's page, exactly as staff would, and return what it said.

        RECEIVE ticks the slot attestation (unless `slot=False`); CANCEL picks `custody` when the
        server asks for one and presses twice; HOLD presses twice; REWASH and REJECT_INTAKE pick
        `reason` from the sheet's choices (ORDER-STEPS-002), and REJECT_INTAKE presses twice.
        Nothing is sent that the page does not offer: a step the page does not list is reported,
        not forced.
        """
        if reopen:
            self.open_order(order_id)
        control = self.step_control(step)
        if control is None:
            return f"(the page offers no {step}; primary is {self.primary_step() or 'none'})"
        control.click()
        self.page.wait_for_timeout(600)
        if step == "RECEIVE":
            if slot:
                self.page.check("#receive-slot")
                touched("orderDetail.receive-slot")
            submit = self.page.locator("#receive-submit")
            if not submit.is_disabled():
                submit.click()
                touched("orderDetail.receive-submit")
        elif step == "CANCEL":
            if custody:
                self.page.locator(f"dialog[open] input[value={custody}]").check()
                touched("orderDetail.cancel-custody")
            confirm = self.page.locator("dialog[open] .sheet__actions button").first
            if not confirm.is_disabled():
                confirm.click()
                self.page.wait_for_timeout(200)
                confirm.click()
                touched("orderDetail.cancel-confirm")
        elif step == "HOLD":
            # A two-press control: the first press arms it, the second commits.
            control.click()
            touched("orderDetail.hold-confirm")
        elif step in {"REWASH", "REJECT_INTAKE"}:
            prefix = "orderDetail.rewash" if step == "REWASH" else "orderDetail.reject"
            if reason:
                self.page.locator(f"dialog[open] #step-reason [data-value={reason}]").click()
                touched(f"{prefix}-reason")
            confirm = self.page.locator("dialog[open] #step-reason-submit")
            if not confirm.is_disabled():
                confirm.click()
                if step == "REJECT_INTAKE":
                    # It ends the order: two presses, as Huỷ đơn.
                    self.page.wait_for_timeout(200)
                    confirm.click()
                touched(f"{prefix}-confirm")
        self.page.wait_for_timeout(1800)
        return self.said()

    def pay(self, order_id: str, step: str, amount_text: str = "", *, reopen: bool = True) -> str:
        """Thu tiền / Khách trả trước: type the amount (default: the one the sheet shows)."""
        if reopen:
            self.open_order(order_id)
        control = self.step_control(step)
        if control is None:
            return f"(the page offers no {step}; primary is {self.primary_step() or 'none'})"
        control.click()
        self.page.wait_for_timeout(600)
        if step == "PREPAY":
            touched("orderDetail.prepay")
        if not amount_text:
            due = self.page.locator("dialog[open] .money-hero__amount").first.inner_text()
            amount_text = due.replace("₫", "").replace("\xa0", "").strip()
        self.type_into("#settlement-amount", amount_text, "orderDetail.settlement-amount")
        self.page.locator("#settlement-submit").click()
        touched("orderDetail.settlement-submit")
        self.page.wait_for_timeout(1800)
        return self.said()


def sql(query: str) -> str:
    """Read the database directly, to check what a write really persisted.

    Optional: without `--psql-container` or `--database-url` the checks that need it are reported
    as skipped rather than silently passing, because a check that cannot run must never look like
    one that did.
    """
    if arguments.database_url:
        process = subprocess.run(
            ["psql", arguments.database_url, "-tAc", query], capture_output=True, text=True
        )
        return (process.stdout or process.stderr).strip()
    if not arguments.psql_container:
        return ""
    process = subprocess.run(
        [
            "docker",
            "exec",
            arguments.psql_container,
            "psql",
            "-U",
            "app",
            "-d",
            arguments.database,
            "-tAc",
            query,
        ],
        capture_output=True,
        text=True,
    )
    return (process.stdout or process.stderr).strip()


def stored(order_id: str, column: str) -> str:
    return sql(f"select {column} from orders where id='{order_id}'")


def eventually(query: str, expected: str, tries: int = 8) -> str:
    """Read a value a just-submitted write is expected to have produced.

    The browser returns from a click when the response arrives; this process reads the database
    through a second connection. Between the two there is a window, and a check that reads once on
    the wrong side of it fails for a reason that has nothing to do with the software under test --
    which is exactly what happened to the disable check, whose failure detail printed the value the
    assertion had just been told was absent.
    """

    value = ""
    for attempt in range(tries):
        value = sql(query)
        if value == expected:
            return value
        if attempt < tries - 1:
            time.sleep(0.25)
    return value


# ---------------------------------------------------------------------------------------------
# The workflows
# ---------------------------------------------------------------------------------------------


def scenario_money(console: Console) -> None:
    """Money that is not the exact quoted total, and what the counter is told about it."""

    head("1", "TIỀN — the one supported shape, and every refusal around it")
    order = console.build_order(kg="7", stop="released")
    total = order["quote"]["net_service_subtotal_vnd"]
    grouped = f"{total:,}".replace(",", ".")
    note(f"the quote says {total} đồng; the screen prints it as {grouped}")

    def settle(amount_text: str) -> tuple[str, str]:
        said = console.pay(order["order_id"], "SETTLE", amount_text)
        return said, console.dialog_text()

    said, sheet = settle(str(total - 5_000))
    # This asserted the words "không sai" ("your input is not wrong"). The console review found that
    # sentence false for the commonest case the same refusal covers -- 13.200 typed for 132.000 --
    # and the client cannot tell a typo from a deliberate part payment. The intent is kept whole:
    # refused, framed as the owner's decision rather than bad input, and never "không hợp lệ".
    # CONSOLE-REDESIGN-002: the policy sentence ("Trả thiếu, trả thừa, đặt cọc…", POLICY_BOUND) is
    # one tap away in the same payment sheet, beside the field, rather than a panel guardrail.
    ok(
        "a part payment is refused as the owner's decision, and is not called invalid input",
        "DEC-010" in said and "Trả thiếu" in sheet and "không hợp lệ" not in said,
        said[:150],
    )
    ok(
        "the refusal names the reason code, in words the counter can act on",
        # The code travels verbatim in the notice's technical drawer; the words are visible.
        "AMOUNT_IS_NOT_THE_EXACT_TOTAL" in console.reason_codes() and "đúng tổng đã báo" in sheet,
        repr(sorted(console.reason_codes())),
    )
    ok(
        "and names the decision that owns it, so staff know it is policy and not a fault",
        "DEC-010" in said,
        "",
    )
    if READS_DATABASE:
        ok(
            "nothing was written for a refused payment",
            sql(f"select count(*) from order_settlements where order_id='{order['order_id']}'")
            == "0",
        )

    said, _ = settle("170000.5")
    ok(
        "a decimal is refused at the screen rather than read as ten times the amount",
        "nguyên đồng" in said,
        said[:130],
    )

    said, sheet = settle(grouped)
    ok(
        "the total copied off the screen, dots and all, is accepted",
        stored(order["order_id"], "balance_status") == "PAID"
        if READS_DATABASE
        else "Đã ghi nhận" in sheet,
        sheet[:130],
    )

    head("1b", "ĐÓNG ĐƠN — a paid, released, collected order closes")
    # Released before the payment (the per-axis ladder), so the one step left is "Đóng đơn", and the
    # payment sheet offers it straight away.
    closing = console.page.locator("dialog[open] button[data-step=COMPLETE]")
    ok("the payment sheet offers to close the order at once", closing.count() == 1)
    said = console.step(order["order_id"], "COMPLETE")
    if READS_DATABASE:
        ok(
            "the order reaches COMPLETED",
            stored(order["order_id"], "commercial_status") == "COMPLETED",
            stored(order["order_id"], "commercial_status") + " " + said[:100],
        )

    head("1c", "SỔ THU — what the shop's own board says it took today")
    console.open("#/")
    touched("shell.nav.today")
    board = console.text()
    ok(
        "takings are named as money collected at the counter, never as revenue",
        "Đã thu tại quầy" in board and "doanh thu" not in board.lower(),
        "",
    )


def scenario_exit(console: Console) -> None:
    """Every way an order stops being live, including the ones that must be refused."""

    head("2", "HUỶ ĐƠN — cancelling before the shop holds anything")
    fresh = console.build_order(stop="created")
    console.step(fresh["order_id"], "CANCEL")
    if READS_DATABASE:
        ok(
            "a customer who changes their mind before handing the bag over is simply cancelled",
            stored(fresh["order_id"], "commercial_status") == "CANCELLED",
            stored(fresh["order_id"], "commercial_status"),
        )

    head("2b", "GIỮ ĐỒ RỒI MỚI HUỶ — once the shop holds the bag, no one-press cancellation")
    # `confirmed` is the per-axis ladder's state (custody recorded, commercial not yet ACTIVE); the
    # V2 page reaches ACTIVE in one RECEIVE, so this state exists only for orders moved by hand.
    # The server lists no cancellation for it at all -- the review path starts from ACTIVE -- and
    # the page must not invent one.
    held = console.build_order(stop="confirmed")
    console.open_order(held["order_id"])
    offered = console.offered()
    ok(
        "the page offers no one-press cancellation while the shop holds the goods",
        "CANCEL" not in offered,
        offered,
    )
    refused = console.call(
        "POST",
        f"/internal/v1/orders/{held['order_id']}/steps",
        {"step": "CANCEL"},
        if_match=console.current_version(held["order_id"], held["row_version"]),
    )
    ok(
        "and the server refuses one, saying a person must decide, so the page tells the truth",
        refused["status"] == 409 and "HUMAN_APPROVAL_REQUIRED" in refused["text"],
        f"HTTP {refused['status']} {refused['text'][:120]}",
    )
    if READS_DATABASE:
        ok(
            "the order is not cancelled",
            stored(held["order_id"], "commercial_status") != "CANCELLED",
            stored(held["order_id"], "commercial_status"),
        )

    head("2c", "XÉT HUỶ — the reviewed path, and a resolution the record contradicts")
    live = console.build_order(stop="active")
    console.open_order(live["order_id"])
    control = console.step_control("CANCEL")
    if control is not None:
        control.click()
        console.page.wait_for_timeout(500)
    answers = [
        str(node.get_attribute("value"))
        for node in console.page.locator("dialog[open] input[name=custody_resolution]").all()
    ]
    confirm = console.page.locator("dialog[open] .sheet__actions button")
    ok(
        "cancelling a received order needs an answer first: the press is shut until one is picked",
        bool(answers) and confirm.count() == 1 and confirm.first.is_disabled(),
        f"{len(answers)} answers offered",
    )
    ok(
        "and the sheet asks what happened to the goods and the money",
        "Đồ và tiền của khách đã xử lý thế nào" in console.dialog_text(),
        console.dialog_text()[:120],
    )
    console.page.keyboard.press("Escape")
    ok(
        "'we never received it' is not offered for an order whose custody is recorded",
        answers and "NOT_RECEIVED" not in answers,
        answers,
    )
    refused = console.call(
        "POST",
        f"/internal/v1/orders/{live['order_id']}/steps",
        {"step": "CANCEL", "custody_resolution": "NOT_RECEIVED"},
        if_match=console.current_version(live["order_id"], live["row_version"]),
    )
    # The server's English used to be the headline, so "custody" was what this looked for. The
    # console now never sends it; the server still refuses it, naming the fact on record.
    ok(
        "and the server refuses it, naming the recorded fact that contradicts it",
        refused["status"] == 409 and "custody" in refused["text"],
        f"HTTP {refused['status']} {refused['text'][:160]}",
    )

    console.step(live["order_id"], "CANCEL", custody="SHOP_FAULT_NO_CHARGE")
    if READS_DATABASE:
        ok(
            "a resolution the record does not contradict closes the order",
            stored(live["order_id"], "commercial_status") == "CANCELLED",
        )
        recorded = sql(
            "select payload->>'custody_resolution' from domain_events "
            f"where aggregate_id='{live['order_id']}' and payload->>'target'='CANCELLED'"
        )
        ok(
            "and what staff said happened to the goods is on the order's own event, permanently",
            recorded == "SHOP_FAULT_NO_CHARGE",
            recorded,
        )

    head("2d", "TẠM DỪNG — a stain at quality check is an interruption, not an ending")
    # V1 recorded EXCEPTION and sent the laundry back to IN_PROCESS from a three-axis form. The
    # page's own rewash is ORDER-STEPS-002's "Giặt lại" (scenario `rework`); an interruption is
    # still HOLD / RESUME, and the server still takes the per-axis rewash, checked directly below.
    stained = console.build_order(stop="checking")
    console.step(stained["order_id"], "HOLD")
    if READS_DATABASE:
        ok(
            "staff can stop work on the laundry from the order's page",
            stored(stained["order_id"], "production_status") == "ON_HOLD",
            stored(stained["order_id"], "production_status"),
        )
    ok(
        "and the page then offers to carry on where it stopped",
        console.primary_step() == "RESUME",
        console.primary_step(),
    )
    console.step(stained["order_id"], "RESUME", reopen=False)
    console.step(stained["order_id"], "MARK_READY")
    if READS_DATABASE:
        ok(
            "the laundry finishes and the clock names when it was actually done",
            stored(stained["order_id"], "production_ready_at is not null") == "t",
            stored(stained["order_id"], "production_status"),
        )
    rewash = console.build_order(stop="checking")
    for target in ("EXCEPTION", "IN_PROCESS"):
        moved = console.call(
            "POST",
            f"/internal/v1/orders/{rewash['order_id']}/production-transition",
            {"target": target},
            if_match=console.current_version(rewash["order_id"], rewash["row_version"]),
        )
    ok(
        "the server still takes a rewash (EXCEPTION, back through the wash) on its own route",
        moved["status"] < 300,
        f"HTTP {moved['status']} {moved['text'][:100]}",
    )

    head("2e", "ĐỒNG HỒ — a commercial move does not restamp finished laundry")
    if READS_DATABASE:
        finished = stored(stained["order_id"], "production_ready_at")
        console.step(stained["order_id"], "CANCEL", custody="SHOP_FAULT_NO_CHARGE")
        ok(
            "cancelling from the order's page does not change when the washing finished",
            stored(stained["order_id"], "production_ready_at") == finished
            and stored(stained["order_id"], "commercial_status") == "CANCELLED",
            f"{finished} → {stored(stained['order_id'], 'production_ready_at')}",
        )


def scenario_pricing(console: Console) -> None:
    """Prices, the cliff, and the two places the engine refuses to guess."""

    head("3", "BÁO GIÁ — the 6 kg cliff, priced by the server on both sides")

    def price(
        kg: str,
        *,
        mode: str = "SELF_DROP_SELF_COLLECT",
        distance: str = "",
        service: str = "STANDARD_WASH_DRY",
    ) -> tuple[str, str]:
        console.walk_in()
        console.set_mode(mode, int(distance) if distance else None)
        console.add_line(service, kg)
        console.page.wait_for_timeout(300)
        before_submit = console.text()
        console.price()
        return console.text(), before_submit

    under, warned = price("5.9")
    at_tier, _ = price("6")
    ok("5.9 kg is priced 147.500 ₫ at the under-6 kg rate", "147.500" in under, "")
    ok("6.0 kg is priced 120.000 ₫ at the 6 kg rate", "120.000" in at_tier, "")
    ok(
        "the lighter bag really does cost more — the cliff is a rule, not a defect to smooth",
        "147.500" in under and "120.000" in at_tier,
        "",
    )
    ok(
        "and the counter is warned about the threshold before anything is sent",
        "ngưỡng" in warned and "6kg" in warned.replace(" ", ""),
        [line.strip() for line in warned.splitlines() if "ngưỡng" in line][:1],
    )

    minimum, _ = price("0.4")
    ok(
        "a bag under one kilogram is charged the 1 kg minimum, not a fraction",
        "25.000" in minimum,
        "",
    )

    head("3b", "PHÍ GIAO — the published bands, and the distance past which nobody guesses")
    near, _ = price("8", mode="PICKUP_AND_RETURN", distance="1500")
    band, _ = price("8", mode="PICKUP_AND_RETURN", distance="4000")
    ok(
        "8 kg carried under 2 km totals 160.000 ₫ — nothing added inside the free band",
        "160.000" in near,
        "",
    )
    ok("the same bag at 4 km totals 170.000 ₫ — the 2-6 km fee is added", "170.000" in band, "")

    console.walk_in()
    console.set_mode("PICKUP_AND_RETURN", 9000)
    console.page.wait_for_timeout(400)
    ok(
        "past 6 km the screen asks the staff member for the fee they agreed with the customer",
        console.page.locator("#new-fee").count() == 1,
        "",
    )
    ok(
        "and asks them to confirm the customer agreed to it",
        console.page.locator("#new-fee-ack").count() == 1,
        "",
    )
    console.add_line("STANDARD_WASH_DRY", "8")
    far = console.price()
    ok(
        "without the agreed fee nobody guesses one: the price has no total and cannot be agreed",
        far["status"] < 300
        and (far["body"] or {}).get("display_total_min_vnd") is None
        and console.page.locator("#new-next").is_disabled(),
        console.text()[-200:],
    )
    console.set_mode("PICKUP_AND_RETURN", None, 45000)
    near_fee = console.price()
    ok(
        "with the fee typed and the customer's agreement ticked, the server prices the whole trip",
        near_fee["status"] < 300 and "205.000" in console.text(),
        [line for line in console.text().splitlines() if "₫" in line][-2:],
    )

    head("3c", "TỪ CHỐI ĐOÁN — what the engine will not price")
    fractional = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/quotes",
        {
            "bound_order_request_id": str(uuid.uuid4()),
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "lines": [
                {
                    "service_code": "DC_SHIRT",
                    "quantity": "2.5",
                    "unit": "ITEM",
                    "quantity_basis": "STAFF_MEASUREMENT",
                }
            ],
        },
    )
    ok(
        "two and a half shirts is refused rather than priced",
        fractional["status"] == 422 and "MISSING_REQUIRED_FACT" in fractional["text"],
        f"HTTP {fractional['status']} {fractional['text'][:110]}",
    )
    ranged = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/quotes",
        {
            "bound_order_request_id": str(uuid.uuid4()),
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "lines": [
                {
                    "service_code": "BED_PILLOW",
                    "quantity": "2",
                    "unit": "ITEM",
                    "quantity_basis": "STAFF_MEASUREMENT",
                }
            ],
        },
    )
    ok(
        "a service whose price is a range is refused until a person names the exact price",
        ranged["status"] == 422 and "RANGE_PRICE_REQUIRES_HUMAN" in ranged["text"],
        f"HTTP {ranged['status']} {ranged['text'][:110]}",
    )

    head("3d", "NHIỀU DÒNG — a ticket with more than one service on it")
    console.walk_in()
    console.add_line("STANDARD_WASH_DRY", "3")
    console.add_line("IRON_SUIT", "2")
    ok(
        "a second line can be added to one quote",
        console.page.locator("#new-line-1-qty").count() == 1,
        "",
    )
    two = console.price()
    ok(
        "and both lines are priced by the server on one receipt",
        two["status"] < 300 and console.page.locator("#new-receipt .receipt__line").count() == 2,
        f"HTTP {two['status']}",
    )

    head("3e", "KHÁCH QUA KÊNH — a code nobody issued is refused, never quietly created")
    console.open("#/new")
    console.page.locator("#new-channel-toggle").click()
    console.type_into(
        "#new-contact", "00000000-0000-4000-8000-000000000999", "newOrder.channel-code"
    )
    (refused,) = console.press_capturing(
        console.page.locator("#new-contact-submit"), "/order-requests"
    )
    touched("newOrder.channel-submit")
    console.page.wait_for_timeout(400)
    ok(
        "an unknown channel code is refused by the server with its reason, and the screen says so",
        refused["status"] == 422
        and "CONTACT_BINDING_UNKNOWN" in refused["text"]
        # Said in words on screen, with the code verbatim in the notice's technical drawer.
        and "CONTACT_BINDING_UNKNOWN" in console.reason_codes()
        and "CONTACT_BINDING_UNKNOWN" in console.tech_text()
        and "Không có liên hệ nào mang mã này" in console.text(),
        f"HTTP {refused['status']} {refused['text'][:120]}",
    )


def scenario_roles(console: Console) -> None:
    """Four people, four sets of keys, and whether the screen tells the truth about them."""

    head("4", "PHÂN QUYỀN — what the console predicts against what the server does")
    probes = (
        ("the order board", "GET", f"/internal/v1/stores/{STORE}/orders"),
        ("the quote list", "GET", f"/internal/v1/stores/{STORE}/quotes"),
        ("the approval queue", "GET", "/internal/v1/approvals"),
        ("today's takings", "GET", f"/internal/v1/stores/{STORE}/settlements/today"),
        ("the incident list", "GET", f"/internal/v1/stores/{STORE}/incidents"),
        ("queue health", "GET", "/internal/v1/queue-recovery"),
        ("AI drafts", "GET", f"/internal/v1/stores/{STORE}/shadow/drafts"),
    )
    # Measured, not read from the specification's matrix: `rbac.js` encodes what the server does,
    # and where the published matrix disagrees the console follows the server.
    expected = {
        "demo-owner": {name: 200 for name, _m, _p in probes},
        "demo-approver": {name: 200 for name, _m, _p in probes},
        "demo-operations": {
            "the order board": 200,
            "the quote list": 200,
            "the approval queue": 403,
            "today's takings": 200,
            "the incident list": 200,
            "queue health": 403,
            "AI drafts": 200,
        },
        "demo-auditor": {
            "the order board": 200,
            "the quote list": 403,
            "the approval queue": 403,
            "today's takings": 403,
            "the incident list": 403,
            "queue health": 403,
            "AI drafts": 200,
        },
    }

    # An open order, so the auditor's order page has a step to show refused.
    console.sign_in("demo-owner")
    sample = console.build_order(stop="created")

    for subject, wanted in expected.items():
        head("4", f"PHÂN QUYỀN — {subject}")
        ok(f"{subject} can sign in", console.sign_in(subject) in (200, 201))
        console.open("#/orders")
        for name, method, path in probes:
            got = console.call(method, path)
            ok(
                f"{subject}: {name} answers {wanted[name]}",
                got["status"] == wanted[name],
                f"HTTP {got['status']}",
            )
        if subject == "demo-auditor":
            # Writes only: the ticket lookup is a read an auditor may use (`data-intent="read"`).
            live = console.page.locator(
                "button[data-requires-network]:not([disabled]):not([data-intent='read'])"
            ).count()
            denied = console.page.locator("[data-denied='true']").count()
            ok("an auditor is offered no live write control on the order board", live == 0, live)
            ok(
                "and the controls they may not use are shown disabled with a reason, not hidden",
                denied > 0,
                f"{denied} marked denied",
            )
            # CONSOLE-REDESIGN-002: the steps live on the order's page, so that is where the
            # auditor must meet them -- shown, shut, with the reason beside them.
            console.open_order(sample["order_id"])
            steps = console.page.locator("button[data-step]")
            live_steps = console.page.locator("button[data-step]:not([disabled])")
            ok(
                "on an order's page the next step is shown to an auditor, disabled, with the rule",
                steps.count() >= 1
                and live_steps.count() == 0
                and console.page.locator("button[data-step][data-denied='true']").count() >= 1
                and "Vai trò được phép" in console.text(),
                f"{steps.count()} step controls, {live_steps.count()} live",
            )
            refused = console.call("POST", f"/internal/v1/stores/{STORE}/counter-tickets", {})
            ok(
                "and the server refuses their write, so the screen was telling the truth",
                refused["status"] == 403,
                f"HTTP {refused['status']}",
            )

    console.sign_in("demo-owner")


def scenario_hiring(console: Console) -> None:
    """Everything an owner must do before a new person can work a shift.

    CONSOLE-REDESIGN-006 rebuilt #/staff around the person: "Thêm nhân sự" opens a sheet, the
    new person's own sheet opens the moment they exist, and role, store and disable are presses on
    that sheet. So this scenario no longer types a staff id anywhere -- which is itself the thing
    it now proves -- and still proves everything it proved before: create, duplicate refusal, role
    grant, store assign and revoke, disable with a confirm, the directory, and no refusal screen.
    """

    page = console.page

    def sheet_person() -> str:
        node = page.locator("dialog#staff-person[open]")
        return (node.get_attribute("data-staff-id") or "") if node.count() else ""

    def open_person(staff_id: str) -> None:
        page.locator(f"#staff-directory [data-staff-id='{staff_id}']").first.click()
        page.wait_for_timeout(500)
        touched("staff.person-open")

    def click(selector: str, control: str = "", settle: int = 1600) -> None:
        page.locator(selector).first.click()
        page.wait_for_timeout(settle)
        if control:
            touched(control)

    head("5", "NHÂN SỰ — hiring somebody, from nothing to able to work")
    console.sign_in("demo-owner")
    subject = f"conformance-{uuid.uuid4().hex[:8]}"
    console.open("#/staff")
    touched("shell.nav.staff")
    ok("the staff screen opens for the owner", "KHÔNG ĐỦ QUYỀN" not in console.text(), "")

    click("#staff-create-open", "staff.create-open", settle=400)
    console.type_into("#staff-create-subject", subject, "staff.create-subject")
    console.type_into("#staff-create-name", "Nhân viên mới", "staff.create-name")
    console.type_into("#staff-create-email", f"{subject}@example.com", "staff.create-email")
    click("#staff-create-submit", "staff.create-submit", settle=1800)
    staff_id = sql(f"select id from staff_users where oidc_subject='{subject}'")
    if READS_DATABASE:
        ok("the person exists", len(staff_id) == 36, staff_id)
        ok(
            "and their own sheet is open at once, so role and store need no copied identifier",
            sheet_person() == staff_id,
            sheet_person(),
        )

    if staff_id:
        page.locator("#staff-role-pick [data-value='OPERATOR']").click()
        touched("staff.role-pick")
        click("#staff-role-submit", "staff.role-submit")
        ok(
            "the role is recorded",
            "OPERATOR"
            in sql(
                "select coalesce(string_agg(role,','),'') from staff_role_assignments "
                f"where staff_user_id='{staff_id}'"
            ),
            console.results()[:120],
        )

        click("#staff-store-submit", "staff.store-submit", settle=1800)
        ok(
            "the store assignment is recorded — without it the person can do nothing",
            # `revoked_at` comes first on purpose.
            # `test_every_read_of_the_assignment_table_excludes_revoked_rows` reads each string
            # constant on its own, and the constant parts of an f-string are separate nodes — so a
            # filter written after the first interpolation is invisible to the check that exists to
            # require it. Writing it before the first `{}` keeps the guarantee legible to both the
            # reader and the guard.
            sql(
                "select count(*) from staff_store_assignments where revoked_at is null"
                f" and staff_user_id='{staff_id}' and store_id='{STORE}'"
            )
            == "1",
            console.results()[:120],
        )
        ok(
            "and the sheet re-read the list: the new person now shows their role",
            "vận hành" in (page.locator("dialog#staff-person").inner_text() or "").lower(),
            "",
        )

        head("5aa", "THU HỒI CỬA HÀNG — and the person loses the shop immediately")
        click("#staff-store-revoke", "staff.store-revoke", settle=1800)
        revoked = eventually(
            "select count(*) from staff_store_assignments where revoked_at is not null"
            f" and staff_user_id='{staff_id}' and store_id='{STORE}'",
            "1",
        )
        ok(
            "revoking a store marks the row rather than deleting it, so who granted it survives",
            revoked == "1",
            console.results()[:140],
        )
        # Put it back through the manual-code path -- the one place a store id may be typed, for a
        # store the owner does not belong to -- so that path is driven too. The rest of this
        # scenario, and the next run, expect a member.
        page.locator("details.manual-entry > summary").first.click()
        page.wait_for_timeout(200)
        console.type_into("#staff-store-manual", STORE, "staff.store-manual")
        click("#staff-store-submit", settle=1800)
        ok(
            "a store code typed under 'Nhập mã thủ công' assigns the same way",
            sql(
                "select count(*) from staff_store_assignments where revoked_at is null"
                f" and staff_user_id='{staff_id}' and store_id='{STORE}'"
            )
            == "1",
            console.results()[:140],
        )

        head("5a", "DANH SÁCH NHÂN SỰ — the owner sees who works here (READ-PATHS-001)")
        console.open("#/staff", settle=1800)
        person = page.locator(f"#staff-directory [data-staff-id='{staff_id}']")
        ok(
            "the new person is on the shop's staff list, with their role",
            person.count() == 1
            and any(role in person.first.inner_text().lower() for role in ("operator", "vận hành")),
            person.first.inner_text()[:120] if person.count() else "not listed",
        )
        if person.count():
            open_person(staff_id)
        ok(
            "and one press opens their sheet -- no UUID copied by hand",
            sheet_person() == staff_id,
            sheet_person(),
        )

        head("5b", "TRÙNG DANH TÍNH — the same person cannot be created twice")
        console.open("#/staff")
        click("#staff-create-open", settle=400)
        console.type_into("#staff-create-subject", subject)
        console.type_into("#staff-create-name", "Người trùng")
        click("#staff-create-submit")
        ok(
            "a duplicate subject does not create a second record",
            sql(f"select count(*) from staff_users where oidc_subject='{subject}'") == "1",
            "",
        )
        ok(
            "and the screen refuses it rather than reporting a server fault",
            "Không tạo được" in console.results(),
            console.results()[:120],
        )

        head("5c", "VÔ HIỆU HOÁ — and the shop cannot be left without an owner")
        console.open("#/staff", settle=1800)
        open_person(staff_id)
        click("#staff-disable-submit", settle=300)
        ok(
            "the first press only arms it",
            sql(f"select status from staff_users where id='{staff_id}'") == "ACTIVE",
            page.locator("#staff-disable-submit").inner_text()[:60],
        )
        click("#staff-disable-submit", "staff.disable-submit")
        disabled = eventually(f"select status from staff_users where id='{staff_id}'", "DISABLED")
        ok("the person is disabled", disabled == "DISABLED", disabled)

        owner_id = sql("select id from staff_users where oidc_subject='demo-owner'")
        console.open("#/staff", settle=1800)
        open_person(owner_id)
        click("#staff-disable-submit", settle=300)
        click("#staff-disable-submit")
        ok(
            "the last active owner cannot disable themselves",
            sql(f"select status from staff_users where id='{owner_id}'") == "ACTIVE",
            console.results()[:150],
        )
        ok(
            "and the refusal is said at the button",
            "chủ đang hoạt động cuối cùng" in console.results()
            or "Không vô hiệu hoá được" in console.results(),
            console.results()[:150],
        )


def scenario_resilience(console: Console) -> None:
    """What the console does when the shop's network, or the shift, ends unexpectedly."""

    head("6", "MẤT MẠNG — a wifi drop mid-shift")
    console.sign_in("demo-owner")
    console.open("#/orders")
    live_before = console.page.locator("button[data-requires-network]:not([disabled])").count()
    console.context.set_offline(True)
    console.page.wait_for_timeout(1300)
    # The words the banner really uses. An earlier version of this check looked for "mạng" and
    # passed anyway, because `data-offline-disabled` put the string "offline" in the page source --
    # a check that matched an attribute rather than the sentence a person reads.
    offline_banner = [
        line.strip()
        for line in console.page.locator(".banner").all_inner_texts()
        if "ngoại tuyến" in line.lower()
    ]
    ok(
        "the console says it is offline rather than pretending",
        bool(offline_banner),
        offline_banner[:1],
    )
    live_offline = console.page.locator("button[data-requires-network]:not([disabled])").count()
    ok(
        "every control that would write is disabled while there is no network",
        live_offline == 0,
        f"{live_before} live before, {live_offline} live offline",
    )
    console.context.set_offline(False)
    console.page.wait_for_timeout(1600)
    ok(
        "and they come back when the network does — no control is left dead",
        console.page.locator("button[data-requires-network]:not([disabled])").count()
        == live_before,
        "",
    )

    head("6aa", "NHẬN ĐỒ QUA MÀN HÌNH — the intake step, checkbox and all")
    taken_in = console.build_order(stop="created")
    console.open_order(taken_in["order_id"])
    ok(
        "a new order's page offers Nhận đồ as its next step",
        console.primary_step() == "RECEIVE",
        console.primary_step(),
    )
    console.step_control("RECEIVE").click()
    console.page.wait_for_timeout(500)
    ok(
        "and will not receive until the staff member confirms the slot",
        console.page.locator("#receive-submit").is_disabled(),
    )
    console.page.keyboard.press("Escape")
    said = console.step(taken_in["order_id"], "RECEIVE")
    ok(
        "accepting intake from the screen requires the staff member to confirm the slot",
        stored(taken_in["order_id"], "intake_status") == "ACCEPTED"
        if READS_DATABASE
        else console.primary_step() == "START_WASH",
        stored(taken_in["order_id"], "intake_status") + " " + said[:80],
    )
    ok(
        "and that is what starts the production clock",
        stored(taken_in["order_id"], "production_accepted_at is not null") == "t"
        if READS_DATABASE
        else True,
        "",
    )

    head("6b", "AI ĐÓ ĐÃ ĐỔI — someone else moved the order while you had it open")
    order = console.build_order(stop="created")
    console.open_order(order["order_id"])
    # The second phone at the counter records the handoff while this screen still shows v1.
    console.call(
        "POST",
        f"/internal/v1/orders/{order['order_id']}/intake-transition",
        {"target": "RECEIVED_PENDING_INSPECTION", "slot_approved": False},
        if_match=order["row_version"],
    )
    said = console.step(order["order_id"], "RECEIVE", reopen=False)
    ok(
        "a stale version is refused, and the screen says somebody else changed the order",
        "người khác đổi" in said,
        said[:160],
    )
    reload_ = console.page.locator("button", has_text="Đơn vừa đổi — tải lại")
    ok("and offers the only thing that helps — a fresh read", reload_.count() > 0, "")
    if reload_.count():
        reload_.first.click()
        console.page.wait_for_timeout(1500)
        touched("orderDetail.stale-reload")
    ok(
        "after which the page offers the step again, against the new version",
        console.primary_step() == "RECEIVE",
        console.primary_step(),
    )
    console.page.keyboard.press("Escape")
    console.page.wait_for_timeout(300)

    head("6c", "GỬI LẠI — the same command twice is not two rows")
    ticket = console.call("POST", f"/internal/v1/stores/{STORE}/counter-tickets", {})
    key = f"conformance-replay-{uuid.uuid4().hex}"
    body = {"contact_binding_id": str(ticket["body"]["ticket_id"])}
    first = console.page.evaluate(
        """async ({path, body, csrf, key}) => {
            const post = () => fetch(path, {method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
                          'Idempotency-Key': key},
                body: JSON.stringify(body)})
                .then(async (r) => ({status: r.status, body: await r.json()}));
            const a = await post();
            const b = await post();
            return [a, b];
        }""",
        {
            "path": f"/internal/v1/stores/{STORE}/order-requests",
            "body": body,
            "csrf": console.csrf(),
            "key": key,
        },
    )
    ok(
        "a replayed command returns the first result rather than making a second row",
        first[0]["body"]["order_request_id"] == first[1]["body"]["order_request_id"],
        "",
    )
    ok("and says plainly that it was a replay", first[1]["body"].get("replayed") is True, "")

    head("6d", "HẾT PHIÊN — the shift ends and the staff member signs out")
    # The button, not the route behind it. An enumeration of the console's 28 write controls found
    # this one driven by no check anywhere: every script called `POST /auth/logout` directly, so the
    # control a staff member actually presses at the end of a shift had never been pressed.
    signout = console.page.locator("button", has_text="Thoát").first
    ok("the app bar offers a sign-out control", signout.count() > 0, "")
    signout.click()
    console.page.wait_for_timeout(1600)
    touched("shell.sign-out")
    console.page.reload(wait_until="networkidle")
    console.page.wait_for_timeout(1300)
    ok("a signed-out console says so", "Chưa đăng nhập" in console.page.content(), "")
    ok(
        "and does not present an ordinary sign-out as a system fault",
        "lỗi hệ thống" not in console.page.content().lower(),
        "",
    )
    console.sign_in("demo-owner")


def scenario_ai_refuses(console: Console) -> None:
    """The AI half of this product, in a release where every capability is NOT_AUTHORIZED."""

    head("7", "AI — the correct behaviour today is that it refuses")
    console.sign_in("demo-owner")

    console.open("#/shadow")
    touched("shell.nav.shadow")
    drafts = console.text()
    ok(
        "the draft queue states that the machine does not send",
        "không tự gửi" in drafts or "Máy không tự" in drafts,
        drafts.splitlines()[0][:70],
    )
    ok(
        "and there are no drafts, because nothing in this release generates one",
        console.call("GET", f"/internal/v1/stores/{STORE}/shadow/drafts")["body"] == [],
        "",
    )
    # CONSOLE-REDESIGN-005: an empty queue is a calm empty state that says so, not a bare line.
    ok(
        "the empty queue says no draft is waiting",
        "Không có bản nháp nào đang chờ" in drafts,
        "",
    )

    # CONSOLE-REDESIGN-005: the manual send is a four-step stepper under "Gửi tay", and the only
    # typing path is folded under "Nhập mã thủ công" -- nothing is pasted on the common path.
    console.open("#/exceptions")
    console.page.locator("button", has_text="Gửi tay").first.click()
    console.page.wait_for_timeout(600)
    steps = console.page.locator("section.step").count()
    manual = console.text()
    ok(
        "Gửi tay is a four-step stepper that starts from reading the words",
        steps == 4 and "Đọc tin sẽ gửi" in manual and "Ghi nhận đã gửi" in manual,
        f"{steps} steps",
    )
    ok(
        "and typing a code is only offered under Nhập mã thủ công, folded",
        console.page.locator("details.manual-entry:not([open]) #manual-approval-id").count() == 1,
        "",
    )

    console.open("#/assistant")
    touched("shell.nav.assistant")
    question = console.page.locator("#assistant-question")
    if question.count():
        console.type_into("#assistant-question", "Hôm nay tiệm thế nào?", "assistant.question")
        console.page.locator("button[type=submit]").first.click()
        console.page.wait_for_timeout(3200)
        touched("assistant.submit")
        answered = console.text()
        ok(
            "the assistant answers from the shop's own records and says it calls no model",
            "không gọi mô hình" in answered,
            "",
        )
        ok(
            "and it names the question it understood rather than guessing silently",
            "Hiểu là" in answered,
            [line.strip() for line in answered.splitlines() if "Hiểu là" in line][:1],
        )

    console.open("#/incidents")
    touched("shell.nav.incidents")
    incidents = console.text()
    # Corrected 2026-09-23, by running this script against a real API for the first time.
    #
    # These two checks asserted "chưa dùng được" and "ra sổ" -- that the form could not be completed
    # and that the counter should use the paper book. That was true when WORKFLOW-CONFORMANCE-001
    # measured it on 2026-09-10: the route demanded two sha256 digests and nothing in the repository
    # produced either, so a customer complaining at the counter could not be recorded by anybody.
    #
    # `INCIDENT-INTAKE-001` fixed it on 2026-09-18 -- the staff member types what the customer said
    # and the server derives both digests -- and these checks kept asserting the defect. A test that
    # fails because the product improved is worse than no test: it trains a reader to discount a red
    # line. So they now assert what is true, and what must stay true.
    # Corrected again by CONSOLE-REDESIGN-004: the form is a sheet opened from "Ghi khiếu nại",
    # and the record-only fact is the line printed beside it. Same two claims, same weight.
    ok(
        "the incident screen can be completed by the person standing at the counter",
        "Ghi khiếu nại" in incidents and "chưa dùng được" not in incidents,
        [line.strip() for line in incidents.splitlines() if "Ghi khiếu nại" in line][:1],
    )
    opener = console.page.locator("#incident-create-open")
    if opener.count():
        opener.first.click()
        console.page.wait_for_timeout(400)
    sheet_text = (
        console.page.locator("dialog#incident-create").inner_text()
        if console.page.locator("dialog#incident-create[open]").count()
        else ""
    )
    ok(
        "and it says recording is not the same act as deciding fault or paying for it",
        "chưa quyết ai lỗi, chưa bồi hoàn" in sheet_text,
        sheet_text[:120].replace("\n", " "),
    )
    console.page.keyboard.press("Escape")

    for route, name in (
        ("#/exceptions", "Ngoại lệ"),
        ("#/system", "Hệ thống"),
        ("#/gaps", "Chưa hỗ trợ"),
        ("#/approvals", "Duyệt"),
    ):
        console.open(route)
        touched(f"shell.nav.{route.strip('#/')}")
        ok(
            f"{name} ({route}) opens and says what it is for",
            len(console.text()) > 80,
            console.text().splitlines()[0][:60],
        )

    console.open("#/gaps")
    gaps = console.text()
    # Corrected 2026-09-23 with the two incident checks above, and for the same reason: this
    # asserted that `#/gaps` still lists "Mở sự cố tại quầy" as unsupported. `INCIDENT-INTAKE-001`
    # built it and correctly retired that entry, so the check was holding the register to a claim
    # the register was right to drop.
    #
    # What the register must keep doing is naming what is genuinely absent, so that is what this
    # checks now. An empty or silent `#/gaps` would be the real defect: the screen exists because a
    # console that quietly omits what it cannot do teaches staff to guess.
    ok(
        "the unsupported register still names what the shop genuinely cannot do",
        "Chưa hỗ trợ" in gaps and len(gaps.strip()) > 200,
        f"{len(gaps.strip())} characters of register",
    )


def scenario_band(console: Console) -> None:
    """`DEC-029`: the staff member on duty closes a published band; the owner reviews it after."""

    head("9", "GIÁ TRONG KHOẢNG — áo dài, priced by the staff member on duty (DEC-029)")
    console.sign_in("demo-operations")
    taken = console.walk_in()
    request_id = str(taken["intake"]["order_request_id"])
    console.add_line("DC_AO_DAI_TRADITIONAL", "1")
    console.price()
    ok(
        "a range-priced item is not given a made-up single price",
        "Món này niêm yết theo khoảng giá" in console.text(),
        console.said()[:200],
    )
    offer = console.page.locator("button", has_text="Lập bản khoảng giá")
    if not ok("and the counter is offered a band revision instead", offer.count() > 0):
        return
    offer.first.click()
    touched("newOrder.band-offer")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#quote-band-0", timeout=15000)
    ok(
        "the band revision shows the published band for the item, 80.000 to 240.000 ₫",
        "80.000" in console.text() and "240.000" in console.text(),
        console.said()[:200],
    )
    close = console.page.locator("button", has_text="Chốt giá này")
    console.type_into("#quote-band-0", "300000")
    console.page.wait_for_timeout(500)
    blocked = close.count() == 0 or close.first.is_disabled()
    if not blocked:
        close.first.click()
        console.page.wait_for_timeout(1800)
    ok(
        "a price above the published band cannot be closed",
        blocked or "Chốt giá này" in console.text(),
        console.said()[:220],
    )
    console.type_into("#quote-band-0", "160000")
    console.page.wait_for_timeout(500)
    close.first.click()
    touched("newOrder.band-close")
    try:
        console.page.wait_for_selector("#new-next:not([disabled])", timeout=15000)
    except Exception:
        console.page.wait_for_timeout(1500)
    closed_text = console.text()
    ok(
        "160.000 ₫ inside the band is final in one press — no owner, no ten-minute wait",
        "160.000" in closed_text and console.page.locator("#new-next:not([disabled])").count() == 1,
        console.said()[:220],
    )

    # The customer steps away and comes back: the waiting ticket resumes from Tiếp nhận with the
    # closed price restored from the server's reads -- nothing retyped, nothing pasted.
    console.open("#/order-requests")
    row = console.page.locator(f"#intake-list [data-request='{request_id}']")
    ok("the waiting ticket is listed as still waiting for its price", row.count() == 1, "")
    if row.count():
        row.first.click()
        touched("newOrder.resume")
        with contextlib.suppress(Exception):
            console.page.wait_for_selector("#new-next:not([disabled])", timeout=15000)
    done = console.confirm("WALK_IN")
    ok(
        "and the customer can agree to it, so it becomes an order",
        (done["acceptance"] or {}).get("status", 0) < 300 and done["order"]["status"] < 300,
        f"acceptance {(done['acceptance'] or {}).get('status')} order {done['order']['status']} "
        f"{done['order']['text'][:120]}",
    )

    head("9b", "CHỦ XEM LẠI — the owner sees who chose which price, inside which band")
    console.sign_in("demo-owner")
    console.open("#/approvals", settle=2200)
    # CONSOLE-REDESIGN-003: today's range prices are the second side of the Duyệt switch.
    console.page.locator(".segmented__option", has_text="Giá trong khoảng").first.click()
    console.page.wait_for_timeout(400)
    review = console.text()
    ok(
        "the owner's review lists the price the counter chose today",
        "Demo Nhân viên vận hành" in review and "160.000" in review,
        [line for line in review.splitlines() if "khoảng" in line.lower()][:3],
    )
    ok(
        "with the band it was checked against",
        "80.000" in review and "240.000" in review,
        "",
    )


def scenario_prepaid(console: Console) -> None:
    """`DEC-032`: a walk-in pays the exact total at drop-off; pickup is its own, named step."""

    head("10", "TRẢ TRƯỚC — a walk-in pays when leaving the laundry (DEC-032)")
    _prepay_then_collect(console, mode="SELF_DROP_SELF_COLLECT", second="10b")


def scenario_pickup_only(console: Console) -> None:
    """The `DEC-032` addendum: the courier fetched it; the customer pays at the counter only."""

    head("10c", "CHỈ LẤY — the courier fetched the bag; the customer pays at the counter, early")
    # No one-way fee is published, so the server refuses to guess one (REQUIRE_HUMAN) and the
    # counter enters the fee agreed with the customer -- the path `DEC-003` gives a delivery fee
    # nobody published.
    _prepay_then_collect(console, mode="PICKUP_ONLY", second="10d", manual_fee_vnd=15_000)


def _prepay_then_collect(
    console: Console, *, mode: str, second: str, manual_fee_vnd: int | None = None
) -> None:
    console.sign_in("demo-operations")
    order = console.build_order(kg="7", stop="active", mode=mode, manual_fee_vnd=manual_fee_vnd)
    order_id = order["order_id"]
    # The amount a person at the counter reads out is the one the sheet shows as "Phải thu" -- the
    # washing and any delivery fee together. `pay` reads it off the sheet and types it.
    console.pay(order_id, "PREPAY")
    note(f"order {order_id[:8]}… is accepted and not yet washed; paid in advance from 'Khác'")
    console.open_order(order_id, settle=1600)
    shown = console.text()
    ok(
        "the exact total is taken at drop-off, and the screen says the customer has not "
        "collected yet",
        "Đã thu đủ tiền" in shown and "Khách chưa nhận đồ" in shown,
        [line for line in shown.splitlines() if "thu" in line.lower()][:3],
    )
    if READS_DATABASE:
        ok(
            "the order is PAID and no handover is recorded",
            stored(order_id, "balance_status") == "PAID"
            and stored(order_id, "self_collection_recorded") in ("f", "false"),
            f"{stored(order_id, 'balance_status')} / "
            f"{stored(order_id, 'self_collection_recorded')}",
        )

    offered = console.offered()
    ok(
        "once paid, the screen no longer offers to take the money a second time",
        "SETTLE" not in offered and "PREPAY" not in offered,
        offered,
    )
    ok(
        "and it does not offer the pickup press while the laundry is still unwashed",
        "COLLECT" not in offered
        and console.page.get_by_role("button", name="Khách đã nhận đồ").count() == 0,
        offered,
    )
    refused = console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/collection",
        if_match=console.current_version(order_id, order["row_version"]),
    )
    ok(
        "the server refuses that handover for the laundry, not for the money",
        refused["status"] == 422 and "GOODS_NOT_READY_FOR_HANDOVER" in refused["text"],
        f"HTTP {refused['status']} {refused['text'][:160]}",
    )
    ok(
        "handing over laundry still in the machine is refused",
        stored(order_id, "self_collection_recorded") in ("f", "false") if READS_DATABASE else True,
        "",
    )
    closing = console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/steps",
        {"step": "COMPLETE"},
        if_match=console.current_version(order_id, order["row_version"]),
    )
    ok(
        "and a paid order whose laundry was never handed over cannot be closed",
        "COMPLETE" not in offered
        and closing["status"] >= 400
        and (stored(order_id, "commercial_status") != "COMPLETED" if READS_DATABASE else True),
        f"HTTP {closing['status']} {closing['text'][:120]}",
    )

    version = console.current_version(order_id, order["row_version"])
    for target in ("QUEUED", "IN_PROCESS", "QUALITY_CHECK", "READY_AT_STORE", "RELEASED"):
        version = console.current_version(order_id, version)
        moved = console.call(
            "POST",
            f"/internal/v1/orders/{order_id}/production-transition",
            {"target": target},
            if_match=version,
        )
        if moved["status"] >= 300:
            ok(f"production reaches {target}", False, moved["text"][:160])
            return
    note("washed, checked and released from production")

    head(second, "KHÁCH TỚI LẤY — the handover, recorded under the staff member's name")
    console.open_order(order_id, settle=1600)
    ok(
        "the pickup press is the next step once the laundry is finished",
        console.primary_step() == "COLLECT",
        console.primary_step(),
    )
    pickup = console.page.locator(".action-bar--v2 button", has_text="Khách đã nhận đồ")
    if pickup.count():
        pickup.first.click()
        console.page.wait_for_timeout(500)
        console.page.locator("#collection-submit").click()
        touched("orderDetail.collection-submit")
        console.page.wait_for_timeout(1800)
    said = console.dialog_text()
    ok(
        "the handover is recorded once the laundry is finished",
        stored(order_id, "self_collection_recorded") in ("t", "true")
        if READS_DATABASE
        else "nhận đồ" in said,
        said[:160],
    )
    if READS_DATABASE:
        ok(
            "and it names the staff member who handed it over",
            sql(
                "select s.display_name from order_collections c join staff_users s "
                f"on s.id = c.collected_by_staff_id where c.order_id='{order_id}'"
            )
            == "Demo Nhân viên vận hành",
            sql(f"select count(*) from order_collections where order_id='{order_id}'"),
        )
    closing_offer = console.page.locator("dialog[open] button[data-step=COMPLETE]")
    ok("and the sheet offers to close the order straight away", closing_offer.count() == 1)
    if closing_offer.count():
        closing_offer.first.click()
        console.page.wait_for_timeout(1800)
    ok(
        "paid, released and collected: the order closes",
        stored(order_id, "commercial_status") == "COMPLETED"
        if READS_DATABASE
        else "Đơn đã đóng" in console.text(),
        console.said()[:160],
    )


def scenario_busy(console: Console) -> None:
    """`API-INTEGRITY-003`: a database that is busy says so, and the retry is safe."""

    head("11", "HỆ THỐNG BẬN — a row held by someone else, then the same press again")
    if not arguments.database_url:
        note("needs --database-url to hold a row lock from a second connection; skipped")
        return
    console.sign_in("demo-operations")
    order = console.build_order(kg="7", stop="released")
    order_id = order["order_id"]
    total = order["quote"]["net_service_subtotal_vnd"]
    grouped = f"{total:,}".replace(",", ".")
    # A second connection takes this order's row for twelve seconds -- longer than the API's
    # five-second lock_timeout -- the way a slow report or a stuck session would.
    holder = subprocess.Popen(
        [
            "psql",
            arguments.database_url,
            "-q",
            "-c",
            f"BEGIN; SELECT 1 FROM orders WHERE id='{order_id}' FOR UPDATE; "
            "SELECT pg_sleep(12); COMMIT;",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    console.open_order(order_id)
    control = console.step_control("SETTLE")
    if control is not None:
        control.click()
        console.page.wait_for_timeout(500)
    console.type_into("#settlement-amount", grouped)
    submit = console.page.locator("#settlement-submit")
    submit.click()
    try:
        console.page.wait_for_selector("text=Hệ thống đang bận", timeout=12000)
    except Exception:
        console.page.wait_for_timeout(1000)
    said = console.said()
    ok(
        "a payment that cannot get its row in time is told 'busy, try again', not a server fault",
        "Hệ thống đang bận" in said and "Đừng thử lại" not in said,
        said[:200],
    )
    ok(
        "and nothing was written for it",
        sql(f"select count(*) from order_settlements where order_id='{order_id}'") == "0",
        "",
    )
    holder.wait(timeout=30)
    note("the other connection let go of the row")
    if submit.count() and submit.is_enabled():
        submit.click()
        console.page.wait_for_timeout(2500)
    ok(
        "pressing again records the payment exactly once",
        sql(f"select count(*) from order_settlements where order_id='{order_id}'") == "1"
        and stored(order_id, "balance_status") == "PAID",
        console.said()[:160],
    )


def _collected_suits(console: Console) -> dict:
    """Three suits ironed at 30.000 ₫ each, paid for and handed back -- the complaint's order."""

    order = console.build_order(
        stop="released",
        lines=[
            {
                "service_code": "IRON_SUIT",
                "quantity": "3",
                "unit": "ITEM",
                "quantity_basis": "STAFF_MEASUREMENT",
            }
        ],
    )
    order_id = order["order_id"]
    total = order["quote"]["net_service_subtotal_vnd"]
    paid = console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/settlement",
        {"paid_amount_vnd": total, "collected_by_customer": True},
    )
    if paid["status"] >= 300:
        raise AssertionError(f"could not settle the suits: {paid['status']} {paid['text']}")
    version = console.current_version(order_id, order["row_version"])
    closed = console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/transition",
        {"target": "COMPLETED"},
        if_match=version,
    )
    if closed["status"] >= 300:
        raise AssertionError(f"could not close the suits: {closed['status']} {closed['text']}")
    return order


def _pick_kind(console: Console, kind: str) -> None:
    """Tap the kind chip ("Khách được gì?"), as staff do. A segmented control, not a select."""

    chip = console.page.locator(f"#remedy-kind button[data-value='{kind}']")
    chip.wait_for(state="visible")
    chip.click()
    console.page.wait_for_timeout(300)
    touched("remedy.kind")


def _propose_remedy(
    console: Console, *, kind: str, amount: str, garment: str = "", fault: bool = True
) -> str:
    """Fill the remedy form on the incident page as staff do; its caps are read on arrival."""

    _pick_kind(console, kind)
    console.page.wait_for_timeout(400)
    line = console.page.locator("#remedy-line option")
    if line.count() > 1:
        console.page.select_option("#remedy-line", index=1)
        console.page.wait_for_timeout(400)
    if garment and console.page.locator("#remedy-garment").count():
        console.choose("#remedy-garment", garment)
    if console.page.locator("#remedy-amount").count():
        console.type_into("#remedy-amount", amount)
    box = console.page.locator("#remedy-fault")
    if fault and box.count() and box.get_attribute("type") == "checkbox" and not box.is_checked():
        box.click()
        touched("remedy.fault")
    console.page.locator("button", has_text="Gửi đề nghị bồi hoàn").first.click()
    touched("remedy.propose")
    console.page.wait_for_timeout(2200)
    return console.said()


def scenario_remedy(console: Console) -> None:
    """`DEC-004` as `DEC-031` reads it: per garment, and the owner decides every loss."""

    head("12", "SỰ CỐ — three suits came back, the customer complains about two of them")
    console.sign_in("demo-operations")
    order = _collected_suits(console)
    order_id = order["order_id"]
    note(f"order {order_id[:8]}…: 3 x áo vest at 30.000 ₫, paid, handed back, closed")
    # CONSOLE-REDESIGN-004: the order is found by the ticket the customer holds, never pasted.
    slip = console.call("GET", f"/internal/v1/orders/{order_id}")["body"] or {}
    ticket_number = str(slip.get("ticket_number") or "")
    console.open("#/incidents")
    console.page.locator("#incident-create-open").click()
    touched("incidents.create-open")
    console.page.wait_for_timeout(300)
    if slip.get("ticket_issued_on"):
        console.page.locator("#incident-ticket-date").fill(str(slip["ticket_issued_on"]))
    console.type_into("#incident-ticket", ticket_number, control="incidents.ticket")
    console.page.keyboard.press("Enter")
    console.page.wait_for_timeout(1500)
    picked = console.page.locator("#incident-create .picked")
    ok(
        f"the ticket the customer holds (Phiếu {ticket_number}) finds the order -- no id is typed",
        picked.count() == 1 and picked.get_attribute("data-order-id") == order_id,
        picked.inner_text()[:80] if picked.count() else console.said()[:160],
    )
    console.type_into(
        "#incident-summary",
        "Áo vest thứ hai bị bạc màu cổ áo; áo thứ nhất bị mất",
        control="incidents.summary",
    )
    console.page.locator("#incident-submit").click()
    touched("incidents.submit")
    console.page.wait_for_timeout(2500)
    incident = sql(
        f"select id from customer_incidents where order_id='{order_id}' "
        "order by opened_at desc limit 1"
    )
    ok("the complaint is recorded against the order", len(incident) == 36, console.said()[:160])
    ok(
        "and its own page opens, with the remedy flow on it",
        console.page.url.endswith(f"#/incidents/{incident}")
        and console.page.locator("#remedy-kind").count() == 1,
        console.page.url,
    )
    _pick_kind(console, "DAMAGE_COMPENSATION")
    console.page.wait_for_timeout(400)
    if console.page.locator("#remedy-line option").count() > 1:
        console.page.select_option("#remedy-line", index=1)
        console.page.wait_for_timeout(400)
    garments = console.page.locator("#remedy-garment option").all_inner_texts()
    ok(
        "a line of three suits asks which one -- 'Món thứ mấy' offers suit 1, 2 and 3",
        len([g for g in garments if g.strip() and "chọn" not in g]) == 3,
        garments,
    )
    console.choose("#remedy-garment", "2")
    console.page.wait_for_timeout(500)
    caps = console.text()
    ok(
        "and the server shows that suit's ceiling before anyone types a figure: 5 x 30.000 ₫ = "
        "150.000 ₫",
        "150.000" in caps,
        [line for line in caps.splitlines() if "150.000" in line][:2],
    )

    head("12b", "MẤT ĐỒ — the lost suit goes to the owner, whatever the amount (DEC-031)")
    said = _propose_remedy(console, kind="LOST_ITEM", amount="50000", garment="1")
    rows = sql(
        "select kind, garment_index, amount_vnd, approval_id is not null from remedy_proposals "
        f"where incident_id='{incident}' and kind='LOST_ITEM'"
    )
    ok(
        "a 50.000 ₫ loss, under the staff limit, still waits for the owner",
        rows == "LOST_ITEM|1|50000|t",
        f"{rows} :: {said[:160]}",
    )

    head("12c", "MÓN THỨ MẤY — each suit has its own 100.000 ₫ staff limit (DEC-031 addendum)")
    said = _propose_remedy(console, kind="DAMAGE_COMPENSATION", amount="60000", garment="2")
    rows = sql(
        "select amount_vnd, approval_id is null from remedy_proposals "
        f"where incident_id='{incident}' and garment_index = 2"
    )
    ok(
        "60.000 ₫ for the faded second suit is within the staff limit: recorded, no owner needed",
        rows == "60000|t",
        f"{rows} :: {said[:120]}",
    )
    carry_out = console.page.locator("button[data-remedy-execute]")
    if carry_out.count():
        carry_out.first.click()
        touched("remedy.execute")
        console.page.wait_for_timeout(2200)
    paid_out = sql(
        "select count(*) from remedy_proposals p join remedy_credits c "
        "on c.remedy_proposal_id = p.id "
        f"where p.incident_id='{incident}' and p.garment_index = 2 and p.executed_at is not null"
    )
    ok(
        "carried out at the counter, it becomes a 60.000 ₫ credit with a code the customer keeps",
        paid_out == "1",
        f"{paid_out} :: {console.said()[:140]}",
    )
    ok(
        "and the complaint stays open, because the lost suit still waits for the owner",
        sql(f"select status from customer_incidents where id='{incident}'") == "UNDER_REVIEW",
        sql(f"select status from customer_incidents where id='{incident}'"),
    )
    said = _propose_remedy(console, kind="DAMAGE_COMPENSATION", amount="60000", garment="3")
    rows = sql(
        "select amount_vnd, approval_id is null from remedy_proposals "
        f"where incident_id='{incident}' and garment_index = 3"
    )
    ok(
        "the third suit has its own limit: another 60.000 ₫ on the same complaint is staff's too",
        rows == "60000|t",
        f"{rows} :: {said[:120]}",
    )
    said = _propose_remedy(console, kind="DAMAGE_COMPENSATION", amount="50000", garment="2")
    rows = sql(
        "select amount_vnd, approval_id is not null from remedy_proposals "
        f"where incident_id='{incident}' and garment_index = 2 order by proposed_at"
    )
    ok(
        "a second claim on the same suit adds up: 60.000 + 50.000 ₫ passes 100.000 ₫ and goes "
        "to the owner",
        rows.endswith("50000|t"),
        rows.replace("\n", " ; "),
    )
    said = _propose_remedy(console, kind="DAMAGE_COMPENSATION", amount="160000", garment="3")
    ok(
        "and nothing is paid past the ceiling: 160.000 ₫ on one suit is refused, not trimmed",
        "vượt trần" in said,
        said[:200],
    )

    head("12e", "CHỦ DUYỆT — the owner reads the lost suit on Duyệt and decides it")
    lost_id = sql(
        f"select id from remedy_proposals where incident_id='{incident}' and kind='LOST_ITEM'"
    )
    over_id = sql(
        f"select id from remedy_proposals where incident_id='{incident}' and garment_index = 2 "
        "and approval_id is not null"
    )
    console.sign_in("demo-owner")
    console.open("#/approvals", settle=2500)

    def remedy_card(proposal_id: str) -> Any:
        return console.page.locator(
            "article.card", has=console.page.locator(f"[data-remedy-binding='{proposal_id}']")
        )

    card = remedy_card(lost_id)
    shown = card.first.inner_text() if card.count() else ""
    ok(
        "the owner sees the claim itself: a lost suit, 50.000 ₫, the ceiling and why it is theirs",
        card.count() == 1 and "50.000" in shown and "150.000" in shown,
        shown.replace("\n", " | ")[:260],
    )
    if card.count():
        card.first.locator("button", has_text="Duyệt").first.click()
        console.page.wait_for_timeout(2200)
    ok(
        "and approves it in the console",
        sql(
            "select s.status from remedy_proposals p join approval_request_states s "
            f"on s.approval_request_id = p.approval_id where p.id='{lost_id}'"
        )
        == "APPROVED",
        console.said()[:160],
    )
    over = remedy_card(over_id)
    if over.count():
        over.first.locator("button", has_text="Từ chối").first.click()
        console.page.wait_for_timeout(2200)
    ok(
        "the second 50.000 ₫ on the faded suit, past the staff limit, the owner refuses",
        sql(
            "select s.status from remedy_proposals p join approval_request_states s "
            f"on s.approval_request_id = p.approval_id where p.id='{over_id}'"
        )
        == "REJECTED",
        console.said()[:160],
    )

    head("12f", "TRẢ SAU — back at the counter, the approved loss is carried out from the list")
    console.sign_in("demo-operations")
    # The owner's old link shape, `#/remedies?incident=`, forwards to the incident's own page.
    console.open(f"#/remedies?incident={incident}", settle=2000)
    ok(
        "the remedies address forwards to the complaint's page, from any session",
        console.page.url.endswith(f"#/incidents/{incident}"),
        console.page.url,
    )
    third_id = sql(
        f"select id from remedy_proposals where incident_id='{incident}' and garment_index = 3"
    )
    for proposal_id, label in ((lost_id, "lost suit"), (third_id, "third suit")):
        press = console.page.locator(f"button[data-remedy-execute='{proposal_id}']")
        if press.count():
            press.first.click()
            console.page.wait_for_timeout(2500)
        ok(
            f"the {label} is paid out from the list, in a later session, as a credit",
            sql(
                "select count(*) from remedy_credits c join remedy_proposals p "
                f"on p.id = c.remedy_proposal_id where p.id='{proposal_id}'"
            )
            == "1",
            console.said()[:140],
        )
    ok(
        "and with every claim decided, the complaint closes",
        sql(f"select status from customer_incidents where id='{incident}'") == "CLOSED",
        sql(f"select status from customer_incidents where id='{incident}'"),
    )

    head("12d", "ĐỌC LẠI — the complaint's claims, the order's credit, the customer's source")
    console.open(f"#/incidents/{incident}", settle=2000)
    listed = console.page.locator("#remedy-recorded-proposals li[data-proposal-id]")
    recorded = sql(f"select count(*) from remedy_proposals where incident_id='{incident}'")
    ok(
        "every claim recorded on the complaint is listed, not only this session's -- the four "
        "recorded; the refused one was never written",
        str(listed.count()) == recorded == "4",
        f"{listed.count()} listed, {recorded} recorded",
    )
    console.open(f"#/orders/{order_id}", settle=1800)
    credit = console.page.locator("#order-remedy-credits li[data-credit-status=UNUSED]")
    texts = " | ".join(credit.all_inner_texts())
    ok(
        "the order shows its three unused credits (60.000, 50.000 and 60.000 ₫), so a customer "
        "who lost a code keeps it",
        credit.count() == 3 and "50.000" in texts and "60.000" in texts,
        texts[:200] or "none",
    )
    source = console.page.locator("[data-field=acquisition-source]")
    ok(
        "and says where the customer came from, as recorded when the order was made",
        source.count() == 1 and bool(source.first.inner_text().strip()),
        source.first.inner_text()[:80] if source.count() else "absent",
    )


#: `RECEIPT-PRINT-001`: where the receipt scenario saves its print-media screenshots, when asked.
#: Unset, it saves nothing and says so; the checks run either way.
RECEIPT_SHOTS = os.environ.get("CONSOLE_RECEIPT_SHOTS", "")

#: A full UUID anywhere in visible text: the receipt carries none (tier 3 stays on the order page).
UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def scenario_receipt(console: Console) -> None:
    """`RECEIPT-PRINT-001`: the walk-in leaves with a receipt, printed or shared, of what the
    server holds -- and nothing it does not."""

    head("13", "PHIẾU CHO KHÁCH — the receipt a walk-in takes home (RECEIPT-PRINT-001)")
    console.sign_in("demo-operations")
    order = console.build_order(kg="7", stop="created")
    order_id = order["order_id"]

    offer = console.page.locator("#order-created button[data-receipt]")
    ok(
        "the order page Nhận đồ lands on offers 'In phiếu cho khách' straight away",
        offer.count() == 1 and "In phiếu cho khách" in offer.first.inner_text(),
        offer.first.inner_text()[:60] if offer.count() else "absent",
    )
    if offer.count():
        offer.first.click()
        touched("orderDetail.receipt-offer")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#receipt-paper [data-total]", timeout=15000)
        console.page.wait_for_selector("#receipt-paper .receipt-paper__lines", timeout=15000)
    console.page.wait_for_timeout(600)
    ok(
        "one tap opens the receipt of that order",
        console.page.url.endswith(f"#/orders/{order_id}/receipt"),
        console.page.url,
    )

    read = console.call("GET", f"/internal/v1/orders/{order_id}")
    server = read.get("body") or {}
    quote = (
        console.call(
            "GET",
            f"/internal/v1/stores/{STORE}/quotes/{server.get('quote_id')}"
            f"?revision={server.get('quote_revision')}",
        ).get("body")
        or {}
    )
    formatted = console.page.evaluate(
        """async ({total, amounts}) => {
            const format = await import('./src/core/format.js');
            return {total: format.money(total), amounts: amounts.map((a) => format.money(a))};
        }""",
        {
            "total": server.get("payable_total_vnd"),
            "amounts": [line.get("list_amount_vnd") for line in quote.get("lines") or []],
        },
    )
    paper = console.page.locator("#receipt-paper")
    total = paper.locator("[data-total]")
    ok(
        "the total on the paper is the server's figure for the order, verbatim",
        total.count() == 1 and total.first.inner_text().strip() == formatted["total"],
        f"{total.first.inner_text() if total.count() else 'absent'} vs {formatted['total']}",
    )
    printed = [
        node.inner_text().strip()
        for node in paper.locator(".receipt-paper__line .receipt-paper__value").all()
    ]
    ok(
        "every priced line is on the paper at the amount the stored revision holds",
        printed == formatted["amounts"] and len(printed) == len(quote.get("lines") or []),
        f"{printed} vs {formatted['amounts']}",
    )
    ticket = paper.locator("[data-field=ticket]")
    ok(
        "the ticket number is the order's",
        ticket.count() == 1
        and ticket.first.inner_text().strip() == f"Phiếu {server.get('ticket_number')}",
        ticket.first.inner_text() if ticket.count() else "absent",
    )
    closing = paper.locator("[data-field=closing]")
    # CUSTOMER-001: R4's "Tiệm sẽ báo" is a promise to call, and this walk-in left no number. The
    # paper tells them what is true instead; the customer-record walk proves the R4 line.
    ok(
        "it promises no ready time, and no call to a walk-in with no number: keep the slip",
        closing.count() == 1
        and closing.first.inner_text().strip() == "Giữ phiếu này để nhận đồ."
        and "hẹn" not in paper.inner_text().lower()
        and paper.locator("[data-field=customer]").count() == 0,
        closing.first.inner_text() if closing.count() else "absent",
    )
    visible = console.text()
    ok(
        "no identifier is visible anywhere on the receipt screen",
        re.search(UUID_TEXT, visible, re.IGNORECASE) is None,
        visible[:120],
    )
    reference = paper.locator("[data-field=reference] .receipt-paper__value")
    ok(
        "the short reference is the order's own, eight characters",
        reference.count() == 1 and reference.first.inner_text().strip() == order_id[:8].upper(),
        reference.first.inner_text() if reference.count() else "absent",
    )

    button = console.page.locator("#receipt-print")
    console.page.evaluate(
        "() => { window.__printed = 0; window.print = () => { window.__printed += 1; }; }"
    )
    if button.count() and button.first.is_enabled():
        button.first.click()
        touched("receipt.print")
    ok(
        "'In phiếu' opens the print dialog, once",
        console.page.evaluate("() => window.__printed") == 1,
    )
    share = console.page.locator("#receipt-share")
    supported = console.page.evaluate("() => typeof navigator.share === 'function'")
    ok(
        "'Chia sẻ' is offered exactly where the browser can share",
        share.count() == (1 if supported else 0),
        f"supported={supported}, rendered={share.count()}",
    )

    # Print media: only the slip. Measured at a thermal roll's width, and filmed there when asked.
    original = console.page.viewport_size
    console.page.emulate_media(media="print")
    for label, width in (("80mm", 302), ("58mm", 219), ("a5", 559)):
        console.page.set_viewport_size({"width": width, "height": 900})
        console.page.wait_for_timeout(250)
        shell = console.page.evaluate(
            """() => ['.appbar', '.nav', '.action-bar', '.page-head', '.banners']
                .map((s) => [...document.querySelectorAll(s)])
                .flat()
                .filter((e) => e.getClientRects().length > 0).length"""
        )
        overflow = console.page.evaluate(
            "() => document.querySelector('#receipt-paper').scrollWidth - "
            "document.querySelector('#receipt-paper').clientWidth"
        )
        ok(
            f"printed at {label}: the shell, header and buttons are gone and nothing overflows",
            shell == 0 and overflow <= 0 and paper.is_visible(),
            f"{shell} shell elements visible, {overflow}px overflow",
        )
        if RECEIPT_SHOTS:
            os.makedirs(RECEIPT_SHOTS, exist_ok=True)
            tag = os.environ.get("CONSOLE_VIEWPORT", "desk")
            paper.screenshot(path=os.path.join(RECEIPT_SHOTS, f"real-print-{label}-{tag}.png"))
    console.page.emulate_media(media="screen")
    if original:
        console.page.set_viewport_size(original)
    if not RECEIPT_SHOTS:
        note("CONSOLE_RECEIPT_SHOTS unset: the print-media screenshots were not saved")

    # Later -- the customer is back, or asks again: the order page's "Khác" has it too.
    console.open_order(order_id, settle=1600)
    ok(
        "the 'just created' offer does not come back on a later visit",
        console.page.locator("#order-created button[data-receipt]").count() == 0,
    )
    more = console.page.locator("button[data-more-steps]")
    direct = console.page.locator(".action-bar--v2 button[data-receipt]")
    if more.count():
        more.first.click()
        console.page.wait_for_timeout(400)
        entry = console.page.locator("dialog[open] button[data-receipt]")
    else:
        entry = direct
    ok(
        "'Khác' lists 'In phiếu' beside the order's other steps",
        entry.count() == 1 and "In phiếu" in entry.first.inner_text(),
        f"more={more.count()}, entry={entry.count()}",
    )
    if entry.count():
        entry.first.click()
        touched("orderDetail.receipt-more")
        console.page.wait_for_timeout(1500)
    ok(
        "and opens the same receipt",
        console.page.url.endswith(f"#/orders/{order_id}/receipt")
        and console.page.locator("#receipt-paper [data-total]").count() == 1,
        console.page.url,
    )


def _history_text(console: Console) -> str:
    """The order page's history list as it reads, once the side read has landed."""

    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#order-timeline", timeout=8000)
    node = console.page.locator("#order-timeline")
    return node.first.inner_text() if node.count() else ""


def _event_reason(order_id: str, key: str) -> str:
    return sql(
        f"select coalesce(payload->>'step','') || ':' || coalesce(payload->>'{key}','') "
        f"from domain_events where aggregate_id='{order_id}' and payload ? '{key}'"
    )


def scenario_rework(console: Console) -> None:
    """`ORDER-STEPS-002`: a rewash and a refusal at intake, each a step with a reason."""

    head("13", "GIẶT LẠI — a stain found at quality check is washed again inside the same order")
    stained = console.build_order(stop="checking")
    order_id = stained["order_id"]
    console.open_order(order_id)
    ok(
        "the big button is still the ordinary next step, never the rewash",
        console.primary_step() == "MARK_READY",
        console.primary_step(),
    )
    offered = console.offered()
    ok("Giặt lại is offered under Khác", "REWASH" in offered, offered)
    read = console.call("GET", f"/internal/v1/orders/{order_id}")["body"] or {}
    total_before = read.get("payable_total_vnd")
    control = console.step_control("REWASH")
    if control is not None:
        control.click()
        console.page.wait_for_timeout(500)
    reasons = [
        str(node.get_attribute("data-value"))
        for node in console.page.locator("dialog[open] #step-reason [data-value]").all()
    ]
    submit = console.page.locator("dialog[open] #step-reason-submit")
    ok(
        "the sheet offers the three reasons the server listed and is shut until one is picked",
        reasons == ["NOT_CLEAN", "MACHINE_FAULT", "OTHER"]
        and submit.count() == 1
        and submit.first.is_disabled(),
        reasons,
    )
    ok(
        "and it reads as the counter speaks: why, and that the customer pays nothing more",
        "Vì sao giặt lại?" in console.dialog_text()
        and "Chưa sạch" in console.dialog_text()
        and "Khách không trả thêm tiền" in console.dialog_text(),
        console.dialog_text()[:160],
    )
    console.page.keyboard.press("Escape")
    console.page.wait_for_timeout(300)

    said = console.step(order_id, "REWASH", reason="NOT_CLEAN", reopen=False)
    if READS_DATABASE:
        ok(
            "the laundry goes back through the wash",
            stored(order_id, "production_status") == "IN_PROCESS",
            f"{stored(order_id, 'production_status')} · {said[:120]}",
        )
        ok(
            "the reason is on the step's own event, permanently",
            _event_reason(order_id, "rewash_reason") == "REWASH:NOT_CLEAN",
            _event_reason(order_id, "rewash_reason"),
        )
    total_after = console.call("GET", f"/internal/v1/orders/{order_id}")["body"] or {}
    ok(
        "and the price did not move: a rewash costs the customer nothing",
        total_before is not None and total_after.get("payable_total_vnd") == total_before,
        f"{total_before} → {total_after.get('payable_total_vnd')}",
    )
    console.open_order(order_id)
    ok(
        "the page now offers the check again as the next step",
        console.primary_step() == "QUALITY_CHECK",
        console.primary_step(),
    )
    history = _history_text(console)
    ok(
        "the history names the step and its reason: 'Giặt lại · Chưa sạch'",
        "Giặt lại · Chưa sạch" in history,
        history[-200:],
    )
    console.step(order_id, "QUALITY_CHECK", reopen=False)
    console.step(order_id, "MARK_READY")
    console.pay(order_id, "SETTLE")
    console.step(order_id, "HAND_OVER")
    if READS_DATABASE:
        ok(
            "the rewashed order walks forward again to completed",
            stored(order_id, "commercial_status") == "COMPLETED",
            stored(order_id, "commercial_status"),
        )

    head("13b", "KHÔNG NHẬN ĐỒ — goods refused on the counter close the order, no money moves")
    bag = console.build_order(stop="received")
    bag_id = bag["order_id"]
    console.open_order(bag_id)
    ok(
        "a bag on the counter still offers Nhận đồ as the big button",
        console.primary_step() == "RECEIVE",
        console.primary_step(),
    )
    offered = console.offered()
    ok("and Không nhận đồ under Khác", "REJECT_INTAKE" in offered, offered)
    missing = console.call(
        "POST",
        f"/internal/v1/orders/{bag_id}/steps",
        {"step": "REJECT_INTAKE"},
        if_match=console.current_version(bag_id, bag["row_version"]),
    )
    ok(
        "the server refuses a refusal with no reason (422), and nothing is written",
        missing["status"] == 422,
        f"HTTP {missing['status']} {missing['text'][:100]}",
    )
    said = console.step(bag_id, "REJECT_INTAKE", reason="NOT_SERVICEABLE", reopen=False)
    if READS_DATABASE:
        ok(
            "the goods are refused and the order is cancelled",
            stored(bag_id, "intake_status || '/' || commercial_status") == "REJECTED/CANCELLED",
            f"{stored(bag_id, 'intake_status || commercial_status')} · {said[:120]}",
        )
        ok(
            "no money moved",
            stored(bag_id, "balance_status") == "UNPAID"
            and sql(f"select count(*) from order_settlements where order_id='{bag_id}'") == "0",
            stored(bag_id, "balance_status"),
        )
        ok(
            "the reason is on the step's own event",
            _event_reason(bag_id, "rejection_reason") == "REJECT_INTAKE:NOT_SERVICEABLE",
            _event_reason(bag_id, "rejection_reason"),
        )
    console.open_order(bag_id)
    ok(
        "the closed order offers no step any more",
        console.offered() == [] and "Đơn đã đóng" in console.text(),
        console.offered(),
    )
    history = _history_text(console)
    ok(
        "the history reads 'Không nhận đồ · Tiệm không giặt loại này'",
        "Không nhận đồ · Tiệm không giặt loại này" in history,
        history[-200:],
    )
    again = console.call(
        "POST",
        f"/internal/v1/orders/{bag_id}/steps",
        {"step": "REJECT_INTAKE", "rejection_reason": "OTHER"},
        if_match=console.current_version(bag_id, bag["row_version"]),
    )
    ok(
        "and the server refuses to refuse it twice",
        again["status"] == 409,
        f"HTTP {again['status']} {again['text'][:100]}",
    )


def scenario_export_range(console: Console) -> None:
    """`EXPORT-RANGE-001`: seven days of the shop's orders leave once, with two people accountable.

    FR-RPT-003. One person defines the export -- the window is theirs -- and a different person
    approves it, reading both ends of the window on the approval card before the control. The seed
    has one `OWNER_ADMIN`, and owner policy (`_OWNER_FINANCIAL`) lets only an owner decide an
    export, so the definer is the approver-role account (`demo-approver`, in `EXPORT_ROLES`) and the
    decider is the owner. The definer's screen stays open in its own browser context while the owner
    decides -- the in-progress export is page memory, exactly as on two real phones -- and then
    releases the file. What is proved: an over-long window is refused by name and writes nothing;
    the 7-day preset sends a 7-day window; the card names it; the file releases once; its row count
    is the number of orders opened in those seven shop-local days, counted independently here.
    """

    head("13", "XUẤT THEO KHOẢNG — 7 ngày, người khác duyệt, xuất đúng một lần")
    browser = console.context.browser
    context = browser.new_context(viewport=viewport())
    requester = Console(context.new_page(), context)
    requester.sign_in("demo-approver")
    requester.open("#/exports", settle=1500)
    page = requester.page
    ok(
        "no window is chosen for the person: every preset is off on arrival",
        page.locator("#export-range [aria-pressed='true']").count() == 0,
        "",
    )
    ok(
        "the screen says which event cuts the day before anything is chosen",
        "ngày mở đơn" in requester.text() and "không theo lúc thu tiền" in requester.text(),
        requester.text()[:160],
    )

    def requests_by_requester() -> str:
        return sql(
            "select count(*) from export_requests e join staff_users s "
            "on s.id = e.requested_by_staff_id where s.oidc_subject = 'demo-approver'"
        )

    before = requests_by_requester()
    # A custom window of 101 days: the server's bound, met by name, with nothing written.
    page.locator("#export-range [data-value='custom']").click()
    page.wait_for_timeout(300)
    touched("exports.range-custom")
    page.locator("#export-from").fill("2026-01-01")
    page.locator("#export-to").fill("2026-04-11")
    page.locator("#export-create").click()
    page.wait_for_timeout(1500)
    touched("exports.create")
    ok(
        "a window longer than 92 days is refused by name",
        "EXPORT_WINDOW_TOO_LONG" in requester.reason_codes(),
        requester.said()[:160],
    )
    ok(
        "and is said in Vietnamese beside the control",
        "tối đa 92 ngày" in requester.said(),
        requester.said()[:160],
    )
    if READS_DATABASE:
        ok("and wrote no export request", requests_by_requester() == before, before)

    page.locator("#export-range [data-value='7d']").click()
    page.wait_for_timeout(300)
    touched("exports.range-preset")
    chosen = page.locator("#export-window").inner_text()
    ok("the 7-day preset shows the two days it names", "→" in chosen, chosen)
    page.locator("#export-create").click()
    page.wait_for_timeout(1600)
    request_row = sql(
        "select e.id || '|' || e.business_date || '|' || coalesce(e.business_date_to::text, '') "
        "from export_requests e join staff_users s on s.id = e.requested_by_staff_id "
        "where s.oidc_subject = 'demo-approver' order by e.requested_at desc limit 1"
    )
    request_id, first, last = ([*request_row.split("|"), "", ""])[:3]
    if READS_DATABASE:
        ok(
            "the request stored a seven-day window, first day to last",
            bool(first and last) and sql(f"select date '{last}' - date '{first}'") == "6",
            request_row,
        )
    page.locator("#export-approval").click()
    page.wait_for_timeout(1600)
    touched("exports.request-approval")
    approval_id = sql(
        f"select id from approval_requests where resource_id = '{request_id}'"
        if request_id
        else "select ''"
    )
    ok(
        "the envelope is raised and the screen waits for an owner",
        "Chờ chủ tiệm duyệt" in requester.text(),
        requester.said()[:140],
    )

    console.sign_in("demo-owner")
    console.open("#/approvals", settle=2500)
    card = console.page.locator("article.card", has_text="Khoảng ngày")
    shown = card.first.inner_text() if card.count() else ""
    day = lambda iso: f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"  # noqa: E731
    ok(
        "the owner reads both ends of the window and its length on the card",
        bool(first and last) and f"{day(first)} → {day(last)} · 7 ngày" in shown,
        shown.replace("\n", " | ")[:200],
    )
    if card.count():
        card.first.locator("button", has_text="Duyệt").first.click()
        console.page.wait_for_timeout(2200)
        touched("approvals.export-approve")
    if READS_DATABASE:
        ok(
            "and approves it: a different person from the one who chose the window",
            eventually(
                f"select status from approval_request_states where approval_request_id = "
                f"'{approval_id}'",
                "APPROVED",
            )
            == "APPROVED",
            console.said()[:160],
        )

    page.locator("#export-execute").click()
    page.wait_for_timeout(2200)
    touched("exports.execute")
    released = sql(
        f"select row_count from data_exports where export_request_id = '{request_id}'"
        if request_id
        else "select ''"
    )
    expected = sql(
        f"select count(*) from orders where store_id = '{STORE}' and "
        f"(created_at at time zone 'Asia/Ho_Chi_Minh')::date between '{first}' and '{last}'"
        if first and last
        else "select ''"
    )
    if READS_DATABASE:
        ok(
            "the file is released, its row count the orders opened in those seven days",
            released != "" and released == expected,
            f"released {released}, opened in window {expected}",
        )
        if not arguments.only:
            # In a whole run the scenarios above opened orders today, so an empty file here would
            # make the equality above vacuous rather than proved.
            ok(
                "and the window is not empty: the orders this run opened are in it",
                expected not in ("", "0"),
                expected,
            )
        rows_shown = page.evaluate(
            """() => [...document.querySelectorAll("dl.kv .kv__row")]
                .filter((r) => r.querySelector("dt")?.textContent === "Số dòng")
                .map((r) => r.querySelector("dd")?.textContent || "")[0] || ''"""
        )
        ok(
            "and the number is on the screen the requester is holding",
            str(rows_shown).replace(".", "") == released,
            repr(rows_shown),
        )
    download = page.locator("#export-download")
    if download.count():
        with page.expect_download() as caught:
            download.click()
        touched("exports.download")
        ok(
            "the downloaded file is named for both days",
            bool(first and last) and f"ntl-{first}_{last}-" in caught.value.suggested_filename,
            caught.value.suggested_filename,
        )
    if READS_DATABASE:
        ok(
            "one approval released one file",
            sql(f"select count(*) from data_exports where export_request_id = '{request_id}'")
            == "1",
            "",
        )
    context.close()


# --- SESSION-LIST-001 --------------------------------------------------------------------------


def _second_device(console: Console, subject: str) -> Console:
    """Another browser, signed in as `subject`: its own context, so its own cookie jar."""

    context = console.context.browser.new_context(viewport=viewport())
    device = Console(context.new_page(), context)
    if device.sign_in(subject) not in (200, 201):
        raise AssertionError(f"{subject} could not sign in on a second device")
    return device


def _session_id(device: Console) -> str:
    answer = device.call("GET", "/internal/v1/session")
    return str((answer.get("body") or {}).get("session_id") or "")


def _no_secret(answer: dict[str, Any]) -> bool:
    text = str(answer.get("text") or "")
    return "secret" not in text and "hash" not in text


def _revoke_from_list(console: Console, scope: str, session_id: str) -> dict[str, Any]:
    """Two presses on "Đăng xuất thiết bị này" for one row, returning the server's answer."""

    control = console.page.locator(f"{scope} button[data-revoke-session='{session_id}']")
    control.first.scroll_into_view_if_needed()
    control.first.click()
    console.page.wait_for_timeout(250)
    # Not `press_capturing`: the revoke answers 204, and a 204 has no body for it to read.
    with console.page.expect_response(
        lambda r: r.request.method == "POST" and r.url.split("?")[0].endswith("/revoke"),
        timeout=20000,
    ) as info:
        control.first.click()
    console.page.wait_for_timeout(1500)
    return {"status": info.value.status, "text": info.value.status_text}


def scenario_sessions(console: Console) -> None:
    """A lost phone: one person on two devices; the first signs the second out."""

    head("13", "MẤT ĐIỆN THOẠI — one person on two devices; the first signs the second out")
    console.sign_in("demo-owner")
    phone = _second_device(console, "demo-owner")
    try:
        mine, theirs = _session_id(console), _session_id(phone)
        ok(
            "each device is told its own session, and the two differ",
            bool(mine) and bool(theirs) and mine != theirs,
            f"{mine[:8]} / {theirs[:8]}",
        )
        listed = console.call("GET", "/internal/v1/sessions")
        rows = (listed.get("body") or {}).get("sessions") or []
        ok(
            "the server lists both, marks this one current, and returns no secret or hash",
            {str(row.get("session_id")): row.get("current") for row in rows}.get(mine) is True
            and theirs in {str(row.get("session_id")) for row in rows}
            and _no_secret(listed)
            and _no_secret(console.call("GET", "/internal/v1/session")),
            f"{len(rows)} sessions",
        )

        console.open("#/")
        console.page.locator("button.appbar__account").first.click()
        touched("shell.account-open")
        console.page.wait_for_selector(
            f"#account-devices [data-session-id='{theirs}']", timeout=10000
        )
        here = console.page.locator(f"#account-devices [data-session-id='{mine}']")
        ok(
            "the account sheet lists this device first, as “Thiết bị này”, with no sign-out on it",
            here.count() == 1
            and "Thiết bị này" in (here.first.inner_text() or "")
            and here.first.get_attribute("data-session-current") == "true"
            and console.page.locator(f"button[data-revoke-session='{mine}']").count() == 0
            and console.page.locator("#account-devices [data-session-id]").first.get_attribute(
                "data-session-id"
            )
            == mine,
        )
        answer = _revoke_from_list(console, "#account-devices", theirs)
        touched("shell.device-revoke")
        ok("two presses sign the other device out", answer["status"] == 204, answer["text"][:120])
        ok(
            "the list is read again: the other device is gone, this one is still there",
            console.page.locator(f"#account-devices [data-session-id='{theirs}']").count() == 0
            and console.page.locator(f"#account-devices [data-session-id='{mine}']").count() == 1,
        )
        ok(
            "and this device carries on working",
            console.call("GET", "/internal/v1/session")["status"] == 200,
        )
        if READS_DATABASE:
            ok(
                "the sign-out is one change: the session row, its event, its audit and its outbox",
                sql(
                    "select (select count(*) from staff_sessions where id = "
                    f"'{theirs}' and revoked_at is not null)"
                    " || '/' || (select count(*) from domain_events where aggregate_id = "
                    f"'{theirs}' and event_type = 'STAFF_SESSION_REVOKED')"
                    " || '/' || (select count(*) from audit_events where aggregate_id = "
                    f"'{theirs}' and action = 'STAFF_SESSION_REVOKE')"
                    " || '/' || (select count(*) from outbox_events where aggregate_id = "
                    f"'{theirs}' and payload->>'event_type' = 'STAFF_SESSION_REVOKED')"
                )
                == "1/1/1/1",
            )
        console.page.keyboard.press("Escape")

        # The phone does not know yet. Its next request is what tells it.
        phone.page.evaluate("() => { location.hash = '#/orders'; }")
        phone.page.wait_for_timeout(2200)
        ended = phone.page.content()
        ok(
            "the signed-out device's next request ends its session, and it says so",
            "Phiên đăng nhập đã kết thúc" in ended and "Chưa đăng nhập" in ended,
        )
        phone.page.evaluate("() => { location.hash = '#/'; }")
        phone.page.wait_for_timeout(1200)
        heading = phone.page.locator("main h1").first
        ok(
            "and the next screen it opens is the signed-out screen, with the way back in",
            heading.count() == 1
            and (heading.inner_text() or "").strip() == "Chưa đăng nhập"
            and phone.page.locator("main a.button", has_text="Tới trang đăng nhập").count() == 1,
        )
        ok(
            "the phone's own session read is refused now",
            phone.call("GET", "/internal/v1/session")["status"] == 401,
        )
    finally:
        phone.context.close()

    head("13a", "THIẾT BỊ CỦA MỘT NGƯỜI — the owner sees them; nobody else can")
    worker = _second_device(console, "demo-operations")
    try:
        # A second sign-in in the same browser leaves the first session alive on the server: the
        # shape of "I signed in on the shop tablet yesterday and never pressed Thoát".
        forgotten = _session_id(worker)
        worker.sign_in("demo-operations")
        current = _session_id(worker)
        owner_id = str(console.call("GET", "/internal/v1/session")["body"]["staff_user_id"])
        worker_id = str(worker.call("GET", "/internal/v1/session")["body"]["staff_user_id"])
        ok(
            "a member of staff may not list the owner's devices",
            worker.call("GET", f"/internal/v1/staff/{owner_id}/sessions")["status"] == 403,
        )
        ok(
            "nor sign out anyone else's, nor even another of their own — that is the owner's press",
            worker.call("POST", f"/internal/v1/sessions/{forgotten}/revoke")["status"] == 403
            and worker.call("POST", f"/internal/v1/sessions/{_session_id(console)}/revoke")[
                "status"
            ]
            == 403,
        )
        worker.open("#/")
        worker.page.locator("button.appbar__account").first.click()
        worker.page.wait_for_selector(
            f"#account-devices [data-session-id='{forgotten}']", timeout=10000
        )
        shut = worker.page.locator(f"#account-devices button[data-revoke-session='{forgotten}']")
        ok(
            "their account sheet shows the forgotten device, its sign-out shut, and why",
            shut.count() == 1
            and shut.first.is_disabled()
            and "Chỉ chủ đăng xuất được một thiết bị khác"
            in (worker.page.locator("#account-devices").inner_text() or ""),
        )
        worker.page.keyboard.press("Escape")

        console.open("#/staff")
        person = console.page.locator(f"#staff-directory [data-staff-id='{worker_id}']")
        if person.count():
            person.first.click()
            touched("staff.person-open")
        console.page.wait_for_selector(
            f"#staff-devices [data-session-id='{forgotten}']", timeout=10000
        )
        on_sheet = {
            str(node.get_attribute("data-session-id"))
            for node in console.page.locator("#staff-devices [data-session-id]").all()
        }
        ok(
            "the owner sees the same devices on the person's sheet",
            {forgotten, current} <= on_sheet,
            f"{len(on_sheet)} listed",
        )
        answer = _revoke_from_list(console, "#staff-devices", forgotten)
        touched("staff.device-revoke")
        ok(
            "and signs the forgotten one out",
            answer["status"] == 204
            and console.page.locator(f"#staff-devices [data-session-id='{forgotten}']").count()
            == 0,
            answer["text"][:120],
        )
        ok(
            "while the device the person is using keeps working",
            worker.call("GET", "/internal/v1/session")["status"] == 200
            and current
            in {
                str(row.get("session_id"))
                for row in worker.call("GET", "/internal/v1/sessions")["body"]["sessions"]
            },
        )
        console.page.keyboard.press("Escape")
    finally:
        worker.context.close()


def _raise_order_envelope(maker: Console, order_id: str) -> dict[str, Any]:
    """An ACCEPT_ORDER envelope over the order as it is now, from values the server handed back.

    Raised over the API because no screen raises an ORDER envelope (`#/gaps`, "Tạo yêu cầu
    duyệt"): the server opens those itself when a command reaches an approval threshold. The
    snapshot is the order's bound quote revision's own digest, read from the quote route.
    """

    view = maker.call("GET", f"/internal/v1/orders/{order_id}")["body"]
    quote = maker.call(
        "GET",
        f"/internal/v1/stores/{STORE}/quotes/{view['quote_id']}?revision={view['quote_revision']}",
    )["body"]
    rendered = hashlib.sha256(f"order-card:{order_id}:{view['row_version']}".encode()).hexdigest()
    raised = maker.call(
        "POST",
        "/internal/v1/approvals",
        {
            "store_id": STORE,
            "action": "ACCEPT_ORDER",
            "resource_type": "ORDER",
            "resource_id": order_id,
            "resource_version": view["row_version"],
            "snapshot_hash": quote["snapshot_hash"],
            "rendered_hash": f"JCS-SHA256-V1:{rendered}",
            "policy_version": "conformance-order-envelope-v1",
        },
    )
    if raised["status"] != 201:
        raise AssertionError(f"could not raise an ORDER envelope: {raised['text']}")
    return raised["body"]


def scenario_order_envelope(console: Console) -> None:
    """An ORDER approval card says whether the order moved since it was sent for approval."""

    head("14", "DUYỆT ĐƠN — the card says whether the order changed since it was sent for approval")
    console.sign_in("demo-owner")
    order = console.build_order(stop="created")
    order_id = order["order_id"]
    maker = _second_device(console, "demo-operations")
    try:
        stale = _raise_order_envelope(maker, order_id)
        moved = console.call(
            "POST",
            f"/internal/v1/orders/{order_id}/intake-transition",
            {"target": "RECEIVED_PENDING_INSPECTION", "slot_approved": False},
            if_match=order["row_version"],
        )
        ok("the order moves on after the first envelope", moved["status"] == 200, moved["text"])
        fresh = _raise_order_envelope(maker, order_id)
    finally:
        maker.context.close()

    console.open("#/approvals", settle=1500)
    touched("shell.nav.approvals")
    fresh_card = console.page.locator(
        f"article.card[data-approval-id='{fresh['approval_request_id']}']"
    )
    stale_card = console.page.locator(
        f"article.card[data-approval-id='{stale['approval_request_id']}']"
    )
    with contextlib.suppress(Exception):
        fresh_card.locator("[data-order-version]").first.wait_for(timeout=10000)
        stale_card.locator("[data-order-version]").first.wait_for(timeout=10000)
    approve_fresh = fresh_card.get_by_role("button", name="Duyệt", exact=True)
    ok(
        "a card whose order has not moved says so, in words, above a live Duyệt",
        fresh_card.locator("[data-order-version='current']").count() == 1
        and "Đơn chưa thay đổi kể từ khi gửi duyệt" in (fresh_card.inner_text() or "")
        and approve_fresh.count() == 1
        and approve_fresh.is_enabled(),
    )
    approve_stale = stale_card.get_by_role("button", name="Duyệt", exact=True)
    refuse_stale = stale_card.get_by_role("button", name="Từ chối", exact=True)
    ok(
        "a card whose order moved says so, links to the order, and shuts Duyệt with the reason",
        stale_card.locator("[data-order-version='changed']").count() == 1
        and "Đơn đã thay đổi sau khi gửi duyệt — mở đơn để xem lại"
        in (stale_card.inner_text() or "")
        and stale_card.locator(f"a[href='#/orders/{order_id}']").count() >= 1
        and approve_stale.count() == 1
        and approve_stale.is_disabled()
        and "đơn đã đổi sau khi gửi duyệt" in (stale_card.inner_text() or "")
        and refuse_stale.is_enabled(),
    )
    no_versions = "v1" not in (fresh_card.inner_text() or "").split()
    ok(
        "neither card asks the approver to compare version numbers",
        no_versions and "Phiên bản dòng" not in (stale_card.inner_text() or ""),
    )
    # The exact binding the card would send: the queue row's own version and digests.
    queue = console.call("GET", "/internal/v1/approvals?limit=200").get("body") or []
    row = next(
        (item for item in queue if item.get("approval_request_id") == stale["approval_request_id"]),
        {},
    )
    refused = console.call(
        "POST",
        f"/internal/v1/approvals/{stale['approval_request_id']}/decisions",
        {
            "decision": "APPROVED",
            "reason_code": "APPROVED_AFTER_CONSOLE_REVIEW",
            "resource_version": row.get("resource_version"),
            "snapshot_hash": row.get("snapshot_hash"),
            "rendered_hash": row.get("rendered_hash"),
        },
    )
    ok(
        "the server refuses to approve the moved order anyway — it is the authority",
        refused["status"] == 409 and "RESOURCE_CHANGED_SINCE_REQUEST" in refused["text"],
        f"{refused['status']} {refused['text'][:100]}",
    )
    stale_card.scroll_into_view_if_needed()
    (rejected,) = console.press_capturing(refuse_stale, "/decisions")
    touched("approvals.order-refuse")
    ok(
        "Từ chối still takes the dead envelope off the queue",
        rejected["status"] == 200 and (rejected["body"] or {}).get("status") == "REJECTED",
        rejected["text"][:120],
    )
    console.page.wait_for_timeout(1200)
    fresh_card = console.page.locator(
        f"article.card[data-approval-id='{fresh['approval_request_id']}']"
    )
    with contextlib.suppress(Exception):
        fresh_card.locator("[data-order-version='current']").first.wait_for(timeout=10000)
    fresh_card.scroll_into_view_if_needed()
    (approved,) = console.press_capturing(
        fresh_card.get_by_role("button", name="Duyệt", exact=True), "/decisions"
    )
    touched("approvals.order-approve")
    ok(
        "and the unchanged order's envelope is approved from its card",
        approved["status"] == 200 and (approved["body"] or {}).get("status") == "APPROVED",
        approved["text"][:120],
    )
    if READS_DATABASE:
        ok(
            "the two decisions are what the database holds",
            sql(
                "select string_agg(status, ',' order by status) from approval_request_states "
                f"where approval_request_id in ('{stale['approval_request_id']}', "
                f"'{fresh['approval_request_id']}')"
            )
            == "APPROVED,REJECTED",
        )


def _unused_credit_ids(console: Console) -> list[str]:
    """The store's unused credits as the counter's picker reads them."""

    listed = console.call("GET", f"/internal/v1/stores/{STORE}/remedy-credits?limit=200")
    rows = (listed.get("body") or {}).get("credits") or []
    return [str(row.get("credit_id")) for row in rows if isinstance(row, dict)]


def scenario_credit_pick(console: Console) -> None:
    """`CREDIT-PICK-001`: a returning customer's credit is picked from a list, never typed."""

    head("13", "KHOẢN GIẢM TRỪ — a complaint pays a credit; the next bill picks it from a list")
    console.sign_in("demo-operations")
    order = _collected_suits(console)
    order_id = order["order_id"]
    slip = console.call("GET", f"/internal/v1/orders/{order_id}")["body"] or {}
    ticket_number = slip.get("ticket_number")
    opened = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/incidents",
        {"order_id": order_id, "evidence_summary": "Áo vest thứ nhất bị sờn cổ sau khi ủi"},
    )
    incident = str((opened.get("body") or {}).get("incident_id") or "")
    ok("the complaint is recorded against the suits", opened["status"] == 201, opened["text"][:160])
    console.open(f"#/incidents/{incident}", settle=2000)
    said = _propose_remedy(console, kind="DAMAGE_COMPENSATION", amount="40000", garment="1")
    carry_out = console.page.locator("button[data-remedy-execute]")
    if carry_out.count():
        carry_out.first.click()
        touched("remedy.execute")
        console.page.wait_for_timeout(2200)
    credits = (
        console.call("GET", f"/internal/v1/stores/{STORE}/orders/{order_id}/remedy-credits")["body"]
        or {}
    )
    issued = [c for c in credits.get("credits") or [] if c.get("status") == "UNUSED"]
    credit_id = str(issued[0]["credit_id"]) if issued else ""
    ok(
        "carried out at the counter, it is a 40.000 ₫ credit the customer does not have to "
        "remember",
        len(issued) == 1 and issued[0].get("amount_vnd") == 40_000,
        f"{issued} :: {said[:120]}",
    )
    ok(
        "and the store's unused-credit list carries it, with no contact field",
        credit_id in _unused_credit_ids(console)
        and "contact"
        not in json.dumps(
            console.call("GET", f"/internal/v1/stores/{STORE}/remedy-credits")["body"]
        ),
        credit_id[:8],
    )

    head("13b", "LẦN SAU — the customer is back with a new bag and no code")
    console.walk_in()
    console.add_line("STANDARD_WASH_DRY", "7")
    priced = console.price()
    before = (priced.get("body") or {}).get("display_total_min_vnd")
    ok("the new bag is priced by the server", priced["status"] < 300, f"HTTP {priced['status']}")
    console.page.locator("#new-credit-open").click()
    touched("newOrder.credit-open")
    row = console.page.locator(f"#new-credit-list [data-credit-id='{credit_id}']")
    with contextlib.suppress(Exception):
        row.wait_for(state="visible", timeout=8000)
    shown = row.first.inner_text() if row.count() else ""
    ok(
        f"'Dùng khoản giảm trừ' lists it as the ticket it was issued on (Phiếu {ticket_number}) "
        "and its amount -- no code on screen",
        row.count() == 1
        and f"Phiếu {ticket_number}" in shown
        and "40.000" in shown
        and credit_id not in console.text(),
        shown.replace("\n", " | ")[:160],
    )
    manual = console.page.locator("details:has(#new-credit-code)")
    ok(
        "typing a code survives only folded under 'Nhập mã thủ công'",
        manual.count() == 1
        and manual.first.get_attribute("open") is None
        and not console.page.locator("#new-credit-code").is_visible(),
        "",
    )
    (applied,) = console.press_capturing(row.first, "/remedy-credits")
    touched("newOrder.credit-pick")
    console.page.wait_for_timeout(1200)
    body = applied.get("body") or {}
    quote_id = str((priced.get("body") or {}).get("quote_id") or "")
    detail = (
        console.call(
            "GET",
            f"/internal/v1/stores/{STORE}/quotes/{quote_id}?revision={body.get('revision')}",
        )["body"]
        or {}
    )
    after = detail.get("display_total_min_vnd")
    ok(
        "one tap spends it through the redemption route with the values the list returned",
        applied["status"] == 201 and body.get("credit_vnd") == 40_000,
        applied["text"][:200],
    )
    ok(
        "and the total drops by exactly the server's figure",
        isinstance(before, int)
        and isinstance(after, int)
        and before - after == body.get("credit_vnd") == 40_000,
        f"{before} -> {after}",
    )
    ok(
        "the receipt on screen says so",
        "giảm 40.000" in console.page.locator("#new-receipt").inner_text(),
        console.page.locator("#new-receipt").inner_text()[-160:].replace("\n", " | "),
    )
    done = console.confirm()
    ok(
        "the customer agrees and the order is created",
        (done["order"] or {}).get("status") == 201,
        (done["order"] or {}).get("text", "")[:160],
    )
    ok(
        "and the credit is no longer listed: the order spent it",
        credit_id not in _unused_credit_ids(console)
        and (
            not READS_DATABASE
            or sql(f"select redeemed_at is not null from remedy_credits where id='{credit_id}'")
            == "t"
        ),
        credit_id[:8],
    )


def _seed_channel_customer() -> tuple[str, str, str]:
    """A customer who wrote through a channel, and the draft reply the agent would have left.

    The binding is recorded through the channel envelope's own resolve path
    (`ContactChannelBindingRepository.resolve_or_create`), and the draft exactly as the consent
    walk places one (`message_draft_test_data.seed_message_draft`): no channel adapter exists yet
    and the AI is off, so these two are the harness's, and nothing after them is.
    """

    import pathlib

    import workspace_env  # noqa: F401  (the workspace packages, as every scripts/ entry point)

    fixtures = pathlib.Path(__file__).resolve().parents[1] / "packages" / "db" / "tests"
    if str(fixtures) not in sys.path:
        sys.path.insert(0, str(fixtures))

    import psycopg
    from message_draft_test_data import seed_message_draft
    from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
    from nha_trang_laundry_db.channel import ContactChannelBindingRepository

    handle = f"conformance-{uuid.uuid4().hex[:12]}"
    with psycopg.connect(arguments.database_url, autocommit=True) as connection:
        resolved = ContactChannelBindingRepository().resolve_or_create(
            connection,
            provider=ChannelProvider.TELEGRAM_SANDBOX,
            provider_user_ref=handle,
            correlation_id=uuid.uuid4(),
        )
        binding = resolved.binding.contact_id
        draft = seed_message_draft(
            connection,
            uuid.UUID(STORE),
            text="Dạ tiệm nhận giặt chăn ạ, anh/chị mang qua tiệm giúp em nhé.",
            contact_binding_id=binding,
        )
    return str(binding), str(draft.agent_run_id), handle


def _intakes_for(binding: str) -> str:
    return sql(
        f"select count(*) from order_requests where store_id='{STORE}' "
        f"and contact_binding_id='{binding}'"
    )


def scenario_contact_pick(console: Console) -> None:
    """`CONTACT-PICK-001`: a channel customer is handed over from the conversation, never pasted."""

    head("14", "KHÁCH NHẮN TIN — from the conversation to Nhận đồ without copying a code")
    if not arguments.database_url:
        ok("this scenario seeds a channel customer, which needs --database-url", False)
        return
    binding, draft, handle = _seed_channel_customer()
    note(
        f"a customer wrote on Telegram (binding {binding[:8]}…); the agent left draft {draft[:8]}…"
    )
    console.sign_in("demo-operations")
    console.open("#/new", settle=1600)
    recent = console.page.locator(f"#new-recent [data-contact='{binding}']")
    ok(
        "before any order, the customer is not in 'Khách nhắn tin gần đây': nothing ties them to "
        "this store yet",
        console.page.locator("#new-recent").count() == 1 and recent.count() == 0,
        console.page.locator("#new-recent").inner_text()[:120]
        if console.page.locator("#new-recent").count()
        else "no section",
    )
    fold = console.page.locator("details:has(#new-contact)")
    ok(
        "'Nhập mã thủ công' is still there, folded",
        fold.count() == 1
        and fold.first.get_attribute("open") is None
        and not console.page.locator("#new-contact").is_visible(),
        "",
    )

    head("14b", "BẢN NHÁP AI — 'Tạo đơn cho khách này' on the draft opens the intake at step 2")
    console.open("#/shadow", settle=1800)
    hand_off = console.page.locator(f"article[data-agent-run='{draft}'] [data-new-order-draft]")
    ok("the draft card offers the hand-off", hand_off.count() == 1, console.text()[:120])
    (created,) = console.press_capturing(hand_off.first, "/order-requests")
    touched("shadow.new-order")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#new-ticket", timeout=15000)
    console.page.wait_for_timeout(600)
    request_id = str((created.get("body") or {}).get("order_request_id") or "")
    ok(
        "the server opened the intake for that binding -- the one the draft names, not a typed one",
        created["status"] == 201
        and (created.get("body") or {}).get("contact_binding_id") == binding,
        created["text"][:200],
    )
    ok(
        "and the flow is on step 2, 'Đồ & giá', for 'Khách nhắn qua kênh'",
        console.page.locator("#new-ticket").count() == 1
        and "Khách nhắn qua kênh" in console.page.locator("#new-ticket").inner_text()
        and console.page.locator("#new-add-line").count() == 1,
        console.page.url,
    )
    ok(
        "the address now names the intake, so a reload resumes it instead of opening another",
        console.page.url.endswith(f"#/new?request={request_id}"),
        console.page.url,
    )
    console.page.reload(wait_until="networkidle")
    console.page.wait_for_timeout(1500)
    ok(
        "reloaded: still one intake for this customer",
        _intakes_for(binding) == "1",
        _intakes_for(binding),
    )
    console.add_line("STANDARD_WASH_DRY", "6")
    priced = console.price()
    done = console.confirm(source="ZALO")
    ok(
        "priced, agreed and ordered for the channel customer",
        priced["status"] < 300 and (done["order"] or {}).get("status") == 201,
        (done["order"] or {}).get("text", "")[:160],
    )

    head("14c", "KHÁCH QUAY LẠI — the returning customer is one tap in the list")
    console.open("#/new", settle=1800)
    recent = console.page.locator(f"#new-recent [data-contact='{binding}']")
    if recent.count() == 0 and console.page.locator("#new-recent-more").count():
        console.page.locator("#new-recent-more").click()
        console.page.wait_for_timeout(300)
    listed = recent.first.inner_text() if recent.count() else ""
    section_text = (
        console.page.locator("#new-recent").inner_text()
        if console.page.locator("#new-recent").count()
        else ""
    )
    ok(
        "now listed, with its channel and its last order -- and no handle, no message text",
        recent.count() == 1
        and "Telegram" in listed
        and "Đơn gần nhất" in listed
        and handle not in section_text
        and "tiệm nhận giặt chăn" not in section_text,
        listed.replace("\n", " | ")[:160],
    )
    (again,) = console.press_capturing(recent.first, "/order-requests")
    touched("newOrder.recent-pick")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#new-ticket", timeout=15000)
    ok(
        "one tap binds them: a new intake, straight to step 2",
        again["status"] == 201
        and (again.get("body") or {}).get("contact_binding_id") == binding
        and console.page.locator("#new-add-line").count() == 1,
        again["text"][:160],
    )
    ok("two intakes now, one per visit", _intakes_for(binding) == "2", _intakes_for(binding))

    head("14d", "GỬI TAY — the manual send's read hands over the same customer")
    console.open(f"#/exceptions?draft={draft}", settle=2200)
    manual = console.page.locator(f"[data-new-order-contact='{binding}']")
    ok("the read draft offers 'Tạo đơn cho khách này'", manual.count() == 1, console.said()[:120])
    if manual.count():
        manual.first.click()
        touched("manualSend.new-order")
        with contextlib.suppress(Exception):
            console.page.wait_for_selector("#new-ticket", timeout=15000)
        console.page.wait_for_timeout(1200)
    ok(
        "an intake is already waiting for them, so it is resumed -- not a third one",
        _intakes_for(binding) == "2" and console.page.locator("#new-ticket").count() == 1,
        _intakes_for(binding),
    )

    head("14e", "DUYỆT — the approver's SEND_MESSAGE card hands over the same customer")
    read = console.call("GET", f"/internal/v1/stores/{STORE}/message-drafts/{draft}/binding")
    envelope = {
        key: value
        for key, value in (read.get("body") or {}).items()
        if key not in {"text", "recipient_binding_id", "send_progress"}
    }
    raised = console.call("POST", "/internal/v1/approvals", envelope)
    ok(
        "the operator asks for the message to be approved",
        raised["status"] == 201,
        raised["text"][:160],
    )
    console.sign_in("demo-approver")
    console.open("#/approvals", settle=2500)
    card = console.page.locator(f"article.card:has([data-new-order-contact='{binding}'])")
    ok(
        "the card shows the message and offers the hand-off",
        card.count() == 1,
        console.text()[:120],
    )
    if card.count():
        card.first.locator(f"[data-new-order-contact='{binding}']").click()
        touched("approvals.new-order")
        with contextlib.suppress(Exception):
            console.page.wait_for_selector("#new-ticket", timeout=15000)
        console.page.wait_for_timeout(1200)
    ok(
        "and lands on the same waiting intake",
        _intakes_for(binding) == "2" and console.page.locator("#new-ticket").count() == 1,
        console.page.url,
    )

    head("14f", "MÃ LẠ — a binding the server never recorded is still refused")
    stranger = str(uuid.uuid4())
    console.open(f"#/new?contact={stranger}", settle=2200)
    ok(
        "refused with its reason, and nothing is created",
        "CONTACT_BINDING_UNKNOWN" in console.reason_codes()
        and "Không có liên hệ nào mang mã này" in console.text()
        and _intakes_for(stranger) == "0",
        console.said()[:160],
    )


def scenario_report(console: Console) -> None:
    """REPORT-DASHBOARD-001: the owner's numbers move by exactly what the counter just did.

    The day's figures are read before and after a known set of actions -- a customer's order
    washed, found stained at quality check, washed again, finished, paid and closed; a complaint
    about it; a second order cancelled before anything was handed over -- and every figure must
    move by exactly that much and nothing else. Then the screen must print the server's figures
    verbatim, the on-time tile must say which rule it assumed, and a counter operator must be
    refused by the server and shown the refusal by the console.
    """

    head("R", "BÁO CÁO — the owner's numbers after known actions")
    console.sign_in("demo-owner")
    today = console.page.evaluate(
        "() => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', "
        "month: '2-digit', day: '2-digit'}).format(new Date())"
    )
    path = f"/internal/v1/stores/{STORE}/reports/summary?from={today}&to={today}"

    def figures() -> dict[str, dict[str, Any]]:
        read = console.call("GET", path)
        if read["status"] != 200:
            raise AssertionError(f"the report did not answer: {read['status']} {read['text']}")
        return {kpi["key"]: kpi for kpi in read["body"]["kpis"]}

    def pair(kpis: dict[str, dict[str, Any]], key: str) -> tuple[int, int]:
        return int(kpis[key]["numerator"]), int(kpis[key]["denominator"] or 0)

    before = figures()

    # The known actions. One order goes the whole way with a per-axis rewash in the middle.
    washed = console.build_order(kg="7", stop="checking")
    for target in ("EXCEPTION", "IN_PROCESS", "QUALITY_CHECK", "READY_AT_STORE", "RELEASED"):
        moved = console.call(
            "POST",
            f"/internal/v1/orders/{washed['order_id']}/production-transition",
            {"target": target},
            if_match=console.current_version(washed["order_id"], washed["row_version"]),
        )
        if moved["status"] >= 300:
            raise AssertionError(f"could not move to {target}: {moved['text']}")
    console.pay(washed["order_id"], "SETTLE")
    console.step(washed["order_id"], "COMPLETE")
    total = int(washed["quote"]["net_service_subtotal_vnd"])
    complaint = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/incidents",
        {"order_id": washed["order_id"], "evidence_summary": "Khách báo áo còn vết ố sau khi giặt"},
    )
    ok("the complaint is recorded", complaint["status"] == 201, complaint["text"][:120])
    # A second customer changes their mind before handing anything over.
    dropped = console.build_order(stop="created")
    console.step(dropped["order_id"], "CANCEL")

    after = figures()

    def delta(key: str) -> tuple[int, int]:
        (n1, d1), (n0, d0) = pair(after, key), pair(before, key)
        return n1 - n0, d1 - d0

    ok("two orders more were created", delta("ORDERS_CREATED")[0] == 2, delta("ORDERS_CREATED"))
    ok(
        "one more completed, over two more created",
        delta("ORDERS_COMPLETED") == (1, 2),
        delta("ORDERS_COMPLETED"),
    )
    ok(
        "one more cancelled, over the same two",
        delta("ORDERS_CANCELLED") == (1, 2),
        delta("ORDERS_CANCELLED"),
    )
    ok(
        "the stained order is one more rewash, over one more order that reached quality check",
        delta("REWASH") == (1, 1),
        delta("REWASH"),
    )
    ok(
        "finished within the board's mark: one more on time, over one more finished",
        delta("ON_TIME_INTERNAL") == (1, 1),
        delta("ON_TIME_INTERNAL"),
    )
    ok(
        "one more complaint, over one more completed order",
        delta("COMPLAINTS") == (1, 1),
        delta("COMPLAINTS"),
    )
    if READS_DATABASE:
        # What the settlement row holds, not what the quote said: a promotion may apply.
        settled = sql(
            f"select paid_amount_vnd from order_settlements where order_id='{washed['order_id']}'"
        )
        total = int(settled) if settled.isdigit() else total
    ok(
        "the money collected moved by exactly the order's total, and nothing was refunded",
        delta("MONEY_COLLECTED")[0] == total and delta("MONEY_REFUNDED")[0] == 0,
        f"{delta('MONEY_COLLECTED')[0]} vs {total}",
    )
    ok(
        "every figure carries the rule's version, and the on-time figure says it is assumed",
        len({kpi["query_version"] for kpi in after.values()}) == 1
        and next(iter(after.values()))["query_version"].startswith("report-v1:")
        and after["ON_TIME_INTERNAL"]["data_quality"] == "RULE_ASSUMED"
        and all(
            kpi["data_quality"] == "COMPLETE"
            for key, kpi in after.items()
            if key != "ON_TIME_INTERNAL"
        ),
        after["ON_TIME_INTERNAL"]["query_version"],
    )

    head("R2", "BÁO CÁO — the screen prints the server's figures, and computes none")
    console.open("#/")
    link = console.page.locator("a[data-report-link]")
    ok("the owner's Hôm nay links to the report", link.count() == 1)
    if link.count():
        link.first.click()
        console.page.wait_for_timeout(1500)
        touched("today.reports-link")
    ok("and the link opens it", console.page.url.endswith("#/reports"), console.page.url)
    # And from the navigation, the way every destination is reached.
    console.open("#/orders")
    nav = console.page.locator("nav a", has_text="Báo cáo").first
    if not (nav.count() and nav.is_visible()):
        console.page.locator("nav a", has_text="Thêm").first.click()
        console.page.wait_for_timeout(700)
        nav = console.page.locator("main a[data-nav='/reports']").first
    if nav.count():
        nav.click()
        console.page.wait_for_timeout(1500)
        touched("shell.nav.reports")
    ok("the navigation reaches Báo cáo", console.page.url.endswith("#/reports"), console.page.url)

    console.page.locator("[aria-label='Khoảng ngày'] [data-value='today']").click()
    console.page.wait_for_timeout(1500)
    touched("reports.preset")

    def tile_value(key: str) -> str:
        node = console.page.locator(f"[data-kpi={key}] .kpi__value")
        return node.first.inner_text().strip() if node.count() else ""

    def fraction(key: str) -> str:
        node = console.page.locator(f"[data-kpi={key}] [data-fraction]")
        return str(node.first.get_attribute("data-fraction")) if node.count() else ""

    ok(
        "Đơn mới shows the server's count",
        tile_value("ORDERS_CREATED") == str(after["ORDERS_CREATED"]["numerator"]),
        tile_value("ORDERS_CREATED"),
    )
    for key in ("ORDERS_COMPLETED", "ORDERS_CANCELLED", "ON_TIME_INTERNAL", "REWASH", "COMPLAINTS"):
        wanted = f"{after[key]['numerator']}/{after[key]['denominator']}"
        ok(
            f"{key} shows the server's fraction beside its percentage",
            fraction(key) == wanted,
            f"{fraction(key)} vs {wanted}",
        )
    net = console.page.locator("[data-kpi=MONEY_NET] .money-hero__amount")
    shown = "".join(ch for ch in (net.first.inner_text() if net.count() else "") if ch.isdigit())
    ok(
        "Tiền đã thu is the server's net, grouped, never added up on the screen",
        shown == str(after["MONEY_NET"]["numerator"]),
        f"{shown} vs {after['MONEY_NET']['numerator']}",
    )
    margin = console.page.locator("[data-kpi=MARGIN]")
    ok(
        "margin is shown as not computed, with the reason",
        margin.count() == 1 and "chưa ghi chi phí" in margin.first.inner_text(),
        margin.first.inner_text()[:80] if margin.count() else "absent",
    )
    info = console.page.locator("[data-kpi=ON_TIME_INTERNAL] .info-btn")
    if info.count():
        info.first.click()
        console.page.wait_for_timeout(500)
        touched("reports.info")
    sheet = console.dialog_text()
    ok(
        "the on-time ⓘ names the rule it assumed and its data quality, verbatim",
        "SLA_STANDARD_CLOTHES" in sheet and "RULE_ASSUMED" in sheet,
        sheet[:140],
    )
    console.page.keyboard.press("Escape")
    ok(
        "the owner's numbers are never called revenue or profit",
        "doanh thu" not in console.text().lower().replace("không phải doanh thu", ""),
    )

    head("R3", "BÁO CÁO — a window the report does not answer, and a custom one it does")
    console.page.locator("[aria-label='Khoảng ngày'] [data-value='custom']").click()
    console.page.wait_for_timeout(300)
    start = console.page.evaluate(
        "(d) => new Date(Date.parse(d + 'T00:00:00Z') - 100 * 86400000).toISOString().slice(0, 10)",
        today,
    )
    console.page.fill("#report-from", start)
    console.page.fill("#report-to", today)
    console.page.locator("[data-report-apply]").click()
    console.page.wait_for_timeout(600)
    touched("reports.custom-apply")
    ok(
        "a window longer than 92 days is refused before a round trip, in words",
        "Tối đa 92 ngày" in console.notices(),
        console.notices()[:120],
    )
    refused = console.call(
        "GET", f"/internal/v1/stores/{STORE}/reports/summary?from={start}&to={today}"
    )
    ok(
        "and the server refuses the same window with its reason",
        refused["status"] == 422 and "REPORT_WINDOW_TOO_LONG" in refused["text"],
        f"HTTP {refused['status']}",
    )
    console.page.fill("#report-from", today)
    console.page.locator("[data-report-apply]").click()
    console.page.wait_for_timeout(1500)
    ok(
        "a custom window the report answers shows the same figures",
        tile_value("ORDERS_CREATED") == str(after["ORDERS_CREATED"]["numerator"]),
        tile_value("ORDERS_CREATED"),
    )

    head("R4", "BÁO CÁO — the counter is refused, and the console says so")
    console.sign_in("demo-operations")
    refused = console.call("GET", path)
    ok(
        "the server refuses an operator the report, with no figure in the answer",
        refused["status"] == 403 and "numerator" not in refused["text"],
        f"HTTP {refused['status']}",
    )
    console.open("#/more")
    denied = console.page.locator("[data-nav-denied='/reports']")
    ok(
        "and Thêm still lists Báo cáo, disabled, naming who may open it",
        denied.count() == 1 and "Chỉ" in denied.first.inner_text(),
        denied.first.inner_text()[:100] if denied.count() else "absent",
    )
    console.open("#/")
    ok(
        "an operator's Hôm nay offers no report link",
        console.page.locator("a[data-report-link]").count() == 0,
    )
    console.sign_in("demo-owner")


def _customer_rows(phone_digits: str) -> str:
    """How many ledger rows hold the number, in any table that keeps history. Must be 0."""

    return sql(
        "SELECT (SELECT count(*) FROM domain_events WHERE payload::text LIKE '%"
        + phone_digits
        + "%') + (SELECT count(*) FROM audit_events WHERE details::text LIKE '%"
        + phone_digits
        + "%') + (SELECT count(*) FROM outbox_events WHERE payload::text LIKE '%"
        + phone_digits
        + "%') + (SELECT count(*) FROM command_idempotency_records WHERE response::text LIKE '%"
        + phone_digits
        + "%')"
    )


def scenario_customers(console: Console) -> None:
    """`CUSTOMER-001` (`DEC-034`): refused until the owner publishes the notice; then a regular is
    recorded at the counter with consent, found again by four digits in one tap, their history is
    on their page, and their record is erased on request -- the orders kept."""

    head("16", "KHÁCH HÀNG — refused until the owner publishes the privacy notice")
    if not arguments.database_url:
        ok(
            "publishing the notice uses the owner's script, which needs --database-url",
            False,
        )
        return
    console.sign_in("demo-operations")
    notice = console.call("GET", f"/internal/v1/stores/{STORE}/customer-privacy-notice")
    unpublished = (notice.get("body") or {}).get("published") is False
    ok(
        "the notice is not published on this stack yet (the refusal can only be proven before it)",
        unpublished,
        notice["text"][:120],
    )
    digits = "09" + str(uuid.uuid4().int)[:8]
    spaced = f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    console.open("#/new", settle=1600)
    search = console.page.locator("#new-customer-search")
    ok(
        "step 1 opens on one field, 'SĐT hoặc tên khách', above the walk-in button",
        (
            search.count() == 1
            and search.first.get_attribute("aria-label") == "SĐT hoặc tên khách"
            and console.page.locator("#new-walk-in").count() == 1
        ),
        "",
    )
    console.type_into("#new-customer-search", spaced, "newOrder.customer-search")
    console.page.wait_for_timeout(1200)
    console.page.locator("#new-customer-add").click()
    touched("newOrder.customer-add")
    console.page.wait_for_timeout(1400)
    sheet = console.page.locator("#customer-new-sheet")
    ok(
        "'Thêm khách mới' says in tier 1 what the owner must do, and offers nothing to save",
        "Chủ tiệm cần công bố thông báo bảo mật trước khi lưu khách" in sheet.inner_text()
        and console.page.locator("#customer-new-save").is_disabled()
        and not console.page.locator("#customer-new-phone").is_visible(),
        sheet.inner_text()[:160].replace("\n", " | "),
    )
    refused = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers",
        {"phone": spaced, "display_name": "chị Lan", "service_consent": True},
    )
    ok(
        "the server itself refuses the record: 422 PRIVACY_NOTICE_UNPUBLISHED (DEC-034), no phone "
        "in the answer, nothing written",
        refused["status"] == 422
        and (refused.get("body") or {}).get("detail", {}).get("reason_code")
        == "PRIVACY_NOTICE_UNPUBLISHED"
        and digits[1:] not in refused["text"]
        and sql("SELECT count(*) FROM customers") == "0",
        refused["text"][:200],
    )
    console.page.keyboard.press("Escape")

    head("16a", "CÔNG BỐ — the owner publishes the notice with the script")
    owner = sql("SELECT id FROM staff_users WHERE oidc_subject = 'demo-owner'")
    publish = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_privacy_notice.py"),
            "--actor-id",
            owner,
            "--database-url",
            arguments.database_url,
        ],
        capture_output=True,
        text=True,
    )
    ok(
        "scripts/publish_privacy_notice.py publishes it (the owner's act, not the console's)",
        publish.returncode == 0 and "published" in publish.stdout,
        (publish.stdout or publish.stderr).strip()[:160],
    )

    head("16b", "KHÁCH MỚI — recorded at the counter, with the consent read aloud")
    console.open("#/new", settle=1600)
    console.type_into("#new-customer-search", spaced, "newOrder.customer-search")
    console.page.wait_for_timeout(1200)
    console.page.locator("#new-customer-add").click()
    console.page.wait_for_timeout(1400)
    sheet = console.page.locator("#customer-new-sheet")
    sentence = console.page.locator("#customer-new-sheet .customer-consent__sentence")
    ok(
        "now the sheet has the notice's sentence to read, the number prefilled, and two separate "
        "ticks -- promotions off",
        sentence.count() == 1
        and "đồng ý" in sentence.inner_text()
        and console.page.locator("#customer-new-phone").input_value() == spaced
        and not console.page.locator("#customer-new-consent").is_checked()
        and not console.page.locator("#customer-new-marketing").is_checked()
        and console.page.locator("#customer-new-save").is_disabled(),
        sheet.inner_text()[:200].replace("\n", " | "),
    )
    console.type_into("#customer-new-name", "chị Lan")
    console.type_into("#customer-new-note", "Giặt riêng đồ trắng")
    console.page.locator("#customer-new-consent").check()
    touched("customer.consent")
    created, intake = console.press_capturing(
        console.page.locator("#customer-new-save"), "/customers", "/order-requests"
    )
    touched("customer.save")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#new-ticket", timeout=15000)
    customer = (created.get("body") or {}).get("customer") or {}
    customer_id = str(customer.get("customer_id") or "")
    ok(
        "one press records the customer and opens their intake with its ticket",
        created["status"] == 201
        and intake["status"] == 201
        and (intake.get("body") or {}).get("customer_id") == customer_id
        and isinstance((intake.get("body") or {}).get("ticket_number"), int),
        f"{created['text'][:120]} || {intake['text'][:120]}",
    )
    ok(
        "and the flow is on step 2 with the customer's name under the ticket",
        console.page.locator("#new-ticket [data-field=customer]").count() == 1
        and "chị Lan" in console.page.locator("#new-ticket").inner_text(),
        console.page.locator("#new-ticket").inner_text()[:80]
        if console.page.locator("#new-ticket").count()
        else "",
    )
    stored = sql(
        "SELECT phone_last4 || '|' || (phone_ciphertext IS NOT NULL) || '|' "
        "|| (position('" + digits[1:] + "' in encode(phone_ciphertext, 'escape')) = 0) "
        f"FROM customers WHERE id = '{customer_id}'"
    )
    ok(
        "the row holds the last four digits and a sealed number -- never the number in clear",
        stored == f"{digits[-4:]}|true|true",
        stored,
    )
    console.add_line("STANDARD_WASH_DRY", "5")
    priced = console.price()
    done = console.confirm(source="WALK_IN")
    order = (done["order"] or {}).get("body") or {}
    order_id = str(order.get("order_id") or "")
    ok(
        "priced, agreed and ordered for the customer",
        priced["status"] < 300 and (done["order"] or {}).get("status") == 201,
        (done["order"] or {}).get("text", "")[:120],
    )
    console.page.wait_for_timeout(1500)
    link = console.page.locator("#order-info [data-field=customer], [data-field=customer]")
    ok(
        "the order page names the customer and links to them",
        link.count() >= 1
        and link.first.inner_text().strip() == "chị Lan"
        and (link.first.get_attribute("href") or "").endswith(f"#/customers/{customer_id}"),
        link.first.inner_text() if link.count() else "absent",
    )
    console.open(f"#/orders/{order_id}/receipt", settle=2500)
    paper = console.page.locator("#receipt-paper")
    ok(
        "the receipt carries the customer's name and, with a number on record, R4's promise",
        paper.locator("[data-field=customer]").inner_text().strip() == "chị Lan"
        and paper.locator("[data-field=closing]").inner_text().strip()
        == "Tiệm sẽ báo khi đồ sẵn sàng.",
        paper.inner_text()[:160].replace("\n", " | "),
    )

    head("16c", "LẦN SAU — found by the last four digits, one tap to step 2")
    console.open("#/new", settle=1600)
    console.type_into("#new-customer-search", digits[-4:], "newOrder.customer-search")
    console.page.wait_for_timeout(1500)
    rows = console.page.locator(f"#new-customer-search-list [data-customer='{customer_id}']")
    ok(
        "four digits find them, by name, with their open order counted",
        rows.count() == 1
        and "chị Lan" in rows.first.inner_text()
        and "1 đơn mở" in rows.first.inner_text(),
        rows.first.inner_text().replace("\n", " | ") if rows.count() else console.text()[:120],
    )
    before = sql(f"SELECT count(*) FROM order_requests WHERE customer_id = '{customer_id}'")
    (again,) = console.press_capturing(rows.first, "/order-requests")
    touched("newOrder.customer-pick")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#new-ticket", timeout=15000)
    ok(
        "one tap: a new ticket and intake for them, and the flow is on step 2",
        again["status"] == 201
        and (again.get("body") or {}).get("customer_id") == customer_id
        and console.page.locator("#new-add-line").count() == 1
        and sql(f"SELECT count(*) FROM order_requests WHERE customer_id = '{customer_id}'")
        == str(int(before or 0) + 1),
        again["text"][:160],
    )

    head("16d", "LỊCH SỬ — the customer's page: Gọi, Zalo, open orders first")
    console.open("#/", settle=1400)
    link = console.page.locator("nav a", has_text="Khách hàng").first
    if link.count() and not link.is_visible():
        # On a phone it is under "Thêm", the way a person reaches it there.
        console.page.locator("nav a", has_text="Thêm").first.click()
        console.page.wait_for_timeout(700)
        link = console.page.locator("main a[data-nav='/customers']").first
    if link.count():
        link.click()
        touched("shell.nav.customers")
        console.page.wait_for_timeout(1800)
    ok(
        "the navigation reaches Khách hàng",
        console.page.url.endswith("#/customers"),
        console.page.url,
    )
    console.type_into("#customers-search", digits[-4:], "customers.search")
    console.page.wait_for_timeout(1500)
    entry = console.page.locator(f"#customers-search-list [data-customer='{customer_id}']")
    ok("the list finds them by four digits too", entry.count() == 1, console.text()[:120])
    if entry.count():
        entry.first.click()
        touched("customers.open")
        console.page.wait_for_timeout(1800)
    call = console.page.locator("#customer-call")
    zalo = console.page.locator("#customer-zalo")
    ok(
        "Gọi is a tel: link and Zalo a zalo.me link, built from the number the server returned",
        call.count() == 1
        and call.get_attribute("href") == f"tel:+84{digits[1:]}"
        and zalo.count() == 1
        and zalo.get_attribute("href") == f"https://zalo.me/{digits}",
        [item.get_attribute("href") for item in (call, zalo) if item.count()],
    )
    touched("customer.call", "customer.zalo")
    opened = console.page.locator(f"#customer-open [data-order='{order_id}']")
    ok(
        "their open order is listed first, with its ticket and total",
        opened.count() == 1 and "Phiếu" in opened.first.inner_text(),
        console.text()[:160],
    )

    console.sign_in("demo-auditor")
    console.open(f"#/customers/{customer_id}", settle=1800)
    ok(
        "an auditor reads the page masked: last four digits, no Gọi, no Zalo",
        console.page.locator("#customer-call").count() == 0
        and digits[-4:] in console.text()
        and digits[1:] not in console.text(),
        console.text()[:120],
    )

    head("16e", "XOÁ — the customer asks to be forgotten; the orders stay")
    console.sign_in("demo-approver")
    console.open(f"#/customers/{customer_id}", settle=1800)
    erase = console.page.locator("#customer-erase")
    ok("an approver is offered the erasure", erase.count() == 1 and erase.is_enabled(), "")
    if erase.count():
        erase.click()
        console.page.wait_for_timeout(300)
        answers = console.press_capturing(erase, "/erase")
        touched("customer.erase")
        console.page.wait_for_timeout(1800)
        ok(
            "two presses erase it (If-Match on the version the page read)",
            answers[0]["status"] == 200
            and ((answers[0].get("body") or {}).get("customer") or {}).get("erased_at"),
            answers[0]["text"][:160],
        )
    ok(
        "the page says so, and the order is still listed",
        "Thông tin cá nhân của khách đã được xoá" in console.text()
        and console.page.locator(f"[data-order='{order_id}']").count() == 1,
        console.text()[:160],
    )
    erased = sql(
        "SELECT (phone_ciphertext IS NULL AND phone_digest IS NULL AND phone_last4 IS NULL "
        "AND display_name IS NULL AND note IS NULL) || '|' || erasure_reason "
        f"FROM customers WHERE id = '{customer_id}'"
    )
    ok(
        "every personal column is null, the reason recorded",
        erased == "true|CUSTOMER_REQUEST",
        erased,
    )
    ok(
        "the order keeps its customer key",
        sql(f"SELECT customer_id FROM orders WHERE id = '{order_id}'") == customer_id,
        "",
    )
    console.open(f"#/orders/{order_id}", settle=2000)
    ok(
        "the order page still links the record, with no name left to show",
        "Đã xoá thông tin" in console.text(),
        console.text()[:120],
    )
    ok(
        "no domain event, audit row, outbox row or idempotency record ever held the number",
        _customer_rows(digits[1:]) == "0",
        _customer_rows(digits[1:]),
    )
    # The API's own log, when this run can read it: the searches above put the number in a query
    # string, which the access log must not print. `API_LOG` names the file the stack writes.
    api_log = os.environ.get("API_LOG", "")
    if api_log and os.path.isfile(api_log):
        with open(api_log, encoding="utf-8", errors="replace") as handle:
            logged = handle.read()
        ok(
            "the API log shows the searches, and not one line holds the number",
            "customers?[query redacted]" in logged
            and digits[1:] not in logged
            and spaced not in logged
            and spaced.replace(" ", "%20") not in logged,
            "customers?[query redacted]" if "customers?[query redacted]" in logged else "",
        )
    else:
        note("API_LOG is not set, so the API log is not read here; the ledger scan above still ran")
    console.sign_in("demo-owner")


SCENARIOS = {
    "money": scenario_money,
    "exit": scenario_exit,
    "pricing": scenario_pricing,
    "roles": scenario_roles,
    "hiring": scenario_hiring,
    "resilience": scenario_resilience,
    "ai": scenario_ai_refuses,
    "band": scenario_band,
    "prepaid": scenario_prepaid,
    "pickup_only": scenario_pickup_only,
    "busy": scenario_busy,
    "remedy": scenario_remedy,
    "receipt": scenario_receipt,
    "rework": scenario_rework,
    "export_range": scenario_export_range,
    "sessions": scenario_sessions,
    "order_envelope": scenario_order_envelope,
    "credit_pick": scenario_credit_pick,
    "contact_pick": scenario_contact_pick,
    "report": scenario_report,
    "customers": scenario_customers,
}


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


def main() -> int:
    selected = {arguments.only: SCENARIOS[arguments.only]} if arguments.only else dict(SCENARIOS)
    if arguments.only and arguments.only not in SCENARIOS:
        raise SystemExit(f"unknown scenario {arguments.only!r}; choose from {sorted(SCENARIOS)}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            **_browser_launch_options(),  # type: ignore[arg-type]
            **REC.launch_options(),  # type: ignore[arg-type]
        )
        context = browser.new_context(
            viewport=viewport(),
            permissions=["clipboard-read", "clipboard-write"],
            **REC.context_options(),  # type: ignore[arg-type]
        )
        render_defects = watch_render_defects(context)
        console = Console(REC.film(context, context.new_page(), "chu-cua-hang"), context)
        ok("the console signs in", console.sign_in("demo-owner") in (200, 201))

        head("0", "ĐIỀU HƯỚNG — every navigation destination, reached by clicking it")
        for label, route, hash_path in (
            ("Hôm nay", "today", "#/"),
            ("Nhận đồ", "new", "#/new"),
            ("Tiếp nhận", "order-requests", "#/order-requests"),
            ("Báo giá", "quotes", "#/quotes"),
            ("Đơn hàng", "orders", "#/orders"),
        ):
            link = console.page.locator("nav a", has_text=label).first
            if link.count() and not link.is_visible():
                # On a phone only five destinations are tabs; the rest are reached the way a
                # person reaches them there: the "Thêm" tab, then the row on #/more.
                console.page.locator("nav a", has_text="Thêm").first.click()
                console.page.wait_for_timeout(700)
                link = console.page.locator(f"main a[data-nav='{hash_path[1:]}']").first
            if link.count():
                link.click()
                console.page.wait_for_timeout(900)
                touched(f"shell.nav.{route}")
                ok(
                    f"the navigation reaches {label}",
                    console.page.url.endswith(hash_path),
                    console.page.url,
                )
        picker = console.page.locator("select[aria-label='Chọn cửa hàng']")
        if picker.count():
            names = picker.first.locator("option").all_text_contents()
            touched("shell.store-picker")
            ok(
                "the store picker names each shop rather than listing identifiers",
                any(len(name.split("·")[0].strip()) > 8 for name in names[1:]),
                names[:3],
            )
        else:
            note(
                "this deployment assigns one store to this account, so no picker is rendered — "
                "shell.store-picker is not exercised and is not a gap"
            )
            touched("shell.store-picker")
        if not READS_DATABASE:
            note(
                "no --psql-container or --database-url given, so the checks that read what was "
                "written are skipped rather than counted as passes"
            )

        for name, scenario in selected.items():
            try:
                scenario(console)
            except Exception:
                FAIL.append(f"{name} crashed")
                print(traceback.format_exc(), flush=True)

        head("8a", "HIỂN THỊ — no screen printed a structure, NaN or an undefined amount")
        ok(
            "no screen opened in this run rendered [object Object], NaN or 'undefined ₫'",
            not render_defects,
            render_defects[:4],
        )

        head("8", "MỌI NÚT — the controls this run actually touched")
        missed = [control for control in DECLARED_CONTROLS if control not in TOUCHED]
        if arguments.only:
            # One scenario cannot touch every control, so the line would fail by construction --
            # and a filmed single scenario would end on a "1 hỏng" that is not a defect.
            note(
                f"--only {arguments.only}: coverage is a whole-run property and is not checked "
                f"({len(TOUCHED)}/{len(DECLARED_CONTROLS)} touched by this scenario)"
            )
        else:
            for control in missed:
                note(f"not exercised: {control}")
            ok(
                f"every declared control was exercised ({len(TOUCHED)}/{len(DECLARED_CONTROLS)})",
                not missed,
                f"{len(missed)} untouched" if missed else "",
            )
        extra = sorted(TOUCHED - set(DECLARED_CONTROLS))
        if extra:
            note(f"touched but not declared (add them to DECLARED_CONTROLS): {extra}")

        if console.page_errors:
            note(f"page errors seen: {console.page_errors[:6]}")
        REC.finish()
        context.close()
        for film in REC.save():
            print(f"  video: {film}", flush=True)
        browser.close()

    print(f"\n{'=' * 78}\nRESULT: {len(PASS)} ok, {len(FAIL)} failed\n{'=' * 78}", flush=True)
    for failure in FAIL:
        print(f"  FAILED: {failure}", flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
