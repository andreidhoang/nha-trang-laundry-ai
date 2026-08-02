from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest
from nha_trang_laundry_observability import (
    METRIC_CONTRACTS,
    REDACTED,
    CorrelationContext,
    EventSeverity,
    SafeStructuredLogger,
    StructuredEvent,
    Telemetry,
    TelemetryContractError,
    correlation_scope,
    current_correlation,
    current_trace_id,
    load_telemetry_contracts,
    safe_attributes,
    sanitize,
    validate_telemetry_contracts,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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


def test_metric_registry_records_vendor_neutral_bounded_measurements() -> None:
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(
        metric_readers=[reader], resource=Resource.create({"service.name": "synthetic-test"})
    )
    tracer_provider = TracerProvider(resource=Resource.create({"service.name": "synthetic-test"}))
    telemetry = Telemetry(
        meter_provider=meter_provider,
        tracer_provider=tracer_provider,
        instrumentation_name="test.telemetry",
    )

    telemetry.record(
        "api_requests_total",
        1,
        {"component": "api", "operation": "internal_api", "outcome": "completed"},
    )
    data = reader.get_metrics_data()

    assert len(METRIC_CONTRACTS) == len({item.metric_id for item in METRIC_CONTRACTS})
    assert data is not None
    metric_names = {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert "api_requests_total" in metric_names


@pytest.mark.parametrize(
    "attributes",
    [
        {"contact_id": str(CORRELATION_ID)},
        {"prompt": "ignore rules"},
        {"operation": "x" * 65},
        {"outcome": "customer-specific-outcome"},
    ],
)
def test_metric_and_span_attributes_reject_pii_and_unbounded_dimensions(
    attributes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="telemetry attribute"):
        safe_attributes(attributes)


def test_traceparent_is_extracted_and_injected_without_business_identifiers() -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry(
        meter_provider=MeterProvider(),
        tracer_provider=tracer_provider,
        instrumentation_name="test.trace",
    )
    incoming = {"traceparent": "00-00000000000000000000000000000901-0000000000000901-01"}

    with telemetry.start_span(
        "worker_process",
        carrier=incoming,
        attributes={"component": "worker", "queue": "internal", "outcome": "completed"},
    ):
        outgoing: dict[str, str] = {}
        telemetry.inject_current(outgoing)
        assert current_trace_id() == "00000000000000000000000000000901"

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].parent is not None
    assert spans[0].parent.span_id == 0x901
    assert outgoing["traceparent"].startswith("00-00000000000000000000000000000901-")


def test_bundled_metric_slo_and_alert_contracts_match_runtime_registry() -> None:
    document = load_telemetry_contracts()

    validate_telemetry_contracts(document)

    assert {item["metric_id"] for item in document["metrics"]} == {
        contract.metric_id for contract in METRIC_CONTRACTS
    }
    assert any(rule["id"] == "any_dlq_business_hours" for rule in document["alerts"])


@pytest.mark.parametrize("mutation", ["pii_dimension", "unknown_metric", "invalid_threshold"])
def test_contract_validation_fails_closed_for_unsafe_or_incomplete_rules(mutation: str) -> None:
    document = deepcopy(load_telemetry_contracts())
    if mutation == "pii_dimension":
        document["metrics"][0]["dimensions"].append("contact_id")
    elif mutation == "unknown_metric":
        document["alerts"][0]["metric_id"] = "undeclared_metric"
    else:
        document["alerts"][0]["threshold"] = "zero"

    with pytest.raises(TelemetryContractError):
        validate_telemetry_contracts(document)
