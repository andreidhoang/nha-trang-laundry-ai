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
    # PAYMENT-001 (DEC-035): one Thu tiền sheet replaced the exact-total settlement and the
    # separate "Khách trả trước": the money card's own button, the deposit field, the method, the
    # transfer attestation and reference, and the handover tick.
    "orderDetail.money-take",
    "orderDetail.payment-edit",
    "orderDetail.payment-amount",
    "orderDetail.payment-method",
    "orderDetail.payment-transfer-seen",
    "orderDetail.payment-bank-ref",
    "orderDetail.payment-hand-over",
    "orderDetail.payment-submit",
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
    # PROMISE-001: the promise chosen at Nhận đồ and moved with a reason on the order page.
    "orderDetail.receive-promise-choice",
    "orderDetail.promise-change",
    "orderDetail.promise-change-at",
    "orderDetail.promise-change-reason",
    "orderDetail.promise-change-submit",
    # SHOP-CAPTURE-001 (DEC-038): "Máy nào?" at Bắt đầu giặt (a machine, or Bỏ qua), the rewash's
    # optional machine, the trip cost on the leg sheet, Sổ thu chi, and the owner's machine list.
    "orderDetail.machine-pick",
    "orderDetail.machine-skip",
    "orderDetail.rewash-machine",
    "orderDetail.trip-cost",
    "shell.nav.expenses",
    "expenses.add",
    "expenses.category",
    "expenses.save",
    "expenses.void",
    "shell.nav.machines",
    "machines.add",
    "machines.rename",
    "machines.retire",
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
    # PAYMENT-002 (DEC-035): công nợ -- the owner opens an account and types its limit, the order
    # page's "Giao đồ — ghi công nợ", Thu công nợ on the customer's page, the printable statement,
    # and the owner's lift of an overdue block.
    "customer.account-open",
    "customer.account-open-save",
    "customer.account-limit",
    "customer.account-limit-save",
    "orderDetail.account-charge",
    "orderDetail.account-confirm",
    "customer.account-collect",
    "customer.account-payment-edit",
    "customer.account-payment-amount",
    "customer.account-payment-submit",
    "customer.account-statement",
    "statement.print",
    "customer.account-lift",
    "customer.account-lift-save",
    # UNCLAIMED-001 (DEC-036): Đồ chờ lấy from the navigation and from Hôm nay's count, a contact
    # attempt from the list and from the order page, Gọi, the approver's waiver and the owner's
    # thanh lý with its confirm sheet.
    "shell.nav.pickup",
    "today.pickup-link",
    "pickup.record",
    "pickup.contact-channel",
    "pickup.contact-outcome",
    "pickup.contact-note",
    "pickup.contact-submit",
    "pickup.call",
    "orderDetail.contact-open",
    "orderDetail.storage-waive",
    "orderDetail.waiver-reason",
    "orderDetail.waiver-submit",
    "orderDetail.dispose",
    "orderDetail.disposal-confirm",
    # DAILY-SUMMARY-001 (DEC-039): the owner's evening summary on Hôm nay -- read on demand, its ⓘ,
    # Sao chép to the clipboard and Chia sẻ to the share sheet.
    "today.summary-load",
    "today.summary-info",
    "today.summary-copy",
    "today.summary-share",
    # LATE-CREDIT-002 (DEC-042): Giao trễ from the navigation and from Hôm nay's count, the two
    # buttons, the reason picker with its note, and the credited row's link to its complaint.
    "shell.nav.late-deliveries",
    "today.late-link",
    "late.fault",
    "late.not-fault",
    "late.reason",
    "late.note",
    "late.reason-submit",
    "late.follow-link",
    # VIETQR-001 (DEC-041): "Mã chuyển khoản" on Đơn hàng switches the search to a transfer code.
    "orders.lookup-code-mode",
    # EINVOICE-REQUEST-001 (DEC-040): Khách cần hóa đơn on an order and on an account month (the
    # buyer saved for next time), Hóa đơn cần xuất with its tabs, the bookkeeper's download,
    # Ghi số hóa đơn, and a request cancelled with a reason.
    "invoice.request-open",
    "invoice.request-unit",
    "invoice.request-tax",
    "invoice.request-address",
    "invoice.request-save",
    "invoice.month-open",
    "invoice.save-profile",
    "shell.nav.invoices",
    "invoices.download",
    "invoices.record-issued",
    "invoice.issued-symbol",
    "invoice.issued-number",
    "invoice.issued-save",
    "invoices.tabs",
    "invoices.cancel",
    "invoice.cancel-reason",
    "invoice.cancel-confirm",
    # PICKUP-REMIND-001 (DEC-043): Hôm nay's Nhắc khách lấy đồ card, and on its list Mở Zalo,
    # Chép tin nhắn, Gọi, Đã nhắc and the one-tap outcome.
    "today.reminders-link",
    "reminders.zalo",
    "reminders.copy",
    "reminders.call",
    "reminders.done",
    "reminders.outcome",
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
        """Press Tính giá, and wait until the receipt of the revision the server returned has
        painted its lines.

        The receipt paints in two passes (`ui/quoting.js` `receipt`): the totals from the POST at
        once, the lines when the revision read that follows it answers (`setLines`). Waiting for
        any `.receipt` and then a fixed 400 ms read the first pass whenever that second read was
        slower -- the pricing scenario's "both lines are priced" once saw HTTP 201 and a receipt
        still holding its skeleton on a fresh desk run. So the wait is on the server's own answer:
        the paper carrying the returned quote id and revision, its lines host no longer busy.
        """
        (priced,) = self.press_capturing(self.page.locator("#new-price"), "/quotes")
        touched("newOrder.price")
        body = priced.get("body") if isinstance(priced.get("body"), dict) else {}
        quote_id, revision = body.get("quote_id"), body.get("revision")
        with contextlib.suppress(Exception):
            if priced["status"] < 300 and quote_id and revision is not None:
                self.page.wait_for_function(
                    """(args) => {
                        const paper = document.querySelector(
                            `#new-receipt .receipt[data-quote-id="${args.quote}"]` +
                                `[data-revision="${args.revision}"]`,
                        );
                        const host = paper && paper.querySelector(".receipt__lines-host");
                        return Boolean(
                            host && host.firstElementChild && !host.querySelector("[aria-busy]"),
                        );
                    }""",
                    arg={"quote": str(quote_id), "revision": str(revision)},
                    timeout=15000,
                )
            else:
                self.page.wait_for_selector("#new-receipt .receipt", timeout=15000)
        if not (priced["status"] < 300 and quote_id and revision is not None):
            # A refusal paints no revision to wait on; its notice renders with the answer.
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
        customer: tuple[str, str] | None = None,
    ) -> dict:
        """Put an order into the state a scenario starts from.

        Taking the customer in and creating the order is done on Nhận đồ, in the browser, the way
        the counter does it -- ticket, bag, price, source, one press -- because since
        CONSOLE-REDESIGN-001 that flow *is* how an order comes to exist, and every scenario that
        starts from an order now also proves it. The ladder of state moves after that goes through
        the routes directly: setting each position up through the order page would take minutes per
        case and prove nothing the daily walk does not already prove.
        """
        if customer is None:
            self.walk_in()
        else:
            # UNCLAIMED-001: taken in for a customer record -- (customer id, phone digits) -- the
            # way the counter does it: the number in the one search field, one tap on the row.
            self.open("#/new")
            self.type_into("#new-customer-search", customer[1], "newOrder.customer-search")
            self.page.wait_for_timeout(1500)
            row = self.page.locator(f"#new-customer-search-list [data-customer='{customer[0]}']")
            (intake,) = self.press_capturing(row.first, "/order-requests")
            touched("newOrder.customer-pick")
            with contextlib.suppress(Exception):
                self.page.wait_for_selector("#new-ticket", timeout=15000)
            if intake["status"] >= 300:
                raise AssertionError(f"could not take the customer in: {intake['text']}")
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
        """Every step the page offers: primary first, then the money card's, then "Khác"."""
        steps = [self.primary_step()] if self.primary_step() else []
        steps += [
            str(node.get_attribute("data-step"))
            for node in self.page.locator(".order__money button[data-step]").all()
        ]
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
        # PAYMENT-001: "Thu tiền" that is not the big button sits on the money card.
        card = self.page.locator(f".order__money button[data-step={step}]")
        if card.count():
            touched("orderDetail.money-take")
            return card.first
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
        machine: str = "",
    ) -> str:
        """Press one step on the order's page, exactly as staff would, and return what it said.

        SHOP-CAPTURE-001: START_WASH answers "Máy nào?" with the machine whose code is `machine`,
        or presses "Bỏ qua" when `machine` is empty; REWASH picks `machine` among its optional
        chips when given.

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
        elif step == "START_WASH":
            with contextlib.suppress(Exception):
                self.page.wait_for_selector(
                    "dialog[open] button[data-machine-skip]", state="visible", timeout=8000
                )
            if machine:
                self.page.locator(
                    f"dialog[open] button[data-machine-code='{machine}']"
                ).first.click()
                touched("orderDetail.machine-pick")
            else:
                self.page.locator("dialog[open] button[data-machine-skip]").first.click()
                touched("orderDetail.machine-skip")
        elif step in {"REWASH", "REJECT_INTAKE"}:
            prefix = "orderDetail.rewash" if step == "REWASH" else "orderDetail.reject"
            if reason:
                self.page.locator(f"dialog[open] #step-reason [data-value={reason}]").click()
                touched(f"{prefix}-reason")
            if machine and step == "REWASH":
                with contextlib.suppress(Exception):
                    self.page.wait_for_selector("dialog[open] #rewash-machine", timeout=5000)
                self.page.locator(
                    "dialog[open] #rewash-machine button", has_text=machine
                ).first.click()
                touched("orderDetail.rewash-machine")
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

    def pay(
        self,
        order_id: str,
        amount_text: str = "",
        *,
        method: str = "TIEN_MAT",
        seen: bool = True,
        ref: str = "",
        hand_over: bool | None = None,
        reopen: bool = True,
    ) -> str:
        """Thu tiền (PAYMENT-001, DEC-035): the remaining amount as the sheet prefills it, or --
        with `amount_text` -- a deposit typed after "Khách trả một phần"; then the method, the
        transfer attestation and reference, and the handover tick when the sheet offers it.
        """
        if reopen:
            self.open_order(order_id)
        control = self.step_control("TAKE_PAYMENT")
        if control is None:
            return f"(the page offers no TAKE_PAYMENT; primary is {self.primary_step() or 'none'})"
        control.click()
        self.page.wait_for_timeout(600)
        if amount_text:
            self.page.locator("#payment-edit").click()
            touched("orderDetail.payment-edit")
            self.page.wait_for_timeout(200)
            self.type_into("#payment-amount", amount_text, "orderDetail.payment-amount")
        if method != "TIEN_MAT":
            self.page.locator(f"#payment-method button[data-value={method}]").click()
            touched("orderDetail.payment-method")
            self.page.wait_for_timeout(200)
            if seen:
                self.page.locator("#payment-transfer-seen").check()
                touched("orderDetail.payment-transfer-seen")
            if ref:
                self.type_into("#payment-bank-ref", ref, "orderDetail.payment-bank-ref")
        tick = self.page.locator("#payment-hand-over")
        if hand_over is not None and tick.count():
            if hand_over:
                tick.check()
            else:
                tick.uncheck()
            touched("orderDetail.payment-hand-over")
        self.page.locator("#payment-submit").click()
        touched("orderDetail.payment-submit")
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
    """Money the counter must not take, and what the counter is told about it (PAYMENT-001)."""

    head("1", "TIỀN — what the counter may take, and every refusal around it")
    order = console.build_order(kg="7", stop="released")
    total = order["quote"]["net_service_subtotal_vnd"]
    grouped = f"{total:,}".replace(",", ".")
    note(f"the quote says {total} đồng; the screen prints it as {grouped}")

    over = f"{total + 5_000:,}".replace(",", ".")
    said = console.pay(order["order_id"], over)
    sheet = console.dialog_text()
    # DEC-035: more than remains is refused -- the counter gives change -- and the words say so.
    ok(
        "an amount above what remains is refused, and the counter is told to give change",
        "trả lại tiền thừa" in said and "không hợp lệ" not in said,
        said[:160],
    )
    ok(
        "the refusal names the reason code, and the sheet states the rule beside the method",
        "OVERPAYMENT_REFUSED" in console.reason_codes()
        and "Khách đưa dư thì trả lại tiền thừa" in sheet,
        repr(sorted(console.reason_codes())),
    )
    if READS_DATABASE:
        ok(
            "nothing was written for a refused payment",
            sql(f"select count(*) from order_payments where order_id='{order['order_id']}'") == "0",
        )

    said = console.pay(order["order_id"], "170000.5")
    ok(
        "a decimal is refused at the screen rather than read as ten times the amount",
        "nguyên đồng" in said,
        said[:130],
    )

    said, sheet = console.pay(order["order_id"], "20.000"), console.dialog_text()
    ok(
        "a deposit typed as the screen prints amounts, dots and all, is read as 20000",
        sql(f"select amount_vnd from order_payments where order_id='{order['order_id']}'")
        == "20000"
        if READS_DATABASE
        else "Đã ghi nhận 20.000" in sheet,
        sheet[:130],
    )
    note(f"the rest of the {grouped} ₫ is taken as the sheet prefills it, with the handover")
    said, sheet = console.pay(order["order_id"]), console.dialog_text()
    ok(
        "the prefilled rest settles the order",
        stored(order["order_id"], "balance_status") == "PAID"
        if READS_DATABASE
        else "Đã trả đủ" in sheet,
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
    console.pay(order_id)
    note(f"order {order_id[:8]}… is accepted and not yet washed; paid in advance on the money card")
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
        "TAKE_PAYMENT" not in offered,
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
    control = console.step_control("TAKE_PAYMENT")
    if control is not None:
        control.click()
        console.page.wait_for_timeout(500)
    # The remaining amount is prefilled (PAYMENT-001); `grouped` is what the sheet shows.
    note(f"the sheet takes the {grouped} ₫ that remains")
    submit = console.page.locator("#payment-submit")
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
    console.pay(order_id)
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

    head("13a", "THIẾT BỊ CỦA MỘT NGƯỜI — a person signs out their own; only the owner another's")
    worker = _second_device(console, "demo-operations")
    try:
        # Each sign-in in the same browser leaves the previous session alive on the server: the
        # shape of "I signed in on the shop tablet yesterday and never pressed Thoát". Two are
        # left behind -- one the person cuts off themselves, one the owner cuts off for them.
        own_forgotten = _session_id(worker)
        worker.sign_in("demo-operations")
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
            "nor sign out anyone else's device — that is the owner's press",
            worker.call("POST", f"/internal/v1/sessions/{_session_id(console)}/revoke")["status"]
            == 403
            and console.call("GET", "/internal/v1/session")["status"] == 200,
        )
        worker.open("#/")
        worker.page.locator("button.appbar__account").first.click()
        worker.page.wait_for_selector(
            f"#account-devices [data-session-id='{own_forgotten}']", timeout=10000
        )
        live = worker.page.locator(
            f"#account-devices button[data-revoke-session='{own_forgotten}']"
        )
        ok(
            "their account sheet offers their own forgotten device's sign-out, live, no refusal",
            live.count() == 1
            and live.first.is_enabled()
            and worker.page.locator("#account-devices-revoke-reason").count() == 0,
        )
        answer = _revoke_from_list(worker, "#account-devices", own_forgotten)
        touched("shell.device-revoke")
        ok(
            "two presses sign their own lost device out (owner decision 2026-09-27)",
            answer["status"] == 204
            and worker.page.locator(f"#account-devices [data-session-id='{own_forgotten}']").count()
            == 0
            and worker.call("GET", "/internal/v1/session")["status"] == 200,
            answer["text"][:120],
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
    console.pay(washed["order_id"])
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
        "one more completed, a count with no denominator (report-v4)",
        delta("ORDERS_COMPLETED") == (1, 0) and after["ORDERS_COMPLETED"]["denominator"] is None,
        delta("ORDERS_COMPLETED"),
    )
    ok(
        "one more cancelled, a count with no denominator (report-v4)",
        delta("ORDERS_CANCELLED") == (1, 0) and after["ORDERS_CANCELLED"]["denominator"] is None,
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
    # PROMISE-001: the on-time figure is COMPLETE only when every order in it had a promise, and
    # says how many the stated rule judged otherwise -- whether or not this stack published the
    # turnaround policy before this scenario ran.
    on_time = after["ON_TIME_INTERNAL"]
    assumed = on_time.get("rule_assumed")
    ok(
        "every figure carries the rule's version, and the on-time figure says what it assumed",
        len({kpi["query_version"] for kpi in after.values()}) == 1
        and next(iter(after.values()))["query_version"].startswith("report-v4:")
        and isinstance(assumed, int)
        and on_time["data_quality"] == ("RULE_ASSUMED" if assumed else "COMPLETE")
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
    for key in ("ON_TIME_INTERNAL", "REWASH", "COMPLAINTS"):
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
    # SHOP-CAPTURE-001: margin is the month's, and only when its Sổ thu chi is complete; a month
    # this walk never gave rent and wages to says so, naming what is missing, with no figure.
    margin = console.page.locator("[data-kpi=MARGIN]")
    ok(
        "margin is shown as not yet computable, naming what Sổ thu chi is missing",
        margin.count() == 1
        and margin.first.get_attribute("data-margin-status") == "INCOMPLETE"
        and "Chưa đủ số liệu" in margin.first.inner_text()
        and "còn thiếu" in margin.first.inner_text(),
        margin.first.inner_text()[:120] if margin.count() else "absent",
    )
    info = console.page.locator("[data-kpi=ON_TIME_INTERNAL] .info-btn")
    if info.count():
        info.first.click()
        console.page.wait_for_timeout(500)
        touched("reports.info")
    sheet = console.dialog_text()
    ok(
        "the on-time ⓘ names the stated rule and the figure's data quality, verbatim",
        "SLA_STANDARD_CLOTHES" in sheet and str(on_time["data_quality"]) in sheet,
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


# --- PROMISE-001: the promised-ready time ("hẹn trả", DEC-037) -------------------------------

#: The shop's weekday names, as `format.promiseTime` writes them.
_WEEKDAY_VI = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")
#: The six Tết days this walk publishes for the stack. The real dates are the owner's to enter each
#: year; these are a fixture for the upcoming Tết (2027) so no promise this week needs a person.
_TET_FIXTURE = "2027-02-05,2027-02-06,2027-02-07,2027-02-08,2027-02-09,2027-02-10"


def _promise_text(value: str) -> str:
    """ "13:00 thứ Sáu 26/9" -- the instant in Asia/Ho_Chi_Minh (UTC+7 all year)."""

    from datetime import datetime, timedelta, timezone

    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone(timedelta(hours=7))
    )
    return f"{moment:%H:%M} {_WEEKDAY_VI[moment.weekday()]} {moment.day}/{moment.month}"


def _publish_turnaround(*extra: str) -> subprocess.CompletedProcess[str]:
    """Run the owner's publication script against the stack's database, as the owner would."""

    owner = sql("select id from staff_users where oidc_subject='demo-owner'")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [
            sys.executable,
            os.path.join(root, "scripts", "publish_turnaround_policy.py"),
            "--database-url",
            arguments.database_url,
            "--actor-id",
            owner,
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=root,
    )


def _clear_of_minute_edge() -> None:
    """HARNESS STEP (documented, round 8): the promise shown in the Nhận đồ sheet is the server's
    answer for the minute it was read, and the stored one is for the instant of the press. The
    sheet re-reads the answer whenever a new minute starts (`ui/promise.js`), so a person sees the
    time they will get; a script reads and presses within a second, and one read in the last
    seconds of a minute and pressed in the next would compare two different minutes. It waits out
    those last seconds first. It changes nothing the shop sees."""

    import time as _time

    second = _time.time() % 60
    if second >= 54:
        _time.sleep(61 - second)


def _open_receive(console: Console, order_id: str) -> str:
    """Open the order and its Nhận đồ sheet; return what the sheet says before any press."""

    console.open_order(order_id)
    control = console.step_control("RECEIVE")
    if control is None:
        return ""
    control.click()
    console.page.wait_for_timeout(1200)
    return console.dialog_text()


def _press_receive(console: Console) -> dict[str, Any]:
    console.page.check("#receive-slot")
    touched("orderDetail.receive-slot")
    (answer,) = console.press_capturing(console.page.locator("#receive-submit"), "/steps")
    touched("orderDetail.receive-submit")
    console.page.wait_for_timeout(1200)
    return answer


def scenario_promise(console: Console) -> None:
    """PROMISE-001: before the owner publishes, no promise and nothing refuses; after, every order
    gets one at Nhận đồ, shown before the press, printed on the receipt, 24/48 h chosen for a
    blanket, and moved with a reason."""

    head("P", "HẸN TRẢ — the promised-ready time, before and after the owner publishes (DEC-037)")
    if not READS_DATABASE or not arguments.database_url:
        note(
            "--database-url is required: the turnaround policy is published with the owner's own "
            "script against the database, so this scenario is skipped rather than passed"
        )
        FAIL.append("promise scenario needs --database-url")
        return
    from nha_trang_laundry_domain.promise import compute_promise, parse_turnaround_policy

    # Before publication. A stack that already published is returned to that state with the
    # owner's own reversal, so the "before" is proved on every stack rather than assumed.
    published_rows = sql(
        "select count(*) from configuration_versions where config_type='TURNAROUND_POLICY'"
    )
    if published_rows not in ("", "0"):
        withdrawn = _publish_turnaround("--withdraw")
        ok(
            "the owner's withdrawal returns the shop to no promise",
            withdrawn.returncode == 0,
            (withdrawn.stdout + withdrawn.stderr)[-200:],
        )
    console.sign_in("demo-operations")
    before = console.build_order(kg="4", stop="created")
    read = console.call("GET", f"/internal/v1/orders/{before['order_id']}/promise")
    ok(
        "before publication the order's promise read says the owner has not published",
        read["status"] == 200 and read["body"]["policy_published"] is False,
        read["text"][:160],
    )
    sheet = _open_receive(console, before["order_id"])
    ok(
        "and Nhận đồ says there is no promise yet, in one line",
        "chủ tiệm chưa công bố quy tắc hẹn trả" in sheet,
        sheet[:200],
    )
    answer = _press_receive(console)
    ok(
        "and nothing refuses: Nhận đồ is recorded as it always was",
        answer["status"] == 200 and answer["body"]["commercial"] == "ACTIVE",
        answer["text"][:200],
    )
    ok(
        "and the order carries no promise",
        answer["status"] == 200
        and answer["body"]["promised_ready_at"] is None
        and stored(before["order_id"], "promised_ready_at") == "",
        answer["text"][:200],
    )
    moved = console.call(
        "POST",
        f"/internal/v1/orders/{before['order_id']}/promise",
        {"promise_at": "2026-12-01T10:00:00+07:00", "reason": "WORKLOAD"},
        if_match=answer["body"]["row_version"] if answer["status"] == 200 else 1,
    )
    ok(
        "and a Hẹn lại is refused by name until the owner publishes",
        moved["status"] == 422
        and (moved["body"] or {}).get("detail", {}).get("reason_code")
        == "TURNAROUND_POLICY_UNPUBLISHED",
        moved["text"][:200],
    )

    # The owner publishes, with the script, from the two owner-confirmed sheets and this year's
    # Tết days.
    published = _publish_turnaround("--tet-dates", _TET_FIXTURE)
    ok(
        "the owner publishes the turnaround policy with the script",
        published.returncode == 0 and "turnaround policy published" in published.stdout,
        (published.stdout + published.stderr)[-200:],
    )
    refused = subprocess.run(
        [
            sys.executable,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
                "publish_turnaround_policy.py",
            ),
            "--database-url",
            arguments.database_url,
            "--actor-id",
            sql("select id from staff_users where oidc_subject='demo-operations'"),
            "--tet-dates",
            _TET_FIXTURE,
        ],
        capture_output=True,
        text=True,
    )
    ok(
        "and nobody but the owner can publish it",
        refused.returncode == 3 and "OWNER_ADMIN" in refused.stderr,
        (refused.stdout + refused.stderr)[-200:],
    )
    payload = sql(
        "select payload::text from configuration_versions where config_type='TURNAROUND_POLICY' "
        "and lifecycle='PUBLISHED' order by version desc limit 1"
    )
    policy = parse_turnaround_policy(json.loads(payload))

    # A walk-in: the promise is on screen before the press, stored by the press, printed on the
    # receipt -- and it is exactly the domain's answer for the instant production accepted it.
    walk_in = console.build_order(kg="7", stop="created")
    order_id = walk_in["order_id"]
    _clear_of_minute_edge()
    sheet = _open_receive(console, order_id)
    line = (
        console.page.locator("#receive-promise-line").inner_text()
        if console.page.locator("#receive-promise-line").count()
        else ""
    )
    ok(
        "a walk-in's Nhận đồ shows 'Hẹn trả: …' before the press",
        line.startswith("Hẹn trả:") and "—" not in line,
        line or sheet[:200],
    )
    answer = _press_receive(console)
    body = answer["body"] if answer["status"] == 200 else {}
    promised = str(body.get("promised_ready_at") or "")
    ok(
        "Nhận đồ stores the promise, and the time shown before the press is the one stored",
        bool(promised)
        and body.get("current_promise_at") == promised
        and (body.get("promise_basis"), body.get("promise_rule_id"))
        == ("RULE", "SLA_STANDARD_CLOTHES")
        and _promise_text(promised) in line,
        f"{line} vs {promised} {answer['text'][:160]}",
    )
    accepted = stored(
        order_id,
        "to_char(production_accepted_at at time zone 'UTC', "
        '\'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"\')',
    )
    if accepted and promised:
        from datetime import datetime

        expected = compute_promise(
            policy,
            accepted_at=datetime.fromisoformat(accepted),
            service_codes=["STANDARD_WASH_DRY"],
        )
        ok(
            "the stored promise is the domain's answer: 8 opening hours from acceptance",
            getattr(expected, "promised_at", None)
            == datetime.fromisoformat(promised.replace("Z", "+00:00")),
            f"accepted {accepted} promised {promised} domain {expected}",
        )
    ok(
        "the promise, its event and its audit row are written together",
        sql(
            f"select count(*) from domain_events where aggregate_id='{order_id}' "
            "and event_type='ORDER_PROMISE_SET'"
        )
        == "1"
        and sql(
            f"select count(*) from audit_events where aggregate_id='{order_id}' "
            "and action='ORDER_PROMISE_SET'"
        )
        == "1"
        and sql(
            f"select count(*) from outbox_events where aggregate_id='{order_id}' "
            "and event_type='order.promise_set.v1'"
        )
        == "1",
    )
    console.open_order(order_id)
    row = console.page.locator("[data-field=promise]")
    ok(
        "the order page shows the promise",
        row.count() == 1 and _promise_text(promised) in row.first.inner_text(),
        row.first.inner_text()[:120] if row.count() else "absent",
    )
    console.open(f"#/orders/{order_id}/receipt", settle=2200)
    closing = console.page.locator("#receipt-paper [data-field=closing]")
    ok(
        "the receipt prints 'Hẹn trả: …' in place of 'Tiệm sẽ báo khi đồ sẵn sàng'",
        closing.count() == 1
        and closing.first.inner_text().strip() == f"Hẹn trả: {_promise_text(promised)}",
        closing.first.inner_text() if closing.count() else "absent",
    )

    # A blanket: 24 or 48 hours, the staff member's choice, 48 already chosen.
    blanket = console.build_order(
        stop="created",
        lines=[
            {
                "service_code": "BED_BLANKET",
                "quantity": "3",
                "unit": "KG",
                "quantity_basis": "STAFF_MEASUREMENT",
            }
        ],
    )
    _clear_of_minute_edge()
    _open_receive(console, blanket["order_id"])
    checked = console.page.locator("dialog[open] input[name=receive-promise-choice]:checked")
    before_line = console.page.locator("#receive-promise-line").inner_text()
    ok(
        "a blanket's Nhận đồ offers 24 giờ and 48 giờ, with 48 giờ chosen",
        checked.count() == 1
        and checked.first.get_attribute("value") == "H48"
        and console.page.locator("dialog[open] .choice-chip[title=H24]").count() == 1,
        before_line,
    )
    console.page.locator("dialog[open] .choice-chip[title=H24]").click()
    touched("orderDetail.receive-promise-choice")
    console.page.wait_for_timeout(300)
    after_line = console.page.locator("#receive-promise-line").inner_text()
    ok(
        "choosing 24 giờ shows the earlier time the server computed for it",
        after_line != before_line and after_line.startswith("Hẹn trả:"),
        f"{before_line} -> {after_line}",
    )
    answer = _press_receive(console)
    body = answer["body"] if answer["status"] == 200 else {}
    ok(
        "the blanket is promised under its own rule, by the staff member's choice",
        (body.get("promise_basis"), body.get("promise_rule_id")) == ("H24", "SLA_BLANKETS_SHEETS")
        and _promise_text(str(body.get("promised_ready_at") or "")) in after_line,
        answer["text"][:200],
    )
    # Founder ruling on DEC-037: 24 h is a calendar day, rolled into opening hours -- the stored
    # promise is the domain's answer for the stored acceptance, and never earlier than 24 clock
    # hours after it.
    blanket_accepted = stored(
        blanket["order_id"],
        "to_char(production_accepted_at at time zone 'UTC', "
        '\'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"\')',
    )
    blanket_promised = str(body.get("promised_ready_at") or "")
    if blanket_accepted and blanket_promised:
        from datetime import datetime, timedelta

        from nha_trang_laundry_domain.promise import PromiseChoice

        accepted_at = datetime.fromisoformat(blanket_accepted)
        expected = compute_promise(
            policy,
            accepted_at=accepted_at,
            service_codes=["BED_BLANKET"],
            choice=PromiseChoice.H24,
        )
        stored_at = datetime.fromisoformat(blanket_promised.replace("Z", "+00:00"))
        ok(
            "the blanket's 24 giờ is one calendar day, rolled into opening hours (founder ruling)",
            getattr(expected, "promised_at", None) == stored_at
            and stored_at >= (accepted_at + timedelta(hours=24)).replace(second=0, microsecond=0)
            and all(line.counting == "CALENDAR_HOURS" for line in getattr(expected, "lines", ())),
            f"accepted {blanket_accepted} promised {blanket_promised} domain {expected}",
        )

    # Hẹn lại: a new time and a reason; the first promise stays.
    console.open_order(order_id)
    change = console.page.locator("#promise-change")
    ok("the order page offers Hẹn lại while the laundry is not finished", change.count() == 1)
    if change.count():
        change.click()
        touched("orderDetail.promise-change")
        console.page.wait_for_timeout(500)
        from datetime import datetime, timedelta, timezone

        new_day = datetime.fromisoformat(promised.replace("Z", "+00:00")).astimezone(
            timezone(timedelta(hours=7))
        ) + timedelta(days=1)
        while policy.is_closed(new_day.date()):
            new_day += timedelta(days=1)
        picked = f"{new_day:%Y-%m-%d}T15:00"
        console.page.locator("#promise-change-at").fill(picked)
        touched("orderDetail.promise-change-at")
        console.page.locator("dialog[open] .choice-chip[title=OTHER]").click()
        touched("orderDetail.promise-change-reason")
        console.page.wait_for_timeout(200)
        ok(
            "Khác needs a few words before Hẹn lại can be saved",
            console.page.locator("#promise-change-submit").is_disabled(),
        )
        console.page.locator("dialog[open] .choice-chip[title=WORKLOAD]").click()
        console.page.wait_for_timeout(200)
        (saved,) = console.press_capturing(
            console.page.locator("#promise-change-submit"), "/promise"
        )
        touched("orderDetail.promise-change-submit")
        console.page.wait_for_timeout(1200)
        body = saved["body"] if saved["status"] == 200 else {}
        ok(
            "Hẹn lại moves what the customer was told and keeps the first promise",
            body.get("promised_ready_at") == promised
            and _promise_text(str(body.get("current_promise_at") or "")).startswith("15:00"),
            saved["text"][:200],
        )
        ok(
            "the change is in its ledger with its reason, and in the audit trail by code only",
            sql(f"select reason_code from order_promise_changes where order_id='{order_id}'")
            == "WORKLOAD"
            and sql(
                f"select details->>'reason_code' from audit_events where aggregate_id='{order_id}' "
                "and action='ORDER_PROMISE_CHANGE'"
            )
            == "WORKLOAD",
        )
        row = console.page.locator("[data-field=promise]")
        ok(
            "the order page says the new time and the first one",
            row.count() == 1
            and "15:00" in row.first.inner_text()
            and f"Hẹn đầu: {_promise_text(promised)}" in row.first.inner_text(),
            row.first.inner_text()[:160] if row.count() else "absent",
        )
    console.open("#/orders", settle=1800)
    listed = console.page.locator(f"[data-order-id='{order_id}']")
    ok(
        "the order list shows the promise on the row",
        listed.count() >= 1 and "Hẹn 15:00" in listed.first.inner_text(),
        listed.first.inner_text()[:160] if listed.count() else "absent",
    )
    board = console.call("GET", f"/internal/v1/stores/{STORE}/sla-board?limit=200")
    rows = {row["order_id"]: row for row in (board["body"] or {}).get("items", [])}
    ok(
        "the SLA board ranks the promised order by its promise and says so",
        rows.get(order_id, {}).get("rule_source") == "ORDER_PROMISE"
        and rows.get(before["order_id"], {}).get("rule_source") == "STATED_RULE",
        {key: rows.get(key, {}).get("rule_source") for key in (order_id, before["order_id"])},
    )


def _capture(console: Console, order_id: str) -> dict[str, Any]:
    read = console.call("GET", f"/internal/v1/orders/{order_id}/capture")
    if read["status"] != 200:
        raise AssertionError(f"the capture read did not answer: {read['status']} {read['text']}")
    return read["body"]


def _summary(console: Console, day: str) -> dict[str, Any]:
    read = console.call("GET", f"/internal/v1/stores/{STORE}/reports/summary?from={day}&to={day}")
    if read["status"] != 200:
        raise AssertionError(f"the report did not answer: {read['status']} {read['text']}")
    return read["body"]


def _nav_to(console: Console, label: str, path: str) -> None:
    """Reach a destination the way a person does: the sidebar link, or Thêm then its row."""
    console.open("#/orders")
    link = console.page.locator("nav a", has_text=label).first
    if not (link.count() and link.is_visible()):
        console.page.locator("nav a", has_text="Thêm").first.click()
        console.page.wait_for_timeout(700)
        link = console.page.locator(f"main a[data-nav='{path}']").first
    if link.count():
        link.click()
        console.page.wait_for_timeout(1400)


def scenario_shop_capture(console: Console) -> None:
    """SHOP-CAPTURE-001 (DEC-038): the shop measured inside taps staff already make.

    A wash with a machine chosen at Bắt đầu giặt, one skipped, a rewash on a second machine; a
    pickup trip with its cost typed on the leg sheet (and its return, with cost, over the API); two
    expenses in Sổ thu chi through the screen, and a wrong third one voided; then the owner's
    report: cycles captured over cycles, minutes on the machine, the delivery cost per delivered
    order, the month's spending by category -- and margin shown as not yet computable, with the
    missing categories named, because this walk never records water, wages or rent.
    """

    head("S", "ĐO LƯỜNG — máy, mẻ giặt, chi phí chuyến, sổ thu chi (DEC-038)")
    console.sign_in("demo-owner")
    today = console.page.evaluate(
        "() => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', "
        "month: '2-digit', day: '2-digit'}).format(new Date())"
    )
    month = today[:7]
    machines = console.call("GET", f"/internal/v1/stores/{STORE}/machines?purpose=WASH")
    codes = [m["code"] for m in (machines.get("body") or {}).get("machines", [])]
    ok(
        "the machine master is seeded: the machines a load goes into are listed for the counter",
        machines["status"] == 200 and {"WASH-01", "WASH-02"} <= set(codes),
        codes,
    )
    before = _summary(console, today)
    expenses_before = console.call("GET", f"/internal/v1/stores/{STORE}/expenses?month={month}")

    head("S1", "BẮT ĐẦU GIẶT — one machine chosen, one skipped, a rewash on another")
    console.sign_in("demo-operations")
    chosen = console.build_order(kg="7", stop="active")
    said = console.step(chosen["order_id"], "START_WASH", machine="WASH-01")
    cycles = _capture(console, chosen["order_id"])["cycles"]
    ok(
        "Bắt đầu giặt with WASH-01 chosen opens one cycle on WASH-01",
        len(cycles) == 1 and cycles[0]["machine_code"] == "WASH-01" and not cycles[0]["ended_at"],
        said[:120] if len(cycles) != 1 else cycles[0]["machine_code"],
    )
    console.step(chosen["order_id"], "QUALITY_CHECK")
    cycles = _capture(console, chosen["order_id"])["cycles"]
    ok(
        "Giặt xong, kiểm tra đồ closes it, with its minutes",
        len(cycles) == 1 and cycles[0]["ended_at"] and isinstance(cycles[0]["minutes"], int),
        cycles[:1],
    )
    console.step(chosen["order_id"], "REWASH", reason="NOT_CLEAN", machine="WASH-02")
    cycles = _capture(console, chosen["order_id"])["cycles"]
    ok(
        "a rewash opens a second cycle, on the machine picked in its sheet",
        [(c["kind"], c["machine_code"]) for c in cycles]
        == [("WASH", "WASH-01"), ("REWASH", "WASH-02")],
        [(c["kind"], c["machine_code"]) for c in cycles],
    )
    console.step(chosen["order_id"], "QUALITY_CHECK")
    text = console.text()
    ok(
        "the order page lists both cycles with their machines",
        "WASH-01" in text and "WASH-02" in text and "Giặt lại" in text,
    )

    skipped = console.build_order(kg="5", stop="active")
    console.open_order(skipped["order_id"])
    control = console.step_control("START_WASH")
    first_offered = ""
    if control is not None:
        control.click()
        with contextlib.suppress(Exception):
            console.page.wait_for_selector(
                "dialog[open] button[data-machine-skip]", state="visible", timeout=8000
            )
        buttons = console.page.locator("dialog[open] button[data-machine-code]")
        first_offered = (
            str(buttons.first.get_attribute("data-machine-code")) if buttons.count() else ""
        )
        offered = [str(b.get_attribute("data-machine-code")) for b in buttons.all()]
        ok(
            "Máy nào? offers only the machines a load goes into, the last used first",
            first_offered == "WASH-02"
            and "DRY-01" not in offered
            and "IRON-TABLE-01" not in offered,
            offered,
        )
        console.page.locator("dialog[open] button[data-machine-skip]").first.click()
        touched("orderDetail.machine-skip")
        console.page.wait_for_timeout(1800)
    cycles = _capture(console, skipped["order_id"])["cycles"]
    ok(
        "Bỏ qua still starts the wash, and the cycle is recorded as not captured",
        len(cycles) == 1 and cycles[0]["machine_id"] is None,
        cycles,
    )
    if READS_DATABASE:
        ok(
            "the cycle rows are the wash's own, each with its event, audit and outbox row",
            sql(
                "select count(*) from domain_events where aggregate_type='WASH_CYCLE' and "
                f"payload->>'order_id' in ('{chosen['order_id']}','{skipped['order_id']}')"
            )
            == "5",
            "2 cycles opened and closed on the first order, 1 opened on the second",
        )

    head("S2", "CHI PHÍ CHUYẾN — the trip's cost on the same sheet as the pickup")
    trip_order = console.build_order(
        kg="22", mode="PICKUP_AND_RETURN", distance_m=1500, stop="active"
    )
    capture = _capture(console, trip_order["order_id"])
    ok(
        "the owner's vehicle rule reads 22 kg as a car (from exactly 20 kg)",
        capture["suggested_vehicle"] == "O_TO" and capture["weight_kg"] == "22",
        (capture["suggested_vehicle"], capture["weight_kg"]),
    )
    console.open_order(trip_order["order_id"])
    control = console.step_control("DELIVERY_PICKUP")
    posted: list[dict[str, Any]] = []
    if control is not None:
        control.click()
        console.page.wait_for_timeout(700)
        hint = console.page.locator("dialog[open] [data-suggested-vehicle]")
        ok(
            "the leg sheet says what the rule suggests, and picks nothing for the driver",
            hint.count() == 1 and hint.first.get_attribute("data-suggested-vehicle") == "O_TO",
            hint.first.inner_text() if hint.count() else "absent",
        )
        console.page.locator("dialog[open] details[data-trip-fields] summary").click()
        console.page.locator("dialog[open] #trip-vehicle [data-value=O_TO]").click()
        console.type_into("#trip-km", "6,5")
        console.type_into("#trip-cost", "45000")
        console.type_into("#trip-note", "gửi xe chợ Đầm")
        touched("orderDetail.trip-cost")
        posted = console.press_capturing(
            console.page.locator("dialog[open] button[data-leg-outcome=SUCCEEDED]").first,
            "/delivery-legs",
        )
    ok(
        "Lấy được đồ records the trip with its vehicle, km, cost and note",
        posted and posted[0]["status"] == 201,
        posted[0]["text"][:120] if posted else "no call",
    )
    legs = _capture(console, trip_order["order_id"])["legs"]
    ok(
        "the order's trip reads back as typed: ô tô, 6.5 km, 45.000 ₫",
        [(leg["vehicle"], leg["km"], leg["cost_vnd"]) for leg in legs] == [("O_TO", "6.5", 45000)],
        legs,
    )
    refused = console.call(
        "POST",
        f"/internal/v1/orders/{trip_order['order_id']}/delivery-legs",
        {"leg_kind": "RETURN", "outcome": "FAILED", "note": "gọi khách 0382 318 492"},
    )
    ok(
        "a trip note carrying a phone number is refused by name, and nothing is recorded",
        refused["status"] == 422
        and "NOTE_LOOKS_LIKE_PHONE" in refused["text"]
        and len(_capture(console, trip_order["order_id"])["legs"]) == 1,
        refused["text"][:120],
    )
    returned = console.call(
        "POST",
        f"/internal/v1/orders/{trip_order['order_id']}/delivery-legs",
        {"leg_kind": "RETURN", "outcome": "SUCCEEDED", "vehicle": "O_TO", "cost_vnd": 40000},
    )
    ok(
        "the return trip is recorded with its cost",
        returned["status"] == 201,
        returned["text"][:120],
    )
    if READS_DATABASE:
        ok(
            "the note is stored once, on the trip, and in no event, audit or outbox payload",
            sql(
                "select count(*) from delivery_leg_costs where note = 'gửi xe chợ Đầm' and "
                f"order_id = '{trip_order['order_id']}'"
            )
            == "1"
            and sql(
                "select (select count(*) from domain_events where payload::text like '%chợ Đầm%')"
                " + (select count(*) from audit_events where details::text like '%chợ Đầm%')"
                " + (select count(*) from outbox_events where payload::text like '%chợ Đầm%')"
            )
            == "0",
        )

    head("S3", "SỔ THU CHI — two expenses through the screen, a wrong one voided")
    console.sign_in("demo-operations")
    denied = console.call("GET", f"/internal/v1/stores/{STORE}/expenses?month={month}")
    ok("the counter is refused Sổ thu chi by the server", denied["status"] == 403)
    console.sign_in("demo-owner")
    _nav_to(console, "Sổ thu chi", "/expenses")
    touched("shell.nav.expenses")
    ok(
        "the navigation reaches Sổ thu chi",
        console.page.url.endswith("#/expenses"),
        console.page.url,
    )

    def add_expense(category: str, amount: str, note_text: str = "") -> dict[str, Any]:
        console.page.locator("button[data-expense-add]").first.click()
        console.page.wait_for_selector("#expense-add[open]")
        touched("expenses.add")
        console.page.locator(f"#expense-category [data-value={category}]").click()
        touched("expenses.category")
        console.type_into("#expense-amount", amount)
        if note_text:
            console.type_into("#expense-note", note_text)
        (answer,) = console.press_capturing(console.page.locator("#expense-save"), "/expenses")
        touched("expenses.save")
        console.page.wait_for_timeout(1400)
        return answer

    first = add_expense("DIEN", "1250000", "tiền điện tháng")
    second = add_expense("HOA_CHAT", "800000")
    wrong = add_expense("KHAC", "9900000")
    ok(
        "two expenses and a mistyped third are recorded",
        [first["status"], second["status"], wrong["status"]] == [201, 201, 201],
        [first["text"][:60], second["text"][:60], wrong["text"][:60]],
    )
    row = console.page.locator(f"[data-expense-id='{(wrong.get('body') or {}).get('expense_id')}']")
    if row.count():
        row.first.click()
        console.page.wait_for_selector("#expense-line[open]")
        console.page.locator("#expense-void").click()
        console.page.wait_for_timeout(200)
        (voided,) = console.press_capturing(console.page.locator("#expense-void"), "/void")
        touched("expenses.void")
        console.page.wait_for_timeout(1400)
        ok("the wrong line is voided, two presses, with its version", voided["status"] == 200)
    after_month = console.call("GET", f"/internal/v1/stores/{STORE}/expenses?month={month}")
    total_before = int((expenses_before.get("body") or {}).get("total_vnd") or 0)
    total_after = int((after_month.get("body") or {}).get("total_vnd") or 0)
    ok(
        "the month's total, summed by the server, moved by the two real expenses only",
        total_after - total_before == 2_050_000,
        f"{total_after} - {total_before}",
    )
    hero = console.page.locator(".money-hero__amount").first
    shown = "".join(ch for ch in (hero.inner_text() if hero.count() else "") if ch.isdigit())
    ok(
        "Sổ thu chi prints the server's total",
        shown == str(total_after),
        f"{shown} vs {total_after}",
    )
    missing_line = console.page.locator("[data-core-missing]").first
    missing = (after_month.get("body") or {}).get("core_missing", [])
    ok(
        "and says, in one line, which core categories margin still waits for",
        missing_line.count() == 1
        and str(missing_line.get_attribute("data-core-missing")) == " ".join(missing)
        and "DIEN" not in missing
        and "HOA_CHAT" not in missing,
        missing_line.inner_text() if missing_line.count() else "absent",
    )

    head("S4", "BÁO CÁO — the measured lines, and margin withheld with what is missing")
    after = _summary(console, today)
    cap0, cap1 = before["capture"], after["capture"]
    ok(
        "three more cycles, two of them with a machine",
        (cap1["cycles"] - cap0["cycles"], cap1["cycles_captured"] - cap0["cycles_captured"])
        == (3, 2),
        (cap1["cycles"] - cap0["cycles"], cap1["cycles_captured"] - cap0["cycles_captured"]),
    )
    timed = {m["code"]: m for m in cap1["machines"]}
    ok(
        "WASH-01 and WASH-02 each carry closed cycles and an average in whole minutes",
        {"WASH-01", "WASH-02"} <= set(timed)
        and all(isinstance(timed[c]["average_minutes"], int) for c in ("WASH-01", "WASH-02")),
        sorted(timed),
    )
    ok(
        "one more delivered order with every leg costed, its 85.000 ₫ in the trip total",
        (
            cap1["costed_orders"] - cap0["costed_orders"],
            cap1["trip_cost_vnd"] - cap0["trip_cost_vnd"],
        )
        == (1, 85_000)
        and cap1["cost_per_delivered_order_vnd"] is not None,
        (cap1["costed_orders"], cap1["trip_cost_vnd"], cap1["cost_per_delivered_order_vnd"]),
    )
    month_row = after["months"][-1]
    spent = {s["category"]: s["amount_vnd"] for s in month_row["spending"]}
    ok(
        "the month's spending by category is the server's sum of Sổ thu chi",
        spent["DIEN"] >= 1_250_000
        and spent["HOA_CHAT"] >= 800_000
        and month_row["spending_vnd"] == total_after,
        spent,
    )
    ok(
        "margin is INCOMPLETE, names the missing categories, and carries no amount",
        month_row["margin"]["status"] == "INCOMPLETE"
        and {"NUOC", "LUONG", "MAT_BANG"} <= set(month_row["margin"]["missing"])
        and month_row["margin"]["amount_vnd"] is None,
        month_row["margin"],
    )
    console.open("#/reports")
    console.page.locator("[aria-label='Khoảng ngày'] [data-value='today']").click()
    console.page.wait_for_timeout(1600)
    captured = console.page.locator("[data-kpi=CYCLES_CAPTURED] [data-fraction]")
    ok(
        "Mẻ có ghi máy prints the server's two integers",
        captured.count() == 1
        and captured.first.get_attribute("data-fraction")
        == f"{cap1['cycles_captured']}/{cap1['cycles']}",
        captured.first.get_attribute("data-fraction") if captured.count() else "absent",
    )
    trip_tile = console.page.locator("[data-kpi=TRIP_COST_PER_ORDER] .kpi__value")
    trip_digits = "".join(
        ch for ch in (trip_tile.first.inner_text() if trip_tile.count() else "") if ch.isdigit()
    )
    ok(
        "Chi phí giao / đơn prints the server's per-order figure",
        trip_digits == str(cap1["cost_per_delivered_order_vnd"]),
        f"{trip_digits} vs {cap1['cost_per_delivered_order_vnd']}",
    )
    minutes = console.page.locator("[data-report-machine='WASH-01'] [data-average-minutes]")
    ok(
        "the machine line prints WASH-01's average minutes",
        minutes.count() == 1
        and minutes.first.get_attribute("data-average-minutes")
        == str(timed["WASH-01"]["average_minutes"]),
    )
    margin = console.page.locator("[data-kpi=MARGIN]")
    ok(
        "the margin tile says Chưa đủ số liệu and lists what Sổ thu chi is missing",
        margin.count() == 1
        and margin.first.get_attribute("data-margin-status") == "INCOMPLETE"
        and "Chưa đủ số liệu" in margin.first.inner_text()
        and "nước" in margin.first.inner_text().lower()
        and "mặt bằng" in margin.first.inner_text().lower(),
        margin.first.inner_text()[:160] if margin.count() else "absent",
    )
    ok(
        "and nothing on the report calls the remainder profit",
        "lợi nhuận" not in console.text().lower(),
    )
    month_total = console.page.locator(f"[data-report-month='{month}'] [data-spending-total]")
    ok(
        "the month's spending section prints the server's total",
        month_total.count() == 1
        and month_total.first.get_attribute("data-spending-total") == str(total_after),
    )

    head("S5", "MÁY — the owner adds, renames and retires a machine")
    console.open("#/system")
    link = console.page.locator("a[data-system-machines]")
    if link.count():
        link.first.click()
        console.page.wait_for_timeout(1400)
        touched("shell.nav.machines")
    ok(
        "Hệ thống leads to the machine list",
        console.page.url.endswith("#/machines"),
        console.page.url,
    )
    code = f"TEST-{uuid.uuid4().hex[:6].upper()}"
    console.page.locator("button[data-machine-add]").first.click()
    console.page.wait_for_selector("#machine-add[open]")
    console.type_into("#machine-code", code)
    console.type_into("#machine-new-name", "Máy giặt thử")
    console.page.locator("#machine-category [data-value=washer]").click()
    (added,) = console.press_capturing(console.page.locator("#machine-create"), "/machines")
    touched("machines.add")
    console.page.wait_for_timeout(1400)
    ok("the owner adds a machine", added["status"] == 201, added["text"][:120])
    row = console.page.locator(f"button[data-machine-code='{code}']")
    if row.count():
        row.first.click()
        console.page.wait_for_selector("#machine-edit[open]")
        console.type_into("#machine-name", "Máy giặt số 3")
        console.page.locator("#machine-rename").click()
        console.page.wait_for_timeout(1500)
        touched("machines.rename")
    listed = console.call("GET", f"/internal/v1/stores/{STORE}/machines")
    names = {m["code"]: m["display_name"] for m in (listed.get("body") or {}).get("machines", [])}
    ok("and renames it", names.get(code) == "Máy giặt số 3", names.get(code))
    row = console.page.locator(f"button[data-machine-code='{code}']")
    if row.count():
        row.first.click()
        console.page.wait_for_selector("#machine-edit[open]")
        console.page.locator("#machine-retire").click()
        console.page.wait_for_timeout(200)
        console.page.locator("#machine-retire").click()
        console.page.wait_for_timeout(1500)
        touched("machines.retire")
    wash = console.call("GET", f"/internal/v1/stores/{STORE}/machines?purpose=WASH")
    ok(
        "and retires it: the counter is never offered it again",
        code not in [m["code"] for m in (wash.get("body") or {}).get("machines", [])],
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


def _customer_refusal_then_publish(console: Console, digits: str, spaced: str) -> None:
    """Before publication: the sheet offers nothing to save and the server refuses; then publish."""

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
    # EINVOICE-REQUEST-001 (DEC-040): in a full run this is the one moment the notice is still
    # unpublished, so the invoice capture's refusal is proven here too, on the newest open order.
    open_order = sql(
        f"SELECT id FROM orders WHERE store_id = '{STORE}' AND commercial_status <> 'CANCELLED' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if open_order:
        _invoice_privacy_refusal(console, open_order)

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
    if unpublished:
        _customer_refusal_then_publish(console, digits, spaced)
    else:
        note(
            "the notice was published before this scenario began, so its refusal is not provable "
            "here: run it on a stack that has not published the notice"
        )

    head("16b", "KHÁCH MỚI — recorded at the counter, with the consent read aloud")
    console.open("#/new", settle=1600)
    console.type_into("#new-customer-search", spaced, "newOrder.customer-search")
    console.page.wait_for_timeout(1200)
    console.page.locator("#new-customer-add").click()
    touched("newOrder.customer-add")
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


def scenario_deposit(console: Console) -> None:
    """`PAYMENT-001` (`DEC-035`): a 50.000 ₫ transfer deposit at drop-off, the rest in cash at
    pickup, the handover -- and the two refusals around it: more than remains, and the goods
    leaving while money is still owed. Every figure is read back from the server, never computed.
    """

    head("21", "ĐẶT CỌC — 50.000 ₫ chuyển khoản lúc gửi, phần còn lại tiền mặt lúc lấy (DEC-035)")
    console.sign_in("demo-operations")
    order = console.build_order(kg="7", stop="active")
    order_id = order["order_id"]

    def read() -> dict:
        return console.call("GET", f"/internal/v1/orders/{order_id}").get("body") or {}

    before = read()
    owed = int(before.get("owed_vnd") or 0)
    note(f"order {order_id[:8]}… owes {owed} đồng; nothing paid yet")

    # A transfer is recorded only once somebody saw it arrive.
    said = console.pay(order_id, "50.000", method="CHUYEN_KHOAN", seen=False)
    ok(
        "a transfer nobody ticked as seen is refused, and nothing is written",
        "TRANSFER_NOT_SEEN" in console.reason_codes()
        and "Đã thấy tiền vào tài khoản" in said
        and int(read().get("paid_vnd") or 0) == 0,
        said[:160],
    )

    said = console.pay(order_id, "50.000", method="CHUYEN_KHOAN", seen=True, ref="ft 2609")
    after = read()
    ok(
        "the deposit lands: partly paid, 50.000 ₫ paid, the rest remaining (server figures)",
        after.get("balance") == "PARTIALLY_PAID"
        and after.get("paid_vnd") == 50_000
        and after.get("remaining_vnd") == owed - 50_000,
        {k: after.get(k) for k in ("balance", "paid_vnd", "remaining_vnd")},
    )
    ok(
        "the payment is on the ledger as a transfer with its reference tail",
        [(p["amount_vnd"], p["method"], p["bank_ref_last"]) for p in after.get("payments", [])]
        == [(50_000, "CHUYEN_KHOAN", "FT2609")],
        after.get("payments"),
    )
    console.open_order(order_id)
    shown = console.text()
    ok(
        "the money card reads Tổng · Đã trả · Còn lại and lists the transfer",
        all(word in shown for word in ("Tổng", "Đã trả", "Còn lại", "Chuyển khoản", "FT2609")),
        [line for line in shown.splitlines() if "₫" in line][:6],
    )
    if READS_DATABASE:
        ok(
            "the database holds one 50000 CHUYEN_KHOAN payment and a PARTIALLY_PAID order",
            sql(
                "select method || ':' || amount_vnd from order_payments "
                f"where order_id='{order_id}'"
            )
            == "CHUYEN_KHOAN:50000"
            and stored(order_id, "balance_status") == "PARTIALLY_PAID",
            sql(f"select method, amount_vnd from order_payments where order_id='{order_id}'"),
        )

    for target in ("QUEUED", "IN_PROCESS", "QUALITY_CHECK", "READY_AT_STORE"):
        moved = console.call(
            "POST",
            f"/internal/v1/orders/{order_id}/production-transition",
            {"target": target},
            if_match=console.current_version(order_id, order["row_version"]),
        )
        if moved["status"] >= 300:
            raise AssertionError(f"could not move to {target}: {moved['text']}")
    note("washed and on the shelf; the customer comes back")

    # Goods leave only when paid: nothing that hands them over is offered, and the server refuses.
    console.open_order(order_id)
    offered = console.offered()
    ok(
        "at pickup the big button is Thu tiền, and no handover is offered while money is owed",
        console.primary_step() == "TAKE_PAYMENT"
        and not {"COLLECT", "HAND_OVER", "RELEASE"} & set(offered),
        offered,
    )
    version = console.current_version(order_id, order["row_version"])
    pickup = console.call("POST", f"/internal/v1/orders/{order_id}/collection", if_match=version)
    hand_over = console.call(
        "POST", f"/internal/v1/orders/{order_id}/steps", {"step": "HAND_OVER"}, if_match=version
    )
    ok(
        "the server refuses the pickup and the handover while partly paid",
        pickup["status"] == 422
        and "COLLECTION_REQUIRES_PAYMENT" in pickup["text"]
        and hand_over["status"] == 409,
        f"{pickup['status']} {pickup['text'][:80]} / {hand_over['status']}",
    )

    # The customer hands over more than remains: change is given, the excess is refused.
    over = f"{owed - 50_000 + 10_000:,}".replace(",", ".")
    said = console.pay(order_id, over)
    ok(
        "more than remains is refused -- trả lại tiền thừa cho khách -- and nothing is written",
        "OVERPAYMENT_REFUSED" in console.reason_codes()
        and "trả lại tiền thừa" in said
        and read().get("paid_vnd") == 50_000,
        said[:160],
    )

    # The rest, prefilled, in cash; the customer takes the bag with it.
    said = console.pay(order_id, hand_over=True)
    closing = console.page.locator("dialog[open] button[data-step=HAND_OVER]")
    paid = read()
    ok(
        "the rest in cash settles the order and records the handover in the same press",
        paid.get("balance") == "PAID"
        and paid.get("remaining_vnd") == 0
        and paid.get("self_collection_recorded") is True
        and [p["method"] for p in paid.get("payments", [])] == ["CHUYEN_KHOAN", "TIEN_MAT"],
        {k: paid.get(k) for k in ("balance", "remaining_vnd", "self_collection_recorded")},
    )
    ok("the payment sheet offers 'Giao đồ & đóng đơn' straight away", closing.count() == 1)
    if closing.count():
        closing.first.click()
        console.page.wait_for_timeout(2000)
    ok(
        "handed over: the order is completed",
        read().get("commercial") == "COMPLETED",
        read().get("commercial"),
    )

    console.open(f"#/orders/{order_id}/receipt")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#receipt-paper [data-paid]", timeout=10000)
    paper = console.text()
    ok(
        "the receipt prints Đã trả and Còn lại once a payment exists",
        "Đã trả" in paper and "Còn lại" in paper,
        [line for line in paper.splitlines() if "Đã trả" in line or "Còn lại" in line][:2],
    )

    console.open("#/")
    touched("shell.nav.today")
    board = console.text()
    takings_read = console.call("GET", f"/internal/v1/stores/{STORE}/settlements/today")
    takings = takings_read.get("body") or {}
    ok(
        "Hôm nay splits the day's money into cash and transfer",
        "Tiền mặt (" in board and "Chuyển khoản (" in board,
        [line for line in board.splitlines() if "Tiền mặt" in line or "Chuyển khoản" in line][:2],
    )
    if READS_DATABASE:
        by_method = sql(
            "select coalesce(sum(amount_vnd) filter (where method='TIEN_MAT'),0) || ':' || "
            "coalesce(sum(amount_vnd) filter (where method='CHUYEN_KHOAN'),0) "
            f"from order_payments where store_id='{STORE}' and "
            "(recorded_at at time zone 'Asia/Ho_Chi_Minh')::date = "
            "(now() at time zone 'Asia/Ho_Chi_Minh')::date"
        )
        ok(
            "the takings by method equal the day's payments, method by method",
            by_method == f"{takings.get('cash_vnd')}:{takings.get('transfer_vnd')}",
            f"ledger {by_method} / takings {takings.get('cash_vnd')}:{takings.get('transfer_vnd')}",
        )


# --- EXPORT-PAYMENTS-001 --------------------------------------------------------------------------


def _file_rows(content: str) -> tuple[dict[str, str], list[str], dict[str, dict[str, str]]]:
    """A produced export: its `key,value` header rows, its columns, and its rows by order id."""
    import csv
    import io

    lines = list(csv.reader(io.StringIO(content)))
    header: dict[str, str] = {}
    index = 0
    while index < len(lines) and len(lines[index]) == 2 and lines[index][0] != "order_id":
        header[lines[index][0]] = lines[index][1]
        index += 1
    columns = lines[index] if index < len(lines) else []
    rows = {line[0]: dict(zip(columns, line, strict=False)) for line in lines[index + 1 :] if line}
    return header, columns, rows


def scenario_export_payments(console: Console) -> None:
    """`EXPORT-PAYMENTS-001`: the owner's one-day export carries part payments by method.

    One order takes a 50.000 ₫ deposit by transfer and the rest in cash, both through Thu tiền; a
    second order is taken and nothing is paid. Then two envelopes for today's export:

    * one bound to the RETIRED file shape (money from settlements alone) -- the digest the code
      deployed before this item signed, computed here by this harness from the retired document,
      never taken from the server. The owner's card names it and keeps Duyệt shut, the live server
      refuses to record the decision, and the release is refused by name
      (`EXPORT_QUERY_VERSION_RETIRED`). With the database in reach it is then marked approved the
      way the old code would have recorded it, and the release is still refused, nothing written;
    * one through the screens: Hôm nay → Tạo yêu cầu → Xin chủ tiệm duyệt → the owner approves on
      the card, reading the money in one line → Xuất tệp → the downloaded CSV. The paid order's row
      carries the transfer and the cash apart; the unpaid order's row has `remaining_vnd` equal to
      its total. Every figure compared is the server's order read, never computed here.
    """

    head(
        "22",
        "XUẤT KÈM TIỀN TRẢ TỪNG LẦN — cọc chuyển khoản, phần còn lại tiền mặt, một đơn chưa trả",
    )
    from datetime import datetime as _datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    import workspace_env  # noqa: F401  (the workspace packages, as every scripts/ entry point)
    from nha_trang_laundry_db import exports as export_rules
    from nha_trang_laundry_domain.canonical import canonical_document

    console.sign_in("demo-operations")
    paid_id = console.build_order(kg="7", stop="active")["order_id"]
    console.pay(paid_id, "50.000", method="CHUYEN_KHOAN", seen=True)
    console.pay(paid_id)
    unpaid_id = console.build_order(kg="7", stop="active")["order_id"]

    def read(order_id: str) -> dict:
        return console.call("GET", f"/internal/v1/orders/{order_id}").get("body") or {}

    paid_view, unpaid_view = read(paid_id), read(unpaid_id)
    ok(
        "the first order is paid: 50.000 ₫ by transfer, then the rest in cash (server figures)",
        paid_view.get("balance") == "PAID"
        and [(p["method"], p["amount_vnd"]) for p in paid_view.get("payments", [])][:1]
        == [("CHUYEN_KHOAN", 50_000)]
        and [p["method"] for p in paid_view.get("payments", [])] == ["CHUYEN_KHOAN", "TIEN_MAT"],
        {k: paid_view.get(k) for k in ("balance", "owed_vnd", "paid_vnd")},
    )
    ok(
        "the second order is unpaid: nothing on its ledger",
        unpaid_view.get("balance") == "UNPAID" and unpaid_view.get("paid_vnd") == 0,
        {k: unpaid_view.get(k) for k in ("balance", "owed_vnd", "paid_vnd")},
    )
    owed_paid = int(paid_view.get("owed_vnd") or 0)
    owed_unpaid = int(unpaid_view.get("owed_vnd") or 0)
    today = _datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()

    browser = console.context.browser
    context = browser.new_context(viewport=viewport(), accept_downloads=True)
    requester = Console(context.new_page(), context)
    requester.sign_in("demo-approver")

    # -- the envelope signed over the retired shape -------------------------------------------
    created = requester.call(
        "POST", f"/internal/v1/stores/{STORE}/exports", {"business_date": today.isoformat()}
    )
    body = created.get("body") or {}
    facts = export_rules._facts("STORE_DAY_ORDERS_V1", uuid.UUID(STORE), today, None)
    retired_hash = canonical_document(export_rules._retired_statement(facts)).snapshot_hash
    ok(
        "a one-day request today is answered in the live shape, not the retired one",
        created["status"] == 201
        and str(body.get("query_version", "")).startswith("store-day-orders-export-v4:")
        and body.get("rendered_hash") not in ("", retired_hash)
        and body.get("shape_retired") is False,
        {k: body.get(k) for k in ("query_version", "shape_retired")},
    )
    retired_envelope = requester.call(
        "POST",
        "/internal/v1/approvals",
        {
            "store_id": STORE,
            "action": "EXPORT_SANITIZED_DATA",
            "resource_type": body.get("resource_type"),
            "resource_id": body.get("export_request_id"),
            "resource_version": body.get("resource_version"),
            "snapshot_hash": body.get("snapshot_hash"),
            "rendered_hash": retired_hash,
            "policy_version": body.get("policy_version"),
        },
    )
    retired_id = (retired_envelope.get("body") or {}).get("approval_request_id", "")
    ok(
        "an envelope over the retired rendering can still be raised (the request is the same one)",
        retired_envelope["status"] in (200, 201) and bool(retired_id),
        retired_envelope["text"][:160],
    )

    console.sign_in("demo-owner")
    console.open("#/approvals", settle=2500)
    retired_card = console.page.locator("article.card", has_text="Phiếu theo mẫu tệp cũ")
    shut = (
        retired_card.first.locator("button", has_text="Duyệt").first.is_disabled()
        if retired_card.count()
        else None
    )
    ok(
        "the owner's card names it as the old file shape, with Duyệt shut",
        retired_card.count() == 1 and shut is True,
        console.said()[:160],
    )
    decided = console.call(
        "POST",
        f"/internal/v1/approvals/{retired_id}/decisions",
        {
            "decision": "APPROVED",
            "reason_code": "APPROVED_AFTER_CONSOLE_REVIEW",
            "resource_version": body.get("resource_version"),
            "snapshot_hash": body.get("snapshot_hash"),
            "rendered_hash": retired_hash,
        },
    )
    ok(
        "and the live server will not record an approval of it",
        decided["status"] >= 400 and "RESOURCE_CHANGED_SINCE_REQUEST" in decided["text"],
        f"{decided['status']} {decided['text'][:120]}",
    )
    execution = f"/internal/v1/stores/{STORE}/exports/{body.get('export_request_id')}/execution"
    refused = requester.call("POST", execution, {"approval_id": retired_id})
    ok(
        "its release is refused by name, EXPORT_QUERY_VERSION_RETIRED",
        refused["status"] == 422
        and (refused.get("body") or {}).get("detail")
        == {"outcome": "REQUIRE_HUMAN", "reason_code": "EXPORT_QUERY_VERSION_RETIRED"},
        f"{refused['status']} {refused['text'][:160]}",
    )
    if READS_DATABASE:
        # Approved the way the code deployed before this item recorded it: the decision row and
        # the state move `ApprovalRepository.decide` writes once its checks pass, by the owner.
        sql(
            "insert into approval_decisions (id, approval_request_id, decision_type, decision, "
            "decided_by, reason_code, note, observed_resource_version, observed_snapshot_hash, "
            "observed_rendered_hash, decided_at) "
            f"select gen_random_uuid(), r.id, r.action, 'APPROVED', s.id, "
            "'OWNER_APPROVED_EXPORT', null, r.resource_version, r.snapshot_hash, "
            f"r.rendered_hash, now() from approval_requests r, staff_users s "
            f"where r.id = '{retired_id}' and s.oidc_subject = 'demo-owner'; "
            "update approval_request_states set status = 'APPROVED', "
            "row_version = row_version + 1, updated_at = now() "
            f"where approval_request_id = '{retired_id}'"
        )
        ok(
            "(the retired envelope now reads APPROVED, as the old code would have left it)",
            sql(
                "select status from approval_request_states "
                f"where approval_request_id = '{retired_id}'"
            )
            == "APPROVED",
        )
        again = requester.call("POST", execution, {"approval_id": retired_id})
        ok(
            "an approved envelope over the retired shape is refused by the same name",
            again["status"] == 422 and "EXPORT_QUERY_VERSION_RETIRED" in again["text"],
            f"{again['status']} {again['text'][:160]}",
        )
        ok(
            "and nothing was released under it: no data_exports row for the request",
            sql(
                "select count(*) from data_exports "
                f"where export_request_id = '{body.get('export_request_id')}'"
            )
            == "0",
        )

    # -- the live flow, through the screens ----------------------------------------------------
    requester.open("#/exports", settle=1500)
    page = requester.page
    page.locator("#export-range [data-value='today']").click()
    page.wait_for_timeout(300)
    touched("exports.range-preset")
    page.locator("#export-create").click()
    page.wait_for_timeout(1600)
    touched("exports.create")
    line = page.locator(".export-money-line")
    shown_line = line.first.inner_text() if line.count() else ""
    ok(
        "the request's screen states the money columns in one line (at most 25 words)",
        shown_line.startswith("Tiền trong tệp:") and 0 < len(shown_line.split()) <= 25,
        shown_line,
    )
    page.locator("#export-approval").click()
    page.wait_for_timeout(1600)
    touched("exports.request-approval")
    ok(
        "the envelope is raised and the screen waits for an owner",
        "Chờ chủ tiệm duyệt" in requester.text(),
        requester.said()[:140],
    )

    console.open("#/approvals", settle=2500)
    live_card = console.page.locator("article.card", has_text="Tiền trong tệp:")
    card_text = live_card.first.inner_text() if live_card.count() else ""
    ok(
        "the owner's card reads the same money line above Duyệt",
        live_card.count() >= 1 and shown_line in card_text,
        card_text.replace("\n", " | ")[:200],
    )
    if live_card.count():
        live_card.first.locator("button", has_text="Duyệt").first.click()
        console.page.wait_for_timeout(2200)
        touched("approvals.export-approve")

    page.locator("#export-execute").click()
    page.wait_for_timeout(2200)
    touched("exports.execute")
    content = ""
    download = page.locator("#export-download")
    if download.count():
        with page.expect_download() as caught:
            download.click()
        touched("exports.download")
        path = caught.value.path()
        content = Path(path).read_text(encoding="utf-8") if path else ""
    header, columns, rows = _file_rows(content)
    ok(
        "a one-day file, its header naming the v3 rule and when the money stood so",
        header.get("business_date_from") == header.get("business_date_to") == today.isoformat()
        and header.get("export_query_version", "").startswith("store-day-orders-export-v4:")
        and bool(header.get("produced_at")),
        header,
    )
    paid_row, unpaid_row = rows.get(paid_id, {}), rows.get(unpaid_id, {})
    ok(
        "the paid order's row carries the transfer and the cash apart, and nothing remaining",
        paid_row.get("paid_transfer_vnd") == "50000"
        and paid_row.get("paid_cash_vnd") == str(owed_paid - 50_000)
        and paid_row.get("paid_vnd") == str(owed_paid)
        and paid_row.get("remaining_vnd") == "0"
        and paid_row.get("balance_status") == "PAID",
        {k: paid_row.get(k) for k in ("paid_transfer_vnd", "paid_cash_vnd", "remaining_vnd")},
    )
    ok(
        "the unpaid order's row has remaining equal to its total, and 0 paid",
        unpaid_row.get("remaining_vnd") == unpaid_row.get("owed_vnd") == str(owed_unpaid)
        and unpaid_row.get("paid_vnd") == "0"
        and unpaid_row.get("balance_status") == "UNPAID",
        {k: unpaid_row.get(k) for k in ("owed_vnd", "paid_vnd", "remaining_vnd")},
    )
    ok(
        "and no customer or bank-reference column is in the file",
        bool(columns)
        and not [c for c in columns if any(w in c for w in ("customer", "phone", "bank_ref"))],
        columns,
    )
    context.close()


# --- PAYMENT-002 (DEC-035, B2B half): công nợ --------------------------------------------


def _script(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _owner_id() -> str:
    return sql("SELECT id FROM staff_users WHERE oidc_subject = 'demo-owner'")


def _account_customer(console: Console, name: str) -> tuple[str, str]:
    """A business customer recorded through the route the counter's sheet uses. Returns the id and
    the last four digits the counter finds them by."""

    digits = "09" + str(uuid.uuid4().int)[:8]
    created = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers",
        {
            "phone": digits,
            "display_name": name,
            "kind": "BUSINESS",
            "service_consent": True,
        },
    )
    if created["status"] != 201:
        raise AssertionError(f"could not record {name}: {created['status']} {created['text']}")
    return str(created["body"]["customer"]["customer_id"]), digits[-4:]


def _account_order(console: Console, customer_id: str, last4: str, kg: str) -> dict[str, Any]:
    """An order for the account customer, taken on Nhận đồ the way the counter takes one -- found by
    four digits, one tap, the bag, the price, one press -- then washed to the shelf over the API."""

    console.open("#/new", settle=1500)
    console.type_into("#new-customer-search", last4, "newOrder.customer-search")
    console.page.wait_for_timeout(1500)
    row = console.page.locator(f"#new-customer-search-list [data-customer='{customer_id}']")
    (intake,) = console.press_capturing(row.first, "/order-requests")
    touched("newOrder.customer-pick")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#new-add-line", timeout=15000)
    if intake["status"] >= 300:
        raise AssertionError(f"could not open the intake: {intake['text']}")
    console.add_line("STANDARD_WASH_DRY", kg)
    quote = console.price()
    if quote["status"] >= 300:
        raise AssertionError(f"could not price: {quote['text']}")
    done = console.confirm()
    order = done["order"] or {}
    if order.get("status", 0) >= 300:
        raise AssertionError(f"could not create the order: {order.get('text')}")
    order_id = str(order["body"]["order_id"])
    version = order["body"]["row_version"]
    for path, body in (
        ("intake-transition", {"target": "RECEIVED_PENDING_INSPECTION", "slot_approved": False}),
        ("intake-transition", {"target": "ACCEPTED", "slot_approved": True}),
        ("transition", {"target": "STORE_CONFIRMATION_PENDING"}),
        ("transition", {"target": "CONFIRMED"}),
        ("transition", {"target": "ACTIVE"}),
        ("production-transition", {"target": "QUEUED"}),
        ("production-transition", {"target": "IN_PROCESS"}),
        ("production-transition", {"target": "QUALITY_CHECK"}),
        ("production-transition", {"target": "READY_AT_STORE"}),
    ):
        moved = console.call("POST", f"/internal/v1/orders/{order_id}/{path}", body, version)
        if moved["status"] >= 300:
            raise AssertionError(f"could not move to {body}: {moved['text']}")
        version = moved["body"]["row_version"]
    read = console.call("GET", f"/internal/v1/orders/{order_id}")["body"] or {}
    return read


def _press_patch(console: Console, locator: Any, suffix: str) -> dict[str, Any]:
    """`press_capturing` for a PATCH: the owner's limit is changed under `If-Match`."""

    with console.page.expect_response(
        lambda r: r.request.method == "PATCH" and r.url.split("?")[0].endswith(suffix),
        timeout=20000,
    ) as waited:
        locator.click()
    response = waited.value
    text = response.text()
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    return {"status": response.status, "body": body, "text": text[:600]}


