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
import re
import socketserver
import sys
import threading
import time
import urllib.parse
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Route, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
# Overridable so several engineers can run the suite at once on one machine.
PORT = int(os.environ.get("CONSOLE_STUB_PORT", "8912"))
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
        # READ-ENRICH-001: the order's walk-in ticket, which is how the list names a complaint.
        "ticket_number": 17,
        "ticket_issued_on": "2026-09-17",
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
        "ticket_number": None,
        "ticket_issued_on": None,
    },
]

#: CONSOLE-REDESIGN-004: the order the ticket search finds for "17" -- the order of the first
#: incident above, as `GET /stores/{store}/orders?ticket=17` returns it. The complaint sheet must
#: send exactly this id, which nobody typed.
TICKET_ORDER = {
    "order_id": INCIDENTS[0]["order_id"],
    "store_id": STORE,
    "commercial": "COMPLETED",
    "intake": "ACCEPTED",
    "production": "RELEASED",
    "balance": "PAID",
    "row_version": 9,
    "replayed": False,
    "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
    "created_at": "2026-09-17T01:00:00+00:00",
    "quote_id": "55555555-6666-4333-8444-888888888888",
    "quote_revision": 1,
    "payable_total_vnd": 90_000,
    "ticket_number": 17,
    "ticket_issued_on": "2026-09-17",
    "self_collection_recorded": True,
    "acquisition_source": "WALK_IN",
}


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
            # `REMEDY-GARMENT-001`: one dress is garment 1, without a picker.
            "garments": 1,
            "garment_committed_vnd": [0],
            "line_level_committed_vnd": 0,
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
            "garments": 1,
            "garment_committed_vnd": [0],
            "line_level_committed_vnd": 0,
        },
    ],
    "late_delivery_credit_vnd": 17_000,
    "late_delivery_threshold_minutes": 120,
    "loss_requires_owner": True,
    "order_refunded": False,
}

#: `REMEDY-GARMENT-001`. The same incident with a line of three shirts at 50.000 d, shirt #1
#: already carrying 100.000 d -- the staff limit exactly. Served only once section 11 asks for it,
#: so every check before it sees the two-line order above unchanged.
REMEDY_OPTIONS_SHIRTS = {
    **REMEDY_OPTIONS,
    "damage_line_ceilings_vnd": {"line-shirts": 250_000},
    "damage_lines": [
        {
            "line_id": "line-shirts",
            "service_code": "DC_SHIRT",
            "service_name": "Áo sơ mi",
            "unit": "ITEM",
            "quantity": "3",
            "item_fee_basis": "UNIT",
            "item_fee_vnd": 50_000,
            "ceiling_vnd": 250_000,
            "pieces": 3,
            "line_ceiling_vnd": 750_000,
            "committed_vnd": 100_000,
            "owner_always": [],
            "garments": 3,
            "garment_committed_vnd": [100_000, 0, 0],
            "line_level_committed_vnd": 0,
        },
    ],
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


#: READ-PATHS-001, section 15. One order as `GET /internal/v1/orders/{id}` returns it, with the
#: acquisition source the counter recorded; the two credits it issued, one unspent and one spent;
#: the proposals of one incident, including a pre-DEC-031 loss with no figure and an owner envelope
#: that ran out; and a store's staff, one of them disabled and one with a name that is markup.
ORDER_VIEW_ID = "eeeeeeee-5555-4333-8444-555555555555"
ORDER_VIEW = {
    "order_id": ORDER_VIEW_ID,
    "store_id": STORE,
    "commercial": "COMPLETED",
    "intake": "ACCEPTED",
    "production": "RELEASED",
    "balance": "PAID",
    "row_version": 14,
    "replayed": False,
    "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
    "created_at": "2026-09-20T03:00:00+00:00",
    "quote_id": "55555555-6666-4333-8444-999999999999",
    "quote_revision": 2,
    "payable_total_vnd": 132_000,
    "ticket_number": 17,
    "ticket_issued_on": "2026-09-20",
    "self_collection_recorded": True,
    "acquisition_source": "GOOGLE_MAPS",
    "delivery_legs": [],
    "required_delivery_legs_succeeded": False,
    "settlement_shape": "EXACT_PAYMENT_AT_PICKUP",
    "next_steps": [],
}
#: The complaint on ORDER_VIEW, as the counter typed it -- markup on purpose (section 17).
ORDER_INCIDENT_SUMMARY = "Cổ áo bị ố <img src=x onerror=alert(1)>"
UNUSED_CREDIT_ID = "abababab-1111-4333-8444-555555555555"
SPENT_CREDIT_ID = "cdcdcdcd-2222-4333-8444-555555555555"
ORDER_CREDITS = {
    "store_id": STORE,
    "order_id": ORDER_VIEW_ID,
    "truncated": False,
    "credits": [
        {
            "credit_id": UNUSED_CREDIT_ID,
            "remedy_proposal_id": "12121212-3333-4333-8444-555555555555",
            "incident_id": INCIDENTS[0]["incident_id"],
            "kind": "LATE_DELIVERY_CREDIT",
            # Not round, so a screen that reformatted or rounded it would be visible.
            "amount_vnd": 13_200,
            "status": "UNUSED",
            "issued_at": "2026-09-21T03:00:00+00:00",
            "redeemed_at": None,
            "redeemed_quote_id": None,
            "redeemed_quote_revision": None,
        },
        {
            "credit_id": SPENT_CREDIT_ID,
            "remedy_proposal_id": "34343434-4444-4333-8444-555555555555",
            "incident_id": INCIDENTS[0]["incident_id"],
            "kind": "DAMAGE_COMPENSATION",
            "amount_vnd": 80_000,
            "status": "REDEEMED",
            "issued_at": "2026-09-21T04:00:00+00:00",
            "redeemed_at": "2026-09-22T04:00:00+00:00",
            "redeemed_quote_id": "56565656-5555-4333-8444-555555555555",
            "redeemed_quote_revision": 3,
        },
    ],
}
RECORDED_PROPOSALS = {
    "store_id": STORE,
    "incident_id": INCIDENTS[0]["incident_id"],
    "order_id": INCIDENTS[0]["order_id"],
    "truncated": False,
    "proposals": [
        {
            "proposal_id": "78787878-6666-4333-8444-555555555555",
            "kind": "DAMAGE_COMPENSATION",
            "status": "OWNER_APPROVAL_REQUIRED",
            "amount_vnd": 150_000,
            "ceiling_vnd": 600_000,
            "order_line_id": "line-large",
            "attested_late_by_minutes": None,
            "window_closes_at": "2026-09-18T03:00:00+00:00",
            "approval_id": "89898989-7777-4333-8444-555555555555",
            "approval_status": "REQUESTED",
            "approval_expires_at": "2026-09-17T03:10:00+00:00",
            "approval_lapsed": True,
            "proposed_by": "00000000-0000-4000-8000-0000000000cc",
            "proposed_by_name": "Nguyễn Thị Lan",
            "proposed_at": "2026-09-17T03:00:00+00:00",
            "executed_at": None,
            "credit_id": None,
            "next_step": "PROPOSE_AGAIN",
        },
        {
            "proposal_id": "90909090-8888-4333-8444-555555555555",
            "kind": "LOST_ITEM",
            "status": "POLICY_UNRESOLVED",
            "amount_vnd": None,
            "ceiling_vnd": None,
            "order_line_id": None,
            "attested_late_by_minutes": None,
            "window_closes_at": None,
            "approval_id": None,
            "approval_status": None,
            "approval_expires_at": None,
            "approval_lapsed": None,
            "proposed_by": "00000000-0000-4000-8000-0000000000cc",
            "proposed_by_name": "Nguyễn Thị Lan",
            "proposed_at": "2026-09-17T04:00:00+00:00",
            "executed_at": None,
            "credit_id": None,
            "next_step": "NONE",
        },
    ],
}

#: `REMEDY-OWNER-DECIDE-001`, section 18. One `APPROVE_REMEDY` envelope as the queue returns it,
#: the owner's read of what it binds, and two more rows for the incident's list: a loss the owner
#: approved (the list offers the execute press) and one still waiting (it does not).
OWNER_REMEDY_APPROVAL = "abcdabcd-1111-4333-8444-666666666666"
OWNER_REMEDY_PROPOSAL = "cdefcdef-2222-4333-8444-666666666666"
OWNER_REMEDY_WAITING = "efefefef-3333-4333-8444-666666666666"
OWNER_REMEDY_SNAPSHOT = "JCS-SHA256-V1:" + "1" * 64
OWNER_REMEDY_RENDERED = "JCS-SHA256-V1:" + "2" * 64
#: The complaint as a staff member typed it -- untrusted text, and hostile on purpose.
OWNER_REMEDY_SUMMARY = 'Khách báo mất áo sơ mi trắng <img src=x onerror="window.__remedyPwned=1">'


def owner_remedy_queue_item() -> dict[str, object]:
    """The envelope in `GET /internal/v1/approvals`, open until the end of the next business day."""

    return {
        "approval_request_id": OWNER_REMEDY_APPROVAL,
        "status": "REQUESTED",
        "envelope_hash": "JCS-SHA256-V1:" + "3" * 64,
        "required_role": "OWNER_ADMIN",
        # Twenty hours: long enough that the badge must read in hours, not minutes.
        "expires_at": (datetime.now(UTC) + timedelta(hours=20)).isoformat(),
        "replayed": False,
        "resource_type": "REMEDY_PROPOSAL",
        "resource_id": OWNER_REMEDY_PROPOSAL,
        "resource_version": 1,
        "snapshot_hash": OWNER_REMEDY_SNAPSHOT,
        "rendered_hash": OWNER_REMEDY_RENDERED,
        "action": "APPROVE_REMEDY",
        "store_id": STORE,
    }


def owner_remedy_binding() -> dict[str, object]:
    """`RemedyApprovalBindingResponse` from `main.py`, field for field."""

    return {
        "store_id": STORE,
        "action": "APPROVE_REMEDY",
        "resource_type": "REMEDY_PROPOSAL",
        "resource_id": OWNER_REMEDY_PROPOSAL,
        "resource_version": 1,
        "snapshot_hash": OWNER_REMEDY_SNAPSHOT,
        "rendered_hash": OWNER_REMEDY_RENDERED,
        "envelope_matches": True,
        "approval_id": OWNER_REMEDY_APPROVAL,
        "approval_status": "REQUESTED",
        "approval_expires_at": (datetime.now(UTC) + timedelta(hours=20)).isoformat(),
        "approval_lapsed": False,
        "next_step": "AWAIT_OWNER",
        "incident_id": INCIDENTS[0]["incident_id"],
        "order_id": ORDER_VIEW_ID,
        "ticket_number": 17,
        "ticket_issued_on": "2026-09-20",
        "kind": "LOST_ITEM",
        "status": "OWNER_APPROVAL_REQUIRED",
        # Not round, so a screen that reformatted or rounded it would be visible.
        "amount_vnd": 52_500,
        "ceiling_vnd": 250_000,
        "staff_approval_ceiling_vnd": 100_000,
        "owner_reasons": ["LOSS_CLAIM"],
        "order_line_id": "line-shirts",
        "service_code": "SHIRT_WASH_PRESS",
        "service_name": "Áo sơ mi giặt ủi",
        "garment_index": 2,
        "incident_summary": OWNER_REMEDY_SUMMARY,
        "proposed_by": "00000000-0000-4000-8000-0000000000cc",
        "proposed_by_name": "Nguyễn Thị Lan",
        "proposed_at": "2026-09-25T02:00:00+00:00",
        "executed_at": None,
        "credit_id": None,
    }


def executable_recorded_proposals() -> dict[str, object]:
    """The incident's list once the owner approved one loss and another still waits."""

    expires = (datetime.now(UTC) + timedelta(hours=20)).isoformat()
    common = {
        "kind": "LOST_ITEM",
        "status": "OWNER_APPROVAL_REQUIRED",
        "ceiling_vnd": 250_000,
        "order_line_id": "line-shirts",
        "attested_late_by_minutes": None,
        "window_closes_at": "2026-09-26T03:00:00+00:00",
        "approval_expires_at": expires,
        "approval_lapsed": False,
        "proposed_by": "00000000-0000-4000-8000-0000000000cc",
        "proposed_by_name": "Nguyễn Thị Lan",
        "proposed_at": "2026-09-25T02:00:00+00:00",
        "executed_at": None,
        "credit_id": None,
    }
    return {
        **RECORDED_PROPOSALS,
        "proposals": [
            *RECORDED_PROPOSALS["proposals"],  # type: ignore[misc]
            {
                **common,
                "proposal_id": OWNER_REMEDY_PROPOSAL,
                "amount_vnd": 52_500,
                "approval_id": OWNER_REMEDY_APPROVAL,
                "approval_status": "APPROVED",
                "next_step": "EXECUTE",
            },
            {
                **common,
                "proposal_id": OWNER_REMEDY_WAITING,
                "amount_vnd": 30_000,
                "approval_id": "fafafafa-4444-4333-8444-666666666666",
                "approval_status": "REQUESTED",
                "next_step": "AWAIT_OWNER",
            },
        ],
    }


STAFF_ACTIVE_ID = "a1a1a1a1-1111-4333-8444-555555555555"
STAFF_DISABLED_ID = "b2b2b2b2-2222-4333-8444-555555555555"
#: The id `POST /internal/v1/staff` answers with in section 17 -- a person in no store yet.
STAFF_NEW_ID = "c3c3c3c3-3333-4333-8444-555555555555"
#: Markup as a display name. The owner typed it; the directory must print it, never parse it.
HOSTILE_NAME = '<img src=x onerror="window.__staffInjected=1">Bình'
STAFF_DIRECTORY = {
    "store_id": STORE,
    "recent_revocation_days": 30,
    "truncated": False,
    "staff": [
        {
            "staff_user_id": STAFF_ACTIVE_ID,
            "display_name": HOSTILE_NAME,
            "status": "ACTIVE",
            "roles": ["OPS_APPROVER", "OPERATOR"],
            "assigned_at": "2026-09-01T03:00:00+00:00",
            "assignment_revoked_at": None,
        },
        {
            "staff_user_id": STAFF_DISABLED_ID,
            "display_name": "Lê Thị Cúc",
            "status": "DISABLED",
            "roles": ["OPERATOR"],
            "assigned_at": "2026-08-01T03:00:00+00:00",
            "assignment_revoked_at": "2026-09-20T03:00:00+00:00",
        },
    ],
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

#: CUSTOMER-001 (DEC-034): one regular of the store, as `GET …/customers?q=` returns them, the
#: notice before and after the owner publishes it, and the record `POST …/customers` answers with.
CUSTOMER_ID = "77777777-8888-4333-8444-aaaaaaaaaaa1"
CUSTOMER_PHONE = "0905123456"
CUSTOMER_SUMMARY = {
    "customer_id": CUSTOMER_ID,
    "display_name": "chị Lan",
    "phone": CUSTOMER_PHONE,
    "phone_last4": "3456",
    "phone_visible": True,
    "kind": "RETAIL",
    "marketing_consent": False,
    "last_activity_at": "2026-09-20T02:00:00+00:00",
    "open_order_count": 1,
}
CUSTOMER_PROFILE = {
    "customer_id": "77777777-8888-4333-8444-aaaaaaaaaaa2",
    "store_id": STORE,
    "display_name": "anh Tuấn",
    "phone": "0382000111",
    "phone_last4": "0111",
    "phone_visible": True,
    "kind": "RETAIL",
    "delivery_address": None,
    "note": None,
    "marketing_consent": False,
    "marketing_consent_at": None,
    "marketing_withdrawn_at": None,
    "service_consent_at": "2026-09-25T03:00:00+00:00",
    "service_consent_notice_version": 1,
    "last_activity_at": "2026-09-25T03:00:00+00:00",
    "created_at": "2026-09-25T03:00:00+00:00",
    "erased_at": None,
    "erasure_reason": None,
    "row_version": 1,
}
NOTICE_UNPUBLISHED = {
    "published": False,
    "version": None,
    "notice_version": None,
    "title": None,
    "text": None,
    "consent_sentence": None,
    "service_consent_label": None,
    "marketing_consent_label": None,
    "retention_months": None,
    "legal_entity": None,
}
NOTICE_PUBLISHED = {
    "published": True,
    "version": 1,
    "notice_version": "V1",
    "title": "Thông tin của anh/chị ở tiệm Giặt Là Sạch Cộng",
    "text": "**Ai giữ thông tin.** CÔNG TY TNHH A & T CARE. Hotline 0382 318 492.\n\n"
    "**Giữ bao lâu.** 24 tháng không có đơn thì tiệm tự xoá.",
    "consent_sentence": "Dạ, tiệm xin lưu số điện thoại để báo khi đồ xong. "
    "Anh/chị đồng ý không ạ?",
    "service_consent_label": "Khách đã nghe và đồng ý cho tiệm lưu thông tin để phục vụ đơn",
    "marketing_consent_label": "Khách muốn nhận tin ưu đãi của tiệm (không bắt buộc)",
    "retention_months": 24,
    "legal_entity": "CÔNG TY TNHH A & T CARE",
}

#: CONTACT-PICK-001: one returning channel customer of the store, as `GET …/contacts/recent` returns
#: it, and a second binding the conversation hand-off names. Neither is typed anywhere.
RECENT_CONTACT = "55555555-6666-4333-8444-999999999991"
HANDED_OFF_CONTACT = "55555555-6666-4333-8444-999999999992"
RECENT_CONTACTS = {
    "store_id": STORE,
    "limit": 20,
    "truncated": False,
    "contacts": [
        {
            "contact_binding_id": RECENT_CONTACT,
            "channels": ["ZALO_OA"],
            "last_activity_at": "2026-08-16T02:00:00+00:00",
            "latest_order": {
                "order_id": "44444444-5555-4333-8444-888888888801",
                "created_at": "2026-08-16T02:00:00+00:00",
                "commercial": "ACTIVE",
                "intake": "ACCEPTED",
                "production": "IN_PROCESS",
                "balance": "UNPAID",
                "payable_total_vnd": 120000,
                "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
                "self_collection_recorded": False,
                "required_delivery_legs_succeeded": False,
            },
            "open_order_count": 1,
            "waiting_order_request_id": None,
        }
    ],
}

#: `OrderResponse`, verbatim. Note what it does not contain: `acquisition_source`. The command
#: reply never carries it; since READ-PATHS-001 the order *read* does (`ORDER_VIEW` below), which is
#: what section 15 reads it back from. Section 7 still checks the form's behaviour, because what the
#: next customer's form says is a property of the form, not of any read.
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

#: Section 15's order: fetched by the shop's courier, collected by the customer at the counter.
PICKUP_ORDER_ID = "77777777-8888-4333-8444-999999999999"

#: Section 20, `RECEIPT-PRINT-001`. An order whose bound revision carries everything a receipt
#: prints: two lines at their list amounts, a promotion and a remedy credit taken off, a delivery
#: fee added, and the order's own total. The figures are chosen so a screen that netted or added
#: anything would print a number that is not here.
#: The receipt prints a credit as U+2212 MINUS SIGN, the typographic minus, not a hyphen.
MINUS = "\u2212"
RECEIPT_ORDER_ID = "abcdef01-2345-4333-8444-555555555555"
RECEIPT_QUOTE_ID = "abcdef02-2345-4333-8444-555555555555"
RECEIPT_TOTAL_VND = 120_000 + 30_000 - 36_000 - 11_000 + 15_000
RECEIPT_ORDER = {
    "order_id": RECEIPT_ORDER_ID,
    "store_id": STORE,
    "commercial": "ACTIVE",
    "intake": "ACCEPTED",
    "production": "IN_PROCESS",
    "balance": "UNPAID",
    "row_version": 6,
    "replayed": False,
    "fulfillment_mode": "RETURN_ONLY",
    "created_at": "2026-09-25T02:15:00+00:00",
    "quote_id": RECEIPT_QUOTE_ID,
    "quote_revision": 3,
    "payable_total_vnd": RECEIPT_TOTAL_VND,
    "ticket_number": 23,
    "ticket_issued_on": "2026-09-25",
    "self_collection_recorded": False,
    "acquisition_source": "WALK_IN",
    "delivery_legs": [],
    "required_delivery_legs_succeeded": False,
    "settlement_shape": None,
    "next_steps": [],
    # CUSTOMER-001: a regular with a number on record, so R4's "Tiệm sẽ báo" is a promise the shop
    # can keep. The walk-in variant is checked by clearing these three.
    "customer_id": "77777777-8888-4333-8444-aaaaaaaaaaa1",
    "customer_name": "chị Lan",
    "customer_has_phone": True,
}
RECEIPT_DETAIL = {
    "quote_id": RECEIPT_QUOTE_ID,
    "revision": 3,
    "row_version": 3,
    "finality": "APPROVED_EXACT",
    "status": "ACCEPTED_FINAL",
    "snapshot_hash": "JCS-SHA256-V1:" + "7" * 64,
    "display_total_min_vnd": RECEIPT_TOTAL_VND,
    "display_total_max_vnd": RECEIPT_TOTAL_VND,
    "valid_until": None,
    "reason_codes": [],
    "lines": [
        {
            "line_id": "line-1",
            "service_code": "STD_WASH_DRY_LT6",
            "quantity": "4.8",
            "unit": "KG",
            "price_kind": "EXACT",
            "net_amount_vnd": 120_000 - 36_000 - 8_000,
            "list_amount_vnd": 120_000,
            "band_minimum_vnd": None,
            "band_maximum_vnd": None,
        },
        {
            "line_id": "line-2",
            "service_code": "IRON_SHIRT_UNREGISTERED",
            "quantity": "3",
            "unit": "ITEM",
            "price_kind": "EXACT",
            "net_amount_vnd": 30_000 - 3_000,
            "list_amount_vnd": 30_000,
            "band_minimum_vnd": None,
            "band_maximum_vnd": None,
        },
    ],
    "customer_accepted_at": "2026-09-25T02:14:00+00:00",
    "order_request_id": "33333333-4444-4333-8444-777777777777",
    "contact_binding_id": "22222222-3333-4333-8444-666666666666",
    "fulfillment_mode": "RETURN_ONLY",
    "adjustments": [
        {
            "kind": "PROMOTION",
            "direction": "CREDIT",
            "amount_min_vnd": 36_000,
            "amount_max_vnd": 36_000,
            "reason_code": "PROMO_WET30_DRY40_20260717_20260831",
        },
        {
            "kind": "REMEDY_CREDIT",
            "direction": "CREDIT",
            "amount_min_vnd": 11_000,
            "amount_max_vnd": 11_000,
            "reason_code": "REMEDY_CREDIT_APPLIED",
        },
        {
            "kind": "DELIVERY",
            "direction": "DEBIT",
            "amount_min_vnd": 15_000,
            "amount_max_vnd": 15_000,
            "reason_code": "AUTO_FIXED",
        },
    ],
}


def step(name: str, primary: bool = False, **extra: object) -> dict[str, object]:
    """One `NextStepResponse` (ORDER-STEPS-001)."""

    return {
        "step": name,
        "primary": primary,
        "requires": list(extra.get("requires", [])),  # type: ignore[call-overload]
        "custody_resolutions": list(extra.get("custody_resolutions", [])),  # type: ignore[call-overload]
        # ORDER-STEPS-002: the reasons a REWASH / REJECT_INTAKE will take, each dry-run.
        "rewash_reasons": list(extra.get("rewash_reasons", [])),  # type: ignore[call-overload]
        "rejection_reasons": list(extra.get("rejection_reasons", [])),  # type: ignore[call-overload]
    }


def promise_read(*, published: bool, options: dict[str, object] | None = None) -> dict[str, object]:
    """`GET /internal/v1/orders/{id}/promise` (PROMISE-001), before Nhận đồ."""

    return {
        "order_id": PICKUP_ORDER_ID,
        "policy_published": published,
        "policy_version": 1 if published else None,
        "opens_at": "08:00" if published else None,
        "closes_at": "20:00" if published else None,
        "promised_ready_at": None,
        "current_promise_at": None,
        "promise_state": None,
        "promise_basis": None,
        "promise_rule_id": None,
        "changes": [],
        "change_count": 0,
        "truncated": False,
        "options": options,
        "evaluated_at": "2026-09-25T10:00:00+00:00",
    }


def promise_options(
    requirement: str,
    default_at: str | None,
    choices: list[tuple[str, str | None]],
    *,
    reasons: list[str] | None = None,
    human: list[str] | None = None,
) -> dict[str, object]:
    """The `options` of a promise read: what pressing Nhận đồ now would promise, per choice."""

    return {
        "requirement": requirement,
        "default_promised_at": default_at,
        "default_choice": "H48" if requirement == "RANGE_CHOICE" else None,
        "choices": [{"choice": choice, "promised_at": at} for choice, at in choices],
        "reason_codes": reasons or [],
        "human_service_codes": human or [],
    }


def order_view(
    mode: str, *, balance: str, collected: bool, steps: list[dict[str, object]] | None = None
) -> dict[str, object]:
    """One `OrderViewResponse`, as `GET /internal/v1/orders/{id}` sends it, at the counter's
    pickup moment: running, washed and released, the total known.

    `steps` is the server's `next_steps` for that order, written out as the domain computes it
    (`order_steps.next_steps`): the console holds no transition table, so what it offers is
    exactly this list and the checks in section 15 are about rendering it faithfully.
    """

    return {
        "order_id": PICKUP_ORDER_ID,
        "store_id": STORE,
        "commercial": "ACTIVE",
        "intake": "ACCEPTED",
        "production": "RELEASED",
        "balance": balance,
        "row_version": 14,
        "replayed": False,
        "fulfillment_mode": mode,
        "created_at": "2026-09-25T02:00:00+00:00",
        "quote_id": "88888888-9999-4333-8444-aaaaaaaaaaaa",
        "quote_revision": 2,
        "payable_total_vnd": 110_000,
        "ticket_number": 12,
        "ticket_issued_on": "2026-09-25",
        "self_collection_recorded": collected,
        "acquisition_source": "WALK_IN",
        "delivery_legs": [],
        "required_delivery_legs_succeeded": False,
        "settlement_shape": None
        if balance == "UNPAID"
        else "EXACT_PAYMENT_PREPAID_SELF_COLLECTION",
        "next_steps": steps or [],
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
    "Đơn có giờ hẹn trả được tính theo giờ hẹn của chính đơn đó. Đơn nhận trước khi chủ tiệm công "
    "bố quy tắc hẹn trả thì tính theo quy tắc SLA_STANDARD_CLOTHES — 8 giờ kể từ khi nhận sản xuất."
)
#: REPORT-DASHBOARD-001. The report's figures, in the `FR-RPT-005` shape. Awkward on purpose: a
#: fraction that does not round to a whole percent, a denominator of zero (no order reached quality
#: check), and a window whose refunds exceeded its takings, so the drawer went OUT. The screen must
#: print every one as the server sent it and compute none of them.
REPORT_VERSION = "report-v3:b65625ff9f50cbe7"

#: SHOP-CAPTURE-001 (DEC-038). The machines a load goes into, in the server's order (the last one
#: used first): the console must offer exactly these, in this order, and never re-sort them.
STUB_WASH_MACHINES = [
    {
        "machine_id": "abababab-0000-4000-8000-000000000002",
        "code": "WASH-02",
        "display_name": "Máy giặt LG 13 kg",
        "category": "washer",
        "starts_cycle": True,
        "source": "MACHINE_MASTER",
        "retired_at": None,
        "row_version": 1,
        "last_used_at": "2026-09-25T02:00:00+00:00",
        "cycles": 4,
        "replayed": False,
    },
    {
        "machine_id": "abababab-0000-4000-8000-000000000001",
        "code": "WASH-01",
        "display_name": "Máy giặt SPINZ 32 kg",
        "category": "washer",
        "starts_cycle": True,
        "source": "MACHINE_MASTER",
        "retired_at": None,
        "row_version": 1,
        "last_used_at": None,
        "cycles": 0,
        "replayed": False,
    },
]

#: The order page's capture read: a closed cycle on WASH-02, and a costed pickup trip. The weight
#: is 22 kg, so the owner's rule suggests a car -- which the leg sheet may say, never pick.
STUB_CAPTURE = {
    "order_id": "77777777-8888-4333-8444-999999999999",
    "store_id": "11111111-2222-4333-8444-555555555555",
    "weight_kg": "22",
    "weight_basis": "MEASURED",
    "suggested_vehicle": "O_TO",
    "cycles": [
        {
            "cycle_id": "cdcdcdcd-0000-4000-8000-000000000001",
            "kind": "WASH",
            "machine_id": "abababab-0000-4000-8000-000000000002",
            "machine_code": "WASH-02",
            "machine_name": "Máy giặt LG 13 kg",
            "started_at": "2026-09-25T02:00:00+00:00",
            "ended_at": "2026-09-25T02:47:00+00:00",
            "minutes": 47,
        }
    ],
    "legs": [
        {
            "leg_id": "efefefef-0000-4000-8000-000000000001",
            "leg_kind": "PICKUP",
            "outcome": "SUCCEEDED",
            "recorded_at": "2026-09-25T01:00:00+00:00",
            "vehicle": "O_TO",
            "km": "6.5",
            "cost_vnd": 45_000,
            "note": "gửi xe",
        }
    ],
}


def expense_month(month: str) -> dict[str, object]:
    """One month of Sổ thu chi as the server sums it. Totals are deliberately not the sum of the
    listed amounts (a voided line is listed and not counted), so a screen that added up its own
    rows would print the wrong figure."""

    categories = [
        "DIEN",
        "NUOC",
        "HOA_CHAT",
        "TUI_NHAN",
        "LUONG",
        "MAT_BANG",
        "SUA_CHUA",
        "XANG_XE",
        "KHAC",
    ]
    amounts = {"DIEN": (1_250_000, 1), "HOA_CHAT": (800_000, 1)}
    line = {
        "recorded_by": "11111111-aaaa-4333-8444-555555555555",
        "recorded_by_name": "Chủ tiệm",
        "recorded_at": "2026-09-25T03:00:00+00:00",
        "row_version": 1,
        "replayed": False,
    }
    return {
        "store_id": STORE,
        "month": month,
        "from_date": f"{month}-01",
        "to_date": f"{month}-30",
        "truncated": False,
        "lines": [
            {
                **line,
                "expense_id": "e1e1e1e1-0000-4000-8000-000000000003",
                "spent_on": f"{month}-20",
                "category": "KHAC",
                "amount_vnd": 9_900_000,
                "note": None,
                "voided_at": "2026-09-25T03:05:00+00:00",
                "row_version": 2,
            },
            {
                **line,
                "expense_id": "e1e1e1e1-0000-4000-8000-000000000002",
                "spent_on": f"{month}-18",
                "category": "HOA_CHAT",
                "amount_vnd": 800_000,
                "note": "<b>nước giặt</b>",
                "voided_at": None,
            },
            {
                **line,
                "expense_id": "e1e1e1e1-0000-4000-8000-000000000001",
                "spent_on": f"{month}-05",
                "category": "DIEN",
                "amount_vnd": 1_250_000,
                "note": "tiền điện",
                "voided_at": None,
            },
        ],
        "totals": [
            {
                "category": c,
                "amount_vnd": amounts.get(c, (0, 0))[0],
                "entries": amounts.get(c, (0, 0))[1],
            }
            for c in categories
        ],
        "total_vnd": 2_050_000,
        "entries": 2,
        "voided_entries": 1,
        "core_missing": ["NUOC", "LUONG", "MAT_BANG"],
    }


def report_kpis(start: str, end: str) -> list[dict[str, object]]:
    def kpi(key, numerator, denominator=None, unit="ORDERS", quality="COMPLETE", **extra):
        return {
            "key": key,
            "numerator": numerator,
            "denominator": denominator,
            "denominator_key": None,
            "unit": unit,
            "window": {"from_date": start, "to_date": end},
            "data_quality": quality,
            "query_version": REPORT_VERSION,
            "direction": extra.get("direction"),
            "entries": extra.get("entries"),
            "amount_vnd": extra.get("amount_vnd"),
            "by_kind": extra.get("by_kind"),
            "rule_assumed": extra.get("rule_assumed"),
        }

    return [
        kpi("ORDERS_CREATED", 12),
        kpi("ORDERS_COMPLETED", 7),
        kpi("ORDERS_CANCELLED", 1),
        # PROMISE-001: two of the six were taken before the owner published the turnaround rules.
        kpi("ON_TIME_INTERNAL", 5, 6, quality="RULE_ASSUMED", rule_assumed=2),
        kpi("REWASH", 0, 0),
        kpi("COMPLAINTS", 2, 7, unit="INCIDENTS"),
        kpi("MONEY_COLLECTED", 90_000, unit="VND", entries=1),
        kpi("MONEY_REFUNDED", 240_000, unit="VND", entries=2),
        kpi("MONEY_NET", 150_000, unit="VND", direction="OUT"),
        kpi(
            "REMEDIES_EXECUTED",
            2,
            unit="REMEDIES",
            amount_vnd=80_000,
            by_kind=[
                {"kind": "FREE_REWASH", "count": 1, "amount_vnd": None},
                {"kind": "DAMAGE_COMPENSATION", "count": 1, "amount_vnd": 80_000},
                {"kind": "LATE_DELIVERY_CREDIT", "count": 0, "amount_vnd": 0},
                {"kind": "LOST_ITEM", "count": 0, "amount_vnd": 0},
            ],
        ),
    ]


def report_body(url: str, *, daily: bool) -> dict[str, object]:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    start, end = query["from"][0], query["to"][0]
    window = {
        "from_date": start,
        "to_date": end,
        "days": 2 if start != end else 1,
        "business_timezone": "Asia/Ho_Chi_Minh",
        "ends_today": True,
    }
    rule = {
        "policy_id": "SLA_STANDARD_CLOTHES",
        "policy_type": "COMMITMENT",
        "target_max_hours": 8,
        "notice_vi": SLA_BOARD_POLICY_NOTICE,
    }
    base = {
        "store_id": STORE,
        "window": window,
        "query_version": REPORT_VERSION,
        "evaluated_at": "2026-09-25T10:00:00+00:00",
        "sla_rule": rule,
    }
    if daily:
        return {
            **base,
            "days": [
                {"date": start, "kpis": report_kpis(start, start)},
                {"date": end, "kpis": report_kpis(end, end)},
            ],
        }
    spending = [
        {"category": c, "amount_vnd": a, "entries": 1 if a else 0}
        for c, a in (
            ("DIEN", 1_250_000),
            ("NUOC", 0),
            ("HOA_CHAT", 800_000),
            ("TUI_NHAN", 0),
            ("LUONG", 0),
            ("MAT_BANG", 0),
            ("SUA_CHUA", 0),
            ("XANG_XE", 0),
            ("KHAC", 0),
        )
    ]
    complete = bool(REPORT_STATE.get("margin_complete"))
    return {
        **base,
        "kpis": report_kpis(start, end),
        # SHOP-CAPTURE-001: 3 of 4 cycles named a machine; one delivered order fully costed.
        "capture": {
            "cycles": 4,
            "cycles_captured": 3,
            "machines": [
                {
                    "machine_id": "abababab-0000-4000-8000-000000000002",
                    "code": "WASH-02",
                    "display_name": "Máy giặt LG 13 kg",
                    "closed_cycles": 3,
                    "average_minutes": 47,
                }
            ],
            "delivered_orders": 2,
            "costed_orders": 1,
            "legs": 4,
            "costed_legs": 3,
            "trip_cost_vnd": 85_000,
            "cost_per_delivered_order_vnd": 85_000,
            "labour_minutes_captured": False,
            "query_version": REPORT_VERSION,
        },
        "months": [
            {
                "month": start[:7],
                "from_date": f"{start[:7]}-01",
                "to_date": f"{start[:7]}-30",
                "in_progress": True,
                "spending": spending,
                "spending_vnd": 2_050_000,
                "spending_entries": 2,
                "collected_vnd": 90_000,
                "refunded_vnd": 240_000,
                "margin": (
                    {
                        "status": "COMPLETE",
                        "missing": [],
                        "amount_vnd": 2_200_000,
                        "direction": "OUT",
                        "excludes_trip_costs": True,
                    }
                    if complete
                    else {
                        "status": "INCOMPLETE",
                        "missing": ["NUOC", "LUONG", "MAT_BANG"],
                        "amount_vnd": None,
                        "direction": None,
                        "excludes_trip_costs": True,
                    }
                ),
                "query_version": REPORT_VERSION,
            }
        ],
    }


#: Toggled by section 19 to show a complete month (whose spending exceeded its takings).
REPORT_STATE: dict[str, object] = {}


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
            # PROMISE-001: this order has a promise, and the board measured it against that.
            "reason_codes": ["PRODUCTION_SLA_EXCLUDES_DELIVERY", "ORDER_PROMISE", "SLA_BREACHED"],
            "elapsed_microseconds": 39_600_000_000,
            "remaining_microseconds": 0,
            # Three hours past the promise.
            "breach_microseconds": 10_800_000_000,
            "rule_source": "ORDER_PROMISE",
            "promise_rule_id": "SLA_STANDARD_CLOTHES",
            "due_at": "2026-09-09T08:00:00+00:00",
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
            # Taken before the owner published the turnaround rules: the stated rule's mark.
            "rule_source": "STATED_RULE",
            "promise_rule_id": None,
            "due_at": "2026-09-09T12:00:00+00:00",
        },
    ],
    "query_version": "sla-risk-board-v2:1f8c822c3e51aee1",
    "policy_id": "SLA_STANDARD_CLOTHES",
    "policy_type": "COMMITMENT",
    "policy_target_max_hours": 8,
    "policy_notice_vi": SLA_BOARD_POLICY_NOTICE,
    "evaluated_at": "2026-09-09T11:00:00+00:00",
    "next_due_at": "2026-09-09T12:00:00+00:00",
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
            "rule_source": "STATED_RULE",
            "promise_rule_id": None,
            "due_at": "2026-09-09T14:00:00+00:00",
        }
    ],
    "evaluated_at": "2026-09-09T11:00:30+00:00",
    "next_due_at": None,
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


