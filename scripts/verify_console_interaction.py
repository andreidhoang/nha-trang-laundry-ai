"""Drive the staff console in a real browser that types, with the API stubbed.

Not part of the default suite, and Playwright is deliberately not a repository dependency — see
``apps/web/README.md`` on why a second package tree is not free here. Run it explicitly::

    uv run --with playwright python scripts/verify_console_interaction.py

It exists because the defects it covers are invisible to everything else in the repository, and one
of them shipped. The earlier browser verification drove the quote builder with Playwright's
``page.fill()``, which sets a field's value in one shot. A human types. Typing rebuilt the form on
every keystroke, so a service code entered character by character produced ``S`` — the flagship
screen was unusable by an operator, and a check that never typed certified it as working.

A source-text test cannot see focus, a caret position, or whether a form survived a 401. This can.

The service field is no longer typed at all: it is a picker over the published pricebook, so the
assertions that typed ``STANDARD_WASH_DRY`` into it were deleted rather than weakened, and section
1 now checks the control that replaced them. The typing invariant kept its real subjects — the
quantity field and the contact field — and is still asserted on both.

Section 6 covers what the owner's-morning redesign is answerable for: that the day's takings are
rendered as the server sent them, that an empty queue costs no space, and that the all-clear line
claims only the queues it actually checked. That last one is an assertion about a *refusal*, which
is the kind this console most needs and most easily loses.

Section 12 is here for the same reason section 1 is: the defect it covers shipped, and every other
kind of test certified the screen while it was broken. `RANGE-APPROVAL-VISIBILITY-001` was an
ordering and reachability defect rather than a missing string -- the approvals queue returned a
digest, the card linked to `#/quotes`, and that screen renders the published BAND, because the
revision the envelope binds is the one before any price was chosen. Every sentence on the screen
was true and the owner still could not see the number they were authorising. So the assertions
there are about document order (`compareDocumentPosition` between the amount and the approve
button), about whether the button is pressable, and -- twice over -- about what is *not* on screen
when the amount cannot be read.
"""

from __future__ import annotations

import http.server
import itertools
import json
import os
import socketserver
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import Route, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
PORT = 8912
STORE = "11111111-2222-4333-8444-555555555555"

PASS: list[str] = []
FAIL: list[str] = []

#: `INCIDENT-INTAKE-001`. Two rows, because the interesting one is the second: an incident whose
#: description has been disposed of under `INCIDENT_EVIDENCE` at 365 days, or which the agent path
#: opened with no summary at all. It must read as an expected retention state, not as a gap.
INCIDENTS = [
    {
        "incident_id": "aaaaaaaa-1111-4333-8444-555555555555",
        "store_id": STORE,
        "order_id": "bbbbbbbb-2222-4333-8444-555555555555",
        "category": "SERVICE_QUALITY",
        "status": "OPEN",
        "fault_decided": False,
        "remedy_decided": False,
        "opened_at": "2026-09-17T03:00:00+00:00",
        "evidence_summary": "Áo sơ mi trắng bị ố vàng ở cổ.",
    },
    {
        "incident_id": "cccccccc-3333-4333-8444-555555555555",
        "store_id": STORE,
        "order_id": "dddddddd-4444-4333-8444-555555555555",
        "category": "SERVICE_QUALITY",
        "status": "CLOSED",
        "fault_decided": True,
        "remedy_decided": True,
        "opened_at": "2025-09-01T03:00:00+00:00",
        "evidence_summary": None,
    },
]


#: `REMEDY-001`. One `RemedyOptionsResponse`, with `DEC-004`'s own figures rather than round
#: numbers: the 100.000 d staff ceiling, both windows still open, and two priced lines whose 5x caps
#: straddle that ceiling -- 90.000 d below it and 600.000 d above. The straddle is the whole point,
#: because "will this need the owner?" has to be answerable per line *before* an amount exists.
REMEDY_OPTIONS = {
    "incident_id": INCIDENTS[0]["incident_id"],
    "order_id": INCIDENTS[0]["order_id"],
    "policy_published": True,
    "staff_approval_ceiling_vnd": 100_000,
    "goods_returned_at": "2026-09-17T03:00:00+00:00",
    "rewash_window_closes_at": "2026-09-24T03:00:00+00:00",
    "rewash_window_open": True,
    "defect_window_closes_at": "2026-09-18T03:00:00+00:00",
    "defect_window_open": True,
    "damage_line_ceilings_vnd": {"line-large": 600_000, "line-small": 90_000},
    # `DEC-031`: the per-item terms the console now reads instead of the map above -- fee basis,
    # ceiling, what the item already carries, and what sends it to the owner whatever the amount.
    "damage_lines": [
        {
            "line_id": "line-large",
            "service_code": "DC_EVENING_DRESS",
            "service_name": "Váy dạ hội",
            "unit": "ITEM",
            "quantity": "1",
            "item_fee_basis": "UNIT",
            "item_fee_vnd": 120_000,
            "ceiling_vnd": 600_000,
            "pieces": 1,
            "line_ceiling_vnd": 600_000,
            "committed_vnd": 0,
            "owner_always": [],
        },
        {
            "line_id": "line-small",
            "service_code": "IRON_KNIT",
            "service_name": "Áo, quần thun",
            "unit": "ITEM",
            "quantity": "1",
            "item_fee_basis": "UNIT",
            "item_fee_vnd": 18_000,
            "ceiling_vnd": 90_000,
            "pieces": 1,
            "line_ceiling_vnd": 90_000,
            "committed_vnd": 0,
            "owner_always": [],
        },
    ],
    "late_delivery_credit_vnd": 17_000,
    "late_delivery_threshold_minutes": 120,
    "loss_requires_owner": True,
    "order_refunded": False,
}

REMEDY_PROPOSAL_ID = "77777777-8888-4333-8444-bbbbbbbbbbbb"
REMEDY_APPROVAL_ID = "88888888-9999-4333-8444-cccccccccccc"
REMEDY_CREDIT_ID = "99999999-aaaa-4333-8444-dddddddddddd"

#: What the server answers for a damage proposal above the staff ceiling: recorded, and stopped
#: with an `APPROVE_REMEDY` envelope raised at proposal time. Section 11 exists mostly to prove the
#: console said the owner would be needed *before* this response arrived.
REMEDY_PROPOSAL_OWNER = {
    "proposal_id": REMEDY_PROPOSAL_ID,
    "incident_id": REMEDY_OPTIONS["incident_id"],
    "order_id": REMEDY_OPTIONS["order_id"],
    "kind": "DAMAGE_COMPENSATION",
    "status": "OWNER_APPROVAL_REQUIRED",
    "outcome": "ALLOW",
    "proposal_hash": "JCS-SHA256-V1:" + "e" * 64,
    "policy_version": 1,
    "amount_vnd": 150_000,
    "ceiling_vnd": 600_000,
    "window_opened_at": REMEDY_OPTIONS["goods_returned_at"],
    "window_closes_at": REMEDY_OPTIONS["defect_window_closes_at"],
    "approval_id": REMEDY_APPROVAL_ID,
    "reason_code": None,
    "replayed": False,
}

REMEDY_EXECUTED = {
    "proposal_id": REMEDY_PROPOSAL_ID,
    "incident_id": REMEDY_OPTIONS["incident_id"],
    "order_id": REMEDY_OPTIONS["order_id"],
    "kind": "DAMAGE_COMPENSATION",
    "status": "EXECUTED",
    "event_type": "CREDIT_EXECUTED",
    "credit_id": REMEDY_CREDIT_ID,
    "amount_vnd": 150_000,
    "replayed": False,
}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'  ok  ' if ok else ' FAIL '} {name}" + (f"  — {detail}" if detail else ""))


#: The one answer the assistant stub gives, and the word-sized frames its stream endpoint replays
#: it in. The POST stub returns exactly this text, so the last progressive poll must equal it.
ASSISTANT_ANSWER = "Hôm nay cửa hàng có 1 đơn ở trạng thái ACTIVE. Tổng cộng 1 đơn."
ASSISTANT_STREAM_CHUNKS = [
    "Hôm nay cửa hàng ",
    "có 1 đơn ",
    "ở trạng thái ACTIVE. ",
    "Tổng cộng 1 đơn.",
]
assert "".join(ASSISTANT_STREAM_CHUNKS) == ASSISTANT_ANSWER

#: The intake stub: one existing contact binding, and the draft the POST creates from it. The
#: order-request id is what the "Báo giá ngay" link and the quotes prefill both carry.
CONTACT = "22222222-3333-4333-8444-666666666666"
ORDER_REQUEST = {
    "order_request_id": "33333333-4444-4333-8444-777777777777",
    "contact_binding_id": CONTACT,
    "status": "DRAFT",
    "row_version": 1,
    "created_at": "2026-08-16T03:00:00+00:00",
}
ORDER_REQUEST_CREATED = {**ORDER_REQUEST, "store_id": STORE, "replayed": False}

#: `OrderResponse`, verbatim. Note what it does not contain: `acquisition_source`. The console
#: cannot show the recorded source back, which is a registered gap on `#/gaps` and the reason
#: section 7 has to check the *form's* behaviour rather than reading the value off a card.
ORDER_CREATED = {
    "order_id": "44444444-5555-4333-8444-888888888888",
    "store_id": STORE,
    "commercial": "REQUESTED",
    "intake": "AWAITING_HANDOFF",
    "production": "NOT_STARTED",
    "balance": "UNPAID",
    "row_version": 1,
    "replayed": False,
}

#: What `GET /internal/v1/stores/{id}/settlements/today` returns. A non-round figure so a screen
#: that quietly rounded or reformatted it would be visible in the assertion.
SETTLEMENTS_TODAY = {
    "collected_vnd": 1_285_000,
    "settlement_count": 7,
    # `collected-today-v2` (DEC-024): refunds and the drawer's net movement travel beside the
    # takings, every amount non-negative. No refund in this fixture, so the drawer moved IN by
    # exactly what was collected.
    "refunded_vnd": 0,
    "refund_count": 0,
    "net_vnd": 1_285_000,
    "net_direction": "IN",
    "business_timezone": "Asia/Ho_Chi_Minh",
    # OPS-BOARD-001, invariant 18: the rule that produced the figure travels with the figure, and
    # the takings card renders it beneath the amount.
    "query_version": "collected-today-v2:c266d2f11377a64c",
}

#: What `GET /internal/v1/stores/{id}/day-summary` returns. Two statuses rather than one, because
#: the card renders a list and a single row would not show that it does.
DAY_SUMMARY = {
    "counts": [["ACTIVE", 4], ["COMPLETED", 2]],
    "total_orders": 6,
    "query_version": "today-status-counts-v1:cd422085b053d921",
    "business_timezone": "Asia/Ho_Chi_Minh",
}

