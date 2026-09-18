"""Publish the owner's promotion programme and read the one in force. `DEC-002`, `PROMO-WIRING-001`.

Two functions and one rule between them: a promotion programme is a published configuration version,
never a Python constant. `CURRENT_PROMOTION` was the constant, and what it cost is on the record --
the shop could not start a programme, change a rate or retire an expired one without a deploy, and
the expired one is still sitting in the source tree. `REMEDY-001` set the shape for fixing that and
this follows it exactly, down to the idempotency-on-digest and the re-hash before parsing.

`read_published_promotion_program` returning `None` is a legitimate and expected answer, not an
error: a deployment whose owner has published no programme has no promotion, and every quote
composes at list price carrying `PROMOTION_NOT_PUBLISHED`. Invariant 11 -- there is no fallback, no
default rate and no last-known-good programme.

Nothing here decides money. `nha_trang_laundry_domain.promotion` does, through
`quote_composition`; this module reads stored state and hands it over, the same division
`remedies.py` and `SettlementRepository` keep.
"""

from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.promotion_policy import (
    PROMOTION_POLICY_CONFIG_TYPE,
    PromotionPolicyError,
    PublishedPromotionProgram,
    parse_promotion_policy,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    ConfigurationValidationError,
    JsonObject,
    snapshot_hash,
)


def validate_promotion_policy_document(payload: JsonObject) -> None:
    """The registered validator for `PROMOTION_POLICY`. A malformed programme is refused here.

    It runs the real parse rather than a shape check, exactly as `validate_pricebook` and
    `validate_remedy_policy` do: a document that satisfies a schema and then fails to produce a rate
    at the counter has moved the failure from the moment somebody could fix it to the moment
    somebody needed it -- with a customer standing there.
    """

    try:
        parse_promotion_policy(payload)
    except PromotionPolicyError as error:
        raise ConfigurationValidationError(
            f"promotion policy is not publishable: {error}"
        ) from error


def promotion_configuration_repository() -> ConfigurationRepository:
    """A repository that knows how to validate this one config type, and no others."""

    return ConfigurationRepository(
        {PROMOTION_POLICY_CONFIG_TYPE: validate_promotion_policy_document}
    )


def publish_promotion_policy(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish one promotion programme, returning its digest and whether this call created it.

    Idempotent on the digest for the reason `publish_remedy_policy` is: republishing an identical
    document would create a second version of the same programme, and every quote citing version 1
    would start refusing acceptance with `PROMOTION_CHANGED_SINCE_QUOTE` for no reason at all --
    `accept_quote_revision` compares version identity before it compares dong.

    A *different* document is a new version, and that is the whole point of the item: the owner
    starts a programme, changes a rate or retires an expired one by publishing, with no deploy.
    """

    validate_promotion_policy_document(payload)
    digest = snapshot_hash(payload)
    repository = promotion_configuration_repository()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version FROM configuration_versions
            WHERE config_type = %s AND snapshot_hash = %s AND lifecycle = 'PUBLISHED'
            """,
            (PROMOTION_POLICY_CONFIG_TYPE, digest),
        )
        if cursor.fetchone() is not None:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (PROMOTION_POLICY_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=PROMOTION_POLICY_CONFIG_TYPE,
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


def read_published_promotion_program(cursor: Any) -> PublishedPromotionProgram | None:
    """The promotion programme in force, or `None`, which means no programme is running.

    Invariant 11. `None` is the correct answer for a fresh deployment and for a shop that is simply
    not running a promotion, and the correct behaviour on it is to quote at list price rather than
    to fall back on a constant nobody ratified.

    The stored payload is re-hashed against the digest recorded at publication before it is parsed,
    for the same reason the pricebook and remedy reads are: a payload that no longer matches its
    digest is not a published programme, whatever the lifecycle column says.
    """

    published = ConfigurationRepository.latest_published(cursor, PROMOTION_POLICY_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    try:
        program = parse_promotion_policy(payload)
    except PromotionPolicyError:
        # A stored payload that no longer parses is not a programme to apply. It fails closed rather
        # than raising, so the caller quotes at list price -- which is what an unusable published
        # document means at the counter, and is the safe direction to fail in for a discount.
        return None
    return PublishedPromotionProgram(
        program=program,
        version_id=published.version_id,
        version=published.version,
        # The prefix `quotes.HASH_PATTERN` requires of every hash on an immutable revision. The
        # column stores the bare digest, exactly as it does for the pricebook.
        snapshot_hash=f"JCS-SHA256-V1:{published.snapshot_hash}",
    )


__all__ = [
    "promotion_configuration_repository",
    "publish_promotion_policy",
    "read_published_promotion_program",
    "validate_promotion_policy_document",
]
