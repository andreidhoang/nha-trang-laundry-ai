"""Publish the approved pricebook as an immutable configuration version.

`CONFIG-001` built the publication primitive and `DOMAIN-002` built the importer, and nothing
connected them: no code path ever turned the owner-confirmed CSV into a published configuration, so
a running system had no prices at all. `QUOTE-COMMAND-001` needed one, because a quote revision must
cite the pricebook it was priced against by version and hash.

This lives in `packages/db` rather than in `scripts/` because it is a persistence operation with
invariants worth testing, and a script is a place tests do not reach. `scripts/publish_pricebook.py`
is the human-facing wrapper.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.pricebook_import import (
    canonical_pricebook_payload,
    import_pricebook_csv,
    published_price_rules,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    ConfigurationValidationError,
    JsonObject,
    snapshot_hash,
)

CONFIG_TYPE = "PRICEBOOK"


class PricebookPublicationError(RuntimeError):
    """Raised when the document to publish is not the document the importer approved."""


def validate_pricebook(payload: JsonObject) -> None:
    """Refuse any pricebook that cannot be turned back into runtime rules.

    The registered validator is the last gate before a payload becomes something quotes cite, so it
    runs the real reconstruction rather than a shape check. A payload that parses but silently loses
    a tier would satisfy a schema and fail here.
    """
    try:
        rules = published_price_rules(payload)
    except ValueError as error:
        raise ConfigurationValidationError(f"pricebook is not publishable: {error}") from error
    if not rules:
        raise ConfigurationValidationError("pricebook produced no runtime rules")


def publish_pricebook(connection: Any, *, actor_id: UUID, source: bytes) -> tuple[str, bool]:
    """Publish the pricebook in `source`, returning its digest and whether this call created it."""
    pricebook = import_pricebook_csv(source)
    payload = canonical_pricebook_payload(pricebook)
    digest = snapshot_hash(payload)
    if f"JCS-SHA256-V1:{digest}" != pricebook.manifest.canonical_snapshot_hash:
        raise PricebookPublicationError(
            "the document to store does not hash to the importer's manifest"
        )

    repository = ConfigurationRepository({CONFIG_TYPE: validate_pricebook})
    with connection.cursor() as cursor:
        # Ask before writing. Publishing the same pricebook twice would create a second version of
        # an identical document, and every quote citing version 1 would start looking out of date
        # for no reason. Idempotency here is correctness, not convenience.
        cursor.execute(
            """
            SELECT version FROM configuration_versions
            WHERE config_type = %s AND snapshot_hash = %s AND lifecycle = 'PUBLISHED'
            """,
            (CONFIG_TYPE, digest),
        )
        if cursor.fetchone() is not None:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1

    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=CONFIG_TYPE,
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


__all__ = ["CONFIG_TYPE", "PricebookPublicationError", "publish_pricebook", "validate_pricebook"]