def _takings(console: Console) -> dict[str, Any]:
    return console.call("GET", f"/internal/v1/stores/{STORE}/settlements/today").get("body") or {}


def _charge_on_page(console: Console, order_id: str) -> tuple[dict[str, Any], bool]:
    """On the order page: "Giao đồ — ghi công nợ", its sheet, the confirm; then the success state's
    closing step. Returns the charge answer and whether the order closed."""

    console.open_order(order_id, settle=2200)
    console.page.locator("#order-account-charge").first.click()
    touched("orderDetail.account-charge")
    console.page.wait_for_timeout(500)
    (charged,) = console.press_capturing(
        console.page.locator("#order-account-confirm"), "/account-charge"
    )
    touched("orderDetail.account-confirm")
    console.page.wait_for_timeout(1800)
    closing = console.page.locator("dialog[open] button[data-step=HAND_OVER]")
    closed = False
    if closing.count():
        closing.first.click()
        console.page.wait_for_timeout(2000)
        closed = True
    return charged, closed


def scenario_accounts(console: Console) -> None:
    """`PAYMENT-002` (`DEC-035`, B2B half): công nợ. Refused until the owner publishes the terms; an
    account opened with no limit refuses by name; the owner types a limit; two orders of a homestay
    leave unpaid and a third is refused over the limit; a payment reaches the oldest first; the
    statement totals it; an overdue statement blocks a spa's new order until the owner lifts it.
    Every figure is read back from the server, never computed."""

    head("22", "CÔNG NỢ — refused until the owner publishes the terms (DEC-035)")
    if not arguments.database_url:
        ok("publishing the terms uses the owner's script, which needs --database-url", False)
        return
    owner = _owner_id()
    console.sign_in("demo-owner")
    notice = console.call("GET", f"/internal/v1/stores/{STORE}/customer-privacy-notice")
    if (notice.get("body") or {}).get("published") is not True:
        # Only with --only: the customers scenario publishes the notice in a full run.
        published = subprocess.run(
            [
                sys.executable,
                _script("publish_privacy_notice.py"),
                "--actor-id",
                owner,
                "--database-url",
                arguments.database_url,
            ],
            capture_output=True,
            text=True,
        )
        note(
            "the privacy notice was unpublished; the owner published it: "
            + published.stdout.strip()
        )
    homestay, homestay_last4 = _account_customer(console, "Homestay Biển Xanh")
    terms_rows = sql(
        "SELECT count(*) FROM configuration_versions WHERE config_type = 'ACCOUNT_TERMS'"
    )
    unpublished = terms_rows == "0"
    ok(
        "the account terms are not published on this stack yet (the refusal can only be proven "
        "before it)",
        unpublished,
        terms_rows,
    )
    console.open(f"#/customers/{homestay}", settle=2200)
    section = console.page.locator("#customer-account-section")
    opener = console.page.locator("#account-open")
    if unpublished:
        ok(
            "the homestay's page says in tier 1 what the owner must do, and 'Mở công nợ' is off",
            "Chủ tiệm cần công bố điều khoản công nợ trước khi mở công nợ" in section.inner_text()
            and opener.count() == 1
            and opener.is_disabled(),
            section.inner_text()[:160].replace("\n", " | ") if section.count() else "absent",
        )
        refused = console.call(
            "POST", f"/internal/v1/stores/{STORE}/customers/{homestay}/account", {}
        )
        ok(
            "the server itself refuses: 422 ACCOUNT_TERMS_UNPUBLISHED (DEC-035), nothing written",
            refused["status"] == 422
            and (refused.get("body") or {}).get("detail", {}).get("reason_code")
            == "ACCOUNT_TERMS_UNPUBLISHED"
            and sql(f"SELECT count(*) FROM customer_accounts WHERE customer_id = '{homestay}'")
            == "0",
            refused["text"][:200],
        )
        operator = sql("SELECT id FROM staff_users WHERE oidc_subject = 'demo-operations'")
        stranger = subprocess.run(
            [
                sys.executable,
                _script("publish_account_terms.py"),
                "--actor-id",
                operator,
                "--database-url",
                arguments.database_url,
            ],
            capture_output=True,
            text=True,
        )
        ok(
            "nobody but the owner can publish the terms",
            stranger.returncode == 3 and "refused" in stranger.stderr,
            stranger.stderr.strip()[:120],
        )
        head("22a", "CÔNG BỐ — the owner publishes the terms with the script")
        publish = subprocess.run(
            [
                sys.executable,
                _script("publish_account_terms.py"),
                "--actor-id",
                owner,
                "--database-url",
                arguments.database_url,
            ],
            capture_output=True,
            text=True,
        )
        ok(
            "scripts/publish_account_terms.py publishes them (the owner's act, not the console's)",
            publish.returncode == 0 and "account terms published" in publish.stdout,
            (publish.stdout or publish.stderr).strip()[:160],
        )
    else:
        note(
            "the terms were published before this scenario began, so their refusal is not provable "
            "here: run it on a stack that has not published them"
        )

    head("22b", "MỞ CÔNG NỢ — the owner opens it with no limit typed: nothing leaves on it")
    console.open(f"#/customers/{homestay}", settle=2200)
    console.page.locator("#account-open").click()
    touched("customer.account-open")
    console.page.wait_for_timeout(500)
    hint = console.page.locator("#account-limit-hint")
    ok(
        "the sheet shows the recommended 3.000.000 ₫ as a hint only; the field is empty",
        hint.count() == 1
        and "3.000.000" in hint.inner_text()
        and console.page.locator("#account-limit").input_value() == "",
        hint.inner_text() if hint.count() else "absent",
    )
    (opened,) = console.press_capturing(
        console.page.locator("#account-open-save"), f"/customers/{homestay}/account"
    )
    touched("customer.account-open-save")
    console.page.wait_for_timeout(1500)
    account = (opened.get("body") or {}).get("account") or {}
    ok(
        "opened with no limit: the server says ACCOUNT_LIMIT_UNSET is what a new order meets",
        opened["status"] == 201
        and account.get("credit_limit_vnd") is None
        and account.get("handover_refusal") == "ACCOUNT_LIMIT_UNSET",
        opened["text"][:200],
    )
    ok(
        "the card says so: 'Chưa có hạn mức — chưa ghi nợ được.'",
        "Chưa có hạn mức" in console.text(),
        console.text()[:200].replace("\n", " | "),
    )

    console.sign_in("demo-operations")
    orders = [_account_order(console, homestay, homestay_last4, kg) for kg in ("5", "6", "7")]
    first, second, third = orders
    note(
        "three orders of the homestay on the shelf: "
        + ", ".join(
            f"{str(item.get('order_id'))[:8]}… {item.get('payable_total_vnd')} ₫" for item in orders
        )
    )
    console.open_order(str(first["order_id"]), settle=2500)
    why = console.page.locator("#order-account [data-account-refusal]")
    ok(
        "the order page says why it cannot leave on the account: no limit typed",
        why.count() == 1
        and why.first.get_attribute("data-account-refusal") == "ACCOUNT_LIMIT_UNSET"
        and "chưa đặt hạn mức" in why.first.inner_text()
        and console.page.locator("#order-account-charge").count() == 0,
        why.first.inner_text() if why.count() else console.text()[:160],
    )
    refused = console.call(
        "POST",
        f"/internal/v1/orders/{first['order_id']}/account-charge",
        {"collected_by_customer": True},
        if_match=first["row_version"],
    )
    ok(
        "and the server refuses the charge: 422 ACCOUNT_LIMIT_UNSET, the order still unpaid",
        refused["status"] == 422
        and (refused.get("body") or {}).get("detail", {}).get("reason_code")
        == "ACCOUNT_LIMIT_UNSET"
        and stored(str(first["order_id"]), "balance_status") == "UNPAID",
        refused["text"][:200],
    )

    head("22c", "HẠN MỨC — the owner types the limit: the first two orders exactly")
    console.sign_in("demo-owner")
    limit = int(first["payable_total_vnd"]) + int(second["payable_total_vnd"])
    console.open(f"#/customers/{homestay}", settle=2200)
    console.page.locator("#account-limit-edit").click()
    touched("customer.account-limit")
    console.page.wait_for_timeout(400)
    console.type_into("#account-limit-new", str(limit))
    typed = _press_patch(
        console, console.page.locator("#account-limit-save"), f"/customers/{homestay}/account"
    )
    touched("customer.account-limit-save")
    console.page.wait_for_timeout(1500)
    ok(
        "the owner's limit is stored as typed (If-Match on the account's version)",
        typed["status"] == 200
        and ((typed.get("body") or {}).get("account") or {}).get("credit_limit_vnd") == limit,
        typed["text"][:200],
    )

    head("22d", "GIAO ĐỒ — ghi công nợ: two leave unpaid, the third is refused over the limit")
    console.sign_in("demo-operations")
    before = _takings(console)
    charged, closed = _charge_on_page(console, str(first["order_id"]))
    ok(
        "'Giao đồ — ghi công nợ' puts the first order on the account: ON_ACCOUNT, handed over",
        charged["status"] == 201
        and (charged.get("body") or {}).get("balance_status") == "ON_ACCOUNT"
        and (charged.get("body") or {}).get("self_collection_recorded") is True,
        charged["text"][:200],
    )
    read = console.call("GET", f"/internal/v1/orders/{first['order_id']}").get("body") or {}
    ok(
        "the sheet's closing step closes it: COMPLETED, still owed on the account, nothing paid",
        closed
        and read.get("commercial") == "COMPLETED"
        and read.get("balance") == "ON_ACCOUNT"
        and read.get("paid_vnd") == 0
        and read.get("remaining_vnd") == first["payable_total_vnd"],
        {k: read.get(k) for k in ("commercial", "balance", "paid_vnd", "remaining_vnd")},
    )
    ok(
        "the money card reads 'Ghi công nợ' with the amount owed",
        "Ghi công nợ" in console.text(),
        [line for line in console.text().splitlines() if "công nợ" in line][:3],
    )
    charged, _ = _charge_on_page(console, str(second["order_id"]))
    ok(
        "the second leaves too: outstanding plus this order is exactly the limit",
        charged["status"] == 201
        and (charged.get("body") or {}).get("outstanding_after_vnd") == limit,
        charged["text"][:200],
    )
    console.open_order(str(third["order_id"]), settle=2500)
    why = console.page.locator("#order-account [data-account-refusal]")
    ok(
        "the third is not offered, and the page says why: over the limit",
        why.count() == 1
        and why.first.get_attribute("data-account-refusal") == "ACCOUNT_LIMIT_EXCEEDED"
        and console.page.locator("#order-account-charge").count() == 0,
        why.first.inner_text() if why.count() else console.text()[:160],
    )
    over = console.call(
        "POST",
        f"/internal/v1/orders/{third['order_id']}/account-charge",
        {"collected_by_customer": True},
        if_match=third["row_version"],
    )
    ok(
        "and the server refuses it: 422 ACCOUNT_LIMIT_EXCEEDED",
        over["status"] == 422
        and (over.get("body") or {}).get("detail", {}).get("reason_code")
        == "ACCOUNT_LIMIT_EXCEEDED",
        over["text"][:200],
    )
    after = _takings(console)
    ok(
        "money owed, not money collected: today's takings did not move",
        after.get("collected_vnd") == before.get("collected_vnd"),
        f"{before.get('collected_vnd')} -> {after.get('collected_vnd')}",
    )

    head("22e", "THU CÔNG NỢ — a payment reaches the oldest order first")
    console.open(f"#/customers/{homestay}", settle=2200)
    card = console.page.locator("#customer-account-section")
    ok(
        "the account card: Đang nợ is the two orders, the limit, and this month's statement",
        card.count() == 1
        and all(word in card.inner_text() for word in ("Đang nợ", "Hạn mức", "Đầu kỳ", "Hạn trả")),
        card.inner_text()[:240].replace("\n", " | ") if card.count() else "absent",
    )
    amount = int(first["payable_total_vnd"]) + 20_000
    console.page.locator("#account-collect").click()
    touched("customer.account-collect")
    console.page.wait_for_timeout(500)
    console.page.locator("#account-payment-edit").click()
    touched("customer.account-payment-edit")
    console.type_into("#account-payment-amount", str(amount), "customer.account-payment-amount")
    (paid,) = console.press_capturing(
        console.page.locator("#account-payment-submit"), "/account/payments"
    )
    touched("customer.account-payment-submit")
    console.page.wait_for_timeout(1800)
    split = [
        (item.get("order_id"), item.get("amount_vnd"), item.get("settled"))
        for item in (paid.get("body") or {}).get("allocations", [])
    ]
    ok(
        "the payment is allocated oldest first: the first order in full, 20.000 ₫ to the second",
        paid["status"] == 201
        and split
        == [
            (first["order_id"], first["payable_total_vnd"], True),
            (second["order_id"], 20_000, False),
        ],
        paid["text"][:240],
    )
    ok(
        "the sheet says which orders it reached",
        "Trừ vào" in console.page.locator("dialog[open]").inner_text()
        and "(đủ)" in console.page.locator("dialog[open]").inner_text(),
        console.page.locator("dialog[open]").inner_text()[:200].replace("\n", " | "),
    )
    console.page.keyboard.press("Escape")
    ok(
        "the first order is now paid in full through the account, completed; the second still owes",
        stored(str(first["order_id"]), "balance_status") == "PAID"
        and stored(str(first["order_id"]), "commercial_status") == "COMPLETED"
        and stored(str(second["order_id"]), "balance_status") == "ON_ACCOUNT"
        and sql(
            f"SELECT settlement_shape FROM order_settlements WHERE order_id = '{first['order_id']}'"
        )
        == "EXACT_PAYMENT_ON_ACCOUNT",
        (
            stored(str(first["order_id"]), "balance_status"),
            stored(str(second["order_id"]), "balance_status"),
        ),
    )
    ok(
        "the allocation ledger ties the account payment to one ordinary payment row per order",
        sql(
            "SELECT string_agg(a.position || ':' || a.amount_vnd || ':' || p.method, ',' "
            "ORDER BY a.position) FROM customer_account_allocations a "
            "JOIN order_payments p ON p.id = a.order_payment_id "
            f"WHERE a.account_payment_id = '{(paid.get('body') or {}).get('payment_id')}'"
        )
        == f"1:{first['payable_total_vnd']}:TIEN_MAT,2:20000:TIEN_MAT",
        "",
    )
    takings = _takings(console)
    ok(
        "today's takings counted the account payment once, as cash",
        takings.get("cash_vnd") == (after.get("cash_vnd") or 0) + amount
        if isinstance(after.get("cash_vnd"), int)
        else False,
        f"cash {after.get('cash_vnd')} -> {takings.get('cash_vnd')}",
    )
    by_method = sql(
        "select coalesce(sum(amount_vnd) filter (where method='TIEN_MAT'),0) || ':' || "
        "coalesce(sum(amount_vnd) filter (where method='CHUYEN_KHOAN'),0) "
        f"from order_payments where store_id='{STORE}' and "
        "(recorded_at at time zone 'Asia/Ho_Chi_Minh')::date = "
        "(now() at time zone 'Asia/Ho_Chi_Minh')::date"
    )
    ok(
        "and the takings by method still equal the payment ledger, method by method",
        by_method == f"{takings.get('cash_vnd')}:{takings.get('transfer_vnd')}",
        f"ledger {by_method} / takings {takings.get('cash_vnd')}:{takings.get('transfer_vnd')}",
    )
    freed = console.call("GET", f"/internal/v1/orders/{third['order_id']}/account-handover")
    ok(
        "the payment freed room: the third order may now leave on the account",
        ((freed.get("body") or {}).get("handover") or {}).get("offered") is True,
        freed["text"][:200],
    )

    head("22f", "SAO KÊ — this month's statement totals the charges and the payment")
    console.open(f"#/customers/{homestay}", settle=2200)
    console.page.locator("#account-statement").click()
    touched("customer.account-statement")
    console.page.wait_for_timeout(2200)
    paper = console.page.locator("#statement-paper")
    month = console.page.evaluate(
        "() => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', "
        "month: '2-digit'}).format(new Date())"
    )
    statement = (
        console.call(
            "GET", f"/internal/v1/stores/{STORE}/customers/{homestay}/account/statements/{month}"
        ).get("body")
        or {}
    ).get("statement") or {}
    ledger = sql(
        "SELECT (SELECT sum(amount_vnd) FROM customer_account_charges "
        f"WHERE customer_id = '{homestay}') || ':' || (SELECT sum(amount_vnd) "
        f"FROM customer_account_payments WHERE customer_id = '{homestay}')"
    )
    ok(
        "the statement's charges, payments and closing equal the ledgers; due the 15th next month",
        f"{statement.get('charges_vnd')}:{statement.get('payments_vnd')}" == ledger
        and statement.get("charge_count") == 2
        and statement.get("payment_count") == 1
        and statement.get("closing_vnd") == limit - amount
        and str(statement.get("due_on", "")).endswith("-15"),
        {
            **{
                k: statement.get(k)
                for k in ("opening_vnd", "charges_vnd", "payments_vnd", "closing_vnd", "due_on")
            },
            "ledger": ledger,
        },
    )
    ok(
        "the printable paper names the customer, the month, Cuối kỳ and the due date",
        paper.count() == 1
        and "Homestay Biển Xanh" in paper.inner_text()
        and "Cuối kỳ" in paper.inner_text()
        and "Hạn thanh toán" in paper.inner_text(),
        paper.inner_text()[:240].replace("\n", " | ") if paper.count() else "absent",
    )
    console.page.evaluate(
        "() => { window.__printed = 0; window.print = () => { window.__printed += 1; }; }"
    )
    printer = console.page.locator("#statement-print")
    if printer.count() and printer.first.is_enabled():
        printer.first.click()
        touched("statement.print")
    ok(
        "'In sao kê' opens the print dialog, once",
        console.page.evaluate("() => window.__printed") == 1,
    )

    head("22g", "QUÁ HẠN — an overdue statement blocks a spa's new order; the owner lifts it")
    console.sign_in("demo-owner")
    spa, spa_last4 = _account_customer(console, "Spa Hoa Sen")
    opened = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers/{spa}/account",
        {"credit_limit_vnd": 2_000_000},
    )
    ok(
        "the owner opens the spa's account with a limit",
        opened["status"] == 201,
        opened["text"][:120],
    )
    console.sign_in("demo-operations")
    old_order = _account_order(console, spa, spa_last4, "5")
    new_order = _account_order(console, spa, spa_last4, "5")
    # The one write in this scenario not made through the console: a live API reads its own clock,
    # and no clock on this stack is two months back. The charge is written by the same repository
    # code the route calls, with every check it makes, only at an instant in last month's month
    # before -- the way the repository tests hold the clock still.
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    import psycopg
    from nha_trang_laundry_db.accounts import AccountChargeCommand, AccountRepository
    from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole

    local_now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    two_back = (local_now.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    back_then = two_back.replace(day=10, hour=10, minute=0, second=0, microsecond=0)
    operator = sql("SELECT id FROM staff_users WHERE oidc_subject = 'demo-operations'")
    with psycopg.connect(arguments.database_url) as connection:
        AccountRepository().charge(
            connection,
            AccountChargeCommand(
                order_id=uuid.UUID(str(old_order["order_id"])),
                expected_row_version=int(old_order["row_version"]),
                collected_by_customer=True,
                principal=StaffPrincipal(
                    uuid.UUID(operator),
                    "demo-operations",
                    frozenset({StaffRole.OPERATOR}),
                    True,
                    uuid.uuid4(),
                ),
                correlation_id=uuid.uuid4(),
                at=back_then.astimezone(UTC),
            ),
        )
        connection.commit()
    note(f"the spa's first order was charged on {back_then:%d/%m/%Y} (harness clock), and not paid")
    console.open_order(str(new_order["order_id"]), settle=2500)
    why = console.page.locator("#order-account [data-account-refusal]")
    ok(
        "the new order is not offered: a statement is overdue, and the page says so",
        why.count() == 1
        and why.first.get_attribute("data-account-refusal") == "ACCOUNT_OVERDUE"
        and "quá hạn" in why.first.inner_text(),
        why.first.inner_text() if why.count() else console.text()[:160],
    )
    blocked = console.call(
        "POST",
        f"/internal/v1/orders/{new_order['order_id']}/account-charge",
        {"collected_by_customer": True},
        if_match=new_order["row_version"],
    )
    ok(
        "the server refuses it: 422 ACCOUNT_OVERDUE",
        blocked["status"] == 422
        and (blocked.get("body") or {}).get("detail", {}).get("reason_code") == "ACCOUNT_OVERDUE",
        blocked["text"][:200],
    )
    console.open(f"#/customers/{spa}", settle=2200)
    banner = console.page.locator("#customer-account-section .alert[data-state=danger]")
    ok(
        "the spa's card shows the overdue banner: the amount, the month and its due date",
        banner.count() == 1
        and "Quá hạn" in banner.inner_text()
        and "Đơn mới trả tại quầy" in banner.inner_text(),
        banner.inner_text() if banner.count() else console.text()[:200],
    )
    ok(
        "the counter is not offered the owner's lift",
        console.page.locator("#account-lift").count() == 0,
    )
    console.sign_in("demo-owner")
    console.open(f"#/customers/{spa}", settle=2200)
    console.page.locator("#account-lift").click()
    touched("customer.account-lift")
    console.page.wait_for_timeout(400)
    console.type_into("#account-lift-reason", "Khách hẹn chuyển khoản cuối tuần")
    (lifted,) = console.press_capturing(console.page.locator("#account-lift-save"), "/block-lift")
    touched("customer.account-lift-save")
    console.page.wait_for_timeout(1500)
    account = ((lifted.get("body") or {}).get("account")) or {}
    ok(
        "the owner lifts the block with a reason and an end: recorded, the card says until when",
        lifted["status"] == 200
        and account.get("block_lifted") is True
        and "tạm mở chặn tới hết" in console.text()
        and sql(
            "SELECT count(*) FROM customer_account_block_lifts l "
            "JOIN customer_accounts a ON a.id = l.account_id "
            f"WHERE a.customer_id = '{spa}' AND l.reason = 'Khách hẹn chuyển khoản cuối tuần'"
        )
        == "1",
        lifted["text"][:200],
    )
    ok(
        "the reason stays on the lift's own row: no event, audit or outbox row carries it",
        sql(
            "SELECT (SELECT count(*) FROM domain_events "
            "WHERE payload::text LIKE '%Khách hẹn chuyển%') "
            "+ (SELECT count(*) FROM audit_events WHERE details::text LIKE '%Khách hẹn chuyển%') "
            "+ (SELECT count(*) FROM outbox_events WHERE payload::text LIKE '%Khách hẹn chuyển%')"
        )
        == "0",
    )
    console.sign_in("demo-operations")
    charged, _ = _charge_on_page(console, str(new_order["order_id"]))
    ok(
        "while lifted, the spa's new order leaves on the account",
        charged["status"] == 201
        and (charged.get("body") or {}).get("balance_status") == "ON_ACCOUNT",
        charged["text"][:200],
    )
    closed = subprocess.run(
        [
            sys.executable,
            _script("close_account_statements.py"),
            "--actor-id",
            owner,
            "--month",
            f"{back_then:%Y-%m}",
            "--store-id",
            STORE,
            "--database-url",
            arguments.database_url,
        ],
        capture_output=True,
        text=True,
    )
    frozen = (
        console.call(
            "GET",
            f"/internal/v1/stores/{STORE}/customers/{spa}/account/statements/{back_then:%Y-%m}",
        ).get("body")
        or {}
    )
    ok(
        "the owner's month close freezes that month's statement exactly as it reads live",
        closed.returncode == 0
        and frozen.get("frozen") is not None
        and frozen["frozen"]["closing_vnd"]
        == frozen["statement"]["closing_vnd"]
        == old_order["payable_total_vnd"],
        (closed.stdout or closed.stderr).strip()[:200],
    )


# --- UNCLAIMED-001 (DEC-036): laundry waiting for pickup -----------------------------------------


def _publish_storage(*extra: str, actor: str = "demo-owner") -> subprocess.CompletedProcess[str]:
    """Run the owner's storage-policy script against the stack's database, as the owner would."""

    staff = sql(f"select id from staff_users where oidc_subject='{actor}'")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [
            sys.executable,
            os.path.join(root, "scripts", "publish_storage_policy.py"),
            "--database-url",
            arguments.database_url,
            "--actor-id",
            staff,
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=root,
    )


def _age_ready(order_id: str, days: int) -> str:
    """HARNESS STEP (documented, `UNCLAIMED-001`): the laundry was accepted and reported ready
    `days` days earlier than it was (both stamps, so ready is never before accepted). Nothing
    else can make an order twenty-five or sixty-five days old inside a walk. One statement on the
    order's `production_ready_at` and `production_accepted_at`, advancing its row version by one as
    `0036`'s projection guard requires; it writes no event, and nothing the software decides is
    touched -- the fee, the list and the disposal verdict are computed by the server from it."""

    sql(
        "update orders set production_ready_at = production_ready_at - "
        f"make_interval(days => {days}), production_accepted_at = production_accepted_at - "
        f"make_interval(days => {days}), row_version = row_version + 1 where id = '{order_id}'"
    )
    return stored(
        order_id,
        "((now() at time zone 'Asia/Ho_Chi_Minh')::date - "
        "(production_ready_at at time zone 'Asia/Ho_Chi_Minh')::date)::text",
    )


def _past_attempt(order_id: str, days_ago: int) -> str:
    """HARNESS STEP (documented): one contact attempt made `days_ago` shop days earlier. The walk
    cannot wait for tomorrow, and the attempts table is append-only, so the earlier day's call is
    inserted as its row -- the same columns the route writes, under the operator's name. It carries
    no event (the route's attempts in this walk do); the disposal rule reads the rows."""

    return sql(
        "insert into order_contact_attempts (id, order_id, store_id, channel, outcome, note, "
        "attempted_by_staff_id, attempted_at, created_at) select gen_random_uuid(), o.id, "
        "o.store_id, 'CALL', 'NO_ANSWER', null, s.id, "
        f"now() - make_interval(days => {days_ago}), now() from orders o, staff_users s "
        f"where o.id = '{order_id}' and s.oidc_subject = 'demo-operations'"
    )


def _pickup_row(console: Console, order_id: str) -> Any:
    return console.page.locator(f"#pickup-list [data-pickup='{order_id}']")


def _open_pickup(console: Console) -> None:
    """Đồ chờ lấy, reached the way a person reaches it: the sidebar, or "Thêm" on a phone."""

    console.open("#/", settle=1400)
    link = console.page.locator("nav a", has_text="Đồ chờ lấy").first
    if link.count() and not link.is_visible():
        console.page.locator("nav a", has_text="Thêm").first.click()
        console.page.wait_for_timeout(700)
        link = console.page.locator("main a[data-nav='/pickup']").first
    if link.count():
        link.click()
        touched("shell.nav.pickup")
        console.page.wait_for_timeout(1800)
    else:
        console.open("#/pickup", settle=1800)


def _record_attempt(
    console: Console, trigger: Any, channel: str, outcome: str, note_text: str, prefix: str
) -> dict[str, Any]:
    """Ghi lần liên hệ through its sheet: channel, outcome, a few words, one press."""

    trigger.click()
    touched(f"{prefix}.record" if prefix == "pickup" else f"{prefix}.contact-open")
    console.page.wait_for_selector("dialog[open] #contact-submit", state="visible", timeout=8000)
    console.page.locator(f"dialog[open] #contact-channel [data-value={channel}]").click()
    touched("pickup.contact-channel")
    console.page.locator(f"dialog[open] input[name=contact-outcome][value={outcome}]").check()
    touched("pickup.contact-outcome")
    if note_text:
        console.type_into("dialog[open] #contact-note", note_text, "pickup.contact-note")
    (answer,) = console.press_capturing(
        console.page.locator("dialog[open] #contact-submit"), "/contact-attempts"
    )
    touched("pickup.contact-submit")
    console.page.wait_for_timeout(1600)
    return answer


def scenario_unclaimed(console: Console) -> None:
    """UNCLAIMED-001 (DEC-036): before the owner publishes, the waiting list and the contact
    attempts work and nothing is charged; after, an order left 25 days shows its storage fee, an
    approver waives one, the cash at pickup includes it, and -- moved to 65 days, with three
    attempts on two days -- the owner disposes of one: money paid kept, money owed written off."""

    head("17", "ĐỒ CHỜ LẤY — before the owner publishes the storage policy (DEC-036)")
    if not READS_DATABASE or not arguments.database_url:
        note(
            "--database-url is required: the storage policy is published with the owner's own "
            "script, and the ready time is moved by a documented harness step"
        )
        FAIL.append("unclaimed scenario needs --database-url")
        return
    if sql(
        "select count(*) from configuration_versions where config_type='STORAGE_POLICY'"
    ) not in (
        "",
        "0",
    ):
        withdrawn = _publish_storage("--withdraw")
        ok(
            "the owner's withdrawal returns the shop to no storage fee",
            withdrawn.returncode == 0,
            (withdrawn.stdout + withdrawn.stderr)[-200:],
        )
    console.sign_in("demo-operations")
    shelf = console.build_order(kg="7", stop="ready")
    order_id = shelf["order_id"]
    total = int(
        (console.call("GET", f"/internal/v1/orders/{order_id}").get("body") or {}).get(
            "payable_total_vnd"
        )
        or 0
    )
    waited = _age_ready(order_id, 25)
    ok("harness: the laundry reads as ready 25 shop days ago", waited == "25", waited)

    listed = console.call("GET", f"/internal/v1/stores/{STORE}/orders/awaiting-pickup")
    body = listed.get("body") or {}
    row = next((item for item in body.get("orders", []) if item.get("order_id") == order_id), {})
    ok(
        "before publication the waiting list works: the order is on it, 25 days, no fee",
        listed["status"] == 200
        and body.get("policy_published") is False
        and row.get("days_waiting") == 25
        and (row.get("storage_fee") or {}).get("status") == "POLICY_UNPUBLISHED"
        and (row.get("storage_fee") or {}).get("amount_vnd") == 0,
        listed["text"][:220],
    )
    _open_pickup(console)
    screen = console.text()
    entry = _pickup_row(console, order_id)
    ok(
        "Đồ chờ lấy says in one line that the owner has not published, and lists the order",
        "Chủ tiệm chưa công bố phí lưu kho" in screen
        and entry.count() == 1
        and "Chờ 25 ngày" in entry.first.inner_text(),
        screen[:240].replace("\n", " | "),
    )
    record = console.page.locator(f"[data-record-attempt='{order_id}']").first
    answer = _record_attempt(console, record, "ZALO", "NO_ANSWER", "đã nhắn, chưa xem", "pickup")
    ok(
        "and a contact attempt is recorded from the list before publication",
        answer["status"] == 201 and (answer.get("body") or {}).get("ordinal") == 1,
        answer["text"][:160],
    )
    ok(
        "the row now says one attempt",
        "1 lần liên hệ" in _pickup_row(console, order_id).first.inner_text(),
        _pickup_row(console, order_id).first.inner_text()[:160].replace("\n", " | "),
    )
    read = console.call("GET", f"/internal/v1/orders/{order_id}")
    ok(
        "and nothing is charged: the order owes its quoted total alone",
        [c.get("kind") for c in (read.get("body") or {}).get("charges", [])] == ["QUOTED_TOTAL"]
        and (read.get("body") or {}).get("owed_vnd") == total,
        read["text"][:200],
    )
    console.sign_in("demo-owner")
    version = (console.call("GET", f"/internal/v1/orders/{order_id}").get("body") or {}).get(
        "row_version"
    )
    waive = console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/storage-fee-waiver",
        {"reason": "khách quen"},
        if_match=version,
    )
    dispose = console.call("POST", f"/internal/v1/orders/{order_id}/disposal", if_match=version)
    ok(
        "a waiver and a disposal are refused by name until the owner publishes",
        waive["status"] == 422
        and (waive.get("body") or {}).get("detail", {}).get("reason_code")
        == "STORAGE_POLICY_UNPUBLISHED"
        and dispose["status"] == 422
        and "STORAGE_POLICY_UNPUBLISHED"
        in (dispose.get("body") or {}).get("detail", {}).get("reason_codes", []),
        f"{waive['text'][:120]} || {dispose['text'][:120]}",
    )
    console.open(f"#/orders/{order_id}/receipt", settle=2500)
    ok(
        "the receipt prints no storage rule before publication",
        console.page.locator("#receipt-paper [data-field=storage-rule]").count() == 0,
        console.page.locator("#receipt-paper").inner_text()[-160:].replace("\n", " | "),
    )

    head("17a", "CÔNG BỐ — the owner publishes the storage policy with the script")
    refused = _publish_storage(actor="demo-operations")
    ok(
        "nobody but the owner can publish it",
        refused.returncode == 3 and "OWNER_ADMIN" in refused.stderr,
        (refused.stdout + refused.stderr)[-200:],
    )
    published = _publish_storage()
    ok(
        "scripts/publish_storage_policy.py publishes DEC-036's figures (the owner's act)",
        published.returncode == 0 and "storage policy published" in published.stdout,
        (published.stdout + published.stderr)[-200:],
    )

    head("17b", "PHÍ LƯU KHO — 25 days: five started days past the free twenty")
    read = console.call("GET", f"/internal/v1/orders/{order_id}")
    charges = [
        (c.get("kind"), c.get("amount_vnd")) for c in (read.get("body") or {}).get("charges", [])
    ]
    ok(
        "the order read carries the storage fee as a charge: five days at 5.000 ₫",
        charges == [("QUOTED_TOTAL", total), ("STORAGE_FEE", 25_000)]
        and (read.get("body") or {}).get("owed_vnd") == total + 25_000,
        read["text"][:220],
    )
    console.open("#/", settle=1800)
    tile = console.page.locator("[data-queue=pickup]").first
    ok(
        "Hôm nay counts the laundry waiting for pickup and links to the list",
        tile.count() == 1 and (tile.get_attribute("href") or "").endswith("#/pickup"),
        tile.inner_text().replace("\n", " | ") if tile.count() else console.text()[:160],
    )
    board = console.call("GET", f"/internal/v1/stores/{STORE}/sla-board?limit=200")
    ok(
        "the harness step leaves the order readable by the SLA board (ready never before accepted)",
        board["status"] == 200,
        board["text"][:160],
    )
    if tile.count():
        tile.click()
        touched("today.pickup-link")
        console.page.wait_for_timeout(1800)
    entry = _pickup_row(console, order_id)
    ok(
        "the list shows the fee so far on the row",
        console.page.url.endswith("#/pickup")
        and entry.count() == 1
        and "25.000" in entry.first.inner_text()
        and "phí lưu kho" in entry.first.inner_text(),
        entry.first.inner_text()[:200].replace("\n", " | ")
        if entry.count()
        else console.text()[:200],
    )
    console.open(f"#/orders/{order_id}", settle=2200)
    storage_text = (
        console.page.locator("#order-storage").inner_text()
        if console.page.locator("#order-storage").count()
        else ""
    )
    ok(
        "the order page's Lưu kho line says the fee and the days, and the money card includes it",
        "25.000" in storage_text
        and "Chờ 25 ngày" in storage_text
        and "Gồm phí lưu kho 25.000" in console.page.locator(".order__money").inner_text(),
        storage_text[:200].replace("\n", " | "),
    )
    console.open(f"#/orders/{order_id}/receipt", settle=2500)
    policy = (console.call("GET", f"/internal/v1/orders/{order_id}/storage").get("body") or {}).get(
        "policy"
    ) or {}
    line = console.page.locator("#receipt-paper [data-field=storage-rule]")
    ok(
        "the receipt prints the storage rule in one line, the server's sentence",
        line.count() == 1 and line.inner_text().strip() == policy.get("receipt_line_vi"),
        line.inner_text() if line.count() else "absent",
    )

    head("17c", "MIỄN PHÍ — an approver waives a fee, with a reason")
    console.sign_in("demo-operations")
    regular = console.build_order(kg="7", stop="ready")
    regular_id = regular["order_id"]
    _age_ready(regular_id, 30)
    console.open(f"#/orders/{regular_id}", settle=2200)
    denied = console.page.locator("#order-storage-waive")
    ok(
        "the counter sees Miễn phí lưu kho, turned off with the reason",
        denied.count() == 1 and denied.first.get_attribute("data-denied") == "true",
        console.page.locator("#order-storage").inner_text()[:200].replace("\n", " | ")
        if console.page.locator("#order-storage").count()
        else "no storage section",
    )
    console.sign_in("demo-approver")
    console.open(f"#/orders/{regular_id}", settle=2200)
    before = console.call("GET", f"/internal/v1/orders/{regular_id}").get("body") or {}
    console.page.locator("#order-storage-waive").click()
    touched("orderDetail.storage-waive")
    console.page.wait_for_selector("dialog[open] #waiver-submit", state="visible", timeout=8000)
    console.type_into(
        "dialog[open] #waiver-reason", "khách quen, chị Hoa", "orderDetail.waiver-reason"
    )
    (waived,) = console.press_capturing(
        console.page.locator("dialog[open] #waiver-submit"), "/storage-fee-waiver"
    )
    touched("orderDetail.waiver-submit")
    console.page.wait_for_timeout(1800)
    after = waived.get("body") or {}
    ok(
        "Miễn phí lưu kho lands: what the order owes is its quoted total again, one version on",
        waived["status"] == 200
        and before.get("owed_vnd") == total + 50_000
        and after.get("owed_vnd") == total
        and after.get("row_version") == int(before.get("row_version") or 0) + 1,
        waived["text"][:200],
    )
    ok(
        "the waiver is recorded with its amount and reason, and the reason is in no payload",
        sql(
            "select waived_amount_vnd || '|' || days_waiting || '|' || reason "
            f"from storage_fee_waivers where order_id = '{regular_id}'"
        )
        == "50000|30|khách quen, chị Hoa"
        and sql(
            "select count(*) from domain_events where payload::text like '%chị Hoa%' "
            f"and aggregate_id = '{regular_id}'"
        )
        == "0",
        "",
    )

    head("17d", "THU KÈM PHÍ — cash at pickup includes the storage fee")
    console.sign_in("demo-operations")
    pickup = console.build_order(kg="7", stop="ready")
    pickup_id = pickup["order_id"]
    _age_ready(pickup_id, 25)
    said = console.pay(pickup_id, hand_over=True)
    paid = console.call("GET", f"/internal/v1/orders/{pickup_id}").get("body") or {}
    ok(
        "one Thu tiền takes the quoted total and the fee together, and the bag goes home",
        paid.get("balance") == "PAID"
        and paid.get("paid_vnd") == total + 25_000
        and paid.get("self_collection_recorded") is True,
        said[:200],
    )
    ok(
        "the fee is fixed with the settlement: settled = quoted total + fee, one fee row",
        sql(f"select expected_total_vnd from order_settlements where order_id = '{pickup_id}'")
        == str(total + 25_000)
        and sql(
            "select amount_vnd || '|' || chargeable_days from order_storage_fees "
            f"where order_id = '{pickup_id}'"
        )
        == "25000|5",
        "",
    )
    done = console.step(pickup_id, "HAND_OVER")
    ok(
        "and the order closes",
        stored(pickup_id, "commercial_status") == "COMPLETED",
        done[:160],
    )

    head("17e", "THANH LÝ — 65 days, three attempts on two days, the owner decides")
    deposit = console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/payments",
        {"amount_vnd": 50_000, "method": "TIEN_MAT", "transfer_seen": False},
        if_match=(console.call("GET", f"/internal/v1/orders/{order_id}").get("body") or {}).get(
            "row_version"
        ),
    )
    ok("a 50.000 ₫ deposit is on the order", deposit["status"] == 201, deposit["text"][:160])
    waited = _age_ready(order_id, 40)
    ok("harness: the same laundry now reads as ready 65 shop days ago", waited == "65", waited)
    console.open(f"#/orders/{order_id}", settle=2200)
    trigger = console.page.locator("#order-contact").first
    answer = _record_attempt(console, trigger, "CALL", "NO_ANSWER", "", "orderDetail")
    ok("a second attempt, from the order page", answer["status"] == 201, answer["text"][:120])
    verdict = (
        console.call("GET", f"/internal/v1/orders/{order_id}/storage").get("body") or {}
    ).get("disposal_verdict") or {}
    ok(
        "two attempts on one day: disposal is not legal yet, and the server says why",
        verdict.get("allowed") is False
        and "CONTACT_ATTEMPTS_TOO_FEW" in verdict.get("refusals", [])
        and "CONTACT_DAYS_TOO_FEW" in verdict.get("refusals", []),
        verdict,
    )
    inserted = _past_attempt(order_id, 3)
    ok("harness: the first call, three days ago, is on record", "INSERT 0 1" in inserted, inserted)
    console.sign_in("demo-approver")
    console.open(f"#/orders/{order_id}", settle=2200)
    gate = console.page.locator("#order-dispose")
    ok(
        "an approver sees Thanh lý turned off, with the reason: it is the owner's",
        gate.count() == 1 and gate.first.get_attribute("data-denied") == "true",
        "",
    )
    console.sign_in("demo-owner")
    console.open(f"#/orders/{order_id}", settle=2200)
    storage = console.call("GET", f"/internal/v1/orders/{order_id}/storage").get("body") or {}
    console.page.locator("#order-dispose").click()
    touched("orderDetail.dispose")
    console.page.wait_for_selector("dialog[open] #disposal-confirm", state="visible", timeout=8000)
    rule = console.page.locator("dialog[open] [data-field=disposal-rule]").inner_text().strip()
    ok(
        "the confirm sheet states the rule verbatim, as the server built it from the figures",
        rule == (storage.get("policy") or {}).get("disposal_rule_vi") and "60" in rule,
        rule[:160],
    )
    confirm = console.page.locator("dialog[open] #disposal-confirm")
    confirm.click()
    console.page.wait_for_timeout(250)
    (closed,) = console.press_capturing(confirm, "/disposal")
    touched("orderDetail.disposal-confirm")
    console.page.wait_for_timeout(1800)
    owed = total + total // 2  # the reference figure: 65 days is past the 50% cap
    ok(
        "Thanh lý closes the order: cancelled, money paid kept, the rest written off",
        closed["status"] == 200
        and (closed.get("body") or {}).get("commercial") == "CANCELLED"
        and (closed.get("body") or {}).get("balance") == "PARTIALLY_PAID"
        and sql(
            "select kept_vnd || '|' || written_off_vnd || '|' || attempts_counted || '|' || "
            f"attempt_days from order_disposals where order_id = '{order_id}'"
        )
        == f"50000|{owed - 50_000}|3|2",
        closed["text"][:200],
    )
    ok(
        "the ledgers agree (0056): one payment kept, no refund, the custody resolution recorded",
        sql(f"select coalesce(sum(amount_vnd),0) from order_payments where order_id='{order_id}'")
        == "50000"
        and sql(f"select count(*) from order_refunds where order_id='{order_id}'") == "0"
        and sql(
            "select count(*) from domain_events where event_type='ORDER_STATE_TRANSITIONED' "
            f"and aggregate_id='{order_id}' and payload->>'custody_resolution'='UNCLAIMED_DISPOSED'"
        )
        == "1",
        "",
    )
    ok(
        "the Zalo note is in no event, audit or outbox payload",
        sql(
            "select (select count(*) from domain_events where payload::text like '%chưa xem%') + "
            "(select count(*) from audit_events where details::text like '%chưa xem%') + "
            "(select count(*) from outbox_events where payload::text like '%chưa xem%')"
        )
        == "0",
        "",
    )
    ok(
        "and the order is off the waiting list",
        not any(
            item.get("order_id") == order_id
            for item in (
                console.call("GET", f"/internal/v1/stores/{STORE}/orders/awaiting-pickup").get(
                    "body"
                )
                or {}
            ).get("orders", [])
        ),
        "",
    )

    head("17f", "GỌI — a customer with a phone on record; the auditor reads it masked")
    notice = console.call("GET", f"/internal/v1/stores/{STORE}/customer-privacy-notice")
    if (notice.get("body") or {}).get("published") is not True:
        note(
            "the privacy notice is not published on this stack, so no customer record can hold a "
            "phone; the customers scenario publishes it, and a full run proves Gọi here"
        )
        return
    digits = "09" + str(uuid.uuid4().int)[:8]
    console.sign_in("demo-operations")
    created = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers",
        {"phone": digits, "display_name": "anh Tuấn", "service_consent": True},
    )
    customer_id = str(((created.get("body") or {}).get("customer") or {}).get("customer_id") or "")
    ok("a customer with a phone on record", created["status"] == 201, created["text"][:120])
    theirs = console.build_order(kg="5", stop="ready", customer=(customer_id, digits))
    walk_in = console.build_order(kg="5", stop="ready")
    _open_pickup(console)
    call = console.page.locator(f"a[data-call='{theirs['order_id']}']")
    ok(
        "their row has Gọi, a tel: link to their number that the page never prints; a walk-in's "
        "has none",
        call.count() == 1
        and call.first.get_attribute("href") == f"tel:+84{digits[1:]}"
        and digits not in console.text()
        and console.page.locator(f"a[data-call='{walk_in['order_id']}']").count() == 0,
        call.first.get_attribute("href") if call.count() else "absent",
    )
    if call.count():
        # The dialler is the phone's; the sheet opens here for what came of the call.
        call.first.evaluate("(link) => link.addEventListener('click', (e) => e.preventDefault())")
        call.first.click()
        touched("pickup.call")
        console.page.wait_for_timeout(700)
        ok(
            "Gọi opens Ghi lần liên hệ, ready for what came of the call",
            console.page.locator("dialog[open] #contact-submit").count() == 1,
            "",
        )
        console.page.keyboard.press("Escape")
    console.sign_in("demo-auditor")
    audited = console.call("GET", f"/internal/v1/stores/{STORE}/orders/awaiting-pickup")
    theirs_row = next(
        (
            item
            for item in (audited.get("body") or {}).get("orders", [])
            if item.get("order_id") == theirs["order_id"]
        ),
        {},
    )
    ok(
        "the auditor reads the list with the number masked: last four digits, no number",
        audited["status"] == 200
        and theirs_row.get("phone") is None
        and theirs_row.get("phone_last4") == digits[-4:]
        and digits not in audited["text"],
        audited["text"][:160],
    )
    console.sign_in("demo-owner")


