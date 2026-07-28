"""Strict synthetic fixture bundles for reproducible agent evaluation paths."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import rfc8785

REQUIRED_FIELDS = frozenset(
    {
        "fixture_id",
        "version",
        "clock",
        "authenticated_context",
        "database_seed",
        "provider_events",
        "fault_injection",
    }
)
PROHIBITED_FIELDS = frozenset(
    {
        "api_key",
        "chain_of_thought",
        "customer_name",
        "email",
        "phone",
        "raw_pii",
        "raw_provider_response",
        "secret",
    }
)


class FixtureBundleError(ValueError):
    """A fixture is malformed, non-canonical, unsafe, or does not match its registry pin."""


@dataclass(frozen=True, slots=True)
class SyntheticFixtureBundle:
    fixture_id: str
    version: int
    payload: Mapping[str, Any]
    sha256: str


def load_synthetic_fixture(
    root: Path, *, fixture_id: str, version: int, payload_path: str, payload_sha256: str
) -> SyntheticFixtureBundle:
    """Load one pinned JSON fixture without allowing repository escape or raw PII fields."""

    root = root.resolve()
    path = (root / payload_path).resolve()
    if path != root and root not in path.parents:
        raise FixtureBundleError("fixture payload path escapes repository")
    if path.suffix != ".json" or not path.is_file():
        raise FixtureBundleError("fixture payload must be an existing JSON file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FixtureBundleError("fixture payload is not valid JSON") from error
    if not isinstance(raw, dict):
        raise FixtureBundleError("fixture payload must be a JSON object")
    _validate_payload(raw, fixture_id, version)
    actual = f"sha256:{sha256(rfc8785.dumps(raw)).hexdigest()}"
    if actual != payload_sha256:
        raise FixtureBundleError("fixture payload hash does not match registry pin")
    return SyntheticFixtureBundle(fixture_id, version, dict(raw), actual)


def _validate_payload(payload: Mapping[str, Any], fixture_id: str, version: int) -> None:
    missing = REQUIRED_FIELDS.difference(payload)
    if missing:
        raise FixtureBundleError(f"fixture payload is missing required fields: {sorted(missing)}")
    if payload.get("fixture_id") != fixture_id or payload.get("version") != version:
        raise FixtureBundleError("fixture identity does not match registry")
    if not isinstance(payload["clock"], str):
        raise FixtureBundleError("fixture clock must be an ISO timestamp string")
    for name in ("authenticated_context", "database_seed", "fault_injection"):
        if not isinstance(payload[name], dict):
            raise FixtureBundleError(f"fixture {name} must be an object")
    if not isinstance(payload["provider_events"], list):
        raise FixtureBundleError("fixture provider_events must be an array")
    _reject_prohibited_fields(payload)


def _reject_prohibited_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise FixtureBundleError("fixture object keys must be strings")
            if key.casefold() in PROHIBITED_FIELDS:
                raise FixtureBundleError(f"fixture contains prohibited field: {key}")
            _reject_prohibited_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_prohibited_fields(child)
