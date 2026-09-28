"""SHADOW-CONSOLE-001: the client must not persist customer data on the device.

The original version of this file asserted equality against four hard-coded asset paths. That was
correct and became wrong the moment the console was split into modules — and it would have become
wrong silently in the other direction too, since a new module simply would not have been listed.

So the assertions here are now about the *property* rather than the list: the precache contains
exactly the static assets that exist on disk, contains nothing under any API path, and the worker
has no branch anywhere that writes a response into a cache. The expected shell is derived from the
filesystem by the same generator the service worker is produced from, which means adding a module
can no longer drift this check, and removing one can no longer leave a stale entry behind.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
SERVICE_WORKER = WEB / "sw.js"

SHELL_SUFFIXES = {".js", ".css", ".webmanifest", ".svg"}


def declared_shell() -> list[str]:
    source = SERVICE_WORKER.read_text(encoding="utf-8")
    block = re.search(r"const SHELL = \[(.*?)\];", source, re.S)
    assert block is not None, "the service worker has no SHELL array"
    return [item.strip().strip('"') for item in block.group(1).split(",") if item.strip()]


def assets_on_disk() -> list[str]:
    paths = ["/staff/"]
    for path in sorted(WEB.rglob("*")):
        if path.is_file() and path.suffix in SHELL_SUFFIXES and path.name != "sw.js":
            paths.append(f"/staff/{path.relative_to(WEB).as_posix()}")
    return paths


def test_the_precache_is_exactly_the_static_shell_that_exists() -> None:
    """Neither a missing module nor a deleted one may pass unnoticed."""

    assert declared_shell() == assets_on_disk()


def test_the_precache_names_no_api_path() -> None:
    for entry in declared_shell():
        assert entry.startswith("/staff/"), entry
        assert "/internal/" not in entry, entry


def test_the_service_worker_never_writes_a_response_into_the_cache() -> None:
    """A network-first shell may read the cache. Writing an API response would leave customer data
    on a shared device, which the Shadow console must never do."""

    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert "cache.put" not in source
    assert "caches.put" not in source
    assert ".put(" not in source


def test_the_service_worker_declines_every_request_outside_the_shell_path() -> None:
    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert 'pathname.startsWith("/staff/")' in source
    assert '"/internal/' not in source


def test_the_service_worker_ignores_mutations_entirely() -> None:
    """A worker that intercepted a POST could replay it. This one returns before it can."""

    source = SERVICE_WORKER.read_text(encoding="utf-8")

    assert 'event.request.method !== "GET"' in source


def test_the_client_registers_no_background_sync() -> None:
    """Background sync is the mechanism that would deliver a stale command hours later."""

    for source in WEB.rglob("*.js"):
        text = source.read_text(encoding="utf-8")
        assert "sync.register" not in text, source
        assert "SyncManager" not in text, source
        assert "periodicSync" not in text, source


def test_shop_capture_notes_stay_off_the_device_and_warn_against_a_customer_phone() -> None:
    """SHOP-CAPTURE-001. Sổ thu chi and trip-cost notes are the shop's books, not a contact list.

    The server refuses a note that looks like a phone number (`NOTE_LOOKS_LIKE_PHONE`) and never
    copies a note into an event, audit or outbox payload (`packages/db/tests/test_shop_capture.py`).
    Here, the console's half: every note field says, beside it, not to type a customer's phone, and
    none of the modules that hold a note writes anything to device storage.
    """

    warnings = {
        "src/ui/shopCapture.js": "Không ghi số điện thoại khách",
        "src/screens/expenses.js": "Không ghi số điện thoại của khách",
    }
    for relative, warning in warnings.items():
        assert warning in (WEB / relative).read_text(encoding="utf-8"), relative
    for relative in (*warnings, "src/screens/machines.js", "src/ui/shopReport.js"):
        text = (WEB / relative).read_text(encoding="utf-8")
        for sink in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            assert sink not in text, (relative, sink)


# --- CUSTOMER-001 (DEC-034): the customer list on a shared counter phone --------------------------

CUSTOMER_MODULES = (
    WEB / "src" / "core" / "customers.js",
    WEB / "src" / "ui" / "customers.js",
    WEB / "src" / "screens" / "customers.js",
)


def test_the_customer_modules_exist() -> None:
    for module in CUSTOMER_MODULES:
        assert module.is_file(), module


def test_a_customer_screen_writes_nothing_to_the_device() -> None:
    """The number and the name live in the page that asked for them, never in storage or history.

    A search typed at the counter must not survive the customer walking away: not in storage, not
    in the address bar (a hash query is kept in history and shown to the next person who presses
    Back), and not in the browser console of a shared phone.
    """

    for module in CUSTOMER_MODULES:
        source = module.read_text(encoding="utf-8")
        for sink in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
            "caches.",
            "history.pushState",
            "history.replaceState",
            "console.",
        ):
            assert sink not in source, f"{module.name} reaches {sink}"
        # Navigation carries a customer's opaque id, never what was typed.
        for match in re.finditer(r"location\.hash\s*=\s*([^;]+);", source):
            assert "customers/" in match.group(1) and "q=" not in match.group(1), match.group(0)


def test_a_customer_search_is_never_an_address_the_console_navigates_to() -> None:
    """The query goes to the API in a read, not into a `#/…?q=` route another screen could log."""

    for source in (WEB / "src").rglob("*.js"):
        text = source.read_text(encoding="utf-8")
        assert not re.search(r"#/customers\?", text), source


def test_the_customer_list_is_in_no_export() -> None:
    """`DEC-034`: the list leaves the system only through an owner-approved envelope, and no export
    the repository builds today selects a customer's personal column."""

    exports = (ROOT / "packages/db/src/nha_trang_laundry_db/exports.py").read_text(encoding="utf-8")
    for column in (
        "customers",
        "phone_ciphertext",
        "phone_last4",
        "display_name",
        "delivery_address",
    ):
        assert column not in exports, column
    for source in (WEB / "src" / "screens").glob("exports.js"):
        text = source.read_text(encoding="utf-8")
        assert "/customers" not in text