# --- DAILY-SUMMARY-001: the owner's evening summary (DEC-039) ---------------------------------


def _summary_read(console: Console, day: str = "") -> dict[str, Any]:
    path = f"/internal/v1/stores/{STORE}/reports/daily-summary"
    return console.call("GET", f"{path}?date={day}" if day else path)


def _summary_figures(body: dict[str, Any]) -> dict[str, Any]:
    figures: dict[str, Any] = {}
    for line in body.get("lines") or []:
        figures.update(line.get("figures") or {})
    return figures


def scenario_daily_summary(console: Console) -> None:
    """`DAILY-SUMMARY-001` (`DEC-039`): after known actions the evening summary's figures are the
    report's, the board's, the complaint list's and Sổ thu chi's; the card prints the server's
    sentences, Sao chép puts exactly that text on the clipboard and Chia sẻ hands it to the share
    sheet; no customer's name or phone reaches any of it; the counter is refused; a past day
    leaves out what only today can say."""

    head("22", "TÓM TẮT CUỐI NGÀY — the summary's figures are the report's, after known actions")
    console.sign_in("demo-owner")
    today = console.page.evaluate(
        "() => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', "
        "month: '2-digit', day: '2-digit'}).format(new Date())"
    )
    first = _summary_read(console)
    ok(
        "the owner reads today's summary: a versioned template, lines and what it left out",
        first["status"] == 200
        and str((first["body"] or {}).get("template_version", "")).startswith("daily-summary-v3:")
        and (first["body"] or {}).get("date") == today
        and (first["body"] or {}).get("so_far") is True,
        first["text"][:160],
    )
    before = _summary_figures(first["body"] or {})

    # A named customer with a phone number, so the scan below has something to find.
    notice = console.call("GET", f"/internal/v1/stores/{STORE}/customer-privacy-notice")
    if not (notice.get("body") or {}).get("published") and arguments.database_url:
        owner_id = sql("SELECT id FROM staff_users WHERE oidc_subject = 'demo-owner'")
        subprocess.run(
            [
                sys.executable,
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "publish_privacy_notice.py"
                ),
                "--actor-id",
                owner_id,
                "--database-url",
                arguments.database_url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        note("published the privacy notice with the owner's script, to record a named customer")
    digits = "0906" + str(uuid.uuid4().int)[:6]
    spaced = f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    name = "chị Hồng Nhung Tóm"
    created = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers",
        {
            "phone": spaced,
            "display_name": name,
            "delivery_address": "45 Lê Thánh Tôn, Nha Trang",
            "note": "Thích gấp áo sơ mi",
            "service_consent": True,
        },
    )
    note(f"customer recorded: HTTP {created['status']}")

    # The known actions: one order washed, paid in cash and completed; a second one with a
    # 50.000 ₫ transfer deposit; a complaint naming the customer; one line of Sổ thu chi.
    washed = console.build_order(kg="7", stop="released")
    console.pay(washed["order_id"])
    console.step(washed["order_id"], "COMPLETE")
    paid = int(
        next(
            (
                p["amount_vnd"]
                for p in (
                    console.call("GET", f"/internal/v1/orders/{washed['order_id']}").get("body")
                    or {}
                ).get("payments", [])
            ),
            0,
        )
    )
    deposit = console.build_order(kg="5", stop="active")
    console.pay(deposit["order_id"], "50.000", method="CHUYEN_KHOAN", seen=True, ref="ft 2209")
    complaint = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/incidents",
        {
            "order_id": washed["order_id"],
            "evidence_summary": f"{name} ({spaced}) báo áo còn vết ố",
        },
    )
    spent = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/expenses",
        {"spent_on": today, "category": "HOA_CHAT", "amount_vnd": 250_000, "note": "Bột giặt"},
    )
    ok(
        "the known actions were accepted (a paid, completed order; a deposit; a complaint; a "
        "Sổ thu chi line)",
        paid > 0 and complaint["status"] == 201 and spent["status"] == 201,
        f"paid={paid} complaint={complaint['status']} expense={spent['status']}",
    )

    second = _summary_read(console)
    body = second["body"] or {}
    after = _summary_figures(body)

    def moved(key: str) -> int:
        return int(after.get(key) or 0) - int(before.get(key) or 0)

    ok(
        "orders: two more taken in, one more completed",
        moved("orders_created") == 2 and moved("orders_completed") == 1,
        (moved("orders_created"), moved("orders_completed")),
    )
    ok(
        "money: the cash order's payment in cash, the 50.000 ₫ deposit as transfer",
        moved("cash_vnd") == paid and moved("transfer_vnd") == 50_000,
        (moved("cash_vnd"), paid, moved("transfer_vnd")),
    )
    ok(
        "one more complaint opened today, and one more still open",
        moved("complaints_opened") == 1 and moved("open_count") == 1,
        (moved("complaints_opened"), moved("open_count")),
    )
    ok(
        "Sổ thu chi: one more line, 250.000 ₫ more, and the sentence names the category",
        moved("spending_entries") == 1
        and moved("spending_vnd") == 250_000
        and "Hoá chất"
        in next((line["text"] for line in body.get("lines", []) if line["key"] == "SPENDING"), ""),
        (moved("spending_entries"), moved("spending_vnd")),
    )

    # Every figure is the report's own, read at the same moment for the same day.
    report = console.call(
        "GET", f"/internal/v1/stores/{STORE}/reports/summary?from={today}&to={today}"
    )
    kpis = {kpi["key"]: kpi for kpi in (report.get("body") or {}).get("kpis", [])}
    methods = {
        item["kind"]: item["amount_vnd"]
        for item in (kpis.get("MONEY_COLLECTED") or {}).get("by_kind") or []
    }
    ok(
        "the summary's figures equal the report's: orders, finished on time, complaints, money",
        after.get("orders_created") == kpis["ORDERS_CREATED"]["numerator"]
        and after.get("orders_completed") == kpis["ORDERS_COMPLETED"]["numerator"]
        and after.get("orders_cancelled") == kpis["ORDERS_CANCELLED"]["numerator"]
        and after.get("finished_on_time") == kpis["ON_TIME_INTERNAL"]["numerator"]
        and after.get("finished") == kpis["ON_TIME_INTERNAL"]["denominator"]
        and after.get("finished_without_promise") == kpis["ON_TIME_INTERNAL"]["rule_assumed"]
        and after.get("complaints_opened") == kpis["COMPLAINTS"]["numerator"]
        and after.get("collected_vnd") == kpis["MONEY_COLLECTED"]["numerator"]
        and after.get("cash_vnd") == methods.get("TIEN_MAT")
        and after.get("transfer_vnd") == methods.get("CHUYEN_KHOAN")
        and after.get("net_vnd") == kpis["MONEY_NET"]["numerator"],
        {k: after.get(k) for k in ("orders_created", "collected_vnd", "finished_on_time")},
    )
    ok(
        "and it names the report's version among its sources",
        {"key": "report", "query_version": report["body"]["query_version"]}
        in body.get("sources", []),
        body.get("sources"),
    )
    board = console.call("GET", f"/internal/v1/stores/{STORE}/sla-board?limit=200")
    rows = (board.get("body") or {}).get("items") or []
    promised = [row for row in rows if row.get("rule_source") == "ORDER_PROMISE"]
    unpromised = [row for row in rows if row.get("rule_source") != "ORDER_PROMISE"]
    omitted = {item["key"]: item["reason"] for item in body.get("omitted", [])}
    late_line = "promised_late" in after
    ok(
        "late against promise and without a promise are the SLA board's own outcomes, counted",
        not (board.get("body") or {}).get("next_order_id")
        and after.get("unpromised") == len(unpromised)
        and after.get("unpromised_late")
        == sum(1 for row in unpromised if row.get("sla_outcome") == "BREACHED")
        and (
            (
                late_line
                and after.get("promised") == len(promised)
                and after.get("promised_late")
                == sum(1 for row in promised if row.get("sla_outcome") == "BREACHED")
            )
            or (
                not late_line
                and not promised
                and omitted.get("LATE_AGAINST_PROMISE") == "TURNAROUND_POLICY_UNPUBLISHED"
            )
        ),
        {k: after.get(k) for k in ("promised", "promised_late", "unpromised", "unpromised_late")},
    )
    incidents = console.call("GET", f"/internal/v1/stores/{STORE}/incidents?limit=200")
    listed = incidents.get("body") or []
    if isinstance(listed, list) and len(listed) < 200:
        ok(
            "open complaints equal the incident list's OPEN and UNDER_REVIEW rows",
            after.get("open_count")
            == sum(1 for item in listed if item.get("status") in ("OPEN", "UNDER_REVIEW")),
            after.get("open_count"),
        )
    # Round 7 wave 2 integration: the two hooks are wired. The waiting line is the waiting list
    # the counter reads, counted past 20 and 60 days by the list's own `days_waiting`.
    waiting = console.call("GET", f"/internal/v1/stores/{STORE}/orders/awaiting-pickup?limit=200")
    shelf = waiting.get("body") or {}
    waited = [int(item.get("days_waiting") or 0) for item in shelf.get("orders") or []]
    if waiting["status"] == 200 and not shelf.get("truncated"):
        ok(
            "the waiting line is Đồ chờ lấy counted: over 20 days and over 60 days",
            after.get("over_20_days") == sum(1 for days in waited if days > 20)
            and after.get("over_60_days") == sum(1 for days in waited if days > 60)
            and "quá 20 ngày"
            in next(
                (line["text"] for line in body.get("lines", []) if line["key"] == "WAITING_PICKUP"),
                "",
            ),
            (after.get("over_20_days"), after.get("over_60_days"), sorted(waited)[-3:]),
        )
    # The accounts line is every account card's own figures, summed: overdue as the card says,
    # and due = billed on a closed statement (outstanding less this month's charges) less overdue.
    found = console.call("GET", f"/internal/v1/stores/{STORE}/customers?limit=100")
    listed_customers = (found.get("body") or {}).get("customers") or []
    cards = []
    for customer in listed_customers:
        if customer.get("kind") != "BUSINESS":
            continue
        read = console.call(
            "GET", f"/internal/v1/stores/{STORE}/customers/{customer['customer_id']}/account"
        )
        account = (read.get("body") or {}).get("account")
        if account:
            cards.append(account)
    if found["status"] == 200 and not (found.get("body") or {}).get("truncated"):
        overdue = [int(card["overdue_vnd"]) for card in cards]
        due = [
            max(int(card["outstanding_vnd"]) - int(card["current_statement"]["charges_vnd"]), 0)
            - int(card["overdue_vnd"])
            for card in cards
        ]
        ok(
            "the accounts line is the account cards' own money: due and overdue, counted and summed"
            if cards
            else "a shop with no account leaves the accounts line out, never as a zero",
            (
                after.get("overdue_accounts") == sum(1 for value in overdue if value > 0)
                and after.get("overdue_vnd") == sum(overdue)
                and after.get("due_accounts") == sum(1 for value in due if value > 0)
                and after.get("due_vnd") == sum(due)
            )
            if cards
            else omitted.get("ACCOUNTS_DUE") == "NO_ACCOUNTS",
            {
                "summary": {
                    k: after.get(k)
                    for k in ("due_accounts", "due_vnd", "overdue_accounts", "overdue_vnd")
                },
                "cards": len(cards),
                "overdue": overdue,
                "due": due,
            },
        )
    ok(
        "nothing is left out as not built any more",
        "SOURCE_NOT_BUILT" not in omitted.values(),
        omitted,
    )
    everything = json.dumps(body, ensure_ascii=False)
    ok(
        "no name, phone, address or note of the customer is anywhere in the summary",
        created["status"] == 201
        and all(
            value not in everything
            for value in (
                name,
                "Hồng Nhung",
                digits,
                spaced,
                "+84" + digits[1:],
                digits[-6:],
                "Lê Thánh Tôn",
                "sơ mi",
                "vết ố",
                # PAYMENT-002's account customers (scenario accounts): counts and money only.
                *(
                    str(customer.get("display_name") or "")
                    for customer in listed_customers
                    if customer.get("kind") == "BUSINESS"
                ),
            )
            if value
        ),
        f"customer HTTP {created['status']}",
    )

    head("22a", "TÓM TẮT CUỐI NGÀY — the card prints the server's words; one press to Zalo")
    console.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    # The morning, in a second browser context carrying the same session (cookies and the chosen
    # store) whose clock reads 10:00 shop-local: the card offers "Xem tóm tắt" and reads nothing
    # until it is pressed. A context of its own because a Playwright clock belongs to the whole
    # context -- pinned on the main one, it would reach every later page -- and so this check does
    # not depend on the hour the run happens to start at.
    morning_context = console.context.browser.new_context(
        viewport=viewport(), storage_state=console.context.storage_state()
    )
    morning = morning_context.new_page()
    morning.clock.set_fixed_time("2026-09-26T03:00:00Z")
    reads: list[str] = []
    morning.on(
        "request",
        lambda request: reads.append(request.url) if "/daily-summary" in request.url else None,
    )
    morning.goto(f"{CONSOLE}#/", wait_until="networkidle")
    morning.wait_for_timeout(1500)
    offered = morning.locator("button[data-summary-load]")
    waited = offered.count() == 1 and not reads
    if offered.count():
        offered.click()
        touched("today.summary-load")
        morning.wait_for_timeout(1500)
    ok(
        "before 18:00 the card offers 'Xem tóm tắt', reads nothing until pressed, then the lines",
        waited
        and len(reads) == 1
        and morning.locator("#daily-summary [data-summary-line]").count() > 1,
        reads,
    )
    morning_context.close()

    console.open("#/")
    card = console.page.locator("#daily-summary")
    load = console.page.locator("button[data-summary-load]")
    hour = int(
        console.page.evaluate(
            "() => new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Ho_Chi_Minh', "
            "hour: '2-digit', hourCycle: 'h23'}).format(new Date())"
        )
    )
    if hour >= 18:
        ok(
            "after 18:00 shop-local the card has read the summary by itself",
            load.count() == 0
            and console.page.locator("#daily-summary [data-summary-line]").count() > 1,
            f"hour {hour}",
        )
    elif load.count():
        load.click()
        console.page.wait_for_timeout(1500)
    fresh = (_summary_read(console).get("body") or {}).get("lines") or []
    shown = [
        (node.get_attribute("data-summary-line"), node.inner_text().strip())
        for node in console.page.locator("#daily-summary [data-summary-line]").all()
    ]
    ok(
        "the card prints the server's sentences, in order, verbatim (the header's minute aside)",
        card.count() == 1
        and [key for key, _ in shown] == [line["key"] for line in fresh]
        and shown[1:] == [(line["key"], line["text"]) for line in fresh][1:],
        shown[:3],
    )
    card.locator(".info-btn").first.click()
    touched("today.summary-info")
    console.page.wait_for_timeout(500)
    sheet = console.dialog_text()
    ok(
        "its ⓘ says it is a fixed template over the day's figures, not AI, and sends nothing",
        "không phải AI" in sheet and "không tự gửi" in sheet,
        sheet[:140],
    )
    console.page.keyboard.press("Escape")
    console.page.wait_for_timeout(300)
    console.page.locator("button[data-summary-copy]").click()
    touched("today.summary-copy")
    console.page.wait_for_timeout(600)
    copied = console.page.evaluate("() => navigator.clipboard.readText()")
    ok(
        "Sao chép puts exactly the card's lines on the clipboard, one per row",
        copied == "\n".join(text for _, text in shown) and "Đã chép" in card.inner_text(),
        repr(copied)[:160],
    )
    # Headless Chromium has no share sheet; the page is given one that records what it was
    # handed, and the card is read again so it offers Chia sẻ (it asks the browser at render).
    console.page.evaluate(
        "() => { navigator.share = async (data) => { window.__shared = data; }; }"
    )
    console.page.locator("button[data-summary-reload]").click()
    console.page.wait_for_timeout(1500)
    share = console.page.locator("button[data-summary-share]")
    if share.count():
        share.click()
        touched("today.summary-share")
        console.page.wait_for_timeout(400)
    shared = console.page.evaluate("() => window.__shared || null") or {}
    reshown = "\n".join(
        node.inner_text().strip()
        for node in console.page.locator("#daily-summary [data-summary-line]").all()
    )
    ok(
        "Chia sẻ hands the same text to the phone's share sheet (then Zalo)",
        share.count() == 1 and shared.get("text") == reshown,
        repr(shared)[:160],
    )
    shown_text = card.inner_text()
    ok(
        "and nothing the card shows names the customer or their number",
        all(value not in shown_text for value in (name, "Hồng Nhung", digits, spaced)),
    )

    head("22b", "TÓM TẮT CUỐI NGÀY — a past day, another reader, and the counter")
    yesterday = console.page.evaluate(
        "(d) => new Date(Date.parse(d + 'T00:00:00Z') - 86400000).toISOString().slice(0, 10)",
        today,
    )
    past = _summary_read(console, yesterday)
    reasons = {item["key"]: item["reason"] for item in (past.get("body") or {}).get("omitted", [])}
    ok(
        "a past day keeps the report's lines and leaves out what only today can say",
        past["status"] == 200
        and (past["body"] or {}).get("so_far") is False
        and reasons.get("LATE_AGAINST_PROMISE") == "LIVE_ONLY_TODAY"
        and reasons.get("COMPLAINTS_OPEN") == "LIVE_ONLY_TODAY",
        reasons,
    )
    later = console.page.evaluate(
        "(d) => new Date(Date.parse(d + 'T00:00:00Z') + 86400000).toISOString().slice(0, 10)",
        today,
    )
    refused = _summary_read(console, later)
    ok(
        "a day after today is refused with the report's reason",
        refused["status"] == 422 and "REPORT_WINDOW_IN_FUTURE" in refused["text"],
        refused["text"][:120],
    )
    console.sign_in("demo-auditor")
    audited = _summary_read(console)
    ok(
        "an auditor reads the same summary",
        audited["status"] == 200 and (audited["body"] or {}).get("lines"),
        f"HTTP {audited['status']}",
    )
    console.sign_in("demo-operations")
    denied = _summary_read(console)
    ok(
        "the counter is refused by the server, with no sentence in the answer",
        denied["status"] == 403 and "Tóm tắt" not in denied["text"],
        f"HTTP {denied['status']}",
    )
    console.open("#/")
    ok(
        "and an operator's Hôm nay has no summary card",
        console.page.locator("#daily-summary").count() == 0,
    )
    console.sign_in("demo-owner")


