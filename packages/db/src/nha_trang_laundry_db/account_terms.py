"""The owner's account terms (điều khoản công nợ) as a published configuration. `PAYMENT-002`.

`DEC-035` fixed the terms an account customer is held to -- the calendar month, due by the 15th of
the next month, the overdue block, a limit the owner types -- and they are a promise the shop makes
about money, so they run only while the owner has published them (`SHOP_OPERATIONS_SPEC_V1.md` §0).
The document is `templates/account-terms-dec-035.json`, published by
`scripts/publish_account_terms.py`: an immutable, hashed configuration version, like the messaging,
turnaround and privacy documents. Only an active `OWNER_ADMIN` may publish it.

**Before publication** no account is opened and no order leaves on one: both refuse
`ACCOUNT_TERMS_UNPUBLISHED`. What an existing account already owes is still collected -- a payment
is never refused for want of a document -- and its statements still read.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.accounts import (
    ACCOUNT_TERMS_CONFIG_TYPE,
    AccountTerms,
    AccountTermsError,
    parse_account_terms,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffRole


class AccountTermsAuthorizationError(PermissionError):
    """Only an active owner publishes the shop's credit terms."""


@dataclass(frozen=True, slots=True)
class PublishedAccountTerms:
    terms: AccountTerms
    version_id: UUID
    version: int
    snapshot_hash: str


def validate_account_terms_document(payload: JsonObject) -> None:
    """The typed validator the configuration repository runs before it stores a draft."""

    parse_account_terms(dict(payload))


def publish_account_terms(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish the terms; return the digest and whether this call created a version.

    Idempotent on the version in force: the same document again changes nothing.
    """

    validate_account_terms_document(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository(
        {ACCOUNT_TERMS_CONFIG_TYPE: validate_account_terms_document}
    )
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        in_force = ConfigurationRepository.latest_published(cursor, ACCOUNT_TERMS_CONFIG_TYPE)
        if in_force is not None and in_force.snapshot_hash == digest:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (ACCOUNT_TERMS_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1
    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=ACCOUNT_TERMS_CONFIG_TYPE,
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


def read_published_account_terms(cursor: Any) -> PublishedAccountTerms | None:
    """The terms in force, or `None` -- which refuses new accounts and new account handovers.

    Re-hashed against the digest recorded at publication and re-parsed: a payload that no longer
    matches, or no longer states `DEC-035`'s terms, is not a published policy.
    """

    published = ConfigurationRepository.latest_published(cursor, ACCOUNT_TERMS_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    try:
        terms = parse_account_terms(dict(payload))
    except AccountTermsError:
        return None
    return PublishedAccountTerms(
        terms=terms,
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
        raise AccountTermsAuthorizationError(
            "only an active OWNER_ADMIN may publish the account terms"
        )


__all__ = [
    "AccountTermsAuthorizationError",
    "PublishedAccountTerms",
    "publish_account_terms",
    "read_published_account_terms",
    "validate_account_terms_document",
]
