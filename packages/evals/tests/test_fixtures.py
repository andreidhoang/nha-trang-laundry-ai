from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from nha_trang_laundry_evals import FixtureBundleError, load_synthetic_fixture


def payload() -> dict[str, object]:
    return {
        "fixture_id": "fixture:synthetic_catalog:v1",
        "version": 1,
        "clock": "2026-07-28T00:00:00Z",
        "authenticated_context": {"channel": "INTERNAL_TEST"},
        "database_seed": {},
        "provider_events": [],
        "fault_injection": {},
    }


def write_fixture(root: Path, value: Any) -> str:
    path = root / "fixtures" / "synthetic_catalog" / "v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return f"sha256:{sha256(rfc8785.dumps(value)).hexdigest()}"


def test_loader_verifies_identity_required_fields_and_jcs_hash(tmp_path: Path) -> None:
    value = payload()
    digest = write_fixture(tmp_path, value)

    loaded = load_synthetic_fixture(
        tmp_path,
        fixture_id="fixture:synthetic_catalog:v1",
        version=1,
        payload_path="fixtures/synthetic_catalog/v1.json",
        payload_sha256=digest,
    )

    assert loaded.sha256 == digest
    assert loaded.payload["authenticated_context"] == {"channel": "INTERNAL_TEST"}


def test_loader_rejects_raw_pii_or_tampered_hash(tmp_path: Path) -> None:
    value = payload()
    value["provider_events"] = [{"phone": "+84999999999"}]
    digest = write_fixture(tmp_path, value)

    with pytest.raises(FixtureBundleError, match="prohibited field"):
        load_synthetic_fixture(
            tmp_path,
            fixture_id="fixture:synthetic_catalog:v1",
            version=1,
            payload_path="fixtures/synthetic_catalog/v1.json",
            payload_sha256=digest,
        )
    value = payload()
    write_fixture(tmp_path, value)
    with pytest.raises(FixtureBundleError, match="hash"):
        load_synthetic_fixture(
            tmp_path,
            fixture_id="fixture:synthetic_catalog:v1",
            version=1,
            payload_path="fixtures/synthetic_catalog/v1.json",
            payload_sha256=f"sha256:{'0' * 64}",
        )