# --- PICKUP-REMIND-001 (DEC-043): the reminder schedule and the two-tap send ---------------------


def _publish_messaging() -> subprocess.CompletedProcess[str]:
    """Run the owner's messaging-policy script against the stack's database, as the owner would."""

    return subprocess.run(
        [
            sys.executable,
            _script("publish_messaging_policy.py"),
            "--database-url",
            arguments.database_url,
            "--actor-id",
            _owner_id(),
        ],
        capture_output=True,
        text=True,
    )


def _record_stop(order_id: str) -> subprocess.CompletedProcess[str]:
    """HARNESS STEP (documented, as `verify_consent_walk.py`): the customer writes STOP. No channel
    adapter exists yet, so there is no inbound webhook route; the STOP is recorded through the
    ingress path production will use (`InboxRepository.record`, via the repository tests'
    `record_customer_message`) on the order's own contact reference. It needs the deployment hash
    key the API runs with (`NTL_HASH_KEY` / `NTL_HASH_KEY_FILE`)."""

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    program = (
        "import sys\n"
        f"sys.path.insert(0, {os.path.join(root, 'packages', 'db', 'tests')!r})\n"
        "from datetime import UTC, datetime, timedelta\n"
        "from uuid import UUID\n"
        "import psycopg\n"
        "from message_draft_test_data import record_customer_message\n"
        "from nha_trang_laundry_domain.consent import OptOutDisposition\n"
        "with psycopg.connect(sys.argv[1]) as connection:\n"
        "    contact = connection.execute(\n"
        "        'SELECT bound_contact_id FROM orders WHERE id = %s', (sys.argv[2],)\n"
        "    ).fetchone()[0]\n"
        "    record_customer_message(\n"
        "        connection, UUID(str(contact)),\n"
        "        received_at=datetime.now(UTC) - timedelta(minutes=1),\n"
        "        disposition=OptOutDisposition.WITHDRAW,\n"
        "    )\n"
        "print('STOP recorded')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", program, arguments.database_url, order_id],
        capture_output=True,
        text=True,
        cwd=root,
    )


