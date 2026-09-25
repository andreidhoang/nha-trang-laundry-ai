"""Publish and read the transactional messaging policy; read the facts a service basis rests on.

`CONSENT-TRANSACTIONAL-001`, `DEC-033`. The policy is a configuration version exactly like the
remedy and promotion policies (invariant 11): an immutable, hashed document in
`configuration_versions`, published by a script a human runs, never seeded by a process that
started. Unlike those two, this one is refused unless the publisher is an active `OWNER_ADMIN`:
the document is the owner's confirmation of the shop's grounds for sending service messages, and
nobody else can give it.

Until it is published every service send fails closed with `MESSAGING_POLICY_UNPUBLISHED`.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.service_messaging import (
    MESSAGING_POLICY_CONFIG_TYPE,
    MessagingPolicyError,
    ServiceBasisFacts,
    TransactionalMessagingPolicy,
    parse_messaging_policy,
    validate_messaging_policy,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffRole

#: The provider event type of a customer's own message. Delivery receipts, reactions and anything
#: else a provider reports are not the customer writing to the shop, and count for nothing here.
INBOUND_MESSAGE_EVENT_TYPE = "MESSAGE"


class MessagingPolicyAuthorizationError(PermissionError):
    """Only an active owner may publish the shop's grounds for service messages."""


@dataclass(frozen=True, slots=True)
class PublishedMessagingPolicy:
    policy: TransactionalMessagingPolicy
    version_id: UUID
    version: int
    snapshot_hash: str


def publish_messaging_policy(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish one messaging policy document; return its digest and whether this call created it.

    Idempotent on the digest of the version *in force*, as `publish_remedy_policy` is:
    publishing the document already in force changes nothing, and publishing an earlier document
    again is a new version that is.
    """

    validate_messaging_policy(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository({MESSAGING_POLICY_CONFIG_TYPE: validate_messaging_policy})
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        in_force = ConfigurationRepository.latest_published(cursor, MESSAGING_POLICY_CONFIG_TYPE)
        if in_force is not None and in_force.snapshot_hash == digest:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (MESSAGING_POLICY_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=MESSAGING_POLICY_CONFIG_TYPE,
            version=next_version,
            payload=payload,
            created_by=actor_id,
        ),
        correlation_id=uuid4(),
    )
    repository.publish(
        connection,
        config_id=config_id,
        version=next_version,
        snapshot_hash_value=digest,
        published_by=actor_id,
        correlation_id=uuid4(),
    )
    return digest, True


def read_published_messaging_policy(cursor: Any) -> PublishedMessagingPolicy | None:
    """The policy in force, or `None` -- which means every service send is refused.

    The stored payload is re-hashed against the digest recorded at publication and re-parsed before
    it is used, as the remedy policy read does: a payload that no longer matches its digest, or no
    longer parses, is not a published policy whatever the lifecycle column says.
    """

    published = ConfigurationRepository.latest_published(cursor, MESSAGING_POLICY_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    try:
        policy = parse_messaging_policy(payload)
    except MessagingPolicyError:
        return None
    return PublishedMessagingPolicy(
        policy=policy,
        version_id=published.version_id,
        version=published.version,
        snapshot_hash=published.snapshot_hash,
    )


def read_service_basis_facts(
    cursor: Any, *, contact_binding_id: UUID, channel: str, at: datetime
) -> ServiceBasisFacts:
    """What the database records about this contact, as of `at`. Nothing is inferred.

    * the latest inbound customer message from the contact on this channel, at or before `at`,
      whose opt-out disposition was `NONE` -- a STOP is not a request for service;
    * whether an order bound to the contact was open at `at`: created by then and neither
      cancelled nor completed by then. A cancelled order records no close time, so it is closed and
      earns no grace -- unknown is not generous;
    * the latest completion (`closed_at`) at or before `at`.
    """

    cursor.execute(
        """
        SELECT max(received_at)
        FROM webhook_events
        WHERE contact_binding_id = %s AND channel = %s AND event_type = %s
          AND opt_out_disposition = 'NONE' AND received_at <= %s
        """,
        (contact_binding_id, channel, INBOUND_MESSAGE_EVENT_TYPE, at),
    )
    inbound = cursor.fetchone()
    cursor.execute(
        """
        SELECT
            EXISTS (
                SELECT 1 FROM orders
                WHERE bound_contact_id = %(contact)s AND created_at <= %(at)s
                  AND (commercial_status NOT IN ('CANCELLED', 'COMPLETED')
                       OR (commercial_status = 'COMPLETED' AND closed_at > %(at)s))
            ),
            (
                SELECT max(closed_at) FROM orders
                WHERE bound_contact_id = %(contact)s AND closed_at <= %(at)s
            )
        """,
        {"contact": contact_binding_id, "at": at},
    )
    orders = cursor.fetchone()
    return ServiceBasisFacts(
        last_inbound_message_at=_aware(inbound[0] if inbound else None),
        has_open_order=bool(orders[0]) if orders else False,
        last_order_closed_at=_aware(orders[1] if orders else None),
    )


def _require_active_owner(cursor: Any, actor_id: UUID) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE' AND r.role = %s AND r.revoked_at IS NULL
        """,
        (actor_id, StaffRole.OWNER_ADMIN.value),
    )
    if cursor.fetchone() is None:
        raise MessagingPolicyAuthorizationError(
            "only an active OWNER_ADMIN may publish the transactional messaging policy"
        )


def _aware(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("stored timestamp is invalid")
    return value


__all__ = [
    "INBOUND_MESSAGE_EVENT_TYPE",
    "MessagingPolicyAuthorizationError",
    "PublishedMessagingPolicy",
    "publish_messaging_policy",
    "read_published_messaging_policy",
    "read_service_basis_facts",
]