#: The SLA board, in the shape and the order the server really answers in: acceptance order,
#: oldest accepted first. The durations are the server's integers -- section 13 asserts the console
#: renders them as Vietnamese units and never as a raw microsecond count or a negative number.
#: Section 12 is the range-approval work; this comment named it until OPS-BOARD-FIX-001.
#:
#: The page carries `next_accepted_at`/`next_order_id`, so the keyset "Tải thêm" control is
#: exercised against a real second page rather than described.
SLA_BOARD_POLICY_NOTICE = (
    "Mốc này tính theo quy tắc SLA_STANDARD_CLOTHES — 8 giờ kể từ khi nhận sản xuất; quy tắc SLA "
    "riêng của từng đơn là quyết định kinh doanh chưa được chốt, nên con số này dùng đúng một quy "
    "tắc đã nêu."
)
SLA_BOARD_FIRST_PAGE = {
    "items": [
        {
            "order_id": "aaaaaaa1-0000-4000-8000-000000000001",
            "commercial_status": "ACTIVE",
            "production_status": "IN_PROCESS",
            "production_accepted_at": "2026-09-09T00:00:00+00:00",
            "production_ready_at": None,
            "internal_risk_due_at": "2026-09-09T08:00:00+00:00",
            "sla_outcome": "BREACHED",
            "overall_outcome": "REQUIRE_HUMAN",
            "reason_codes": [
                "PRODUCTION_SLA_EXCLUDES_DELIVERY",
                "HUMAN_PROMISE_REQUIRED",
                "ELAPSED_EIGHT_HOUR_INTERNAL_RISK",
                "EXACT_CLOSURE_CUTOFF_UNPUBLISHED",
                "SLA_BREACHED",
                "BREACH_REMEDY_REQUIRES_HUMAN",
            ],
            "elapsed_microseconds": 39_600_000_000,
            "remaining_microseconds": 0,
            # Three hours past the mark.
            "breach_microseconds": 10_800_000_000,
        },
        {
            "order_id": "aaaaaaa2-0000-4000-8000-000000000002",
            "commercial_status": "ACTIVE",
            "production_status": "QUEUED",
            "production_accepted_at": "2026-09-09T04:00:00+00:00",
            "production_ready_at": None,
            "internal_risk_due_at": "2026-09-09T12:00:00+00:00",
            "sla_outcome": "PENDING",
            "overall_outcome": "REQUIRE_HUMAN",
            "reason_codes": ["SLA_PENDING", "ELAPSED_EIGHT_HOUR_INTERNAL_RISK"],
            "elapsed_microseconds": 25_200_000_000,
            # One hour left.
            "remaining_microseconds": 3_600_000_000,
            "breach_microseconds": 0,
        },
    ],
    "query_version": "sla-risk-board-v1:e56e50ea7beb4021",
    "policy_id": "SLA_STANDARD_CLOTHES",
    "policy_type": "COMMITMENT",
    "policy_target_max_hours": 8,
    "policy_notice_vi": SLA_BOARD_POLICY_NOTICE,
    "evaluated_at": "2026-09-09T11:00:00+00:00",
    "next_accepted_at": "2026-09-09T04:00:00+00:00",
    "next_order_id": "aaaaaaa2-0000-4000-8000-000000000002",
}
SLA_BOARD_SECOND_PAGE = {
    **SLA_BOARD_FIRST_PAGE,
    "items": [
        {
            "order_id": "aaaaaaa3-0000-4000-8000-000000000003",
            "commercial_status": "ACTIVE",
            "production_status": "READY_AT_STORE",
            "production_accepted_at": "2026-09-09T06:00:00+00:00",
            "production_ready_at": "2026-09-09T07:00:00+00:00",
            "internal_risk_due_at": "2026-09-09T14:00:00+00:00",
            "sla_outcome": "MET",
            "overall_outcome": "REQUIRE_HUMAN",
            "reason_codes": ["SLA_MET", "ELAPSED_EIGHT_HOUR_INTERNAL_RISK"],
            "elapsed_microseconds": 3_600_000_000,
            "remaining_microseconds": 25_200_000_000,
            "breach_microseconds": 0,
        }
    ],
    "evaluated_at": "2026-09-09T11:00:30+00:00",
    "next_accepted_at": None,
    "next_order_id": None,
}

#: What `GET /internal/v1/pricebook/services` returns, in the shape the real route publishes.
#: Two categories so the picker's grouping is actually exercised, and the units differ so that
#: choosing a service is observably what sets the line's unit.
PRICEBOOK_SERVICES = [
    {
        "code": "STD_WASH_DRY_LT6",
        "display_name": "Giặt sấy dưới 6kg",
        "category": "standard_weight",
        "unit": "KG",
    },
    {
        "code": "STD_WASH_DRY_GE6",
        "display_name": "Giặt sấy từ 6kg trở lên",
        "category": "standard_weight",
        "unit": "KG",
    },
    {
        "code": "IRON_TROUSERS_SHIRT",
        "display_name": "Ủi quần tây, áo sơ mi",
        "category": "ironing",
        "unit": "ITEM",
    },
    # `RANGE-PRICE-001`. One of the twenty services the published pricebook prices by inspection,
    # with the band the owner really published: `templates/services-pricebook.csv` carries
    # `DC_AO_DAI_TRADITIONAL,dry_cleaning,"Áo dài truyền thống",bộ,80000,240000`. Section 10 closes
    # this band, so the fixture is the real row rather than a round number chosen to pass.
    {
        "code": "DC_AO_DAI_TRADITIONAL",
        "display_name": "Áo dài truyền thống",
        "category": "dry_cleaning",
        "unit": "SET",
    },
]
MISSING_REQUEST = "99999999-9999-4999-8999-999999999999"

#: The quote section 10 drives, and the three responses the band flow needs.
#:
#: The shapes are `QuoteRevisionResponse`, `QuoteRevisionDetailResponse` and
#: `RangePriceProposalResponse` from `main.py`, field for field. What matters most about them is
#: what the first one does *not* carry: a revision presented as a band has no single service
#: subtotal, and the two scalar fields that look like one are the range maximum under a scalar
#: name. The stub sends the maximum in both, exactly as the server does, so the check below that
#: the console withholds them is checking against the real hazard.
BAND_QUOTE = "55555555-6666-4333-8444-999999999999"
BAND_SNAPSHOT = "JCS-SHA256-V1:" + "a" * 64
BAND_APPROVAL = "77777777-8888-4333-8444-bbbbbbbbbbbb"
#: The digest the envelope binds. One constant rather than two literals, because section 12 turns
#: on the queue row and the proposal body carrying the *same* value: the console refuses to show an
#: amount whose rendering is not the one the card is about to sign.
BAND_RENDERED = "JCS-SHA256-V1:" + "c" * 64
#: The band and the chosen amount exactly as `core/format.js` prints them.
#:
#: Written as escapes, and worth the awkwardness: `Intl.NumberFormat` puts a NO-BREAK SPACE (U+00A0)
#: before the currency sign and `moneyRange` joins the ends with an EN DASH (U+2013). A check
#: written with an ordinary space is absent from the page whether the amount is rendered or not --
#: which would make section 12's negative assertions, the ones that prove no figure is shown beside
#: a shut approve control, pass vacuously. Those are the assertions this item most needs to be real.
CHOSEN_AS_RENDERED = "150.000\u00a0\u20ab"
BAND_AS_RENDERED = "80.000\u00a0\u20ab \u2013 240.000\u00a0\u20ab"
AO_DAI_MIN_VND = 80_000
AO_DAI_MAX_VND = 240_000
CHOSEN_VND = 150_000

BAND_REVISION = {
    "quote_id": BAND_QUOTE,
    "revision": 1,
    "row_version": 1,
    "finality": "RANGE",
    "status": "REVIEW_REQUIRED",
    "snapshot_hash": BAND_SNAPSHOT,
    "list_service_subtotal_vnd": AO_DAI_MAX_VND,
    "net_service_subtotal_vnd": AO_DAI_MAX_VND,
    "display_total_min_vnd": AO_DAI_MIN_VND,
    "display_total_max_vnd": AO_DAI_MAX_VND,
    "reason_codes": ["RANGE_PRICE_REQUIRES_HUMAN", "TAX_TREATMENT_UNVERIFIED"],
    "required_approvals": [],
    "replayed": False,
}

#: `RangePriceProposalContentResponse` from `main.py`, field for field: what the owner is actually
#: being asked to authorise. Before this item no read returned it and the number lived nowhere at
#: all, so the owner followed the queue's link, read the BAND on `#/quotes`, and approved a figure
#: they had never seen.
RANGE_PRICE_PROPOSAL_CONTENT = {
    "approval_request_id": BAND_APPROVAL,
    "quote_id": BAND_QUOTE,
    "revision": 1,
    "pricebook_version": 1,
    "rendered_hash": BAND_RENDERED,
    "proposed_at": "2026-09-18T03:00:00+00:00",
    "lines": [
        {
            "service_code": "DC_AO_DAI_TRADITIONAL",
            "band_minimum_vnd": AO_DAI_MIN_VND,
            "band_maximum_vnd": AO_DAI_MAX_VND,
            "proposed_amount_vnd": CHOSEN_VND,
        }
    ],
}

BAND_DETAIL = {
    "quote_id": BAND_QUOTE,
    "revision": 1,
    "row_version": 1,
    "finality": "RANGE",
    "status": "REVIEW_REQUIRED",
    "snapshot_hash": BAND_SNAPSHOT,
    "display_total_min_vnd": AO_DAI_MIN_VND,
    "display_total_max_vnd": AO_DAI_MAX_VND,
    "valid_until": "2026-09-19T03:00:00+00:00",
    "reason_codes": ["RANGE_PRICE_REQUIRES_HUMAN"],
    "lines": [
        {
            "line_id": "line-1",
            "service_code": "DC_AO_DAI_TRADITIONAL",
            "quantity": "1",
            "unit": "SET",
            "price_kind": "RANGE",
            "net_amount_vnd": None,
            "band_minimum_vnd": AO_DAI_MIN_VND,
            "band_maximum_vnd": AO_DAI_MAX_VND,
        }
    ],
}

#: The revision `apply_range_prices` writes: an exact price nobody has agreed to yet. `status` is
#: `APPROVED`, not `ACCEPTED_FINAL`, and the difference is the whole of the check at the end of
#: section 10 -- the console used to read `finality === "APPROVED_EXACT"` as "the customer has
#: agreed", which was exact only while nothing else could produce that finality.
CLOSED_REVISION = {
    "quote_id": BAND_QUOTE,
    "revision": 2,
    "row_version": 2,
    "finality": "APPROVED_EXACT",
    "status": "APPROVED",
    "snapshot_hash": "JCS-SHA256-V1:" + "d" * 64,
    "list_service_subtotal_vnd": CHOSEN_VND,
    "net_service_subtotal_vnd": CHOSEN_VND,
    "display_total_min_vnd": CHOSEN_VND,
    "display_total_max_vnd": CHOSEN_VND,
    "reason_codes": ["TAX_TREATMENT_UNVERIFIED", "PROMOTION_OUTSIDE_INTERVAL"],
    "required_approvals": [],
    "replayed": False,
    # `QuotePromotionResponse` for the shop's one confirmed programme, which ended on 31/08/2026.
    # The discount is a real computed zero and not an absence, and the whole of section 10's last
    # check is that the screen says which of the two it is: PROMO-WIRING-001 put this interval on
    # the wire and nothing drew it, so a counter saw 0 d with the reason only in a code list.
    #
    # `interval_end_at_exclusive` is the first instant the programme no longer covers. The console
    # must print that instant rather than subtracting a day to name "the last day", which is why
    # the check below looks for 01/09/2026 and not 31/08/2026.
    "promotion": {
        "policy_code": "PROMO_WET30_DRY40_20260717_20260831",
        "configuration_version": 1,
        "status": "INELIGIBLE",
        "discount_amount_vnd": 0,
        "rate_bps": [],
        "interval_start_at": "2026-07-17T00:00:00+07:00",
        "interval_end_at_exclusive": "2026-09-01T00:00:00+07:00",
        "inside_interval": False,
        "eligibility_resolved": True,
        "reason_codes": ["PROMOTION_OUTSIDE_INTERVAL"],
    },
}