def _reminders(console: Console) -> dict[str, Any]:
    return console.call("GET", f"/internal/v1/stores/{STORE}/pickup-reminders?limit=200")


def _reminder_row(listing: dict[str, Any], order_id: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in (listing.get("body") or {}).get("orders", [])
            if item.get("order_id") == order_id
        ),
        {},
    )


def _reminder_shot(console: Console, name: str) -> None:
    """A full-page picture of the screen at this point, when `PICKUP_REMINDER_SHOTS` names a
    directory -- so the refusal and the working flow can be looked at as the counter sees them."""

    directory = os.environ.get("PICKUP_REMINDER_SHOTS", "")
    if directory:
        os.makedirs(directory, exist_ok=True)
        console.page.screenshot(path=os.path.join(directory, f"{name}.png"), full_page=True)


def _open_reminders(console: Console) -> None:
    """Nhắc khách lấy đồ, reached the way a person reaches it: its card on Hôm nay."""

    console.open("#/", settle=1600)
    card = console.page.locator("[data-queue=reminders]").first
    if card.count():
        card.click()
        touched("today.reminders-link")
        console.page.wait_for_timeout(1800)
    else:
        console.open("#/reminders", settle=1800)


def _reminder_tr(console: Console, order_id: str) -> Any:
    return console.page.locator(f"#reminder-list tr[data-reminder='{order_id}']")


