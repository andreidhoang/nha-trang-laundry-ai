"""Typed structured events that cannot alter application decisions on failure."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .correlation import CorrelationContext, current_correlation
from .redaction import sanitize

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,99}$")


class EventSeverity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class StructuredEvent:
    component: str
    name: str
    outcome: str
    correlation: CorrelationContext
    severity: EventSeverity = EventSeverity.INFO
    fields: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 1

    def __post_init__(self) -> None:
        for label, value in (("component", self.component), ("name", self.name)):
            if not _EVENT_NAME.fullmatch(value):
                raise ValueError(f"invalid structured event {label}")
        if not _EVENT_NAME.fullmatch(self.outcome.casefold()):
            raise ValueError("invalid structured event outcome")
        if self.occurred_at.tzinfo is None:
            raise ValueError("structured event timestamp must be timezone-aware")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "severity": self.severity.value,
            "component": self.component,
            "event": self.name,
            "outcome": self.outcome,
            "correlation_id": self.correlation.header_value,
            "trace_id": self.correlation.trace_id,
            "fields": sanitize(self.fields),
        }

    def serialize(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SafeStructuredLogger:
    """Local structured output whose failures are contained and observable by return value."""

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self._sink = sink or logging.getLogger("nha_trang_laundry.structured").info

    def emit(self, event: StructuredEvent) -> bool:
        try:
            self._sink(event.serialize())
        except Exception:
            return False
        return True

    def record(
        self,
        *,
        component: str,
        name: str,
        outcome: str,
        fields: Mapping[str, Any] | None = None,
        severity: EventSeverity = EventSeverity.INFO,
        correlation: CorrelationContext | None = None,
    ) -> bool:
        try:
            context = correlation or current_correlation() or CorrelationContext.new()
            event = StructuredEvent(
                component=component,
                name=name,
                outcome=outcome,
                correlation=context,
                severity=severity,
                fields=fields or {},
            )
        except Exception:
            return False
        return self.emit(event)
