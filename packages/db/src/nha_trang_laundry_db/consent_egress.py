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

`CONSENT-TRANSACTIONAL-001` (`DEC-033`) added the second purpose. A TRANSACTIONAL (service) send
passes only when (a) no TRANSACTIONAL suppression blocks it -- an absent row or a CLEAR one passes
this half, because nobody wrote STOP -- and (b) the owner's published messaging policy names a basis
the server can prove: the customer wrote on this channel recently, or has an open order. No
published policy refuses every service send with `MESSAGING_POLICY_UNPUBLISHED`. The pure rule is
`nha_trang_laundry_domain.service_messaging.decide_transactional_egress`; this module reads its
facts under the same advisory lock the ingress STOP writer takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.consent import SuppressionState, marketing_send_allowed
from nha_trang_laundry_domain.service_messaging import (
    TransactionalEgressDecision,
    TransactionalRefusal,
    decide_transactional_egress,
)

from .service_messaging import read_published_messaging_policy, read_service_basis_facts

#: The two purposes the suppression tables model since `0053`.
MARKETING = "MARKETING"
TRANSACTIONAL = "TRANSACTIONAL"

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
    #: The stored state, or `NONE` for a TRANSACTIONAL check that found no row (nobody wrote STOP).
    suppression_state: str
    consent_active: bool
    purpose: str
    #: TRANSACTIONAL only: why a send was refused (`TransactionalRefusal`), the basis an allowed
    #: send rests on (`ServiceBasis`), and the published policy version the decision applied.
    reason_code: str | None = None
    basis: str | None = None
    policy_version: int | None = None
    policy_snapshot_hash: str | None = None
    evaluated_at: datetime | None = None

    @property
    def send_allowed(self) -> bool:
        return self.decision is EgressDecision.ALLOW

    def record(self) -> dict[str, object]:
        """What the audit keeps about why a send was allowed or refused. JSON-safe."""

        return {
            "purpose": self.purpose,
            "decision": self.decision.value,
            "suppression_state": self.suppression_state,
            "reason_code": self.reason_code,
            "basis": self.basis,
            "policy_version": self.policy_version,
            "policy_snapshot_hash": self.policy_snapshot_hash,
            "evaluated_at": None if self.evaluated_at is None else self.evaluated_at.isoformat(),
        }


#: `suppression_state` for a TRANSACTIONAL check that found no row at all.
NO_SUPPRESSION_RECORDED = "NONE"


class EgressRefusedError(ValueError):
    """A send refused by the egress guard, carrying the decision so a caller can say why.

    Raised by the manual-send prepare and attest steps and by the `SEND_MESSAGE` approval request.
    Raised before anything is written, so the caller's transaction rolls back whole.
    """

    def __init__(
        self,
        check: EgressCheck,
        *,
        contact_binding_id: UUID,
        channel: str,
        store_id: UUID | None = None,
    ) -> None:
        super().__init__(f"egress refused: {check.reason_code or check.decision.value}")
        self.check = check
        self.contact_binding_id = contact_binding_id
        self.channel = channel
        self.store_id = store_id


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
    at: datetime | None = None,
) -> EgressCheck:
    """Re-check suppression inside the caller's send transaction. Never called by a model.

    `cursor` must belong to the transaction that will dispatch the message. The guard takes the
    advisory lock first, so a STOP arriving concurrently either commits before this read and is
    seen, or waits until the send transaction ends.

    A TRANSACTIONAL check needs `at`, the instant the send is judged at: the service window and the
    closed-order grace are measured back from it, and a guard that read the wall clock itself could
    not be reproduced from its record.
    """

    if purpose == TRANSACTIONAL:
        if at is None:
            raise EgressSuppressionError("a TRANSACTIONAL egress check needs the instant it judges")
        suppression_lock(cursor, contact_binding_id=contact_binding_id, channel=channel)
        return evaluate_transactional_egress(
            cursor, contact_binding_id=contact_binding_id, channel=channel, at=at
        )
    if purpose != MARKETING:
        # MARKETING and TRANSACTIONAL are what the tables model (`0053`). Any other is unknown
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


