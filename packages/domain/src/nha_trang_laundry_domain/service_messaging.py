"""The published transactional messaging policy, and the pure decision a service send rests on.

`CONSENT-TRANSACTIONAL-001`, `DEC-033`. A service message -- "đồ của anh/chị đã giặt xong" -- is not
marketing, and the SECURITY spec keeps the two purposes distinct (§8.4, §14). It still needs a
basis the server can prove, and a customer who wrote STOP still gets nothing the shop initiates on
that channel until a human releases the block on evidence the server verifies.

This module decides, and only decides. It reads no clock, no database and no environment: every
fact -- the suppression state, the policy in force, when the contact last wrote, whether they have
an open order and when their last order closed, and the instant being judged -- is passed in, so a
decision can be recomputed from its recorded inputs years later.

Which bases count and the hour values are the owner's (`DEC-033` boundary): they amount to the
shop's legal grounds for sending service messages under Vietnamese personal-data rules. The code
ships no default. With no published policy every service send is refused with
`MESSAGING_POLICY_UNPUBLISHED`, which is the intended state of a fresh deployment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from nha_trang_laundry_domain.consent import SuppressionState

#: The `configuration_versions.config_type` the policy is published under.
MESSAGING_POLICY_CONFIG_TYPE: Final = "TRANSACTIONAL_MESSAGING_POLICY"
MESSAGING_POLICY_SCHEMA: Final = "transactional-messaging-policy-v1"
MESSAGING_POLICY_DECISION: Final = "DEC-033"

#: Largest window accepted, in hours: one year. A figure past this is not a service window anybody
#: meant, and refusing it at publication is cheaper than explaining it later.
MAX_WINDOW_HOURS: Final = 24 * 366


class ServiceBasis(StrEnum):
    """A ground on which the shop may initiate a service message; the owner publishes which."""

    #: The contact wrote to the shop on this channel within `service_window_hours`.
    CUSTOMER_INITIATED = "CUSTOMER_INITIATED"
    #: The contact has an order that is not closed, or closed within `closed_order_grace_hours`.
    OPEN_ORDER = "OPEN_ORDER"


class TransactionalEgressDecision(StrEnum):
    ALLOW = "ALLOW"
    SUPPRESSED = "SUPPRESSED"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class TransactionalRefusal(StrEnum):
    """Why a service send was refused. The console renders each one in Vietnamese."""

    #: An exact STOP on this channel: nothing the shop initiates may be sent until a release.
    SUPPRESSED = "SUPPRESSED"
    #: An ambiguous opt-out is waiting for a human to read it.
    PENDING_REVIEW = "PENDING_REVIEW"
    #: A suppression row in a state nobody should have written. Unknown blocks.
    SUPPRESSION_UNKNOWN = "SUPPRESSION_UNKNOWN"
    #: The owner has not published the grounds for service messages.
    MESSAGING_POLICY_UNPUBLISHED = "MESSAGING_POLICY_UNPUBLISHED"
    #: No recent message from the customer and no open (or recently closed) order.
    NO_SERVICE_BASIS = "NO_SERVICE_BASIS"


class MessagingPolicyError(ValueError):
    """Raised when a transactional messaging policy document is malformed."""


@dataclass(frozen=True, slots=True)
class TransactionalMessagingPolicy:
    service_window_hours: int
    closed_order_grace_hours: int
    #: In the canonical order of `ServiceBasis`, whatever order the document listed them in.
    bases: tuple[ServiceBasis, ...]


@dataclass(frozen=True, slots=True)
class ServiceBasisFacts:
    """What the server read about the contact, at the instant being judged. Nothing inferred."""

    #: The latest inbound customer message from this contact on this channel at or before `at`.
    last_inbound_message_at: datetime | None
    #: Whether an order bound to this contact is not closed.
    has_open_order: bool
    #: The latest `closed_at` of an order bound to this contact, at or before `at`.
    last_order_closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TransactionalEgressOutcome:
    decision: TransactionalEgressDecision
    refusal: TransactionalRefusal | None
    #: The basis the send rests on when allowed; `None` otherwise.
    basis: ServiceBasis | None

    @property
    def allowed(self) -> bool:
        return self.decision is TransactionalEgressDecision.ALLOW


def parse_messaging_policy(payload: Mapping[str, Any]) -> TransactionalMessagingPolicy:
    """Read a published payload into typed figures, or refuse it.

    Registered as the configuration type's validator *and* run again on every read, so a document
    that reached the table without passing is refused at the send rather than trusted.
    """

    if not isinstance(payload, Mapping) or payload.get("schema") != MESSAGING_POLICY_SCHEMA:
        raise MessagingPolicyError(
            "messaging policy payload is not a transactional-messaging-policy-v1 document"
        )
    if payload.get("decision") != MESSAGING_POLICY_DECISION:
        raise MessagingPolicyError("a messaging policy must name the decision it expresses")
    for statement in ("owner_confirmation_vi", "owner_confirmation_en"):
        text = payload.get(statement)
        if not isinstance(text, str) or not text.strip():
            # The document is the owner's confirmation of the shop's grounds; a copy without the
            # sentence saying so is not the document the owner was asked to publish.
            raise MessagingPolicyError(f"messaging policy must carry {statement}")
    service_window_hours = _hours(payload, "service_window_hours", minimum=1)
    closed_order_grace_hours = _hours(payload, "closed_order_grace_hours", minimum=0)
    raw_bases = payload.get("bases")
    if not isinstance(raw_bases, list) or not raw_bases:
        raise MessagingPolicyError("messaging policy must list at least one basis")
    named: set[ServiceBasis] = set()
    for value in raw_bases:
        try:
            basis = ServiceBasis(value) if isinstance(value, str) else None
        except ValueError:
            basis = None
        if basis is None:
            raise MessagingPolicyError(f"messaging policy names an unknown basis: {value!r}")
        if basis in named:
            raise MessagingPolicyError(f"messaging policy names {basis.value} twice")
        named.add(basis)
    return TransactionalMessagingPolicy(
        service_window_hours=service_window_hours,
        closed_order_grace_hours=closed_order_grace_hours,
        bases=tuple(basis for basis in ServiceBasis if basis in named),
    )


def validate_messaging_policy(payload: Mapping[str, Any]) -> None:
    """The configuration repository's validator shape: raise or return nothing."""

    parse_messaging_policy(payload)


