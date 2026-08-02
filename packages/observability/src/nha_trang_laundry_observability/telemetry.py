"""Vendor-neutral OpenTelemetry instruments with bounded, PII-safe attributes."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.metrics import Meter, MeterProvider
from opentelemetry.trace import Span, Tracer, TracerProvider


class InstrumentKind(StrEnum):
    COUNTER = "COUNTER"
    HISTOGRAM = "HISTOGRAM"


@dataclass(frozen=True, slots=True)
class MetricContract:
    metric_id: str
    version: int
    kind: InstrumentKind
    unit: str
    business_question: str


METRIC_CONTRACTS = (
    MetricContract("api_requests_total", 1, InstrumentKind.COUNTER, "{request}", "API outcomes"),
    MetricContract("api_latency_ms", 1, InstrumentKind.HISTOGRAM, "ms", "API latency"),
    MetricContract("database_latency_ms", 1, InstrumentKind.HISTOGRAM, "ms", "DB latency"),
    MetricContract("queue_depth", 1, InstrumentKind.HISTOGRAM, "{item}", "Queue depth"),
    MetricContract("queue_oldest_age_s", 1, InstrumentKind.HISTOGRAM, "s", "Queue age"),
    MetricContract("queue_retries_total", 1, InstrumentKind.COUNTER, "{retry}", "Retries"),
    MetricContract("dlq_items_total", 1, InstrumentKind.COUNTER, "{item}", "DLQ arrivals"),
    MetricContract("agent_budget_exhausted_total", 1, InstrumentKind.COUNTER, "{run}", "Budgets"),
    MetricContract("agent_processing_latency_ms", 1, InstrumentKind.HISTOGRAM, "ms", "Agent time"),
    MetricContract("agent_cost_usd", 1, InstrumentKind.HISTOGRAM, "USD", "Agent cost"),
    MetricContract("approval_backlog", 1, InstrumentKind.HISTOGRAM, "{item}", "Approvals"),
    MetricContract("suppression_miss_count", 1, InstrumentKind.COUNTER, "{send}", "Suppression"),
    MetricContract("kill_switch_age_s", 1, InstrumentKind.HISTOGRAM, "s", "Switch freshness"),
    MetricContract("denied_capability_total", 1, InstrumentKind.COUNTER, "{attempt}", "Denials"),
    MetricContract("backup_age_s", 1, InstrumentKind.HISTOGRAM, "s", "Backup freshness"),
    MetricContract("restore_test_age_s", 1, InstrumentKind.HISTOGRAM, "s", "Restore freshness"),
    MetricContract(
        "confirmed_money_exact_rate", 1, InstrumentKind.HISTOGRAM, "1", "Money exactness"
    ),
    MetricContract("wrong_money_count", 1, InstrumentKind.COUNTER, "{error}", "Wrong money"),
    MetricContract(
        "high_risk_handoff_recall", 1, InstrumentKind.HISTOGRAM, "1", "Risk handoff recall"
    ),
    MetricContract(
        "false_auto_eligible_rate", 1, InstrumentKind.HISTOGRAM, "1", "False eligibility"
    ),
    MetricContract(
        "eligible_abstention_rate", 1, InstrumentKind.HISTOGRAM, "1", "Eligible abstention"
    ),
    MetricContract(
        "required_abstention_rate", 1, InstrumentKind.HISTOGRAM, "1", "Required abstention"
    ),
    MetricContract("duplicate_send_count", 1, InstrumentKind.COUNTER, "{send}", "Duplicate sends"),
    MetricContract(
        "material_mutation_audit_coverage",
        1,
        InstrumentKind.HISTOGRAM,
        "1",
        "Audit coverage",
    ),
    MetricContract(
        "unauthorized_agent_action_count",
        1,
        InstrumentKind.COUNTER,
        "{attempt}",
        "Unauthorized actions",
    ),
    MetricContract(
        "cross_customer_disclosure_count",
        1,
        InstrumentKind.COUNTER,
        "{incident}",
        "Cross-customer disclosures",
    ),
)

SAFE_ATTRIBUTE_VALUES: Mapping[str, frozenset[str] | None] = {
    "component": frozenset({"api", "database", "worker", "agent_runner", "backup"}),
    "operation": frozenset(
        {
            "healthz",
            "staff_shell",
            "staff_auth",
            "internal_api",
            "not_found",
            "worker_cycle",
            "outbox_process",
            "quote_compute",
            "backup",
            "restore",
        }
    ),
    "outcome": frozenset({"accepted", "completed", "denied", "failed", "held", "timeout"}),
    "status_class": frozenset({"2xx", "3xx", "4xx", "5xx"}),
    "queue": frozenset({"internal", "agent"}),
    "capability": frozenset(
        {"public_faq", "intake", "quote_explanation", "order_status", "incident_intake"}
    ),
    "stage": frozenset({"SHADOW", "ASSISTED", "BOUNDED_AUTO"}),
    "retryable": frozenset({"true", "false"}),
}
_BOUNDED_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CONTRACT_BY_ID = {contract.metric_id: contract for contract in METRIC_CONTRACTS}


def safe_attributes(values: Mapping[str, object]) -> dict[str, str | bool | int]:
    """Reject unknown/high-cardinality attributes instead of redacting them into labels."""

    result: dict[str, str | bool | int] = {}
    for key, value in values.items():
        allowed = SAFE_ATTRIBUTE_VALUES.get(key)
        if key not in SAFE_ATTRIBUTE_VALUES or isinstance(value, float):
            raise ValueError(f"telemetry attribute is not allowlisted: {key}")
        normalized = str(value).casefold() if isinstance(value, bool) else str(value)
        if _BOUNDED_VALUE.fullmatch(normalized) is None:
            raise ValueError(f"telemetry attribute value is unbounded: {key}")
        if allowed is not None and normalized not in allowed:
            raise ValueError(f"telemetry attribute value is not canonical: {key}")
        result[key] = value if isinstance(value, (bool, int)) else normalized
    return result


class Telemetry:
    """Small wrapper that keeps instrumentation names and attributes contract-bound."""

    def __init__(
        self,
        *,
        meter_provider: MeterProvider,
        tracer_provider: TracerProvider,
        instrumentation_name: str,
    ) -> None:
        self._meter: Meter = meter_provider.get_meter(instrumentation_name)
        self._tracer: Tracer = tracer_provider.get_tracer(instrumentation_name)
        self._instruments: dict[str, Any] = {}
        for contract in METRIC_CONTRACTS:
            factory = (
                self._meter.create_counter
                if contract.kind is InstrumentKind.COUNTER
                else self._meter.create_histogram
            )
            self._instruments[contract.metric_id] = factory(
                contract.metric_id,
                unit=contract.unit,
                description=f"v{contract.version}: {contract.business_question}",
            )

    def record(self, metric_id: str, value: int | float, attributes: Mapping[str, object]) -> None:
        contract = _CONTRACT_BY_ID.get(metric_id)
        if contract is None or value < 0:
            raise ValueError("metric or value violates telemetry contract")
        instrument = self._instruments[metric_id]
        bounded = safe_attributes(attributes)
        if contract.kind is InstrumentKind.COUNTER:
            instrument.add(value, bounded)
        else:
            instrument.record(value, bounded)

    @contextmanager
    def start_span(
        self,
        operation: str,
        *,
        attributes: Mapping[str, object],
        carrier: Mapping[str, str] | None = None,
    ) -> Iterator[Span]:
        if _BOUNDED_VALUE.fullmatch(operation) is None:
            raise ValueError("span operation is unbounded")
        context = propagate.extract(carrier or {})
        with self._tracer.start_as_current_span(
            operation,
            context=context,
            attributes=safe_attributes(attributes),
        ) as span:
            yield span

    @staticmethod
    def inject_current(carrier: MutableMapping[str, str]) -> None:
        propagate.inject(carrier)


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None
