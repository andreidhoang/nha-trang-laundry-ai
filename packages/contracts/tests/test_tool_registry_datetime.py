"""AGENT-SHADOW-DEFECTS-001 F8: `format: date-time` in the tool contract is enforced.

`jsonschema.FormatChecker` checks `date-time` only when the optional `rfc3339-validator` package is
installed. It is not, so every `format: date-time` in `agent-tools-v1.openapi.yaml` was silently
accepted as any string at all: a model could send `"tomorrow afternoon please"` as a timestamp and
the contract let it through to the backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from nha_trang_laundry_contracts import (
    STRICT_FORMAT_CHECKER,
    AgentToolOperation,
    ToolArgumentsInvalid,
    load_agent_tool_registry,
)

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = load_agent_tool_registry(ROOT / "specs/contracts/agent-tools-v1.openapi.yaml")
CAPACITY = REGISTRY.get(AgentToolOperation.CAPACITY_CHECK)


@pytest.mark.parametrize(
    "value",
    [
        "tomorrow afternoon please",
        "chiều mai",
        "2026-09-25",  # a date is not a date-time
        "2026-09-25T15:00:00",  # no offset: which clock?
        "2026-09-25 15:00:00+07:00",  # RFC 3339 requires the T separator
        "2026-13-01T10:00:00+07:00",  # month 13
        "2026-02-30T10:00:00+07:00",  # 30 February
        "2026-09-25T24:00:00Z",
        "2026-09-25T15:00:00+7:00",
        "",
    ],
)
def test_a_value_that_is_not_an_rfc3339_date_time_is_refused(value: str) -> None:
    with pytest.raises(ToolArgumentsInvalid, match="date-time"):
        CAPACITY.validate_model_arguments({"requested_ready_at": value})


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-25T15:00:00+07:00",
        "2026-09-25T08:00:00Z",
        "2026-09-25T08:00:00.123456Z",
        "2026-09-25t08:00:00z",
    ],
)
def test_an_rfc3339_date_time_is_accepted(value: str) -> None:
    assert CAPACITY.validate_model_arguments({"requested_ready_at": value}) == {
        "requested_ready_at": value
    }


def test_null_remains_allowed_where_the_contract_allows_it() -> None:
    assert CAPACITY.validate_model_arguments({"requested_ready_at": None}) == {
        "requested_ready_at": None
    }


def test_the_strict_checker_really_checks_date_time() -> None:
    """The failure mode was silent absence, so assert the presence directly."""
    assert "date-time" in STRICT_FORMAT_CHECKER.checkers
    assert STRICT_FORMAT_CHECKER.conforms("2026-09-25T15:00:00+07:00", "date-time")
    assert not STRICT_FORMAT_CHECKER.conforms("tomorrow afternoon please", "date-time")


def test_a_response_carrying_a_malformed_date_time_is_refused() -> None:
    response: dict[str, Any] = {
        "ok": True,
        "trace_id": "tr_12345678",
        "decision": {
            "outcome": "REQUIRE_HUMAN",
            "reason_codes": [],
            "obligations": ["SLOT_CONFIRMATION"],
            "policy_version": "p",
            "snapshot_hash": "sha256:" + "a" * 64,
        },
        "data": {
            "advisory": "UNKNOWN",
            "advisory_ready_window_start": "sometime tomorrow",
            "advisory_ready_window_end": None,
            "slot_confirmed": False,
            "required_approval": "SLOT_CONFIRMATION",
        },
    }

    with pytest.raises(ToolArgumentsInvalid, match="date-time"):
        CAPACITY.validate_success_response(response)
    # The same response with a real timestamp is accepted, so the refusal above is the format's.
    response["data"]["advisory_ready_window_start"] = "2026-09-26T14:00:00+07:00"
    assert CAPACITY.validate_success_response(response)["data"]["advisory"] == "UNKNOWN"
