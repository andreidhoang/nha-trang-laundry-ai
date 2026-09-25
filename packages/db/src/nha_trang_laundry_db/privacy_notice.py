"""Publish and read the customer privacy notice the shop's customer list rests on.

`CUSTOMER-001`, `DEC-034`. The notice tells a customer what the shop keeps, why, for how long and
how to ask for deletion (`docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md`). It is a configuration
version exactly like the messaging policy (`service_messaging.publish_messaging_policy`, which this
mirrors): an immutable hashed document in `configuration_versions`, published by a script a human
runs -- `scripts/publish_privacy_notice.py` -- and only by an active `OWNER_ADMIN`, because
publishing it is the owner confirming the text customers are told. Nothing seeds it.

Until it is published every attempt to create a customer record is refused with
`PRIVACY_NOTICE_UNPUBLISHED`, and the counter keeps serving walk-ins with a ticket (`DEC-013`).
The software asserts no legal basis of its own; whether the business must also file a processing
impact dossier is for the owner and their adviser (`DEC-034`, last bullet).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.customers import (
    PRIVACY_NOTICE_CONFIG_TYPE,
    PrivacyNotice,
    PrivacyNoticeError,
    parse_privacy_notice,
    validate_privacy_notice,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffRole


class PrivacyNoticeAuthorizationError(PermissionError):
    """Only an active owner may publish the notice customers are told."""


@dataclass(frozen=True, slots=True)
class PublishedPrivacyNotice:
    notice: PrivacyNotice
    version_id: UUID
    version: int
    snapshot_hash: str


def publish_privacy_notice(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish one notice document; return its digest and whether this call created a version.

    Idempotent on the digest of the version *in force*: publishing the notice already in force
    changes nothing, and publishing an earlier text again is a new version that is.
    """

    validate_privacy_notice(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository({PRIVACY_NOTICE_CONFIG_TYPE: validate_privacy_notice})
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        in_force = ConfigurationRepository.latest_published(cursor, PRIVACY_NOTICE_CONFIG_TYPE)
        if in_force is not None and in_force.snapshot_hash == digest:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (PRIVACY_NOTICE_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=PRIVACY_NOTICE_CONFIG_TYPE,
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


def read_published_privacy_notice(cursor: Any) -> PublishedPrivacyNotice | None:
    """The notice in force, or `None` -- which means no customer record may be created.

    Re-hashed against the digest recorded at publication and re-parsed before use, as the messaging
    policy read does: a payload that no longer matches its digest is not a published notice.
    """

    published = ConfigurationRepository.latest_published(cursor, PRIVACY_NOTICE_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    try:
        notice = parse_privacy_notice(payload)
    except PrivacyNoticeError:
        return None
    return PublishedPrivacyNotice(
        notice=notice,
        version_id=published.version_id,
        version=published.version,
        snapshot_hash=published.snapshot_hash,
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
        raise PrivacyNoticeAuthorizationError(
            "only an active OWNER_ADMIN may publish the customer privacy notice"
        )


__all__ = [
    "PrivacyNoticeAuthorizationError",
    "PublishedPrivacyNotice",
    "publish_privacy_notice",
    "read_published_privacy_notice",
]