def decide_transactional_egress(
    *,
    suppression: SuppressionState | None,
    policy: TransactionalMessagingPolicy | None,
    facts: ServiceBasisFacts,
    at: datetime,
) -> TransactionalEgressOutcome:
    """Whether the shop may initiate a service message to this contact on this channel at `at`.

    Order matters and is fixed: suppression first, because a STOP is the truest reason and must be
    the one staff read; then the policy; then the basis. `suppression is None` means no
    TRANSACTIONAL row exists -- nobody wrote STOP on this channel -- and that alone permits nothing:
    a basis is still required.

    Window edges are inclusive at exactly `service_window_hours` / `closed_order_grace_hours` before
    `at`, and a fact dated after `at` is not counted.
    """

    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("the instant being judged must be timezone-aware")
    if suppression is SuppressionState.SUPPRESSED:
        return _refused(TransactionalEgressDecision.SUPPRESSED, TransactionalRefusal.SUPPRESSED)
    if suppression is SuppressionState.PENDING_REVIEW_BLOCKED:
        return _refused(
            TransactionalEgressDecision.REQUIRE_HUMAN, TransactionalRefusal.PENDING_REVIEW
        )
    if suppression is not None and suppression is not SuppressionState.CLEAR:
        return _refused(
            TransactionalEgressDecision.REQUIRE_HUMAN, TransactionalRefusal.SUPPRESSION_UNKNOWN
        )
    if policy is None:
        return _refused(
            TransactionalEgressDecision.REQUIRE_HUMAN,
            TransactionalRefusal.MESSAGING_POLICY_UNPUBLISHED,
        )
    for basis in policy.bases:
        if basis is ServiceBasis.CUSTOMER_INITIATED and _within(
            facts.last_inbound_message_at, at=at, hours=policy.service_window_hours
        ):
            return TransactionalEgressOutcome(TransactionalEgressDecision.ALLOW, None, basis)
        if basis is ServiceBasis.OPEN_ORDER and (
            facts.has_open_order
            or _within(facts.last_order_closed_at, at=at, hours=policy.closed_order_grace_hours)
        ):
            return TransactionalEgressOutcome(TransactionalEgressDecision.ALLOW, None, basis)
    return _refused(
        TransactionalEgressDecision.REQUIRE_HUMAN, TransactionalRefusal.NO_SERVICE_BASIS
    )


def _within(moment: datetime | None, *, at: datetime, hours: int) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a basis timestamp must be timezone-aware")
    return at - timedelta(hours=hours) <= moment <= at


def _refused(
    decision: TransactionalEgressDecision, refusal: TransactionalRefusal
) -> TransactionalEgressOutcome:
    return TransactionalEgressOutcome(decision, refusal, None)


def _hours(payload: Mapping[str, Any], field: str, *, minimum: int) -> int:
    value = payload.get(field)
    # `bool` is excluded because `True` is an `int` in Python and would publish as one hour.
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= MAX_WINDOW_HOURS
    ):
        raise MessagingPolicyError(
            f"messaging policy field {field} must be a whole number of hours "
            f"from {minimum} to {MAX_WINDOW_HOURS}"
        )
    return value


__all__ = [
    "MAX_WINDOW_HOURS",
    "MESSAGING_POLICY_CONFIG_TYPE",
    "MESSAGING_POLICY_DECISION",
    "MESSAGING_POLICY_SCHEMA",
    "MessagingPolicyError",
    "ServiceBasis",
    "ServiceBasisFacts",
    "TransactionalEgressDecision",
    "TransactionalEgressOutcome",
    "TransactionalMessagingPolicy",
    "TransactionalRefusal",
    "decide_transactional_egress",
    "parse_messaging_policy",
    "validate_messaging_policy",
]