#: CONSOLE-REDESIGN-001. The ordinary quote Nhận đồ prices in sections 1-3 and 7: a weight-priced
#: service, an estimate nobody has agreed to yet, then the revision `accept_quote` derives from it.
#: The accepted read carries `customer_accepted_at`, which is the only value the order create takes
#: that the create response does not -- so the check in section 7 is that the console sends exactly
#: this instant and not one it made up from the device clock.
EXACT_QUOTE = "88888888-9999-4333-8444-cccccccccccc"
EXACT_TOTAL_VND = 147_500
ACCEPTED_AT = "2026-09-25T03:04:05.123456+00:00"
EXACT_REVISION = {
    "quote_id": EXACT_QUOTE,
    "revision": 1,
    "row_version": 1,
    "finality": "ESTIMATE",
    "status": "PROVISIONAL",
    "snapshot_hash": "JCS-SHA256-V1:" + "e" * 64,
    "list_service_subtotal_vnd": EXACT_TOTAL_VND,
    "net_service_subtotal_vnd": EXACT_TOTAL_VND,
    "display_total_min_vnd": EXACT_TOTAL_VND,
    "display_total_max_vnd": EXACT_TOTAL_VND,
    "reason_codes": ["TAX_TREATMENT_UNVERIFIED"],
    "required_approvals": [],
    "replayed": False,
    "promotion": None,
}
ACCEPTED_REVISION = {
    **EXACT_REVISION,
    "revision": 2,
    "row_version": 2,
    "finality": "APPROVED_EXACT",
    "status": "ACCEPTED_FINAL",
    "snapshot_hash": "JCS-SHA256-V1:" + "f" * 64,
}


