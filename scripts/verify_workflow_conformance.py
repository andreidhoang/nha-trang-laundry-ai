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
import datetime as dt
import json
import subprocess
import sys
import time
import traceback
import urllib.request
import uuid
from typing import Any

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
parser.add_argument("--only", default="", help="run one scenario by name")
arguments = parser.parse_args()

BASE = arguments.base_url.rstrip("/")
CONSOLE = f"{BASE}/staff/"
IDP = arguments.idp_url.rstrip("/")
STORE = arguments.store

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
    "quotes.manual-toggle",
    "quotes.order-request",
    "quotes.fulfillment",
    "quotes.distance",
    "quotes.manual-fee",
    "quotes.manual-ack",
    "quotes.line-code",
    "quotes.line-qty",
    "quotes.add-line",
    "quotes.submit",
    "orders.move-order",
    "orders.move-version",
    "orders.move-dimension",
    "orders.move-target",
    "orders.move-slot",
    "orders.move-custody",
    "orders.move-submit",
    "orderDetail.settlement-amount",
    "orderDetail.settlement-collected",
    "orderDetail.settlement-submit",
    "staff.create-subject",
    "staff.create-name",
    "staff.create-email",
    "staff.create-submit",
    "staff.role-id",
    "staff.role-role",
    "staff.role-submit",
    "staff.store-staff",
    "staff.store-store",
    "staff.store-submit",
    "staff.disable-id",
    "staff.disable-submit",
    "assistant.question",
    "assistant.submit",
    "shell.nav.order-requests",
    "shell.sign-out",
    "staff.store-revoke",
)

PASS: list[str] = []
FAIL: list[str] = []
TOUCHED: set[str] = set()


def ok(name: str, condition: object, detail: object = "") -> bool:
    passed = bool(condition)
    (PASS if passed else FAIL).append(name)
    print(
        f"  {'ok  ' if passed else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""),
        flush=True,
    )
    return passed


def note(text: object) -> None:
    print(f"  ··   {text}", flush=True)