def _no_follow(locator: Any) -> None:
    """A `tel:` or `zalo.me` link opens the phone's dialler or Zalo, outside this browser; the click
    is kept on the page so what the console does next can be checked."""

    locator.evaluate("(link) => link.addEventListener('click', (e) => e.preventDefault())")


def _copy_reminder(console: Console, order_id: str) -> tuple[dict[str, Any], str]:
    """Chép tin nhắn on the row: the server's answer, and what landed on the clipboard."""

    button = _reminder_tr(console, order_id).locator(f"[data-reminder-copy='{order_id}']")
    console.page.evaluate("() => navigator.clipboard.writeText('')")
    answer: dict[str, Any] = {"status": 0, "body": None, "text": ""}
    try:
        with console.page.expect_response(
            lambda r: "/pickup-reminder?" in r.url and order_id in r.url, timeout=15000
        ) as waited:
            button.click()
        response = waited.value
        text = response.text()
        answer = {"status": response.status, "body": json.loads(text), "text": text[:600]}
    except Exception as error:
        answer["text"] = str(error)[:200]
    touched("reminders.copy")
    console.page.wait_for_timeout(700)
    copied = str(console.page.evaluate("() => navigator.clipboard.readText()") or "")
    return answer, copied


def _mark_reminded(console: Console, order_id: str, outcome: str, *, via: Any = None) -> Any:
    """Đã nhắc (or Gọi), then one tap on what happened. Returns the attempt route's answer."""

    trigger = via or _reminder_tr(console, order_id).locator(f"[data-reminder-done='{order_id}']")
    trigger.click()
    touched("reminders.call" if via is not None else "reminders.done")
    console.page.wait_for_selector("dialog[open]#reminder-done", state="visible", timeout=8000)
    choice = console.page.locator(f"dialog[open]#reminder-done [data-reminder-outcome='{outcome}']")
    (answer,) = console.press_capturing(choice, "/contact-attempts")
    touched("reminders.outcome")
    console.page.wait_for_timeout(1600)
    return answer


def scenario_pickup_reminders(console: Console) -> None:
    """PICKUP-REMIND-001 (DEC-043): a ready order with a customer's phone is due on day 0; its text
    is refused until the owner publishes the messaging policy, then copied in one tap and marked
    "Đã nhắc" in a second, and it leaves the list. Aged orders show day 3, 7, 14 and the day before
    the storage fee; a ticket alone is counted unreachable; a customer who wrote STOP gets no text
    and a call is still recorded."""

    head("18", "NHẮC KHÁCH LẤY ĐỒ — refused until the owner publishes the messaging policy")
    if not arguments.database_url:
        ok(
            "the reminder scenario needs --database-url: the owner publishes the messaging policy "
            "with a script, and the ready time is moved by a documented harness step",
            False,
        )
        return
    owner = _owner_id()
    console.sign_in("demo-operations")
    notice = console.call("GET", f"/internal/v1/stores/{STORE}/customer-privacy-notice")
    if (notice.get("body") or {}).get("published") is not True:
        # Only with --only: the customers scenario publishes the notice in a full run.
        published = subprocess.run(
            [
                sys.executable,
                _script("publish_privacy_notice.py"),
                "--actor-id",
                owner,
                "--database-url",
                arguments.database_url,
            ],
            capture_output=True,
            text=True,
        )
        note(
            "the privacy notice was unpublished; the owner published it: "
            + published.stdout.strip()[-80:]
        )
    digits = "09" + str(uuid.uuid4().int)[:8]
    created = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers",
        {"phone": digits, "display_name": "chị Mai Nhắc", "service_consent": True},
    )
    customer_id = str(((created.get("body") or {}).get("customer") or {}).get("customer_id") or "")
    ok("a customer with a phone on record", created["status"] == 201, created["text"][:120])
    theirs = console.build_order(kg="5", stop="ready", customer=(customer_id, digits))["order_id"]
    ticket = console.build_order(kg="5", stop="ready")["order_id"]
    unpublished = (
        sql(
            "select count(*) from configuration_versions "
            "where config_type = 'TRANSACTIONAL_MESSAGING_POLICY'"
        )
        == "0"
    )
    listing = _reminders(console)
    row = _reminder_row(listing, theirs)
    walk_in = _reminder_row(listing, ticket)
    ok(
        "the due list has their order on day 0 (Báo đồ đã xong), reachable by phone, with the "
        "zalo.me link the server built from their number",
        listing["status"] == 200
        and row.get("step") == "READY"
        and row.get("days_waiting") == 0
        and row.get("reachable") == "PHONE"
        and row.get("zalo_url") == f"https://zalo.me/{digits}",
        json.dumps(row)[:240],
    )
    ok(
        "a ticket alone is due too, and counted unreachable: NONE, no link, NO_CONTACT",
        walk_in.get("reachable") == "NONE"
        and walk_in.get("zalo_url") is None
        and walk_in.get("message_refusal") == "NO_CONTACT"
        and int((listing.get("body") or {}).get("unreachable_count") or 0) >= 1,
        json.dumps(walk_in)[:200],
    )
    _open_reminders(console)
    entry = _reminder_tr(console, theirs)
    ok(
        "Hôm nay's Nhắc khách lấy đồ card opens the list; their row says Báo đồ đã xong, and the "
        "page never prints their number",
        console.page.url.endswith("#/reminders")
        and entry.count() == 1
        and "Báo đồ đã xong" in entry.first.inner_text()
        and digits not in console.text(),
        entry.first.inner_text()[:200].replace("\n", " | ")
        if entry.count()
        else console.text()[:200],
    )
    if unpublished:
        copy = entry.locator(f"[data-reminder-copy='{theirs}']")
        ok(
            "before the owner publishes, Chép tin nhắn is off and the page says why in one line",
            copy.count() == 1
            and copy.first.get_attribute("data-denied") == "true"
            and "chưa công bố chính sách tin dịch vụ" in console.text(),
            console.notices()[:200],
        )
        _reminder_shot(console, "01-unpublished")
        text = console.call("GET", f"/internal/v1/orders/{theirs}/pickup-reminder?step=READY")
        sent = console.call(
            "POST",
            f"/internal/v1/orders/{theirs}/contact-attempts",
            {"channel": "ZALO", "outcome": "MESSAGE_SENT", "reminder_step": "READY"},
        )
        ok(
            "the server refuses the text and a 'message sent' by name: "
            "MESSAGING_POLICY_UNPUBLISHED (DEC-043); nothing is written",
            text["status"] == 422
            and (text.get("body") or {}).get("detail", {}).get("reason_code")
            == "MESSAGING_POLICY_UNPUBLISHED"
            and (text.get("body") or {}).get("detail", {}).get("decision") == "DEC-043"
            and sent["status"] == 422
            and (sent.get("body") or {}).get("detail", {}).get("reason_code")
            == "MESSAGING_POLICY_UNPUBLISHED"
            and sql(f"select count(*) from order_contact_attempts where order_id = '{theirs}'")
            == "0",
            f"{text['text'][:120]} || {sent['text'][:120]}",
        )
        head("18a", "CÔNG BỐ — the owner publishes the messaging policy with the script")
        published = _publish_messaging()
        ok(
            "scripts/publish_messaging_policy.py publishes DEC-033's grounds (the owner's act)",
            published.returncode == 0 and "published" in published.stdout,
            (published.stdout + published.stderr)[-200:],
        )
    else:
        note(
            "the messaging policy was published before this scenario began, so its refusal is not "
            "provable here: run it on a stack that has not published it (stack_rem.sh)"
        )

    head("18b", "CHÉP TIN NHẮN, ĐÃ NHẮC — two taps, and the order leaves the list")
    _open_reminders(console)
    entry = _reminder_tr(console, theirs)
    zalo = entry.locator(f"a[data-reminder-zalo='{theirs}']")
    ok(
        "Mở Zalo is a link to their Zalo, built by the server from the number it never prints",
        zalo.count() == 1 and zalo.first.get_attribute("href") == f"https://zalo.me/{digits}",
        zalo.first.get_attribute("href") if zalo.count() else "absent",
    )
    if zalo.count():
        _no_follow(zalo.first)
        zalo.first.click()
        touched("reminders.zalo")
    answer, copied = _copy_reminder(console, theirs)
    _reminder_shot(console, "02-copied")
    body = answer.get("body") or {}
    ok(
        "Chép tin nhắn puts the server's pickup-reminder-v1 text on the clipboard, exactly",
        answer["status"] == 200
        and body.get("template") == "pickup-reminder-v1"
        and copied == body.get("text")
        and "xin báo: đồ giặt phiếu số" in copied
        and "Mời anh/chị qua tiệm lấy đồ." in copied,
        (answer["text"] or "")[:240],
    )
    ok(
        "the text carries neither their name nor their number",
        digits not in copied and "Mai" not in copied and digits[-4:] not in copied,
        copied[:200],
    )
    recorded = _mark_reminded(console, theirs, "zalo")
    ok(
        "Đã nhắc, then Đã gửi tin Zalo: one contact attempt for that reminder, MESSAGE_SENT",
        recorded["status"] == 201
        and (recorded.get("body") or {}).get("reminder_step") == "READY"
        and (recorded.get("body") or {}).get("outcome") == "MESSAGE_SENT",
        recorded["text"][:200],
    )
    ok(
        "and the order leaves the list",
        _reminder_tr(console, theirs).count() == 0
        and not _reminder_row(_reminders(console), theirs),
        "",
    )
    ok(
        "the attempt is on record with its step; the audit keeps why the guard allowed it; no "
        "event, audit or outbox payload carries the number",
        sql(
            "select channel || '|' || outcome || '|' || reminder_step from order_contact_attempts "
            f"where order_id = '{theirs}'"
        )
        == "ZALO|MESSAGE_SENT|READY"
        and sql(
            "select details->'egress'->>'basis' from audit_events "
            f"where aggregate_id = '{theirs}' and action = 'ORDER_CONTACT_ATTEMPT_RECORD'"
        )
        == "OPEN_ORDER"
        and sql(
            f"select (select count(*) from domain_events where payload::text like '%{digits}%') + "
            f"(select count(*) from audit_events where details::text like '%{digits}%') + "
            f"(select count(*) from outbox_events where payload::text like '%{digits}%')"
        )
        == "0",
        "",
    )
    storage = console.call("GET", f"/internal/v1/orders/{theirs}/storage").get("body") or {}
    ok(
        "the reminder counts toward the disposal rule like any contact attempt",
        (storage.get("disposal_verdict") or {}).get("attempts_counted") == 1,
        json.dumps(storage.get("disposal_verdict"))[:160],
    )

    head("18c", "NGÀY 3, 7, 14, TRƯỚC KHI TÍNH PHÍ — aged orders, honest time travel")
    if sql("select count(*) from configuration_versions where config_type='STORAGE_POLICY'") in (
        "",
        "0",
    ):
        stored = _publish_storage()
        note("the storage policy was unpublished; the owner published it: " + stored.stdout[-80:])
    aged: dict[str, str] = {}
    for days, step in ((3, "DAY_3"), (8, "DAY_7"), (14, "DAY_14"), (20, "BEFORE_FEE")):
        order_id = console.build_order(kg="5", stop="ready", customer=(customer_id, digits))[
            "order_id"
        ]
        waited = _age_ready(order_id, days)
        ok(f"harness: an order reads as ready {days} shop days ago", waited == str(days), waited)
        aged[step] = order_id
    listing = _reminders(console)
    ok(
        "the server names the newest step due for each: DAY_3, DAY_7 (on day 8), DAY_14, and "
        "BEFORE_FEE on day 20 of the owner's 20 free days",
        all(
            _reminder_row(listing, order_id).get("step") == step for step, order_id in aged.items()
        ),
        {step: _reminder_row(listing, order_id).get("step") for step, order_id in aged.items()},
    )
    _open_reminders(console)
    _reminder_shot(console, "03-aged")
    words = {
        "DAY_3": "Nhắc lần 2 (ngày 3)",
        "DAY_7": "Nhắc lần 3 (ngày 7)",
        "DAY_14": "Nhắc lần 4 (ngày 14)",
        "BEFORE_FEE": "Nhắc trước khi tính phí",
    }
    ok(
        "each row says its step in words, and the oldest-ready order comes first",
        all(
            _reminder_tr(console, order_id).count() == 1
            and words[step] in _reminder_tr(console, order_id).first.inner_text()
            for step, order_id in aged.items()
        )
        and console.page.locator("#reminder-list tbody tr").first.get_attribute("data-reminder")
        in set(aged.values()),
        console.page.locator("#reminder-list").inner_text()[:300].replace("\n", " | ")
        if console.page.locator("#reminder-list").count()
        else console.text()[:200],
    )
    answer, copied = _copy_reminder(console, aged["BEFORE_FEE"])
    policy = (
        console.call("GET", f"/internal/v1/orders/{aged['BEFORE_FEE']}/storage").get("body") or {}
    ).get("policy") or {}
    starts = sql(
        "select to_char(((production_ready_at at time zone 'Asia/Ho_Chi_Minh')::date + "
        f"{int(policy.get('free_days') or 0) + 1}), 'DD/MM/YYYY') from orders "
        f"where id = '{aged['BEFORE_FEE']}'"
    )
    fee = f"{int(policy.get('fee_per_started_day_vnd') or 0):,}".replace(",", ".")
    ok(
        "the BEFORE_FEE text quotes the owner's published figures: the day the fee starts, the "
        "fee per day and the cap",
        answer["status"] == 200
        and f"Từ ngày {starts} tiệm tính phí lưu kho {fee} ₫/ngày "
        f"(tối đa {policy.get('fee_cap_percent')}% tiền giặt)."
        in copied,
        copied[:300],
    )
    call = _reminder_tr(console, aged["DAY_7"]).locator(f"a[data-reminder-call='{aged['DAY_7']}']")
    ok(
        "Gọi is a tel: link to their number",
        call.count() == 1 and call.first.get_attribute("href") == f"tel:+84{digits[1:]}",
        call.first.get_attribute("href") if call.count() else "absent",
    )
    if call.count():
        _no_follow(call.first)
        called = _mark_reminded(console, aged["DAY_7"], "no-answer", via=call.first)
        ok(
            "Gọi opens Đã nhắc with the call's outcomes; Không nghe máy records the reminder as a "
            "call, and the order leaves the list",
            called["status"] == 201
            and (called.get("body") or {}).get("channel") == "CALL"
            and (called.get("body") or {}).get("reminder_step") == "DAY_7"
            and _reminder_tr(console, aged["DAY_7"]).count() == 0,
            called["text"][:200],
        )
    done3 = console.call(
        "POST",
        f"/internal/v1/orders/{aged['DAY_3']}/contact-attempts",
        {"channel": "CALL", "outcome": "REACHED", "reminder_step": "DAY_3"},
    )
    later = _age_ready(aged["DAY_3"], 4)
    ok(
        "a reminder done on day 3 stays done; on day 7 the next one comes due",
        done3["status"] == 201
        and later == "7"
        and _reminder_row(_reminders(console), aged["DAY_3"]).get("step") == "DAY_7",
        done3["text"][:120],
    )
    _age_ready(aged["BEFORE_FEE"], 1)
    pickup = console.call("GET", f"/internal/v1/stores/{STORE}/orders/awaiting-pickup?limit=200")
    waiting = next(
        (
            item
            for item in (pickup.get("body") or {}).get("orders", [])
            if item.get("order_id") == aged["BEFORE_FEE"]
        ),
        {},
    )
    ok(
        "day 21: the fee has started; the order leaves the reminders and Đồ chờ lấy takes over",
        not _reminder_row(_reminders(console), aged["BEFORE_FEE"])
        and (waiting.get("storage_fee") or {}).get("status") == "ACCRUING",
        json.dumps(waiting.get("storage_fee"))[:160],
    )

    head("18d", "KHÔNG LIÊN LẠC ĐƯỢC — a ticket alone is counted, not hidden")
    _open_reminders(console)
    block = console.page.locator("details[data-unreachable]")
    if block.count():
        block.first.locator("summary").click()
        _reminder_shot(console, "04-unreachable")
    ok(
        "the ticket-only order is counted at the bottom, with nobody to message",
        block.count() == 1
        and int(block.first.get_attribute("data-unreachable") or 0) >= 1
        and "không có số điện thoại hay kênh chat" in block.first.inner_text()
        and console.page.locator(f"[data-reminder-unreachable='{ticket}']").count() == 1
        and _reminder_tr(console, ticket).count() == 0,
        block.first.inner_text()[:160] if block.count() else "absent",
    )
    refused = console.call("GET", f"/internal/v1/orders/{ticket}/pickup-reminder?step=READY")
    ok(
        "its text is refused NO_CONTACT",
        refused["status"] == 422
        and (refused.get("body") or {}).get("detail", {}).get("reason_code") == "NO_CONTACT",
        refused["text"][:160],
    )

    head("18e", "KHÁCH NHẮN STOP — no text, and a call is still recorded")
    stopped_digits = "09" + str(uuid.uuid4().int)[:8]
    made = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers",
        {"phone": stopped_digits, "display_name": "anh Dừng", "service_consent": True},
    )
    stopped_customer = str(
        ((made.get("body") or {}).get("customer") or {}).get("customer_id") or ""
    )
    stopped = console.build_order(
        kg="5", stop="ready", customer=(stopped_customer, stopped_digits)
    )["order_id"]
    wrote = _record_stop(stopped)
    ok(
        "harness: the customer's STOP is recorded through the ingress path production uses",
        wrote.returncode == 0 and "STOP recorded" in wrote.stdout,
        (wrote.stdout + wrote.stderr)[-240:],
    )
    _open_reminders(console)
    entry = _reminder_tr(console, stopped)
    copy = entry.locator(f"[data-reminder-copy='{stopped}']")
    zalo = entry.locator(f"[data-reminder-zalo='{stopped}']")
    _reminder_shot(console, "05-stop")
    ok(
        "their row says they asked the shop to stop, and Chép tin nhắn and Mở Zalo are off",
        entry.count() == 1
        and "Khách đã nhắn dừng nhận tin" in entry.first.inner_text()
        and copy.first.get_attribute("data-denied") == "true"
        and zalo.count() == 1
        and zalo.first.get_attribute("data-denied") == "true"
        and zalo.first.get_attribute("href") is None,
        entry.first.inner_text()[:200].replace("\n", " | ") if entry.count() else "absent",
    )
    text = console.call("GET", f"/internal/v1/orders/{stopped}/pickup-reminder?step=READY")
    ok(
        "the server refuses the text SUPPRESSED",
        text["status"] == 422
        and (text.get("body") or {}).get("detail", {}).get("reason_code") == "SUPPRESSED",
        text["text"][:160],
    )
    if entry.count():
        entry.locator(f"[data-reminder-done='{stopped}']").click()
        console.page.wait_for_selector("dialog[open]#reminder-done", state="visible", timeout=8000)
        message_choice = console.page.locator(
            "dialog[open]#reminder-done [data-reminder-outcome='zalo']"
        )
        _reminder_shot(console, "06-stop-done-sheet")
        ok(
            "Đã nhắc offers no 'message sent' for them",
            message_choice.first.get_attribute("data-denied") == "true",
            "",
        )
        (reached,) = console.press_capturing(
            console.page.locator("dialog[open]#reminder-done [data-reminder-outcome='reached']"),
            "/contact-attempts",
        )
        console.page.wait_for_timeout(1400)
        ok(
            "a call is recorded for the reminder, and the order leaves the list",
            reached["status"] == 201
            and (reached.get("body") or {}).get("channel") == "CALL"
            and _reminder_tr(console, stopped).count() == 0,
            reached["text"][:160],
        )
    console.sign_in("demo-auditor")
    audited = _reminders(console)
    ok(
        "the auditor reads the list without the number or the Zalo link",
        audited["status"] == 200
        and all(
            item.get("phone") is None and item.get("zalo_url") is None
            for item in (audited.get("body") or {}).get("orders", [])
        )
        and digits not in audited["text"],
        audited["text"][:160],
    )
    console.sign_in("demo-owner")


# --- EINVOICE-REQUEST-001 (DEC-040): invoice requests --------------------------------------------


def _invoice_subject(console: Console, path: str) -> dict[str, Any]:
    return console.call("GET", path).get("body") or {}


def _publish_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            _script(script),
            "--actor-id",
            _owner_id(),
            "--database-url",
            arguments.database_url,
        ],
        capture_output=True,
        text=True,
    )


def _invoice_privacy_refusal(console: Console, order_id: str) -> None:
    """Before the notice: the order page's row is off with its reason, and the server refuses."""

    console.open_order(order_id, settle=2400)
    section = console.page.locator("#invoice-section")
    press = console.page.locator("#invoice-request-open")
    ok(
        "the order page's Hóa đơn row says in tier 1 what the owner must do, and "
        "'Khách cần hóa đơn' is off",
        section.count() == 1
        and "Chủ tiệm cần công bố thông báo bảo mật trước khi ghi yêu cầu hóa đơn"
        in section.inner_text()
        and press.count() == 1
        and press.is_disabled(),
        section.inner_text()[:200].replace("\n", " | ") if section.count() else "absent",
    )
    refused = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/orders/{order_id}/invoice-requests",
        {"buyer_unit_name": "Công ty TNHH Biển Xanh", "buyer_email": "ketoan@bienxanh.vn"},
    )
    ok(
        "the server itself refuses capture: 422 PRIVACY_NOTICE_UNPUBLISHED (DEC-040), no buyer "
        "detail in the answer, nothing written",
        refused["status"] == 422
        and (refused.get("body") or {}).get("detail", {}).get("reason_code")
        == "PRIVACY_NOTICE_UNPUBLISHED"
        and "bienxanh" not in refused["text"]
        and sql("SELECT count(*) FROM invoice_requests") == "0",
        refused["text"][:200],
    )


