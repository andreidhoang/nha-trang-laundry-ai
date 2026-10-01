"""`EXPORT-PAYMENTS-001`: the owner's export carries part payments and names the ledger they are in.

`PAYMENT-001` put the counter's money on the append-only `order_payments` ledger and left
`order_settlements` one row per order *paid in full*. The export read the settlement alone, so a
partly paid order left the building with empty money cells, under a signed sentence promising
"số tiền đã thu". Four properties, in the order of their weight:

* **a partly paid order exports what was paid, by method**, and an unpaid one what remains --
  summed by PostgreSQL from the ledger, owed and remaining decided by the domain;
* **an envelope signed over the retired shape is refused by name** (`EXPORT_QUERY_VERSION_RETIRED`)
  and never released as the new shape it did not sign; the same request under an envelope over the
  live document releases -- both directions, for one day and for a window;
* **the signed statement names each money column's ledger and cut**, and the retired documents are
  rebuilt exactly as the retired code rendered them (pinned digests), which is what makes the
  recognition exact rather than a guess;
* **no customer personal data reaches the file** -- a customer is seeded with a name, a phone
  number, an address and a note, a transfer with a bank reference is taken, and the produced bytes
  are searched for each.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db import exports
from nha_trang_laundry_db.account_terms import publish_account_terms
from nha_trang_laundry_db.accounts import (
    AccountChargeCommand,
    AccountPaymentCommand,
    AccountRepository,
    OpenAccountCommand,
)
from nha_trang_laundry_db.approvals import ApprovalResourceChangedError
from nha_trang_laundry_db.customers import CustomerRepository
from nha_trang_laundry_db.exports import (
    EXPORT_COLUMNS,
    EXPORT_EXCLUSIONS,
    EXPORT_HEADER_KEYS,
    EXPORT_MONEY_LINE_VI,
    EXPORT_MONEY_SOURCES,
    EXPORT_QUERY,
    EXPORT_WINDOW_QUERY,
    ExportExecutionCommand,
    ExportStateError,
    SanitizedExportRepository,
    _facts,
    _retired_statement,
    _statement,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.query_version import query_version
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import AcquisitionSource, CustodyResolution, FulfillmentMode
from nha_trang_laundry_domain.customers import CustomerKind
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.unclaimed import withdrawal_document
from quote_test_data import accepted_quote
from test_customer_notice import notice_payload
from test_export_range import PRE_WINDOW_RENDERED, STORE, _request
from test_order_step_repository import TOTAL_VND, _order, _read, _step
from test_order_step_repository import _staff as _operator
from test_sanitized_export import _approve, _decide, _local_date, _raise_envelope, _Shop
from test_unclaimed_laundry import policy_payload as storage_payload

#: The rendered digest the retired code (`exports.py` at `abb9ce2`, window query
#: `store-window-orders-export-v1`) produced for `STORE` and the window 1-30 September 2026,
#: computed by calling that module's own `_statement` before `EXPORT-PAYMENTS-001` changed it.
RETIRED_WINDOW_RENDERED = (
    "JCS-SHA256-V1:48265918e1ada5f95806275463e67a279a20b78059254e159f802f139c4c3d7e"
)

#: `PAYMENT-002`'s account terms, as the owner publishes them (round 7 wave 2 integration).
ACCOUNT_TERMS = json.loads(
    (Path(__file__).resolve().parents[3] / "templates/account-terms-dec-035.json").read_text(
        encoding="utf-8"
    )
)

CASH = PaymentMethod.TIEN_MAT
TRANSFER = PaymentMethod.CHUYEN_KHOAN


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _pay(
    connection: Any,
    order_id: UUID,
    staff: Any,
    amount: int,
    method: PaymentMethod,
    *,
    ref: str | None = None,
    collected: bool = False,
) -> None:
    PaymentRepository().record(
        connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, staff).row_version,
            amount_vnd=amount,
            method=method,
            transfer_seen=method is TRANSFER,
            bank_ref_last=ref,
            collected_by_customer=collected,
            principal=staff,
            correlation_id=uuid4(),
        ),
    )


def _received(connection: Any, shop: _Shop, staff: Any) -> UUID:
    order_id = _order(connection, shop.store_id, staff)
    _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True)
    return order_id


def _release(connection: Any, shop: _Shop, created: Any, approval_id: UUID) -> Any:
    return SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
        ),
    )


def _body(content: str) -> dict[str, dict[str, str]]:
    """The file's rows keyed by order id, each as `{column: cell}` under the column header."""
    lines = content.splitlines()
    count = len(EXPORT_HEADER_KEYS)
    assert lines[count] == ",".join(EXPORT_COLUMNS)
    rows = [dict(zip(EXPORT_COLUMNS, line.split(","), strict=True)) for line in lines[count + 1 :]]
    return {row["order_id"]: row for row in rows}


