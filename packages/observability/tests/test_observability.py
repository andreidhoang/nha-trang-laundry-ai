from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from nha_trang_laundry_observability import (
    REDACTED,
    CorrelationContext,
    EventSeverity,
    SafeStructuredLogger,
    StructuredEvent,
    correlation_scope,
    current_correlation,
    sanitize,
)

CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000901")


def test_correlation_is_canonical_scoped_and_trace_bound() -> None:
    context = CorrelationContext.from_http_header(str(CORRELATION_ID))

    assert context.correlation_id == CORRELATION_ID
    assert context.trace_id == "tr_00000000000000000000000000000901"
    assert current_correlation() is None
    with correlation_scope(context):
        assert current_correlation() is context
    assert current_correlation() is None


@pytest.mark.parametrize(
    "untrusted",
    ["not-a-uuid", "A" * 100, "{00000000-0000-0000-0000-000000000901}"],
)
def test_invalid_http_correlation_is_replaced(untrusted: str) -> None:
    assert CorrelationContext.from_http_header(untrusted).correlation_id != CORRELATION_ID


def test_complete_serialized_event_redacts_nested_secret_and_pii_values() -> None:
    bearer = "Bearer synthetic-token-material-000000"
    api_key = "sk-test-only-key-material-000000"
    phone = "+840000000000"
    cookie = "synthetic_session=test-only-cookie"
    address = "999 TEST ONLY STREET"
    event = StructuredEvent(
        component="agent-worker",
        name="agent.run.failed",
        outcome="failed",
        severity=EventSeverity.ERROR,
        correlation=CorrelationContext.from_uuid(CORRELATION_ID),
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        fields={
            "authorization": bearer,
            "nested": [
                {"api_key": api_key, "customerPhone": phone},
                {"address": address, "cookie": cookie},
            ],
            "exception": RuntimeError(f"failure {bearer} {phone}"),
            "reasoning": "private reasoning test fixture",
            "tool_payload": {"safe": "must not survive under a forbidden container"},
        },
    )

    serialized = event.serialize()

    for forbidden in (
        bearer,
        api_key,
        phone,
        cookie,
        address,
        "private reasoning test fixture",
        "must not survive under a forbidden container",
    ):
        assert forbidden not in serialized
    parsed = json.loads(serialized)
    assert parsed["correlation_id"] == str(CORRELATION_ID)
    assert parsed["trace_id"] == "tr_00000000000000000000000000000901"
    assert parsed["fields"]["authorization"] == REDACTED
    assert parsed["fields"]["exception"] == {
        "exception_type": "RuntimeError",
        "message": REDACTED,
    }


def test_attacker_field_names_and_oversized_values_are_bounded_without_repr() -> None:
    secret_in_key = "Bearer synthetic-field-secret-000000"
    oversized = "safe-prefix-" + "x" * 1000

    sanitized = sanitize(
        {
            secret_in_key: "value",
            "9 invalid field": "value",
            "oversized": oversized,
            "objects": [object()],
        }
    )
    serialized = json.dumps(sanitized, sort_keys=True)

    assert secret_in_key not in serialized
    assert "9 invalid field" not in serialized
    assert oversized not in serialized
    assert "[TRUNCATED]" in serialized
    assert "object_type" in serialized


def test_logging_failure_is_contained() -> None:
    def broken_sink(payload: str) -> None:
        del payload
        raise OSError("synthetic local sink failure")

    logger = SafeStructuredLogger(broken_sink)

    assert not logger.record(
        component="api",
        name="domain.decision.completed",
        outcome="allowed",
        correlation=CorrelationContext.from_uuid(CORRELATION_ID),
        fields={"decision": "unchanged"},
    )


def test_event_rejects_unbounded_or_untyped_core_fields() -> None:
    with pytest.raises(ValueError, match="component"):
        StructuredEvent(
            component="INVALID COMPONENT",
            name="request.completed",
            outcome="completed",
            correlation=CorrelationContext.from_uuid(CORRELATION_ID),
        )
