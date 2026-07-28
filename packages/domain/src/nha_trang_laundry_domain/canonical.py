"""RFC 8785 canonical documents for immutable business snapshots."""

from __future__ import annotations

import hmac
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from typing import Final
from uuid import UUID

import rfc8785

type JsonValue = bool | int | str | list[JsonValue] | dict[str, JsonValue] | None

HASH_PREFIX: Final = "JCS-SHA256-V1:"
VOLATILE_HASH_FIELDS: Final = frozenset({"trace_id", "request_received_at", "server_generated_at"})


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by the canonical snapshot contract."""


@dataclass(frozen=True)
class CanonicalDocument:
    canonical_json: bytes
    snapshot_hash: str


def canonical_document(value: object, *, exclude_volatile: bool = True) -> CanonicalDocument:
    """Normalize a typed object and return its immutable JCS bytes and versioned hash."""
    try:
        normalized = _json_value(value, exclude_volatile=exclude_volatile)
        canonical = rfc8785.dumps(normalized)
    except (TypeError, ValueError, rfc8785.CanonicalizationError) as error:
        raise CanonicalizationError("value is not valid JCS-SHA256-V1 input") from error
    digest = sha256(canonical).hexdigest()
    return CanonicalDocument(canonical, f"{HASH_PREFIX}{digest}")


def verify_canonical_document(document: CanonicalDocument) -> bool:
    """Verify both canonical byte representation and the bound versioned digest."""
    try:
        rebuilt = canonical_document_from_bytes(document.canonical_json)
    except CanonicalizationError:
        return False
    return hmac.compare_digest(
        rebuilt.canonical_json, document.canonical_json
    ) and hmac.compare_digest(rebuilt.snapshot_hash, document.snapshot_hash)


def canonical_document_from_bytes(payload: bytes) -> CanonicalDocument:
    """Reparse stored JSON and require that it was already in canonical byte form."""
    import json

    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CanonicalizationError("stored snapshot is not JSON") from error
    rebuilt = canonical_document(parsed, exclude_volatile=False)
    if not hmac.compare_digest(rebuilt.canonical_json, payload):
        raise CanonicalizationError("stored snapshot bytes are not canonical")
    return rebuilt


def _json_value(value: object, *, exclude_volatile: bool) -> JsonValue:
    if isinstance(value, CanonicalDocument):
        import json

        parsed: object = json.loads(value.canonical_json)
        return _json_value(parsed, exclude_volatile=False)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value, exclude_volatile=exclude_volatile)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalizationError("snapshot timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name), exclude_volatile=exclude_volatile)
            for field in fields(value)
            if not (exclude_volatile and field.name in VOLATILE_HASH_FIELDS)
        }
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("snapshot object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if exclude_volatile and normalized_key in VOLATILE_HASH_FIELDS:
                continue
            if normalized_key in normalized:
                raise CanonicalizationError("normalized snapshot keys collide")
            normalized[normalized_key] = _json_value(item, exclude_volatile=exclude_volatile)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_json_value(item, exclude_volatile=exclude_volatile) for item in value]
    raise CanonicalizationError(f"unsupported snapshot value type: {type(value).__name__}")
