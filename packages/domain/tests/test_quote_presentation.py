"""AGENT-SHADOW-DEFECTS-001 F4: the content an agent-raised quote approval presents, rendered.

A rendering is a deterministic function of a stored revision and the action. These tests pin the
properties the approval relies on: it moves when the revision, the figures or the action move, and
it refuses a document that is not a quote revision rather than hashing whatever it was given.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.quote_presentation import (
    QUOTE_PRESENTATION_RENDERING,
    QuotePresentationError,
    render_quote_presentation,
)


def _revision(**overrides: Any) -> dict[str, Any]:
    revision: dict[str, Any] = {
        "schema_version": 1,
        "quote_id": "7f0c2b39-3a55-4e2f-9d2c-0b4a1f7e9a01",
        "revision": 1,
        "finality": "ESTIMATE",
        "status": "REVIEW_REQUIRED",
        "priced_at": "2026-09-25T03:00:00.000000Z",
        "valid_until": None,
        "currency": "VND",
        "lines": [
            {
                "line_id": "L1",
                "service_code": "STANDARD_WASH_DRY",
                "quantity": "6",
                "unit": "KG",
                "amounts": {"kind": "EXACT", "net_amount_vnd": 90000},
            }
        ],
        "adjustments": [],
        "totals": {"display_total_min_vnd": 90000, "display_total_max_vnd": 90000},
        "required_approvals": ["PRESENT_QUOTE"],
        "calculation_traces": [{"component": "pricing"}],
    }
    revision.update(overrides)
    return revision


def _render(revision: dict[str, Any], action: str = "PRESENT_QUOTE") -> str:
    document = canonical_document(revision, exclude_volatile=False)
    return render_quote_presentation(document, action=action).snapshot_hash


def test_the_rendering_is_deterministic_and_names_its_version_and_revision() -> None:
    document = canonical_document(_revision(), exclude_volatile=False)
    rendered = render_quote_presentation(document, action="PRESENT_QUOTE")

    assert rendered == render_quote_presentation(document, action="PRESENT_QUOTE")
    content = json.loads(rendered.canonical_json)
    assert content["rendering"] == QUOTE_PRESENTATION_RENDERING
    assert content["revision_snapshot_hash"] == document.snapshot_hash
    assert content["presented"]["totals"] == _revision()["totals"]
    # Engine internals are not what a customer is shown, so they are not what is approved.
    assert "calculation_traces" not in content["presented"]


@pytest.mark.parametrize(
    "changed",
    [
        {"totals": {"display_total_min_vnd": 1, "display_total_max_vnd": 1}},
        {"revision": 2},
        {"valid_until": "2026-09-26T03:00:00.000000Z"},
        {"lines": []},
    ],
)
def test_the_rendering_moves_when_what_is_presented_moves(changed: dict[str, Any]) -> None:
    assert _render(_revision()) != _render(_revision(**changed))


def test_the_rendering_moves_with_the_action() -> None:
    assert _render(_revision(), "PRESENT_QUOTE") != _render(_revision(), "APPLY_PROMOTION")


def test_a_document_that_is_not_a_quote_revision_is_refused() -> None:
    not_a_quote = canonical_document({"order_id": "x"}, exclude_volatile=False)

    with pytest.raises(QuotePresentationError):
        render_quote_presentation(not_a_quote, action="PRESENT_QUOTE")