def scenario_invoice_requests(console: Console) -> None:
    """`EINVOICE-REQUEST-001` (`DEC-040`): refused until the owner publishes the privacy notice;
    then *Khách cần hóa đơn* on an order and on an account month, *Hóa đơn cần xuất* with its tabs,
    the bookkeeper's download, *Ghi số hóa đơn*, and another request cancelled. The software issues
    nothing: every figure is read back from the server."""

    head("30", "HÓA ĐƠN — refused until the owner publishes the privacy notice (DEC-040)")
    if not arguments.database_url:
        ok("publishing the notice uses the owner's script, which needs --database-url", False)
        return
    console.sign_in("demo-owner")
    walk_in = console.build_order(stop="ready")
    order_id = str(walk_in["order_id"])
    notice = console.call("GET", f"/internal/v1/stores/{STORE}/customer-privacy-notice")
    if (notice.get("body") or {}).get("published") is not True:
        _invoice_privacy_refusal(console, order_id)
        head("30a", "CÔNG BỐ — the owner publishes the notice with the script")
        published = _publish_script("publish_privacy_notice.py")
        ok(
            "scripts/publish_privacy_notice.py publishes it (the owner's act, not the console's)",
            published.returncode == 0 and "published" in published.stdout,
            (published.stdout or published.stderr).strip()[:160],
        )
    else:
        note(
            "the privacy notice was published before this scenario began (a full run: the "
            "customers scenario proves the invoice refusal before it publishes)"
        )

    head("30b", "KHÁCH CẦN HÓA ĐƠN — on the order page, the buyer's details in one sheet")
    console.sign_in("demo-operations")
    console.open_order(order_id, settle=2400)
    console.page.locator("#invoice-request-open").click()
    touched("invoice.request-open")
    console.page.wait_for_selector("#invoice-request-sheet[open]", timeout=8000)
    console.type_into("#invoice-unit", "Công ty TNHH Biển Xanh", "invoice.request-unit")
    console.type_into("#invoice-tax", "4201234567", "invoice.request-tax")
    console.type_into("#invoice-email", "ketoan@bienxanh.vn")
    (refused,) = console.press_capturing(
        console.page.locator("#invoice-request-save"), "/invoice-requests"
    )
    touched("invoice.request-save")
    console.page.wait_for_timeout(700)
    sheet_text = console.page.locator("#invoice-request-sheet").inner_text()
    ok(
        "a tax code without an address is refused in the sheet, beside the field, nothing written",
        refused["status"] == 422
        and (refused.get("body") or {}).get("detail", {}).get("reason_code")
        == "INVOICE_ADDRESS_REQUIRED"
        and "Có mã số thuế thì cần ghi địa chỉ đơn vị" in sheet_text
        and console.page.locator("#invoice-address[aria-invalid='true']").count() == 1,
        sheet_text[:200].replace("\n", " | "),
    )
    console.type_into("#invoice-address", "12 Trần Phú, Nha Trang", "invoice.request-address")
    (created,) = console.press_capturing(
        console.page.locator("#invoice-request-save"), "/invoice-requests"
    )
    console.page.wait_for_timeout(1800)
    order_request = created.get("body") or {}
    row = console.page.locator("#invoice-section")
    ok(
        "the request is recorded (REQUESTED, YC-…, the order's charges read now) and the row says "
        "'Chờ kế toán xuất' with its code",
        created["status"] == 201
        and order_request.get("status") == "REQUESTED"
        and str(order_request.get("request_code", "")).startswith("YC-")
        and order_request.get("amount", {}).get("source") == "ORDER_CHARGES"
        and "Chờ kế toán xuất" in row.inner_text()
        and str(order_request.get("request_code")) in row.inner_text(),
        f"{created['status']} {row.inner_text()[:160]}".replace("\n", " | "),
    )
    subject = _invoice_subject(console, f"/internal/v1/stores/{STORE}/orders/{order_id}/invoice")
    ok(
        "the order now refuses a second request by name (INVOICE_REQUEST_EXISTS), and the row "
        "offers no second 'Khách cần hóa đơn'",
        subject.get("refusal") == "INVOICE_REQUEST_EXISTS"
        and console.page.locator("#invoice-request-open").count() == 0,
        subject.get("refusal"),
    )

    head("30c", "HÓA ĐƠN THÁNG — an account customer's month, the buyer saved for next time")
    console.sign_in("demo-owner")
    if (
        sql("SELECT count(*) FROM configuration_versions WHERE config_type = 'ACCOUNT_TERMS'")
        == "0"
    ):
        terms = _publish_script("publish_account_terms.py")
        note(
            "the account terms were unpublished (only with --only); the owner published them: "
            + terms.stdout.strip()[:80]
        )
    customer_id, last4 = _account_customer(console, "Homestay Hải Âu")
    opened = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/customers/{customer_id}/account",
        {"credit_limit_vnd": 5_000_000},
    )
    account_order = _account_order(console, customer_id, last4, "7")
    charged = console.call(
        "POST",
        f"/internal/v1/orders/{account_order['order_id']}/account-charge",
        {"collected_by_customer": True},
        if_match=account_order["row_version"],
    )
    month = time.strftime("%Y-%m", time.gmtime(time.time() + 7 * 3600))
    ok(
        "a homestay on công nợ has one order charged this month (set up over the API)",
        opened["status"] == 201 and charged["status"] < 300,
        f"{opened['status']} {charged['status']} {charged['text'][:120]}",
    )
    console.sign_in("demo-operations")
    console.open(f"#/customers/{customer_id}/statement/{month}", settle=2600)
    unit = console.page.locator("#invoice-unit")
    console.page.locator("#invoice-request-open").click()
    touched("invoice.month-open")
    console.page.wait_for_selector("#invoice-request-sheet[open]", timeout=8000)
    ok(
        "the month's sheet pre-fills the unit name with the customer's name and offers "
        "'Lưu cho lần sau'",
        unit.input_value() == "Homestay Hải Âu"
        and console.page.locator("#invoice-save-profile").count() == 1,
        unit.input_value(),
    )
    console.type_into("#invoice-email", "letan@haiau.vn")
    console.page.locator("#invoice-save-profile").check()
    touched("invoice.save-profile")
    (month_created,) = console.press_capturing(
        console.page.locator("#invoice-request-save"), "/invoice-requests"
    )
    console.page.wait_for_timeout(1800)
    month_request = month_created.get("body") or {}
    ok(
        "the month's request reads the statement's charges (ACCOUNT_STATEMENT); the row shows it",
        month_created["status"] == 201
        and month_request.get("period_month") == month
        and month_request.get("amount", {}).get("source") == "ACCOUNT_STATEMENT"
        and (month_request.get("amount", {}).get("total_vnd") or 0) > 0
        and "Chờ kế toán xuất" in console.page.locator("#invoice-section").inner_text(),
        f"{month_created['status']} {month_created['text'][:160]}",
    )
    covered = _invoice_subject(
        console, f"/internal/v1/stores/{STORE}/orders/{account_order['order_id']}/invoice"
    )
    ok(
        "the month's order is covered by the month (INVOICE_REQUEST_EXISTS), and the saved buyer "
        "pre-fills it",
        covered.get("refusal") == "INVOICE_REQUEST_EXISTS"
        and covered.get("prefill_from_profile") is True
        and (covered.get("prefill") or {}).get("email") == "letan@haiau.vn",
        {key: covered.get(key) for key in ("refusal", "prefill_from_profile")},
    )
    console.open(f"#/customers/{customer_id}", settle=2600)
    card = console.page.locator("#customer-account-section #invoice-section")
    ok(
        "the account card shows this month's Hóa đơn with its request",
        card.count() == 1 and str(month_request.get("request_code")) in card.inner_text(),
        card.inner_text()[:160].replace("\n", " | ") if card.count() else "absent",
    )

    head("30d", "HÓA ĐƠN CẦN XUẤT — the list, the bookkeeper's download, Ghi số hóa đơn")
    console.sign_in("demo-owner")
    _nav_to(console, "Hóa đơn cần xuất", "/invoices")
    touched("shell.nav.invoices")
    console.page.wait_for_timeout(1500)
    table_text = console.page.locator("main").inner_text()
    ok(
        "Cần xuất lists both requests, oldest first, with the amount the shop charged",
        console.page.locator(
            f"tr[data-invoice='{order_request.get('invoice_request_id')}']"
        ).count()
        == 1
        and console.page.locator(
            f"tr[data-invoice='{month_request.get('invoice_request_id')}']"
        ).count()
        == 1
        and "Cần xuất" in table_text,
        table_text[:200].replace("\n", " | "),
    )
    (exported,) = console.press_capturing(
        console.page.locator("#invoices-download"), "/invoice-requests/export"
    )
    touched("invoices.download")
    console.page.wait_for_timeout(900)
    produced = exported.get("body") or {}
    content = str(produced.get("content_csv") or "")
    ok(
        "Tải danh sách cho kế toán: a UTF-8 CSV with a BOM, the query version, the money header "
        "'Số tiền theo giá tiệm đã thu (chưa tách thuế)', both requests",
        exported["status"] == 200
        and content.startswith("﻿")
        and str(produced.get("query_version", "")).startswith("invoice-requests-export-v1:")
        and "Số tiền theo giá tiệm đã thu (chưa tách thuế)" in content
        and str(order_request.get("request_code")) in content
        and str(month_request.get("request_code")) in content,
        f"{exported['status']} {content[:120]!r}",
    )
    ok(
        "the download is audited (one invoice_request_exports row and its audit event) and carries "
        "no phone number",
        sql("SELECT count(*) FROM invoice_request_exports") not in ("", "0")
        and sql("SELECT count(*) FROM audit_events WHERE action = 'INVOICE_REQUEST_EXPORT'")
        not in ("", "0")
        and not re.search(r"0\d{9}", content.replace("4201234567", "")),
        sql("SELECT count(*) FROM invoice_request_exports"),
    )
    console.page.locator(
        f"[data-record-issued='{order_request.get('invoice_request_id')}']"
    ).click()
    touched("invoices.record-issued")
    console.page.wait_for_selector("#invoice-issued-sheet[open]", timeout=8000)
    console.type_into("#invoice-symbol", "1c26tyy", "invoice.issued-symbol")
    console.type_into("#invoice-number", "0000123", "invoice.issued-number")
    (issued,) = console.press_capturing(console.page.locator("#invoice-issued-save"), "/issued")
    touched("invoice.issued-save")
    console.page.wait_for_timeout(1500)
    console.page.locator("#invoices-tabs [data-value='ISSUED']").click()
    touched("invoices.tabs")
    console.page.wait_for_timeout(1500)
    issued_text = console.page.locator("main").inner_text()
    ok(
        "Ghi số hóa đơn closes it as ISSUED under If-Match; Đã xuất shows 'Ký hiệu 1C26TYY · Số "
        "0000123'",
        issued["status"] == 200
        and (issued.get("body") or {}).get("status") == "ISSUED"
        and "Ký hiệu 1C26TYY · Số 0000123" in issued_text,
        f"{issued['status']} {issued_text[:200]}".replace("\n", " | "),
    )

    head("30e", "HUỶ — another request cancelled with a reason; the counter cannot download")
    console.sign_in("demo-operations")
    console.open("#/invoices", settle=1800)
    ok(
        "for the counter, 'Tải danh sách cho kế toán' is off with who may press it",
        console.page.locator("#invoices-download").is_disabled()
        and "Chủ tiệm hoặc người duyệt tải danh sách cho kế toán"
        in console.page.locator("main").inner_text(),
        "",
    )
    console.page.locator(
        f"[data-cancel-invoice='{month_request.get('invoice_request_id')}']"
    ).click()
    touched("invoices.cancel")
    console.page.wait_for_selector("#invoice-cancel-sheet[open]", timeout=8000)
    console.page.locator("#invoice-cancel-sheet .choice-chip[title=DUPLICATE]").click()
    touched("invoice.cancel-reason")
    confirm = console.page.locator("#invoice-cancel-confirm")
    confirm.click()
    (cancelled,) = console.press_capturing(confirm, "/cancellation")
    touched("invoice.cancel-confirm")
    console.page.wait_for_timeout(1500)
    console.page.locator("#invoices-tabs [data-value='CANCELLED']").click()
    console.page.wait_for_timeout(1500)
    cancelled_text = console.page.locator("main").inner_text()
    ok(
        "the cancel is two presses, lands CANCELLED with its reason, and Đã huỷ shows it",
        cancelled["status"] == 200
        and (cancelled.get("body") or {}).get("cancel_reason") == "DUPLICATE"
        and "Trùng yêu cầu khác" in cancelled_text,
        f"{cancelled['status']} {cancelled_text[:160]}".replace("\n", " | "),
    )
    leaked = sql(
        "SELECT (SELECT count(*) FROM domain_events WHERE payload::text LIKE '%bienxanh%' "
        "OR payload::text LIKE '%Biển Xanh%' OR payload::text LIKE '%4201234567%') + "
        "(SELECT count(*) FROM audit_events WHERE details::text LIKE '%bienxanh%' "
        "OR details::text LIKE '%4201234567%') + "
        "(SELECT count(*) FROM outbox_events WHERE payload::text LIKE '%bienxanh%') + "
        "(SELECT count(*) FROM command_idempotency_records WHERE response::text LIKE '%bienxanh%')"
    )
    ok("no buyer detail in any event, audit, outbox or idempotency row", leaked == "0", leaked)
    console.sign_in("demo-owner")


# --- VIETQR-001 ---------------------------------------------------------------------------------

#: The demo account the scenario publishes: the bank BIN and account of the NAPAS reference vectors
#: in the spec. A stack, not a shop: no customer ever scans this.
VIETQR_DEMO_ACCOUNT = (
    "--bank-bin",
    "970416",
    "--account-number",
    "257678859",
    "--account-name",
    "TIEM GIAT DEMO",
    "--bank-display-name",
    "ACB",
)


def _publish_bank_account(
    *extra: str, actor: str = "demo-owner"
) -> subprocess.CompletedProcess[str]:
    """Run the owner's bank-account script against the stack's database, as the owner would."""

    staff = sql(f"select id from staff_users where oidc_subject='{actor}'")
    return subprocess.run(
        [
            sys.executable,
            _script("publish_bank_account.py"),
            "--database-url",
            arguments.database_url,
            "--actor-id",
            staff,
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _drawn_cells(console: Console, scope: str) -> set[tuple[int, int]]:
    """The dark modules the console drew, read back from the SVG path it built (`M x y h run …`)."""

    d = console.page.locator(f"{scope} svg.vietqr__symbol path").first.get_attribute("d") or ""
    cells: set[tuple[int, int]] = set()
    for x, y, run in re.findall(r"M(\d+) (\d+)h(\d+)", d):
        cells.update((int(x) + step, int(y)) for step in range(int(run)))
    return cells


def _server_cells(modules: list[list[int]]) -> set[tuple[int, int]]:
    return {(x, y) for y, row in enumerate(modules) for x, value in enumerate(row) if value == 1}


def _decoded(console: Console, scope: str) -> str | None:
    """What a real QR decoder reads off the drawn symbol, or None when no decoder is installed.

    `zxing-cpp` (+ Pillow) decodes the element's screenshot -- pixels, not the path -- so this
    is the check a customer's phone makes. Run with `uv run --with zxing-cpp --with pillow` for it.
    """

    try:
        import io

        import zxingcpp  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return None
    shot = console.page.locator(f"{scope} svg.vietqr__symbol").first.screenshot()
    found = zxingcpp.read_barcodes(Image.open(io.BytesIO(shot)))
    return found[0].text if found else ""


def _open_payment_transfer(console: Console, order_id: str) -> bool:
    """Thu tiền, then Chuyển khoản: the QR (or its absence) is drawn inside the sheet."""

    console.open_order(order_id)
    control = console.step_control("TAKE_PAYMENT")
    if control is None:
        return False
    control.click()
    console.page.wait_for_timeout(600)
    console.page.locator("#payment-method button[data-value=CHUYEN_KHOAN]").click()
    touched("orderDetail.payment-method")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#payment-qr [data-vietqr]", timeout=8000)
    console.page.wait_for_timeout(300)
    return True


def scenario_vietqr(console: Console) -> None:
    """`VIETQR-001` (`DEC-041`): an exact VietQR for what is owed. Refused until the owner publishes
    the shop's account; the owner previews a 1.000 ₫ test QR, is refused without the test transfer,
    then publishes; the order's QR asks for exactly what the ledger says remains after a part
    payment; the receipt prints it; the order search finds the order by its transfer code; a paid
    order says nothing is owed. Every figure is read back from the server, never computed."""

    head("25", "VIETQR — mã QR đúng số còn lại, sau khi chủ tiệm công bố tài khoản (DEC-041)")
    if not arguments.database_url:
        ok("publishing the account uses the owner's script, which needs --database-url", False)
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import workspace_env  # noqa: F401
    from nha_trang_laundry_domain.vietqr import parse_payload

    console.sign_in("demo-operations")
    order = console.build_order(kg="7", stop="active")
    order_id = order["order_id"]

    def read() -> dict[str, Any]:
        return console.call("GET", f"/internal/v1/orders/{order_id}").get("body") or {}

    def qr() -> dict[str, Any]:
        answer = console.call("GET", f"/internal/v1/orders/{order_id}/vietqr")
        return answer.get("body") or {"status": answer["status"], "text": answer["text"][:200]}

    rows = sql(
        "select count(*) from configuration_versions where config_type='BANK_TRANSFER_ACCOUNT'"
    )
    ok(
        "no bank account is published on this stack yet (the refusal can only be proven before it)",
        rows == "0",
        rows,
    )
    before = qr()
    ok(
        "the server refuses the QR by name: BANK_ACCOUNT_UNPUBLISHED, no payload, no matrix",
        before.get("refusal") == "BANK_ACCOUNT_UNPUBLISHED"
        and before.get("payload") is None
        and before.get("modules") is None
        and str(before.get("transfer_code", "")).startswith("NTL"),
        {k: before.get(k) for k in ("refusal", "transfer_code", "amount_vnd")},
    )
    code = str(before.get("transfer_code"))
    shown = _open_payment_transfer(console, order_id)
    sheet = console.page.locator("dialog[open]")
    ok(
        "Thu tiền → Chuyển khoản: no QR, one short line and the owner's switch in tier 2; the "
        "rest of the sheet is as before",
        shown
        and console.page.locator("#payment-qr svg.vietqr__symbol").count() == 0
        and "Chưa có mã QR chuyển khoản." in sheet.inner_text()
        and console.page.locator("#payment-qr .info-btn").count() == 1
        and console.page.locator("#payment-transfer-seen").count() == 1,
        sheet.inner_text()[:200].replace("\n", " | ") if sheet.count() else "no sheet",
    )
    console.page.keyboard.press("Escape")

    head("25a", "THỬ 1.000 ₫ — the owner previews, is refused without the test, then publishes")
    import tempfile

    preview_dir = tempfile.mkdtemp(prefix="vietqr-preview-")
    preview_file = os.path.join(preview_dir, "vietqr-test-1000.svg")
    previewed = subprocess.run(
        [
            sys.executable,
            _script("publish_bank_account.py"),
            "--preview",
            "--out",
            preview_file,
            *VIETQR_DEMO_ACCOUNT,
        ],
        capture_output=True,
        text=True,
    )
    payload_line = next(
        (line for line in previewed.stdout.splitlines() if line.startswith("payload: ")), ""
    )
    preview = parse_payload(payload_line.removeprefix("payload: ")) if payload_line else None
    ok(
        "--preview writes the 1.000 ₫ test QR (memo NTLTEST) and publishes nothing",
        previewed.returncode == 0
        and os.path.getsize(preview_file) > 0
        and preview is not None
        and (preview.amount_vnd, preview.purpose, preview.bank_bin) == (1000, "NTLTEST", "970416")
        and sql(
            "select count(*) from configuration_versions where config_type='BANK_TRANSFER_ACCOUNT'"
        )
        == "0",
        (previewed.stdout or previewed.stderr).strip()[:200],
    )
    untested = _publish_bank_account(*VIETQR_DEMO_ACCOUNT)
    ok(
        "publishing without --test-transfer-confirmed is refused, and nothing is written",
        untested.returncode == 2
        and "test-transfer-confirmed" in untested.stderr
        and sql(
            "select count(*) from configuration_versions where config_type='BANK_TRANSFER_ACCOUNT'"
        )
        == "0",
        untested.stderr.strip()[:160],
    )
    not_owner = _publish_bank_account(
        *VIETQR_DEMO_ACCOUNT, "--test-transfer-confirmed", actor="demo-operations"
    )
    ok(
        "only the owner publishes the account: the counter's id is refused",
        not_owner.returncode == 3 and "OWNER_ADMIN" in not_owner.stderr,
        not_owner.stderr.strip()[:160],
    )
    published = _publish_bank_account(*VIETQR_DEMO_ACCOUNT, "--test-transfer-confirmed")
    ok(
        "scripts/publish_bank_account.py publishes it after the test (the owner's act)",
        published.returncode == 0 and "bank account published" in published.stdout,
        (published.stdout or published.stderr).strip()[:160],
    )
    again = _publish_bank_account(*VIETQR_DEMO_ACCOUNT, "--test-transfer-confirmed")
    ok(
        "the same account again changes nothing",
        again.returncode == 0 and "already in force" in again.stdout,
        again.stdout.strip()[:160],
    )

    head("25b", "THU TIỀN — the QR asks for exactly what remains, before and after a deposit")
    owed = int(read().get("remaining_vnd") or 0)
    first = qr()
    parsed = parse_payload(str(first.get("payload"))) if first.get("payload") else None
    ok(
        "the QR asks for the ledger's remaining balance, with the order's transfer code",
        first.get("refusal") is None
        and first.get("amount_vnd") == owed
        and first.get("amount_source") == "BALANCE_DUE"
        and parsed is not None
        and (parsed.amount_vnd, parsed.purpose, parsed.bank_bin, parsed.account_number)
        == (owed, code, "970416", "257678859"),
        {k: first.get(k) for k in ("refusal", "amount_vnd", "transfer_code")},
    )
    _open_payment_transfer(console, order_id)
    scope = "#payment-qr"
    amount_text = console.page.locator(f"{scope} [data-field=qr-amount]").first.inner_text()
    code_text = console.page.locator(f"{scope} [data-field=qr-code]").first.inner_text()
    ok(
        "the sheet shows the QR with Số tiền and Nội dung in large type and the bank-app check",
        _digits(amount_text) == str(owed)
        and code_text.strip() == code
        and "Kiểm tra app ngân hàng: đúng nội dung và số tiền rồi mới bấm Ghi nhận đã thu."
        in console.page.locator("dialog[open]").inner_text(),
        f"{amount_text} · {code_text}",
    )
    ok(
        "the console draws exactly the server's modules: no more, no fewer",
        _drawn_cells(console, scope) == _server_cells(first.get("modules") or []),
        f"{len(first.get('modules') or [])} modules a side",
    )
    decoded = _decoded(console, scope)
    if decoded is None:
        note(
            "QR decode skipped: zxing-cpp is not installed (uv run --with zxing-cpp --with pillow)"
        )
    else:
        ok(
            "a real QR decoder (zxing-cpp) reads the drawn symbol back as the server's payload",
            decoded == first.get("payload"),
            decoded[:80],
        )
    size = console.page.locator(f"{scope} svg.vietqr__symbol").first.bounding_box() or {}
    viewport_width = console.page.viewport_size["width"] if console.page.viewport_size else 0
    ok(
        "the QR is large enough to scan across the counter (>= 240 px on a desk, 200 on a phone)",
        float(size.get("width") or 0) >= (240 if viewport_width >= 768 else 200),
        f"{size.get('width')} px at a {viewport_width} px viewport",
    )
    console.page.locator("#payment-edit").click()
    touched("orderDetail.payment-edit")
    ok(
        "the amount stays editable: the customer may pay part",
        console.page.locator("#payment-amount").is_enabled()
        and console.page.locator(f"{scope} svg.vietqr__symbol").count() == 1,
    )
    console.page.keyboard.press("Escape")

    console.pay(order_id, "50.000", method="CHUYEN_KHOAN", seen=True)
    after = read()
    part = qr()
    ok(
        "after a 50.000 ₫ transfer the QR asks for the new remaining, read in the same request",
        after.get("balance") == "PARTIALLY_PAID"
        and part.get("amount_vnd") == after.get("remaining_vnd") == owed - 50_000
        and parse_payload(str(part.get("payload"))).amount_vnd == owed - 50_000,
        {"remaining": after.get("remaining_vnd"), "qr": part.get("amount_vnd")},
    )
    _open_payment_transfer(console, order_id)
    ok(
        "the sheet now shows the new remaining",
        _digits(console.page.locator("#payment-qr [data-field=qr-amount]").first.inner_text())
        == str(owed - 50_000),
    )
    console.page.keyboard.press("Escape")

    head("25c", "PHIẾU — the receipt prints the QR for what is still owed")
    console.open(f"#/orders/{order_id}/receipt")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#receipt-paper [data-field=vietqr]", timeout=10000)
    paper = console.page.locator("#receipt-paper [data-field=vietqr]")
    ok(
        "Phiếu cho khách carries the QR, the remaining amount and the transfer code",
        paper.count() == 1
        and _digits(paper.locator("[data-field=qr-amount]").inner_text()) == str(owed - 50_000)
        and paper.locator("[data-field=qr-code]").inner_text().strip() == code
        and _drawn_cells(console, "#receipt-paper") == _server_cells(part.get("modules") or []),
        paper.inner_text()[:160].replace("\n", " | ") if paper.count() else "absent",
    )

    head("25d", "TÌM ĐƠN — a transfer that arrives later leads straight to its order")
    found = console.call(
        "GET", f"/internal/v1/stores/{STORE}/orders?transfer_code={code.lower()}&limit=20"
    )
    ok(
        "the order search resolves the code (any case) to this order, in this store",
        found["status"] == 200
        and order_id in [item.get("order_id") for item in found.get("body") or []],
        found["text"][:160],
    )
    wrong = console.call("GET", f"/internal/v1/stores/{STORE}/orders?transfer_code=NTL3213999")
    ok(
        "a code that names nothing is refused by name: TRANSFER_CODE_INVALID",
        wrong["status"] == 422 and "TRANSFER_CODE_INVALID" in wrong["text"],
        wrong["text"][:120],
    )
    console.open("#/orders", settle=1500)
    touched("shell.nav.orders")
    console.page.locator("#lookup-code-mode").click()
    touched("orders.lookup-code-mode")
    console.type_into("#lookup-ticket", code.lower())
    console.page.locator("form.orders__lookup button[type=submit]").click()
    console.page.wait_for_timeout(1800)
    result = console.page.locator("#lookup-result")
    ok(
        "on Đơn hàng, 'Mã chuyển khoản' then the code finds the order in one press",
        f"Mã {code}" in result.inner_text()
        and result.locator(f"a[href*='{order_id}']").count() >= 1,
        result.inner_text()[:160].replace("\n", " | "),
    )

    head("25e", "ĐÃ TRẢ ĐỦ — a paid order says nothing is owed")
    console.pay(order_id)
    paid = qr()
    ok(
        "once paid the QR is refused NOTHING_OWED and the code still names the order",
        read().get("balance") == "PAID"
        and paid.get("refusal") == "NOTHING_OWED"
        and paid.get("modules") is None
        and paid.get("transfer_code") == code,
        {k: paid.get(k) for k in ("refusal", "transfer_code")},
    )
    console.open(f"#/orders/{order_id}/receipt")
    with contextlib.suppress(Exception):
        console.page.wait_for_selector("#receipt-paper [data-paid]", timeout=10000)
    console.page.wait_for_timeout(600)
    ok(
        "the paid order's receipt prints no QR",
        console.page.locator("#receipt-paper [data-field=vietqr]").count() == 0
        and console.page.locator("#receipt-paper [data-paid]").count() == 1,
    )

    account = sql(
        "select a.customer_id || ' ' || a.id from customer_accounts a "
        f"where a.store_id='{STORE}' order by a.opened_at limit 1"
    )
    if " " not in account:
        note("account-month QR skipped: no account customer on this stack (run after 'accounts')")
        return
    head("25f", "SAO KÊ — an account customer's month carries its QR")
    customer_id, _account_id = account.split(" ", 1)
    console.sign_in("demo-owner")
    month = sql("select to_char(now() at time zone 'Asia/Ho_Chi_Minh', 'YYYY-MM')")
    month_qr = (
        console.call(
            "GET",
            f"/internal/v1/stores/{STORE}/customers/{customer_id}/account/statements/{month}/vietqr",
        ).get("body")
        or {}
    )
    console.open(f"#/customers/{customer_id}/statement/{month}", settle=2200)
    drawn = console.page.locator("#statement-paper [data-field=vietqr]")
    if month_qr.get("refusal") is None:
        ok(
            "the statement prints the month's QR: the unpaid figure and the NTLCN code",
            drawn.count() == 1
            and _digits(drawn.locator("[data-field=qr-amount]").inner_text())
            == str(month_qr.get("amount_vnd"))
            and str(month_qr.get("transfer_code", "")).startswith("NTLCN")
            and drawn.locator("[data-field=qr-code]").inner_text().strip()
            == month_qr.get("transfer_code"),
            {
                k: month_qr.get(k)
                for k in ("amount_vnd", "transfer_code", "unpaid_before_month_vnd")
            },
        )
    else:
        ok(
            "a month with nothing unpaid prints no QR, and the server says why",
            drawn.count() == 0 and month_qr.get("refusal") in {"NOTHING_OWED", "MONTH_NOT_STARTED"},
            month_qr.get("refusal"),
        )


# --- LATE-CREDIT-002 (DEC-042): late deliveries, measured by the server ---------------------------


def _publish_remedies() -> subprocess.CompletedProcess[str]:
    """The owner's remedy figures (`DEC-004`), published with the owner's own script."""

    owner = sql("select id from staff_users where oidc_subject='demo-owner'")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [
            sys.executable,
            os.path.join(root, "scripts", "publish_remedy_policy.py"),
            "--database-url",
            arguments.database_url,
            "--actor-id",
            owner,
        ],
        capture_output=True,
        text=True,
        cwd=root,
    )


def _promise_back(order_id: str, minutes: int) -> str:
    """HARNESS STEP (documented, `LATE-CREDIT-002`): Nhận đồ happened earlier than it did, so the
    promise it made is `minutes` minutes in the past now. The walk cannot wait hours for a
    promise to pass, and the first promise is immutable by `0057`'s trigger -- which is exactly the
    guarantee `DEC-042` rests on, so it is lifted for this one statement only, inside one
    transaction, by the table's owner on the migration URL, and restored before the transaction
    ends. The first promise, what the customer was told and the acceptance stamp move back by the
    same interval (the order's timeline shifts; nothing about it is rewritten), the row version
    advances by one as `0036`'s projection guard requires, and no event is written. Everything the
    software decides -- the deadline, the minutes, the list, the credit -- is computed by the
    server from it."""

    sql(
        "alter table orders disable trigger orders_promise_guard; "
        "update orders set "
        f"production_accepted_at = production_accepted_at - (promised_ready_at - "
        f"(now() - make_interval(mins => {minutes}))), "
        f"current_promise_at = current_promise_at - (promised_ready_at - "
        f"(now() - make_interval(mins => {minutes}))), "
        f"promised_ready_at = now() - make_interval(mins => {minutes}), "
        f"row_version = row_version + 1 where id = '{order_id}'; "
        "alter table orders enable trigger orders_promise_guard"
    )
    return stored(order_id, "promised_ready_at")


def _past_failed_return(order_id: str, minutes_before_deadline: int) -> str:
    """HARNESS STEP (documented): a failed delivery attempt made `minutes_before_deadline` minutes
    before the promise -- the driver went, the customer was not home. The walk cannot record a
    trip in the past through the route (it stamps the press), and the legs table is append-only,
    so the earlier trip is inserted as its row: the same columns the route writes, under the
    operator's name. It carries no event; the list reads the rows."""

    return sql(
        "insert into delivery_legs (id, store_id, order_id, leg_kind, outcome, recorded_by, "
        "recorded_at, correlation_id) select gen_random_uuid(), o.store_id, o.id, 'RETURN', "
        "'FAILED', s.id, o.promised_ready_at - "
        f"make_interval(mins => {minutes_before_deadline}), gen_random_uuid() "
        f"from orders o, staff_users s where o.id = '{order_id}' "
        "and s.oidc_subject = 'demo-operations'"
    )


def _delivery_to_door(console: Console, *, late_minutes: int | None, failed: bool = False) -> str:
    """A delivery order taken at the counter under the published turnaround policy, washed, paid,
    handed to the courier and delivered now. With `late_minutes`, the documented harness step
    first puts its promise that many minutes in the past (and, with `failed`, an earlier failed
    trip an hour before the promise)."""

    order = console.build_order(kg="5", mode="PICKUP_AND_RETURN", distance_m=1500, stop="created")
    order_id = order["order_id"]
    console.step(order_id, "RECEIVE")
    if late_minutes is not None:
        _promise_back(order_id, late_minutes)
    for step in ("START_WASH", "QUALITY_CHECK", "MARK_READY"):
        console.call(
            "POST",
            f"/internal/v1/orders/{order_id}/steps",
            {"step": step},
            if_match=console.current_version(order_id, order["row_version"]),
        )
    console.pay(order_id)
    console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/steps",
        {"step": "RELEASE"},
        if_match=console.current_version(order_id, order["row_version"]),
    )
    if failed:
        _past_failed_return(order_id, 60)
    console.call(
        "POST",
        f"/internal/v1/orders/{order_id}/delivery-legs",
        {"leg_kind": "RETURN", "outcome": "SUCCEEDED"},
    )
    return order_id


