"""One real counter transaction, end to end, against a running stack.

`SHOP-WALKTHROUGH-001`. Ticket, quote, acceptance, order, intake, production, settlement,
completion -- the shop's whole day as one command, so deploy day can run it before staff are let in
rather than clicking through and hoping.

**Every path is asserted against the routes the console actually calls**, extracted from
`apps/web/src/screens/*.js` at runtime. A step naming a route no screen has raises before the
request is made, so this walks the surface a member of staff has and nothing else. That assertion
is the point: until `CONSOLE-LIFECYCLE-001` the console had no intake or production surface, so an
order could not leave `CONFIRMED`, and a walk driven by whatever the server happened to serve would
have reported success anyway.

Written against the demo stack, whose identity provider mints a token for any subject. On a real
host the sign-in is a browser and this script is not the way in; what it is for there is proving
the deterministic core end to end on the machine that will run the shop.
"""

import argparse
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "scripts")
from verify_demo_stack import DemoClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="https://staging.internal:8443",
        help="the console origin; the demo stack by default",
    )
    parser.add_argument("--ca-file", default=".demo/ca.crt", help="the private CA to verify with")
    parser.add_argument(
        "--store-id",
        default="11111111-2222-4333-8444-555555555555",
        help="the store to trade in; the demo seed's by default",
    )
    parser.add_argument("--subject", default="demo-owner", help="the OIDC subject to sign in as")
    arguments = parser.parse_args()

    ROOT = Path(".")
    STORE = arguments.store_id
    BASE = arguments.base_url
    CA = Path(arguments.ca_file)

    # What the console can actually reach.
    PATHS = set()
    for js in (ROOT / "apps/web/src").rglob("*.js"):
        for m in re.findall(
            r"""["'`](/internal/v1[^"'`\s]*)["'`]?""", js.read_text(encoding="utf-8")
        ):
            PATHS.add(re.sub(r"\$\{[^}]*\}", "{}", m).split("?")[0].rstrip("/"))

    def console_can(path: str) -> bool:
        shape = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "{}", path)
        return shape.split("?")[0].rstrip("/") in PATHS

    client = DemoClient(BASE, CA)
    token = client.token(arguments.subject)
    client.request(
        "/internal/v1/auth/session",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Origin": BASE},
    )
    csrf = client.cookies.get("staff_csrf", "")

    step = 0

    def call(label, path, body=None, method="POST", if_match=None):
        nonlocal step
        step += 1
        assert console_can(path), f"NO CONSOLE SCREEN CALLS {path}"
        headers = {
            "Origin": BASE,
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"walk-{uuid.uuid4().hex}",
        }
        if if_match is not None:
            headers["If-Match"] = str(if_match)
        status, raw, _ = client.request(
            path, method=method, headers=headers, body=json.dumps(body).encode() if body else None
        )
        data = json.loads(raw) if raw and raw[:1] in b"{[" else {}
        ok = status < 300
        print(f"{step:2}  {'OK ' if ok else 'ERR'}  {status}  {label}")
        if not ok:
            print(f"      {raw[:200]!r}")
            raise SystemExit(1)
        return data

    ticket = call("phát phiếu (counter ticket)", f"/internal/v1/stores/{STORE}/counter-tickets", {})
    # DEC-013: the counter ticket number *is* the customer reference. No customer record exists.
    req = call(
        "tiếp nhận (order request)",
        f"/internal/v1/stores/{STORE}/order-requests",
        {"contact_binding_id": str(ticket["ticket_id"])},
    )
    quote = call(
        "báo giá 7 kg (quote)",
        f"/internal/v1/stores/{STORE}/quotes",
        {
            "bound_order_request_id": req["order_request_id"],
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "lines": [
                {
                    "service_code": "STANDARD_WASH_DRY",
                    "quantity": "7",
                    "unit": "KG",
                    "quantity_basis": "STAFF_MEASUREMENT",
                }
            ],
        },
    )
    print(f"      → {quote['net_service_subtotal_vnd']:,} VND at the 6 kg tier rate")
    acc = call(
        "khách đã chốt giá (acceptance)",
        f"/internal/v1/stores/{STORE}/quotes/{quote['quote_id']}/acceptance",
        {
            "expected_current_revision": quote["revision"],
            "expected_snapshot_hash": quote["snapshot_hash"],
        },
    )
    order = call(
        "tạo đơn (order)",
        f"/internal/v1/stores/{STORE}/orders",
        {
            "bound_contact_id": req["contact_binding_id"],
            "quote_id": quote["quote_id"],
            "quote_revision": acc["revision"],
            "quote_snapshot_hash": acc["snapshot_hash"],
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "customer_final_quote_accepted_at": datetime.now(UTC).isoformat(),
        },
    )
    oid, v = order["order_id"], order["row_version"]

    def move(label, path, body):
        nonlocal v
        r = call(label, path, body, if_match=v)
        v = r["row_version"]
        return r

    # The two dimensions that had no console surface until CONSOLE-LIFECYCLE-001.
    move(
        "nhận đồ → đã nhận, chờ kiểm",
        f"/internal/v1/orders/{oid}/intake-transition",
        {"target": "RECEIVED_PENDING_INSPECTION", "slot_approved": False},
    )
    move(
        "nhận đồ → đã nhận",
        f"/internal/v1/orders/{oid}/intake-transition",
        {"target": "ACCEPTED", "slot_approved": True},
    )
    move(
        "thương mại → chờ tiệm xác nhận",
        f"/internal/v1/orders/{oid}/transition",
        {"target": "STORE_CONFIRMATION_PENDING"},
    )
    move(
        "thương mại → đã xác nhận", f"/internal/v1/orders/{oid}/transition", {"target": "CONFIRMED"}
    )
    move("thương mại → đang chạy", f"/internal/v1/orders/{oid}/transition", {"target": "ACTIVE"})
    for target, vi in (
        ("QUEUED", "đã xếp hàng"),
        ("IN_PROCESS", "đang giặt"),
        ("QUALITY_CHECK", "đang kiểm tra"),
        ("READY_AT_STORE", "sẵn sàng"),
        ("RELEASED", "đã giao ra"),
    ):
        move(
            f"sản xuất → {vi}",
            f"/internal/v1/orders/{oid}/production-transition",
            {"target": target},
        )
    move(
        "tất toán (settlement)",
        f"/internal/v1/orders/{oid}/settlement",
        {"paid_amount_vnd": quote["net_service_subtotal_vnd"], "collected_by_customer": True},
    )
    final = move(
        "thương mại → hoàn tất", f"/internal/v1/orders/{oid}/transition", {"target": "COMPLETED"}
    )

    print()
    print(
        f"  commercial={final['commercial']}  intake={final['intake']} "
        f"production={final['production']}  balance={final['balance']}"
    )
    print(f"  every one of the {step} steps used a route a console screen calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