def test_the_export_statements_select_no_customer_key_and_no_bank_reference() -> None:
    """`EXPORT-PAYMENTS-001`: the export now reads the payment ledger, which sits beside two things
    that identify a person -- the order's customer key and a transfer's bank reference tail. Neither
    export statement selects either, and the exclusion list the owner signs names both."""

    exports = (ROOT / "packages/db/src/nha_trang_laundry_db/exports.py").read_text(encoding="utf-8")
    statements = re.findall(r'^_EXPORT(?:_WINDOW)?_SQL = """(.*?)"""', exports, re.S | re.M)
    assert len(statements) == 2
    for statement in statements:
        for column in ("customer_id", "bank_ref", "bound_contact_id", "recorded_by"):
            assert column not in statement, column
    for excluded in ('"orders.customer_id"', '"order_payments.bank_ref_last"'):
        assert excluded in exports, excluded


# --- PAYMENT-002 (DEC-035): công nợ on the same shared counter device -----------------------------

ACCOUNT_MODULES = (
    WEB / "src" / "core" / "accounts.js",
    WEB / "src" / "ui" / "account.js",
    WEB / "src" / "ui" / "accountHandover.js",
    WEB / "src" / "screens" / "accountStatement.js",
)


def test_the_account_modules_write_nothing_to_the_device() -> None:
    """A business customer's debts, the owner's lift reason and the statement live in the page."""

    for module in ACCOUNT_MODULES:
        assert module.is_file(), module
        source = module.read_text(encoding="utf-8")
        for sink in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
            "caches.",
            "history.pushState",
            "history.replaceState",
            "console.",
        ):
            assert sink not in source, f"{module.name} reaches {sink}"