#: `EXPORT-FIX-001`. The export envelope `#/exports` raises, and the read that makes it decidable.
#:
#: `VIEWABLE_RESOURCES` in `screens/approvals.js` had no `EXPORT_REQUEST` entry, so this envelope
#: reached the queue permanently undecidable: a staff member could ask for an export that no owner
#: in the shop was able to release. What section 14 checks is the fix *and* its safe direction --
#: the columns, the day and the exclusions are on the card above the approve control, and every
#: path that cannot show them leaves the control shut.
EXPORT_APPROVAL = "99999999-aaaa-4333-8444-cccccccccccc"
EXPORT_REQUEST_ID = "88888888-9999-4333-8444-dddddddddddd"
#: The digest the envelope binds. One constant for the same reason `BAND_RENDERED` is one: the card
#: must refuse content whose rendering is not the one the press would hand back.
EXPORT_RENDERED = "JCS-SHA256-V1:" + "d" * 64
EXPORT_SNAPSHOT = "JCS-SHA256-V1:" + "e" * 64
#: The business day this export is about, written out so the check below is looking for the day the
#: card was told about rather than for any date at all.
EXPORT_BUSINESS_DATE = "2026-09-16"

#: `ExportRequestContentResponse` from `main.py`, field for field. `incident_open` is deliberately
#: absent from the column list: nothing in this system ever sets `orders.incident_open`, so the
#: column published a constant `false` as a fact inside a document an owner signs.
EXPORT_REQUEST_CONTENT = {
    "approval_request_id": EXPORT_APPROVAL,
    "export_request_id": EXPORT_REQUEST_ID,
    "dataset": "STORE_DAY_ORDERS_V1",
    "business_date": EXPORT_BUSINESS_DATE,
    "business_timezone": "Asia/Ho_Chi_Minh",
    "day_boundary": "orders.created_at",
    "columns": [
        "order_id",
        "created_at",
        "commercial_status",
        "intake_status",
        "production_status",
        "production_accepted_at",
        "production_ready_at",
        "production_released_at",
        "closed_at",
        "expected_total_vnd",
        "paid_amount_vnd",
        "settlement_attested_at",
        "balance_status",
        "refunded_amount_vnd",
        "refunded_at",
    ],
    "excludes": [
        "customer_incident_evidence.summary",
        "assistant_turn_payloads.question",
        "assistant_turn_payloads.answer",
        "orders.bound_contact_id",
    ],
    "query_version": "store-day-orders-export-v2:3f884e227d6a2d05",
    "statement_vi": (
        "Xuất bản sao hồ sơ của chính cửa hàng cho ngày 2026-09-16 (theo giờ Việt Nam): mã đơn, "
        "trạng thái, mốc thời gian và số tiền đã thu của những đơn MỞ trong ngày đó. Ngày được "
        "cắt theo lúc mở đơn, không phải theo lúc thu tiền."
    ),
    "rendered_hash": EXPORT_RENDERED,
    "requested_at": "2026-09-16T03:00:00+00:00",
    "requested_by_you": False,
}


def export_queue_item() -> dict[str, object]:
    """The export envelope as `GET /internal/v1/approvals` returns it, for section 14.

    Built at call time for the reason the two above are: `_OWNER_FINANCIAL` is a ten-minute TTL and
    the card renders the time remaining, so a frozen timestamp would make it read "đã hết hạn".
    """

    return {
        "approval_request_id": EXPORT_APPROVAL,
        "status": "REQUESTED",
        "envelope_hash": "JCS-SHA256-V1:" + "9" * 64,
        "required_role": "OWNER_ADMIN",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "replayed": False,
        "resource_type": "EXPORT_REQUEST",
        "resource_id": EXPORT_REQUEST_ID,
        "resource_version": 1,
        "snapshot_hash": EXPORT_SNAPSHOT,
        "rendered_hash": EXPORT_RENDERED,
        "action": "EXPORT_SANITIZED_DATA",
    }


def range_price_approval() -> dict[str, object]:
    """The raised envelope, as the server answers since `DEC-029`: already attested by the chooser.

    `APPROVED`, `OPERATOR` and thirty minutes -- the `DEC-021` counter attestation -- where it read
    `REQUESTED`, `OWNER_ADMIN` and ten minutes while a range price needed the owner. Built at call
    time rather than as a constant, because the console renders the time remaining and a fixed
    timestamp would make it read "đã hết hạn" the day after this file was written.
    """

    return {
        "approval_request_id": BAND_APPROVAL,
        "status": "APPROVED",
        "envelope_hash": "JCS-SHA256-V1:" + "b" * 64,
        "required_role": "OPERATOR",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "resource_version": 1,
        "snapshot_hash": BAND_SNAPSHOT,
        "rendered_hash": BAND_RENDERED,
        "replayed": False,
    }