def _data_exports(connection: Any, store_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports WHERE store_id = %s", (store_id,))
        return int(cursor.fetchone()[0])


# --- pure: the signed document --------------------------------------------------------------------


def test_the_retired_documents_are_rebuilt_exactly_as_the_retired_code_rendered_them() -> None:
    """The recognition is exact: both retired digests are pinned to the retired code's output.

    `PRE_WINDOW_RENDERED` was computed by the one-day module before windows existed and still held
    until this item; `RETIRED_WINDOW_RENDERED` by the window module this item replaced. The live
    documents for the same facts are different documents, under different query versions.
    """
    day = _facts("STORE_DAY_ORDERS_V1", STORE, date(2026, 9, 16), None)
    window = _facts("STORE_DAY_ORDERS_V1", STORE, date(2026, 9, 1), date(2026, 9, 30))

    assert canonical_document(_retired_statement(day)).snapshot_hash == PRE_WINDOW_RENDERED
    assert canonical_document(_retired_statement(window)).snapshot_hash == RETIRED_WINDOW_RENDERED
    assert _retired_statement(day).query_version == "store-day-orders-export-v2:3f884e227d6a2d05"
    assert (
        _retired_statement(window).query_version == "store-window-orders-export-v1:b0ae2bdf3725ab24"
    )
    for facts, retired in ((day, PRE_WINDOW_RENDERED), (window, RETIRED_WINDOW_RENDERED)):
        assert canonical_document(_statement(facts)).snapshot_hash != retired
    assert _statement(day).query_version == EXPORT_QUERY.label
    assert _statement(window).query_version == EXPORT_WINDOW_QUERY.label


def test_the_signed_statement_names_each_money_column_s_ledger_and_cut() -> None:
    """Which of two ledgers each figure comes from, and its cut, is part of what is signed."""
    money_columns = [column for column in EXPORT_COLUMNS if column.endswith("_vnd")]
    assert [source.column for source in EXPORT_MONEY_SOURCES] == [
        "expected_total_vnd",
        "paid_amount_vnd",
        "refunded_amount_vnd",
        "owed_vnd",
        "paid_cash_vnd",
        "paid_transfer_vnd",
        "paid_vnd",
        "remaining_vnd",
        # MONEY-LIFECYCLE-009 (DEC-045): the part of a refund netted for a spent remedy credit.
        "refund_netted_remedy_vnd",
    ]
    assert sorted(source.column for source in EXPORT_MONEY_SOURCES) == sorted(money_columns)
    ledgers = {source.column: source.ledger for source in EXPORT_MONEY_SOURCES}
    assert (
        ledgers["paid_cash_vnd"].startswith("order_payments.")
        and "TIEN_MAT" in (ledgers["paid_cash_vnd"])
    )
    assert "CHUYEN_KHOAN" in ledgers["paid_transfer_vnd"]
    assert ledgers["paid_amount_vnd"].startswith("order_settlements.")
    assert ledgers["refunded_amount_vnd"].startswith("order_refunds.")

    for facts in (
        _facts("STORE_DAY_ORDERS_V1", STORE, date(2026, 9, 16), None),
        _facts("STORE_DAY_ORDERS_V1", STORE, date(2026, 9, 1), date(2026, 9, 7)),
    ):
        statement = _statement(facts)
        assert statement.money_sources == EXPORT_MONEY_SOURCES
        assert statement.money_line_vi == EXPORT_MONEY_LINE_VI
        text = statement.statement_vi
        # Every money column is named in the words the owner reads, with its ledger.
        for column in money_columns:
            assert column in text, column
        for ledger in ("order_payments", "order_settlements", "order_refunds"):
            assert f"({ledger})" in text
        # The takings figure it differs from is named by the ledger and timestamp it is cut on now.
        assert "order_payments.recorded_at" in text
        assert "chỉ có khi đơn đã trả đủ" in text
        assert "không phải theo lúc thu tiền" in text
        # Round 9 (brief decision 5): `owed_vnd`'s sentence covers the fee held on hold and the
        # part already paid; the retired wording said the fee applied only while the order waited.
        assert "đơn đang tạm giữ thì phí dừng ở mức lúc bắt đầu giữ" in text
        assert "phần phí khách đã trả" in text and "luôn được giữ nguyên" in text
        assert "(phí đã chốt khi trả đủ hoặc khi ghi công nợ; đơn còn chờ thì phí tính tới" not in (
            text
        )
    # The tier-1 line: at most 25 words, beside a control.
    assert len(EXPORT_MONEY_LINE_VI.split()) <= 25

    # The money sources are a hashed input of the version: re-sourcing a column moves it.
    without_sources = query_version(
        EXPORT_QUERY.identifier,
        exports._EXPORT_SQL,
        exports.BUSINESS_TIMEZONE,
        exports.EXPORT_DAY_BOUNDARY,
        ",".join(EXPORT_COLUMNS),
        ",".join(EXPORT_EXCLUSIONS),
    )
    assert without_sources.digest != EXPORT_QUERY.digest


# --- against PostgreSQL ---------------------------------------------------------------------------


def test_a_partly_paid_order_exports_what_was_paid_by_method_and_an_unpaid_one_what_remains(
    connection: psycopg.Connection[Any],
) -> None:
    """The headline property, on one shop-local day with four orders.

    * paid in full: a 50.000 đ deposit by transfer, the rest in cash -- the split, and the
      settlement columns filled as before;
    * only the deposit: paid by transfer, 60.000 đ remaining, the settlement columns EMPTY -- which
      is exactly the row the retired shape exported with no money at all;
    * nothing taken: 0 paid, everything remaining;
    * a deposit then cancelled and refunded: the refund beside the payment, 0 remaining.
    """
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    paid = _received(connection, shop, staff)
    _pay(connection, paid, staff, 50_000, TRANSFER)
    _pay(connection, paid, staff, TOTAL_VND - 50_000, CASH)
    deposit = _received(connection, shop, staff)
    _pay(connection, deposit, staff, 50_000, TRANSFER)
    unpaid = _received(connection, shop, staff)
    cancelled = _received(connection, shop, staff)
    _pay(connection, cancelled, staff, 30_000, CASH)
    view = _read(connection, cancelled, staff)
    _step(
        connection,
        cancelled,
        staff,
        view.row_version,
        OrderStep.CANCEL,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    )

    created = _request(connection, shop, _local_date(shop.now), None)
    assert created.query_version == EXPORT_QUERY.label
    assert created.money_line_vi == EXPORT_MONEY_LINE_VI
    assert created.money_sources == EXPORT_MONEY_SOURCES
    assert created.shape_retired is False
    produced = _release(connection, shop, created, _approve(connection, shop, created))

    assert produced.row_count == 4
    rows = _body(produced.content_csv)
    total = str(TOTAL_VND)

    full = rows[str(paid)]
    assert full["balance_status"] == "PAID"
    assert (full["paid_transfer_vnd"], full["paid_cash_vnd"], full["paid_vnd"]) == (
        "50000",
        str(TOTAL_VND - 50_000),
        total,
    )
    assert (full["owed_vnd"], full["remaining_vnd"]) == (total, "0")
    assert (full["expected_total_vnd"], full["paid_amount_vnd"]) == (total, total)
    assert full["settlement_attested_at"] != ""

    part = rows[str(deposit)]
    assert part["balance_status"] == "PARTIALLY_PAID"
    assert (part["paid_transfer_vnd"], part["paid_cash_vnd"], part["paid_vnd"]) == (
        "50000",
        "0",
        "50000",
    )
    assert (part["owed_vnd"], part["remaining_vnd"]) == (total, str(TOTAL_VND - 50_000))
    # Not settled in full, so the settlement ledger has nothing to say -- and says nothing.
    assert (
        part["expected_total_vnd"],
        part["paid_amount_vnd"],
        part["settlement_attested_at"],
    ) == ("", "", "")

    none = rows[str(unpaid)]
    assert none["balance_status"] == "UNPAID"
    assert (none["paid_cash_vnd"], none["paid_transfer_vnd"], none["paid_vnd"]) == ("0", "0", "0")
    assert none["remaining_vnd"] == none["owed_vnd"] == total

    gone = rows[str(cancelled)]
    assert (gone["commercial_status"], gone["balance_status"]) == ("CANCELLED", "REFUNDED")
    assert (gone["paid_cash_vnd"], gone["paid_vnd"], gone["refunded_amount_vnd"]) == (
        "30000",
        "30000",
        "30000",
    )
    assert gone["remaining_vnd"] == "0"

    # The header names when the money columns stood so: the release's own instant.
    header = dict(line.split(",", 1) for line in produced.content_csv.splitlines()[:6])
    assert datetime.fromisoformat(header["produced_at"]) == produced.produced_at


@pytest.mark.parametrize("window", [False, True], ids=["one day", "window"])
def test_an_envelope_approved_under_the_retired_shape_is_refused_by_name_never_released(
    connection: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch, window: bool
) -> None:
    """Both directions: the retired envelope is refused by name; the live one releases.

    The envelope is raised over the retired rendering and decided while `exports._statement` is
    patched to the retired one -- which is exactly what the code deployed at the time derived, so
    `ApprovalRepository.decide` accepts it through its real checks. Then the patch is gone, as the
    deploy made it gone:

    * the release refuses with `EXPORT_QUERY_VERSION_RETIRED` and writes nothing -- not the old
      shape (its SQL no longer exists to run) and not the new shape (nobody signed it);
    * the approval read names what the envelope binds (`bound_query_version`,
      `bound_shape_retired`);
    * a retired envelope still waiting cannot be signed under the live code either;
    * the same request, under an envelope over the live document, is approved and releases once.
    """
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    order_id = _received(connection, shop, staff)
    _pay(connection, order_id, staff, 50_000, TRANSFER)
    first = _local_date(shop.now)
    last = date.fromordinal(first.toordinal() + 2) if window else None
    created = _request(connection, shop, first, last)
    facts = _facts("STORE_DAY_ORDERS_V1", shop.store_id, first, last)
    retired_rendered = canonical_document(_retired_statement(facts)).snapshot_hash

    class _Signed:
        """The request as the retired code answered it: same facts, the retired rendering."""

        export_request_id = created.export_request_id
        resource_version = created.resource_version
        snapshot_hash = created.snapshot_hash
        rendered_hash = retired_rendered
        policy_version = created.policy_version

    approval_id = _raise_envelope(connection, shop, _Signed)
    waiting_id = _raise_envelope(connection, shop, _Signed)
    with monkeypatch.context() as retired_code:
        retired_code.setattr(exports, "_statement", _retired_statement)
        _decide(connection, shop, _Signed, approval_id, decided_by=shop.owner)

    with pytest.raises(ExportStateError) as refused:
        _release(connection, shop, created, approval_id)
    assert refused.value.reason_code == "EXPORT_QUERY_VERSION_RETIRED"
    assert _data_exports(connection, shop.store_id) == 0

    # A retired envelope that is still waiting is refused the same way at release, and the live
    # code will not let an owner sign it.
    with pytest.raises(ExportStateError) as unsigned:
        _release(connection, shop, created, waiting_id)
    assert unsigned.value.reason_code == "EXPORT_QUERY_VERSION_RETIRED"
    with pytest.raises(ApprovalResourceChangedError):
        _decide(connection, shop, _Signed, waiting_id, decided_by=shop.owner)

    with connection.cursor() as cursor:
        disclosed = SanitizedExportRepository.read_for_approval(
            cursor, approval_id=approval_id, principal=shop.owner
        )
    assert disclosed is not None
    assert disclosed.bound_shape_retired is True
    assert disclosed.bound_query_version == _retired_statement(facts).query_version
    assert disclosed.query_version == created.query_version
    assert disclosed.rendered_hash == created.rendered_hash != retired_rendered

    # An envelope matching neither document is still the generic mismatch, not the retired name.
    class _Forged(_Signed):
        rendered_hash = "JCS-SHA256-V1:" + "c" * 64

    forged_id = _raise_envelope(connection, shop, _Forged)
    with pytest.raises(ExportStateError) as forged:
        _release(connection, shop, created, forged_id)
    assert forged.value.reason_code == "EXPORT_APPROVAL_NOT_BOUND"

    # The other direction: the same request under an envelope over the live document.
    live_id = _approve(connection, shop, created)
    with connection.cursor() as cursor:
        live = SanitizedExportRepository.read_for_approval(
            cursor, approval_id=live_id, principal=shop.owner
        )
    assert live is not None
    assert (live.bound_shape_retired, live.bound_query_version) == (False, created.query_version)
    produced = _release(connection, shop, created, live_id)
    assert produced.query_version == created.query_version
    assert produced.query_version == (EXPORT_WINDOW_QUERY if window else EXPORT_QUERY).label
    row = _body(produced.content_csv)[str(order_id)]
    assert (row["paid_transfer_vnd"], row["remaining_vnd"]) == ("50000", str(TOTAL_VND - 50_000))
    assert _data_exports(connection, shop.store_id) == 1


def test_no_customer_personal_data_reaches_the_file(connection: psycopg.Connection[Any]) -> None:
    """`CUSTOMER-001` gave orders a customer; spec §0 says no phone value ever enters an export.

    A customer is recorded with a distinctive name, phone number, address and note, an order is
    taken for them, and a deposit by transfer is recorded with a bank reference. The produced bytes
    are then searched for each -- the file a person holds, not the SQL -- and for the customer's
    key. Each value is first shown to be really stored, so the search cannot pass over nothing.
    """
    shop = _Shop(connection, datetime.now(UTC))
    publish_privacy_notice(connection, actor_id=shop.owner.staff_user_id, payload=notice_payload())
    staff = _operator(connection, shop.store_id)
    subscriber = f"9{uuid4().int % 10**8:08d}"
    national, e164 = f"0{subscriber}", f"+84{subscriber}"
    name = "Chị Riêng-Tư Xuất-7731"
    address = "Hẻm Riêng 7731, Vĩnh Hải"
    note = "GHI-CHU-RIENG-7731"
    customer_id = CustomerRepository().create(
        connection,
        store_id=shop.store_id,
        principal=staff,
        phone=national,
        display_name=name,
        delivery_address=address,
        note=note,
        kind=CustomerKind.RETAIL,
        service_consent=True,
        marketing_consent=False,
        at=shop.now,
        correlation_id=uuid4(),
    )
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=shop.store_id, principal=staff, customer_id=customer_id
    )
    order_id = (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                shop.store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                shop.now,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )
    _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True)
    _pay(connection, order_id, staff, 50_000, TRANSFER, ref="RIENG7731")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT o.customer_id, c.display_name, c.delivery_address, c.note, c.phone_last4,
                   p.bank_ref_last
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            JOIN order_payments p ON p.order_id = o.id
            WHERE o.id = %s
            """,
            (order_id,),
        )
        stored = cursor.fetchone()
    assert stored is not None
    assert (UUID(str(stored[0])), stored[1], stored[2], stored[3]) == (
        customer_id,
        name,
        address,
        note,
    )
    assert stored[4] == subscriber[-4:]
    assert stored[5] == "RIENG7731"

    created = _request(connection, shop, _local_date(shop.now), None)
    produced = _release(connection, shop, created, _approve(connection, shop, created))
    content = produced.content_csv

    assert str(order_id) in content
    assert _body(content)[str(order_id)]["paid_transfer_vnd"] == "50000"
    for leaked in (national, e164, subscriber, name, "Riêng-Tư", address, note, str(customer_id)):
        assert leaked not in content, leaked
    assert "RIENG7731" not in content
    # And no column that could carry any of them is in the header.
    header = content.splitlines()[len(EXPORT_HEADER_KEYS)]
    for column in ("customer", "phone", "name", "address", "note", "bank_ref"):
        assert column not in header, column
    # The exclusions say so by name, in the document the owner signs.
    assert {"orders.customer_id", "order_payments.bank_ref_last"} <= set(created.excludes)


# --- round 7 wave 2 integration: the storage fee and the account ----------------------------------


def test_the_storage_fee_is_owed_and_an_account_order_is_owed_not_paid(
    connection: psycopg.Connection[Any],
) -> None:
    """`UNCLAIMED-001` and `PAYMENT-002` on the export, one shop-local day, four orders:

    * waited 25 days and paid at pickup, the fee included: before this the file refused itself
      (`EXPORT_MONEY_INCONSISTENT`), since the payments exceeded the quoted total it called owed;
    * waited 25 days, a deposit taken, still on the shelf: the fee accrued so far is owed;
    * a hotel's order that left on its account, then half paid through the account: owed, and paid
      only what the account paid -- `balance_status` says `ON_ACCOUNT`;
    * a hotel's order that waited 25 days and left on the account: the fee fixed with the charge.
    """
    shop = _Shop(connection, datetime.now(UTC))
    owner = shop.owner
    publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())
    publish_account_terms(connection, actor_id=owner.staff_user_id, payload=ACCOUNT_TERMS)
    publish_storage_policy(connection, actor_id=owner.staff_user_id, payload=storage_payload())
    staff = _operator(connection, shop.store_id)
    try:
        hotel = CustomerRepository().create(
            connection,
            store_id=shop.store_id,
            principal=staff,
            phone=f"09{uuid4().int % 10**8:08d}",
            display_name="Khách sạn Xuất-Công-Nợ",
            delivery_address=None,
            note=None,
            kind=CustomerKind.BUSINESS,
            service_consent=True,
            marketing_consent=False,
            at=shop.now,
            correlation_id=uuid4(),
        )
        AccountRepository().open(
            connection,
            OpenAccountCommand(
                store_id=shop.store_id,
                customer_id=hotel,
                credit_limit_vnd=2_000_000,
                principal=owner,
                correlation_id=uuid4(),
                at=shop.now,
            ),
        )
        settled = _ready_for(connection, shop, staff, None)
        waiting = _ready_for(connection, shop, staff, None)
        on_account = _ready_for(connection, shop, staff, hotel)
        waited_on_account = _ready_for(connection, shop, staff, hotel)
        for order_id in (settled, waiting, waited_on_account):
            _age_ready(connection, order_id, 25)
        fee = _read(connection, settled, staff).owed_vnd - TOTAL_VND
        assert fee > 0
        _pay(connection, settled, staff, TOTAL_VND + fee, CASH, collected=True)
        _pay(connection, waiting, staff, 50_000, CASH)
        for order_id in (on_account, waited_on_account):
            AccountRepository().charge(
                connection,
                AccountChargeCommand(
                    order_id=order_id,
                    expected_row_version=_read(connection, order_id, staff).row_version,
                    collected_by_customer=True,
                    principal=staff,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )
        half = TOTAL_VND // 2
        with connection.cursor() as cursor:
            account = AccountRepository().read(
                cursor,
                store_id=shop.store_id,
                customer_id=hotel,
                principal=owner,
                now=datetime.now(UTC),
            )
        assert account.account is not None
        AccountRepository().record_payment(
            connection,
            AccountPaymentCommand(
                store_id=shop.store_id,
                customer_id=hotel,
                expected_row_version=account.account.row_version,
                amount_vnd=half,
                method=TRANSFER,
                transfer_seen=True,
                bank_ref_last=None,
                principal=staff,
                correlation_id=uuid4(),
                at=datetime.now(UTC),
            ),
        )

        created = _request(connection, shop, _local_date(shop.now), None)
        produced = _release(connection, shop, created, _approve(connection, shop, created))
    finally:
        publish_storage_policy(
            connection, actor_id=owner.staff_user_id, payload=withdrawal_document()
        )

    rows = _body(produced.content_csv)
    total, with_fee = TOTAL_VND, TOTAL_VND + fee

    row = rows[str(settled)]
    assert row["balance_status"] == "PAID"
    assert (row["owed_vnd"], row["paid_cash_vnd"], row["paid_vnd"], row["remaining_vnd"]) == (
        str(with_fee),
        str(with_fee),
        str(with_fee),
        "0",
    )
    assert (row["expected_total_vnd"], row["paid_amount_vnd"]) == (str(with_fee), str(with_fee))

    row = rows[str(waiting)]
    assert row["balance_status"] == "PARTIALLY_PAID"
    assert (row["owed_vnd"], row["paid_vnd"], row["remaining_vnd"]) == (
        str(with_fee),
        "50000",
        str(with_fee - 50_000),
    )

    # The account's payment reaches the oldest charge first: this order, half paid by transfer.
    row = rows[str(on_account)]
    assert row["balance_status"] == "ON_ACCOUNT"
    assert (
        row["owed_vnd"],
        row["paid_cash_vnd"],
        row["paid_transfer_vnd"],
        row["paid_vnd"],
        row["remaining_vnd"],
    ) == (str(total), "0", str(half), str(half), str(total - half))
    # Owed, not paid: nothing settled, so the settlement ledger has nothing to say.
    assert (row["expected_total_vnd"], row["paid_amount_vnd"]) == ("", "")

    row = rows[str(waited_on_account)]
    assert row["balance_status"] == "ON_ACCOUNT"
    assert (row["owed_vnd"], row["paid_vnd"], row["remaining_vnd"]) == (
        str(with_fee),
        "0",
        str(with_fee),
    )
    # And no account customer's name reaches the file.
    assert "Xuất-Công-Nợ" not in produced.content_csv


def _ready_for(connection: Any, shop: _Shop, staff: Any, customer_id: UUID | None) -> UUID:
    """An order taken today (for `customer_id`, when given) and washed to ready."""
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=shop.store_id, principal=staff, customer_id=customer_id
    )
    order_id = (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                shop.store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                shop.now,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )
    view = _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True).view
    for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
        view = _step(connection, order_id, staff, view.row_version, step).view
    return order_id


def _age_ready(connection: Any, order_id: UUID, days: int) -> None:
    """The documented harness step (`test_unclaimed_laundry._age`): ready `days` days earlier."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE orders
            SET production_ready_at = production_ready_at - make_interval(days => %s),
                production_accepted_at = production_accepted_at - make_interval(days => %s),
                row_version = row_version + 1
            WHERE id = %s
            """,
            (days, days, order_id),
        )