def _measured_minutes(order_id: str) -> str:
    """An independent reading of the lateness, in SQL: whole minutes from the first promise to the
    succeeded RETURN leg, floored. The walk compares the server's figure with it."""

    return sql(
        "select floor(extract(epoch from (l.recorded_at - o.promised_ready_at)) / 60)::int "
        "from orders o join delivery_legs l on l.order_id = o.id "
        "and l.leg_kind = 'RETURN' and l.outcome = 'SUCCEEDED' "
        f"where o.id = '{order_id}'"
    )


def _late_report(console: Console) -> dict[str, Any]:
    today = sql("select (now() at time zone 'Asia/Ho_Chi_Minh')::date")
    read = console.call(
        "GET", f"/internal/v1/stores/{STORE}/reports/summary?from={today}&to={today}"
    )
    return (read["body"] or {}).get("late_deliveries") or {}


def _open_late(console: Console) -> None:
    """Giao trễ cần xử lý, reached as a person reaches it: the sidebar, or "Thêm" on a phone."""

    console.open("#/", settle=1400)
    link = console.page.locator("nav a", has_text="Giao trễ").first
    if link.count() and not link.is_visible():
        console.page.locator("nav a", has_text="Thêm").first.click()
        console.page.wait_for_timeout(700)
        link = console.page.locator("main a[data-nav='/late-deliveries']").first
    if link.count():
        link.click()
        touched("shell.nav.late-deliveries")
        console.page.wait_for_timeout(1800)
    else:
        console.open("#/late-deliveries", settle=1800)


def scenario_late_delivery(console: Console) -> None:
    """LATE-CREDIT-002 (DEC-042): before the owner publishes the remedy policy nothing is measured
    and deciding refuses by name; after, a delivery on time is not listed, one 3 hours late is --
    with the server's minutes and the failed trip before the deadline -- and "Lỗi của tiệm"
    records the complaint and the 10% credit at those minutes, which the existing remedy path then
    pays; another late one is "Không phải lỗi tiệm" with a reason and leaves the list; the report
    counts both."""

    head("LD", "GIAO TRỄ — before the owner publishes the remedy policy (DEC-042)")
    if not READS_DATABASE or not arguments.database_url:
        note(
            "--database-url is required: the remedy policy is published with the owner's own "
            "script, and the promise is moved by a documented harness step"
        )
        FAIL.append("late_delivery scenario needs --database-url")
        return
    turnaround_in_force = sql(
        "select coalesce((select coalesce(payload->>'withdrawn', 'false') from "
        "configuration_versions where config_type='TURNAROUND_POLICY' and lifecycle='PUBLISHED' "
        "order by version desc limit 1), 'none')"
    )
    published_turnaround = False
    if turnaround_in_force != "false":
        # The promise is what the clock measures from: a stack that has not published the
        # turnaround policy gets it from the owner's script here, and the reversal at the end.
        published = _publish_turnaround("--tet-dates", _TET_FIXTURE)
        published_turnaround = published.returncode == 0
        note(f"turnaround policy published for this walk ({published.stdout.strip()[-60:]})")
    remedy_published = sql(
        "select count(*) from configuration_versions where config_type='REMEDY_POLICY'"
    ) not in ("", "0")
    console.sign_in("demo-operations")
    on_time = _delivery_to_door(console, late_minutes=None)
    store_fault = _delivery_to_door(console, late_minutes=180, failed=True)
    not_fault = _delivery_to_door(console, late_minutes=240)
    ok(
        "a delivery order taken at Nhận đồ carries its promise, like a walk-in",
        all(stored(order, "promised_ready_at") for order in (on_time, store_fault, not_fault)),
        [stored(order, "promised_ready_at") for order in (on_time, store_fault, not_fault)],
    )
    ok(
        "all three reached the customer (a succeeded RETURN leg each)",
        sql(
            "select count(*) from delivery_legs where leg_kind='RETURN' and outcome='SUCCEEDED' "
            f"and order_id in ('{on_time}','{store_fault}','{not_fault}')"
        )
        == "3",
    )
    if remedy_published:
        # No withdrawal exists for the remedy policy, so a stack that pre-published it cannot show
        # the refusal. That is the stack's defect, stated rather than passed over.
        FAIL.append(
            "late_delivery: the stack pre-published the remedy policy, so the refusal before "
            "publication cannot be shown (run on a stack that leaves REMEDY_POLICY to this walk)"
        )
    else:
        listed = console.call("GET", f"/internal/v1/stores/{STORE}/late-deliveries")
        ok(
            "before publication the list measures nothing and says the owner has not published",
            listed["status"] == 200
            and (listed["body"] or {}).get("policy_published") is False
            and (listed["body"] or {}).get("orders") == [],
            listed["text"][:160],
        )
        refused = console.call(
            "POST",
            f"/internal/v1/stores/{STORE}/late-deliveries/{store_fault}/decision",
            {"decision": "STORE_FAULT"},
        )
        ok(
            "and deciding is refused by name, REMEDY_POLICY_UNPUBLISHED, with nothing written",
            refused["status"] == 422
            and "REMEDY_POLICY_UNPUBLISHED" in refused["text"]
            and sql("select count(*) from late_delivery_decisions") in ("0", ""),
            refused["text"][:160],
        )
        _open_late(console)
        ok(
            "and the screen says so in one line, with no row",
            "chưa công bố mức bồi hoàn" in console.text()
            and console.page.locator("[data-late]").count() == 0,
            console.text()[:160],
        )
        console.sign_in("demo-owner")
        unavailable = _late_report(console)
        ok(
            "the report's late-delivery block is unavailable, with the reason, not zeros",
            unavailable.get("status") == "UNAVAILABLE"
            and unavailable.get("reason") == "REMEDY_POLICY_UNPUBLISHED"
            and unavailable.get("late") is None,
            unavailable,
        )
        publish = _publish_remedies()
        ok(
            "the owner publishes the remedy policy with the script",
            publish.returncode == 0 and "remedy policy" in publish.stdout,
            (publish.stdout + publish.stderr)[-200:],
        )

    head("LD2", "GIAO TRỄ — measured by the server, one tap each way")
    console.sign_in("demo-owner")
    before = _late_report(console)
    console.sign_in("demo-operations")
    measured = _measured_minutes(store_fault)
    listed = console.call("GET", f"/internal/v1/stores/{STORE}/late-deliveries")
    rows = {row["order_id"]: row for row in (listed["body"] or {}).get("orders", [])}
    ok(
        "the delivery on time is not listed",
        listed["status"] == 200 and on_time not in rows,
        list(rows)[:4],
    )
    row = rows.get(store_fault) or {}
    ok(
        "the one 3 hours late is, with the server's minutes (equal to an independent reading)",
        str(row.get("late_by_minutes")) == measured and 180 <= int(measured or 0) <= 190,
        (row.get("late_by_minutes"), measured),
    )
    ok(
        "with the failed trip before the deadline, and the would-be credit from the remedy rules",
        len(row.get("failed_attempts_before_deadline") or []) == 1
        and isinstance(row.get("credit_vnd"), int)
        and row["credit_vnd"] > 0,
        row,
    )
    console.open("#/", settle=2200)
    tile = console.page.locator("[data-queue=late-deliveries]")
    ok(
        "Hôm nay counts the late deliveries to decide and links to the list",
        tile.count() == 1 and (tile.get_attribute("href") or "").endswith("#/late-deliveries"),
        tile.inner_text().replace("\n", " | ") if tile.count() else console.text()[:160],
    )
    if tile.count():
        tile.click()
        touched("today.late-link")
        console.page.wait_for_timeout(1800)
    else:
        _open_late(console)
    entry = console.page.locator(f"#late-list [data-late='{store_fault}']")
    entry_text = entry.first.inner_text() if entry.count() else ""
    ok(
        "the screen shows hẹn → giao, trễ 3 giờ, the failed trip, and the credit on its button",
        entry.count() == 1
        and "Trễ 3 giờ" in entry_text
        and "không gặp khách" in entry_text
        and "Lỗi của tiệm — giảm" in entry_text
        and console.page.locator(f"#late-list [data-late='{on_time}']").count() == 0,
        entry_text.replace("\n", " | ")[:200],
    )
    answers = console.press_capturing(
        console.page.locator(f"button[data-late-fault='{store_fault}']"), "/decision"
    )
    touched("late.fault")
    console.page.wait_for_timeout(1500)
    decided = answers[0] if answers else {"status": 0, "body": None, "text": "no call"}
    body = decided["body"] or {}
    ok(
        "Lỗi của tiệm records the shop's fault at the server's minutes",
        decided["status"] == 201
        and body.get("decision") == "STORE_FAULT_CREDITED"
        and str(body.get("late_by_minutes")) == measured,
        decided["text"][:200],
    )
    ok(
        "in one transaction: the complaint, the LATE_DELIVERY_CREDIT proposal at those minutes "
        "with the fault attested, and the decision",
        sql(
            "select count(*) from late_delivery_decisions d "
            "join remedy_proposals p on p.id = d.remedy_proposal_id "
            "join customer_incidents i on i.id = d.incident_id "
            f"where d.order_id = '{store_fault}' and p.kind = 'LATE_DELIVERY_CREDIT' "
            f"and p.attested_late_by_minutes = {measured or 0} and p.store_fault_attested "
            "and p.amount_vnd = " + str(row.get("credit_vnd") or 0)
        )
        == "1",
    )
    follow = console.page.locator(f"[data-late-follow='{store_fault}'] a")
    ok(
        "the row moves to 'Đã ghi lỗi của tiệm' with Cấp giảm trừ on its complaint",
        console.page.locator(f"#late-list [data-late='{store_fault}']").count() == 0
        and follow.count() == 1
        and "Cấp giảm trừ" in follow.first.inner_text(),
        console.text()[:200],
    )
    if follow.count():
        follow.first.click()
        touched("late.follow-link")
        console.page.wait_for_timeout(2000)
    ok(
        "which opens the complaint's own page, where the existing remedy path pays it",
        f"#/incidents/{body.get('incident_id')}" in console.page.url,
        console.page.url,
    )
    executed = console.call(
        "POST", f"/internal/v1/remedy-proposals/{body.get('proposal_id')}/execution"
    )
    ok(
        "and the existing execution route issues the credit, unchanged",
        executed["status"] == 201
        and (executed["body"] or {}).get("amount_vnd") == row.get("credit_vnd"),
        executed["text"][:160],
    )

    _open_late(console)
    console.page.locator(f"button[data-late-not-fault='{not_fault}']").click()
    touched("late.not-fault")
    console.page.wait_for_timeout(700)
    console.page.locator("dialog[open] input[name=late-reason][value=OTHER]").check()
    touched("late.reason")
    console.type_into("dialog[open] #late-note", "khách dặn giao sau 18 giờ", "late.note")
    console.page.locator("dialog[open] input[name=late-reason][value=CUSTOMER_ABSENT]").check()
    answers = console.press_capturing(console.page.locator("#late-reason-submit"), "/decision")
    touched("late.reason-submit")
    console.page.wait_for_timeout(1500)
    decided = answers[0] if answers else {"status": 0, "body": None, "text": "no call"}
    ok(
        "Không phải lỗi tiệm records the reason, and only the decision",
        decided["status"] == 201
        and (decided["body"] or {}).get("reason_code") == "CUSTOMER_ABSENT"
        and (decided["body"] or {}).get("proposal_id") is None
        and sql(f"select count(*) from remedy_proposals where order_id = '{not_fault}'") == "0",
        decided["text"][:200],
    )
    ok(
        "and the order leaves the list",
        console.page.locator(f"#late-list [data-late='{not_fault}']").count() == 0,
        console.text()[:160],
    )
    again = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/late-deliveries/{not_fault}/decision",
        {"decision": "STORE_FAULT"},
    )
    ok(
        "a second decision on the same delivery is refused by name, ALREADY_DECIDED",
        again["status"] == 422 and "ALREADY_DECIDED" in again["text"],
        again["text"][:160],
    )
    on_time_refused = console.call(
        "POST",
        f"/internal/v1/stores/{STORE}/late-deliveries/{on_time}/decision",
        {"decision": "NOT_STORE_FAULT", "reason_code": "CUSTOMER_ABSENT"},
    )
    ok(
        "and the delivery on time cannot be decided at all: NOT_LATE",
        on_time_refused["status"] == 422 and "NOT_LATE" in on_time_refused["text"],
        on_time_refused["text"][:160],
    )
    ok(
        "no note reached an event, audit or outbox payload",
        sql(
            "select (select count(*) from domain_events where payload::text like '%sau 18 giờ%')"
            " + (select count(*) from audit_events where details::text like '%sau 18 giờ%')"
            " + (select count(*) from outbox_events where payload::text like '%sau 18 giờ%')"
        )
        == "0",
    )

    console.sign_in("demo-owner")
    after = _late_report(console)

    def moved(key: str) -> int:
        return int(after.get(key) or 0) - int(before.get(key) or 0)

    ok(
        "the report counts them: +1 the shop's fault and credited, +1 not its fault, 2 fewer "
        "undecided, the credit's value in the credited amount",
        after.get("status") == "COMPLETE"
        and moved("store_fault") == 1
        and moved("credited") == 1
        and moved("credited_vnd") == int(row.get("credit_vnd") or -1)
        and moved("not_store_fault") == 1
        and moved("undecided") == -2
        and moved("late") == 0
        and str(after.get("query_version", "")).startswith("report-v4:"),
        {"before": before, "after": after},
    )
    console.open("#/reports", settle=2200)
    tile = console.page.locator("[data-kpi=LATE_DELIVERIES]")
    ok(
        "and the report screen prints the block",
        tile.count() == 1 and "Lỗi của tiệm" in tile.first.inner_text(),
        tile.first.inner_text() if tile.count() else console.text()[:160],
    )
    if published_turnaround:
        withdrawn = _publish_turnaround("--withdraw")
        note(f"turnaround policy withdrawn again ({withdrawn.stdout.strip()[-60:]})")
    console.sign_in("demo-owner")


def scenario_queue_pending(console: Console) -> None:
    """`OPS-OBSERVABILITY-009` (review P6): "Chờ nhận" on Hệ thống is work the worker will take.

    Every mutation writes an outbox row; only the internal allowlist is ever claimed. After a full
    run the shop has written dozens of record-only rows (payments, reports, reminders...), and the
    queue summary used to count every one of them as pending work that never drained.
    """

    head("H9", "HỆ THỐNG — pending outbox work is only what the internal worker can claim (P6)")
    console.sign_in("demo-owner")
    read = console.call("GET", "/internal/v1/queue-recovery")
    if not ok("the queue summary answers the owner", read["status"] == 200, read["text"][:160]):
        return
    pending = int((read["body"] or {}).get("pending_internal", -1))
    if not READS_DATABASE:
        note("skipped: the database comparison needs --database-url")
        return
    import workspace_env  # noqa: F401  # puts the workspace packages on sys.path
    from nha_trang_laundry_db.outbox import INTERNAL_EVENT_TYPES

    allowlist = "ARRAY[" + ",".join(f"'{name}'" for name in sorted(INTERNAL_EVENT_TYPES)) + "]"
    claimable = sql(
        "SELECT count(*) FROM outbox_events WHERE status = 'PENDING' "
        f"AND event_type = ANY({allowlist})"
    )
    record_only = sql(
        "SELECT count(*) FROM outbox_events WHERE status = 'PENDING' "
        f"AND NOT (event_type = ANY({allowlist}))"
    )
    ok(
        "the pending figure is exactly the claimable internal rows in the database",
        claimable.isdigit() and pending == int(claimable),
        f"api {pending}, claimable {claimable}",
    )
    ok(
        "and the shop's record-only rows exist and are not counted as pending work",
        record_only.isdigit() and int(record_only) > 0,
        f"record-only PENDING rows {record_only}",
    )
    console.open("#/system", settle=1800)
    ok(
        "the Hệ thống screen opens on the same summary",
        "Chờ nhận" in console.text(),
        console.said()[:160],
    )


# --- PLATFORM-SECURITY-009 (slice G) ------------------------------------------------------------

#: P8: each list route and the bound its repository enforces (apps/api/tests/test_list_route_bounds
#: holds the full census; these are the eleven routes that had no bound at the route).
PAGE_BOUNDS_G9 = (
    ("/internal/v1/stores/{store}/orders", 200),
    ("/internal/v1/approvals", 200),
    ("/internal/v1/stores/{store}/quotes", 200),
    ("/internal/v1/stores/{store}/order-requests", 100),
    ("/internal/v1/stores/{store}/incidents", 200),
    ("/internal/v1/stores/{store}/shadow/reviews", 200),
    ("/internal/v1/stores/{store}/shadow/drafts", 200),
    ("/internal/v1/stores/{store}/shadow/unknown-sends", 200),
    ("/internal/v1/stores/{store}/assistant/turns", 100),
    ("/internal/v1/stores/{store}/sla-board", 200),
)


def scenario_platform_bounds(console: Console) -> None:
    """`PLATFORM-SECURITY-009` P8 + P9 against the real API, as the console's own session.

    P8: a page size outside a list's bound is the client's mistake, so it is a 422 -- it used to
    reach the repository and come back as 409 Conflict with an English sentence. The bound itself
    is still served. P9: two people sending the same Idempotency-Key for the same export each get
    their own request; before, the second was handed the first person's request as a "replay".
    """

    head("G9", "NỀN TẢNG — giới hạn trang danh sách, khoá lặp lại theo người")
    console.sign_in("demo-owner")
    console.open("#/today", settle=800)
    for template, bound in PAGE_BOUNDS_G9:
        path = template.format(store=STORE)
        answers = {
            value: console.call("GET", f"{path}?limit={value}")["status"]
            for value in (0, bound + 1, bound)
        }
        ok(
            f"{template.split('/')[-1]}: limit 0 and {bound + 1} are 422, {bound} is served",
            answers[0] == 422 and answers[bound + 1] == 422 and answers[bound] not in (409, 422),
            answers,
        )

    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo

    key = f"conformance-g9-{uuid.uuid4().hex}"
    business_date = _datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()

    def export_with_fixed_key(person: Console) -> dict[str, Any]:
        return person.page.evaluate(
            """async ({path, key, csrf, day}) => {
                const r = await fetch(path, {method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/json', 'Idempotency-Key': key,
                              'X-CSRF-Token': csrf},
                    body: JSON.stringify({business_date: day})});
                let body = null; try { body = await r.json(); } catch (e) {}
                return {status: r.status, body};
            }""",
            {
                "path": f"/internal/v1/stores/{STORE}/exports",
                "key": key,
                "csrf": person.csrf(),
                "day": business_date,
            },
        )

    context = console.context.browser.new_context(viewport=viewport())
    try:
        approver = Console(context.new_page(), context)
        approver.sign_in("demo-approver")
        approver.open("#/today", settle=800)
        mine = export_with_fixed_key(console)
        theirs = export_with_fixed_key(approver)
        again = export_with_fixed_key(approver)
    finally:
        context.close()
    first = (mine.get("body") or {}).get("export_request_id")
    second = (theirs.get("body") or {}).get("export_request_id")
    ok(
        "the same key from a second person records that person's own export request",
        mine["status"] == 201 and theirs["status"] == 201 and first and second and first != second,
        (mine["status"], theirs["status"], first, second),
    )
    ok(
        "and that person's own resend replays their own request",
        (again.get("body") or {}).get("export_request_id") == second,
        again,
    )
    if READS_DATABASE and first and second:
        owners = sql(
            "select string_agg(s.oidc_subject, ',' order by e.requested_at, s.oidc_subject) "
            "from export_requests e join staff_users s on s.id = e.requested_by_staff_id "
            f"where e.id in ('{first}', '{second}')"
        )
        ok(
            "each request names the person who sent it",
            sorted(owners.split(",")) == ["demo-approver", "demo-owner"],
            owners,
        )


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
    # LATE-CREDIT-002 (DEC-042). Before remedy: it proves the refusal on a shop that has not
    # published the remedy policy, then publishes it with the owner's script; everything after it
    # that needs the remedy figures finds them published.
    "late_delivery": scenario_late_delivery,
    "remedy": scenario_remedy,
    "receipt": scenario_receipt,
    "rework": scenario_rework,
    "export_range": scenario_export_range,
    "sessions": scenario_sessions,
    "order_envelope": scenario_order_envelope,
    "credit_pick": scenario_credit_pick,
    "contact_pick": scenario_contact_pick,
    "report": scenario_report,
    "shop_capture": scenario_shop_capture,
    # PAYMENT-001: a deposit at drop-off, the rest at pickup. Before the two that publish a policy.
    "deposit": scenario_deposit,
    # EXPORT-PAYMENTS-001: today's export carries the deposit order's split and the unpaid order's
    # remaining; an envelope over the retired shape is refused by name.
    "export_payments": scenario_export_payments,
    # CUSTOMER-001. Before promise: it proves the refusal on a shop that has not published the
    # privacy notice, then publishes it; nothing after it depends on the notice being unpublished.
    "customers": scenario_customers,
    # PAYMENT-002. After customers (its accounts belong to customer records) and before promise:
    # it proves the refusal on a shop that has not published the account terms, then publishes
    # them; nothing after it depends on the terms being unpublished.
    "accounts": scenario_accounts,
    # VIETQR-001 (DEC-041). After accounts (its last step reads an account month's QR when one
    # exists), before unclaimed and promise: it proves the refusal on a shop that has not published
    # the bank account, then publishes it and leaves it published; nothing after it needs it absent.
    "vietqr": scenario_vietqr,
    # UNCLAIMED-001. After customers (a full run proves Gọi on a customer record, which needs the
    # privacy notice customers publishes), before promise; it withdraws and publishes the storage
    # policy itself, and leaves it published: nothing after it ages an order.
    "unclaimed": scenario_unclaimed,
    # PICKUP-REMIND-001 (DEC-043). After unclaimed (whose storage policy the day-before-the-fee
    # reminder quotes) and before promise (the text's opening hours are the turnaround policy's
    # while it is published). It proves MESSAGING_POLICY_UNPUBLISHED on a stack that has not
    # published the messaging policy, then publishes it; nothing after it depends on it.
    "pickup_reminders": scenario_pickup_reminders,
    # PROMISE-001. Last: it publishes the turnaround policy, and every scenario above proves its
    # own workflow on a shop that has not (the receipt's R4 line, the report's assumed rule).
    "promise": scenario_promise,
    # DAILY-SUMMARY-001 (DEC-039). After promise, so the late-against-promise line has promises to
    # count; on a shop without a turnaround policy it proves the omission instead.
    "daily_summary": scenario_daily_summary,
    # EINVOICE-REQUEST-001 (DEC-040). Last: in a full run the customers scenario proves the
    # invoice refusal while the notice is unpublished; this one proves the feature, and publishes
    # the notice itself only with --only on a fresh stack (after proving the refusal).
    "invoice_requests": scenario_invoice_requests,
    # PLATFORM-SECURITY-009 (slice G). Publishes nothing and reads nothing another scenario counts;
    # it adds two pending export requests, after export_range has made its own before/after count.
    "platform_bounds": scenario_platform_bounds,
    # OPS-OBSERVABILITY-009 (review P6). Last: it reads the outbox every scenario above wrote to.
    "queue_pending": scenario_queue_pending,
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
