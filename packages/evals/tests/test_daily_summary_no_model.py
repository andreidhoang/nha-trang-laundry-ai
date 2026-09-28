"""`DAILY-SUMMARY-001` (`DEC-039`): the evening summary's two claims, bound to the code.

The card on Hôm nay tells the owner two things that are only true while the code keeps them true:

* **"không phải AI"** -- the summary is a fixed template over the day's figures. `DEC-006` is open
  and all 13 AI capabilities are `NOT_AUTHORIZED`, so no module on the summary's path may import a
  model runtime, a provider client or a network client. The day somebody wires one in, this fails
  and the sentence on the card has to be re-read.
* **no personal data** -- the summary's statements select no customer column, the template's inputs
  carry no free text (`packages/domain/tests/test_daily_summary_template.py`), the text is scanned
  after seeding named customers with phones (`packages/db/tests/test_daily_summary_repository.py`),
  and the console keeps nothing on the device.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUMMARY_PATH = (
    ROOT / "packages/domain/src/nha_trang_laundry_domain/daily_summary.py",
    ROOT / "packages/db/src/nha_trang_laundry_db/daily_summary.py",
    ROOT / "apps/api/src/nha_trang_laundry_api/daily_summary.py",
)
CARD = ROOT / "apps/web/src/ui/dailySummary.js"

#: Anything that could put a model or a network between the figures and the text.
FORBIDDEN_IMPORTS = (
    "nha_trang_laundry_worker",
    "nha_trang_laundry_api.assistant",
    "nha_trang_laundry_db.assistant",
    "nha_trang_laundry_db.agent_runs",
    "nha_trang_laundry_db.message_drafts",
    "openai",
    "anthropic",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "aiohttp",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_module_on_the_summary_path_imports_a_model_or_a_network_client() -> None:
    for path in SUMMARY_PATH:
        for module in _imports(path):
            for forbidden in FORBIDDEN_IMPORTS:
                assert not (module == forbidden or module.startswith(f"{forbidden}.")), (
                    path.name,
                    module,
                )


def test_the_card_says_it_is_a_fixed_template_not_ai_and_that_nothing_is_sent_by_itself() -> None:
    card = CARD.read_text(encoding="utf-8")
    assert "Một mẫu câu cố định, không phải AI" in card
    assert "Hệ thống không tự gửi tóm tắt đi đâu" in card
    assert "DEC-006" in card
    # The card composes no sentence of its own from figures: it prints the server's lines and
    # copies the server's `text`.
    assert "clipboard.writeText(text)" in card
    assert 'navigator.share({ title: "Tóm tắt cuối ngày", text })' in card
    assert "_vnd" not in card and "money(" not in card


def test_the_summarys_statements_select_no_personal_column() -> None:
    from nha_trang_laundry_db import (
        accounts,
        daily_summary,
        invoice_requests,
        late_deliveries,
        unclaimed,
    )

    statements = [
        value
        for name, value in vars(daily_summary).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]
    assert statements, "the summary module has no statement to check"
    # Round 7 wave 2 integration: the two wired hooks' own statements, which the summary runs
    # through `UnclaimedRepository.count_waiting` and `AccountRepository.accounts_due` -- counts and
    # sums over the waiting list and the account ledgers, never a customer's column.
    statements += [unclaimed._WAITING_COUNT_SQL, accounts._ACCOUNTS_DUE_SQL]
    # Round 8 (`SUMMARY-ATTENTION-001`): the late-delivery and invoice-request counts' own
    # statements. (The reminder count reads through the due list's scan, which asks only whether a
    # phone is on record -- a presence test, never the number -- and is held by the reminder tests.)
    statements += [late_deliveries.LATE_COUNT_SQL, invoice_requests.INVOICE_WAITING_OVER_SQL]
    for statement in statements:
        lowered = statement.lower()
        for column in (
            "customers",
            "phone",
            "display_name",
            "delivery_address",
            "evidence_summary",
            "customer_incident_evidence",
            "counter_tickets",
            "note",
            "bound_contact_id",
        ):
            assert not re.search(rf"\b{column}\b", lowered), (column, statement)


def test_the_card_keeps_nothing_on_the_device() -> None:
    card = CARD.read_text(encoding="utf-8")
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
        assert sink not in card, sink
