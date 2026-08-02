"""Typed, fail-safe observability primitives with mandatory redaction."""

from .correlation import (
    CORRELATION_HEADER,
    CorrelationContext,
    correlation_scope,
    current_correlation,
)
from .events import EventSeverity, SafeStructuredLogger, StructuredEvent
from .redaction import REDACTED, sanitize

__all__ = [
    "CORRELATION_HEADER",
    "REDACTED",
    "CorrelationContext",
    "EventSeverity",
    "SafeStructuredLogger",
    "StructuredEvent",
    "correlation_scope",
    "current_correlation",
    "sanitize",
]
