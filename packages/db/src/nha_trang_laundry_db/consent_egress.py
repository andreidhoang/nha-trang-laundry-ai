"""The egress suppression re-check a sender must run inside its own send transaction.

Ingress suppression already exists: `InboxRepository.record` writes the consent event and the
suppression entry atomically with the inbound webhook. That closes half the hole. The other half is
the claim-to-send window — a STOP that arrives after a message is claimed for sending but before it
is dispatched. A check performed only when the outbox row was created is insufficient, and the
manifest counts a suppression miss as a zero-tolerance G2 defect.

This module is the reusable guard. It is deliberately **not** a sender: it takes the cursor of a
transaction someone else opened, so the check cannot drift outside the transaction that dispatches
the message. `CHANNEL-ZALO-001` owns proving that a real sender calls it there.

The race is closed with a transaction-scoped advisory lock keyed by contact and channel, taken by
both the ingress writer and this guard. A row-level lock alone would not do it: on a contact's first
STOP there is no row to lock, so the insert could commit in the window between the guard's read and
the dispatch. Both sides taking the same advisory key serialize whether or not a row exists.

Every outcome fails closed. Unknown, pending and policy-unavailable states all deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.consent import SuppressionState, marketing_send_allowed

#: Namespace for the advisory lock, so this key space cannot collide with another feature's.
_LOCK_NAMESPACE = 0x53555052  # "SUPR"


class EgressDecision(StrEnum):
    ALLOW = "ALLOW"
    SUPPRESSED = "SUPPRESSED"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class EgressSuppressionError(RuntimeError):
    """Raised when the guard is used outside a transaction it can protect."""


@dataclass(frozen=True, slots=True)
class EgressCheck:
    decision: EgressDecision
    suppression_state: str
    consent_active: bool
    purpose: str

    @property
    def send_allowed(self) -> bool:
        return self.decision is EgressDecision.ALLOW


def suppression_lock(cursor: Any, *, contact_binding_id: UUID, channel: str) -> None:
    """Take the transaction-scoped lock that serializes a STOP against an in-flight send.

    Both the ingress writer and the egress guard call this with the same key. It is released when
    the surrounding transaction ends, so a caller cannot hold it across a dispatch by accident.
    """

    cursor.execute(
        "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
        (_LOCK_NAMESPACE, f"{contact_binding_id}:{channel}"),
    )


def check_egress_allowed(
    cursor: Any,
    *,
    contact_binding_id: UUID,
    channel: str,
    purpose: str = "MARKETING",
    consent_active: bool = True,
) -> EgressCheck:
    """Re-check suppression inside the caller's send transaction. Never called by a model.

    `cursor` must belong to the transaction that will dispatch the message. The guard takes the
    advisory lock first, so a STOP arriving concurrently either commits before this read and is
    seen, or waits until the send transaction ends.
    """

    if purpose != "MARKETING":
        # The only purpose the suppression tables model today. An unmodelled purpose is unknown
        # policy, and unknown policy fails closed rather than defaulting to allow.
        return EgressCheck(
            decision=EgressDecision.REQUIRE_HUMAN,
            suppression_state=SuppressionState.UNKNOWN_BLOCKED.value,
            consent_active=consent_active,
            purpose=purpose,
        )
    suppression_lock(cursor, contact_binding_id=contact_binding_id, channel=channel)
    cursor.execute(
        """
        SELECT state
        FROM suppression_entries
        WHERE contact_binding_id = %s AND purpose = 'MARKETING' AND channel = %s
        """,
        (contact_binding_id, channel),
    )
    row = cursor.fetchone()
    # No row means nobody has recorded a consent decision for this contact and channel. That is
    # unknown, not clear, and unknown blocks.
    state = SuppressionState(str(row[0])) if row is not None else SuppressionState.UNKNOWN_BLOCKED
    if marketing_send_allowed(consent_active=consent_active, suppression=state):
        decision = EgressDecision.ALLOW
    elif state is SuppressionState.SUPPRESSED:
        decision = EgressDecision.SUPPRESSED
    else:
        decision = EgressDecision.REQUIRE_HUMAN
    return EgressCheck(
        decision=decision,
        suppression_state=state.value,
        consent_active=consent_active,
        purpose=purpose,
    )


def record_clear_consent(
    cursor: Any,
    *,
    contact_binding_id: UUID,
    channel: str,
    source_consent_event_id: UUID,
    now: Any,
) -> None:
    """Record an explicit CLEAR state so a send has something affirmative to rely on.

    Absent this, every contact is UNKNOWN_BLOCKED and no marketing send is ever permitted, which is
    correct but means CLEAR must be written by a deliberate, audited act rather than by omission.
    """

    suppression_lock(cursor, contact_binding_id=contact_binding_id, channel=channel)
    cursor.execute(
        """
        INSERT INTO suppression_entries (
            contact_binding_id, purpose, channel, state, source_consent_event_id,
            row_version, updated_at
        ) VALUES (%s, 'MARKETING', %s, 'CLEAR', %s, 1, %s)
        ON CONFLICT (contact_binding_id, purpose, channel) DO UPDATE
        SET state = CASE
                -- A withdrawal is never undone by a later CLEAR write; only an explicit,
                -- separately authorized consent restoration may do that.
                WHEN suppression_entries.state = 'SUPPRESSED' THEN 'SUPPRESSED'
                ELSE 'CLEAR'
            END,
            source_consent_event_id = EXCLUDED.source_consent_event_id,
            row_version = suppression_entries.row_version + 1,
            updated_at = EXCLUDED.updated_at
        """,
        (contact_binding_id, channel, source_consent_event_id, now),
    )


__all__ = [
    "EgressCheck",
    "EgressDecision",
    "EgressSuppressionError",
    "check_egress_allowed",
    "record_clear_consent",
    "suppression_lock",
]
