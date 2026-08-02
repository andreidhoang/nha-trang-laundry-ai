"""Correlation identifiers shared by HTTP and recoverable job boundaries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID, uuid4

CORRELATION_HEADER = "X-Correlation-ID"
_CURRENT: ContextVar[CorrelationContext | None] = ContextVar(
    "nha_trang_laundry_correlation", default=None
)


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Non-authoritative diagnostic identity created or validated by the server."""

    correlation_id: UUID
    trace_id: str

    def __post_init__(self) -> None:
        expected = f"tr_{self.correlation_id.hex}"
        if self.trace_id != expected:
            raise ValueError("trace_id must be derived from correlation_id")

    @classmethod
    def new(cls) -> CorrelationContext:
        return cls.from_uuid(uuid4())

    @classmethod
    def from_uuid(cls, value: UUID) -> CorrelationContext:
        return cls(value, f"tr_{value.hex}")

    @classmethod
    def from_http_header(cls, value: str | None) -> CorrelationContext:
        """Use a canonical UUID header or replace malformed/unbounded input."""
        if value is None or len(value) > 36:
            return cls.new()
        try:
            parsed = UUID(value)
        except (ValueError, AttributeError):
            return cls.new()
        if str(parsed) != value.lower():
            return cls.new()
        return cls.from_uuid(parsed)

    @property
    def header_value(self) -> str:
        return str(self.correlation_id)


def current_correlation() -> CorrelationContext | None:
    return _CURRENT.get()


@contextmanager
def correlation_scope(context: CorrelationContext) -> Iterator[CorrelationContext]:
    token: Token[CorrelationContext | None] = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