def evaluate_transactional_egress(
    cursor: Any, *, contact_binding_id: UUID, channel: str, at: datetime
) -> EgressCheck:
    """The TRANSACTIONAL decision from the facts as the cursor sees them. Takes no lock.

    `check_egress_allowed` is the guard and takes the advisory lock first; this is the evaluation
    it runs, exposed for reads that only report a state (the console's service-messaging panel) and
    must not serialize against ingress. A read's answer is advice; a send's is the guard's.
    """

    stored = transactional_suppression_state(
        cursor, contact_binding_id=contact_binding_id, channel=channel
    )
    published = read_published_messaging_policy(cursor)
    facts = read_service_basis_facts(
        cursor, contact_binding_id=contact_binding_id, channel=channel, at=at
    )
    outcome = decide_transactional_egress(
        suppression=stored,
        policy=None if published is None else published.policy,
        facts=facts,
        at=at,
    )
    decision = {
        TransactionalEgressDecision.ALLOW: EgressDecision.ALLOW,
        TransactionalEgressDecision.SUPPRESSED: EgressDecision.SUPPRESSED,
        TransactionalEgressDecision.REQUIRE_HUMAN: EgressDecision.REQUIRE_HUMAN,
    }[outcome.decision]
    return EgressCheck(
        decision=decision,
        suppression_state=NO_SUPPRESSION_RECORDED if stored is None else stored.value,
        consent_active=True,
        purpose=TRANSACTIONAL,
        reason_code=None if outcome.refusal is None else outcome.refusal.value,
        basis=None if outcome.basis is None else outcome.basis.value,
        policy_version=None if published is None else published.version,
        policy_snapshot_hash=None if published is None else published.snapshot_hash,
        evaluated_at=at,
    )


def transactional_suppression_state(
    cursor: Any, *, contact_binding_id: UUID, channel: str
) -> SuppressionState | None:
    """The stored TRANSACTIONAL state for this contact and channel, or `None` when no row exists."""

    cursor.execute(
        """
        SELECT state
        FROM suppression_entries
        WHERE contact_binding_id = %s AND purpose = 'TRANSACTIONAL' AND channel = %s
        """,
        (contact_binding_id, channel),
    )
    row = cursor.fetchone()
    return None if row is None else SuppressionState(str(row[0]))


def transactional_suppression_refusal(
    cursor: Any, *, contact_binding_id: UUID, channel: str
) -> EgressCheck | None:
    """The suppression half of the TRANSACTIONAL rule alone, or `None` when it does not block.

    For the advisory pre-check a `SEND_MESSAGE` approval request runs so staff learn early. It takes
    no lock and judges no basis -- a basis can appear before the send (the customer writes in) and
    the policy can be published, so neither is a reason to refuse an approval. A STOP is.
    """

    stored = transactional_suppression_state(
        cursor, contact_binding_id=contact_binding_id, channel=channel
    )
    if stored is None or stored is SuppressionState.CLEAR:
        return None
    if stored is SuppressionState.SUPPRESSED:
        decision, reason = EgressDecision.SUPPRESSED, TransactionalRefusal.SUPPRESSED
    elif stored is SuppressionState.PENDING_REVIEW_BLOCKED:
        decision, reason = EgressDecision.REQUIRE_HUMAN, TransactionalRefusal.PENDING_REVIEW
    else:
        decision, reason = EgressDecision.REQUIRE_HUMAN, TransactionalRefusal.SUPPRESSION_UNKNOWN
    return EgressCheck(
        decision=decision,
        suppression_state=stored.value,
        consent_active=True,
        purpose=TRANSACTIONAL,
        reason_code=reason.value,
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
    "MARKETING",
    "NO_SUPPRESSION_RECORDED",
    "TRANSACTIONAL",
    "EgressCheck",
    "EgressDecision",
    "EgressRefusedError",
    "EgressSuppressionError",
    "check_egress_allowed",
    "evaluate_transactional_egress",
    "record_clear_consent",
    "suppression_lock",
    "transactional_suppression_refusal",
    "transactional_suppression_state",
]
