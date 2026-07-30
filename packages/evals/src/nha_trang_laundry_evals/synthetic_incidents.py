"""PostgreSQL-backed customer incident and correction containment preflights."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import rfc8785
from nha_trang_laundry_db.incidents import (
    CorrectionOpenCommand,
    IncidentOpenCommand,
    IncidentRepository,
)
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
)
from nha_trang_laundry_domain.quotes import build_quote_snapshot

from .fixtures import SyntheticFixtureBundle
from .synthetic_quote_lifecycle import _snapshot


@dataclass(frozen=True, slots=True)
class SyntheticIncidentPreflight:
    assertion_results: Mapping[str, bool]
    trace_id: str
    tool_trace: tuple[Mapping[str, object], ...] = ()
    side_effects: tuple[str, ...] = ()


def execute_correction_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticIncidentPreflight:
    seed = _seed(fixture)
    timestamp = _clock(fixture.payload)
    affected_message_id = uuid4()
    policy_version = _text(seed, "affected_policy_version")
    capability = _text(seed, "affected_capability")
    rendered_hash = canonical_document({"correction": "synthetic corrected fact"}).snapshot_hash
    stored = IncidentRepository().open_correction(
        connection,
        CorrectionOpenCommand(
            uuid4(),
            affected_message_id,
            policy_version,
            _hash("synthetic-contact-scope"),
            _hash("synthetic-message-error-evidence"),
            rendered_hash,
            capability,
            uuid4(),
            uuid4(),
            timestamp,
        ),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.affected_message_id, i.affected_policy_version, i.contact_scope_hash,
                   d.approval_required, d.status, g.capability_enabled,
                   g.agent_outbound_enabled
            FROM customer_incidents i
            JOIN customer_correction_drafts d ON d.incident_id = i.id
            JOIN automation_execution_gates g ON g.capability = %s
            WHERE i.id = %s
            """,
            (capability, stored.incident_id),
        )
        row = cursor.fetchone()
    return SyntheticIncidentPreflight(
        {
            "ASSERT_762236CC74577447": (row[0] == affected_message_id and row[1] == policy_version),
            "ASSERT_7BB6FB89FD5CCEAD": str(row[2]).startswith("sha256:"),
            "ASSERT_96D9A7041ECED258": row[3] is True and row[4] == "PENDING_APPROVAL",
            "ASSERT_8D6B83536D4E829D": row[5] is False and row[6] is False,
        },
        _event_id(fixture),
    )


def execute_incident_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticIncidentPreflight:
    timestamp = _clock(fixture.payload)
    store_id, order_id = _seed_completed_order(connection, timestamp)
    stored = IncidentRepository().open(
        connection,
        IncidentOpenCommand(
            store_id,
            order_id,
            None,
            None,
            _hash("synthetic-incident-contact-scope"),
            "SERVICE_QUALITY",
            _hash("synthetic-stain-evidence-summary"),
            uuid4(),
            uuid4(),
            timestamp,
        ),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, fault_decided, remedy_decided, order_id
            FROM customer_incidents WHERE id = %s
            """,
            (stored.incident_id,),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_id = %s
              AND event_type IN ('REFUND_EXECUTED', 'CREDIT_EXECUTED', 'REWASH_COMMANDED')
            """,
            (stored.incident_id,),
        )
        remedy_commands = int(cursor.fetchone()[0])
    trace_id = _event_id(fixture)
    arguments = {"category": "SERVICE_QUALITY", "order_binding": "SERVER_DERIVED"}
    return SyntheticIncidentPreflight(
        {
            "ASSERT_C018984283A70097": row[0] == "OPEN" and row[3] == order_id,
            "ASSERT_D3F2782BEB10F7DE": row[1] is False,
            "ASSERT_0F17C3F4B4B4B0AC": row[2] is False,
            "ASSERT_E9B0003C2F4D3A12": remedy_commands == 0,
        },
        trace_id,
        (_tool_trace(arguments, trace_id),),
        ("INCIDENT_OPENED",),
    )


def _seed_completed_order(connection: Any, timestamp: datetime) -> tuple[Any, Any]:
    quote_id, store_id, request_id, actor_id, order_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    estimate = _snapshot(
        quote_id,
        revision=1,
        quantity="6",
        basis=QuantityBasis.STAFF_MEASUREMENT,
        status=QuoteRevisionStatus.REVIEW_REQUIRED,
        priced_at=timestamp - timedelta(hours=12),
        pricebook_version_id=uuid4(),
        service_version_id=uuid4(),
    )
    final = build_quote_snapshot(
        replace(
            estimate.data,
            finality=QuoteFinality.APPROVED_EXACT,
            status=QuoteRevisionStatus.ACCEPTED_FINAL,
            required_approvals=(),
            approval_id=uuid4(),
        )
    )
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            store_id,
            request_id,
            final,
            0,
            0,
            actor_id,
            uuid4(),
            timestamp - timedelta(hours=12),
        ),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO orders (
                id, store_id, bound_contact_id, current_quote_id, current_quote_revision,
                current_quote_snapshot_hash, commercial_status, intake_status,
                production_status, fulfillment_mode, balance_status,
                self_collection_recorded, customer_final_quote_accepted_at,
                production_accepted_at, closed_at, row_version, created_at
            ) VALUES (
                %s, %s, %s, %s, 1, %s, 'COMPLETED', 'ACCEPTED', 'RELEASED',
                'SELF_DROP_SELF_COLLECT', 'PAID', TRUE, %s, %s, %s, 1, %s
            )
            """,
            (
                order_id,
                store_id,
                uuid4(),
                quote_id,
                final.document.snapshot_hash,
                timestamp - timedelta(hours=11),
                timestamp - timedelta(hours=10),
                timestamp - timedelta(hours=1),
                timestamp - timedelta(hours=12),
            ),
        )
    return store_id, order_id


def _hash(value: str) -> str:
    return f"sha256:{sha256(value.encode('ascii')).hexdigest()}"


def _tool_trace(arguments: dict[str, Any], trace_id: str) -> Mapping[str, object]:
    return {
        "sequence": 1,
        "operation_id": "incidentOpen",
        "argument_field_names": sorted(arguments),
        "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
        "status_code": 200,
        "trace_id": trace_id,
    }


def _seed(fixture: SyntheticFixtureBundle) -> Mapping[str, Any]:
    value = fixture.payload.get("database_seed")
    if not isinstance(value, Mapping):
        raise ValueError("incident fixture seed is invalid")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"incident fixture {key} is invalid")
    return result


def _clock(payload: Mapping[str, Any]) -> datetime:
    value = datetime.fromisoformat(_text(payload, "clock").replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("incident fixture clock is invalid")
    return value


def _event_id(fixture: SyntheticFixtureBundle) -> str:
    events = fixture.payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("incident fixture must contain one event")
    return _text(events[0], "event_id")


__all__ = [
    "SyntheticIncidentPreflight",
    "execute_correction_preflight",
    "execute_incident_preflight",
]
