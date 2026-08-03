from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml
from nha_trang_laundry_contracts import (
    SupplyChainEvidenceError,
    verify_supply_chain_evidence,
)

from scripts.normalize_container_scan import main as normalize_container_scan

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "a" * 40
IMAGE = f"registry.example/nha-trang-laundry-api@sha256:{'b' * 64}"
NOW = datetime(2026, 7, 31, 3, tzinfo=UTC)


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(value, indent=2) + "\n").encode()
    path.write_bytes(content)
    return f"sha256:{sha256(content).hexdigest()}"


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    lockfiles: list[dict[str, str]] = []
    for relative, content in (
        ("uv.lock", b"python-lock\n"),
        ("runtime/plugin/package-lock.json", b'{"lockfileVersion":3}\n'),
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        lockfiles.append({"uri": relative, "sha256": f"sha256:{sha256(content).hexdigest()}"})

    sbom_path = repository / "artifacts/api.spdx.json"
    sbom_hash = _write_json(sbom_path, {"spdxVersion": "SPDX-2.3"})
    scan = {
        "schema_version": 1,
        "evidence_id": "SCAN:API:0001",
        "image_ref": IMAGE,
        "scanner": "docker-scout",
        "scanner_version": "1.23.1",
        "scanned_at": "2026-07-31T01:30:00Z",
        "status": "PASSED",
        "vulnerabilities": {"critical": 0, "high": 0},
        "sbom": {
            "format": "SPDX_JSON",
            "uri": "artifacts/api.spdx.json",
            "sha256": sbom_hash,
        },
    }
    scan_path = repository / "artifacts/api-scan.json"
    scan_hash = _write_json(scan_path, scan)
    report_artifacts = {}
    for name in ("gitleaks", "pip-audit", "npm-audit", "licenses"):
        uri = f"artifacts/{name}.json"
        report_artifacts[name] = {"uri": uri, "sha256": _write_json(repository / uri, [])}
    scanner = {"scanner": "local-test-scanner", "scanner_version": "1.0", "status": "PASSED"}
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "evidence_id": "SUPPLY:TEST:0001",
        "release_commit_sha": COMMIT,
        "generated_at": "2026-07-31T02:00:00Z",
        "expires_at": "2026-08-01T02:00:00Z",
        "status": "PASSED",
        "secret_scan": {
            **scanner,
            "scanned_commit_sha": COMMIT,
            "findings": 0,
            "report": report_artifacts["gitleaks"],
        },
        "dependency_audit": {
            **scanner,
            "lockfiles": lockfiles,
            "reports": [report_artifacts["pip-audit"], report_artifacts["npm-audit"]],
            "vulnerabilities": {"critical": 0, "high": 0},
        },
        "license_audit": {
            **scanner,
            "lockfiles": lockfiles,
            "forbidden_or_unknown_count": 0,
            "report": report_artifacts["licenses"],
        },
        "images": [
            {
                "image_ref": IMAGE,
                "scan_evidence": {
                    "uri": "artifacts/api-scan.json",
                    "sha256": scan_hash,
                },
            }
        ],
        "waivers": [],
    }
    bundle_path = repository / "artifacts/supply-chain.json"
    _write_json(bundle_path, bundle)
    return repository, bundle_path, bundle


def _verify(repository: Path, path: Path, *, commit: str = COMMIT, now: datetime = NOW) -> None:
    verify_supply_chain_evidence(
        artifact_root=repository,
        schema_root=ROOT,
        path=path,
        expected_commit_sha=commit,
        now=now,
    )


def test_valid_bundle_binds_commit_lockfiles_image_scan_and_sbom(tmp_path: Path) -> None:
    repository, path, _ = _fixture(tmp_path)
    verified = verify_supply_chain_evidence(
        artifact_root=repository,
        schema_root=ROOT,
        path=path,
        expected_commit_sha=COMMIT,
        now=NOW,
    )
    assert verified.release_commit_sha == COMMIT
    assert verified.image_refs == (IMAGE,)
    assert set(verified.verified_uris) == {
        "uv.lock",
        "runtime/plugin/package-lock.json",
        "artifacts/api-scan.json",
        "artifacts/api.spdx.json",
        "artifacts/gitleaks.json",
        "artifacts/pip-audit.json",
        "artifacts/npm-audit.json",
        "artifacts/licenses.json",
    }


