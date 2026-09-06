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
from .logging_setup import (
    STRUCTURED_LOGGER_NAME,
    configure_structured_logging,
    structured_logging_is_live,
)
from .redaction import REDACTED, redact_text, sanitize
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
    "STRUCTURED_LOGGER_NAME",
    "CorrelationContext",
    "EventSeverity",
    "MetricContract",
    "SafeStructuredLogger",
    "StructuredEvent",
    "Telemetry",
    "TelemetryContractError",
    "configure_structured_logging",
    "correlation_scope",
    "current_correlation",
    "current_trace_id",
    "load_telemetry_contracts",
    "redact_text",
    "safe_attributes",
    "sanitize",
    "structured_logging_is_live",
    "validate_telemetry_contracts",
]
