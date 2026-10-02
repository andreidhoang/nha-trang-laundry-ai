"""`DAILY-SUMMARY-001` (`DEC-039`) against real PostgreSQL.

What only a database can prove:

* **the summary restates the report** -- on the report's own seeded shop, a closed day's lines are
  the report's one-day figures, word for word as the template writes them, and every figure beside
  a sentence equals the report's;
* **a live source is today's only** -- a past day omits the SLA board lines, the open-complaint
  count and the waiting list as `LIVE_ONLY_TODAY`; a shop with no account omits the accounts line
  as `NO_ACCOUNTS`;
* **the wave-2 hooks, wired** (round 7 wave 2 integration) -- the waiting list counted past 20 and
  60 days with the list's own population and day rule (an order that left on account is not on
  it), and the accounts' money due and overdue on a fixed past day, equal to the account card's
  own figures -- in counts and money only, no account customer's name;
* **today, every source answers** -- a promised order past its promise, an unpromised order past
  the stated mark, an open complaint and a line of Sổ thu chi each land in their line, with the
  report's figures unchanged;
* **no personal data** -- customers with names and phone numbers, a complaint and an expense note
  that mention them: none of it appears anywhere in the summary;
* **the report's gate** -- an operator, a reader of another shop and a day after today are refused;
  an accountant reads everything but the board, which says so;
* **no promise, no policy** -- a shop with no published turnaround policy and no promised order
  omits "late against promise" rather than printing a zero about promises nobody made;
* **a pinned version** -- the template's digest is pinned, so a changed sentence fails here.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
from collections.abc import Generator, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.customers import CustomerRepository
from nha_trang_laundry_db.daily_summary import (
    DailySummary,
    DailySummaryRepository,
    daily_summary_template_version,
    day_figures,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.promise_policy import publish_turnaround_policy
from nha_trang_laundry_db.reports import (
    ReportAuthorizationError,
    ReportRepository,
    ReportWindowError,
    shop_today,
)
from nha_trang_laundry_db.shop_capture import ExpenseRepository, RecordExpenseCommand
from nha_trang_laundry_db.unclaimed import UnclaimedRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.customers import CustomerKind
from nha_trang_laundry_domain.daily_summary import format_day
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.promise import PromiseChoice
from nha_trang_laundry_domain.shop_capture import ExpenseCategory
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from psycopg import sql
from psycopg.conninfo import make_conninfo
from quote_test_data import accepted_quote
from test_customer_accounts import Shop as AccountShop
from test_customer_accounts import _local as _account_local
from test_customer_notice import notice_payload
from test_order_promise_repository import STANDARD, _document, _receive
from test_order_step_repository import TOTAL_VND
from test_reports import AS_OF, DAY, _Order, _person, _seeded_shop, _store

#: `daily-summary-v2`, pinned. A changed sentence, unit, order of lines, omission rule or statement
#: moves the digest; the suite then fails here until the change is read and the identifier moved.
#: v2 (round 7 wave 2 integration): the waiting-list and accounts hooks wired, `NO_ACCOUNTS` added,
#: and the two hooks' statements hashed in. v1 was `daily-summary-v1:935e9e90a50bd6a8`.
#: v3 (round 8, `SUMMARY-ATTENTION-001`): the *Cần chú ý* block and its statements. v2 was
#: `daily-summary-v2:13441b651e00127d`.
#: v4 (round 9, GOODS-AND-DRAWER-009, review M4): the money line says how refunds went back and what
#: the drawer did -- cash in minus cash handed back. v3 was `daily-summary-v3:b73120eb9bf3c640`.
#: v5 (round 9b, MONEY-RESIDUAL-009B J1, `DEC-050`): "chờ quá N ngày" and the reminder steps count
#: the days the laundry waited, not the days the shop held it (`awaiting-pickup-count-v2`, the
#: reminder rules over `unclaimed.waiting_clock`). v4 was `daily-summary-v4:c8b8abadb5e4578f`.
#: The v5 digest moved once more inside round 9b, before v5 ever left the branch (it was
#: `d0cbea0c5eb30373`): the reminder rules module now carries `pickup-reminder-v2`, whose text names
#: the held days (verification round 1, P2). The count the summary prints did not change.
PINNED_TEMPLATE_VERSION = "daily-summary-v5:1d5f720d3dfa67f2"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url()) as established:
        apply_migrations(established)
        established.commit()
        yield established


@pytest.fixture
def scratch_url() -> Iterator[str]:
    """A database of its own, migrated from empty, where nobody has published anything."""

    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_summary_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _summary(
    connection: Any,
    store_id: UUID,
    principal: StaffPrincipal,
    day: Any,
    as_of: datetime,
) -> DailySummary:
    connection.commit()
    return DailySummaryRepository.read(
        connection,
        store_id=store_id,
        principal=principal,
        policy=STANDARD_WASH_SLA,
        day=day,
        as_of=as_of,
    )


def _lines(summary: DailySummary) -> dict[str, tuple[str, dict[str, Any]]]:
    return {line.key.value: (line.text, dict(line.figures)) for line in summary.rendered.lines}


def _omitted(summary: DailySummary) -> dict[str, tuple[str, str]]:
    return {item.key.value: (item.reason.value, item.source) for item in summary.rendered.omitted}


def _as_json(summary: DailySummary) -> str:
    """Everything the summary carries, as one string a scan can search."""
    return json.dumps(
        {
            "lines": [dataclasses.asdict(line) for line in summary.rendered.lines],
            "omitted": [dataclasses.asdict(item) for item in summary.rendered.omitted],
            "text": summary.rendered.text,
            "sources": summary.sources,
        },
        ensure_ascii=False,
        default=str,
    )


# --- the pinned version ---------------------------------------------------------------------------


def test_the_template_version_is_pinned() -> None:
    version = daily_summary_template_version()
    assert version.identifier == "daily-summary-v5"
    assert version.label == PINNED_TEMPLATE_VERSION


# --- a closed day: the report's figures, in the template's words ----------------------------------


def test_a_closed_day_restates_the_report_word_for_word(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _seeded_shop(connection)

    summary = _summary(connection, shop.store_id, shop.owner, DAY, AS_OF)

    assert summary.so_far is False
    assert summary.day == DAY
    assert summary.template_version == PINNED_TEMPLATE_VERSION
    # The seeded shop's DAY, in the fixture's own story (`test_reports._seeded_shop`): A, B, C, E,
    # F and G taken in; A completed; G cancelled; A, B and C finished, B late by the 8-hour mark and
    # none of them promised; A's two complaints; A paid at 12:30 and F at the day's last instant.
    lines = {key: text for key, (text, _) in _lines(summary).items()}
    assert lines == {
        "HEADER": f"Tóm tắt {format_day(DAY)}.",
        "ORDERS": "Nhận 6 đơn mới. Hoàn tất 1 đơn. Huỷ 1 đơn.",
        # Settlements before PAYMENT-001's method are cash (`0056`); F's refund is NEXT's, at
        # midnight exactly, so this day has none.
        "MONEY": "Đã thu 220.000đ (2 khoản). Tiền mặt 220.000đ. Chuyển khoản 0đ.",
        "FINISHED_ON_TIME": (
            "Giặt xong 3 đơn. 2 trên 3 đơn xong đúng hẹn. "
            "3 đơn không có giờ hẹn, được so với mốc nội bộ 8 giờ."
        ),
        "COMPLAINTS_NEW": "2 khiếu nại mới trong ngày.",
        "SPENDING": "Chưa ghi khoản chi nào trong ngày.",
    }
    assert _omitted(summary) == {
        "LATE_AGAINST_PROMISE": ("LIVE_ONLY_TODAY", "SLA_BOARD"),
        "WITHOUT_PROMISE": ("LIVE_ONLY_TODAY", "SLA_BOARD"),
        "WAITING_PICKUP": ("LIVE_ONLY_TODAY", "UNCLAIMED-001"),
        "COMPLAINTS_OPEN": ("LIVE_ONLY_TODAY", "INCIDENTS"),
        "ACCOUNTS_DUE": ("NO_ACCOUNTS", "PAYMENT-002"),
        # v3: the seeded shop has no trade in the four weeks before, so there is no usual.
        "ATTN_NUMBERS": ("TOO_LITTLE_HISTORY", "REPORT"),
    }
    assert summary.rendered.text == "\n".join(text for text, _ in _lines(summary).values())


def test_every_figure_beside_a_sentence_is_the_reports_own(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _seeded_shop(connection)
    summary = _summary(connection, shop.store_id, shop.owner, DAY, AS_OF)
    with connection.cursor() as cursor:
        report = ReportRepository.store_report(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            policy=STANDARD_WASH_SLA,
            from_date=DAY,
            to_date=DAY,
            as_of=AS_OF,
        )
    expected = day_figures(report)
    figures: dict[str, Any] = {}
    for _, attached in _lines(summary).values():
        figures.update(attached)
    for field in (
        "orders_created",
        "orders_completed",
        "orders_cancelled",
        "finished",
        "finished_on_time",
        "finished_without_promise",
        "complaints_opened",
        "collected_vnd",
        "collected_entries",
        "cash_vnd",
        "transfer_vnd",
        "refunded_vnd",
        "net_vnd",
        "net_direction",
        # GOODS-AND-DRAWER-009 (review M4): the refunds by method and the drawer.
        "refunded_cash_vnd",
        "refunded_transfer_vnd",
        "refunded_unknown_vnd",
        "refunded_unknown_entries",
        "drawer_vnd",
        "drawer_direction",
    ):
        assert figures[field] == getattr(expected, field), field
    assert (figures["cash_vnd"], figures["transfer_vnd"]) == (
        expected.cash_vnd,
        expected.transfer_vnd,
    )
    assert summary.sources == (("report", report.query_version),)


# --- today: every source, and no personal data ----------------------------------------------------


def _mobile() -> tuple[str, str, str]:
    """A fresh mobile number: national, e164 and spaced the way the console prints it."""
    subscriber = "9" + "".join(random.choice("0123456789") for _ in range(8))
    national = f"0{subscriber}"
    return national, f"+84{subscriber}", f"{national[:4]} {national[4:7]} {national[7:]}"


def _customer_order(
    connection: Any, store_id: UUID, staff: StaffPrincipal, customer_id: UUID
) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, lines=(STANDARD,), customer_id=customer_id
    )
    return (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                datetime.now(UTC),
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )


def test_today_every_source_answers_and_no_name_or_phone_reaches_the_text(
    connection: psycopg.Connection[Any],
) -> None:
    now = datetime.now(UTC)
    today = shop_today(now)
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    accountant = _person(connection, store_id, frozenset({StaffRole.ACCOUNTANT}))
    publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())
    # Published for the whole database (a configuration version is not per shop); idempotent on
    # the version in force, so a suite that already published it changes nothing here.
    publish_turnaround_policy(connection, actor_id=owner.staff_user_id, payload=_document())
    connection.commit()

    people = [("chị Lan Phương", *_mobile()), ("anh Trần Tuấn Kiệt", *_mobile())]
    customers = [
        CustomerRepository().create(
            connection,
            store_id=store_id,
            principal=staff,
            phone=national,
            display_name=name,
            delivery_address="12 Trần Phú, Lộc Thọ, Nha Trang",
            note=f"Gọi {spaced} trước khi giao",
            kind=CustomerKind.RETAIL,
            service_consent=True,
            marketing_consent=False,
            at=now,
            correlation_id=uuid4(),
        )
        for name, national, _, spaced in people
    ]

    before = _summary(connection, store_id, owner, today, now)
    assert before.so_far is True

    # A promised order, received 30 hours ago with the express promise: past its promise now.
    promised = _customer_order(connection, store_id, staff, customers[0])
    received = _receive(
        connection,
        promised,
        staff,
        occurred_at=now - timedelta(hours=30),
        choice=PromiseChoice.EXPRESS_2H,
    )
    PaymentRepository().record(
        connection,
        PaymentCommand(
            order_id=promised,
            expected_row_version=received.row_version,
            amount_vnd=50_000,
            method=PaymentMethod.CHUYEN_KHOAN,
            transfer_seen=True,
            bank_ref_last=None,
            collected_by_customer=False,
            principal=staff,
            correlation_id=uuid4(),
        ),
    )
    # An order with no promise, accepted ten hours ago: past the stated 8-hour mark.
    unpromised = _Order(connection, store_id, staff, created=now - timedelta(hours=10))
    unpromised.activate(now - timedelta(hours=10))
    IncidentRepository().open_from_counter(
        connection,
        StaffIncidentOpenCommand(
            store_id=store_id,
            order_id=promised,
            evidence_summary=f"{people[0][0]} ({people[0][3]}) báo áo còn vết ố",
            actor_id=staff.staff_user_id,
            correlation_id=uuid4(),
            opened_at=now,
        ),
        principal=staff,
    )
    ExpenseRepository().record(
        connection,
        RecordExpenseCommand(
            store_id=store_id,
            spent_on=today,
            category=ExpenseCategory.HOA_CHAT,
            amount_vnd=320_000,
            note=f"Mua hoá chất, nhờ {people[1][0]} chở giúp",
            principal=owner,
            idempotency_key=f"expense-{uuid4().hex}",
            correlation_id=uuid4(),
            today=today,
        ),
    )

    after = _summary(connection, store_id, owner, today, now)

    lines = _lines(after)
    assert lines["LATE_AGAINST_PROMISE"] == (
        "1 đơn chưa trả khách đã trễ giờ hẹn.",
        {"promised": 1, "promised_late": 1},
    )
    assert lines["WITHOUT_PROMISE"] == (
        "1 đơn chưa trả khách không có giờ hẹn. 1 đơn trong số đó đã quá mốc nội bộ 8 giờ.",
        {"unpromised": 1, "unpromised_late": 1},
    )
    assert lines["COMPLAINTS_OPEN"] == ("Còn 1 khiếu nại đang mở.", {"open_count": 1})
    assert lines["SPENDING"][0] == "Ghi 1 khoản chi, tổng 320.000đ. Hoá chất 320.000đ."
    # The report's figures moved by exactly what was done today, and the summary says so.
    assert lines["MONEY"][1]["transfer_vnd"] == before_figure(before, "transfer_vnd") + 50_000
    assert lines["COMPLAINTS_NEW"][1]["complaints_opened"] == (
        before_figure(before, "complaints_opened") + 1
    )
    assert lines["HEADER"][0].startswith(f"Tóm tắt {format_day(today)}, tính đến ")
    # Nothing of this shop has waited on the shelf, and it has opened no account.
    assert lines["WAITING_PICKUP"] == (
        "0 đơn giặt xong chờ khách lấy quá 20 ngày.",
        {"over_20_days": 0, "over_60_days": 0},
    )
    omitted = _omitted(after)
    # v3: the comparison waits for closing time today, or for three weeks of trade; which of the
    # two depends on the wall clock this test runs at, and both are said in words.
    assert omitted.pop("ATTN_NUMBERS") in (
        ("DAY_NOT_OVER", "REPORT"),
        ("TOO_LITTLE_HISTORY", "REPORT"),
    )
    assert omitted == {"ACCOUNTS_DUE": ("NO_ACCOUNTS", "PAYMENT-002")}
    # The promised order past its promise heads the summary under *Cần chú ý*.
    assert lines["ATTENTION"][0] == "Cần chú ý:"
    assert lines["ATTN_OVERDUE"] == (
        "- 1 đơn chưa trả khách đã trễ giờ hẹn.",
        {"promised_late": 1},
    )
    assert [key for key, _ in after.sources] == ["report", "sla_board", "awaiting_pickup"]

    # No personal data: every written form of every number, every name, the address and the
    # notes are absent from everything the summary carries.
    scanned = _as_json(after)
    for name, national, e164, spaced in people:
        for value in (name, national, e164, e164[1:], spaced, national[-6:]):
            assert value not in scanned, value
    for value in ("Trần Phú", "vết ố", "chở giúp", "Lan", "Kiệt"):
        assert value not in scanned, value

    # An accountant reads the same summary, except the board, which is not an accountant's read.
    seen = _summary(connection, store_id, accountant, today, now)
    assert _omitted(seen)["LATE_AGAINST_PROMISE"] == ("ROLE_NOT_PERMITTED", "SLA_BOARD")
    assert _omitted(seen)["WITHOUT_PROMISE"] == ("ROLE_NOT_PERMITTED", "SLA_BOARD")
    # Nor the waiting list or the account card, which are not an accountant's reads either.
    assert _omitted(seen)["WAITING_PICKUP"] == ("ROLE_NOT_PERMITTED", "UNCLAIMED-001")
    assert _omitted(seen)["ACCOUNTS_DUE"] == ("ROLE_NOT_PERMITTED", "PAYMENT-002")
    assert _lines(seen)["COMPLAINTS_OPEN"] == lines["COMPLAINTS_OPEN"]
    assert _lines(seen)["MONEY"] == lines["MONEY"]


def before_figure(summary: DailySummary, name: str) -> int:
    for _, figures in _lines(summary).values():
        if name in figures:
            return int(figures[name])
    raise KeyError(name)


# --- the report's gate ----------------------------------------------------------------------------


def test_an_operator_another_shop_and_a_day_after_today_are_refused(
    connection: psycopg.Connection[Any],
) -> None:
    now = datetime.now(UTC)
    today = shop_today(now)
    store_id = _store(connection)
    elsewhere = _store(connection)
    operator = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    stranger = _person(connection, elsewhere, frozenset({StaffRole.OWNER_ADMIN}))
    unverified = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}), mfa=False)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    for refused in (operator, stranger, unverified):
        with pytest.raises(ReportAuthorizationError):
            _summary(connection, store_id, refused, today, now)
        connection.rollback()
    with pytest.raises(ReportWindowError) as caught:
        _summary(connection, store_id, owner, today + timedelta(days=1), now)
    assert caught.value.reason_code == "REPORT_WINDOW_IN_FUTURE"
    connection.rollback()
    # The four report readers read it.
    for role in (
        StaffRole.OWNER_ADMIN,
        StaffRole.OPS_APPROVER,
        StaffRole.ACCOUNTANT,
        StaffRole.AUDITOR,
    ):
        reader = _person(connection, store_id, frozenset({role}))
        assert _summary(connection, store_id, reader, today, now).so_far is True


def test_the_read_writes_nothing(connection: psycopg.Connection[Any]) -> None:
    shop = _seeded_shop(connection)

    def ledger() -> tuple[int, int, int]:
        with connection.cursor() as cursor:
            counts = []
            for table in ("domain_events", "audit_events", "outbox_events"):
                cursor.execute(f"SELECT count(*) FROM {table}")
                row = cursor.fetchone()
                assert row is not None
                counts.append(int(row[0]))
        connection.commit()
        return counts[0], counts[1], counts[2]

    before = ledger()
    _summary(connection, shop.store_id, shop.owner, DAY, AS_OF)
    _summary(connection, shop.store_id, shop.owner, shop_today(AS_OF), AS_OF)
    assert ledger() == before


# --- no promise, no policy ------------------------------------------------------------------------


def test_with_no_turnaround_policy_and_no_promise_late_against_promise_is_omitted(
    scratch_url: str,
) -> None:
    with psycopg.connect(scratch_url) as connection:
        apply_migrations(connection)
        connection.commit()
        now = datetime.now(UTC)
        store_id = _store(connection)
        owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
        staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
        _Order(connection, store_id, staff, created=now - timedelta(hours=2)).activate(
            now - timedelta(hours=2)
        )

        summary = _summary(connection, store_id, owner, shop_today(now), now)

        assert _omitted(summary)["LATE_AGAINST_PROMISE"] == (
            "TURNAROUND_POLICY_UNPUBLISHED",
            "PROMISE-001",
        )
        assert _lines(summary)["WITHOUT_PROMISE"] == (
            "1 đơn chưa trả khách không có giờ hẹn.",
            {"unpromised": 1, "unpromised_late": 0},
        )
        assert "trễ giờ hẹn" not in summary.rendered.text


# --- round 7 wave 2 integration: the two hooks, wired ---------------------------------------------


def _age_ready(connection: Any, order_id: UUID, days: int) -> None:
    """The documented harness step `test_unclaimed_laundry._age` takes: the laundry was accepted
    and reported ready `days` days earlier, both stamps moved together."""

    with connection.transaction(), connection.cursor() as cursor:
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


def test_the_waiting_line_is_the_waiting_list_counted_and_an_account_order_is_not_on_it(
    connection: psycopg.Connection[Any],
) -> None:
    shop = AccountShop(connection)
    retail = shop.customer(CustomerKind.RETAIL)
    hotel = shop.customer()
    shop.open(hotel, 5_000_000)
    waited_25 = shop.ready_order(retail)
    waited_65 = shop.ready_order(retail)
    shop.ready_order(retail)  # ready today: waiting, not past any threshold
    on_account = shop.ready_order(hotel)
    for order_id, days in ((waited_25, 25), (waited_65, 65), (on_account, 30)):
        _age_ready(connection, order_id, days)
    # The hotel takes its laundry on the account: it has left the shop, so it is not waiting.
    shop.charge(on_account)
    now = datetime.now(UTC)

    summary = _summary(connection, shop.store_id, shop.owner, shop_today(now), now)

    assert _lines(summary)["WAITING_PICKUP"] == (
        "2 đơn giặt xong chờ khách lấy quá 20 ngày. 1 đơn đã chờ quá 60 ngày.",
        {"over_20_days": 2, "over_60_days": 1},
    )
    # The list the counter reads says the same: its population, its days.
    with connection.cursor() as cursor:
        listed = UnclaimedRepository.list_awaiting_pickup(
            cursor, store_id=shop.store_id, principal=shop.owner, as_of=now
        )
    assert on_account not in {row.order_id for row in listed.orders}
    waited = [row.days_waiting or 0 for row in listed.orders]
    assert (sum(days > 20 for days in waited), sum(days > 60 for days in waited)) == (2, 1)
    # The hotel's account now exists, so its line is printed: charged today, nothing due yet.
    assert _lines(summary)["ACCOUNTS_DUE"] == (
        "0 khách công nợ đến hạn trả, tổng 0đ.",
        {"due_accounts": 0, "due_vnd": 0, "overdue_accounts": 0, "overdue_vnd": 0},
    )
    assert [key for key, _ in summary.sources][-2:] == ["awaiting_pickup", "accounts"]
    # Counts and money only: the account customer's name is nowhere in it.
    assert "Homestay" not in _as_json(summary) and "Biển Xanh" not in _as_json(summary)


def test_the_accounts_line_is_the_ledgers_due_and_overdue_on_a_past_day(
    connection: psycopg.Connection[Any],
) -> None:
    """A spa charged in July and in August, paying one order's worth on 12 September.

    On the 10th, July is past due (its statement was due on 15 August) and August is due on the
    15th; on the 13th the payment has covered July and August is still only due; on the 16th August
    is past due too. The ledgers are append-only, so each past day is answered as of its end, and
    an account with no activity is counted as an account and in neither figure.
    """

    shop = AccountShop(connection)
    spa = shop.customer()
    idle = shop.customer()
    opened = _account_local(date(2026, 7, 1), 9)
    shop.open(spa, 2_000_000, at=opened)
    shop.open(idle, 1_000_000, at=opened)
    shop.charge(shop.ready_order(spa), at=_account_local(date(2026, 7, 20)))
    shop.charge(shop.ready_order(spa), at=_account_local(date(2026, 8, 10)))
    shop.pay(spa, TOTAL_VND, at=_account_local(date(2026, 9, 12)))
    now = datetime.now(UTC)

    def accounts_line(day: date) -> tuple[str, dict[str, Any]]:
        summary = _summary(connection, shop.store_id, shop.owner, day, now)
        assert summary.so_far is False
        assert "Homestay" not in _as_json(summary) and "Biển Xanh" not in _as_json(summary)
        return _lines(summary)["ACCOUNTS_DUE"]

    assert accounts_line(date(2026, 9, 10)) == (
        "1 khách công nợ đến hạn trả, tổng 110.000đ. 1 khách công nợ đã quá hạn, tổng 110.000đ.",
        {"due_accounts": 1, "due_vnd": 110_000, "overdue_accounts": 1, "overdue_vnd": 110_000},
    )
    assert accounts_line(date(2026, 9, 13)) == (
        "1 khách công nợ đến hạn trả, tổng 110.000đ.",
        {"due_accounts": 1, "due_vnd": 110_000, "overdue_accounts": 0, "overdue_vnd": 0},
    )
    sixteenth = accounts_line(date(2026, 9, 16))
    assert sixteenth == (
        "0 khách công nợ đến hạn trả, tổng 0đ. 1 khách công nợ đã quá hạn, tổng 110.000đ.",
        {"due_accounts": 0, "due_vnd": 0, "overdue_accounts": 1, "overdue_vnd": 110_000},
    )
    # The account card's own overdue figure, on the same day, is the summary's.
    card = shop.account(spa, _account_local(date(2026, 9, 16), 20)).account
    assert card.overdue_vnd == sixteenth[1]["overdue_vnd"]
    assert card.outstanding_vnd == TOTAL_VND