@pytest.mark.parametrize("missing", ["artifacts/api-scan.json", "artifacts/api.spdx.json"])
def test_missing_scan_or_sbom_is_rejected(tmp_path: Path, missing: str) -> None:
    repository, path, _ = _fixture(tmp_path)
    (repository / missing).unlink()
    with pytest.raises(SupplyChainEvidenceError, match="missing"):
        _verify(repository, path)


def test_digest_or_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    repository, path, bundle = _fixture(tmp_path)
    bundle["images"][0]["image_ref"] = f"registry.example/other@sha256:{'c' * 64}"
    _write_json(path, bundle)
    with pytest.raises(SupplyChainEvidenceError, match="image digest"):
        _verify(repository, path)

    _, path, bundle = _fixture(tmp_path / "hash")
    bundle["images"][0]["scan_evidence"]["sha256"] = f"sha256:{'0' * 64}"
    _write_json(path, bundle)
    with pytest.raises(SupplyChainEvidenceError, match="hash mismatch"):
        _verify(path.parents[1], path)


def test_stale_or_wrong_commit_evidence_is_rejected(tmp_path: Path) -> None:
    repository, path, _ = _fixture(tmp_path)
    with pytest.raises(SupplyChainEvidenceError, match="expired"):
        _verify(repository, path, now=datetime(2026, 8, 2, tzinfo=UTC))
    with pytest.raises(SupplyChainEvidenceError, match="commit"):
        _verify(repository, path, commit="d" * 40)


def test_high_or_schema_invalid_result_is_rejected(tmp_path: Path) -> None:
    repository, path, bundle = _fixture(tmp_path)
    scan_path = repository / "artifacts/api-scan.json"
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    scan["vulnerabilities"]["high"] = 1
    scan_hash = _write_json(scan_path, scan)
    bundle["images"][0]["scan_evidence"]["sha256"] = scan_hash
    _write_json(path, bundle)
    with pytest.raises(SupplyChainEvidenceError, match="violates schema"):
        _verify(repository, path)


def test_waivers_are_human_owned_time_bounded_and_never_high_severity(tmp_path: Path) -> None:
    repository, path, bundle = _fixture(tmp_path)
    waiver = {
        "waiver_id": "WAIVER:0001",
        "finding_id": "CVE-test",
        "severity": "HIGH",
        "reason": "reviewed exception",
        "approved_by": "model:agent",
        "authority": "MODEL",
        "approved_at": "2026-07-31T01:00:00Z",
        "expires_at": "2026-08-01T00:00:00Z",
    }
    bundle["waivers"] = [waiver]
    _write_json(path, bundle)
    with pytest.raises(SupplyChainEvidenceError, match="violates schema"):
        _verify(repository, path)

    valid_looking = deepcopy(waiver)
    valid_looking.update(
        severity="LOW", approved_by="human:security-owner", authority="HUMAN_SECURITY_REVIEW"
    )
    valid_looking["expires_at"] = "2026-07-31T02:30:00Z"
    bundle["waivers"] = [valid_looking]
    _write_json(path, bundle)
    with pytest.raises(SupplyChainEvidenceError, match="expired"):
        _verify(repository, path)


def test_release_workflow_has_least_privilege_and_local_scanners() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-supply-chain.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["supply-chain"]
    assert job["permissions"] == {"contents": "read"}
    text = json.dumps(workflow)
    assert "artifact attestations: write" not in text
    assert "id-token: write" not in text
    assert "gitleaks" in text
    assert "pip-audit" in text
    assert (
        "aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
        in text
    )
    assert "docker scout" not in text
    assert "docker/scout-action" not in text
    assert "/var/run/docker.sock" not in text
    assert "docker save" in text
    assert "--scanner trivy" in text
    assert "--severity CRITICAL,HIGH --exit-code 1" in text


