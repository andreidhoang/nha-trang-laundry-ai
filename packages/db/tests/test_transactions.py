from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import pytest
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, use_span


class FakeCursor:
    def __init__(self, fail_on: str | None = None) -> None:
        self.executed: list[str] = []
        self.parameters: list[object | None] = []
        self.fail_on = fail_on

    def execute(self, query: str, params: object | None = None) -> None:
        self.executed.append(query)
        self.parameters.append(params)
        if self.fail_on is not None and self.fail_on in query:
            raise RuntimeError("injected database failure")

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        return False


class FakeConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.cursor_instance = FakeCursor(fail_on)
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def transaction(self) -> Any:
        try:
            yield
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


def material_change() -> MaterialChange:
    return MaterialChange(
        aggregate_type="ORDER",
        aggregate_id=UUID("00000000-0000-0000-0000-000000000001"),
        aggregate_version=1,
        event_type="ORDER_CREATED",
        event_payload={"source": "test"},
        audit_action="ORDER_CREATE",
        actor_type="OPERATOR",
        actor_id=None,
        correlation_id=UUID("00000000-0000-0000-0000-000000000002"),
        outbox_events=(OutboxEvent("ORDER_CREATED", {"source": "test"}, "order:1:created"),),
        occurred_at=datetime(2026, 7, 27, tzinfo=UTC),
    )


def test_commits_mutation_domain_audit_and_outbox_together() -> None:
    connection = FakeConnection()

    commit_material_change(
        connection,
        material_change(),
        lambda cursor: cursor.execute("INSERT INTO orders (id) VALUES ('test')"),
    )

    assert connection.committed is True
    assert connection.rolled_back is False
    statements = "\n".join(connection.cursor_instance.executed)
    assert "INSERT INTO orders" in statements
    assert "INSERT INTO domain_events" in statements
    assert "INSERT INTO audit_events" in statements
    assert "INSERT INTO outbox_events" in statements


def test_audit_failure_rolls_back_material_mutation() -> None:
    connection = FakeConnection(fail_on="INSERT INTO audit_events")

    with pytest.raises(RuntimeError, match="injected database failure"):
        commit_material_change(
            connection,
            material_change(),
            lambda cursor: cursor.execute("INSERT INTO orders (id) VALUES ('test')"),
        )

    assert connection.committed is False
    assert connection.rolled_back is True


def test_material_change_rejects_missing_required_outbox() -> None:
    change = material_change()
    no_outbox_change = MaterialChange(
        **{**change.__dict__, "outbox_events": ()},
    )

    with pytest.raises(ValueError, match="requires at least one outbox event"):
        commit_material_change(FakeConnection(), no_outbox_change, lambda cursor: None)


def test_material_change_persists_current_w3c_context_beside_outbox_payload() -> None:
    connection = FakeConnection()
    span = NonRecordingSpan(
        SpanContext(
            trace_id=0x901,
            span_id=0x902,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )

    with use_span(span, end_on_exit=False):
        commit_material_change(connection, material_change(), lambda cursor: None)

    outbox_index = next(
        index
        for index, statement in enumerate(connection.cursor_instance.executed)
        if "INSERT INTO outbox_events" in statement
    )
    parameters = connection.cursor_instance.parameters[outbox_index]
    assert isinstance(parameters, tuple)
    assert parameters[-2] == "00-00000000000000000000000000000901-0000000000000902-01"
    assert parameters[-1] is None
    assert trace.get_current_span().get_span_context().is_valid is False
