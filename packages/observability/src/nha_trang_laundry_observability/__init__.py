"""Typed, fail-safe observability primitives with mandatory redaction."""

from .contract_validation import (
    TelemetryContractError,
    load_telemetry_contracts,
    validate_telemetry_contracts,
)
from .correlation import (
    CORRELATION_HEADER,
    CorrelationContext,
    correlation_scope,
    current_correlation,
)
from .events import EventSeverity, SafeStructuredLogger, StructuredEvent
from .redaction import REDACTED, sanitize
from .telemetry import (
    METRIC_CONTRACTS,
    MetricContract,
    Telemetry,
    current_trace_id,
    safe_attributes,
)

__all__ = [
    "CORRELATION_HEADER",
    "METRIC_CONTRACTS",
    "REDACTED",
    "CorrelationContext",
    "EventSeverity",
    "MetricContract",
    "SafeStructuredLogger",
    "StructuredEvent",
    "Telemetry",
    "TelemetryContractError",
    "correlation_scope",
    "current_correlation",
    "current_trace_id",
    "load_telemetry_contracts",
    "safe_attributes",
    "sanitize",
    "validate_telemetry_contracts",
]