def range_price_queue_item() -> dict[str, object]:
    """The same envelope as it appears in `GET /internal/v1/approvals`, for section 12.

    `RANGE-APPROVAL-VISIBILITY-001`. `action` is the field that was not projected until this item:
    four actions share the `QUOTE_REVISION` resource type and only this one asks its approver to
    authorise a number the linked quote screen does not render. Built at call time for the same
    reason the proposal above is — the countdown is ten minutes and a frozen timestamp would make
    the card read "đã hết hạn".
    """

    return {
        "approval_request_id": BAND_APPROVAL,
        "status": "REQUESTED",
        "envelope_hash": "JCS-SHA256-V1:" + "b" * 64,
        "required_role": "OWNER_ADMIN",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "replayed": False,
        "resource_type": "QUOTE_REVISION",
        "resource_id": BAND_QUOTE,
        "resource_version": 1,
        "snapshot_hash": BAND_SNAPSHOT,
        "rendered_hash": BAND_RENDERED,
        "action": "SET_RANGE_PRICE",
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files plus one drip-fed SSE endpoint.

    Playwright's ``route.fulfill`` delivers a body in one shot, which would make the progressive
    rendering check unobservable. The stream URL is therefore exempted from interception (see
    ``route.fallback()`` below) and served here, frame by frame with real pauses — the same shape
    the API's own stream route produces.
    """

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    def do_GET(self) -> None:
        if "/assistant/turns/" in self.path and self.path.endswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for chunk in ASSISTANT_STREAM_CHUNKS:
                frame = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.25)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        super().do_GET()


class Quiet(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


server = Quiet(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

SESSION_OK = {
    "staff_user_id": "00000000-0000-4000-8000-0000000000aa",
    "roles": ["OWNER_ADMIN"],
    "mfa_verified": True,
}

state = {
    "authenticated": True,
    "hold_ticket": False,
    "owner_approved": False,
    # Section 12 only. The queue is empty for every earlier section, because the home screen reads
    # the same endpoint and section 6 asserts an all-clear line that claims only what it checked.
    "approvals_listed": False,
    "proposal_unreadable": False,
    # Section 14 only, and separate from `approvals_listed` so that section 12's card is alone in
    # the queue while it is being checked and this one is alone while it is.
    "export_listed": False,
    "export_unreadable": False,
}
held_ticket_routes: list[Route] = []

with sync_playwright() as playwright:
    # Real Chrome by default, because the staff console is opened in a real browser and the
    # rendering defects this file exists to catch are browser behaviour, not DOM shape.
    #
    # `CONSOLE_BROWSER_CHANNEL=chromium` selects Playwright's bundled build instead, for a container
    # that has one and no Chrome -- which is every container this repository is worked on in. Before
    # this, the script did not run there at all: it raised "Chromium distribution 'chrome' is not
    # found", so the one check that can see a form a human cannot use was the one check nobody
    # could run. An opt-in that names what it selected is better than a silent fallback, and better
    # than a verifier that only works on one machine.
    # `CONSOLE_BROWSER_PATH` takes precedence over both: a container that ships a Chromium build
    # Playwright's own version does not expect answers "Executable doesn't exist" and points at a
    # download this environment blocks. Naming the binary is the documented way out and keeps the
    # launch honest about which browser produced the result.
    channel = os.environ.get("CONSOLE_BROWSER_CHANNEL", "chrome")
    executable = os.environ.get("CONSOLE_BROWSER_PATH", "")
    if executable:
        options: dict[str, object] = {"executable_path": executable}
    elif channel == "chromium":
        options = {}
    else:
        options = {"channel": channel}
    browser = playwright.chromium.launch(headless=True, **options)  # type: ignore[arg-type]
    print(f"browser: {executable or channel}")
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
        elif "/range-price-proposal" in url:
            # The read section 12 exists for. When it refuses, it refuses with a code -- the
            # console has to render that as a refusal and keep the approve control shut, and a
            # console that had to parse a sentence to know so is a console that would not.
            if state.get("proposal_unreadable"):
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "detail": {
                                "outcome": "REQUIRE_HUMAN",
                                "reason_codes": ["RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH"],
                            }
                        }
                    ),
                )
                return
            body = RANGE_PRICE_PROPOSAL_CONTENT
        elif "/export-request" in url:
            # The read section 14 exists for. Its failure direction is the important one: the
            # console must keep the approve control shut and say why, rather than offering a
            # decision about a file whose contents it could not read.
            if state.get("export_unreadable"):
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "detail": {
                                "outcome": "REQUIRE_HUMAN",
                                "reason_code": "EXPORT_REQUEST_CORRUPT",
                            }
                        }
                    ),
                )
                return
            body = EXPORT_REQUEST_CONTENT
        elif url.split("?")[0].endswith("/internal/v1/approvals"):
            if state.get("export_listed"):
                body = [export_queue_item()]
            else:
                body = [range_price_queue_item()] if state.get("approvals_listed") else []
        elif "/assistant/turns/" in url and url.endswith("/stream"):
            # Exempt from interception: the static server drip-feeds this one so the
            # progressive-rendering check below observes genuine mid-stream states.
            route.fallback()
            return
        elif "/assistant/turns" in url:
            if route.request.method == "POST":
                body = {
                    "turn_id": "00000000-0000-4000-8000-0000000000bb",
                    "intent": "TODAY_OVERVIEW",
                    "answer": ASSISTANT_ANSWER,
                    "links": [{"label": "Mở đơn hàng hôm nay", "href": "#/"}],
                    "reason_codes": [],
                    "created_at": "2026-08-15T03:00:00+00:00",
                    "replayed": False,
                }
            else:
                # Empty until a check asks for history. The four-chip empty-transcript assertions
                # earlier in section 4 depend on this being empty, so the purged-turn check below
                # opts in rather than changing the world for everything before it.
                body = state.get("assistant_history") or []
        elif "remedy-options" in url:
            # A pure read. Nothing is written and nothing is reserved by asking, which is what lets
            # the console put the ceiling and the owner requirement on screen before anything is
            # typed rather than after a round trip that commits something.
            body = REMEDY_OPTIONS
        elif "remedy-proposals" in url and url.endswith("/execution"):
            # The owner-approval branch, both halves. Until the envelope is decided the server
            # refuses with a machine-readable reason and no `outcome` key at all -- the shape that
            # used to reach this console as "Du lieu nhap khong hop le".
            if not state.get("owner_approved"):
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "detail": {
                                "reason_code": "REMEDY_APPROVAL_REQUIRED",
                                "authority": "DEC-004",
                            }
                        }
                    ),
                )
                return
            route.fulfill(
                status=201, content_type="application/json", body=json.dumps(REMEDY_EXECUTED)
            )
            return
        elif "remedy-proposals" in url and route.request.method == "POST":
            # Captured rather than merely answered: the property section 11 proves is that the
            # body carries exactly the keys this kind owns and no ceiling of its own. A stub that
            # only returned 201 would certify a console that sends anything at all.
            state.setdefault("remedy_posts", []).append(route.request.post_data)
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(REMEDY_PROPOSAL_OWNER),
            )
            return
        elif "/incidents" in url:
            if route.request.method == "POST":
                # Captured rather than merely answered. The seam this section exists to test is the
                # request body itself: `IncidentOpenRequest` is a `StrictRequest`, so a third key is
                # a 422 against the real server, and the two `sha256:` fields it used to demand are
                # now the server's to derive. A stub that only returned 201 would certify a console
                # that sends anything at all.
                state.setdefault("incident_posts", []).append(route.request.post_data)
                route.fulfill(
                    status=201,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "incident_id": INCIDENTS[0]["incident_id"],
                            "status": "OPEN",
                            "fault_decided": False,
                            "remedy_decided": False,
                            "replayed": False,
                        }
                    ),
                )
                return
            # Empty until section 9 asks for rows. The home screen reads this same endpoint, and
            # section 6 asserts an all-clear line that claims only the queues it checked -- so a
            # stub that always answered with incidents would fail that check for a reason that has
            # nothing to do with it, and the harness would be the defect.
            body = INCIDENTS if state.get("incidents_listed") else []
        elif "/settlements/today" in url:
            body = SETTLEMENTS_TODAY
        elif "/day-summary" in url:
            body = DAY_SUMMARY
        elif "/sla-board" in url:
            # The keyset, exercised rather than described: a request carrying `after_order_id` is
            # the second page, and the second page ends the board. A stub that answered the same
            # rows to both would certify a "Tải thêm" control that appends what is already on
            # screen, which is the defect the paging shape exists to avoid.
            body = SLA_BOARD_SECOND_PAGE if "after_order_id=" in url else SLA_BOARD_FIRST_PAGE
        elif "/pricebook/services" in url:
            # The quote form refuses to build without this, so an empty stub would leave every
            # assertion below looking at a refusal notice rather than a form.
            body = PRICEBOOK_SERVICES
        elif "/counter-tickets" in url and route.request.method == "POST":
            # Held open on request, so a test can observe a button while its write is in flight.
            if state.get("hold_ticket"):
                held_ticket_routes.append(route)
                return
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ticket_id": "66666666-7777-4333-8444-aaaaaaaaaaaa",
                        "ticket_number": 1,
                        "issued_on": "2026-09-09",
                    }
                ),
            )
            return
        elif url.endswith("/orders") and route.request.method == "POST":
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(ORDER_CREATED),
            )
            return
        elif "/order-requests/" in url:
            # The single-fetch endpoint, which the quotes prefill resolves `?request=` against.
            if state.get("request_missing"):
                route.fulfill(
                    status=404,
                    content_type="application/json",
                    body=json.dumps({"detail": "order request not found"}),
                )
                return
            body = ORDER_REQUEST
        elif "/order-requests" in url:
            if route.request.method == "POST":
                route.fulfill(
                    status=201,
                    content_type="application/json",
                    body=json.dumps(ORDER_REQUEST_CREATED),
                )
                return
            body = [ORDER_REQUEST]
        elif "/range-prices" in url:
            # Two routes share this prefix and the suffix is what tells them apart: the bare path
            # raises the envelope, the one carrying an approval id applies it. Both bodies are
            # captured, because the property section 10 exists to prove is that the amounts sent
            # the second time are byte-identical to the amounts the owner signed -- the console
            # holds them, nothing on the server does.
            state.setdefault("range_posts", []).append(route.request.post_data)
            applying = not url.split("?")[0].rstrip("/").endswith("range-prices")
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(CLOSED_REVISION if applying else range_price_approval()),
            )
            return
        elif "/quotes/" in url:
            # `GET /internal/v1/stores/{store}/quotes/{quote}` -- one revision with its lines, the
            # read that makes a band drawable at all. Nothing else returns `band_minimum_vnd`.
            body = BAND_DETAIL
        elif url.split("?")[0].endswith("/quotes") and route.request.method == "POST":
            sent = json.loads(route.request.post_data or "{}")
            state.setdefault("quote_posts", []).append(sent)
            if not sent.get("present_range_as_band"):
                # What the engine really answers for a banded service asked for as a price: it
                # refuses rather than choosing a number in the interval. The console has no way to
                # know in advance -- the catalog route carries no price kind -- so this refusal is
                # the only thing that can tell it, and section 10 checks what it does with it.
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "detail": {
                                "outcome": "REQUIRE_HUMAN",
                                "reason_codes": [
                                    "RANGE_PRICE_REQUIRES_HUMAN",
                                    "TAX_TREATMENT_UNVERIFIED",
                                ],
                            }
                        }
                    ),
                )
                return
            route.fulfill(
                status=201, content_type="application/json", body=json.dumps(BAND_REVISION)
            )
            return
        else:
            body = []
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/internal/**", route_api)
    page.goto(f"http://localhost:{PORT}/#/quotes", wait_until="networkidle")
    page.wait_for_timeout(1200)

    print("=" * 74)
    print("1. THE SERVICE PICKER — chosen by name, never typed as a code")
    print("=" * 74)

    # This section used to drive `#quote-line-0-code` as a text input and assert that
    # `STANDARD_WASH_DRY`, typed one character at a time, survived intact with the caret where the
    # operator left it. That invariant is gone because its subject is gone: the field is a
    # <select> fed by the published pricebook, and an operator no longer types a service code at
    # all. Those assertions are deleted rather than weakened — the honest replacement is to check
    # the control that took its place. The typing invariant itself still has real subjects, and is
    # still asserted below on the quantity field (section 2) and the contact field (section 5).

    code = page.locator("#quote-line-0-code")

    check(
        "the service field is a picker, not a field to type a code into",
        code.evaluate("node => node.tagName") == "SELECT",
        code.evaluate("node => node.tagName"),
    )
    check(
        "it offers the published services by their Vietnamese names",
        code.locator("option", has_text="Giặt sấy dưới 6kg").count() == 1,
        f"{code.locator('option').count()} options",
    )
    check(
        "grouped under Vietnamese category headings, not pricebook tokens",
        code.locator("optgroup[label='Giặt sấy theo ký']").count() == 1
        and code.locator("optgroup[label='standard_weight']").count() == 0,
        repr(code.evaluate("n => [...n.querySelectorAll('optgroup')].map(g => g.label)")),
    )

    code.select_option(label="Giặt sấy dưới 6kg")
    page.wait_for_timeout(200)

    check(
        "choosing by name is what sets the code the server will receive",
        code.input_value() == "STD_WASH_DRY_LT6",
        repr(code.input_value()),
    )
    check(
        "and the unit follows the chosen service, as a stated fact not a second choice",
        "Tính theo: Kilôgam" in page.content() and page.locator("#quote-line-0-unit").count() == 0,
        repr("Tính theo: Kilôgam" in page.content()),
    )

    # The unit belongs to the service, so de-selecting must not leave the previous one standing.
    code.select_option(value="")
    page.wait_for_timeout(200)
    check(
        "going back to no service clears the unit rather than keeping a stale one",
        "Tính theo:" not in page.content(),
        repr(page.locator("#quote-line-0-code").input_value()),
    )
    code.select_option(label="Giặt sấy dưới 6kg")
    page.wait_for_timeout(200)

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

    # The bare-UUID field is a collapsed recovery hatch since INTAKE-UI-001; open it first,
    # exactly as an operator who needs it would.
    page.locator("#quote-manual-toggle").click()
    page.wait_for_timeout(150)
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
        "the service the operator picked is still selected",
        page.locator("#quote-line-0-code").input_value() == "STD_WASH_DRY_LT6",
    )
    check(
        "the quantity is still there",
        page.locator("#quote-line-0-qty").input_value() == "5.9",
    )
    check(
        "the app bar no longer claims the operator is signed in",
        "Chưa đăng nhập" in body,
    )

    print()
    print("=" * 74)
    print("4. TRỢ LÝ AI — the composer must survive the transcript rebuild")
    print("=" * 74)

    # Section 3 ended the session; a fresh boot signs back in against the stub. The hash alone
    # does not reload the page, so the reload is explicit.
    state["authenticated"] = True
    page.goto(f"http://localhost:{PORT}/#/assistant")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)

    composer = page.locator("#assistant-question")
    check(
        "the empty transcript welcomes with four suggestion chips",
        page.locator(".chat__empty .chip").count() == 4,
        f"chips={page.locator('.chat__empty .chip').count()}",
    )
    composer.click()
    page.keyboard.type("Hôm nay thế nào?", delay=12)
    page.wait_for_timeout(200)

    check(
        "the question survives being typed one character at a time",
        composer.input_value() == "Hôm nay thế nào?",
        repr(composer.input_value()),
    )
    check(
        "focus is still in the composer after typing",
        page.evaluate("document.activeElement?.id") == "assistant-question",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    page.locator("button[type=submit]").first.click()

    def last_answer_text() -> str:
        return page.evaluate(
            "(() => { const nodes = document.querySelectorAll('.chat__answer');"
            " return nodes.length ? nodes[nodes.length - 1].textContent : ''; })()"
        )

    # The stubbed stream drip-feeds four frames 250ms apart; poll far faster than that so the
    # intermediate lengths are genuinely observed rather than assumed.
    samples: list[str] = []
    saw_caret = False
    focus_during_stream: str | None = None
    deadline = time.time() + 8
    while time.time() < deadline:
        text = last_answer_text()
        if not samples or text != samples[-1]:
            samples.append(text)
        if page.locator(".chat__caret").count():
            saw_caret = True
        if 0 < len(text) < len(ASSISTANT_ANSWER) and focus_during_stream is None:
            focus_during_stream = page.evaluate("document.activeElement?.id")
        if text == ASSISTANT_ANSWER and not page.locator(".chat__caret").count():
            break
        page.wait_for_timeout(80)

    prefixes = [sample for sample in samples if 0 < len(sample) < len(ASSISTANT_ANSWER)]
    check(
        "the answer streamed progressively, not in one blob",
        len(prefixes) >= 2 and all(len(a) < len(b) for a, b in itertools.pairwise(prefixes)),
        "lengths: " + " > ".join(str(len(sample)) for sample in samples),
    )
    check("a blinking caret trailed the answer while it streamed", saw_caret)
    check(
        "the composer kept focus while the answer streamed",
        focus_during_stream == "assistant-question",
        f"activeElement={focus_during_stream}",
    )
    check(
        "the final streamed text is the whole stored answer",
        last_answer_text() == ASSISTANT_ANSWER,
        repr(last_answer_text()),
    )
    check(
        "the caret is gone once the stream ends",
        page.locator(".chat__caret").count() == 0,
    )

    transcript = page.content()
    check(
        "the welcome gave way to the conversation once a turn was recorded",
        page.locator(".chat__empty").count() == 0,
    )
    check(
        "the question landed in the transcript",
        "Hôm nay thế nào?" in transcript and "Bạn hỏi" in transcript,
    )
    check(
        "the assistant's answer landed with its intent token",
        "Tổng cộng" in transcript and "TODAY_OVERVIEW" in transcript,
    )
    check(
        "the composer was cleared for the next question",
        composer.input_value() == "",
        repr(composer.input_value()),
    )
    check(
        "and focus returned to the composer after the rebuild",
        page.evaluate("document.activeElement?.id") == "assistant-question",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    # The transcript rebuild must not have replaced the composer node: typing again lands in it.
    page.keyboard.type("Còn gì chờ duyệt?", delay=12)
    page.wait_for_timeout(150)
    check(
        "typing after the transcript rebuild still lands in the composer",
        composer.input_value() == "Còn gì chờ duyệt?",
        repr(composer.input_value()),
    )

    # `ASSISTANT-RETENTION-001`. `DEC-008` disposes of a transcript's words at 180 days and keeps
    # the turn -- its intent, reason codes, actor and timestamp are ledger. Before this the console
    # rendered `item.answer || ""`, so a disposed turn appeared as an empty bubble: a silent gap,
    # which this project treats as worse than an error. `#/incidents` already has this check in
    # section 9 and the assistant had no sibling, which is the asymmetry being closed.
    state["assistant_history"] = [
        {
            "turn_id": "00000000-0000-4000-8000-0000000000cc",
            "question": None,
            "intent": "TODAY_OVERVIEW",
            "answer": None,
            "links": [],
            "reason_codes": ["NO_ORDERS_TODAY"],
            "created_at": "2026-03-01T03:00:00+00:00",
        }
    ]
    # `reload`, not `goto`: section 4 is already on `#/assistant`, and navigating to a URL that
    # differs only in its hash does not re-fetch anything -- the stub would never be asked and every
    # assertion below would pass against the screen as it already was.
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(900)

    transcript = page.inner_text("body").replace("\n", " ")
    check(
        "a transcript whose words were disposed of says so instead of rendering an empty bubble",
        "Không còn giữ nguyên văn" in transcript and "không phải mất dữ liệu" in transcript,
        transcript[:160],
    )
    check(
        "and the turn itself is still on the books",
        "TODAY_OVERVIEW" in transcript or "NO_ORDERS_TODAY" in transcript,
        "expected the intent or reason code to survive the disposal",
    )
    empty_bubbles = page.evaluate(
        "() => Array.from(document.querySelectorAll('.chat__user, .chat__answer'))"
        ".filter((node) => !node.innerText.trim()).length"
    )
    check(
        "no bubble is left empty by a disposed turn",
        empty_bubbles == 0,
        f"{empty_bubbles} empty",
    )
    state["assistant_history"] = []

    print()
    print("=" * 74)
    print("5. TIẾP NHẬN — real keystrokes, then Báo giá ngay with no typing at all")
    print("=" * 74)

    page.goto(f"http://localhost:{PORT}/#/order-requests")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)

    contact_input = page.locator("#intake-contact")
    contact_input.click()
    page.keyboard.type(CONTACT, delay=6)
    page.wait_for_timeout(200)

    check(
        "the contact binding survives being typed one character at a time",
        contact_input.input_value() == CONTACT,
        repr(contact_input.input_value()),
    )
    check(
        "focus is still in the contact field after 36 keystrokes",
        page.evaluate("document.activeElement?.id") == "intake-contact",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    page.locator("button[type=submit]").first.click()
    page.wait_for_timeout(1200)

    check(
        "the result card announces the recorded intake",
        "Đã tiếp nhận" in page.content(),
    )
    quote_now = page.locator("#intake-quote-now")
    check(
        "and offers the Báo giá ngay link carrying the new request id",
        quote_now.count() == 1
        and ORDER_REQUEST["order_request_id"] in (quote_now.first.get_attribute("href") or ""),
        quote_now.first.get_attribute("href") if quote_now.count() else "absent",
    )
    check(
        "the recent-intake list shows the draft's state in Vietnamese, with the token in reach",
        "Nháp" in page.content() and page.locator("[title='DRAFT']").count() >= 1,
        repr(page.locator("[title='DRAFT']").count()),
    )

    # The whole point of the slice: follow the link and the quote screen binds the request
    # itself — resolved from the server, without one typed character.
    quote_now.first.click()
    page.wait_for_timeout(1500)

    summary = page.locator("#quote-request-summary")
    check(
        "the quotes screen prefilled from ?request= with no typing",
        summary.count() == 1 and "Đang báo giá cho yêu cầu" in summary.first.text_content(),
        summary.first.text_content() if summary.count() else "no summary rendered",
    )
    check(
        "the prefilled summary names the same request the intake created",
        summary.count() == 1 and "33333333…7777" in summary.first.text_content(),
    )
    check(
        "the picker offers the same request as a one-tap choice",
        page.locator("button", has_text="Dùng yêu cầu này").count() >= 1,
    )

    # The honest not-found path: a request id the store does not have must say so, not pretend.
    state["request_missing"] = True
    page.evaluate(f"location.hash = '#/quotes?request={MISSING_REQUEST}'")
    page.wait_for_timeout(1500)
    missing_summary = page.locator("#quote-request-summary")
    check(
        "an unknown ?request= gets the honest not-found notice, nothing prefilled",
        missing_summary.count() == 1
        and "Không tìm thấy yêu cầu này" in missing_summary.first.text_content(),
        missing_summary.first.text_content() if missing_summary.count() else "no notice rendered",
    )
    state["request_missing"] = False

    print()
    print("=" * 74)
    print("6. HÔM NAY — the owner's morning, and what it refuses to claim")
    print("=" * 74)

    # Every queue stub returns `[]`, so this is the empty-morning case: the one an owner sees most
    # often and the one the old screen answered with five zero-cards and a wall of footnotes.
    page.goto(f"http://localhost:{PORT}/#/", wait_until="networkidle")
    page.wait_for_timeout(1200)
    body = page.content()

    check(
        "the day's takings lead the screen, formatted as VND and not recomputed",
        "1.285.000" in body.replace("&nbsp;", " ") or "1.285.000 ₫" in body,
        repr(page.locator(".takings__amount").text_content()),
    )
    check(
        "the figure says how many settlements it is a sum of",
        "7 đơn đã tất toán hôm nay" in body,
    )
    check(
        "and names itself as money collected, never as doanh thu",
        "Đã thu tại quầy" in body and "đây không phải doanh thu" in body,
    )
    # Asked as "can the operator see it", not "does it carry the class". The first version of this
    # check counted `.tile--clear` elements and passed while all four tiles were still on screen:
    # the class was applied, and a `display: grid` declared later in the same stylesheet outranked
    # the `display: none`. A class is an intention; visibility is the claim.
    empty_tiles = page.locator("article.tile--clear")
    check(
        "an empty queue takes no space instead of showing a zero card",
        empty_tiles.count() >= 1
        and all(not empty_tiles.nth(i).is_visible() for i in range(empty_tiles.count())),
        f"{empty_tiles.count()} marked empty, "
        f"{sum(empty_tiles.nth(i).is_visible() for i in range(empty_tiles.count()))} still visible",
    )
    check(
        "the all-clear line claims only what was checked, never that nothing is pending",
        "Các hàng đợi đã kiểm đều đang trống." in body
        and "Không có việc nào đang chờ bạn xử lý" not in body,
    )
    # `page.content()` serialises hidden nodes too, so this must ask what the operator can see.
    # The scope note stays in the DOM and is revealed only for a session with more than one store —
    # the reader it can matter to — rather than being deleted for everyone.
    scope_note = page.locator("p.hint", has_text="Mọi cửa hàng bạn được gán.")
    check(
        "a single-store session is not told which reads ignore the store picker",
        scope_note.count() >= 1 and not scope_note.first.is_visible(),
        f"{scope_note.count()} in DOM, hidden from a one-store session",
    )

    print()
    print("=" * 74)
    print("7. ĐƠN HÀNG — the acquisition source, and the pressure it must not apply")
    print("=" * 74)

    # ACQUISITION-ATTRIBUTION-001. The claim is not "a select exists". It is that a counter which
    # did not ask can leave the field alone and be recorded as not knowing, without the screen
    # pushing back — because a required field with no comfortable honest option gets filled with
    # whatever clears the form, and the resulting channel report is worse than no report. That is a
    # claim about styling and default state, which no source-text test can make.
    page.goto(f"http://localhost:{PORT}/#/orders", wait_until="networkidle")
    page.wait_for_timeout(1200)

    source = page.locator("#order-source")
    check(
        "the order form asks where the customer came from",
        source.count() == 1,
        f"{source.count()} controls with that id",
    )
    check(
        "and it rests on 'nobody asked' rather than on a plausible channel",
        source.count() == 1 and source.input_value() == "UNKNOWN",
        repr(source.input_value()) if source.count() else "absent",
    )
    check(
        "the resting option reads as an answer, in Vietnamese",
        "Chưa biết" in page.content(),
    )
    check(
        "the send-reconciliation gloss did not leak into it",
        "chưa rõ kết quả" not in (source.text_content() or "").lower(),
        repr(source.text_content()) if source.count() else "absent",
    )
    check(
        "every source the enum offers is reachable in one interaction",
        source.locator("option").count() == 9,
        f"{source.locator('option').count()} options",
    )
    # The pressure test: no warning colour, no aria-invalid, nothing that reads as disapproval
    # while the honest answer is selected.
    check(
        "leaving it unanswered raises no warning state",
        source.get_attribute("aria-invalid") is None
        and "warn" not in (source.get_attribute("class") or "")
        and "danger" not in (source.get_attribute("class") or ""),
        f"aria-invalid={source.get_attribute('aria-invalid')} "
        f"class={source.get_attribute('class')}",
    )
    check(
        "the field warns that the entry is final, since no screen can show it back",
        "không sửa được" in page.content(),
    )
    # And the label points at the select, which is the CONSOLE-LABEL-001 defect one screen over.
    source.locator("xpath=../label").first.click()
    page.wait_for_timeout(150)
    check(
        "tapping its label focuses the field",
        page.evaluate("document.activeElement?.id") == "order-source",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    # The assertion that matters most, and the one the first version of this section did not make:
    # what the *next* customer's form says. A sticky select would have the console itself supply a
    # plausible answer nobody gave, on a field that is immutable and that no screen reads back --
    # which is precisely the failure `UNKNOWN` exists to prevent, committed by the software rather
    # than by a hurried operator. Checking only the initial default cannot see it.
    source.select_option("GOOGLE_MAPS")
    page.locator("#order-contact").fill(CONTACT)
    page.locator("#order-quote").fill("55555555-6666-4333-8444-999999999999")
    page.locator("#order-hash").fill(f"JCS-SHA256-V1:{'a' * 64}")
    page.locator("#order-accepted").fill("2026-09-08T10:00")
    page.wait_for_timeout(150)
    # Scoped by the field itself: the create form is not the first `form.form` on this screen --
    # the transition form is, and clicking that one submits a different command entirely.
    create_form = page.locator("form.form").filter(has=page.locator("#order-source"))
    create_form.locator("button[type=submit]").first.click()
    page.wait_for_timeout(1200)

    check(
        "the order was accepted, so this is the real post-submit form",
        "Đã tạo đơn" in page.content(),
    )
    check(
        "the next customer's form is back on 'nobody asked', not on the last answer",
        page.locator("#order-source").input_value() == "UNKNOWN",
        repr(page.locator("#order-source").input_value()),
    )

    print()
    print("=" * 74)
    print("8. BA NGUỒN VÔ HIỆU HOÁ — coming back online must undo only its own doing")
    print("=" * 74)

    # Three separate things disable a control here: a permission refusal from `gated()`, an
    # in-flight write, and being offline. `syncNetworkAffordance` re-applies after every render and
    # used to own the `disabled` attribute outright, so `online` cleared whatever anyone else had
    # set. That produced two defects a week apart: a read-only AUDITOR offered a live "Tạo đơn",
    # and a button re-armed mid-write for a second submit of a command already in flight. Neither
    # is visible without driving the events, which is why this section exists.
    page.goto(f"http://localhost:{PORT}/#/order-requests", wait_until="networkidle")
    page.wait_for_timeout(1000)

    ticket_button = page.locator("button", has_text="Phát phiếu")
    check(
        "the walk-in ticket button is declared network-dependent",
        ticket_button.count() == 1
        and ticket_button.first.get_attribute("data-requires-network") == "true",
        ticket_button.first.get_attribute("data-requires-network")
        if ticket_button.count()
        else "absent",
    )

    # `context.set_offline` is what actually flips `navigator.onLine`; dispatching the event alone
    # leaves it true, and `syncNetworkAffordance` reads the property rather than the event.
    context.set_offline(True)
    page.evaluate("() => window.dispatchEvent(new Event('offline'))")
    page.wait_for_timeout(300)
    offline_disabled = ticket_button.first.is_disabled()
    context.set_offline(False)
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(300)
    check(
        "it goes dead when the connection drops and comes back when it returns",
        offline_disabled and not ticket_button.first.is_disabled(),
        f"offline={offline_disabled} online={not ticket_button.first.is_disabled()}",
    )

    # Hold its request open so the button is genuinely in flight, then fire `online` underneath it.
    state["hold_ticket"] = True
    ticket_button.first.click()
    page.wait_for_timeout(400)
    in_flight = ticket_button.first.is_disabled()
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(400)
    check(
        "a button disabled by its own in-flight write is not re-armed by an `online` event",
        in_flight and ticket_button.first.is_disabled(),
        f"in_flight={in_flight} still_disabled={ticket_button.first.is_disabled()}",
    )
    state["hold_ticket"] = False
    for held_route in held_ticket_routes:
        held_route.fulfill(
            status=201,
            content_type="application/json",
            body=json.dumps(
                {
                    "ticket_id": "66666666-7777-4333-8444-aaaaaaaaaaaa",
                    "ticket_number": 1,
                    "issued_on": "2026-09-09",
                }
            ),
        )
    held_ticket_routes.clear()
    page.wait_for_timeout(200)

    print()
    print("=" * 74)
    print("9. OPENING AN INCIDENT — the counter path DEC-028 made completable")
    print("=" * 74)

    # Until DEC-028 this screen could not be completed by anybody: the request demanded
    # `contact_scope_hash` and `evidence_summary_hash` and nothing in the repository produced
    # either. Nothing in this file covered it, so the form that could not be submitted was also the
    # form nothing drove. Both halves changed together.
    state["incidents_listed"] = True
    page.goto(f"http://localhost:{PORT}/#/incidents", wait_until="networkidle")
    page.wait_for_timeout(700)

    body_text = page.inner_text("body")
    check(
        "the screen no longer says the form cannot be completed",
        "chưa dùng được" not in body_text and "sha256" not in body_text,
        body_text[:160].replace("\n", " "),
    )
    check(
        "an incident whose description was purged reads as retention, not as a gap",
        "không phải mất dữ liệu" in body_text.replace("\n", " "),
        "expected the muted absent-value sentence on the CLOSED row",
    )

    summary_box = page.locator("form textarea").first
    order_box = page.locator("input[type=text]").first
    check("the complaint is typed into a textarea, not a hash field", summary_box.count() > 0)

    order_box.fill(INCIDENTS[0]["order_id"])
    # Typed, not `fill()`. This whole file exists because a `fill()`-based check certified a screen
    # that rebuilt its form on every keystroke and was unusable by a human.
    summary_box.click()
    summary_box.type("Áo sơ mi trắng bị ố vàng ở cổ.", delay=12)
    typed = summary_box.input_value()
    check(
        "the complaint survives being typed one character at a time",
        typed == "Áo sơ mi trắng bị ố vàng ở cổ.",
        f"got {typed!r}",
    )

    page.locator("form button[type=submit]").last.click()
    page.wait_for_timeout(600)

    posts = state.get("incident_posts") or []
    sent = json.loads(posts[-1]) if posts else {}
    check("the form actually submits", bool(posts), f"{len(posts)} POST(s)")
    check(
        "it sends exactly order_id and evidence_summary, and no hash",
        set(sent) == {"order_id", "evidence_summary"},
        f"keys={sorted(sent)}",
    )
    check(
        "the complaint reaches the server as the words the staff member typed",
        sent.get("evidence_summary") == "Áo sơ mi trắng bị ố vàng ở cổ.",
        f"got {sent.get('evidence_summary')!r}",
    )

    print()
    print("=" * 74)
    print("10. KHOẢNG GIÁ — the counter closes a band, and is warned before it sends")
    print("=" * 74)

    # `RANGE-PRICE-001`. Twenty of the forty-four published services are priced by inspection, and
    # until this item none of them could be quoted at all. The whole path is here because every
    # step of it is a place the console could quietly decide money: choosing a number inside the
    # band, showing the top of an interval as a price, or treating an owner's approval as the
    # customer's agreement. Each is checked below as a refusal rather than as a feature.

    page.evaluate(f"location.hash = '#/quotes?request={ORDER_REQUEST['order_request_id']}'")
    page.wait_for_timeout(1500)

    band_code = page.locator("#quote-line-0-code")
    band_code.select_option(label="Áo dài truyền thống")
    page.wait_for_timeout(200)
    band_qty = page.locator("#quote-line-0-qty")
    band_qty.click()
    band_qty.type("1", delay=12)
    page.wait_for_timeout(150)

    page.locator("form button[type=submit]", has_text="Tính giá").first.click()
    page.wait_for_timeout(900)

    content = page.content()
    check(
        "a banded service is refused rather than priced at a number nobody chose",
        "Món này niêm yết theo khoảng giá" in content,
    )
    check(
        "the refusal keeps its code and gains a Vietnamese reason",
        "RANGE_PRICE_REQUIRES_HUMAN" in content and "nhân viên phải chọn giá chính xác" in content,
    )
    first_post = (state.get("quote_posts") or [{}])[-1]
    check(
        "and the first attempt did not ask for a band on the console's own initiative",
        first_post.get("present_range_as_band") is None,
        f"sent {first_post.get('present_range_as_band')!r}",
    )

    page.locator("button", has_text="Lập bản khoảng giá").first.click()
    page.wait_for_timeout(1400)

    banded_post = (state.get("quote_posts") or [{}])[-1]
    check(
        "asking for a band is a deliberate press, and it is what sets the flag",
        banded_post.get("present_range_as_band") is True,
        f"sent {banded_post.get('present_range_as_band')!r}",
    )

    content = page.content()
    check(
        "the stored band is shown whole, both ends, under the KHOẢNG GIÁ badge",
        "80.000" in content and "240.000" in content and "KHOẢNG GIÁ" in content,
    )
    check(
        "the two scalar subtotals are withheld on a band instead of printing its maximum",
        "Vì sao hai dòng trên" in content,
        "expected the console to refuse to print net_service_subtotal_vnd on a RANGE revision",
    )
    check(
        "a band cannot be accepted, because there is no single number to agree to",
        "Bản này là một khoảng giá, chưa phải một số" in content
        and page.locator("button", has_text="Khách đã chốt giá").count() == 0,
    )

    band_field = page.locator("#quote-band-0")
    check("the band gets a money box of its own", band_field.count() == 1)
    check(
        "with no placeholder, because any example inside the band is a suggestion",
        band_field.get_attribute("placeholder") in (None, ""),
        repr(band_field.get_attribute("placeholder")),
    )

    # The `pricingCliffNotice` property, applied to money: the warning has to arrive while the
    # number is being typed, not after a round trip refuses it in front of a customer.
    band_field.click()
    page.keyboard.type("250000", delay=12)
    page.wait_for_timeout(300)
    check(
        "an amount above the band is caught in the browser, before anything is sent",
        "Số này cao hơn khoảng giá đã niêm yết" in page.content(),
    )
    check(
        "the warning names the band and the code the server would answer with",
        "RANGE_PRICE_OUT_OF_BAND" in page.content() and "80.000" in page.content(),
    )
    check(
        "and nothing was sent while it was being typed",
        not state.get("range_posts"),
        f"{len(state.get('range_posts') or [])} request(s)",
    )
    check(
        "focus stayed in the money box while the warning appeared",
        page.evaluate("document.activeElement?.id") == "quote-band-0",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    for _ in range(6):
        page.keyboard.press("Backspace")
    page.keyboard.type("150000", delay=12)
    page.wait_for_timeout(300)
    check(
        "an amount inside the band is accepted, and reads as a proposal rather than a price",
        "Số này cao hơn khoảng giá đã niêm yết" not in page.content()
        and "Giá bạn chọn cho dòng này" in page.content(),
    )
    check(
        "the typed amount survives being typed one character at a time",
        band_field.input_value() == "150000",
        repr(band_field.input_value()),
    )

    # `DEC-029`: one press. The server answers with the chooser's own attestation and the console
    # writes the price with it straight away -- two requests, no owner step in between.
    page.locator("button", has_text="Chốt giá này").first.click()
    page.wait_for_timeout(1500)

    posts = state.get("range_posts") or []
    check(
        "one press sends the proposal and then the price, with nobody else involved",
        len(posts) == 2,
        f"{len(posts)} request(s)",
    )
    proposed = json.loads((posts[0] if posts else "{}") or "{}")
    check("the proposal actually reaches the server", bool(state.get("range_posts")))
    check(
        "it carries the amount the staff member chose and no band of its own",
        proposed.get("choices")
        == [{"service_code": "DC_AO_DAI_TRADITIONAL", "amount_vnd": CHOSEN_VND}],
        repr(proposed.get("choices")),
    )
    check(
        "bound to the exact revision and digest the customer was read",
        proposed.get("expected_current_revision") == 1
        and proposed.get("expected_snapshot_hash") == BAND_SNAPSHOT,
        repr(proposed.get("expected_snapshot_hash")),
    )

    content = page.content()
    check(
        "the screen never tells the counter to wait for the owner",
        "Đang chờ chủ tiệm duyệt" not in content,
    )

    applied = json.loads((posts[-1] if posts else "{}") or "{}")
    check(
        "applying re-sends exactly the content that was attested, because the digest binds it",
        applied == proposed,
        "the applied body differs from the proposed one",
    )

    check(
        "the closed band becomes one exact price",
        "Đã ghi giá vào bản sửa đổi 2" in content and "150.000" in content,
    )
    check(
        "choosing the price is not the customer's agreement: the quote still has to be accepted",
        page.locator("button", has_text="Khách đã chốt giá").count() == 1,
        "expected the acceptance control on an APPROVED_EXACT revision that is not ACCEPTED_FINAL",
    )
    check(
        "and it is not yet offered as an order",
        page.locator("a", has_text="Tạo đơn từ báo giá này").count() == 0,
    )

    # `PROMO-WIRING-001` §5.3: today's honest answer is 0 d, and the screen has to say *which* zero
    # it is. The API has carried the programme's interval since that item landed and nothing drew
    # it, so the counter saw an unexplained zero -- the same failure as rendering a null total as 0.
    check(
        "the expired programme is named on the result, not just its reason code",
        "PROMO_WET30_DRY40_20260717_20260831" in content,
    )
    check(
        "with the end of its window on screen, so the zero has a date behind it",
        "01/09/2026" in content,
        "expected the programme's exclusive end bound rendered on the quote result",
    )
    check(
        "and the bound is stated as exclusive rather than quietly turned into 'the last day'",
        "Mốc sau là mốc kết thúc không bao gồm" in content,
    )

    print()
    print("=" * 74)
    print("11. BỒI HOÀN — the ceiling and the owner, before a single digit is typed")
    print("=" * 74)

    # `REMEDY-001`'s whole claim is an ordering claim, and ordering is exactly what a source-text
    # test cannot see: that the computed ceiling, the window and the owner requirement are on
    # screen *before* the money box, not beside it and not after the refusal. A staff member who
    # learns after filling the form in that the owner has to approve it has already told a customer
    # something the shop cannot do, with that customer standing in front of them.

    incident = str(INCIDENTS[0]["incident_id"])
    page.evaluate(f"location.hash = '#/remedies?incident={incident}'")
    page.wait_for_timeout(900)

    content = page.content()
    check(
        "the deep link from an incident loads that incident's figures unasked",
        page.locator("#remedy-incident").input_value() == incident,
        repr(page.locator("#remedy-incident").input_value()),
    )
    check(
        "the staff approval ceiling is stated as a figure, not as a rule to remember",
        "100.000" in content,
    )
    check(
        "a rewash says it has no ceiling rather than showing 0 ₫",
        "Không có trần" in content and "0 ₫" not in content,
    )
    check(
        "and its window is shown with whether it is still open",
        "còn hạn" in content,
    )

    # Damage: the one kind where a person chooses the figure, and the only one with a money box.
    kind = page.locator("#remedy-kind")
    check(
        "there is no money box until the kind that needs one is chosen",
        page.locator("#remedy-amount").count() == 0,
    )

    kind.select_option(value="DAMAGE_COMPENSATION")
    page.wait_for_timeout(300)
    check(
        "choosing damage asks which priced line was damaged before it asks for money",
        page.locator("#remedy-line").count() == 1 and page.locator("#remedy-amount").count() == 0,
        f"line={page.locator('#remedy-line').count()} "
        f"amount={page.locator('#remedy-amount').count()}",
    )
    check(
        "and it says why: the 5x cap belongs to one line, not to the order",
        "Chưa chọn món bị hỏng" in page.content(),
    )

    page.locator("#remedy-line").select_option(value="line-large")
    page.wait_for_timeout(300)
    content = page.content()
    check(
        "the server-computed ceiling for that line is on screen with the box still empty",
        "600.000" in content and page.locator("#remedy-amount").input_value() == "",
        repr(page.locator("#remedy-amount").input_value()),
    )
    check(
        "the box offers no example amount, because every example is a number nobody chose",
        (page.locator("#remedy-amount").get_attribute("placeholder") or "") == "",
        repr(page.locator("#remedy-amount").get_attribute("placeholder")),
    )
    check(
        "the form warns that an amount on this line may need the owner — before any amount exists",
        "Tuỳ số tiền" in content,
    )
    check(
        "the defect window and its state are shown beside the ceiling, not discovered later",
        "Cửa sổ thời gian" in content and "Khách nhận đồ lúc" in content,
    )

    # The boundary `DEC-004` sets, typed rather than filled, at the one đồng that decides it.
    amount = page.locator("#remedy-amount")
    amount.click()
    page.keyboard.type("100000", delay=12)
    page.wait_for_timeout(300)
    check(
        "100.000 d is inside what staff may approve, and the owner is not mentioned",
        "sẽ lập phiếu chờ chủ tiệm duyệt" not in page.content(),
    )
    # One đồng over, and one đồng only. Typing a further "1" onto "100000" appends rather than
    # increments and gives 1.000.001 ₫ -- above this line's 600.000 ₫ cap as well as above the
    # staff ceiling, so the screen would answer about the cap and the boundary `DEC-004` actually
    # sets would never be exercised. Backspace first, then retype the last digit: 100.000 -> 10.000
    # -> 100.001.
    page.keyboard.press("Backspace")
    page.keyboard.type("1", delay=12)
    page.wait_for_timeout(300)
    check(
        "100.001 ₫ crosses it, and the warning arrives while typing rather than after sending",
        "sẽ lập phiếu chờ chủ tiệm duyệt" in page.content(),
    )
    check(
        "the typed amount survives being typed one character at a time",
        amount.input_value() == "100001",
        repr(amount.input_value()),
    )
    # And it arrives before the attestation box is ticked. That box is the last thing a staff
    # member touches, so a plan that stopped at it -- as this one did -- never read the amount, and
    # the owner warning appeared only after the form was complete: exactly what the packet forbids.
    check(
        "the owner warning does not wait for the fault box to be ticked",
        page.locator("#remedy-fault").is_checked() is False,
    )
    check(
        "and focus stayed in the money box while the warning appeared",
        page.evaluate("document.activeElement?.id") == "remedy-amount",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )

    # Above the line's own cap: refused here with the cap named, never reduced to it.
    for _ in range(6):
        page.keyboard.press("Backspace")
    page.keyboard.type("600001", delay=8)
    page.wait_for_timeout(300)
    content = page.content()
    check(
        "an amount over the line's cap is refused with the cap named",
        # The whole sentence, not the two words separately: "Vượt trần" is also in the money box's
        # own hint and "600.000" is in the line picker, so testing for each on its own passed
        # before the refusal was rendered at all. Reworded with the founder's per-item ruling:
        # a single claim is capped at one item, and this line is one item.
        "Vượt trần một món: mỗi đề nghị tối đa 600.000" in content,
    )
    check(
        "and the box still holds what was typed — nothing was silently reduced to the cap",
        amount.input_value() == "600001",
        repr(amount.input_value()),
    )

    for _ in range(6):
        page.keyboard.press("Backspace")
    page.keyboard.type("150000", delay=8)
    page.locator("#remedy-fault").check()
    page.wait_for_timeout(300)

    page.locator("button", has_text="Gửi đề nghị bồi hoàn").first.click()
    page.wait_for_timeout(900)

    proposed = json.loads((state.get("remedy_posts") or ["{}"])[-1] or "{}")
    check("the proposal actually reaches the server", bool(state.get("remedy_posts")))
    check(
        "it carries exactly the keys this kind owns, and no ceiling of its own",
        proposed
        == {
            "kind": "DAMAGE_COMPENSATION",
            "store_fault_attested": True,
            "order_line_id": "line-large",
            "amount_vnd": 150_000,
        },
        repr(proposed),
    )

    content = page.content()
    check(
        "the screen then says a second person has to decide it",
        "Chờ chủ tiệm duyệt" in content,
    )
    check(
        "and links the queue where that happens rather than leaving staff to find it",
        page.locator("a[href='#/approvals']").count() >= 1,
    )

    # Executing before the owner has decided. The refusal is a policy answer with a code, and the
    # sentence it must NOT produce is the one that tells a staff member their typing was wrong.
    page.locator("button", has_text="Thực hiện bồi hoàn").first.click()
    page.wait_for_timeout(900)
    content = page.content()
    check(
        "executing early is refused as a refusal, not as bad input",
        "REMEDY_APPROVAL_REQUIRED" in content and "Dữ liệu nhập không hợp lệ" not in content,
    )
    check(
        "with the refusal glossed in Vietnamese beside its code",
        "phải có chủ tiệm duyệt trước khi thực hiện" in content,
    )

    state["owner_approved"] = True
    page.locator("button", has_text="Thực hiện bồi hoàn").first.click()
    page.wait_for_timeout(900)
    content = page.content()
    check(
        "once the owner has decided, the same press carries the remedy out",
        "Đã thực hiện" in content and "CREDIT_EXECUTED" in content,
    )
    check(
        "the credit id is shown in full with a copy control: nothing can look it up",
        REMEDY_CREDIT_ID in content and "Chép mã giảm trừ này lại ngay" in content,
    )

    # Loss, since `DEC-031`. Until 2026-09-25 this section asserted that loss rendered as an
    # unsupported capability with no figure at all. The ruling gave loss the damage ceiling and one
    # rule -- every loss claim needs the owner, whatever the amount -- so what is asserted now is
    # that the form never lets a loss read as something staff can approve.
    kind.select_option(value="LOST_ITEM")
    page.wait_for_timeout(300)
    content = page.content()
    check(
        "loss says it waits for the owner before a line or an amount is chosen",
        "Mất đồ — luôn chờ chủ tiệm duyệt" in content
        and page.locator("#remedy-amount").count() == 0,
    )
    check(
        "the picker names the garment by its service, not by a bare line identifier",
        "Áo, quần thun" in content and ">line-small" not in content,
    )
    page.locator("#remedy-line").select_option(value="line-small")
    page.wait_for_timeout(300)
    amount = page.locator("#remedy-amount")
    amount.click()
    page.keyboard.type("1000", delay=8)
    page.wait_for_timeout(300)
    content = page.content()
    check(
        "a 1.000 d loss, far under the staff limit, still says the owner must approve it",
        "Có — phải có chủ tiệm duyệt" in content and "mất đồ luôn do chủ tiệm duyệt" in content,
    )
    check(
        "and its ceiling is the damage ceiling of that one item",
        "90.000" in content,
    )

    print()
    print("=" * 74)
    print("12. DUYỆT — the number is on screen before the button that approves it")
    print("=" * 74)

    # `RANGE-APPROVAL-VISIBILITY-001`, and the reason it needs a browser rather than a source test.
    #
    # The defect was an ORDERING and REACHABILITY defect, not a missing string. The queue returned
    # a digest, the card linked to `#/quotes`, and the quote screen renders the published BAND --
    # 80.000 d - 240.000 d -- because the revision the envelope binds is the one before any price
    # was chosen. Every sentence on that screen was true. The owner still could not see the number
    # they were authorising, so a staff member who agreed 150.000 d with the customer could propose
    # 240.000 d and the only second-party control over that figure passed it through.
    #
    # A source test can prove the sentence exists. Only this can prove the amount is in the
    # document *above* the approve control, that the control is pressable when it is, and that it
    # is not when the amount cannot be read. The last one is asserted twice: once on the button's
    # own disabled state, and once on the absence of the figure -- a screen that showed 150.000 d
    # and disabled the button would pass a check that only looked at the button.

    def rendered_text() -> str:
        """The page's text, not its serialized HTML.

        `page.content()` is what every other section reads, and it cannot answer this one:
        Chromium serializes the NO-BREAK SPACE that `Intl.NumberFormat` puts before ₫ as the
        entity `&nbsp;`, so "150.000\u00a0\u20ab" is absent from the HTML whether the amount is on
        screen or not. Reading `textContent` is the difference between checking the money and
        checking nothing.
        """

        return str(page.evaluate("() => document.body.textContent"))

    state["approvals_listed"] = True
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()
    text = rendered_text()

    check(
        "the queue names the action, not only the resource type",
        "SET_RANGE_PRICE" in content and "QUOTE_REVISION" in content,
    )
    check(
        "the published band is on the card, as a range and never as one end of one",
        # The whole string, not the two ends separately: both numbers appear elsewhere in the
        # card, and the property is that they are joined into one interval.
        BAND_AS_RENDERED in text,
    )
    check(
        "and so is the exact amount the owner is being asked to authorise",
        CHOSEN_AS_RENDERED in text and "Nhân viên đề nghị" in text,
    )

    # The whole item, in one assertion. `compareDocumentPosition` is the only honest way to ask it:
    # both nodes could exist with the button first, or in a collapsed panel, and the screen would
    # still contain every string checked above.
    ordering = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          const money = [...card.querySelectorAll(".money")]
            .find((n) => n.textContent.includes("150.000"));
          if (!approve) return "no approve control";
          if (!money) return "the amount is not rendered as money";
          const before =
            Boolean(money.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING);
          return { before, disabled: approve.disabled };
        }"""
    )
    check(
        "the amount precedes the approve control in the document, not merely on the page",
        isinstance(ordering, dict) and ordering.get("before") is True,
        repr(ordering),
    )
    check(
        "and the control is pressable, because the owner can now see what it approves",
        isinstance(ordering, dict) and ordering.get("disabled") is False,
        repr(ordering),
    )

    # Now the failure direction, which is the one that has to be safe. The server refuses the read
    # -- a stored amount that does not re-derive to the digest the envelope binds -- and the card
    # must go back to offering nothing.
    state["proposal_unreadable"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()

    blocked = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          return approve ? approve.disabled : "no approve control";
        }"""
    )
    check(
        "an amount that cannot be read leaves the approve control shut",
        blocked is True,
        repr(blocked),
    )
    check(
        "no figure at all is shown beside the shut control",
        CHOSEN_AS_RENDERED not in rendered_text(),
        "an amount is on screen while the console says it cannot read one",
    )
    check(
        "the card says why, in Vietnamese, rather than only greying out",
        "Chưa xem được số tiền được đề nghị" in content
        and "chưa thấy số thì chưa quyết" in content.lower(),
    )
    check(
        "and the server's own refusal code travels through intact",
        "RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH" in content,
    )

    # Back to readable, and this time the digest disagrees. Same direction, different cause: the
    # queue row and the proposal body describe two different renderings, which means the amount on
    # screen would not be the amount the press hands back.
    state["proposal_unreadable"] = False
    RANGE_PRICE_PROPOSAL_CONTENT["rendered_hash"] = "JCS-SHA256-V1:" + "f" * 64
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()
    stale = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          return approve ? approve.disabled : "no approve control";
        }"""
    )
    check(
        "amounts whose digest is not the card's own are withheld, not shown with a caveat",
        stale is True and CHOSEN_AS_RENDERED not in rendered_text(),
        repr(stale),
    )
    check(
        "and the card names that as the reason, so it reads as staleness and not as a bug",
        "Số tiền không khớp phiếu" in content,
    )
    # Restored, and the queue reloaded before the link is looked for: the card on screen at this
    # point is the blocked one, which by design carries no link to anything.
    RANGE_PRICE_PROPOSAL_CONTENT["rendered_hash"] = BAND_RENDERED
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)

    # The link an approver follows. It has to carry the revision the envelope binds: without it
    # `#/quotes?quote=<id>` opens whichever revision is newest, so an approver could read revision
    # 3 while signing revision 1's digest.
    href = page.evaluate(
        """() => {
          const link = document.querySelector("article.card a[href^='#/quotes']");
          return link ? link.getAttribute("href") : null;
        }"""
    )
    check(
        "the link to the quote names the revision the envelope binds",
        isinstance(href, str) and "revision=1" in href,
        repr(href),
    )

    state["approvals_listed"] = False

    print()
    print("=" * 74)
    print("13. BẢNG TRỄ HẠN — which order, how long is left, and whose rule says so")
    print("=" * 74)

    # `OPS-BOARD-001`. The SLA engine and its board query already existed; the only way to reach
    # them was to ask the assistant, which answered "N in production, M past the mark". A count is
    # not something a shift can act on, and this section checks the three things that make the list
    # actionable without making it dishonest: which order, how long, and under whose rule.
    page.goto(f"http://localhost:{PORT}/#/sla-board", wait_until="networkidle")
    page.wait_for_timeout(700)

    cards = page.locator("article[data-sla-outcome]")
    check("the board lists the orders the server returned", cards.count() == 2, str(cards.count()))
    # The server answers in acceptance order, oldest first, and the screen renders that order
    # unchanged. Asserted as the sequence rather than as "the breached one is on top": the board
    # does not rank by urgency and must not look as though it does, because an order whose clock
    # stopped at `production_ready_at` carries frozen time remaining and can outrank a running one.
    check(
        "the rows are in the server's acceptance order, not one the browser chose",
        [cards.nth(index).get_attribute("data-order-id") for index in range(cards.count())]
        == [item["order_id"] for item in SLA_BOARD_FIRST_PAGE["items"]],
        repr([cards.nth(index).get_attribute("data-order-id") for index in range(cards.count())]),
    )
    check(
        "and each row carries its own outcome, which is what a shift ranks by",
        cards.first.get_attribute("data-sla-outcome") == "BREACHED",
        repr(cards.first.get_attribute("data-sla-outcome")),
    )

    body_text = page.inner_text("body")
    check(
        "time past the mark reads as a duration, never as a negative number",
        "quá mốc 3 giờ" in body_text and "-3" not in body_text,
    )
    check(
        "time still left reads the same way, in whole Vietnamese units",
        "còn 1 giờ" in body_text,
    )
    check(
        "no raw microsecond count reaches the screen",
        "10800000000" not in body_text and "3600000000" not in body_text,
    )
    check(
        "the board states which rule produced its numbers, in the assistant's own words",
        SLA_BOARD_POLICY_NOTICE in body_text.replace("\n", " "),
        "the shared policy sentence is missing or reworded",
    )
    check(
        "and says plainly that this is the shop's own mark, not a promise to a customer",
        "không phải hẹn với khách" in body_text,
    )
    check(
        "the versioned query behind the figures is on the screen, not only in the response",
        "sla-risk-board-v1:e56e50ea7beb4021" in body_text,
    )
    check(
        "each row links to the order it is about, so the list is something to act on",
        page.locator("a[href='#/orders/aaaaaaa1-0000-4000-8000-000000000001']").count() >= 1,
    )

    # Paging. The control must fetch the *next* page and append it, not re-fetch the first -- and
    # the second page was read at a second instant, which the screen has to say rather than file
    # both reads under one stamp.
    page.locator("button", has_text="Tải thêm").first.click()
    page.wait_for_timeout(700)
    check(
        "Tải thêm appends the next keyset page rather than replacing or repeating the first",
        page.locator("article[data-sla-outcome]").count() == 3,
        str(page.locator("article[data-sla-outcome]").count()),
    )
    body_text = page.inner_text("body")
    check(
        "the appended rows say they were read at their own, later instant",
        "muộn hơn phần ở trên" in body_text,
    )
    check(
        "a finished order shows the time it finished with, not a clock still running",
        "Xong trước mốc" in body_text and "còn 7 giờ" in body_text,
    )
    check(
        "and the control disappears once the board has no further page",
        page.locator("button", has_text="Tải thêm").count() == 0,
    )

    print()
    print("=" * 74)
    print("14. DUYỆT XUẤT DỮ LIỆU — an owner can decide it, and only with the file in view")
    print("=" * 74)

    # `EXPORT-FIX-001`. Two defects, both on this path, and the browser is the only place either
    # can be proved. The first is reachability: `VIEWABLE_RESOURCES` had no `EXPORT_REQUEST` entry,
    # so the envelope `#/exports` raises could never be decided from `#/approvals` by anybody — a
    # staff member could request an export that no owner in the shop was able to release. A source
    # test can prove the table now has a key; only this can prove the button is pressable.
    #
    # The second is that fixing reachability must not produce blind approval. What leaves the
    # building on that press cannot be recalled, so the day, the columns and the exclusions have to
    # be in the document ABOVE the control, and every path that cannot show them has to leave it
    # shut. Both directions are checked, and the shut direction twice: once on the button and once
    # on the absence of the content beside it.

    state["approvals_listed"] = False
    state["export_listed"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()
    text = rendered_text()

    check(
        "the queue names the export action and its resource type",
        "EXPORT_SANITIZED_DATA" in content and "EXPORT_REQUEST" in content,
    )
    check(
        "the business day the file is about is on the card",
        EXPORT_BUSINESS_DATE in text,
    )
    check(
        "so is the exact column list the file will carry",
        "settlement_attested_at" in text and "paid_amount_vnd" in text,
    )
    check(
        "and the exclusions, named rather than implied by the word sanitized",
        "customer_incident_evidence.summary" in text and "orders.bound_contact_id" in text,
    )
    check(
        "the always-false incident column is not offered as a fact",
        "incident_open" not in text,
    )
    check(
        "the card says which event cuts the shop's day, because two figures here share a name",
        "orders.created_at" in text and "không phải theo lúc thu tiền" in text,
    )

    # The whole item, in one assertion, asked the only honest way: both nodes could exist with the
    # button first and every string above would still be on the page.
    ordering = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          const columns = [...card.querySelectorAll(".mono")]
            .find((n) => n.textContent.includes("settlement_attested_at"));
          if (!approve) return "no approve control";
          if (!columns) return "the column list is not rendered";
          const before =
            Boolean(columns.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING);
          return { before, disabled: approve.disabled };
        }"""
    )
    check(
        "the column list precedes the approve control in the document, not merely on the page",
        isinstance(ordering, dict) and ordering.get("before") is True,
        repr(ordering),
    )
    check(
        "and the control is pressable at last — this envelope was undecidable by anyone",
        isinstance(ordering, dict) and ordering.get("disabled") is False,
        repr(ordering),
    )
    check(
        "the card carries no link, because no screen renders a stored export request",
        page.evaluate(
            """() => document.querySelectorAll("article.card a[href^='#/exports']").length"""
        )
        == 0,
    )

    # Separation of duty, bound to the person who DEFINED the export rather than to whoever raised
    # the envelope. The server refuses that decision; the card says so first, beside a shut control,
    # so the owner is not sent to press a button that will 403.
    EXPORT_REQUEST_CONTENT["requested_by_you"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()
    mine = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          return approve ? approve.disabled : "no approve control";
        }"""
    )
    check(
        "the staff member who defined the export cannot approve it from here",
        mine is True,
        repr(mine),
    )
    check(
        "and is told that before pressing, not by a 403 afterwards",
        "do chính bạn tạo" in content and "Nhờ một chủ tiệm khác quyết" in content,
    )
    check(
        "the contents stay on screen, because the refusal is about who may sign and not about what",
        EXPORT_BUSINESS_DATE in rendered_text(),
    )
    EXPORT_REQUEST_CONTENT["requested_by_you"] = False

    # Now the failure direction that has to be safe. The server cannot re-derive the document the
    # owner is being asked to sign, so the card must offer nothing at all.
    state["export_unreadable"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()
    blocked = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          return approve ? approve.disabled : "no approve control";
        }"""
    )
    check(
        "contents that cannot be read leave the approve control shut",
        blocked is True,
        repr(blocked),
    )
    check(
        "no column list is shown beside the shut control",
        "settlement_attested_at" not in rendered_text(),
        "columns are on screen while the console says it cannot read them",
    )
    check(
        "the card says why, in Vietnamese, rather than only greying out",
        "Chưa xem được dữ liệu sắp rời khỏi hệ thống" in content,
    )
    check(
        "and the server's own refusal code travels through intact",
        "EXPORT_REQUEST_CORRUPT" in content,
    )
    check(
        "that code is glossed rather than left as a bare English token",
        "Bản ghi của yêu cầu xuất này không đọc lại được" in content,
    )

    # Readable again, and this time the digest disagrees: the export's columns moved after the
    # envelope was raised, so what is on screen is not what the press would hand back.
    state["export_unreadable"] = False
    EXPORT_REQUEST_CONTENT["rendered_hash"] = "JCS-SHA256-V1:" + "1" * 64
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    content = page.content()
    stale = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          return approve ? approve.disabled : "no approve control";
        }"""
    )
    check(
        "contents whose digest is not the card's own are withheld, not shown with a caveat",
        stale is True and "settlement_attested_at" not in rendered_text(),
        repr(stale),
    )
    check(
        "and the card names that as the reason, so it reads as a moved column list and not a bug",
        "Nội dung không khớp phiếu" in content,
    )
    EXPORT_REQUEST_CONTENT["rendered_hash"] = EXPORT_RENDERED
    state["export_listed"] = False

    print()
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