def exact_detail(revision: int) -> dict[str, object]:
    """`QuoteRevisionDetailResponse` for the ordinary quote, with READ-ENRICH-001's fields."""

    source = ACCEPTED_REVISION if revision == 2 else EXACT_REVISION
    return {
        **{
            key: source[key]
            for key in (
                "quote_id",
                "revision",
                "row_version",
                "finality",
                "status",
                "snapshot_hash",
                "display_total_min_vnd",
                "display_total_max_vnd",
                "reason_codes",
            )
        },
        "valid_until": "2026-09-26T03:00:00+00:00",
        "lines": [
            {
                "line_id": "line-1",
                "service_code": "STD_WASH_DRY_LT6",
                "quantity": "5.9",
                "unit": "KG",
                "price_kind": "EXACT",
                "net_amount_vnd": EXACT_TOTAL_VND,
                "band_minimum_vnd": None,
                "band_maximum_vnd": None,
            }
        ],
        "customer_accepted_at": ACCEPTED_AT if revision == 2 else None,
        "order_request_id": "33333333-4444-4333-8444-777777777777",
        "contact_binding_id": "22222222-3333-4333-8444-666666666666",
        "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
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
    # `EXPORT-RANGE-001`: one day is first == last and a count of one, as the server now answers.
    "business_date_to": EXPORT_BUSINESS_DATE,
    "window_days": 1,
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


#: `MESSAGE-DRAFT-BINDING-001`. A `SEND_MESSAGE` envelope, and the read that makes it decidable.
#:
#: Until this item the card said the message body was stored nowhere and kept both buttons shut
#: for good, and nothing in the console could raise the envelope in the first place. Section 15
#: checks both halves: the words are on the card above the approve control, and step 0 of the
#: manual-send panel raises the envelope from the server's read with nothing typed.
MESSAGE_APPROVAL = "aaaaaaaa-bbbb-4333-8444-dddddddddddd"
MESSAGE_DRAFT_ID = "bbbbbbbb-cccc-4333-8444-eeeeeeeeeeee"
MESSAGE_RECIPIENT = "cccccccc-dddd-4333-8444-ffffffffffff"
MESSAGE_SNAPSHOT = "JCS-SHA256-V1:" + "5" * 64
MESSAGE_RENDERED = "JCS-SHA256-V1:" + "6" * 64
#: Untrusted text, and deliberately hostile: a draft is shaped by a customer's conversation. The
#: markup must arrive on screen as characters, never as an element.
MESSAGE_TEXT = (
    "Dạ, đồ của anh/chị đã giặt xong, mời anh/chị ghé tiệm nhận ạ.\n"
    '<img src=x onerror="window.__pwned = true"> Tiệm mở cửa đến 21 giờ.'
)

#: `CONSOLE-REDESIGN-005`. The envelope step 3 locks, and step 4 attests under If-Match.
MANUAL_ENVELOPE_ID = "ffffffff-3333-4333-8444-000000000001"

#: `CONSOLE-REDESIGN-005`, section 16. One undecided draft on `#/shadow`, hostile like the message
#: above: a model's words must reach the reviewer as characters.
SHADOW_DRAFT_ID = "abababab-cdcd-4333-8444-efefefefefef"
SHADOW_DRAFT_TEXT = 'Dạ, đồ xong rồi ạ. <b onmouseover="window.__pwned = true">Ghé lấy nhé</b>'

#: `CONSENT-TRANSACTIONAL-001` (`DEC-033`), section 19. The customer's own messages after the STOP,
#: newest first, as `release_evidence` lists them. The release must send the one the operator picks
#: from this list, value for value -- never an id the console composed or was typed.
SERVICE_EVIDENCE = [
    {
        "webhook_event_id": "dddddddd-1111-4333-8444-000000000001",
        "received_at": "2026-09-25T02:40:00+00:00",
    },
    {
        "webhook_event_id": "dddddddd-1111-4333-8444-000000000002",
        "received_at": "2026-09-25T02:10:00+00:00",
    },
]


def service_messaging(kind: str) -> dict[str, object]:
    """`ServiceMessagingStateResponse` for the recipient of `MESSAGE_BINDING`, in four states."""

    blocked = kind in {"SUPPRESSED", "SUPPRESSED_NO_EVIDENCE"}
    return {
        "store_id": STORE,
        "contact_binding_id": MESSAGE_RECIPIENT,
        "channel": "INTERNAL_TEST",
        "purpose": "TRANSACTIONAL",
        "transactional_state": "SUPPRESSED"
        if blocked
        else ("CLEAR" if kind == "CLEAR" else "NONE"),
        "blocked_since": "2026-09-25T02:00:00+00:00" if blocked else None,
        "releasable": blocked,
        "marketing_state": "SUPPRESSED" if blocked or kind == "CLEAR" else "NONE",
        "egress": (
            {
                "decision": "SUPPRESSED",
                "reason_code": "SUPPRESSED",
                "basis": None,
                "policy_version": 1,
                "suppression_state": "SUPPRESSED",
            }
            if blocked
            else {
                "decision": "ALLOW",
                "reason_code": None,
                "basis": "CUSTOMER_INITIATED",
                "policy_version": 1,
                "suppression_state": "CLEAR" if kind == "CLEAR" else "NONE",
            }
        ),
        "release_evidence": SERVICE_EVIDENCE if kind == "SUPPRESSED" else [],
    }


def egress_refusal(reason: str) -> dict[str, object]:
    """The 422 body `_egress_refusal_detail` sends for a refused service send."""

    return {
        "detail": {
            "outcome": "SUPPRESSED" if reason == "SUPPRESSED" else "REQUIRE_HUMAN",
            "reason_code": reason,
            "purpose": "TRANSACTIONAL",
            "suppression_state": "SUPPRESSED" if reason == "SUPPRESSED" else "NONE",
            "policy_version": None if reason == "MESSAGING_POLICY_UNPUBLISHED" else 1,
            "contact_binding_id": MESSAGE_RECIPIENT,
            "channel": "INTERNAL_TEST",
            "store_id": STORE,
            "decision": "DEC-033",
        }
    }


#: `MessageDraftBindingResponse` from `main.py`, field for field.
MESSAGE_BINDING = {
    "store_id": STORE,
    "action": "SEND_MESSAGE",
    "resource_type": "MESSAGE_DRAFT",
    "resource_id": MESSAGE_DRAFT_ID,
    "resource_version": 1,
    "text": MESSAGE_TEXT,
    "recipient_binding_id": MESSAGE_RECIPIENT,
    "snapshot_hash": MESSAGE_SNAPSHOT,
    "rendered_hash": MESSAGE_RENDERED,
    "policy_version": "manual-send-policy-v1",
}


#: SESSION-LIST-001 (section 20): this browser's session, another device of the same owner, and
#: the ORDER envelope whose order is `ORDER_VIEW` (row version 14).
SESSION_CURRENT = "5e551011-0000-4000-8000-000000000001"
SESSION_OTHER = "5e551011-0000-4000-8000-000000000002"
ORDER_APPROVAL = "0a0a0a0a-1111-4333-8444-555555555555"
STAFF_DEVICE_IDS = ("5e551011-0000-4000-8000-0000000000a1", "5e551011-0000-4000-8000-0000000000a2")


def device(session_id: str, current: bool, last_seen: str) -> dict[str, object]:
    """One `StaffSessionEntryResponse`: timestamps and an identifier, never a secret or a hash."""

    return {
        "session_id": session_id,
        "issued_at": "2026-09-25T01:00:00+00:00",
        "last_seen_at": last_seen,
        "idle_expires_at": "2026-09-25T11:00:00+00:00",
        "absolute_expires_at": "2026-09-26T01:00:00+00:00",
        "current": current,
    }


def order_queue_item() -> dict[str, object]:
    """An `ACCEPT_ORDER` envelope over `ORDER_VIEW`, binding whichever version section 20 sets."""

    return {
        "approval_request_id": ORDER_APPROVAL,
        "status": "REQUESTED",
        "envelope_hash": "JCS-SHA256-V1:" + "7" * 64,
        "required_role": "OPS_APPROVER",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        "replayed": False,
        "resource_type": "ORDER",
        "resource_id": ORDER_VIEW_ID,
        "resource_version": state.get("order_envelope_version", 14),
        "snapshot_hash": "JCS-SHA256-V1:" + "3" * 64,
        "rendered_hash": "JCS-SHA256-V1:" + "4" * 64,
        "action": "ACCEPT_ORDER",
        "store_id": None if state.get("order_envelope_storeless") else STORE,
    }


def message_queue_item() -> dict[str, object]:
    """The envelope as `GET /internal/v1/approvals` returns it, with the store it belongs to."""

    return {
        "approval_request_id": MESSAGE_APPROVAL,
        "status": "REQUESTED",
        "envelope_hash": "JCS-SHA256-V1:" + "7" * 64,
        "required_role": "OPS_APPROVER",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "replayed": False,
        "resource_type": "MESSAGE_DRAFT",
        "resource_id": MESSAGE_DRAFT_ID,
        "resource_version": 1,
        "snapshot_hash": MESSAGE_SNAPSHOT,
        "rendered_hash": MESSAGE_RENDERED,
        "action": "SEND_MESSAGE",
        "store_id": STORE,
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
    # SESSION-LIST-001: the session this cookie is. Section 20 reads it; nothing earlier does.
    "session_id": SESSION_CURRENT,
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
    # Section 15 only: the one order `GET /internal/v1/orders/{id}` and the store's order list
    # answer with. `None` leaves both on the catch-all, as every earlier section expects.
    "order_view": None,
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
        elif url.split("?")[0].endswith("/machines") and route.request.method == "GET":
            # SHOP-CAPTURE-001: empty unless a section asks for machines, so every earlier press
            # of Bắt đầu giặt still runs the step directly, as it did before the chooser existed.
            state.setdefault("machine_reads", []).append(url)
            body = {"store_id": STORE, "truncated": False, "machines": state.get("machines") or []}
        elif url.split("?")[0].endswith("/capture") and route.request.method == "GET":
            body = state.get("capture") or {
                **STUB_CAPTURE,
                "cycles": [],
                "legs": [],
                "suggested_vehicle": None,
                "weight_kg": None,
                "weight_basis": "UNKNOWN",
            }
        elif "/expenses" in url and route.request.method == "POST":
            state.setdefault("expense_writes", []).append(
                {
                    "path": url.split("?")[0],
                    "body": route.request.post_data,
                    "if_match": route.request.headers.get("if-match"),
                    "key": route.request.headers.get("idempotency-key"),
                }
            )
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(expense_month("2026-09")["lines"][2]),
            )
            return
        elif url.split("?")[0].endswith("/expenses") and route.request.method == "GET":
            from urllib.parse import parse_qs, urlsplit

            asked = parse_qs(urlsplit(url).query).get("month", ["2026-09"])[0]
            state.setdefault("expense_reads", []).append(asked)
            body = expense_month(asked)
        elif url.endswith("/internal/v1/stores"):
            body = {"store_ids": [STORE]}
        elif "/customer-privacy-notice" in url:
            body = NOTICE_PUBLISHED if state.get("notice_published") else NOTICE_UNPUBLISHED
        elif url.split("?")[0].endswith("/customers") and route.request.method == "POST":
            # CUSTOMER-001: what the sheet sends, under what key. Never a query string.
            state.setdefault("customer_posts", []).append(
                (
                    json.loads(route.request.post_data or "{}"),
                    route.request.headers.get("idempotency-key"),
                )
            )
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps({"customer": CUSTOMER_PROFILE, "replayed": False}),
            )
            return
        elif url.split("?")[0].endswith("/customers"):
            state.setdefault("customer_searches", []).append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q", [""])[0]
            body = {
                "store_id": STORE,
                "mode": "LAST4" if query == "3456" else "PHONE_INCOMPLETE",
                "limit": 20,
                "truncated": False,
                "customers": [CUSTOMER_SUMMARY] if query == "3456" else [],
            }
        elif "/contacts/recent" in url:
            state.setdefault("recent_reads", []).append(url)
            body = RECENT_CONTACTS
        elif "/service-messaging/release" in url and route.request.method == "POST":
            # `DEC-033`, section 19. Captured with its key: the property is that the evidence sent
            # is the one picked from the server's own list, under an idempotency key.
            state.setdefault("release_posts", []).append(
                (route.request.post_data, route.request.headers.get("idempotency-key"))
            )
            state["service_state"] = "CLEAR"
            sent = json.loads(route.request.post_data or "{}")
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {
                        "release_consent_event_id": "eeeeeeee-2222-4333-8444-000000000001",
                        "contact_binding_id": MESSAGE_RECIPIENT,
                        "channel": sent.get("channel"),
                        "purpose": "TRANSACTIONAL",
                        "previous_state": "SUPPRESSED",
                        "state": "CLEAR",
                        "evidence_webhook_event_id": sent.get("evidence_webhook_event_id"),
                        "released_at": "2026-09-25T03:00:00+00:00",
                        "replayed": False,
                    }
                ),
            )
            return
        elif "/service-messaging" in url:
            state.setdefault("service_reads", []).append(url)
            body = service_messaging(str(state.get("service_state") or "ALLOW"))
        elif url.split("?")[0].endswith("/manual-send") and route.request.method == "POST":
            # Only section 19 presses step 1; every refusal it reads is the server's structured one.
            reason = state.get("prepare_refusal")
            state.setdefault("prepare_posts", []).append(route.request.post_data)
            if reason:
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(egress_refusal(str(reason))),
                )
                return
            body = {
                "manual_send_envelope_id": MANUAL_ENVELOPE_ID,
                "approval_request_id": MESSAGE_APPROVAL,
                "status": "APPROVED_FOR_MANUAL_SEND",
                "recipient_binding_id": MESSAGE_RECIPIENT,
                "rendered_hash": MESSAGE_RENDERED,
                "row_version": 1,
                "replayed": False,
            }
            route.fulfill(status=201, content_type="application/json", body=json.dumps(body))
            return
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
        elif "/message-drafts/" in url and url.split("?")[0].endswith("/binding"):
            # The read section 15 exists for. Three answers: the envelope's own words, a draft a
            # reviewer edited since (revision 2, other digests), and a draft rejected since (404).
            state.setdefault("binding_reads", []).append(url)
            if state.get("message_rejected"):
                route.fulfill(
                    status=404,
                    content_type="application/json",
                    body=json.dumps({"detail": "no sendable message draft"}),
                )
                return
            if state.get("message_edited"):
                body = {
                    **MESSAGE_BINDING,
                    "resource_version": 2,
                    "text": "Dạ, tiệm giao tận nơi trong hôm nay ạ.",
                    "snapshot_hash": "JCS-SHA256-V1:" + "8" * 64,
                    "rendered_hash": "JCS-SHA256-V1:" + "9" * 64,
                }
            else:
                body = MESSAGE_BINDING
            # MANUAL-SEND-RESUME, section 16: how far the latest send over the draft has gone.
            body = {**body, "send_progress": state.get("send_progress")}
        elif "/remedy-proposals/" in url and url.split("?")[0].endswith("/approval-binding"):
            # The read section 18 exists for: the owner's view of one `APPROVE_REMEDY` envelope.
            # Stale means the order's quote digest moved under the envelope, which the server
            # reports both as a different `snapshot_hash` and as `envelope_matches: false`.
            state.setdefault("remedy_binding_reads", []).append(url)
            body = owner_remedy_binding()
            if state.get("remedy_stale"):
                body = {
                    **body,
                    "snapshot_hash": "JCS-SHA256-V1:" + "4" * 64,
                    "envelope_matches": False,
                }
        elif url.split("?")[0].endswith("/attest") and route.request.method == "POST":
            # Step 4 of the manual send. Captured with its headers: the property is that the
            # attestation carries the envelope's row version as a strong-quoted If-Match and an
            # idempotency key, and sends back exactly the values step 3 returned.
            state.setdefault("attest_posts", []).append(
                (
                    route.request.post_data,
                    route.request.headers.get("if-match"),
                    route.request.headers.get("idempotency-key"),
                    url,
                )
            )
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {
                        "manual_send_envelope_id": MANUAL_ENVELOPE_ID,
                        "approval_request_id": MESSAGE_APPROVAL,
                        "status": "MANUAL_SEND_RECORDED",
                        "recipient_binding_id": MESSAGE_RECIPIENT,
                        "rendered_hash": MESSAGE_RENDERED,
                        "row_version": 2,
                        "replayed": False,
                    }
                ),
            )
            return
        elif (
            "/shadow/drafts/" in url
            and url.split("?")[0].endswith("/decision")
            and route.request.method == "POST"
        ):
            # A reviewer's decision on one draft. Captured: the property is what the three
            # actions send, and that the client-side checks stop an illegal payload before this.
            state.setdefault("shadow_decisions", []).append(route.request.post_data)
            sent = json.loads(route.request.post_data or "{}")
            state["drafts_listed"] = False
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "review_id": "dddddddd-eeee-4333-8444-999999999999",
                        "agent_run_id": SHADOW_DRAFT_ID,
                        "decision": sent.get("decision"),
                        "decided_by_staff_id": SESSION_OK["staff_user_id"],
                        "decided_at": "2026-09-25T03:10:00+00:00",
                    }
                ),
            )
            return
        elif "/shadow/drafts" in url and state.get("drafts_listed"):
            body = [
                {
                    "agent_run_id": SHADOW_DRAFT_ID,
                    "conversation_binding_id": "ffffffff-1111-4333-8444-222222222222",
                    "terminal_outcome": "REQUIRE_HUMAN",
                    "terminal_code": "DRAFT_REQUIRES_HUMAN",
                    "tool_call_count": 2,
                    "produced_at": "2026-09-25T03:05:00+00:00",
                    "draft_text": SHADOW_DRAFT_TEXT,
                }
            ]
        elif "/shadow/reviews" in url and state.get("reviews_listed"):
            # Two decided drafts: one a person approved, which may now be asked to send, and one
            # they rejected, which has nothing sendable and must offer no such link.
            body = [
                {
                    "review_id": "dddddddd-eeee-4333-8444-111111111111",
                    "agent_run_id": MESSAGE_DRAFT_ID,
                    "decision": "APPROVE",
                    "reason_code": None,
                    "edited_text": None,
                    "decided_by_staff_id": SESSION_OK["staff_user_id"],
                    "decided_at": "2026-09-25T03:00:00+00:00",
                    "draft_text": "Dạ, đồ của anh/chị đã giặt xong ạ.",
                    "terminal_outcome": "DRAFT",
                    "terminal_code": "DRAFT_REQUIRES_HUMAN",
                    "produced_at": "2026-09-25T02:59:00+00:00",
                },
                {
                    "review_id": "dddddddd-eeee-4333-8444-222222222222",
                    "agent_run_id": "eeeeeeee-ffff-4333-8444-333333333333",
                    "decision": "REJECT",
                    "reason_code": "TONE_NOT_APPROPRIATE",
                    "edited_text": None,
                    "decided_by_staff_id": SESSION_OK["staff_user_id"],
                    "decided_at": "2026-09-25T02:00:00+00:00",
                    "draft_text": "Mai lấy nhé.",
                    "terminal_outcome": "DRAFT",
                    "terminal_code": "DRAFT_REQUIRES_HUMAN",
                    "produced_at": "2026-09-25T01:59:00+00:00",
                },
            ]
        elif "/internal/v1/approvals/" in url and url.endswith("/decisions"):
            # Captured, because the property is what the press sends back: the envelope's own
            # version and digests, exactly as the queue gave them.
            state.setdefault("decision_posts", []).append(route.request.post_data)
            sent = json.loads(route.request.post_data or "{}")
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "approval_request_id": MESSAGE_APPROVAL,
                        "status": sent.get("decision", "APPROVED"),
                        "envelope_hash": "JCS-SHA256-V1:" + "7" * 64,
                        "required_role": "OPS_APPROVER",
                        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                        "replayed": False,
                    }
                ),
            )
            return
        elif (
            url.split("?")[0].endswith("/internal/v1/approvals") and route.request.method == "POST"
        ):
            # Step 0 of the manual-send panel raising the `SEND_MESSAGE` envelope.
            state.setdefault("approval_posts", []).append(route.request.post_data)
            route.fulfill(
                status=201,
                content_type="application/json",
                body=json.dumps(
                    {
                        "approval_request_id": MESSAGE_APPROVAL,
                        "status": "REQUESTED",
                        "envelope_hash": "JCS-SHA256-V1:" + "7" * 64,
                        "required_role": "OPS_APPROVER",
                        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                        "replayed": False,
                    }
                ),
            )
            return
        elif url.split("?")[0].endswith("/internal/v1/approvals"):
            if state.get("order_listed"):
                body = [order_queue_item()]
            elif state.get("remedy_listed"):
                body = [owner_remedy_queue_item()]
            elif state.get("message_listed"):
                body = [message_queue_item()]
            elif state.get("export_listed"):
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
        elif "/orders?" in url and "ticket=" in url and route.request.method == "GET":
            # CONSOLE-REDESIGN-004: the complaint sheet finds the order by its ticket number.
            state.setdefault("ticket_reads", []).append(url)
            body = [TICKET_ORDER] if "ticket=17" in url else []
        elif "remedy-options" in url:
            state.setdefault("options_reads", []).append(url)
            # A pure read. Nothing is written and nothing is reserved by asking, which is what lets
            # the console put the ceiling and the owner requirement on screen before anything is
            # typed rather than after a round trip that commits something.
            body = REMEDY_OPTIONS_SHIRTS if state.get("remedy_shirts") else REMEDY_OPTIONS
        elif "remedy-proposals" in url and url.endswith("/execution"):
            # The owner-approval branch, both halves. Until the envelope is decided the server
            # refuses with a machine-readable reason and no `outcome` key at all -- the shape that
            # used to reach this console as "Du lieu nhap khong hop le".
            state.setdefault("execution_posts", []).append(
                (url, route.request.headers.get("idempotency-key"))
            )
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
        elif "remedy-proposals" in url and route.request.method == "GET":
            # READ-PATHS-001. Empty until section 15 asks for rows, so section 11's checks about
            # what *its own* proposal and execution put on screen are not answered by this list.
            state.setdefault("proposal_reads", []).append(url)
            body = (
                executable_recorded_proposals()
                if state.get("recorded_executable")
                else RECORDED_PROPOSALS
                if state.get("recorded_listed")
                else {**RECORDED_PROPOSALS, "proposals": []}
            )
            # CONSOLE-REDESIGN-004: a proposal is carried out from its row in this list, never from
            # the answer to the proposal, so what section 11 proposed has to come back here -- with
            # the next step the server would give it at the time of the read.
            if state.get("session_proposals"):
                body = {
                    **body,
                    "proposals": [
                        *body["proposals"],
                        *[
                            {**row, "next_step": state.get("session_next_step", "AWAIT_OWNER")}
                            for row in state["session_proposals"]
                        ],
                    ],
                }
        elif "/orders/" in url and url.split("?")[0].endswith("/incidents"):
            # READ-ENRICH-001: the order page's "Khiếu nại" section, read from the order's store.
            # The summary is hostile on purpose: it is customer-reported text and must stay text.
            state.setdefault("order_incident_reads", []).append(url)
            body = [
                {
                    **INCIDENTS[0],
                    "order_id": ORDER_VIEW_ID,
                    "evidence_summary": ORDER_INCIDENT_SUMMARY,
                }
            ]
        elif "/internal/v1/orders/" in url and route.request.method == "POST":
            # CONSOLE-REDESIGN-002: every write the order page makes, with the headers that make it
            # safe -- an Idempotency-Key on each, a strong-quoted If-Match where the route is CAS.
            path = url.split("?")[0]
            state.setdefault("order_writes", []).append(
                {
                    "path": path.split("/internal/v1/orders/")[1],
                    "body": route.request.post_data,
                    "if_match": route.request.headers.get("if-match"),
                    "key": route.request.headers.get("idempotency-key"),
                }
            )
            reply = state.get("order_write_reply")
            if reply is not None:
                route.fulfill(
                    status=reply[0], content_type="application/json", body=json.dumps(reply[1])
                )
                return
            if path.endswith("/collection"):
                route.fulfill(
                    status=201,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "collection_id": "12121212-3434-4333-8444-565656565656",
                            "order_id": PICKUP_ORDER_ID,
                            "settlement_id": "78787878-9090-4333-8444-121212121212",
                            "collected_by_staff_id": "11111111-aaaa-4333-8444-555555555555",
                            "collected_at": "2026-09-25T09:00:00+00:00",
                            "self_collection_recorded": True,
                            "row_version": 15,
                            "replayed": False,
                        }
                    ),
                )
                return
            route.fulfill(status=200, content_type="application/json", body="{}")
            return
        elif "/orders/" in url and "/remedy-credits" in url:
            state.setdefault("credit_reads", []).append(url)
            body = ORDER_CREDITS
        elif (
            "/internal/v1/sessions/" in url
            and url.split("?")[0].endswith("/revoke")
            and route.request.method == "POST"
        ):
            # SESSION-LIST-001, section 20: one device signed out. Captured with its key; a 404 is
            # the route's own answer for a session that is already gone.
            state.setdefault("revoke_posts", []).append(
                (url.split("?")[0], route.request.headers.get("idempotency-key"))
            )
            if state.get("revoke_status") == 404:
                route.fulfill(
                    status=404,
                    content_type="application/json",
                    body=json.dumps({"detail": "session unavailable"}),
                )
                return
            gone = url.split("/internal/v1/sessions/")[1].split("/")[0]
            state["sessions_live"] = [
                row for row in state.get("sessions_live", []) if row["session_id"] != gone
            ]
            route.fulfill(status=204, content_type="application/json", body="")
            return
        elif url.split("?")[0].endswith("/internal/v1/sessions") or (
            "/internal/v1/staff/" in url and url.split("?")[0].endswith("/sessions")
        ):
            state.setdefault("session_reads", []).append(url)
            owner_view = "/internal/v1/staff/" in url
            body = {
                "staff_user_id": STAFF_ACTIVE_ID if owner_view else SESSION_OK["staff_user_id"],
                "truncated": False,
                # Another person's devices are never this browser's session.
                "sessions": (
                    [
                        device(STAFF_DEVICE_IDS[0], False, "2026-09-25T02:00:00+00:00"),
                        device(STAFF_DEVICE_IDS[1], False, "2026-09-24T09:00:00+00:00"),
                    ]
                    if owner_view
                    else state.get("sessions_live", [])
                ),
            }
        elif "/internal/v1/staff" in url and route.request.method != "GET":
            # CONSOLE-REDESIGN-006: the person sheet's writes, captured with their keys so section
            # 17 can prove each carries an Idempotency-Key and names the person the owner tapped.
            state.setdefault("staff_writes", []).append(
                (
                    route.request.method,
                    url.split("/internal/v1/")[1],
                    route.request.post_data,
                    route.request.headers.get("idempotency-key"),
                )
            )
            if url.split("?")[0].endswith("/internal/v1/staff"):
                route.fulfill(
                    status=201,
                    content_type="application/json",
                    body=json.dumps({"staff_user_id": STAFF_NEW_ID}),
                )
            else:
                route.fulfill(status=204, content_type="application/json", body="")
            return
        elif "/stores/" in url and url.split("?")[0].endswith("/staff"):
            state.setdefault("staff_reads", []).append(url)
            body = STAFF_DIRECTORY
        elif url.split("?")[0].endswith(f"/internal/v1/orders/{ORDER_VIEW_ID}"):
            state.setdefault("order_view_reads", []).append(url)
            if state.get("order_read_fails"):
                # Section 20: an order that cannot be read. The card must not guess it unchanged.
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "operations unavailable"}),
                )
                return
            body = ORDER_VIEW
        elif url.split("?")[0].endswith(f"/internal/v1/orders/{RECEIPT_ORDER_ID}"):
            body = state.get("receipt_order") or RECEIPT_ORDER
        elif f"/quotes/{RECEIPT_QUOTE_ID}" in url:
            # Section 20. Recorded, so the check can see the receipt asked for the bound revision.
            state.setdefault("receipt_quote_reads", []).append(url)
            if state.get("receipt_quote_fails"):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "operations unavailable"}),
                )
                return
            body = RECEIPT_DETAIL
        elif "remedy-proposals" in url and route.request.method == "POST":
            # Captured rather than merely answered: the property section 11 proves is that the
            # body carries exactly the keys this kind owns and no ceiling of its own. A stub that
            # only returned 201 would certify a console that sends anything at all.
            state.setdefault("remedy_posts", []).append(route.request.post_data)
            sent = json.loads(route.request.post_data or "{}")
            state.setdefault("session_proposals", []).append(
                {
                    "proposal_id": REMEDY_PROPOSAL_ID,
                    "kind": sent.get("kind"),
                    "status": "OWNER_APPROVAL_REQUIRED",
                    "amount_vnd": sent.get("amount_vnd"),
                    "ceiling_vnd": 600_000,
                    "order_line_id": sent.get("order_line_id"),
                    "attested_late_by_minutes": None,
                    "window_closes_at": REMEDY_OPTIONS["defect_window_closes_at"],
                    "approval_id": REMEDY_APPROVAL_ID,
                    "approval_status": "REQUESTED",
                    "approval_expires_at": (datetime.now(UTC) + timedelta(hours=20)).isoformat(),
                    "approval_lapsed": False,
                    "proposed_by": "00000000-0000-4000-8000-0000000000cc",
                    "proposed_by_name": "Demo Chủ tiệm",
                    "proposed_at": datetime.now(UTC).isoformat(),
                    "executed_at": None,
                    "credit_id": None,
                }
            )
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
            path = url.split("?")[0]
            tail = path.rsplit("/incidents/", 1)[-1] if "/incidents/" in path else ""
            if tail and "/" not in tail:
                # CONSOLE-REDESIGN-004: `GET /stores/{store}/incidents/{id}`, the detail page's own
                # read. Captured so section 9 can prove the page read the incident it was sent to.
                state.setdefault("incident_reads", []).append(url)
                found = [item for item in INCIDENTS if item["incident_id"] == tail]
                if not found:
                    route.fulfill(
                        status=404,
                        content_type="application/json",
                        body=json.dumps({"detail": "incident not found"}),
                    )
                    return
                body = found[0]
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
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
            if "after_order_id=" in url:
                # PROMISE-001: the keyset is the due instant, handed back as the server gave it.
                state["sla_after_due"] = "after_due_at=" in url
        elif "/reports/summary" in url or "/reports/daily" in url:
            # REPORT-DASHBOARD-001: every read is recorded, so section 19 can prove a refused
            # window and a refused role never reached the server.
            state.setdefault("report_reads", []).append(url)
            body = report_body(url, daily="/reports/daily" in url)
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
            # Captured with its key: section 7 checks what the flow sends, and that a refused
            # press followed by another press is one intent under one key.
            state.setdefault("order_posts", []).append(
                {
                    "key": route.request.headers.get("idempotency-key"),
                    "body": json.loads(route.request.post_data or "{}"),
                }
            )
            if state.pop("order_refuse_once", False):
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "detail": {
                                "outcome": "REQUIRE_HUMAN",
                                "reason_codes": ["CAPACITY_UNVERIFIED"],
                            }
                        }
                    ),
                )
                return
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
                # CONTACT-PICK-001: which binding each intake was opened for, and under what key.
                state.setdefault("intake_posts", []).append(
                    (
                        json.loads(route.request.post_data or "{}"),
                        route.request.headers.get("idempotency-key"),
                    )
                )
                sent = json.loads(route.request.post_data or "{}")
                # CUSTOMER-001: an intake for a customer record is answered with the ticket the
                # server issued for it in the same write.
                route.fulfill(
                    status=201,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            **ORDER_REQUEST_CREATED,
                            "customer_id": sent["customer_id"],
                            "ticket_number": 31,
                            "ticket_issued_on": "2026-09-25",
                        }
                        if sent.get("customer_id")
                        else ORDER_REQUEST_CREATED
                    ),
                )
                return
            body = [ORDER_REQUEST]
        elif "/acceptance" in url and route.request.method == "POST":
            state.setdefault("accept_posts", []).append(
                {
                    "key": route.request.headers.get("idempotency-key"),
                    "body": json.loads(route.request.post_data or "{}"),
                }
            )
            if state.pop("accept_fail_once", False):
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "operations unavailable"}),
                )
                return
            route.fulfill(
                status=201, content_type="application/json", body=json.dumps(ACCEPTED_REVISION)
            )
            return
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
            if EXACT_QUOTE in url:
                body = exact_detail(2 if "revision=2" in url else 1)
            elif "revision=2" in url:
                body = {
                    **BAND_DETAIL,
                    **{
                        key: CLOSED_REVISION[key]
                        for key in (
                            "revision",
                            "row_version",
                            "finality",
                            "status",
                            "snapshot_hash",
                            "display_total_min_vnd",
                            "display_total_max_vnd",
                        )
                    },
                    "lines": [
                        {
                            **BAND_DETAIL["lines"][0],  # type: ignore[index]
                            "price_kind": "EXACT",
                            "net_amount_vnd": CHOSEN_VND,
                            "band_minimum_vnd": None,
                            "band_maximum_vnd": None,
                        }
                    ],
                }
            else:
                body = BAND_DETAIL
        elif url.split("?")[0].endswith("/quotes") and route.request.method == "POST":
            sent = json.loads(route.request.post_data or "{}")
            state.setdefault("quote_posts", []).append(sent)
            banded = any(
                line.get("service_code") == "DC_AO_DAI_TRADITIONAL"
                for line in sent.get("lines", [])
            )
            if not banded:
                # An ordinary weight-priced line: the engine prices it.
                route.fulfill(
                    status=201, content_type="application/json", body=json.dumps(EXACT_REVISION)
                )
                return
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
        elif route.request.method == "GET" and url.split("?")[0].endswith("/promise"):
            # PROMISE-001: the order's promise read, as the server answers it -- unpublished by
            # default, the published shapes set by section 15's promise checks.
            state.setdefault("promise_reads", []).append(url)
            body = state.get("promise_read") or promise_read(published=False)
        elif (
            state.get("order_view") is not None
            and route.request.method == "GET"
            and url.split("?")[0].endswith(f"/internal/v1/orders/{PICKUP_ORDER_ID}")
        ):
            body = state["order_view"]
        elif (
            state.get("order_view") is not None
            and route.request.method == "GET"
            and url.split("?")[0].endswith(f"/stores/{STORE}/orders")
        ):
            body = [state["order_view"]]
        else:
            body = []
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/internal/**", route_api)
    page.goto(f"http://localhost:{PORT}/#/new", wait_until="networkidle")
    page.wait_for_timeout(1200)
    # CONSOLE-REDESIGN-001: pricing happens inside Nhận đồ. One press issues the ticket and opens
    # the intake; the service is then picked from a sheet by its published name.
    page.locator("#new-walk-in").click()
    page.wait_for_timeout(1200)

    print("=" * 74)
    print("1. THE SERVICE PICKER — chosen by name, never typed as a code")
    print("=" * 74)

    check(
        "one press issued the ticket and bound the intake, and the number is on screen",
        "Phiếu 1" in (page.locator("#new-ticket").text_content() or ""),
        page.locator("#new-ticket").text_content()
        if page.locator("#new-ticket").count()
        else "absent",
    )
    check(
        "there is no field to type a service code into",
        page.locator("#new-lines input[name='service_code'], #new-lines select").count() == 0,
    )
    page.locator("#new-add-line").click()
    page.wait_for_timeout(400)
    picker = page.locator("#new-picker")
    check(
        "the picker offers the published services by their Vietnamese names",
        picker.locator("[data-code='STD_WASH_DRY_LT6']", has_text="Giặt sấy dưới 6kg").count() == 1,
        f"{picker.locator('[data-code]').count()} services",
    )
    check(
        "grouped under Vietnamese category headings, not pricebook tokens",
        picker.locator("h3", has_text="Giặt sấy theo ký").count() == 1
        and "standard_weight" not in (picker.inner_text() or ""),
        repr(picker.locator("h3").all_inner_texts()),
    )
    page.locator("#new-picker-search").fill("ao dai")
    page.wait_for_timeout(200)
    check(
        "searching folds Vietnamese, so 'ao dai' finds Áo dài and hides the washes",
        picker.locator("[data-code='DC_AO_DAI_TRADITIONAL']").count() == 1
        and picker.locator("[data-code='STD_WASH_DRY_LT6']").count() == 0,
    )
    page.locator("#new-picker-search").fill("")
    page.wait_for_timeout(200)
    picker.locator("[data-code='STD_WASH_DRY_LT6']").click()
    page.wait_for_timeout(400)

    check(
        "choosing by name is what sets the code the server will receive",
        page.locator("#new-lines [data-service='STD_WASH_DRY_LT6']").count() == 1,
    )
    check(
        "and the unit follows the chosen service, as a stated fact not a second choice",
        (page.locator("#new-lines .stepper__unit").first.text_content() or "") == "kg"
        and page.locator("#new-lines select[aria-label='Đơn vị']").count() == 0,
        repr(page.locator("#new-lines .stepper__unit").first.text_content()),
    )

    print()
    print("=" * 74)
    print("2. THE 6KG NOTICE — must follow the typed weight without stealing focus")
    print("=" * 74)

    check(
        "picking a service puts the cursor in its quantity: the next thing typed is the weight",
        page.evaluate("document.activeElement?.id") == "new-line-0-qty",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )
    qty = page.locator("#new-line-0-qty")
    page.keyboard.type("3", delay=12)
    page.wait_for_timeout(200)
    check("no cliff notice at 3 kg", "Gần ngưỡng 6kg" not in page.content())

    page.keyboard.press("Backspace")
    page.keyboard.type("5.9", delay=12)
    page.wait_for_timeout(250)
    check("the cliff notice appears at 5.9 kg", "Gần ngưỡng 6kg" in page.content())
    check(
        "and focus stayed in the quantity field while it appeared",
        page.evaluate("document.activeElement?.id") == "new-line-0-qty",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )
    check("the typed quantity is intact", qty.input_value() == "5.9", repr(qty.input_value()))

    print()
    print("=" * 74)
    print("3. SESSION ENDS MID-FORM — the operator's work must survive")
    print("=" * 74)

    # Every subsequent call now 401s, exactly as an eight-hour idle timeout would.
    state["authenticated"] = False
    page.locator("#new-price").click()
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
    check(
        "the customer's ticket is still the one on screen",
        "Phiếu 1" in (page.locator("#new-ticket").text_content() or ""),
    )
    check(
        "the service the operator picked is still selected",
        page.locator("#new-lines [data-service='STD_WASH_DRY_LT6']").count() == 1,
    )
    check(
        "the quantity is still there",
        page.locator("#new-line-0-qty").input_value() == "5.9",
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
    print("5. TIẾP NHẬN — real keystrokes, then resuming a waiting customer with no typing at all")
    print("=" * 74)

    # CONTACT-PICK-001: a returning channel customer is one tap in "Khách nhắn tin gần đây", and
    # the typing path below is folded under "Nhập mã thủ công" -- still proved keystroke by
    # keystroke, because this file exists for the form that rebuilt itself on every keystroke.
    page.goto(f"http://localhost:{PORT}/#/new")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)
    recent_row = page.locator(f"#new-recent [data-contact='{RECENT_CONTACT}']")
    recent_text = recent_row.first.inner_text() if recent_row.count() else ""
    check(
        "a returning channel customer is listed by channel, last order and total -- no code shown",
        recent_row.count() == 1
        and "Zalo" in recent_text
        and "Đang giặt" in recent_text
        and "120.000" in recent_text
        and RECENT_CONTACT not in (page.locator("main").inner_text() or ""),
        recent_text.replace("\n", " | "),
    )
    fold = page.locator("details:has(#new-contact)")
    check(
        "typing a customer code survives only folded under 'Nhập mã thủ công'",
        fold.count() == 1
        and fold.first.get_attribute("open") is None
        and not page.locator("#new-contact").is_visible(),
    )
    state["intake_posts"] = []  # earlier sections open walk-in intakes; only this tap counts here
    recent_row.first.click()
    page.wait_for_timeout(1200)
    posts = state.get("intake_posts", [])
    check(
        "one tap opens the intake for exactly that binding, under an idempotency key",
        len(posts) == 1
        and posts[0][0] == {"contact_binding_id": RECENT_CONTACT}
        and bool(posts[0][1]),
        posts,
    )
    check(
        "and the flow moves on to the bag",
        page.locator("#new-ticket").count() == 1 and page.locator("#new-add-line").count() == 1,
    )

    # The hand-off from a conversation: `#/new?contact=` opens the intake and lands on step 2, and
    # the address then names the intake so a reload resumes it instead of opening a second one.
    page.goto("about:blank")
    page.goto(
        f"http://localhost:{PORT}/#/new?contact={HANDED_OFF_CONTACT}", wait_until="networkidle"
    )
    page.wait_for_timeout(1500)
    posts = state.get("intake_posts", [])
    check(
        "#/new?contact= opens the intake for the binding the conversation named",
        len(posts) == 2 and posts[1][0] == {"contact_binding_id": HANDED_OFF_CONTACT},
        posts[1:],
    )
    check(
        "lands on step 2 and the address becomes ?request=<the intake>",
        page.locator("#new-add-line").count() == 1
        and page.url.endswith(f"#/new?request={ORDER_REQUEST['order_request_id']}"),
        page.url,
    )
    state["intake_posts"] = []

    page.goto(f"http://localhost:{PORT}/#/new")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)
    page.locator("#new-channel-toggle").click()
    page.wait_for_timeout(200)

    contact_input = page.locator("#new-contact")
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
        page.evaluate("document.activeElement?.id") == "new-contact",
        f"activeElement={page.evaluate('document.activeElement?.id')}",
    )
    check(
        "the screen says why a code is typed at all: only for a code read somewhere else",
        "Chỉ dùng khi có mã khách đọc được ở nơi khác." in page.content(),
    )

    page.locator("#new-contact-submit").click()
    page.wait_for_timeout(1200)
    check(
        "the intake is recorded and the flow moves on to the bag",
        page.locator("#new-ticket").count() == 1
        and "Khách nhắn qua kênh" in (page.locator("#new-ticket").text_content() or ""),
    )

    # Tiếp nhận is the history now: an intake that has not become an order resumes the flow.
    page.goto(f"http://localhost:{PORT}/#/order-requests")
    page.wait_for_timeout(1200)
    row = page.locator(f"#intake-list [data-request='{ORDER_REQUEST['order_request_id']}']")
    check(
        "the waiting intake is listed with its state in words",
        row.count() == 1 and "Đang chờ báo giá" in (row.first.text_content() or ""),
        row.first.text_content() if row.count() else "no row",
    )
    check(
        "and its row resumes Nhận đồ carrying the request id, not a form to fill in",
        row.count() == 1
        and (row.first.get_attribute("href") or "")
        == f"#/new?request={ORDER_REQUEST['order_request_id']}",
        row.first.get_attribute("href") if row.count() else "absent",
    )
    check(
        "no identifier is printed on the row",
        row.count() == 1
        and ORDER_REQUEST["order_request_id"][:8] not in (row.first.inner_text() or ""),
    )

    row.first.click()
    page.wait_for_timeout(1500)
    check(
        "resuming reads the intake from the server and lands on the bag, with no typing",
        page.locator("#new-ticket").count() == 1 and page.locator("#new-add-line").count() == 1,
        page.url,
    )

    # The honest not-found path: a request id the store does not have must say so, not pretend.
    state["request_missing"] = True
    page.evaluate(f"location.hash = '#/new?request={MISSING_REQUEST}'")
    page.wait_for_timeout(1500)
    missing = page.locator("#new-request-missing")
    check(
        "an unknown ?request= gets the honest not-found notice, nothing prefilled",
        missing.count() == 1
        and "Không tìm thấy yêu cầu này" in (missing.first.text_content() or "")
        and page.locator("#new-ticket").count() == 0,
        missing.first.text_content() if missing.count() else "no notice rendered",
    )
    state["request_missing"] = False

    print()
    print("=" * 74)
    print("6. HÔM NAY — the owner's morning, and what it refuses to claim")
    print("=" * 74)

    # Every list-shaped queue stub returns `[]`; the SLA board answers its fixture, whose first page
    # holds one order past the internal mark and has a next page. So this is the near-empty
    # morning: one queue with work, four clear -- and the old screen answered it with five cards
    # and a wall of footnotes.
    page.goto(f"http://localhost:{PORT}/#/", wait_until="networkidle")
    page.wait_for_timeout(1200)
    body = page.content()

    check(
        "the day's takings lead the screen, formatted as VND and not recomputed",
        "1.285.000" in body.replace("&nbsp;", " ") or "1.285.000 ₫" in body,
        repr(page.locator(".money-hero__amount").first.text_content()),
    )
    check(
        "the figure says how many settlements it is a sum of",
        "7 đơn đã tất toán hôm nay" in body,
    )
    check(
        "and names itself as money collected, never as doanh thu",
        "Đã thu tại quầy" in body and "đây không phải doanh thu" in body,
    )
    # Asked as "can the operator see it", not "does it carry a class". The first version of this
    # check counted `.tile--clear` elements and passed while all four tiles were still on screen:
    # the class was applied, and a later `display: grid` outranked the `display: none`. A class is
    # an intention; visibility is the claim. Since CONSOLE-REDESIGN-003 an empty queue renders no
    # row at all, so the claim is that no row for an empty queue is visible.
    empty_rows = [
        queue
        for queue in ("approvals", "drafts", "unknown-sends", "incidents")
        if any(
            page.locator(f"[data-queue='{queue}']").nth(i).is_visible()
            for i in range(page.locator(f"[data-queue='{queue}']").count())
        )
    ]
    check(
        "an empty queue takes no space instead of showing a zero row",
        not empty_rows,
        f"visible rows for empty queues: {empty_rows}",
    )
    sla_row = page.locator("[data-queue='sla']")
    check(
        "a queue with work is one row, its count at the right, a floor when the read has more",
        sla_row.count() == 1
        and sla_row.first.is_visible()
        and "1+" in (sla_row.first.inner_text() or "")
        and sla_row.first.get_attribute("href") == "#/sla-board",
        repr(sla_row.first.inner_text() if sla_row.count() else None),
    )
    clear = page.locator("[data-queue-clear]")
    clear_text = clear.first.inner_text() if clear.count() else ""
    check(
        "the all-clear line claims only what was checked, never that nothing is pending",
        clear.count() == 1
        and clear.first.get_attribute("data-queue-clear") == "some"
        and "Đang trống:" in clear_text
        and "chờ duyệt" in clear_text
        and "Không có gì chờ bạn" not in page.inner_text("main")
        and "Các hàng đợi đã kiểm đều đang trống." not in page.inner_text("main"),
        repr(clear_text),
    )
    # The scope note is for the reader it can matter to -- a session with more than one store --
    # and a one-store session is not shown it at all.
    scope_note = page.get_by_text("Mọi cửa hàng bạn được gán.")
    check(
        "a single-store session is not told which reads ignore the store picker",
        all(not scope_note.nth(i).is_visible() for i in range(scope_note.count())),
        f"{scope_note.count()} visible to a one-store session",
    )
    chips = page.locator("a.today__chip")
    check(
        "the day's orders are the server's counts, one tappable status each, into the order list",
        chips.count() == 2
        and chips.first.get_attribute("href") == "#/orders?status=ACTIVE"
        and "4" in (chips.first.inner_text() or "")
        and "Tổng 6 đơn" in page.inner_text("main"),
        repr([chips.nth(i).inner_text() for i in range(chips.count())]),
    )
    check(
        "the two counter actions are on the first screen: Nhận đồ and the pickup lookup",
        page.locator("a[href='#/new']", has_text="Nhận đồ").first.is_visible()
        and page.locator("a[href='#/orders?lookup=1']").first.is_visible(),
    )
    check(
        "the rule behind the takings travels with the figure, in the technical drawer",
        "collected-today-v2:c266d2f11377a64c" in body,
    )

    print()
    print("=" * 74)
    print("7. XÁC NHẬN — the acquisition source, then acceptance and the order, in that order")
    print("=" * 74)

    # ACQUISITION-ATTRIBUTION-001. The claim is not "a control exists". It is that a counter which
    # did not ask can leave the answer alone and be recorded as not knowing, without the screen
    # pushing back -- a claim about styling and default state no source-text test can make.
    def walk_to_confirm() -> None:
        page.goto(f"http://localhost:{PORT}/#/new")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1000)
        page.locator("#new-walk-in").click()
        page.wait_for_timeout(1000)
        page.locator("#new-add-line").click()
        page.wait_for_timeout(300)
        page.locator("#new-picker [data-code='STD_WASH_DRY_LT6']").click()
        page.wait_for_timeout(300)
        page.keyboard.type("5.9", delay=8)
        page.locator("#new-price").click()
        page.wait_for_timeout(1200)
        page.locator("#new-next").click()
        page.wait_for_timeout(500)

    state["accept_posts"] = []
    state["order_posts"] = []
    walk_to_confirm()

    source = page.locator("#new-source")
    chips = source.locator("button")
    pressed = source.locator("button[aria-pressed='true']")
    check("the confirmation asks where the customer came from", source.count() == 1)
    check(
        "and it rests on 'nobody asked' rather than on a plausible channel",
        pressed.count() == 1 and pressed.first.get_attribute("data-value") == "UNKNOWN",
        pressed.first.get_attribute("data-value") if pressed.count() else "none pressed",
    )
    check(
        "the resting option reads as an answer, in Vietnamese",
        pressed.count() == 1 and (pressed.first.text_content() or "").strip() == "Chưa biết",
    )
    check(
        "the send-reconciliation gloss did not leak into it",
        "chưa rõ kết quả" not in (source.text_content() or "").lower(),
        repr(source.text_content()),
    )
    check(
        "every source the enum offers is reachable in one tap",
        chips.count() == 9,
        f"{chips.count()} options",
    )
    check(
        "leaving it unanswered raises no warning state",
        source.get_attribute("aria-invalid") is None
        and pressed.first.get_attribute("data-state") is None
        and "warn" not in (source.get_attribute("class") or ""),
        f"class={source.get_attribute('class')}",
    )
    check(
        "the step warns that the entry is final: the order shows it back, never edits it",
        "không sửa được" in page.content(),
    )
    check(
        "nothing has been accepted or ordered by reaching this step",
        not state["accept_posts"] and not state["order_posts"],
    )

    source.locator("button[data-value='GOOGLE_MAPS']").click()
    # The order is refused once, as the server refuses one it cannot take; the acceptance before it
    # is recorded. The next press must resume at the order -- not accept a second time -- and be
    # the same intent under the same key.
    state["order_refuse_once"] = True
    page.locator("#new-confirm").click()
    page.wait_for_timeout(1500)
    check(
        "one press records the customer's acceptance first, of the revision on screen",
        len(state["accept_posts"]) == 1
        and state["accept_posts"][0]["body"]
        == {
            "expected_current_revision": 1,
            "expected_snapshot_hash": EXACT_REVISION["snapshot_hash"],
        },
        repr(state["accept_posts"][:1]),
    )
    check(
        "a refused order stops the flow there and says why, at the button",
        len(state["order_posts"]) == 1
        and "Cần người quyết định trước khi tạo đơn"
        in (page.locator("#new-confirm-result").inner_text() or "")
        and "#/new" in page.url,
        page.locator("#new-confirm-result").inner_text()[:160],
    )
    check(
        "and the button now names the one step left",
        (page.locator("#new-confirm").inner_text() or "").strip() == "Tạo đơn",
        repr(page.locator("#new-confirm").inner_text()),
    )
    page.locator("#new-confirm").click()
    page.wait_for_timeout(1500)
    orders = state["order_posts"]
    sent = orders[-1]["body"] if orders else {}
    check(
        "the next press resumes at the order: no second acceptance",
        len(state["accept_posts"]) == 1 and len(orders) == 2,
        f"{len(state['accept_posts'])} acceptance(s), {len(orders)} order post(s)",
    )
    check(
        "the retried order is the same intent under the same idempotency key",
        len(orders) == 2 and orders[0]["key"] and orders[0]["key"] == orders[1]["key"],
        repr([o["key"] for o in orders]),
    )
    check(
        "every value the order needs was carried from the server's answers, none typed",
        sent
        == {
            "bound_contact_id": CONTACT,
            "quote_id": EXACT_QUOTE,
            "quote_revision": 2,
            "quote_snapshot_hash": ACCEPTED_REVISION["snapshot_hash"],
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "acquisition_source": "GOOGLE_MAPS",
            "customer_final_quote_accepted_at": ACCEPTED_AT,
        },
        repr(sent),
    )
    check(
        "and the created order opens by itself, with the ticket in the confirmation",
        page.url.endswith(f"#/orders/{ORDER_CREATED['order_id']}")
        and "Đã tạo đơn · Phiếu 1" in page.content(),
        page.url,
    )
    # RECEIPT-PRINT-001: the customer is still at the counter, so the receipt is one tap away.
    handoff = page.locator("#order-created button[data-receipt]")
    check(
        "and the order it opens offers 'In phiếu cho khách' at once, not as the page's primary",
        handoff.count() == 1
        and "In phiếu cho khách" in (handoff.inner_text() or "")
        and handoff.get_attribute("data-variant") != "primary",
        f"{handoff.count()} offers",
    )

    # The assertion that matters most: what the *next* customer's confirmation says. A sticky
    # answer would have the console supply a plausible source nobody gave, on a field that is
    # immutable -- the failure `UNKNOWN` exists to prevent, committed by the software.
    walk_to_confirm()
    pressed = page.locator("#new-source button[aria-pressed='true']")
    check(
        "the next customer's confirmation is back on 'nobody asked', not on the last answer",
        pressed.count() == 1 and pressed.first.get_attribute("data-value") == "UNKNOWN",
        pressed.first.get_attribute("data-value") if pressed.count() else "none",
    )

    # A lost acceptance answer: the press may have landed, so the same key is sent again and the
    # server replays rather than records twice. Nothing retries by itself.
    state["accept_posts"] = []
    state["accept_fail_once"] = True
    page.locator("#new-confirm").click()
    page.wait_for_timeout(1200)
    check(
        "a lost acceptance answer is not retried by the console on its own",
        len(state["accept_posts"]) == 1 and "#/new" in page.url,
        f"{len(state['accept_posts'])} acceptance post(s)",
    )
    page.locator("#new-confirm").click()
    page.wait_for_timeout(1500)
    keys = [post["key"] for post in state["accept_posts"]]
    check(
        "and the person's second press carries the same key, so it cannot be recorded twice",
        len(keys) == 2 and keys[0] == keys[1],
        repr(keys),
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
    page.goto(f"http://localhost:{PORT}/#/new")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)

    ticket_button = page.locator("#new-walk-in")
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
    print("9. GHI KHIẾU NẠI — the order found by its ticket, the complaint as the customer said it")
    print("=" * 74)

    # Until DEC-028 this screen could not be completed by anybody: the request demanded
    # `contact_scope_hash` and `evidence_summary_hash` and nothing in the repository produced
    # either. CONSOLE-REDESIGN-004 then removed the last value a person had to paste: the order is
    # found by the number on the customer's ticket, and the id that is sent is the one the server's
    # own lookup returned. What is checked is unchanged in kind -- the body is exactly the two keys
    # the strict request model accepts, the complaint survives typing -- plus the zero-paste claim.
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
    check(
        "the list names a complaint by its ticket, not by an id",
        "Phiếu 17" in body_text and INCIDENTS[0]["incident_id"] not in body_text,
        body_text[:200].replace("\n", " "),
    )

    page.locator("#incident-create-open").click()
    page.wait_for_timeout(300)
    summary_box = page.locator("#incident-summary")
    check(
        "the complaint is typed into a textarea, not a hash field",
        summary_box.count() > 0 and summary_box.evaluate("n => n.tagName") == "TEXTAREA",
    )

    page.locator("#incident-ticket").click()
    page.keyboard.type("17", delay=12)
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    picked = page.locator("#incident-create .picked")
    check(
        "the ticket number finds the order, read from the selected store",
        any(f"/stores/{STORE}/orders?ticket=17" in url for url in state.get("ticket_reads", [])),
        repr(state.get("ticket_reads")),
    )
    check(
        "and the order is shown by its ticket, with no id to read or paste",
        picked.count() == 1
        and "Phiếu 17" in (picked.text_content() or "")
        and INCIDENTS[0]["order_id"] not in (picked.text_content() or ""),
        repr(picked.text_content()) if picked.count() else "nothing picked",
    )

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

    page.locator("#incident-submit").click()
    page.wait_for_timeout(900)

    posts = state.get("incident_posts") or []
    sent = json.loads(posts[-1]) if posts else {}
    check("the form actually submits", bool(posts), f"{len(posts)} POST(s)")
    check(
        "it sends exactly order_id and evidence_summary, and no hash",
        set(sent) == {"order_id", "evidence_summary"},
        f"keys={sorted(sent)}",
    )
    check(
        "the order sent is the one the ticket lookup returned -- nobody pasted it",
        sent.get("order_id") == INCIDENTS[0]["order_id"],
        f"got {sent.get('order_id')!r}",
    )
    check(
        "the complaint reaches the server as the words the staff member typed",
        sent.get("evidence_summary") == "Áo sơ mi trắng bị ố vàng ở cổ.",
        f"got {sent.get('evidence_summary')!r}",
    )
    check(
        "recording it opens the complaint's own page",
        page.evaluate("location.hash") == f"#/incidents/{INCIDENTS[0]['incident_id']}",
        page.evaluate("location.hash"),
    )
    page.wait_for_timeout(600)
    check(
        "which reads that incident from the selected store",
        any(
            f"/stores/{STORE}/incidents/{INCIDENTS[0]['incident_id']}" in url
            for url in state.get("incident_reads", [])
        ),
        repr(state.get("incident_reads")),
    )
    order_link = page.locator("[data-field=incident-order-link]")
    check(
        "and links its order by ticket, to the order page",
        order_link.count() == 1
        and (order_link.text_content() or "") == "Phiếu 17"
        and order_link.get_attribute("href") == f"#/orders/{INCIDENTS[0]['order_id']}",
        repr(order_link.text_content()) if order_link.count() else "absent",
    )
    detail_text = page.inner_text("main")
    check(
        "recording decides nothing: both flags read 'chưa quyết định'",
        detail_text.count("chưa quyết định") >= 2,
        detail_text[:200].replace("\n", " "),
    )

    # The order page's hand-off: the order arrives already chosen, shown by its ticket.
    page.evaluate(f"location.hash = '#/orders/{ORDER_VIEW_ID}'")
    page.wait_for_timeout(1000)
    # CONSOLE-REDESIGN-002: the order page's "Khiếu nại" section carries the press, as
    # "Ghi khiếu nại" -- the same words the complaint list uses for the same action.
    handoff = page.locator("button", has_text="Ghi khiếu nại")
    if handoff.count():
        handoff.first.click()
        page.wait_for_timeout(1000)
    picked = page.locator("#incident-create .picked")
    check(
        "the order page hands its order to the complaint sheet, by ticket",
        page.evaluate("location.hash") == "#/incidents"
        and picked.count() == 1
        and "Phiếu 17" in (picked.text_content() or "")
        and picked.get_attribute("data-order-id") == ORDER_VIEW_ID,
        repr(picked.text_content()) if picked.count() else "nothing picked",
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    print()
    print("=" * 74)
    print("10. KHOẢNG GIÁ — the counter closes a band, and is warned before it sends")
    print("=" * 74)

    # `RANGE-PRICE-001`. Twenty of the forty-four published services are priced by inspection, and
    # until this item none of them could be quoted at all. The whole path is here because every
    # step of it is a place the console could quietly decide money: choosing a number inside the
    # band, showing the top of an interval as a price, or treating an owner's approval as the
    # customer's agreement. Each is checked below as a refusal rather than as a feature.

    state["accept_posts"] = []
    state["order_posts"] = []
    page.goto(f"http://localhost:{PORT}/#/new")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1000)
    page.locator("#new-walk-in").click()
    page.wait_for_timeout(1000)
    page.locator("#new-add-line").click()
    page.wait_for_timeout(300)
    page.locator("#new-picker [data-code='DC_AO_DAI_TRADITIONAL']").click()
    page.wait_for_timeout(300)
    page.keyboard.type("1", delay=12)
    page.wait_for_timeout(150)

    page.locator("#new-price").click()
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
        and page.locator("#new-next").is_disabled()
        and "Chốt một giá trong khoảng ở trên trước" in content,
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
        page.locator("#new-next").count() == 1
        and not page.locator("#new-next").is_disabled()
        and not state["accept_posts"],
        "expected the way on to the acceptance step, and no acceptance sent by closing the band",
    )
    check(
        "and it is not yet an order",
        not state["order_posts"],
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
        "the old deep link forwards to the incident's own page, replacing itself in history",
        page.evaluate("location.hash") == f"#/incidents/{incident}",
        page.evaluate("location.hash"),
    )
    check(
        "which loads that incident's figures unasked -- there is no read button to press",
        any(f"/incidents/{incident}/remedy-options" in u for u in state.get("options_reads", []))
        and page.locator("button", has_text="Đọc mức trần").count() == 0,
        repr(state.get("options_reads")),
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
    def kind_pick(value: str) -> None:
        page.locator(f"#remedy-kind button[data-value='{value}']").click()

    check(
        "there is no money box until the kind that needs one is chosen",
        page.locator("#remedy-amount").count() == 0,
    )

    kind_pick("DAMAGE_COMPENSATION")
    page.wait_for_timeout(300)
    check(
        "the summary line -- ceiling, time left, owner -- sits above every number box",
        page.evaluate(
            """() => {
                 const summary = document.querySelector('#remedy-summary');
                 const line = document.querySelector('#remedy-line');
                 return Boolean(summary && line &&
                   (summary.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING));
               }"""
        ),
    )
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
            # `REMEDY-GARMENT-001`: a line of one garment names garment 1, so the row says which.
            "garment_index": 1,
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
    own_row = page.locator(
        f"#remedy-recorded-proposals li[data-proposal-id='{REMEDY_PROPOSAL_ID}']"
    )
    check(
        "the proposal is re-read into the incident's list, where it waits and offers no press",
        own_row.count() == 1
        and own_row.get_attribute("data-proposal-next-step") == "AWAIT_OWNER"
        and own_row.locator("button[data-remedy-execute]").count() == 0,
        repr(own_row.text_content()) if own_row.count() else "absent",
    )

    # The server marks it executable, and the execute route still re-checks and refuses (the
    # envelope moved between the read and the press). The refusal is a policy answer with a code,
    # and the sentence it must NOT produce is the one that tells a staff member their typing was
    # wrong. Nothing retries it: one press, one request.
    state["session_next_step"] = "EXECUTE"
    state["execution_posts"] = []
    page.evaluate("location.hash = '#/incidents'")
    page.wait_for_timeout(400)
    page.evaluate(f"location.hash = '#/incidents/{incident}'")
    page.wait_for_timeout(1200)
    page.locator("button[data-remedy-execute]").first.click()
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

    check(
        "a refused execution is sent once and not retried",
        len(state.get("execution_posts", [])) == 1,
        repr(state.get("execution_posts")),
    )

    state["owner_approved"] = True
    page.locator("button[data-remedy-execute]").first.click()
    page.wait_for_timeout(900)
    posts = state.get("execution_posts", [])
    check(
        "the second press reuses the first press's key, so the server can replay, not repeat",
        len(posts) == 2 and bool(posts[0][1]) and posts[0][1] == posts[1][1],
        repr(posts),
    )
    content = page.content()
    check(
        "once the owner has decided, the same press carries the remedy out",
        "Đã thực hiện" in content and "CREDIT_EXECUTED" in content,
    )
    check(
        "the credit id is shown in full with a copy control at the moment it is issued",
        REMEDY_CREDIT_ID in content and "Chép mã giảm trừ này lại ngay" in content,
    )

    # Loss, since `DEC-031`. Until 2026-09-25 this section asserted that loss rendered as an
    # unsupported capability with no figure at all. The ruling gave loss the damage ceiling and one
    # rule -- every loss claim needs the owner, whatever the amount -- so what is asserted now is
    # that the form never lets a loss read as something staff can approve.
    state["session_proposals"] = []
    state.pop("session_next_step", None)
    kind_pick("LOST_ITEM")
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

    # `REMEDY-GARMENT-001` (the DEC-031 addendum): on a line of three shirts each shirt has its own
    # 100.000 d staff limit and its own 250.000 d ceiling. Shirt #1 already carries 100.000 d, so
    # the same 100.000 d is the staff's on shirt #2 and the owner's on shirt #1 -- and the form must
    # say which before anything is sent, which only a browser can show.
    state["remedy_shirts"] = True
    page.evaluate("location.hash = '#/incidents'")
    page.wait_for_timeout(500)
    page.evaluate(f"location.hash = '#/remedies?incident={incident}'")
    page.wait_for_timeout(900)
    kind_pick("DAMAGE_COMPENSATION")
    page.wait_for_timeout(300)
    page.locator("#remedy-line").select_option(value="line-shirts")
    page.wait_for_timeout(300)
    content = page.content()
    check(
        "three shirts: the form asks which shirt before it offers a money box",
        page.locator("#remedy-garment").count() == 1
        and page.locator("#remedy-amount").count() == 0
        and "Chưa chọn món thứ mấy trên dòng này" in content,
        f"garment={page.locator('#remedy-garment').count()} "
        f"amount={page.locator('#remedy-amount').count()}",
    )
    check(
        "the shirt picker shows what each shirt already carries, and preselects none",
        "Món thứ 1 · đã ghi 100.000" in content
        and page.locator("#remedy-garment").input_value() == "",
        repr(page.locator("#remedy-garment").input_value()),
    )
    page.locator("#remedy-garment").select_option(value="2")
    page.wait_for_timeout(300)
    amount = page.locator("#remedy-amount")
    amount.click()
    page.keyboard.type("100000", delay=8)
    page.wait_for_timeout(300)
    check(
        "100.000 d on shirt #2 is the staff's to approve, whatever shirt #1 carries",
        "sẽ lập phiếu chờ chủ tiệm duyệt" not in page.content(),
    )
    page.locator("#remedy-garment").select_option(value="1")
    page.wait_for_timeout(300)
    content = page.content()
    check(
        "the same 100.000 d on shirt #1 is cumulative with its 100.000 d, and the owner's",
        "sẽ lập phiếu chờ chủ tiệm duyệt" in content and "Món thứ 1 đã ghi đền" in content,
    )
    check(
        "and changing the shirt kept what was typed",
        page.locator("#remedy-amount").input_value() == "100000",
        repr(page.locator("#remedy-amount").input_value()),
    )
    page.locator("#remedy-garment").select_option(value="2")
    page.locator("#remedy-fault").check()
    page.wait_for_timeout(300)
    page.locator("button", has_text="Gửi đề nghị bồi hoàn").first.click()
    page.wait_for_timeout(900)
    proposed = json.loads((state.get("remedy_posts") or ["{}"])[-1] or "{}")
    check(
        "the proposal names shirt #2 by its position on the line",
        proposed
        == {
            "kind": "DAMAGE_COMPENSATION",
            "store_fault_attested": True,
            "order_line_id": "line-shirts",
            "amount_vnd": 100_000,
            "garment_index": 2,
        },
        repr(proposed),
    )
    state["remedy_shirts"] = False
    state["session_proposals"] = []
    state.pop("session_next_step", None)
    state["owner_approved"] = False

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

    # CONSOLE-REDESIGN-003 (spec V2 §5.5): the queue is the first thing under the title -- only the
    # two-way switch between it and today's range prices sits in between -- and the screen's limits
    # are one ⓘ on the title, verbatim, not a panel of prose above or below the work.
    layout = page.evaluate(
        """() => {
          const head = document.querySelector(".approvals > .page-head");
          const next = head && head.nextElementSibling;
          const pane = next && next.nextElementSibling;
          return {
            switch: next ? next.getAttribute("role") + ":" + next.getAttribute("aria-label") : null,
            pane: pane ? pane.getAttribute("data-pane") : null,
            cardInPane: Boolean(pane && pane.querySelector("article.card")),
          };
        }"""
    )
    check(
        "the queue is the first thing under the title, behind one two-way switch",
        layout == {"switch": "group:Chọn danh sách", "pane": "queue", "cardInPane": True},
        repr(layout),
    )
    page.locator(".approvals .page-head .info-btn").first.click()
    page.wait_for_timeout(300)
    limits = page.locator("dialog.sheet[open]").first.inner_text()
    check(
        "the screen's limits are one tap away, verbatim",
        "Quyết định ở đây ghi thẳng vào máy chủ và không hoàn tác được." in limits
        and "Tại sao nút Duyệt đang tắt?" in limits
        and "Phê duyệt còn những quy tắc nào khác?" in limits,
        limits[:160],
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    page.locator(".approvals .segmented__option", has_text="Giá trong khoảng").click()
    page.wait_for_timeout(300)
    check(
        "the switch shows today's range prices and hides the queue, without a reload",
        page.locator("[data-pane='reviews']").is_visible()
        and not page.locator("[data-pane='queue']").is_visible()
        and "chưa có món nào được chốt giá trong khoảng" in page.inner_text("main"),
    )
    page.locator(".approvals .segmented__option", has_text="Chờ duyệt").click()
    page.wait_for_timeout(200)

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
    described = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          const approve = card && [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          const id = approve && approve.getAttribute("aria-describedby");
          const target = id && document.getElementById(id);
          return target ? target.textContent.includes("Khoá theo loại nội dung") : id || "none";
        }"""
    )
    check(
        "the shut control is described by the full reason, which lives in the title's ⓘ",
        described is True,
        repr(described),
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

    # Rows, since CONSOLE-REDESIGN-003: each order is a list row that links to it.
    cards = page.locator("[data-sla-outcome]")
    check("the board lists the orders the server returned", cards.count() == 2, str(cards.count()))
    # The server answers soonest due first (PROMISE-001) and the screen renders that order
    # unchanged. Asserted as the sequence the server sent, not as a property the browser derives.
    check(
        "the rows are in the server's order (soonest due), not one the browser chose",
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
        "time past the promise reads as a duration, never as a negative number",
        "trễ hẹn 3 giờ" in body_text and "-3" not in body_text,
    )
    # PROMISE-001: each row says which rule measured it -- the customer's promise or the shop's own
    # internal mark -- in words, and the token rides on the row.
    promised_row = page.locator("[data-rule-source='ORDER_PROMISE']")
    stated_row = page.locator("[data-rule-source='STATED_RULE']")
    check(
        "a row measured against the customer's promise says Hẹn and Trễ hẹn",
        promised_row.count() == 1
        and "Hẹn " in promised_row.first.inner_text()
        and "Trễ hẹn" in promised_row.first.inner_text(),
        promised_row.first.inner_text()[:120] if promised_row.count() else "no promised row",
    )
    check(
        "a row taken before the owner published the rules says Mốc nội bộ, not Hẹn",
        stated_row.count() == 1
        and "Mốc nội bộ" in stated_row.first.inner_text()
        and "Trễ hẹn" not in stated_row.first.inner_text(),
        stated_row.first.inner_text()[:120] if stated_row.count() else "no stated-rule row",
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
        "and says plainly, on the screen itself, which mark is a promise and which is internal",
        "“Hẹn” là giờ đã hẹn với khách" in body_text and "Mốc nội bộ" in body_text,
    )
    # The rule is one tap away (tier 2): open the ⓘ on the title, as a person would, and read it.
    page.locator(".page-head .info-btn").first.click()
    page.wait_for_timeout(300)
    sheet_text = page.locator("dialog.sheet[open]").first.inner_text()
    check(
        "the board states which rule produced its numbers, in the assistant's own words",
        SLA_BOARD_POLICY_NOTICE in sheet_text.replace("\n", " "),
        "the shared policy sentence is missing or reworded",
    )
    check(
        "and in which order the rows come, in the same sheet",
        "Đơn đến hạn sớm nhất nằm trên cùng" in sheet_text,
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check(
        "the versioned query behind the figures is on the screen (technical drawer), not only in "
        "the response",
        "sla-risk-board-v2:1f8c822c3e51aee1" in str(page.evaluate("document.body.textContent")),
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
        page.locator("[data-sla-outcome]").count() == 3,
        str(page.locator("[data-sla-outcome]").count()),
    )
    check(
        "and asks for it by the due instant the server handed back (PROMISE-001's keyset)",
        state.get("sla_after_due") is True,
        repr(state.get("sla_after_due")),
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
        "the business day the file is about is on the card, as a person reads a date",
        "16/09/2026" in text,
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

    # `EXPORT-RANGE-001`: a window. Both ends are inside what the owner signs, so both ends -- and
    # the server's day count -- are on the card in human form, above the control, and the card's
    # title names the window rather than one day.
    EXPORT_REQUEST_CONTENT["business_date_to"] = "2026-09-22"
    EXPORT_REQUEST_CONTENT["window_days"] = 7
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    text = rendered_text()
    check(
        "a window is on the export card as both ends and its day count",
        "16/09/2026 → 22/09/2026 · 7 ngày" in text and "Khoảng ngày" in text,
        text[:200],
    )
    card_text = page.evaluate(
        """() => (document.querySelector("article.card") || {innerText: ""}).innerText"""
    )
    heading = str(card_text).split("Dữ liệu bạn đang được đề nghị")[0]
    check(
        "and the card's title names the window, not only its first day",
        "22/09/2026" in heading,
        heading[:120],
    )
    check(
        "the approve sentence says no other day is released",
        "không ngày nào khác" in text,
    )
    window_ordering = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          const approve = card && [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          const window = card && [...card.querySelectorAll("dd, .fields *")]
            .find((n) => n.textContent.includes("22/09/2026"));
          if (!approve || !window) return "missing";
          const after = window.compareDocumentPosition(approve);
          return Boolean(after & Node.DOCUMENT_POSITION_FOLLOWING);
        }"""
    )
    check(
        "the window precedes the approve control in the document",
        window_ordering is True,
        repr(window_ordering),
    )
    EXPORT_REQUEST_CONTENT["business_date_to"] = EXPORT_BUSINESS_DATE
    EXPORT_REQUEST_CONTENT["window_days"] = 1
    state["export_listed"] = False

    # `EXPORT-RANGE-001`: the picker on #/exports. No window is chosen on arrival (a window nobody
    # chose is a window nobody answers for); "7 ngày" is today and the six days before it, cut in
    # the shop's timezone rather than the device's; "Tự chọn" offers the two ends as date fields.
    page.evaluate("location.hash = '#/exports'")
    page.wait_for_timeout(900)
    pressed = page.evaluate(
        """() => [...document.querySelectorAll("#export-range [aria-pressed='true']")].length"""
    )
    check("no range preset is pressed when the export screen opens", pressed == 0, repr(pressed))
    shop_today = datetime.now(UTC).astimezone(timezone(timedelta(hours=7))).date()
    week_start = shop_today - timedelta(days=6)
    page.locator("#export-range [data-value='7d']").click()
    page.wait_for_timeout(200)
    chosen = page.evaluate("() => document.querySelector('#export-window')?.textContent || ''")
    check(
        "'7 ngày' names today and the six days before it, in the shop's own days",
        chosen == f"{week_start:%d/%m/%Y} → {shop_today:%d/%m/%Y}",
        repr(chosen),
    )
    check(
        "and the picker keeps saying which event cuts the day",
        "ngày mở đơn" in rendered_text() and "không theo lúc thu tiền" in rendered_text(),
    )
    page.locator("#export-range [data-value='custom']").click()
    page.wait_for_timeout(200)
    check(
        "'Tự chọn' offers both ends as date fields, starting from the days already chosen",
        page.locator("#export-from").input_value() == week_start.isoformat()
        and page.locator("#export-to").input_value() == shop_today.isoformat(),
        page.locator("#export-from").input_value(),
    )

    print()
    print("=" * 74)
    print("15. KHÁCH TỚI LẤY ĐỒ — the pickup press for every customer who collects at the counter")
    print("=" * 74)

    # `PICKUP-ONLY-SETTLE-001`, the `DEC-032` addendum. The shop's courier fetched the laundry; the
    # customer came by the counter and paid the exact total before it was finished. The server
    # records that as the walk-in's prepayment and closes the order only once the handover is
    # recorded -- so if the console offers "Khách đã nhận đồ" to walk-ins alone, this customer's
    # order is paid, released and impossible to close from the screen.
    #
    # CONSOLE-REDESIGN-002: the order page no longer decides which button to offer. It renders the
    # server's `next_steps` (ORDER-STEPS-001, computed by the domain), so the property checked is
    # that it renders that list faithfully -- the pickup press exactly when the server lists
    # COLLECT, never otherwise -- and that each press carries the headers that make it safe.

    def pickup_button() -> int:
        return page.get_by_role("button", name="Khách đã nhận đồ").count()

    def open_order(view: dict[str, object]) -> None:
        state["order_view"] = view
        page.evaluate("location.hash = '#/orders'")
        page.wait_for_timeout(600)
        page.evaluate(f"location.hash = '#/orders/{PICKUP_ORDER_ID}'")
        page.wait_for_timeout(900)

    def open_dialog_text() -> str:
        return str(page.evaluate("() => document.querySelector('dialog[open]')?.textContent || ''"))

    prepaid_pickup = order_view(
        "PICKUP_ONLY", balance="PAID", collected=False, steps=[step("COLLECT", True)]
    )
    open_order(prepaid_pickup)
    check(
        "a courier-fetched order paid in advance offers the pickup press",
        pickup_button() == 1,
        f"{pickup_button()} buttons",
    )
    check(
        "once paid, no payment is offered and the screen says the money is taken",
        page.locator("button[data-step=SETTLE], button[data-step=PREPAY]").count() == 0
        and "Đã thu đủ tiền" in rendered_text()
        and "Khách chưa nhận đồ" in rendered_text(),
    )
    state["order_writes"] = []
    page.get_by_role("button", name="Khách đã nhận đồ").first.click()
    page.wait_for_timeout(400)
    check(
        "the press opens a confirmation that says the customer paid and is to be handed the bag",
        "Khách đã trả trước. Khi đưa đồ cho khách" in open_dialog_text(),
        open_dialog_text()[:120],
    )
    page.locator("dialog[open] #collection-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "the handover is recorded on the collection route, against the version read",
        len(writes) == 1
        and writes[0]["path"] == f"{PICKUP_ORDER_ID}/collection"
        and writes[0]["if_match"] == '"14"'
        and bool(writes[0]["key"]),
        repr(writes),
    )
    check(
        "and the success is said where the press was, not as a silent redraw",
        "Đã ghi nhận khách nhận đồ" in open_dialog_text(),
        open_dialog_text()[:120],
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # The payment sheet, and its guardrail: the finality line beside the field and the DEC-010 /
    # DEC-032 policy (POLICY_BOUND) one tap away in the same sheet.
    unpaid_pickup = order_view(
        "PICKUP_ONLY",
        balance="UNPAID",
        collected=False,
        steps=[step("SETTLE", True), step("PREPAY")],
    )
    open_order(unpaid_pickup)
    check("an unpaid order offers no pickup press", pickup_button() == 0)
    page.locator(".action-bar--v2 button[data-step=SETTLE]").click()
    page.wait_for_timeout(400)
    sheet_text = open_dialog_text()
    check(
        "Thu tiền shows the amount due large, and the field is empty -- typed on purpose",
        "110.000" in sheet_text and page.locator("#settlement-amount").input_value() == "",
        sheet_text[:120],
    )
    check(
        "the settlement guardrail says no courier takes money, and that the record is final",
        "Người giao không thu tiền" in sheet_text and "Ghi rồi không sửa được" in sheet_text,
    )
    state["order_writes"] = []
    page.locator("#settlement-amount").type("110.000", delay=15)
    check(
        "the typed amount is echoed as the server will read it",
        "= 110.000" in open_dialog_text(),
    )
    page.locator("dialog[open] #settlement-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    sent = json.loads(writes[0]["body"]) if writes else {}
    check(
        "the payment posts the typed figure with collected_by_customer, and an Idempotency-Key",
        len(writes) == 1
        and writes[0]["path"] == f"{PICKUP_ORDER_ID}/settlement"
        and sent == {"paid_amount_vnd": 110000, "collected_by_customer": True}
        and bool(writes[0]["key"]),
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    open_order(
        order_view(
            "SELF_DROP_SELF_COLLECT", balance="PAID", collected=False, steps=[step("COLLECT", True)]
        )
    )
    check("a walk-in paid at drop-off still offers it", pickup_button() == 1)

    # The refusal direction. A delivery reaches its customer by a leg (`DEC-023`), a customer who
    # paid at pickup was recorded by that settlement, and an unpaid customer pays first. The server
    # lists no COLLECT for any of them, and the page must not invent one.
    for mode, balance, collected, steps, why in (
        ("PICKUP_AND_RETURN", "PAID", False, [step("DELIVERY_RETURN", True)], "a prepaid delivery"),
        ("RETURN_ONLY", "PAID", False, [step("DELIVERY_RETURN", True)], "a prepaid return-only"),
        ("PICKUP_ONLY", "PAID", True, [step("COMPLETE", True)], "an order already collected"),
    ):
        open_order(order_view(mode, balance=balance, collected=collected, steps=steps))
        check(f"{why} offers no pickup press", pickup_button() == 0, f"{pickup_button()} buttons")

    # A stale version: never retried, the way out is a reload.
    state["order_writes"] = []
    open_order(
        order_view(
            "SELF_DROP_SELF_COLLECT",
            balance="UNPAID",
            collected=False,
            steps=[
                step("HAND_OVER", True),
                step(
                    "CANCEL",
                    requires=["custody_resolution"],
                    custody_resolutions=["SHOP_FAULT_NO_CHARGE"],
                ),
            ],
        )
    )
    state["order_write_reply"] = (409, {"detail": "STALE_VERSION: order changed"})
    page.locator(".action-bar--v2 button[data-step=HAND_OVER]").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "a composite step posts to /steps with the step, If-Match and an Idempotency-Key",
        len(writes) == 1
        and writes[0]["path"] == f"{PICKUP_ORDER_ID}/steps"
        and json.loads(writes[0]["body"] or "{}") == {"step": "HAND_OVER"}
        and writes[0]["if_match"] == '"14"'
        and bool(writes[0]["key"]),
        repr(writes),
    )
    check(
        "a stale refusal is shown at the button with 'Đơn vừa đổi — tải lại', and nothing retried",
        "người khác đổi" in rendered_text()
        and page.get_by_role("button", name="Đơn vừa đổi — tải lại").count() == 1
        and len(state.get("order_writes") or []) == 1,
    )
    # A cancellation through review offers only the custody answers the server listed.
    state["order_write_reply"] = None
    page.locator("button[data-more-steps]").click()
    page.wait_for_timeout(300)
    page.locator("dialog[open] button[data-step=CANCEL]").click()
    page.wait_for_timeout(300)
    radios = page.locator("dialog[open] input[name=custody_resolution]")
    check(
        "Huỷ đơn offers exactly the custody answers the server listed, and needs one first",
        radios.count() == 1
        and radios.first.get_attribute("value") == "SHOP_FAULT_NO_CHARGE"
        and page.locator("dialog[open] .sheet__actions button").first.is_disabled(),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # RECEIVE refused for a readiness fact the server derives itself: the reason is said in
    # Vietnamese at the button, with the code, and nothing is claimed to have happened.
    open_order(
        order_view(
            "SELF_DROP_SELF_COLLECT",
            balance="UNPAID",
            collected=False,
            steps=[step("RECEIVE", True, requires=["slot_approved"])],
        )
    )
    state["order_writes"] = []
    state["order_write_reply"] = (
        422,
        {"detail": {"outcome": "REQUIRE_HUMAN", "reason_codes": ["QUANTITY_NOT_MEASURED"]}},
    )
    page.locator(".action-bar--v2 button[data-step=RECEIVE]").click()
    page.wait_for_timeout(400)
    receive_shut = page.locator("#receive-submit").is_disabled()
    page.locator("#receive-slot").check()
    page.locator("#receive-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "Nhận đồ is shut until the slot is attested, and then sends it as the operator's word",
        receive_shut
        and len(writes) == 1
        and json.loads(writes[0]["body"] or "{}") == {"step": "RECEIVE", "slot_approved": True},
        repr(writes),
    )
    check(
        "a readiness refusal is said in Vietnamese at the button, with its code, nothing recorded",
        "Chưa nhận đồ được" in open_dialog_text()
        and "QUANTITY_NOT_MEASURED" in open_dialog_text()
        and "khách tự ước" in open_dialog_text(),
        open_dialog_text()[:160],
    )
    check(
        "before the owner publishes the turnaround rules, Nhận đồ says there is no promise yet",
        "chủ tiệm chưa công bố quy tắc hẹn trả" in open_dialog_text(),
        open_dialog_text()[:200],
    )
    state["order_write_reply"] = None
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # PROMISE-001 (DEC-037). Hẹn trả before the press: the server's time for each choice, the
    # 24/48 chips with 48 already chosen for a blanket, a picker a person must fill for a special
    # item, and the choice sent as the step's own fields. Nothing here computes a time.
    receive_view = order_view(
        "SELF_DROP_SELF_COLLECT",
        balance="UNPAID",
        collected=False,
        steps=[step("RECEIVE", True, requires=["slot_approved"])],
    )

    def press_receive(read: dict[str, object]) -> None:
        state["promise_read"] = read
        state["order_writes"] = []
        open_order(receive_view)
        page.locator(".action-bar--v2 button[data-step=RECEIVE]").click()
        page.wait_for_timeout(600)

    press_receive(
        promise_read(
            published=True,
            options=promise_options(
                "RANGE_CHOICE",
                "2026-09-27T10:00:00+00:00",
                [
                    ("H24", "2026-09-26T10:00:00+00:00"),
                    ("H48", "2026-09-27T10:00:00+00:00"),
                    ("CUSTOM", None),
                ],
            ),
        )
    )
    line = (
        page.locator("#receive-promise-line").inner_text()
        if page.locator("#receive-promise-line").count()
        else ""
    )
    chosen = page.locator("dialog[open] input[name=receive-promise-choice]:checked")
    check(
        "a blanket's Nhận đồ shows the promise before the press, with 48 giờ already chosen",
        "Hẹn trả:" in line
        and "17:00 Chủ nhật 27/9" in line
        and chosen.count() == 1
        and chosen.first.get_attribute("value") == "H48",
        line,
    )
    page.locator("dialog[open] .choice-chip", has_text="24 giờ").click()
    page.wait_for_timeout(200)
    line = page.locator("#receive-promise-line").inner_text()
    check(
        "choosing 24 giờ shows the server's time for it, not a time the browser worked out",
        "17:00 thứ Bảy 26/9" in line,
        line,
    )
    page.locator("#receive-slot").check()
    page.locator("#receive-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "the choice travels as the step's own field",
        len(writes) == 1
        and json.loads(writes[0]["body"] or "{}")
        == {"step": "RECEIVE", "slot_approved": True, "promise_choice": "H24"},
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    press_receive(
        promise_read(
            published=True,
            options=promise_options(
                "CUSTOM",
                None,
                [("CUSTOM", None)],
                reasons=["HUMAN_ETA_REQUIRED"],
                human=["OTHER_PLUSH"],
            ),
        )
    )
    page.locator("#receive-slot").check()
    page.wait_for_timeout(150)
    shut_without_time = page.locator("#receive-submit").is_disabled()
    check(
        "a special item asks the person for the day and hour, and Nhận đồ waits for it",
        shut_without_time
        and page.locator("#receive-promise-at").is_visible()
        and "Bạn chọn ngày giờ trả" in open_dialog_text(),
        open_dialog_text()[:200],
    )
    page.locator("#receive-promise-at").fill("2026-09-28T10:00")
    page.wait_for_timeout(150)
    page.locator("#receive-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "the time the person picked is sent as shop time, with its offset",
        len(writes) == 1
        and json.loads(writes[0]["body"] or "{}")
        == {
            "step": "RECEIVE",
            "slot_approved": True,
            "promise_choice": "CUSTOM",
            "custom_at": "2026-09-28T10:00:00+07:00",
        },
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    press_receive(
        promise_read(
            published=True,
            options=promise_options(
                "NONE",
                "2026-09-26T06:00:00+00:00",
                [("EXPRESS_2H", "2026-09-25T12:00:00+00:00"), ("CUSTOM", None)],
            ),
        )
    )
    line = page.locator("#receive-promise-line").inner_text()
    page.locator("#receive-slot").check()
    page.locator("#receive-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "ordinary laundry shows the rule's time and, left alone, sends no choice at all",
        "13:00 thứ Bảy 26/9" in line
        and len(writes) == 1
        and json.loads(writes[0]["body"] or "{}") == {"step": "RECEIVE", "slot_approved": True},
        f"{line} {writes!r}",
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    state["promise_read"] = None

    # The promise on the order page, its state, and Hẹn lại with a reason.
    late_view = {
        **order_view(
            "SELF_DROP_SELF_COLLECT",
            balance="UNPAID",
            collected=False,
            steps=[step("START_WASH", True)],
        ),
        "production": "NOT_STARTED",
        "promised_ready_at": "2026-09-26T06:00:00+00:00",
        "current_promise_at": "2026-09-26T08:00:00+00:00",
        "promise_state": "LATE",
        "promise_rule_id": "SLA_STANDARD_CLOTHES",
        "promise_basis": "RULE",
    }
    state["order_writes"] = []
    open_order(late_view)
    promise_row = page.locator("[data-field=promise]")
    promise_text = promise_row.inner_text() if promise_row.count() else ""
    check(
        "the order page shows the promise the customer was told, a LATE pill and the first promise",
        "15:00 thứ Bảy 26/9" in promise_text
        and "Trễ hẹn" in promise_text
        and "Hẹn đầu: 13:00 thứ Bảy 26/9" in promise_text,
        promise_text,
    )
    page.locator("#promise-change").click()
    page.wait_for_timeout(300)
    save_shut = page.locator("#promise-change-submit").is_disabled()
    page.locator("#promise-change-at").fill("2026-09-27T10:00")
    page.locator("dialog[open] .choice-chip[title=OTHER]").click()
    page.wait_for_timeout(150)
    still_shut = page.locator("#promise-change-submit").is_disabled()
    page.locator("#promise-change-note").fill("Khách đi công tác")
    page.wait_for_timeout(150)
    page.locator("#promise-change-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "Hẹn lại needs a time and a reason (a note for Khác), then posts with If-Match and a key",
        save_shut
        and still_shut
        and len(writes) == 1
        and writes[0]["path"] == f"{PICKUP_ORDER_ID}/promise"
        and json.loads(writes[0]["body"] or "{}")
        == {
            "promise_at": "2026-09-27T10:00:00+07:00",
            "reason": "OTHER",
            "note": "Khách đi công tác",
        }
        and writes[0]["if_match"] == '"14"'
        and bool(writes[0]["key"]),
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(900)
    listed = page.locator(f"[data-order-id='{PICKUP_ORDER_ID}']")
    listed_text = listed.first.inner_text() if listed.count() else ""
    check(
        "the order list shows the promise and the LATE pill on the row",
        "Hẹn 15:00 26/9" in listed_text and "Trễ hẹn" in listed_text,
        listed_text[:160],
    )

    # ORDER-STEPS-002. Giặt lại and Không nhận đồ: never the big button, each a sheet offering
    # exactly the reasons the server listed, shut until one is picked, and the reason sent as the
    # step's own field. The refusal ends the order, so it takes two presses.
    rewash_view = {
        **order_view("SELF_DROP_SELF_COLLECT", balance="UNPAID", collected=False),
        "production": "QUALITY_CHECK",
        "next_steps": [
            step("MARK_READY", True),
            step("HOLD"),
            step(
                "REWASH",
                requires=["rewash_reason"],
                rewash_reasons=["NOT_CLEAN", "MACHINE_FAULT", "OTHER"],
            ),
        ],
    }
    open_order(rewash_view)
    check(
        "Giặt lại is never the big button; it waits under Khác",
        page.locator(".action-bar--v2 button[data-step=REWASH]").count() == 0
        and page.locator(".action-bar--v2 button[data-step=MARK_READY]").count() == 1,
    )
    page.locator("button[data-more-steps]").click()
    page.wait_for_timeout(300)
    page.locator("dialog[open] button[data-step=REWASH]").click()
    page.wait_for_timeout(300)
    offered = [
        str(node.get_attribute("data-value"))
        for node in page.locator("dialog[open] #step-reason [data-value]").all()
    ]
    shut = page.locator("dialog[open] #step-reason-submit").is_disabled()
    check(
        "the rewash sheet offers exactly the server's reasons, in words, and is shut until a pick",
        offered == ["NOT_CLEAN", "MACHINE_FAULT", "OTHER"]
        and shut
        and "Chưa sạch" in open_dialog_text()
        and "NOT_CLEAN" not in open_dialog_text(),
        f"{offered} shut={shut}",
    )
    state["order_writes"] = []
    page.locator("dialog[open] #step-reason [data-value=MACHINE_FAULT]").click()
    page.locator("dialog[open] #step-reason-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "Giặt lại posts the step with its reason, If-Match and an Idempotency-Key",
        len(writes) == 1
        and writes[0]["path"] == f"{PICKUP_ORDER_ID}/steps"
        and json.loads(writes[0]["body"] or "{}")
        == {"step": "REWASH", "rewash_reason": "MACHINE_FAULT"}
        and writes[0]["if_match"] == '"14"'
        and bool(writes[0]["key"]),
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Only a never-primary step is legal: nothing is promoted to the big button.
    refuse_only = {
        **order_view("SELF_DROP_SELF_COLLECT", balance="UNPAID", collected=False),
        "commercial": "REQUESTED",
        "intake": "RECEIVED_PENDING_INSPECTION",
        "production": "NOT_STARTED",
        "next_steps": [
            step(
                "REJECT_INTAKE",
                requires=["rejection_reason"],
                rejection_reasons=["NOT_SERVICEABLE", "DAMAGED_ON_ARRIVAL", "OTHER"],
            )
        ],
    }
    open_order(refuse_only)
    check(
        "with no primary step the bar says so and keeps Không nhận đồ under Khác",
        page.locator(".action-bar--v2 button[data-step]").count() == 0
        and "Chưa có bước tiếp theo." in rendered_text()
        and page.locator("button[data-more-steps]").count() == 1,
    )
    page.locator("button[data-more-steps]").click()
    page.wait_for_timeout(300)
    page.locator("dialog[open] button[data-step=REJECT_INTAKE]").click()
    page.wait_for_timeout(300)
    state["order_writes"] = []
    page.locator("dialog[open] #step-reason [data-value=NOT_SERVICEABLE]").click()
    page.locator("dialog[open] #step-reason-submit").click()
    page.wait_for_timeout(300)
    armed_only = len(state.get("order_writes") or []) == 0
    page.locator("dialog[open] #step-reason-submit").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "Không nhận đồ needs two presses and then sends the step with its reason",
        armed_only
        and len(writes) == 1
        and json.loads(writes[0]["body"] or "{}")
        == {"step": "REJECT_INTAKE", "rejection_reason": "NOT_SERVICEABLE"}
        and bool(writes[0]["key"]),
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # The list the counter searches at pickup says the same thing on the row.
    state["order_view"] = prepaid_pickup
    page.evaluate("location.hash = '#/'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(1200)
    page.locator("#order-tabs button[data-value=ready]").click()
    page.wait_for_timeout(300)
    check(
        "the order list marks a courier-fetched order as paid and waiting for the customer",
        "Khách đã trả trước, chưa nhận đồ" in rendered_text(),
    )
    check(
        "the row is 'Phiếu 12' with one status word and the money, no identifier",
        "Phiếu 12" in rendered_text() and PICKUP_ORDER_ID not in page.inner_text("main"),
    )
    print()
    print("=" * 74)
    print("15b. MÁY NÀO? VÀ CHI PHÍ CHUYẾN — measured inside the taps staff already make")
    print("=" * 74)
    # SHOP-CAPTURE-001 (DEC-038). The machines are the server's list in the server's order; the
    # console offers exactly those plus "Bỏ qua", and sends the machine picked as the step's own
    # field. With no machine listed, the step runs as before.
    state["machines"] = STUB_WASH_MACHINES
    wash_view = {
        **order_view("SELF_DROP_SELF_COLLECT", balance="UNPAID", collected=False),
        "production": "NOT_STARTED",
        "next_steps": [step("START_WASH", True), step("HOLD")],
    }
    open_order(wash_view)
    state["order_writes"] = []
    page.locator(".action-bar--v2 button[data-step=START_WASH]").click()
    page.wait_for_timeout(700)
    offered = [
        str(node.get_attribute("data-machine-code"))
        for node in page.locator("dialog[open] button[data-machine-code]").all()
    ]
    check(
        "Bắt đầu giặt asks Máy nào? with the server's machines in its order, and Bỏ qua",
        offered == ["WASH-02", "WASH-01"]
        and page.locator("dialog[open] button[data-machine-skip]").count() == 1
        and "Vừa dùng" in open_dialog_text()
        and not state.get("order_writes"),
        f"{offered} writes={state.get('order_writes')}",
    )
    page.locator("dialog[open] button[data-machine-code=WASH-01]").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "picking a machine posts the step with that machine, If-Match and an Idempotency-Key",
        len(writes) == 1
        and json.loads(writes[0]["body"] or "{}")
        == {"step": "START_WASH", "machine_id": STUB_WASH_MACHINES[1]["machine_id"]}
        and writes[0]["if_match"] == '"14"'
        and bool(writes[0]["key"]),
        repr(writes),
    )
    open_order(wash_view)
    state["order_writes"] = []
    page.locator(".action-bar--v2 button[data-step=START_WASH]").click()
    page.wait_for_timeout(700)
    page.locator("dialog[open] button[data-machine-skip]").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "Bỏ qua posts the step with no machine at all",
        len(writes) == 1 and json.loads(writes[0]["body"] or "{}") == {"step": "START_WASH"},
        repr(writes),
    )
    # The order page shows what was recorded, as the server read it.
    state["capture"] = STUB_CAPTURE
    delivery_view = {
        **order_view("PICKUP_AND_RETURN", balance="UNPAID", collected=False),
        "production": "NOT_STARTED",
        "next_steps": [step("DELIVERY_PICKUP", True)],
    }
    open_order(delivery_view)
    page.wait_for_timeout(500)
    capture_text = page.locator("#order-capture").inner_text()
    check(
        "the order page lists the cycle's machine and minutes and the trip's cost, as sent",
        "WASH-02" in capture_text
        and "47 phút" in capture_text
        and "45.000" in capture_text
        and "6,5 km" in capture_text,
        capture_text[:160],
    )
    state["order_writes"] = []
    page.locator(".action-bar--v2 button[data-step=DELIVERY_PICKUP]").click()
    page.wait_for_timeout(800)
    hint = page.locator("dialog[open] [data-suggested-vehicle]")
    pressed = page.locator("dialog[open] #trip-vehicle [aria-pressed=true]").count()
    check(
        "the leg sheet says the owner's rule for 22 kg and picks no vehicle for the driver",
        hint.count() == 1
        and hint.first.get_attribute("data-suggested-vehicle") == "O_TO"
        and "22 kg" in (hint.first.text_content() or "")
        and pressed == 0,
        f"{hint.first.text_content() if hint.count() else 'absent'} pressed={pressed}",
    )
    page.locator("dialog[open] details[data-trip-fields] summary").click()
    page.locator("dialog[open] #trip-vehicle [data-value=O_TO]").click()
    page.locator("#trip-km").fill("6,5")
    page.locator("#trip-cost").fill("45.000")
    page.locator("#trip-note").fill("gửi xe")
    page.locator("dialog[open] button[data-leg-outcome=SUCCEEDED]").click()
    page.wait_for_timeout(900)
    writes = state.get("order_writes") or []
    check(
        "Lấy được đồ sends the trip as typed: vehicle, km as text, the amount as an integer",
        len(writes) == 1
        and writes[0]["path"].endswith("/delivery-legs")
        and json.loads(writes[0]["body"] or "{}")
        == {
            "leg_kind": "PICKUP",
            "outcome": "SUCCEEDED",
            "vehicle": "O_TO",
            "km": "6,5",
            "cost_vnd": 45000,
            "note": "gửi xe",
        }
        and bool(writes[0]["key"]),
        repr(writes),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    open_order(delivery_view)
    state["order_writes"] = []
    page.locator(".action-bar--v2 button[data-step=DELIVERY_PICKUP]").click()
    page.wait_for_timeout(600)
    page.locator("dialog[open] details[data-trip-fields] summary").click()
    page.locator("#trip-km").fill("4,55")
    page.locator("dialog[open] button[data-leg-outcome=SUCCEEDED]").click()
    page.wait_for_timeout(600)
    check(
        "a kilometre figure that cannot be read is refused in words, and nothing is sent",
        not state.get("order_writes") and "Số km là một số" in open_dialog_text(),
        open_dialog_text()[:120],
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    state["machines"] = []
    state["capture"] = None

    state["order_view"] = None
    state["order_writes"] = []

    print()
    print("=" * 74)
    print("16. DUYỆT GỬI TIN — the exact words above the button, and raised from the server's read")
    print("=" * 74)

    # `MESSAGE-DRAFT-BINDING-001`. The card used to say the message body was stored nowhere and
    # kept both buttons shut for good, and nothing in the console could raise the envelope. Four
    # properties, each only provable in a browser: the words are in the document above the approve
    # control; hostile markup in a draft is shown as characters and never becomes an element; a
    # draft that moved after the envelope was raised withholds its text and leaves only "Từ chối";
    # and step 0 raises the envelope from the read with nothing typed.

    def approve_state() -> object:
        return page.evaluate(
            """() => {
              const card = document.querySelector("article.card");
              if (!card) return "no card";
              const find = (label) => [...card.querySelectorAll("button")]
                .find((b) => b.textContent.trim() === label);
              const approve = find("Duyệt");
              const refuse = find("Từ chối");
              if (!approve || !refuse) return "missing a control";
              return { approve: approve.disabled, refuse: refuse.disabled };
            }"""
        )

    state["message_listed"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    text = rendered_text()

    check(
        "the queue names the send action and its resource type",
        "SEND_MESSAGE" in text and "MESSAGE_DRAFT" in text,
    )
    check(
        "the card read the words through the envelope's own store",
        any(
            f"/stores/{STORE}/message-drafts/{MESSAGE_DRAFT_ID}/binding" in u
            for u in state.get("binding_reads", [])
        ),
        repr(state.get("binding_reads")),
    )
    check(
        "the exact words the envelope binds are on the card",
        "mời anh/chị ghé tiệm nhận ạ." in text and "Tiệm mở cửa đến 21 giờ." in text,
    )
    check(
        "hostile markup in the draft is shown as characters",
        '<img src=x onerror="window.__pwned = true">' in text,
    )
    check(
        "and never becomes an element or runs",
        page.evaluate(
            """() => document.querySelectorAll("article.card img").length === 0
                     && window.__pwned !== true"""
        )
        is True,
    )
    check(
        "the stale 'hệ thống không lưu nội dung tin nhắn' reasoning is gone from the screen",
        "không lưu nội dung tin nhắn" not in text,
    )
    ordering = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          const body = card.querySelector("[data-message-body]");
          if (!approve) return "no approve control";
          if (!body) return "the message body is not rendered";
          const before =
            Boolean(body.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING);
          return { before, disabled: approve.disabled };
        }"""
    )
    check(
        "the words precede the approve control in the document",
        isinstance(ordering, dict) and ordering.get("before") is True,
        repr(ordering),
    )
    check(
        "and the control is pressable — this envelope was undecidable by anyone",
        isinstance(ordering, dict) and ordering.get("disabled") is False,
        repr(ordering),
    )

    page.locator("article.card button", has_text="Duyệt").first.click()
    page.wait_for_timeout(700)
    toasts = page.locator(".toasts .toast").all_inner_texts()
    check(
        "a recorded decision is confirmed out loud, not by the card silently vanishing",
        any("Đã duyệt" in t and "Cho gửi tin nhắn" in t for t in toasts),
        repr(toasts),
    )
    posts = [json.loads(p or "{}") for p in state.get("decision_posts", [])]
    check(
        "pressing Duyệt sends back the envelope's own version and both digests",
        bool(posts)
        and posts[-1].get("decision") == "APPROVED"
        and posts[-1].get("resource_version") == 1
        and posts[-1].get("snapshot_hash") == MESSAGE_SNAPSHOT
        and posts[-1].get("rendered_hash") == MESSAGE_RENDERED,
        repr(posts[-1:]),
    )

    # A reviewer edited the draft after the envelope was raised. The words on file are no longer
    # the words the press would hand back, so they are withheld and only a refusal is offered.
    state["message_edited"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    text = rendered_text()
    controls = approve_state()
    check(
        "an edited draft leaves Duyệt shut and Từ chối usable",
        controls == {"approve": True, "refuse": False},
        repr(controls),
    )
    check(
        "its new text is withheld rather than shown with a caveat",
        "tiệm giao tận nơi" not in text and "mời anh/chị ghé tiệm nhận ạ." not in text,
    )
    check(
        "and the card names the reason, with both versions",
        "Tin nhắn đã đổi so với phiếu" in text and "v1" in text and "v2" in text,
    )
    before = len(state.get("decision_posts", []))
    page.locator("article.card button", has_text="Từ chối").first.click()
    page.wait_for_timeout(700)
    posts = [json.loads(p or "{}") for p in state.get("decision_posts", [])]
    check(
        "refusing the stale envelope sends a REJECTED decision and nothing else",
        len(posts) == before + 1 and posts[-1].get("decision") == "REJECTED",
        repr(posts[-1:]),
    )
    state["message_edited"] = False

    # A reviewer rejected the draft since: nothing is sendable.
    state["message_rejected"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(900)
    text = rendered_text()
    controls = approve_state()
    check(
        "a rejected draft leaves Duyệt shut and Từ chối usable",
        controls == {"approve": True, "refuse": False},
        repr(controls),
    )
    check(
        "and says the draft has nothing left to send",
        "Bản nháp không còn gửi được" in text and "mời anh/chị" not in text,
    )
    state["message_rejected"] = False
    state["message_listed"] = False

    # `CONSOLE-REDESIGN-005`. The undecided queue on `#/shadow`: the draft is a bubble of text
    # nodes, the three actions are "Duyệt" / "Sửa rồi duyệt" / "Từ chối", and the table's CHECK
    # constraints are enforced before anything is sent -- an unchanged edit and a reasonless
    # refusal never reach the server, and a refusal sends exactly the reason picked.
    state["drafts_listed"] = True
    state["shadow_decisions"] = []
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/shadow'")
    page.wait_for_timeout(900)
    card = page.locator(f"article[data-agent-run='{SHADOW_DRAFT_ID}']")
    check(
        "an undecided draft renders as a card with its words as characters, marked untrusted",
        card.count() == 1
        and SHADOW_DRAFT_TEXT in (card.text_content() or "")
        and "Văn bản không tin cậy" in (card.text_content() or "")
        and page.evaluate(
            "() => document.querySelectorAll('article.draft b').length === 0"
            " && window.__pwned !== true"
        ),
        repr((card.text_content() or "")[:160]),
    )
    labels = page.evaluate(
        f"""() => [...document.querySelectorAll("article[data-agent-run='{SHADOW_DRAFT_ID}']"""
        """ .draft__actions button")].map((b) => b.textContent.trim())"""
    )
    check(
        "the card offers exactly Duyệt, Sửa rồi duyệt and Từ chối",
        labels == ["Duyệt", "Sửa rồi duyệt", "Từ chối"],
        repr(labels),
    )
    card.locator("button", has_text="Sửa rồi duyệt").click()
    page.wait_for_timeout(200)
    edit_box = card.locator("textarea")
    check(
        "Sửa rồi duyệt opens the edit box in place, holding the draft's words",
        edit_box.count() == 1 and edit_box.input_value() == SHADOW_DRAFT_TEXT,
    )
    card.locator("button", has_text="Lưu và duyệt").click()
    page.wait_for_timeout(300)
    check(
        "an edit that changes nothing is refused on screen and sends nothing",
        state["shadow_decisions"] == [] and "giống hệt bản gốc" in (card.text_content() or ""),
        repr(state["shadow_decisions"]),
    )
    card.locator("button", has_text="Huỷ").click()
    page.wait_for_timeout(200)
    card.locator("button", has_text="Từ chối").click()
    page.wait_for_timeout(200)
    card.locator("button", has_text="Từ chối bản nháp").click()
    page.wait_for_timeout(300)
    check(
        "a refusal without a reason is refused on screen and sends nothing",
        state["shadow_decisions"] == [] and "phải kèm lý do" in (card.text_content() or ""),
        repr(state["shadow_decisions"]),
    )
    card.locator("button", has_text="Mã khác").click()
    page.wait_for_timeout(150)
    card.locator("input[type=text]").type("wr", delay=10)
    card.locator("button", has_text="Từ chối bản nháp").click()
    page.wait_for_timeout(300)
    check(
        "a free reason code is upper-cased as typed and still held to the table's pattern",
        state["shadow_decisions"] == []
        and card.locator("input[type=text]").input_value() == "WR"
        and "Mã lý do phải viết hoa" in (card.text_content() or ""),
        repr(card.locator("input[type=text]").input_value()),
    )
    card.locator("button", has_text="Sai thông tin").click()
    page.wait_for_timeout(150)
    card.locator("button", has_text="Từ chối bản nháp").click()
    page.wait_for_timeout(900)
    sent = [json.loads(p or "{}") for p in state["shadow_decisions"]]
    check(
        "the refusal sends the picked chip's code, and no edited text",
        sent == [{"decision": "REJECT", "reason_code": "WRONG_FACTS", "edited_text": None}],
        repr(sent),
    )
    check(
        "the decision is confirmed as recorded and not sent, and the queue is calm and empty",
        "Không có tin nhắn nào được gửi đi" in rendered_text()
        and "Máy không tự gửi" in rendered_text()
        and page.locator("article.draft").count() == 0,
    )
    state["drafts_listed"] = False

    # Where the operator starts: a decided draft on `#/shadow`. Only one a person approved or
    # rewrote offers the way to ask for a send.
    state["reviews_listed"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/shadow'")
    page.wait_for_timeout(900)
    links = page.evaluate(
        """() => [...document.querySelectorAll("a[href^='#/exceptions?draft=']")]
                 .map((a) => a.getAttribute("href"))"""
    )
    check(
        "an approved draft on #/shadow links to step 0 with its id, and a rejected one does not",
        links == [f"#/exceptions?draft={MESSAGE_DRAFT_ID}"],
        repr(links),
    )
    state["reviews_listed"] = False

    # Step 0: the operator opens the manual-send panel on a draft, as the `#/shadow` link does.
    page.evaluate(f"location.hash = '#/exceptions?draft={MESSAGE_DRAFT_ID}'")
    page.wait_for_timeout(1000)
    text = rendered_text()
    check(
        "step 0 reads the draft and prints the words before offering anything",
        "Đúng những chữ sẽ được xin duyệt và gửi" in text
        and "mời anh/chị ghé tiệm nhận ạ." in text,
    )
    check(
        "the recipient is the opaque binding, shortened, and nothing else about the customer",
        MESSAGE_RECIPIENT[:8] in text and MESSAGE_RECIPIENT not in text,
    )
    page.locator("button", has_text="Xin duyệt gửi đúng tin này").first.click()
    page.wait_for_timeout(700)
    raised = [json.loads(p or "{}") for p in state.get("approval_posts", [])]
    check(
        "the envelope is raised from the server's read, value for value",
        bool(raised)
        and raised[-1]
        == {
            k: MESSAGE_BINDING[k]
            for k in MESSAGE_BINDING
            if k not in {"text", "recipient_binding_id"}
        },
        repr(raised[-1:]),
    )
    check(
        "and names no recipient of its own",
        bool(raised) and "recipient_binding_id" not in raised[-1],
    )
    prefilled = page.evaluate(
        """() => ({
          approval: document.querySelector("#manual-approval-id")?.value,
          version: document.querySelector("#manual-resource-version")?.value,
          snapshot: document.querySelector("#manual-snapshot-hash")?.value,
          rendered: document.querySelector("#manual-rendered-hash")?.value,
        })"""
    )
    check(
        "step 1 is filled from the raised envelope, so nothing is pasted",
        prefilled
        == {
            "approval": MESSAGE_APPROVAL,
            "version": "1",
            "snapshot": MESSAGE_SNAPSHOT,
            "rendered": MESSAGE_RENDERED,
        },
        repr(prefilled),
    )
    check(
        "and the page says a different person must approve and nothing was sent",
        "không phải bạn" in rendered_text() and "Chưa có gì được gửi" in rendered_text(),
    )

    # `CONSOLE-REDESIGN-005`: steps 3 and 4 carry every value forward, and the attestation -- the
    # one CAS write on this screen -- goes out under the envelope's row version as a strong-quoted
    # If-Match. That header was sent since the panel existed and asserted by nothing (PART B).
    state["prepare_refusal"] = None
    state["attest_posts"] = []
    page.locator("button", has_text="Khoá phong bì cho người gửi tay").first.click()
    page.wait_for_timeout(800)
    carried = page.evaluate(
        """() => ({
          envelope: document.querySelector("#attest-envelope-id")?.value,
          version: document.querySelector("#attest-resource-version")?.value,
          rendered: document.querySelector("#attest-rendered-hash")?.value,
          row: document.querySelector("#attest-row-version")?.value,
        })"""
    )
    check(
        "locking the envelope carries all four attestation values into step 4, nothing pasted",
        "Phong bì đã khoá" in rendered_text()
        and carried
        == {
            "envelope": MANUAL_ENVELOPE_ID,
            "version": "1",
            "rendered": MESSAGE_RENDERED,
            "row": "1",
        },
        repr(carried),
    )
    attest_button = page.locator("button", has_text="Chứng thực rằng tôi đã gửi tin này").first
    notice_before = page.evaluate(
        """() => {
          const button = [...document.querySelectorAll("button")]
            .find((b) => b.textContent.includes("Chứng thực rằng tôi đã gửi tin này"));
          const notice = [...document.querySelectorAll("[data-not-delivered]")]
            .find((n) => n.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING);
          return Boolean(notice && notice.offsetParent !== null
            && notice.textContent.includes("không có nghĩa là khách đã nhận"));
        }"""
    )
    check(
        "the not-delivered fact is visible right above the attest button",
        notice_before is True,
    )
    page.locator("button", has_text="Vừa gửi xong").first.click()
    page.wait_for_timeout(150)
    attest_button.click()
    page.wait_for_timeout(900)
    attests = state.get("attest_posts", [])
    body = json.loads(attests[-1][0] or "{}") if attests else {}
    check(
        "the attestation sends If-Match with the envelope's row version, strong-quoted",
        len(attests) == 1 and attests[0][1] == '"1"',
        repr([(a[1], a[2]) for a in attests]),
    )
    check(
        "under an idempotency key, to the locked envelope, with the carried version and digest",
        len(attests) == 1
        and bool(attests[0][2])
        and attests[0][3].split("?")[0].endswith(f"/manual-sends/{MANUAL_ENVELOPE_ID}/attest")
        and body.get("observed_resource_version") == 1
        and body.get("exact_rendered_hash") == MESSAGE_RENDERED
        and str(body.get("sent_at", "")).endswith("Z"),
        repr(body),
    )
    outcome = page.locator("[data-outcome='MANUAL_SEND_RECORDED']")
    check(
        "the recorded result repeats that MANUAL_SEND_RECORDED is not delivery",
        outcome.count() == 1
        and "không có nghĩa là khách đã nhận" in (outcome.text_content() or ""),
    )

    # MANUAL-SEND-RESUME (spec V2 principle 2 -- zero paste). A new session on the same draft:
    # the binding read carries `send_progress`, and the panel lands on the right step with every
    # value carried. "Nhập mã thủ công" is never opened in any of these, and every write sends
    # exactly the values the read returned.

    def progress_body(**overrides: object) -> dict[str, object]:
        return {
            "approval_request_id": MESSAGE_APPROVAL,
            "status": "REQUESTED",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=40)).isoformat(),
            "past_expiry": False,
            "resource_version": 1,
            "snapshot_hash": MESSAGE_SNAPSHOT,
            "rendered_hash": MESSAGE_RENDERED,
            "requested_by_you": True,
            "envelope_id": None,
            "envelope_status": None,
            "envelope_row_version": None,
            "prepared_by_you": None,
            **overrides,
        }

    def reopen(progress: dict[str, object] | None) -> str:
        state["send_progress"] = progress
        page.evaluate("location.hash = '#/orders'")
        page.wait_for_timeout(400)
        page.evaluate(f"location.hash = '#/exceptions?draft={MESSAGE_DRAFT_ID}'")
        page.wait_for_timeout(1100)
        return rendered_text()

    def manual_entry_opened() -> bool:
        return bool(
            page.evaluate(
                """() => [...document.querySelectorAll("details.manual-entry")]
                         .some((d) => d.open)"""
            )
        )

    # Waiting: step 2 says so, offers Duyệt to someone who may decide, and no second raise.
    posts_before = len(state.get("approval_posts", []))
    reopen(progress_body(requested_by_you=False))
    waiting = page.locator("#manual-step-2 [data-send-phase='waiting']")
    check(
        "a reopened draft whose approval is REQUESTED shows step 2 'Đang chờ duyệt'",
        waiting.count() == 1 and "Đang chờ duyệt" in (waiting.text_content() or ""),
    )
    check(
        "with the way to Duyệt for someone who may decide, and no second 'Xin duyệt' press",
        page.locator("#manual-step-2 a[href='#/approvals']").count() == 1
        and page.locator("button[data-raise-envelope]").count() == 0,
    )
    reopen(progress_body(requested_by_you=True))
    check(
        "the person who asked is told a different person decides, and is not sent to decide it",
        page.locator("#manual-step-2 a[href='#/approvals']").count() == 0
        and "không phải bạn" in (page.locator("#manual-step-2").text_content() or ""),
    )
    check(
        "and nothing was raised again by opening the page",
        len(state.get("approval_posts", [])) == posts_before,
    )

    # Approved, no envelope: one press locks, from the read alone.
    state["prepare_refusal"] = None
    state["prepare_posts"] = []
    reopen(progress_body(status="APPROVED"))
    check(
        "an APPROVED approval lands on step 3, ready, with its approval named",
        page.locator("#manual-step-3[data-state='current'] [data-send-phase='approved']").count()
        == 1
        and MESSAGE_APPROVAL[:8] in (page.locator("#manual-step-3").text_content() or ""),
    )
    page.locator("button[data-lock-envelope]").first.click()
    page.wait_for_timeout(800)
    prepared = [json.loads(p or "{}") for p in state.get("prepare_posts", [])]
    check(
        "one press 'Khoá phong bì' sends the approval's own version and digests",
        len(prepared) == 1
        and prepared[0]
        == {
            "observed_resource_version": 1,
            "observed_snapshot_hash": MESSAGE_SNAPSHOT,
            "observed_rendered_hash": MESSAGE_RENDERED,
            "channel": "INTERNAL_TEST",
        },
        repr(prepared),
    )
    check(
        "and the manual-entry fields were never needed",
        not manual_entry_opened() and "Phong bì đã khoá" in rendered_text(),
    )

    # Locked by you, in another session: step 4 is ready and attests from the read alone.
    state["attest_posts"] = []
    reopen(
        progress_body(
            status="APPROVED",
            envelope_id=MANUAL_ENVELOPE_ID,
            envelope_status="APPROVED_FOR_MANUAL_SEND",
            envelope_row_version=1,
            prepared_by_you=True,
        )
    )
    check(
        "an envelope locked by you lands on step 4, ready",
        page.locator("#manual-step-4[data-state='current']").count() == 1
        and page.locator("#manual-step-3[data-state='done']").count() == 1,
    )
    page.locator("button", has_text="Vừa gửi xong").first.click()
    page.wait_for_timeout(150)
    page.locator("button", has_text="Chứng thực rằng tôi đã gửi tin này").first.click()
    page.wait_for_timeout(900)
    attests = state.get("attest_posts", [])
    body = json.loads(attests[-1][0] or "{}") if attests else {}
    check(
        "the attestation goes to the envelope the server named, If-Match its row version",
        len(attests) == 1
        and attests[0][1] == '"1"'
        and attests[0][3].split("?")[0].endswith(f"/manual-sends/{MANUAL_ENVELOPE_ID}/attest")
        and body.get("observed_resource_version") == 1
        and body.get("exact_rendered_hash") == MESSAGE_RENDERED,
        repr((attests, body)),
    )
    check("with nothing pasted", not manual_entry_opened())

    # Locked by somebody else: step 4 is theirs, and nothing is prefilled for you.
    reopen(
        progress_body(
            status="APPROVED",
            envelope_id=MANUAL_ENVELOPE_ID,
            envelope_status="APPROVED_FOR_MANUAL_SEND",
            envelope_row_version=1,
            prepared_by_you=False,
        )
    )
    check(
        "an envelope somebody else locked is theirs to attest, and says so",
        "Người khác đã khoá phong bì này" in rendered_text()
        and page.locator("#attest-envelope-id").input_value() == "",
    )

    # Recorded: done, and the not-delivered fact is still on screen.
    reopen(
        progress_body(
            status="APPROVED",
            envelope_id=MANUAL_ENVELOPE_ID,
            envelope_status="MANUAL_SEND_RECORDED",
            envelope_row_version=2,
            prepared_by_you=True,
        )
    )
    recorded_block = page.locator("#manual-step-4 [data-send-phase='recorded']")
    check(
        "a recorded send is the done state, and still says recorded is not delivered",
        page.locator("#manual-step-4[data-state='done']").count() == 1
        and recorded_block.count() == 1
        and "không có nghĩa là khách đã nhận" in (recorded_block.text_content() or "")
        and page.locator("button", has_text="Chứng thực rằng tôi đã gửi tin này").count() == 0,
    )

    # Stale: the draft moved past what was approved. Say so; ask again from the new read.
    state["message_edited"] = True
    posts_before = len(state.get("approval_posts", []))
    reopen(progress_body(status="APPROVED"))
    stale = page.locator("#manual-step-2 [data-send-phase='stale']")
    check(
        "a draft edited after its approval says the approval is stale, with both versions",
        stale.count() == 1
        and "v1" in (stale.text_content() or "")
        and "v2" in (stale.text_content() or "")
        # The lock waits, shut away under the fallback fields, for an approval that can be spent.
        and not page.locator("button[data-lock-envelope]").first.is_visible()
        and page.locator("#manual-step-3[data-state='todo']").count() == 1,
    )
    page.locator("button[data-raise-envelope]", has_text="Xin duyệt lại").first.click()
    page.wait_for_timeout(700)
    raised = [json.loads(p or "{}") for p in state.get("approval_posts", [])]
    check(
        "'Xin duyệt lại' raises a new approval over the new words, from the new read",
        len(raised) == posts_before + 1
        and raised[-1].get("resource_version") == 2
        and raised[-1].get("rendered_hash") == "JCS-SHA256-V1:" + "9" * 64,
        repr(raised[-1:]),
    )
    state["message_edited"] = False

    # Lapsed: stored REQUESTED, past its expiry by the server's clock.
    reopen(progress_body(past_expiry=True))
    check(
        "a lapsed approval says so and offers 'Xin duyệt lại'",
        page.locator("#manual-step-2 [data-send-phase='lapsed']").count() == 1
        and page.locator("button[data-raise-envelope]", has_text="Xin duyệt lại").count() == 1,
    )
    state["send_progress"] = None

    print()
    print("=" * 74)
    print("17. ĐỌC LẠI — who works here, what an order issued, what an incident holds, and where")
    print("    the customer came from")
    print("=" * 74)

    # READ-PATHS-001. Four reads the console's own gap register admitted it lacked. What a browser
    # can prove that a source test cannot: that each reaches the screen that needs it, that a spent
    # credit offers no copy control while an unspent one does, that the directory's buttons fill
    # the *existing* forms rather than replacing them, and that a name typed as markup stays text.

    page.evaluate(f"location.hash = '#/orders/{ORDER_VIEW_ID}'")
    page.wait_for_timeout(1200)
    text = rendered_text()
    source = page.locator("[data-field=acquisition-source]")
    # Spec V2 §4.1: an operator surface shows the gloss; the token stays on the element (title)
    # and in "Chi tiết kỹ thuật".
    check(
        "the order detail shows the recorded acquisition source, glossed, its token kept",
        source.count() == 1
        and (source.text_content() or "") == "Tìm trên Google"
        and source.get_attribute("title") == "GOOGLE_MAPS",
        repr(source.text_content()) if source.count() else "absent",
    )
    check(
        "and says beside it that the value cannot be corrected",
        "ghi rồi thì không sửa được" in text,
    )
    check(
        "the credits are read from the order's own store, by the order's id",
        any(
            f"/stores/{STORE}/orders/{ORDER_VIEW_ID}/remedy-credits" in url
            for url in state.get("credit_reads", [])
        ),
        repr(state.get("credit_reads")),
    )
    unused = page.locator(f"#order-remedy-credits li[data-credit-id='{UNUSED_CREDIT_ID}']")
    spent = page.locator(f"#order-remedy-credits li[data-credit-id='{SPENT_CREDIT_ID}']")
    check(
        "the section is headed for the counter and lists both credits",
        "Khoản giảm trừ" in text and unused.count() == 1 and spent.count() == 1,
    )
    check(
        "an unused code is printed in full with a copy control",
        UNUSED_CREDIT_ID in (unused.text_content() or "")
        and unused.locator("button", has_text="Sao chép").count() == 1
        and unused.get_attribute("data-credit-status") == "UNUSED",
    )
    check(
        "a spent code offers no copy control, and says it was used",
        spent.locator("button").count() == 0
        and "Đã dùng" in (spent.text_content() or "")
        and SPENT_CREDIT_ID not in (spent.text_content() or ""),
        repr(spent.text_content()),
    )
    check(
        "the amounts are the server's integers, formatted and not rounded",
        "13.200" in (unused.text_content() or "") and "80.000" in (spent.text_content() or ""),
    )
    # READ-ENRICH-001: the order's own complaints, from the order's store, the customer's words as
    # text (never markup), each opening the complaint it names.
    check(
        "the order's complaints are read from the order's own store, by the order's id",
        any(
            f"/stores/{STORE}/orders/{ORDER_VIEW_ID}/incidents" in url
            for url in state.get("order_incident_reads", [])
        ),
        repr(state.get("order_incident_reads")),
    )
    complaint = page.locator("a[data-incident-id]")
    check(
        "each complaint opens its incident, and the customer's words stay text",
        complaint.count() == 1
        and INCIDENTS[0]["incident_id"] in (complaint.first.get_attribute("href") or "")
        and ORDER_INCIDENT_SUMMARY in (complaint.first.text_content() or "")
        and page.locator("main img").count() == 0,
    )
    check(
        "a closed order offers no next step and says so",
        page.locator("button[data-step]").count() == 0 and "Đơn đã đóng" in text,
    )
    check(
        "the four raw axes and the row version are only in 'Chi tiết kỹ thuật'",
        "RELEASED" not in page.inner_text("main")
        and ORDER_VIEW_ID not in page.inner_text("main")
        and "v14" in (page.locator("details.tech").text_content() or "")
        and "RELEASED" in (page.locator("details.tech").text_content() or ""),
    )
    check(
        "no expiry is invented for a credit",
        "Máy chủ không ghi hạn dùng" in text and "hết hạn" not in (unused.text_content() or ""),
    )

    state["recorded_listed"] = True
    incident = str(INCIDENTS[0]["incident_id"])
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate(f"location.hash = '#/remedies?incident={incident}'")
    page.wait_for_timeout(1200)
    recorded = page.locator("#remedy-recorded-proposals li[data-proposal-id]")
    check(
        "reading an incident lists every proposal the server recorded on it",
        recorded.count() == 2,
        f"{recorded.count()} rows",
    )
    check(
        "the list is the incident's, read from the selected store",
        any(
            f"/stores/{STORE}/incidents/{incident}/remedy-proposals" in url
            for url in state.get("proposal_reads", [])
        ),
    )
    lapsed = page.locator(
        "#remedy-recorded-proposals li[data-proposal-status=OWNER_APPROVAL_REQUIRED]"
    )
    legacy = page.locator("#remedy-recorded-proposals li[data-proposal-status=POLICY_UNRESOLVED]")
    check(
        "an owner envelope that ran out says the owner can no longer decide it",
        "quá hạn" in (lapsed.text_content() or "")
        and "Nguyễn Thị Lan" in (lapsed.text_content() or ""),
        repr(lapsed.text_content()),
    )
    check(
        "a loss recorded before DEC-031 is listed, with no figure rather than 0 ₫",
        legacy.count() == 1
        and "Không có số tiền" in (legacy.text_content() or "")
        and "0 ₫" not in (legacy.text_content() or ""),
        repr(legacy.text_content()),
    )
    state["recorded_listed"] = False

    # CONSOLE-REDESIGN-006 rebuilt #/staff around the person: a row opens that person's sheet, and
    # the sheet carries every command, so nothing below types or pastes a staff id. What these
    # checks prove is unchanged from the V1 form-per-endpoint screen: the list is read only with a
    # store, untrusted names stay text, status and roles are on the row, a disabled account is not
    # offered disable, opening a person sends nothing, and each write carries its own key.
    page.evaluate("location.hash = '#/staff'")
    page.wait_for_timeout(1200)
    rows = page.locator("#staff-directory [data-staff-id]")
    check(
        "the owner sees who works in the selected store",
        rows.count() == 2 and bool(state.get("staff_reads")),
        f"{rows.count()} rows",
    )
    active_row = page.locator(f"#staff-directory [data-staff-id='{STAFF_ACTIVE_ID}']")
    disabled_row = page.locator(f"#staff-directory [data-staff-id='{STAFF_DISABLED_ID}']")
    check(
        "a display name that is markup is printed as text and never parsed",
        HOSTILE_NAME in (active_row.text_content() or "")
        and active_row.locator("img").count() == 0
        and page.evaluate("window.__staffInjected") is None,
    )
    check(
        "each person's roles and status are on the row (gloss shown, token kept on the pill)",
        "Người duyệt vận hành" in (active_row.text_content() or "")
        and active_row.locator("[data-token='OPS_APPROVER']").count() == 1
        and "Đã vô hiệu hoá" in (disabled_row.text_content() or "")
        and disabled_row.locator("[data-token='DISABLED']").count() == 1
        and "Thu hồi lúc" in (disabled_row.text_content() or ""),
    )
    writes_before = len(state.get("staff_writes", []))
    disabled_row.click()
    page.wait_for_timeout(400)
    sheet_node = page.locator("dialog#staff-person")
    check(
        "a disabled account's sheet offers no disable, and says it cannot be switched back on",
        sheet_node.get_attribute("open") is not None
        and sheet_node.get_attribute("data-staff-id") == STAFF_DISABLED_ID
        and page.locator("#staff-disable-submit").count() == 0
        and "Tài khoản đã vô hiệu hoá" in (sheet_node.text_content() or ""),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    active_row.click()
    page.wait_for_timeout(400)
    check(
        "one tap on a person opens their sheet, with every command on it and nothing sent",
        sheet_node.get_attribute("data-staff-id") == STAFF_ACTIVE_ID
        and page.locator("#staff-role-submit").count() == 1
        and page.locator("#staff-store-submit").count() == 1
        and page.locator("#staff-store-revoke").count() == 1
        and page.locator("#staff-disable-submit").count() == 1
        and len(state.get("staff_writes", [])) == writes_before
        and "Bấm lần nữa" not in rendered_text(),
    )
    check(
        "no staff id is typed for a listed person: the only id field is the store's manual entry",
        page.locator("#staff-role-id, #staff-store-staff, #staff-disable-id").count() == 0
        and page.locator("#staff-store-manual").count() == 1,
    )
    page.locator("#staff-disable-submit").click()
    page.wait_for_timeout(300)
    check(
        "disabling is two-press: the first press arms it and sends nothing",
        len(state.get("staff_writes", [])) == writes_before
        and "Bấm lần nữa" in (page.locator("#staff-disable-submit").text_content() or ""),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    page.locator("#staff-create-open").click()
    page.wait_for_timeout(300)
    page.locator("#staff-create-subject").fill("stub|new-person")
    page.locator("#staff-create-name").fill("Phạm Văn Mới")
    page.locator("#staff-create-submit").click()
    page.wait_for_timeout(800)
    writes = state.get("staff_writes", [])[writes_before:]
    check(
        "creating somebody who belongs to no store yet opens their sheet straight away",
        bool(writes)
        and writes[0][1] == "staff"
        and json.loads(writes[0][2] or "{}")
        == {"oidc_subject": "stub|new-person", "display_name": "Phạm Văn Mới", "email": None}
        and sheet_node.get_attribute("open") is not None
        and sheet_node.get_attribute("data-staff-id") == STAFF_NEW_ID,
        repr(writes[:1]),
    )
    page.locator("#staff-role-pick [data-value='DRIVER']").click()
    page.locator("#staff-role-submit").click()
    page.wait_for_timeout(800)
    page.locator("#staff-store-submit").click()
    page.wait_for_timeout(800)
    writes = state.get("staff_writes", [])[writes_before:]
    check(
        "role and store land on the new person, from their sheet, each with its own key",
        len(writes) == 3
        and writes[1][:2] == ("POST", f"staff/{STAFF_NEW_ID}/roles")
        and json.loads(writes[1][2] or "{}") == {"role": "DRIVER"}
        and writes[2][:2] == ("POST", f"staff/{STAFF_NEW_ID}/stores/{STORE}")
        and all(write[3] for write in writes)
        and len({write[3] for write in writes}) == 3,
        repr([write[:2] for write in writes]),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    print()
    print("=" * 74)
    print("18. DUYỆT BỒI HOÀN — the owner reads and decides the claim; the counter pays it later")
    print("=" * 74)

    # `REMEDY-OWNER-DECIDE-001`. Since DEC-031 every loss waits on an `APPROVE_REMEDY` envelope
    # only the owner may decide, and until this item the card showed nothing about one and kept
    # both buttons shut; an approved claim could then be paid only from the browser session that
    # proposed it. What only a browser can prove: the figures are on the card above an approve
    # control that is pressable; the press hands back the envelope's own binding; a stale claim
    # withholds the figures and leaves only "Từ chối"; and the incident's list offers the execute
    # press on exactly the row the server marks executable.

    def press(control) -> None:  # type: ignore[no-untyped-def]
        """Press a control only if it can be pressed: a shut one is what the checks report, and
        waiting thirty seconds on it would end the run before the later checks are reached."""

        if control.count() and control.is_enabled():
            control.click()

    state["remedy_listed"] = True
    state["remedy_stale"] = False
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(1000)
    text = rendered_text()
    card = page.locator("article.card").first
    card_text = card.text_content() or ""

    check(
        "the card read the claim through the envelope's own store and proposal",
        any(
            f"/stores/{STORE}/remedy-proposals/{OWNER_REMEDY_PROPOSAL}/approval-binding" in u
            for u in state.get("remedy_binding_reads", [])
        ),
        repr(state.get("remedy_binding_reads")),
    )
    fields = page.evaluate(
        """() => Object.fromEntries(
             [...document.querySelectorAll("article.card [data-field^='remedy-']")]
               .map((n) => [n.getAttribute("data-field"), n.textContent]))"""
    )
    check(
        "the card names the kind, with its token",
        "Mất đồ (LOST_ITEM)" in str(fields.get("remedy-kind")),
        repr(fields.get("remedy-kind")),
    )
    check(
        "the amount, the ceiling and the staff limit are the server's integers, formatted",
        "52.500" in str(fields.get("remedy-amount"))
        and "250.000" in str(fields.get("remedy-ceiling"))
        and "100.000" in str(fields.get("remedy-staff-limit")),
        repr(fields),
    )
    check(
        "the item is the line's service and the garment the claim names",
        "Áo sơ mi giặt ủi (SHIRT_WASH_PRESS)" in str(fields.get("remedy-item"))
        and "món thứ 2" in str(fields.get("remedy-item")),
        repr(fields.get("remedy-item")),
    )
    check(
        "why the owner is needed is the recorded reason, in the counter's words",
        "mất đồ luôn do chủ tiệm duyệt" in str(fields.get("remedy-why")),
        repr(fields.get("remedy-why")),
    )
    check(
        "the order is named by the customer's ticket, with a link to it",
        "Phiếu 17" in str(fields.get("remedy-order"))
        and page.locator(f"article.card a[href='#/orders/{ORDER_VIEW_ID}']").count() == 1,
        repr(fields.get("remedy-order")),
    )
    check(
        "the complaint is printed as characters and never becomes an element",
        '<img src=x onerror="window.__remedyPwned=1">' in str(fields.get("remedy-summary"))
        and page.locator("article.card img").count() == 0
        and page.evaluate("window.__remedyPwned") is None,
        repr(fields.get("remedy-summary")),
    )
    check(
        "the envelope's countdown reads in hours, open to the end of the next business day",
        "còn 19 giờ" in card_text or "còn 20 giờ" in card_text,
        card_text[:200],
    )
    check(
        "the queue names the action and its resource type",
        "APPROVE_REMEDY" in text and "REMEDY_PROPOSAL" in text,
    )
    ordering = page.evaluate(
        """() => {
          const card = document.querySelector("article.card");
          if (!card) return "no card";
          const approve = [...card.querySelectorAll("button")]
            .find((b) => b.textContent.trim() === "Duyệt");
          const amount = card.querySelector("[data-field='remedy-amount']");
          if (!approve) return "no approve control";
          if (!amount) return "the amount is not rendered";
          const before =
            Boolean(amount.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING);
          return { before, disabled: approve.disabled };
        }"""
    )
    check(
        "the figures precede the approve control, and it is pressable",
        ordering == {"before": True, "disabled": False},
        repr(ordering),
    )

    before = len(state.get("decision_posts", []))
    press(page.locator("article.card button", has_text="Duyệt").first)
    page.wait_for_timeout(700)
    posts = [json.loads(p or "{}") for p in state.get("decision_posts", [])]
    check(
        "pressing Duyệt sends back the envelope's own version and both digests",
        len(posts) == before + 1
        and posts[-1].get("decision") == "APPROVED"
        and posts[-1].get("resource_version") == 1
        and posts[-1].get("snapshot_hash") == OWNER_REMEDY_SNAPSHOT
        and posts[-1].get("rendered_hash") == OWNER_REMEDY_RENDERED,
        repr(posts[-1:]),
    )

    # The order was re-priced under the envelope: the server resolves a different digest and says
    # the envelope no longer matches. The figures are withheld and only a refusal is offered.
    state["remedy_stale"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(1000)
    text = rendered_text()
    controls = approve_state()
    check(
        "a stale claim leaves Duyệt shut and Từ chối usable",
        controls == {"approve": True, "refuse": False},
        repr(controls),
    )
    check(
        "its figures are withheld rather than shown with a caveat",
        "52.500" not in text
        and "Áo sơ mi giặt ủi" not in text
        and page.locator("article.card [data-field^='remedy-']").count() == 0,
    )
    check(
        "and the card names the reason",
        "Khoản bồi hoàn đã đổi so với phiếu" in text
        and page.locator("article.card [data-remedy-stale]").count() == 1,
    )
    before = len(state.get("decision_posts", []))
    press(page.locator("article.card button", has_text="Từ chối").first)
    page.wait_for_timeout(700)
    posts = [json.loads(p or "{}") for p in state.get("decision_posts", [])]
    check(
        "refusing the stale claim sends a REJECTED decision",
        len(posts) == before + 1 and posts[-1].get("decision") == "REJECTED",
        repr(posts[-1:]),
    )
    state["remedy_stale"] = False
    state["remedy_listed"] = False

    # Later, on the counter: the incident's list, with no session state from any proposal.
    state["recorded_listed"] = True
    state["recorded_executable"] = True
    state["owner_approved"] = True
    state["execution_posts"] = []
    incident = str(INCIDENTS[0]["incident_id"])
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate(f"location.hash = '#/remedies?incident={incident}'")
    page.wait_for_timeout(1200)
    buttons = page.evaluate(
        """() => [...document.querySelectorAll(
                 "#remedy-recorded-proposals li[data-proposal-id] button[data-remedy-execute]")]
               .map((b) => ({
                 row: b.closest("li").getAttribute("data-proposal-id"),
                 step: b.closest("li").getAttribute("data-proposal-next-step"),
                 label: b.textContent.trim(),
                 disabled: b.disabled,
               }))"""
    )
    check(
        "only the owner-approved, unexecuted row offers the execute press",
        buttons
        == [
            {
                "row": OWNER_REMEDY_PROPOSAL,
                "step": "EXECUTE",
                "label": "Thực hiện bồi hoàn",
                "disabled": False,
            }
        ],
        repr(buttons),
    )
    waiting = page.locator(
        f"#remedy-recorded-proposals li[data-proposal-id='{OWNER_REMEDY_WAITING}']"
    )
    check(
        "a claim still waiting for the owner says so plainly and offers nothing",
        "đang chờ chủ tiệm duyệt" in (waiting.text_content() or "")
        and waiting.locator("button[data-remedy-execute]").count() == 0,
        repr(waiting.text_content()),
    )
    lapsed = page.locator("#remedy-recorded-proposals li[data-proposal-next-step=PROPOSE_AGAIN]")
    check(
        "an envelope that ran out says to propose again",
        lapsed.count() == 1 and "đề nghị lại" in (lapsed.text_content() or ""),
        repr(lapsed.text_content()) if lapsed.count() else "absent",
    )
    approved_row = page.locator(
        f"#remedy-recorded-proposals li[data-proposal-id='{OWNER_REMEDY_PROPOSAL}']"
    )
    check(
        "the approved row's badge no longer says it waits for the owner",
        "Chủ tiệm đã duyệt, chờ thực hiện" in (approved_row.text_content() or ""),
        repr(approved_row.text_content()),
    )
    reads_before = len(state.get("proposal_reads", []))
    press(approved_row.locator("button[data-remedy-execute]").first)
    page.wait_for_timeout(1000)
    executed = state.get("execution_posts", [])
    check(
        "the press calls the existing execute route for that proposal, with a key and no body",
        len(executed) == 1
        and executed[0][0].endswith(
            f"/internal/v1/remedy-proposals/{OWNER_REMEDY_PROPOSAL}/execution"
        )
        and bool(executed[0][1]),
        repr(executed),
    )
    result = page.locator("#remedy-recorded-execution")
    # Read once, and only if the host exists: a console without it must fail the checks below,
    # not end the run waiting thirty seconds for an element that is never coming.
    result_text = (result.text_content() or "") if result.count() else ""
    check(
        "the issued credit code is printed in full under the list, with a copy control",
        REMEDY_CREDIT_ID in result_text
        and result.locator("button", has_text="Sao chép").count() >= 1,
        repr(result_text),
    )
    check(
        "and the list is re-read from the server rather than edited in place",
        len(state.get("proposal_reads", [])) > reads_before,
    )
    check(
        "paying one claim from the list claims nothing about closing the incident",
        bool(result_text) and "CLOSED" not in result_text,
        repr(result_text),
    )
    state["recorded_executable"] = False
    state["recorded_listed"] = False

    print()
    print("=" * 74)
    print(
        "19. GỬI THỦ CÔNG — a STOP refuses the send; only the customer's own later message lifts it"
    )
    print("=" * 74)

    # `CONSENT-TRANSACTIONAL-001` (`DEC-033`). What a browser can prove that the HTTP tests cannot:
    # that step 0 opens the contact's service-messaging card from the server's read, that a
    # refusal's headline is the server's reason in Vietnamese rather than "không khoá được", that
    # the release sends the message picked from the server's list and nothing composed, and that a
    # role that may not release is shown the control shut with the rule under it.

    state["service_state"] = "SUPPRESSED"
    state["service_reads"] = []
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate(f"location.hash = '#/exceptions?draft={MESSAGE_DRAFT_ID}'")
    page.wait_for_timeout(1200)
    card = page.locator("#manual-service-messaging")
    card_text = (card.text_content() or "") if card.count() else ""
    check(
        "step 0 opens the contact's service-messaging card, read from the draft's own store",
        any(
            f"/stores/{STORE}/contacts/{MESSAGE_RECIPIENT}/service-messaging?channel=INTERNAL_TEST"
            in url
            for url in state.get("service_reads", [])
        )
        and card.count() == 1
        and card.is_visible(),
        repr(state.get("service_reads")),
    )
    section = page.locator("#manual-service-messaging section[data-transactional-state]")
    check(
        "the card states the STOP in Vietnamese, with the server's state and reason on the element",
        section.count() == 1
        and section.get_attribute("data-transactional-state") == "SUPPRESSED"
        and section.get_attribute("data-egress-reason") == "SUPPRESSED"
        and "Khách đã yêu cầu dừng nhận tin trên kênh này" in card_text,
        repr(card_text[:300]),
    )
    check(
        "and says a release lifts service messages only, marketing stays blocked",
        "Tin quảng cáo vẫn bị chặn" in card_text,
    )

    # A blocked send looks blocked. The server's own consent read says the send is not allowed, so
    # step 2's press is shut with the reason as visible text under it -- a prediction; the server
    # still re-checks on any press -- and the refusal sentence is said once, on the card.
    raise_control = page.locator("button[data-raise-envelope]")
    check(
        "with the send suppressed, 'Xin duyệt gửi đúng tin này' is shut, marked with the reason",
        raise_control.count() == 1
        and not raise_control.is_enabled()
        and raise_control.get_attribute("data-consent-blocked") == "SUPPRESSED",
    )
    check(
        "and the reason is visible text under it, pointing at the card rather than repeating it",
        "khách này đang không nhận tin dịch vụ"
        in (page.locator("#manual-step-2").inner_text() or ""),
    )

    def visible_count(sentence: str) -> int:
        return (page.locator("main").inner_text() or "").count(sentence)

    check(
        "the STOP sentence appears exactly once on the visible screen",
        visible_count("Khách đã yêu cầu dừng nhận tin trên kênh này") == 1,
        str(visible_count("Khách đã yêu cầu dừng nhận tin trên kênh này")),
    )
    options = page.evaluate(
        """() => [...document.querySelectorAll("#service-release-evidence option")]
                 .map((o) => o.value)"""
    )
    check(
        "the evidence is a picker over the server's list, nothing typed",
        options == [item["webhook_event_id"] for item in SERVICE_EVIDENCE]
        and page.locator("#manual-service-messaging input[type=text]").count() == 0,
        repr(options),
    )
    release_button = page.locator("button[data-service-release]")
    check(
        "an owner is offered the release control",
        release_button.count() == 1 and release_button.is_enabled(),
    )
    # `CONSOLE-REDESIGN-005`: the release is a sheet. The button opens it; the picker lists the
    # customer's messages by the time they arrived, never by id.
    opener = page.locator("button[data-service-release-open]")
    check(
        "the release opens from a button on the card, and the sheet names messages by time only",
        opener.count() == 1
        and opener.is_enabled()
        and page.evaluate(
            """() => [...document.querySelectorAll("#service-release-evidence option")]
                     .every((o) => o.textContent.startsWith("Khách nhắn lúc ")
                                   && !o.textContent.includes(o.value.slice(0, 8)))"""
        ),
    )
    opener.click()
    page.wait_for_timeout(300)
    page.locator("#service-release-evidence").select_option(SERVICE_EVIDENCE[1]["webhook_event_id"])
    press(release_button)
    page.wait_for_timeout(900)
    posts = state.get("release_posts", [])
    check(
        "the release sends the picked message and the channel, under an idempotency key",
        len(posts) == 1
        and json.loads(posts[0][0] or "{}")
        == {
            "channel": "INTERNAL_TEST",
            "evidence_webhook_event_id": SERVICE_EVIDENCE[1]["webhook_event_id"],
        }
        and bool(posts[0][1]),
        repr(posts),
    )
    result_text = page.locator("#service-release-result").text_content() or ""
    section = page.locator("#manual-service-messaging section[data-transactional-state]")
    check(
        "after the release the card is re-read from the server and says a send is allowed",
        "Đã gỡ chặn tin dịch vụ" in result_text
        and section.count() == 1
        and section.get_attribute("data-egress-decision") == "ALLOW"
        and page.locator("button[data-service-release]").count() == 0,
        repr(result_text),
    )

    # Released: the server's read now allows the send, so the press is live again.
    check(
        "after the release the 'Xin duyệt' press is live again",
        page.locator("button[data-raise-envelope]").count() == 1
        and page.locator("button[data-raise-envelope]").is_enabled()
        and page.locator("button[data-raise-envelope]").get_attribute("data-consent-blocked")
        is None,
    )

    # Step 3 refused: raise the envelope so the four boxes fill, then press the lock. The server
    # refuses although the card said ALLOW (it re-checks on every press). SUPPRESSED goes last:
    # after it the card says the send is blocked and the lock is shut, as it should be.
    page.locator("button", has_text="Xin duyệt gửi đúng tin này").first.click()
    page.wait_for_timeout(700)
    refusals = {
        "MESSAGING_POLICY_UNPUBLISHED": "Chủ tiệm chưa công bố chính sách tin dịch vụ",
        "NO_SERVICE_BASIS": "Chưa có căn cứ để gửi tin dịch vụ",
        "PENDING_REVIEW": "có thể là yêu cầu dừng nhận tin",
        "SUPPRESSED": "Khách đã yêu cầu dừng nhận tin trên kênh này",
    }
    for reason, sentence in refusals.items():
        state["prepare_refusal"] = reason
        state["service_state"] = "SUPPRESSED" if reason == "SUPPRESSED" else "ALLOW"
        reads_before = len(state.get("service_reads", []))
        page.locator("button", has_text="Khoá phong bì cho người gửi tay").first.click()
        page.wait_for_timeout(800)
        refused = page.locator(f"[data-consent-refusal='{reason}']")
        notice = refused.locator(".notice[data-reason-codes]")
        panel_text = rendered_text()
        visible = page.locator("main").inner_text() or ""
        on_card = refused.get_attribute("data-reason-on-card") if refused.count() else None
        card_says = sentence in (page.locator("#manual-service-messaging").inner_text() or "")
        check(
            f"a {reason} refusal at step 3 says the server's reason in Vietnamese, exactly once",
            refused.count() == 1
            and visible.count(sentence) == 1
            # Where it is said: on the card when the card's re-read says the same, else here.
            and (
                (on_card == "true" and card_says)
                or (on_card == "false" and sentence in (refused.inner_text() or ""))
            ),
            f"{visible.count(sentence)} times; on card: {on_card}",
        )
        check(
            f"and the {reason} refusal keeps its codes and owner decision in the tech drawer",
            notice.count() == 1
            and reason in (notice.get_attribute("data-reason-codes") or "").split()
            and notice.get_attribute("data-decision") == "DEC-033"
            and "DEC-033" in (notice.locator("details.tech").text_content() or "")
            and "DEC-033" not in (notice.inner_text() or ""),
            repr(notice.get_attribute("data-reason-codes") if notice.count() else None),
        )
        check(
            f"and the {reason} refusal locks nothing and opens the contact's card again",
            "Phong bì đã khoá" not in panel_text
            and len(state.get("service_reads", [])) > reads_before,
        )
    lock_control = page.locator("button[data-lock-envelope]")
    check(
        "once the card says the send is blocked, the lock is shut with the reason under it",
        lock_control.count() == 1
        and not lock_control.is_enabled()
        and lock_control.get_attribute("data-consent-blocked") == "SUPPRESSED",
    )
    state["prepare_refusal"] = None

    # No message from the customer since the STOP: nothing to cite, so no release control at all.
    state["service_state"] = "SUPPRESSED_NO_EVIDENCE"
    page.locator("button", has_text="Đọc lại tin nhắn").first.click()
    page.wait_for_timeout(900)
    check(
        "with no later message from the customer the card offers no release, and says why",
        page.locator("#manual-service-messaging [data-release-evidence=none]").count() == 1
        and page.locator("button[data-service-release]").count() == 0
        and "chưa có tin nhắn nào của chính khách" in rendered_text(),
    )

    # An operator sees the release shut, with the rule under it.
    state["service_state"] = "SUPPRESSED"
    SESSION_OK["roles"] = ["OPERATOR"]
    page.reload()
    page.wait_for_timeout(1500)
    shut = page.locator("button[data-service-release]")
    check(
        "an operator is shown the release control shut, with who may release it",
        shut.count() == 1
        and not shut.is_enabled()
        and "chỉ dành cho chủ tiệm hoặc người duyệt" in rendered_text(),
        f"{shut.count()} controls",
    )
    check(
        "and the evidence picker is shut with it",
        page.locator("#service-release-evidence").count() == 1
        and not page.locator("#service-release-evidence").is_enabled(),
    )
    SESSION_OK["roles"] = ["OWNER_ADMIN"]
    state["service_state"] = None

    print("=" * 74)
    print("20. PHIẾU CHO KHÁCH — the receipt prints what the server holds, and nothing else")
    print("=" * 74)

    def main_text() -> str:
        return page.locator("main").inner_text() or ""

    formatted = page.evaluate(
        """async (amounts) => {
            const format = await import('./src/core/format.js');
            return amounts.map((amount) => format.money(amount));
        }""",
        [RECEIPT_TOTAL_VND, 120_000, 30_000, 36_000, 11_000, 15_000],
    )
    total_text, wash_text, iron_text, promo_text, credit_text, fee_text = formatted

    # First paint: the order read alone. The quote read fails, so the lines are not in.
    state["receipt_quote_fails"] = True
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate(f"location.hash = '#/orders/{RECEIPT_ORDER_ID}/receipt'")
    page.wait_for_timeout(1500)
    paper = page.locator("#receipt-paper")
    printer = page.locator("#receipt-print")
    check(
        "with the lines unread, the total on the paper is the server's figure, verbatim",
        paper.locator("[data-total]").count() == 1
        and paper.locator("[data-total]").inner_text().strip() == total_text,
        paper.locator("[data-total]").inner_text() if paper.locator("[data-total]").count() else "",
    )
    check(
        "and 'In phiếu' is shut with its reason: a receipt without its lines is not handed over",
        printer.count() == 1
        and printer.is_disabled()
        and "Chưa in được: chưa đọc được các món của đơn." in main_text(),
    )
    reads = state.get("receipt_quote_reads") or []
    check(
        "the lines are read from the revision the order is bound to, in the order's store",
        bool(reads) and f"/stores/{STORE}/quotes/{RECEIPT_QUOTE_ID}?revision=3" in reads[-1],
        reads[-1] if reads else "no read",
    )

    # Second paint: the lines, once the read answers.
    state["receipt_quote_fails"] = False
    page.locator("#receipt-paper button", has_text="Đọc lại các món").first.click()
    page.wait_for_timeout(1200)
    lines = [
        node.inner_text().strip()
        for node in paper.locator(".receipt-paper__line .receipt-paper__value").all()
    ]
    check(
        "each line prints at its list amount, with its quantity and published name",
        lines == [wash_text, iron_text]
        and "Giặt sấy dưới 6kg" in paper.inner_text()
        and "4.8 kg" in paper.inner_text()
        and "3 cái" in paper.inner_text(),
        repr(lines),
    )
    adjustments = {
        str(node.get_attribute("data-adjustment")): node.inner_text().replace("\n", " ")
        for node in paper.locator("[data-adjustment]").all()
    }
    check(
        "the promotion and the credit are printed as taken off, the delivery fee as added",
        adjustments.get("PROMOTION") == f"Khuyến mãi {MINUS}{promo_text}"
        and adjustments.get("REMEDY_CREDIT") == f"Khoản giảm trừ {MINUS}{credit_text}"
        and adjustments.get("DELIVERY") == f"Phí giao +{fee_text}",
        repr(adjustments),
    )
    if os.environ.get("CONSOLE_STUB_SHOTS"):
        os.makedirs(os.environ["CONSOLE_STUB_SHOTS"], exist_ok=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        page.screenshot(
            path=os.path.join(os.environ["CONSOLE_STUB_SHOTS"], "stub-receipt-phone.png"),
            full_page=True,
        )
        page.set_viewport_size({"width": 1280, "height": 900})
    check(
        "and the total is still exactly the server's, never re-added on the screen",
        paper.locator("[data-total]").inner_text().strip() == total_text,
    )
    check(
        "it names the ticket and its day, and promises no ready time (R4)",
        paper.locator("[data-field=ticket]").inner_text().strip() == "Phiếu 23"
        and "25/09/2026" in paper.inner_text()
        and paper.locator("[data-field=closing]").inner_text().strip()
        == "Tiệm sẽ báo khi đồ sẵn sàng."
        and "hẹn" not in paper.inner_text().lower(),
    )
    check(
        "CUSTOMER-001: the paper names the customer the order was taken for",
        paper.locator("[data-field=customer]").inner_text().strip() == "chị Lan",
    )
    check(
        "a store the server holds no name for gets no name line: nothing is invented",
        paper.locator("[data-field=store]").count() == 0,
    )
    visible = main_text()
    check(
        "no identifier is visible on the receipt screen: the reference is eight characters",
        re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", visible, re.I)
        is None
        and paper.locator("[data-field=reference]").inner_text().split()[-1] == "ABCDEF01",
        visible[:160],
    )
    check(
        "with the lines in, 'In phiếu' opens the print dialog",
        printer.is_enabled(),
    )
    page.evaluate(
        "() => { window.__printed = 0; window.print = () => { window.__printed += 1; }; }"
    )
    printer.click()
    check("one press, one print dialog", page.evaluate("() => window.__printed") == 1)
    check(
        "'Chia sẻ' is not offered by a browser that cannot share",
        page.evaluate("() => typeof navigator.share") != "function"
        and page.locator("#receipt-share").count() == 0,
    )

    # Print media: only the slip.
    page.emulate_media(media="print")
    page.wait_for_timeout(200)
    shown = page.evaluate(
        """() => ['.appbar', '.nav', '.banners', '.toasts', '.action-bar', '.page-head',
                  '#receipt-print']
            .flatMap((s) => [...document.querySelectorAll(s)])
            .filter((e) => e.getClientRects().length > 0)
            .map((e) => e.className || e.id)"""
    )
    check(
        "printed, the app bar, tab bar, toasts, header and buttons are hidden; the slip is not",
        shown == [] and paper.is_visible(),
        repr(shown),
    )
    for label, width in (("80mm", 302), ("58mm", 219), ("a5", 559)):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(200)
        overflow = page.evaluate(
            "() => { const p = document.querySelector('#receipt-paper');"
            " return p.scrollWidth - p.clientWidth; }"
        )
        check(f"at {label} the slip fits its paper", overflow <= 0, f"{overflow}px")
        shots = os.environ.get("CONSOLE_STUB_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
            paper.screenshot(path=os.path.join(shots, f"stub-print-{label}.png"))
    page.emulate_media(media="screen")
    page.set_viewport_size({"width": 1280, "height": 900})

    # A browser with a share sheet gets "Chia sẻ", and it shares plain text.
    page.add_init_script("navigator.share = async (data) => { window.__shared = data; };")
    page.reload()
    page.wait_for_timeout(1800)
    share = page.locator("#receipt-share")
    if share.count():
        share.click()
        page.wait_for_timeout(300)
    shared = page.evaluate("() => window.__shared || null") or {}
    text = str(shared.get("text") or "")
    check(
        "'Chia sẻ' shares the receipt as plain text: lines, credit, total, no identifier",
        share.count() == 1
        and f"Tổng cộng: {total_text}" in text
        and f"Khoản giảm trừ: {MINUS}{credit_text}" in text
        and "Tiệm sẽ báo khi đồ sẵn sàng." in text
        and re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text, re.I) is None,
        text[:200],
    )

    # CUSTOMER-001: a walk-in who left no number is not promised a call; the slip is how they
    # get their laundry back.
    state["receipt_order"] = {
        **RECEIPT_ORDER,
        "customer_id": None,
        "customer_name": None,
        "customer_has_phone": False,
    }
    page.reload()
    page.wait_for_timeout(1500)
    check(
        "a walk-in with no number is told to keep the slip, and no name is printed",
        paper.locator("[data-field=closing]").inner_text().strip() == "Giữ phiếu này để nhận đồ."
        and paper.locator("[data-field=customer]").count() == 0,
        paper.locator("[data-field=closing]").inner_text(),
    )

    # A closed order: the receipt says so instead of promising to call.
    state["receipt_order"] = {**RECEIPT_ORDER, "commercial": "COMPLETED", "production": "RELEASED"}
    page.reload()
    page.wait_for_timeout(1500)
    check(
        "a completed order's receipt no longer says the shop will call",
        paper.locator("[data-field=closing]").inner_text().strip() == "Đơn đã hoàn tất.",
    )
    # PROMISE-001: an order with a promise prints the time the customer was told (R4 replaced).
    state["receipt_order"] = {
        **RECEIPT_ORDER,
        "promised_ready_at": "2026-09-26T06:00:00+00:00",
        "current_promise_at": "2026-09-26T06:00:00+00:00",
        "promise_state": "ON_TRACK",
    }
    page.reload()
    page.wait_for_timeout(1500)
    check(
        "an order with a promise prints 'Hẹn trả: 13:00 thứ Bảy 26/9' in place of R4's sentence",
        paper.locator("[data-field=closing]").inner_text().strip() == "Hẹn trả: 13:00 thứ Bảy 26/9",
        paper.locator("[data-field=closing]").inner_text(),
    )
    state["receipt_order"] = None

    # The ways in: a closed order offers it beside the closed line; an AUDITOR sees it shut.
    page.evaluate(f"location.hash = '#/orders/{ORDER_VIEW_ID}'")
    page.wait_for_timeout(1500)
    entry = page.locator(".action-bar--v2 button[data-receipt]")
    check(
        "a closed order's page offers 'In phiếu' in its action bar",
        entry.count() == 1 and entry.is_enabled(),
    )
    if entry.count():
        entry.click()
        page.wait_for_timeout(900)
    check(
        "and it opens that order's receipt",
        page.evaluate("location.hash") == f"#/orders/{ORDER_VIEW_ID}/receipt",
        page.evaluate("location.hash"),
    )
    SESSION_OK["roles"] = ["AUDITOR"]
    page.evaluate(f"location.hash = '#/orders/{ORDER_VIEW_ID}'")
    page.reload()
    page.wait_for_timeout(1800)
    denied = page.locator(".action-bar--v2 button[data-receipt]")
    check(
        "an AUDITOR, who cannot read the quote the receipt prints, sees 'In phiếu' shut with why",
        denied.count() == 1
        and denied.is_disabled()
        and "AUDITOR bị từ chối" in (page.locator(".action-bar--v2").inner_text() or ""),
    )
    SESSION_OK["roles"] = ["OWNER_ADMIN"]
    page.reload()
    page.wait_for_timeout(1200)

    print()
    print("=" * 74)
    print("20. THIẾT BỊ VÀ DUYỆT ĐƠN — sign one device out; an ORDER card says if the order moved")
    print("=" * 74)

    # SESSION-LIST-001. What only a browser can prove: the account sheet names this device and
    # never offers to sign it out; the others are signed out with two presses under a key; a role
    # that may not do it sees the control shut with the reason before the rows; the owner reads a
    # person's devices on their sheet; and an ORDER card compares the order's current version with
    # the envelope's before it lets anyone press Duyệt.
    for flag in ("remedy_listed", "message_listed", "export_listed", "approvals_listed"):
        state[flag] = False
    state["sessions_live"] = [
        device(SESSION_CURRENT, True, "2026-09-25T03:00:00+00:00"),
        device(SESSION_OTHER, False, "2026-09-25T01:30:00+00:00"),
    ]
    # Section 19 hands back the owner's roles without a reload; the principal is re-read here.
    page.evaluate("location.hash = '#/'")
    page.reload()
    page.wait_for_timeout(1500)
    page.locator("button.appbar__account").first.click()
    page.wait_for_timeout(1000)
    rows = page.locator("#account-devices [data-session-id]")
    check(
        "the account sheet lists this person's devices, this one first, named “Thiết bị này”",
        rows.count() == 2
        and rows.first.get_attribute("data-session-id") == SESSION_CURRENT
        and "Thiết bị này" in (rows.first.inner_text() or ""),
        f"{rows.count()} rows",
    )
    check(
        "this device is never offered a sign-out from the list",
        page.locator(f"button[data-revoke-session='{SESSION_CURRENT}']").count() == 0,
    )
    other = page.locator(f"#account-devices button[data-revoke-session='{SESSION_OTHER}']")
    check(
        "the other device offers “Đăng xuất thiết bị này”, live for the owner",
        other.count() == 1
        and other.is_enabled()
        and "Đăng xuất thiết bị này" in (other.inner_text() or ""),
    )
    sheet_text = page.locator("dialog[open]").first.inner_text() or ""
    check(
        "no session identifier is visible outside the technical drawer",
        SESSION_OTHER not in sheet_text and SESSION_CURRENT not in sheet_text,
    )
    other.click()
    page.wait_for_timeout(250)
    check("one press only arms it", not state.get("revoke_posts"))
    other.click()
    page.wait_for_timeout(1200)
    posts = state.get("revoke_posts", [])
    check(
        "the second press signs that one device out, under an idempotency key",
        len(posts) == 1
        and posts[0][0].endswith(f"/internal/v1/sessions/{SESSION_OTHER}/revoke")
        and bool(posts[0][1]),
        repr(posts),
    )
    check(
        "and the list is read again: this device is still there, the other is gone",
        page.locator("#account-devices [data-session-id]").count() == 1
        and page.locator(f"#account-devices [data-session-id='{SESSION_CURRENT}']").count() == 1,
    )

    # A device already signed out (its own Thoát, an idle expiry): the goal state, not an error.
    state["sessions_live"].append(device(SESSION_OTHER, False, "2026-09-25T01:30:00+00:00"))
    state["revoke_status"] = 404
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.locator("button.appbar__account").first.click()
    page.wait_for_timeout(1000)
    again = page.locator(f"#account-devices button[data-revoke-session='{SESSION_OTHER}']")
    again.click()
    page.wait_for_timeout(200)
    again.click()
    page.wait_for_timeout(1200)
    check(
        "a device that was already signed out is said so, and is not shown as a failure",
        "đã đăng xuất rồi" in (page.content() or "")
        and page.locator("#account-devices .notice").count() == 0,
    )
    state["revoke_status"] = None
    state["revoke_posts"] = []
    page.keyboard.press("Escape")

    # A member of staff: their other device is shown, its sign-out shut, the reason said first.
    state["sessions_live"] = [
        device(SESSION_CURRENT, True, "2026-09-25T03:00:00+00:00"),
        device(SESSION_OTHER, False, "2026-09-25T01:30:00+00:00"),
    ]
    SESSION_OK["roles"] = ["OPERATOR"]
    page.reload()
    page.wait_for_timeout(1500)
    page.locator("button.appbar__account").first.click()
    page.wait_for_timeout(1000)
    shut = page.locator(f"#account-devices button[data-revoke-session='{SESSION_OTHER}']")
    reason = page.locator("#account-devices-revoke-reason")
    check(
        "an operator sees the other device's sign-out shut, marked denied, with who may do it",
        shut.count() == 1
        and not shut.is_enabled()
        and shut.get_attribute("data-denied") == "true"
        and shut.get_attribute("aria-describedby") == "account-devices-revoke-reason"
        and "Chỉ chủ đăng xuất được một thiết bị khác" in (reason.inner_text() or ""),
    )
    check(
        "and the reason is read before the rows, not under them",
        page.evaluate(
            """() => {
                const reason = document.querySelector('#account-devices-revoke-reason');
                const list = document.querySelector('#account-devices ul.rows');
                return Boolean(reason && list &&
                  (reason.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING));
            }"""
        ),
    )
    page.keyboard.press("Escape")
    SESSION_OK["roles"] = ["OWNER_ADMIN"]
    page.reload()
    page.wait_for_timeout(1500)

    # The owner, on a person's sheet.
    page.evaluate("location.hash = '#/staff'")
    page.wait_for_timeout(1200)
    page.locator(f"#staff-directory [data-staff-id='{STAFF_ACTIVE_ID}']").first.click()
    page.wait_for_timeout(1200)
    check(
        "the owner reads the person's devices on their sheet, from that person's list",
        any(
            f"/internal/v1/staff/{STAFF_ACTIVE_ID}/sessions" in u
            for u in state.get("session_reads", [])
        )
        and page.locator("#staff-devices [data-session-id]").count() == 2
        and page.locator("#staff-devices button[data-revoke-session]").count() == 2,
        repr(state.get("session_reads", [])[-2:]),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # The ORDER card, order unchanged.
    state["order_listed"] = True
    state["order_envelope_version"] = 14
    state["decision_posts"] = []
    page.evaluate("location.hash = '#/orders'")
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/approvals'")
    page.wait_for_timeout(1400)
    card = page.locator(f"article.card[data-approval-id='{ORDER_APPROVAL}']")
    approve = card.get_by_role("button", name="Duyệt", exact=True)
    refuse = card.get_by_role("button", name="Từ chối", exact=True)
    check(
        "the ORDER card read the order the envelope names",
        any(ORDER_VIEW_ID in u for u in state.get("order_view_reads", [])),
    )
    check(
        "an unchanged order says so in words, above a live Duyệt",
        card.locator("[data-order-version='current']").count() == 1
        and "Đơn chưa thay đổi kể từ khi gửi duyệt" in (card.inner_text() or "")
        and "Phiếu 17" in (card.inner_text() or "")
        and approve.is_enabled()
        and page.evaluate(
            """() => {
                const card = document.querySelector("article.card[data-approval-id]");
                const said = card.querySelector("[data-order-version='current']");
                const press = [...card.querySelectorAll("button")].find(
                  (b) => b.textContent.trim() === "Duyệt");
                return Boolean(said && press &&
                  (said.compareDocumentPosition(press) & Node.DOCUMENT_POSITION_FOLLOWING));
            }"""
        ),
    )
    check(
        "no version number is put in front of the approver",
        "v14" not in (card.inner_text() or ""),
    )
    approve.click()
    page.wait_for_timeout(1200)
    sent = [json.loads(body or "{}") for body in state.get("decision_posts", [])]
    check(
        "Duyệt sends back the envelope's own version and digests",
        len(sent) == 1
        and sent[0].get("decision") == "APPROVED"
        and sent[0].get("resource_version") == 14
        and sent[0].get("snapshot_hash") == "JCS-SHA256-V1:" + "3" * 64
        and sent[0].get("rendered_hash") == "JCS-SHA256-V1:" + "4" * 64,
        repr(sent),
    )

    # The order moved after the envelope was raised.
    state["order_envelope_version"] = 13
    state["decision_posts"] = []
    page.reload()
    page.wait_for_timeout(1600)
    card = page.locator(f"article.card[data-approval-id='{ORDER_APPROVAL}']")
    approve = card.get_by_role("button", name="Duyệt", exact=True)
    refuse = card.get_by_role("button", name="Từ chối", exact=True)
    check(
        "a moved order says so, links to the order, and shuts Duyệt with the reason",
        card.locator("[data-order-version='changed']").count() == 1
        and "Đơn đã thay đổi sau khi gửi duyệt — mở đơn để xem lại" in (card.inner_text() or "")
        and card.locator(f"a[href='#/orders/{ORDER_VIEW_ID}']").count() >= 1
        and not approve.is_enabled()
        and approve.get_attribute("aria-describedby") == "approval-decision-blocked"
        and "đơn đã đổi sau khi gửi duyệt" in (card.inner_text() or ""),
    )
    check(
        "the moved order's summary is withheld, and Từ chối still works",
        "Phiếu 17 ·" not in (card.inner_text() or "") and refuse.is_enabled(),
    )
    refuse.click()
    page.wait_for_timeout(1200)
    sent = [json.loads(body or "{}") for body in state.get("decision_posts", [])]
    check(
        "Từ chối sends REJECTED with the envelope's own version",
        len(sent) == 1
        and sent[0].get("decision") == "REJECTED"
        and sent[0].get("resource_version") == 13,
        repr(sent),
    )

    # The order cannot be read: unknown, so nothing is pressable.
    state["order_envelope_version"] = 14
    state["order_read_fails"] = True
    page.reload()
    page.wait_for_timeout(1600)
    card = page.locator(f"article.card[data-approval-id='{ORDER_APPROVAL}']")
    check(
        "an order that cannot be read leaves both buttons shut and says why",
        not card.get_by_role("button", name="Duyệt", exact=True).is_enabled()
        and not card.get_by_role("button", name="Từ chối", exact=True).is_enabled()
        and "Chưa đối chiếu được đơn với phiếu" in (card.inner_text() or "")
        and "Đơn chưa thay đổi" not in (card.inner_text() or ""),
    )
    state["order_read_fails"] = False

    # An envelope with no store: refused before anything is read.
    state["order_envelope_storeless"] = True
    reads_before = len(state.get("order_view_reads", []))
    page.reload()
    page.wait_for_timeout(1600)
    card = page.locator(f"article.card[data-approval-id='{ORDER_APPROVAL}']")
    check(
        "a store-less ORDER envelope is refused before the order is read",
        not card.get_by_role("button", name="Duyệt", exact=True).is_enabled()
        and "Không biết đơn thuộc cửa hàng nào" in (card.inner_text() or "")
        and len(state.get("order_view_reads", [])) == reads_before,
    )
    state["order_envelope_storeless"] = False
    state["order_listed"] = False

    print()
    print("=" * 74)
    print("19. BÁO CÁO — the owner's numbers, printed as the server sent them")
    print("=" * 74)

    state["report_reads"] = []
    # A fresh load, so the console reads the owner's session again rather than the last section's.
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/reports", wait_until="networkidle")
    page.wait_for_timeout(900)
    reads = list(state["report_reads"])
    check(
        "the report reads the summary and the days for the default seven-day window",
        any("/reports/summary?from=" in url for url in reads)
        and any("/reports/daily?from=" in url for url in reads),
        repr(reads),
    )

    def tile(key: str) -> str:
        node = page.locator(f"[data-kpi={key}]")
        return node.first.inner_text() if node.count() else ""

    check(
        "a fraction is shown beside the percentage the console formats from it, and a count "
        "(report-v3: completed) is a count with no rate beside it",
        "5 / 6" in tile("ON_TIME_INTERNAL")
        and "7" in tile("ORDERS_COMPLETED")
        and "%" not in tile("ORDERS_COMPLETED")
        and "/" not in tile("ORDERS_COMPLETED"),
        tile("ORDERS_COMPLETED")[:80] + " | " + tile("ON_TIME_INTERNAL")[:80],
    )
    check(
        "no denominator is not zero percent: nothing reached quality check reads as unknown",
        "—" in tile("REWASH") and "0%" not in tile("REWASH") and "0 / 0" in tile("REWASH"),
        tile("REWASH")[:80],
    )
    check(
        "the on-time tile says how many of its orders were measured by the internal mark",
        "2 đơn theo mốc nội bộ" in tile("ON_TIME_INTERNAL") and "5 / 6" in tile("ON_TIME_INTERNAL"),
        tile("ON_TIME_INTERNAL")[:80],
    )
    money_tile = tile("MONEY_NET")
    check(
        "a drawer that went down is said in words with the server's amount, never a minus sign",
        "Két giảm" in money_tile
        and "150.000" in money_tile
        and "-150" not in money_tile
        and "\u2212" not in money_tile,
        money_tile[:120],
    )
    check(
        "what came in and what went back are the server's own sums",
        "90.000" in money_tile and "240.000" in money_tile,
        money_tile[:120],
    )
    # SHOP-CAPTURE-001: margin is the month's, and the server sends no figure until the month's
    # Sổ thu chi has all five core categories. The tile says so and names what is missing.
    check(
        "margin for an incomplete month says Chưa đủ số liệu and names the missing categories",
        "Chưa đủ số liệu" in tile("MARGIN")
        and "nước, lương, mặt bằng" in tile("MARGIN")
        and "₫" not in tile("MARGIN"),
        tile("MARGIN")[:160],
    )
    check(
        "the capture tiles print the server's integers: 3 / 4 cycles, 85.000 ₫ per delivered order",
        "3 / 4 mẻ" in tile("CYCLES_CAPTURED")
        and "75%" in tile("CYCLES_CAPTURED")
        and "85.000" in tile("TRIP_COST_PER_ORDER")
        and "1 / 2 đơn giao" in tile("TRIP_COST_PER_ORDER"),
        f"{tile('CYCLES_CAPTURED')[:60]} | {tile('TRIP_COST_PER_ORDER')[:60]}",
    )
    month_text = (
        page.locator("[data-report-month]").first.inner_text()
        if page.locator("[data-report-month]").count()
        else ""
    )
    check(
        "the month's spending is listed by category with the server's total, never re-added",
        "Điện" in month_text
        and "1.250.000" in month_text
        and "2.050.000" in month_text
        and "Nước" not in month_text,
        month_text[:160],
    )
    REPORT_STATE["margin_complete"] = True
    page.locator("[aria-label='Khoảng ngày'] [data-value='30d']").click()
    page.wait_for_timeout(900)
    check(
        "a complete month whose spending exceeded its takings is said in words, never with a minus",
        "Chi nhiều hơn thu" in tile("MARGIN")
        and "2.200.000" in tile("MARGIN")
        and "-2" not in tile("MARGIN")
        and "\u2212" not in tile("MARGIN")
        and "lợi nhuận" not in tile("MARGIN").lower(),
        tile("MARGIN")[:160],
    )
    REPORT_STATE["margin_complete"] = False
    check(
        "the remedies tile names the kinds that happened, in the counter's words",
        "Giặt lại miễn phí: 1" in tile("REMEDIES_EXECUTED")
        and "80.000" in tile("REMEDIES_EXECUTED"),
        tile("REMEDIES_EXECUTED")[:120],
    )
    check(
        "the versioned query behind the figures is in the technical drawer",
        REPORT_VERSION in str(page.evaluate("document.body.textContent")),
    )
    page.locator("[data-kpi=ON_TIME_INTERNAL] .info-btn").first.click()
    page.wait_for_timeout(300)
    sheet_text = page.locator("dialog.sheet[open]").first.inner_text().replace("\n", " ")
    check(
        "the on-time ⓘ carries the board's rule verbatim and the data quality token",
        SLA_BOARD_POLICY_NOTICE in sheet_text and "RULE_ASSUMED" in sheet_text,
        sheet_text[:160],
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check(
        "one row per day, newest first",
        page.locator("[data-report-day]").count() == 2,
        str(page.locator("[data-report-day]").count()),
    )

    # A window the server would refuse is refused here first, and never sent.
    page.locator("[aria-label='Khoảng ngày'] [data-value='custom']").click()
    page.wait_for_timeout(200)
    before = len(state["report_reads"])
    page.fill("#report-from", "2020-01-01")
    page.locator("[data-report-apply]").click()
    page.wait_for_timeout(400)
    check(
        "a window longer than 92 days is refused in words, without a request",
        "Tối đa 92 ngày" in rendered_text() and len(state["report_reads"]) == before,
        str(len(state["report_reads"]) - before),
    )

    # An operator: the entry stays in Thêm, shut, with who may open it; the screen asks nothing.
    SESSION_OK["roles"] = ["OPERATOR"]
    state["report_reads"] = []
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/more", wait_until="networkidle")
    page.wait_for_timeout(900)
    denied = page.locator("[data-nav-denied='/reports']")
    check(
        "an operator still sees Báo cáo under Thêm, disabled, naming who may open it",
        denied.count() == 1 and "Chỉ" in denied.first.inner_text(),
    )
    page.goto(f"http://localhost:{PORT}/#/reports", wait_until="networkidle")
    page.wait_for_timeout(900)
    check(
        "and opening it anyway shows the rule and makes no report request",
        "Kế toán" in rendered_text() and not state["report_reads"],
        repr(state["report_reads"]),
    )
    SESSION_OK["roles"] = ["OWNER_ADMIN"]

    # ============================================================================================
    # 22. CUSTOMER-001 (DEC-034) -- Nhận đồ step 1 is one search field above the walk-in button; a
    #     regular is one tap; "Thêm khách mới" offers nothing to save until the owner publishes the
    #     notice, and afterwards sends two separate consents. Nothing typed is kept on the device.
    # ============================================================================================
    print()
    print("[22] Khách quen: one field, one tap, consent before a record")
    state["customer_searches"] = []
    state["customer_posts"] = []
    state["intake_posts"] = []
    state["notice_published"] = False
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/new", wait_until="networkidle")
    page.wait_for_timeout(1000)
    field = page.locator("#new-customer-search")
    order = page.evaluate(
        """() => {
            const a = document.querySelector('#new-customer-search');
            const b = document.querySelector('#new-walk-in');
            return a && b ? Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
                          : false;
        }"""
    )
    check(
        "step 1 is one field, 'SĐT hoặc tên khách', above the walk-in button",
        field.count() == 1 and field.get_attribute("aria-label") == "SĐT hoặc tên khách" and order,
    )
    check("nothing is searched before anything is typed", not state["customer_searches"])
    field.click()
    page.keyboard.type("3456", delay=20)
    page.wait_for_timeout(900)
    row = page.locator(f"#new-customer-search-list [data-customer='{CUSTOMER_ID}']")
    check(
        "four digits list the regular by name and number, read aloud-style",
        row.count() == 1
        and "chị Lan" in row.inner_text()
        and "0905 123 456" in row.inner_text()
        and "1 đơn mở" in row.inner_text(),
        row.inner_text() if row.count() else "absent",
    )
    check(
        "the search is a read (q in the request), never an address the console navigates to",
        any("q=3456" in url for url in state["customer_searches"])
        and "3456" not in page.url
        and page.evaluate("() => JSON.stringify(Object.keys(localStorage))")
        == '["staff_store_id"]',
        repr(state["customer_searches"][-1:]) + " " + page.url,
    )
    row.click()
    page.wait_for_timeout(900)
    posted = state["intake_posts"][-1] if state["intake_posts"] else ({}, None)
    check(
        "one tap opens the intake for the record -- customer_id only, under a key",
        posted[0] == {"customer_id": CUSTOMER_ID} and bool(posted[1]),
        repr(posted),
    )
    check(
        "and the flow is on step 2 with the ticket the server issued and the customer's name",
        page.locator("#new-ticket").count() == 1
        and "31" in page.locator("#new-ticket").inner_text()
        and page.locator("#new-ticket [data-field=customer]").inner_text() == "chị Lan",
        page.locator("#new-ticket").inner_text() if page.locator("#new-ticket").count() else "",
    )

    # Not yet published: the sheet says what the owner must do and offers nothing to fill.
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/new", wait_until="networkidle")
    page.wait_for_timeout(1000)
    page.locator("#new-customer-search").click()
    page.keyboard.type("0382 000 111", delay=10)
    page.wait_for_timeout(700)
    page.locator("#new-customer-add").click()
    page.wait_for_timeout(700)
    sheet_text = page.locator("#customer-new-sheet").inner_text()
    check(
        "unpublished: tier 1 says the owner must publish; no field to fill, 'Lưu' off",
        "Chủ tiệm cần công bố thông báo bảo mật trước khi lưu khách" in sheet_text
        and not page.locator("#customer-new-phone").is_visible()
        and page.locator("#customer-new-save").is_disabled()
        and not state["customer_posts"],
        sheet_text[:120].replace("\n", " | "),
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Published: the sentence to read, the number carried over, two ticks, promotions off.
    state["notice_published"] = True
    page.locator("#new-customer-add").click()
    page.wait_for_timeout(900)
    check(
        "published: the notice's sentence to read aloud, the typed number carried into the sheet",
        "Anh/chị đồng ý không ạ?"
        in page.locator("#customer-new-sheet .customer-consent__sentence").inner_text()
        and page.locator("#customer-new-phone").input_value() == "0382 000 111",
    )
    check(
        "'Lưu' stays off until the customer's consent is ticked; promotions start unticked",
        page.locator("#customer-new-save").is_disabled()
        and not page.locator("#customer-new-marketing").is_checked(),
    )
    page.locator("#customer-new-name").click()
    page.keyboard.type("anh Tuấn", delay=10)
    page.locator("#customer-new-consent").check()
    page.wait_for_timeout(200)
    state["intake_posts"] = []
    page.locator("#customer-new-save").click()
    page.wait_for_timeout(1200)
    sent = state["customer_posts"][-1] if state["customer_posts"] else ({}, None)
    check(
        "the record is sent with the attestation, promotions false, under its own key",
        sent[0].get("phone") == "0382 000 111"
        and sent[0].get("display_name") == "anh Tuấn"
        and sent[0].get("service_consent") is True
        and sent[0].get("marketing_consent") is False
        and bool(sent[1]),
        repr(sent),
    )
    check(
        "then the intake is opened for the new record, and step 2 names them",
        state["intake_posts"]
        and state["intake_posts"][-1][0] == {"customer_id": CUSTOMER_PROFILE["customer_id"]}
        and "anh Tuấn" in page.locator("#new-ticket").inner_text(),
        repr(state["intake_posts"][-1:]),
    )
    check(
        "nothing a customer said is left on the device",
        page.evaluate("() => JSON.stringify(Object.keys(localStorage))") == '["staff_store_id"]'
        and page.evaluate("() => sessionStorage.length") == 0
        and "0382" not in page.url,
    )
    state["notice_published"] = False

    print()
    print("=" * 74)
    print("21. SỔ THU CHI — the month as the server summed it, one line added, a wrong one voided")
    print("=" * 74)
    state["expense_writes"] = []
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/expenses?month=2026-09", wait_until="networkidle")
    page.wait_for_timeout(900)
    hero = page.locator(".money-hero__amount").first.inner_text()
    check(
        "the month's total is the server's, not the sum of the rows on screen",
        "2.050.000" in hero and "11.950.000" not in rendered_text(),
        hero,
    )
    missing_line = page.locator("[data-core-missing]").first
    check(
        "one line names what margin still waits for, from the server's core_missing",
        missing_line.get_attribute("data-core-missing") == "NUOC LUONG MAT_BANG"
        and "nước, lương, mặt bằng" in missing_line.inner_text(),
        missing_line.inner_text(),
    )
    check(
        "a voided line is still listed, marked, and a hostile note stays text",
        page.locator("[data-expense-id='e1e1e1e1-0000-4000-8000-000000000003']").count() == 1
        and "Đã huỷ" in rendered_text()
        and "<b>nước giặt</b>" in rendered_text(),
    )
    page.locator("button[data-expense-add]").click()
    page.wait_for_timeout(400)
    page.locator("#expense-save").click()
    page.wait_for_timeout(300)
    check(
        "Ghi vào sổ with no category says what is missing and sends nothing",
        not state["expense_writes"] and "Chọn mục chi" in open_dialog_text(),
    )
    page.locator("#expense-category [data-value=LUONG]").click()
    page.locator("#expense-amount").fill("7.000.000")
    page.locator("#expense-note").fill("lương tháng 9")
    page.locator("#expense-save").click()
    page.wait_for_timeout(900)
    writes = state["expense_writes"]
    sent = json.loads(writes[0]["body"] or "{}") if writes else {}
    check(
        "the expense is sent as typed: category, an integer amount, the day, the note, with a key",
        len(writes) == 1
        and sent.get("category") == "LUONG"
        and sent.get("amount_vnd") == 7_000_000
        and bool(sent.get("spent_on"))
        and sent.get("note") == "lương tháng 9"
        and bool(writes[0]["key"]),
        repr(writes),
    )
    state["expense_writes"] = []
    page.locator("[data-expense-id='e1e1e1e1-0000-4000-8000-000000000001']").click()
    page.wait_for_timeout(400)
    page.locator("#expense-void").click()
    page.wait_for_timeout(150)
    armed = not state["expense_writes"]
    page.locator("#expense-void").click()
    page.wait_for_timeout(900)
    writes = state["expense_writes"]
    check(
        "Huỷ dòng này takes two presses and sends If-Match with the line's version",
        armed
        and len(writes) == 1
        and writes[0]["path"].endswith("/expenses/e1e1e1e1-0000-4000-8000-000000000001/void")
        and writes[0]["if_match"] == '"1"',
        repr(writes),
    )
    page.keyboard.press("Escape")
    SESSION_OK["roles"] = ["AUDITOR"]
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/expenses", wait_until="networkidle")
    page.wait_for_timeout(900)
    add = page.locator("button[data-expense-add]")
    check(
        "an auditor reads the book, and Ghi khoản chi stays visible, shut, with who may write",
        "2.050.000" in rendered_text() and add.count() == 1 and add.first.is_disabled(),
    )
    SESSION_OK["roles"] = ["OPERATOR"]
    page.goto("about:blank")
    page.goto(f"http://localhost:{PORT}/#/more", wait_until="networkidle")
    page.wait_for_timeout(700)
    check(
        "an operator sees Sổ thu chi under Thêm, disabled, naming who may open it",
        page.locator("[data-nav-denied='/expenses']").count() == 1,
    )
    SESSION_OK["roles"] = ["OWNER_ADMIN"]

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