def test_the_printed_statement_carries_no_phone_number() -> None:
    """The statement a customer is handed names them and their money, never their number: the
    paper reads `customer_name` from the statement read, which carries no phone at all."""

    source = (WEB / "src" / "screens" / "accountStatement.js").read_text(encoding="utf-8")
    assert "phone" not in source
    assert "customer_name" in source


# --- UNCLAIMED-001 (DEC-036): calling customers about laundry left on the shelf -------------------

UNCLAIMED_MODULES = (
    WEB / "src" / "ui" / "unclaimed.js",
    WEB / "src" / "screens" / "pickup.js",
)


def test_the_waiting_list_prints_no_phone_and_writes_nothing_to_the_device() -> None:
    """The list a shared counter phone shows all day.

    The number reaches the screen only inside a `tel:` link built by `telHref` -- "Gọi" -- and is
    never a text node; an auditor, whom the server gives no number, sees the last four digits. The
    contact note warns, beside the field, not to type a customer's phone (the server refuses one,
    `NOTE_LOOKS_LIKE_PHONE`, and keeps notes out of every event, audit and outbox payload:
    `packages/db/tests/test_unclaimed_laundry.py`). Nothing is kept on the device.
    """

    for module in UNCLAIMED_MODULES:
        assert module.is_file(), module
        source = module.read_text(encoding="utf-8")
        for sink in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
            "caches.",
            "history.pushState",
            "history.replaceState",
            "console.",
        ):
            assert sink not in source, f"{module.name} reaches {sink}"
    pickup = (WEB / "src" / "screens" / "pickup.js").read_text(encoding="utf-8")
    # `item.phone` is read in exactly two places: to build the link, and to decide the masked hint.
    uses = re.findall(r"item\.phone\b(?!_)", pickup)
    assert len(uses) == 2, uses
    assert "telHref(item.phone)" in pickup
    assert "formatPhone" not in pickup
    shared = (WEB / "src" / "ui" / "unclaimed.js").read_text(encoding="utf-8")
    assert "Không ghi số điện thoại khách" in shared


# --- LATE-CREDIT-002 (DEC-042): late deliveries, whose fault --------------------------------------

LATE_DELIVERY_MODULES = (
    WEB / "src" / "screens" / "lateDeliveries.js",
    WEB / "src" / "ui" / "lateDelivery.js",
)


def test_the_late_delivery_list_reads_no_phone_and_writes_nothing_to_the_device() -> None:
    """The list is ticket, customer name, times and figures -- the server returns no phone for it.

    The one free-text field, the note on "Lý do khác", warns beside it not to type a customer's
    phone; the server refuses one (`NOTE_LOOKS_LIKE_PHONE`) and keeps the note out of every event,
    audit and outbox payload (`packages/db/tests/test_late_deliveries.py`). Nothing is kept on the
    device.
    """

    for module in LATE_DELIVERY_MODULES:
        assert module.is_file(), module
        source = module.read_text(encoding="utf-8")
        for sink in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
            "caches.",
            "history.pushState",
            "history.replaceState",
            "console.",
            ".phone",
            "telHref",
        ):
            assert sink not in source, f"{module.name} reaches {sink}"
    screen = (WEB / "src" / "screens" / "lateDeliveries.js").read_text(encoding="utf-8")
    assert "Không ghi số điện thoại khách" in screen


def test_the_late_delivery_route_returns_no_phone_field() -> None:
    """The response models carry a display name at most -- never a phone, masked or not."""

    from nha_trang_laundry_api.main import (
        LateDeliveryFollowUpResponse,
        LateDeliveryItemResponse,
        LateDeliveryListResponse,
    )

    for model in (LateDeliveryItemResponse, LateDeliveryFollowUpResponse, LateDeliveryListResponse):
        assert not [name for name in model.model_fields if "phone" in name], model.__name__