def head(number: str, title: str) -> None:
    print(f"\n{'=' * 78}\n{number}. {title}\n{'=' * 78}", flush=True)


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
                for line in self.page.locator(".notice").all_inner_texts()
                if line.strip()
            )
        except Exception:
            return ""

    def said(self) -> str:
        return f"{self.results()} || {self.notices()}"

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

    # -- building the situation a scenario is about ------------------------------------
    def build_order(
        self, *, kg: str = "7", mode: str = "SELF_DROP_SELF_COLLECT", stop: str
    ) -> dict:
        """Put an order into the state a scenario starts from, through the real routes.

        Setting a starting position up by hand through the interface would take twenty minutes per
        case and prove nothing the walk-through script does not already prove. What each scenario
        drives in the browser is the step it is actually about.
        """
        ticket = self.call("POST", f"/internal/v1/stores/{STORE}/counter-tickets", {})
        request = self.call(
            "POST",
            f"/internal/v1/stores/{STORE}/order-requests",
            {"contact_binding_id": str(ticket["body"]["ticket_id"])},
        )
        quote = self.call(
            "POST",
            f"/internal/v1/stores/{STORE}/quotes",
            {
                "bound_order_request_id": request["body"]["order_request_id"],
                "fulfillment_mode": mode,
                "lines": [
                    {
                        "service_code": "STANDARD_WASH_DRY",
                        "quantity": kg,
                        "unit": "KG",
                        "quantity_basis": "STAFF_MEASUREMENT",
                    }
                ],
            },
        )
        priced = quote["body"]
        acceptance = self.call(
            "POST",
            f"/internal/v1/stores/{STORE}/quotes/{priced['quote_id']}/acceptance",
            {
                "expected_current_revision": priced["revision"],
                "expected_snapshot_hash": priced["snapshot_hash"],
            },
        )
        order = self.call(
            "POST",
            f"/internal/v1/stores/{STORE}/orders",
            {
                "bound_contact_id": request["body"]["contact_binding_id"],
                "quote_id": priced["quote_id"],
                "quote_revision": acceptance["body"]["revision"],
                "quote_snapshot_hash": acceptance["body"]["snapshot_hash"],
                "fulfillment_mode": mode,
                "customer_final_quote_accepted_at": dt.datetime.now(dt.UTC).isoformat(),
                "acquisition_source": "WALK_IN",
            },
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

    def move(
        self,
        order_id: str,
        version: object,
        dimension: str,
        target: str,
        *,
        custody: str = "",
        slot: bool = False,
    ) -> str:
        """Fill the transition panel on #/orders and submit it, exactly as staff would."""
        self.open("#/orders")
        self.page.fill("#move-order", str(order_id))
        self.page.fill("#move-version", str(version))
        touched("orders.move-order", "orders.move-version")
        self.choose("#move-dimension", dimension, "orders.move-dimension")
        self.choose("#move-target", target, "orders.move-target")
        if dimension == "intake" and slot:
            self.page.check("#move-slot")
            touched("orders.move-slot")
        if custody:
            self.choose("#move-custody", custody, "orders.move-custody")
        self.page.locator("button[type=submit]", has_text="Chuyển trạng thái").first.click()
        self.page.wait_for_timeout(1600)
        touched("orders.move-submit")
        return self.said()


def sql(query: str) -> str:
    """Read the database directly, to check what a write really persisted.

    Optional: without `--psql-container` the checks that need it are reported as skipped rather
    than silently passing, because a check that cannot run must never look like one that did.
    """
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

    def settle(amount_text: str, *, collected: bool) -> str:
        console.open(f"#/orders/{order['order_id']}")
        console.type_into("#settlement-amount", amount_text, "orderDetail.settlement-amount")
        box = console.page.locator("#settlement-collected")
        if collected != box.is_checked():
            box.click()
            touched("orderDetail.settlement-collected")
        console.page.locator("button[type=submit]", has_text="Ghi nhận tất toán").first.click()
        console.page.wait_for_timeout(1600)
        touched("orderDetail.settlement-submit")
        return console.said()

    said = settle(str(total - 5_000), collected=True)
    ok(
        "a part payment is refused, and is not called invalid input",
        "chưa được hỗ trợ" in said and "không sai" in said,
        said[:150],
    )
    ok(
        "the refusal names the reason code, in words the counter can act on",
        "AMOUNT_IS_NOT_THE_EXACT_TOTAL" in said and "đúng tổng đã báo" in said,
        "",
    )
    ok(
        "and names the decision that owns it, so staff know it is policy and not a fault",
        "DEC-010" in said,
        "",
    )
    if arguments.psql_container:
        ok(
            "nothing was written for a refused payment",
            sql(f"select count(*) from order_settlements where order_id='{order['order_id']}'")
            == "0",
        )

    said = settle("170000.5", collected=True)
    ok(
        "a decimal is refused at the screen rather than read as ten times the amount",
        "nguyên đồng" in said,
        said[:130],
    )

    said = settle(grouped, collected=True)
    ok(
        "the total copied off the screen, dots and all, is accepted",
        stored(order["order_id"], "balance_status") == "PAID"
        if arguments.psql_container
        else "Đã ghi nhận" in said,
        said[:130],
    )

    head("1b", "ĐÓNG ĐƠN — a paid, released, collected order closes")
    version = stored(order["order_id"], "row_version") or "1"
    console.move(order["order_id"], version, "commercial", "COMPLETED")
    if arguments.psql_container:
        ok(
            "the order reaches COMPLETED",
            stored(order["order_id"], "commercial_status") == "COMPLETED",
            stored(order["order_id"], "commercial_status"),
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
    console.move(fresh["order_id"], fresh["row_version"], "commercial", "CANCELLED")
    if arguments.psql_container:
        ok(
            "a customer who changes their mind before handing the bag over is simply cancelled",
            stored(fresh["order_id"], "commercial_status") == "CANCELLED",
            stored(fresh["order_id"], "commercial_status"),
        )

    head("2b", "GIỮ ĐỒ RỒI MỚI HUỶ — once the shop holds the bag, cancelling needs a review")
    held = console.build_order(stop="confirmed")
    said = console.move(held["order_id"], held["row_version"], "commercial", "CANCELLED")
    ok(
        "the order is not cancelled in one press while the shop holds the goods",
        stored(held["order_id"], "commercial_status") != "CANCELLED"
        if arguments.psql_container
        else "duyệt" in said.lower(),
        stored(held["order_id"], "commercial_status"),
    )
    ok("and the screen says a person must approve it", "duyệt" in said.lower(), said[:120])

    head("2c", "XÉT HUỶ — the reviewed path, and a resolution the record contradicts")
    live = console.build_order(stop="active")
    console.move(live["order_id"], live["row_version"], "commercial", "CANCELLATION_REVIEW")
    version = stored(live["order_id"], "row_version") or live["row_version"]
    if arguments.psql_container:
        ok(
            "an active order can be sent to cancellation review",
            stored(live["order_id"], "commercial_status") == "CANCELLATION_REVIEW",
        )
    said = console.move(
        live["order_id"], version, "commercial", "CANCELLED", custody="NOT_RECEIVED"
    )
    ok(
        "'we never received it' is refused for an order whose custody is recorded",
        stored(live["order_id"], "commercial_status") != "CANCELLED"
        if arguments.psql_container
        else "records custody" in said,
        "",
    )
    ok("and the refusal says which recorded fact contradicts it", "custody" in said, said[:160])

    said = console.move(
        live["order_id"], version, "commercial", "CANCELLED", custody="SHOP_FAULT_NO_CHARGE"
    )
    if arguments.psql_container:
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

    head("2d", "GIẶT LẠI — a stain at quality check is an interruption, not an ending")
    stained = console.build_order(stop="checking")
    console.move(stained["order_id"], stained["row_version"], "production", "EXCEPTION")
    version = stored(stained["order_id"], "row_version") or stained["row_version"]
    if arguments.psql_container:
        ok(
            "staff can record that something is wrong with the laundry",
            stored(stained["order_id"], "production_status") == "EXCEPTION",
        )
    console.move(stained["order_id"], version, "production", "IN_PROCESS")
    if arguments.psql_container:
        ok(
            "and send it back through the wash, which is backward movement the domain allows",
            stored(stained["order_id"], "production_status") == "IN_PROCESS",
            stored(stained["order_id"], "production_status"),
        )
        for target in ("QUALITY_CHECK", "READY_AT_STORE"):
            console.move(
                stained["order_id"],
                stored(stained["order_id"], "row_version"),
                "production",
                target,
            )
        ok(
            "the rewash finishes and the clock names when the laundry was actually done",
            stored(stained["order_id"], "production_ready_at is not null") == "t",
        )

    head("2e", "ĐỒNG HỒ — a commercial move does not restamp finished laundry")
    if arguments.psql_container:
        finished = stored(stained["order_id"], "production_ready_at")
        console.move(
            stained["order_id"],
            stored(stained["order_id"], "row_version"),
            "commercial",
            "CANCELLATION_REVIEW",
        )
        ok(
            "pressing a button on the order board does not change when the washing finished",
            stored(stained["order_id"], "production_ready_at") == finished,
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
        ticket = console.call("POST", f"/internal/v1/stores/{STORE}/counter-tickets", {})
        request = console.call(
            "POST",
            f"/internal/v1/stores/{STORE}/order-requests",
            {"contact_binding_id": str(ticket["body"]["ticket_id"])},
        )
        console.open("#/quotes")
        console.page.click("#quote-manual-toggle")
        touched("quotes.manual-toggle")
        console.type_into(
            "#quote-order-request", request["body"]["order_request_id"], "quotes.order-request"
        )
        console.choose("#quote-fulfillment", mode, "quotes.fulfillment")
        if distance:
            console.type_into("#quote-distance", distance, "quotes.distance")
        console.choose("#quote-line-0-code", service, "quotes.line-code")
        console.type_into("#quote-line-0-qty", kg, "quotes.line-qty")
        console.page.wait_for_timeout(300)
        before_submit = console.text()
        console.page.locator("button[type=submit]", has_text="Tính giá").first.click()
        # Wait for the answer rather than for a stopwatch. A fixed pause is long enough on an idle
        # laptop and not on a busy one, and a pricing check that reads the screen before the price
        # arrives fails for a reason that has nothing to do with the price.
        try:
            console.page.wait_for_selector("text=Bản sửa đổi", timeout=15000)
        except Exception:
            console.page.wait_for_timeout(1500)
        console.page.wait_for_timeout(400)
        touched("quotes.submit")
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

    console.open("#/quotes")
    console.choose("#quote-fulfillment", "PICKUP_AND_RETURN", "quotes.fulfillment")
    console.type_into("#quote-distance", "9000", "quotes.distance")
    console.page.wait_for_timeout(400)
    ok(
        "past 6 km the screen asks the staff member for the fee they agreed with the customer",
        console.page.locator("#quote-manual-fee").count() == 1,
        "",
    )
    ok(
        "and asks them to confirm the customer agreed to it",
        console.page.locator("#quote-manual-ack").count() == 1,
        "",
    )
    touched("quotes.manual-fee", "quotes.manual-ack")

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
    console.open("#/quotes")
    console.page.locator("button", has_text="Thêm dòng").first.click()
    console.page.wait_for_timeout(400)
    touched("quotes.add-line")
    ok(
        "a second line can be added to one quote",
        console.page.locator("#quote-line-1-code").count() == 1,
        "",
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
            live = console.page.locator("button[data-requires-network]:not([disabled])").count()
            denied = console.page.locator("[data-denied='true']").count()
            ok("an auditor is offered no live write control on the order board", live == 0, live)
            ok(
                "and the controls they may not use are shown disabled with a reason, not hidden",
                denied > 0,
                f"{denied} marked denied",
            )
            refused = console.call("POST", f"/internal/v1/stores/{STORE}/counter-tickets", {})
            ok(
                "and the server refuses their write, so the screen was telling the truth",
                refused["status"] == 403,
                f"HTTP {refused['status']}",
            )

    console.sign_in("demo-owner")


def scenario_hiring(console: Console) -> None:
    """Everything an owner must do before a new person can work a shift."""

    head("5", "NHÂN SỰ — hiring somebody, from nothing to able to work")
    console.sign_in("demo-owner")
    subject = f"conformance-{uuid.uuid4().hex[:8]}"
    console.open("#/staff")
    touched("shell.nav.staff")
    ok("the staff screen opens for the owner", "KHÔNG ĐỦ QUYỀN" not in console.text(), "")

    console.type_into("#staff-create-subject", subject, "staff.create-subject")
    console.type_into("#staff-create-name", "Nhân viên mới", "staff.create-name")
    console.type_into("#staff-create-email", f"{subject}@example.com", "staff.create-email")
    console.page.locator("button[type=submit]").first.click()
    console.page.wait_for_timeout(1800)
    touched("staff.create-submit")
    staff_id = sql(f"select id from staff_users where oidc_subject='{subject}'")
    if arguments.psql_container:
        ok("the person exists", len(staff_id) == 36, staff_id)
        ok(
            "and their identifier is on the screen, because the next two forms need it",
            staff_id in console.page.content(),
            "",
        )

    if staff_id:
        console.type_into("#staff-role-id", staff_id, "staff.role-id")
        console.choose("#staff-role-role", "OPERATOR", "staff.role-role")
        console.press("Gán vai trò", "staff.role-submit", within="#staff-role-id")
        ok(
            "the role is recorded",
            "OPERATOR"
            in sql(
                "select coalesce(string_agg(role,','),'') from staff_role_assignments "
                f"where staff_user_id='{staff_id}'"
            ),
            console.results()[:120],
        )

        console.type_into("#staff-store-staff", staff_id, "staff.store-staff")
        console.type_into("#staff-store-store", STORE, "staff.store-store")
        console.press("Gán cửa hàng", "staff.store-submit", within="#staff-store-staff")
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

        head("5aa", "THU HỒI CỬA HÀNG — and the person loses the shop immediately")
        console.page.locator("button", has_text="Thu hồi").first.click()
        console.page.wait_for_timeout(1600)
        touched("staff.store-revoke")
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
        # Put it back: the rest of this scenario, and the next run, expect a member.
        console.type_into("#staff-store-staff", staff_id)
        console.type_into("#staff-store-store", STORE)
        console.press("Gán cửa hàng", within="#staff-store-staff")

        head("5b", "TRÙNG DANH TÍNH — the same person cannot be created twice")
        console.open("#/staff")
        console.type_into("#staff-create-subject", subject)
        console.type_into("#staff-create-name", "Người trùng")
        console.page.locator("button[type=submit]").first.click()
        console.page.wait_for_timeout(1600)
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
        console.open("#/staff")
        console.type_into("#staff-disable-id", staff_id, "staff.disable-id")
        console.press("Vô hiệu hoá", "staff.disable-submit", within="#staff-disable-id")
        confirm = console.page.locator("button", has_text="Vô hiệu hoá")
        if confirm.count():
            confirm.last.click()
            console.page.wait_for_timeout(1600)
        disabled = eventually(f"select status from staff_users where id='{staff_id}'", "DISABLED")
        ok("the person is disabled", disabled == "DISABLED", disabled)

        owner_id = sql("select id from staff_users where oidc_subject='demo-owner'")
        console.open("#/staff")
        console.type_into("#staff-disable-id", owner_id)
        console.press("Vô hiệu hoá", within="#staff-disable-id")
        confirm = console.page.locator("button", has_text="Vô hiệu hoá")
        if confirm.count():
            confirm.last.click()
            console.page.wait_for_timeout(1600)
        ok(
            "the last active owner cannot disable themselves",
            sql(f"select status from staff_users where id='{owner_id}'") == "ACTIVE",
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
    console.move(
        taken_in["order_id"],
        taken_in["row_version"],
        "intake",
        "RECEIVED_PENDING_INSPECTION",
    )
    version = stored(taken_in["order_id"], "row_version") or taken_in["row_version"]
    said = console.move(taken_in["order_id"], version, "intake", "ACCEPTED", slot=True)
    ok(
        "accepting intake from the screen requires the staff member to confirm the slot",
        stored(taken_in["order_id"], "intake_status") == "ACCEPTED"
        if arguments.psql_container
        else "đã chuyển" in said.lower(),
        stored(taken_in["order_id"], "intake_status"),
    )
    ok(
        "and that is what starts the production clock",
        stored(taken_in["order_id"], "production_accepted_at is not null") == "t"
        if arguments.psql_container
        else True,
        "",
    )

    head("6b", "AI ĐÓ ĐÃ ĐỔI — someone else moved the order while you had it open")
    order = console.build_order(stop="created")
    console.call(
        "POST",
        f"/internal/v1/orders/{order['order_id']}/intake-transition",
        {"target": "RECEIVED_PENDING_INSPECTION", "slot_approved": False},
        if_match=order["row_version"],
    )
    said = console.move(order["order_id"], order["row_version"], "intake", "WAITING_PRICE_APPROVAL")
    ok(
        "a stale version is refused, and the screen says somebody else changed the order",
        "người khác đổi" in said,
        said[:160],
    )
    ok(
        "and offers the only thing that helps — a fresh board",
        console.page.locator("button", has_text="Tải lại").count() > 0,
        "",
    )

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
    ok(
        "the incident screen says plainly that its form cannot be completed yet",
        "chưa dùng được" in incidents,
        [line.strip() for line in incidents.splitlines() if "chưa dùng được" in line][:1],
    )
    ok(
        "and tells the counter what to do meanwhile",
        "ra sổ" in incidents,
        "",
    )

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

    gaps = console.text()
    console.open("#/gaps")
    gaps = console.text()
    ok(
        "the unsupported register names incident intake, which this run confirmed is unusable",
        "Mở sự cố tại quầy" in gaps,
        "",
    )


SCENARIOS = {
    "money": scenario_money,
    "exit": scenario_exit,
    "pricing": scenario_pricing,
    "roles": scenario_roles,
    "hiring": scenario_hiring,
    "resilience": scenario_resilience,
    "ai": scenario_ai_refuses,
}


def main() -> int:
    selected = {arguments.only: SCENARIOS[arguments.only]} if arguments.only else dict(SCENARIOS)
    if arguments.only and arguments.only not in SCENARIOS:
        raise SystemExit(f"unknown scenario {arguments.only!r}; choose from {sorted(SCENARIOS)}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        console = Console(context.new_page(), context)
        ok("the console signs in", console.sign_in("demo-owner") in (200, 201))

        head("0", "ĐIỀU HƯỚNG — every navigation destination, reached by clicking it")
        for label, route, hash_path in (
            ("Hôm nay", "today", "#/"),
            ("Tiếp nhận", "order-requests", "#/order-requests"),
            ("Báo giá", "quotes", "#/quotes"),
            ("Đơn hàng", "orders", "#/orders"),
        ):
            link = console.page.locator("nav a", has_text=label).first
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
        if not arguments.psql_container:
            note(
                "no --psql-container given, so the checks that read what was written are skipped "
                "rather than counted as passes"
            )

        for name, scenario in selected.items():
            try:
                scenario(console)
            except Exception:
                FAIL.append(f"{name} crashed")
                print(traceback.format_exc(), flush=True)

        head("8", "MỌI NÚT — the controls this run actually touched")
        missed = [control for control in DECLARED_CONTROLS if control not in TOUCHED]
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
        browser.close()

    print(f"\n{'=' * 78}\nRESULT: {len(PASS)} ok, {len(FAIL)} failed\n{'=' * 78}", flush=True)
    for failure in FAIL:
        print(f"  FAILED: {failure}", flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