def test_normalizer_rejects_sbom_for_a_different_image_digest(tmp_path: Path) -> None:
    sarif = tmp_path / "scan.sarif"
    _write_json(sarif, {"runs": [{"results": []}]})
    sbom = tmp_path / "sbom.json"
    _write_json(
        sbom,
        {
            "spdxVersion": "SPDX-2.3",
            "packages": [
                {"externalRefs": [{"referenceLocator": f"pkg:oci/app@sha256:{'c' * 64}?tag=test"}]}
            ],
        },
    )
    with pytest.raises(ValueError, match="not bound"):
        normalize_container_scan(
            [
                "--sarif",
                str(sarif),
                "--sbom",
                str(sbom),
                "--sbom-uri",
                "artifacts/sbom.json",
                "--image-ref",
                IMAGE,
                "--scanner-version",
                "1",
                "--evidence-id",
                "SCAN:TEST:0001",
                "--output",
                str(tmp_path / "evidence.json"),
            ]
        )


def test_trivy_cyclonedx_and_sarif_bind_the_exact_scanned_image(tmp_path: Path) -> None:
    sarif = tmp_path / "scan.sarif"
    _write_json(
        sarif,
        {
            "runs": [
                {
                    "results": [],
                    "properties": {"imageID": "sha256:" + "b" * 64},
                }
            ]
        },
    )
    sbom = tmp_path / "sbom.cdx.json"
    _write_json(
        sbom,
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "metadata": {
                "component": {
                    "properties": [
                        {
                            "name": "aquasecurity:trivy:ImageID",
                            "value": "sha256:" + "b" * 64,
                        }
                    ]
                }
            },
        },
    )
    output = tmp_path / "evidence.json"

    assert (
        normalize_container_scan(
            [
                "--sarif",
                str(sarif),
                "--sbom",
                str(sbom),
                "--sbom-uri",
                "artifacts/sbom.cdx.json",
                "--image-ref",
                IMAGE,
                "--scanner",
                "trivy",
                "--scanner-version",
                "0.72.0",
                "--evidence-id",
                "SCAN:TEST:TRIVY",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["scanner"] == "trivy"
    assert evidence["sbom"]["format"] == "CYCLONEDX_JSON"

    changed = json.loads(sbom.read_text(encoding="utf-8"))
    changed["metadata"]["component"]["properties"][0]["value"] = "sha256:" + "c" * 64
    _write_json(sbom, changed)
    with pytest.raises(ValueError, match="SBOM is not bound"):
        normalize_container_scan(
            [
                "--sarif",
                str(sarif),
                "--sbom",
                str(sbom),
                "--sbom-uri",
                "artifacts/sbom.cdx.json",
                "--image-ref",
                IMAGE,
                "--scanner",
                "trivy",
                "--scanner-version",
                "0.72.0",
                "--evidence-id",
                "SCAN:TEST:TRIVY",
                "--output",
                str(output),
            ]
        )

    changed["metadata"]["component"]["properties"][0]["value"] = "sha256:" + "b" * 64
    _write_json(sbom, changed)
    changed_sarif = json.loads(sarif.read_text(encoding="utf-8"))
    changed_sarif["runs"][0]["properties"]["imageID"] = "sha256:" + "c" * 64
    _write_json(sarif, changed_sarif)
    with pytest.raises(ValueError, match="SARIF is not bound"):
        normalize_container_scan(
            [
                "--sarif",
                str(sarif),
                "--sbom",
                str(sbom),
                "--sbom-uri",
                "artifacts/sbom.cdx.json",
                "--image-ref",
                IMAGE,
                "--scanner",
                "trivy",
                "--scanner-version",
                "0.72.0",
                "--evidence-id",
                "SCAN:TEST:TRIVY",
                "--output",
                str(output),
            ]
        )
