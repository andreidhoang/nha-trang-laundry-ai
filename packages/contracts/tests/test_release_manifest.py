from __future__ import annotations

import base64
import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePrivateKey,
    generate_private_key,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from nha_trang_laundry_contracts import (
    ReleaseCapability,
    ReleaseManifestError,
    ReleaseSignatureAlgorithm,
    ReleaseSignerFunction,
    RepositoryArtifactResolver,
    TrustedReleaseSigner,
    load_trusted_release_signers,
    verify_release_manifest,
)

ROOT = Path(__file__).resolve().parents[3]
COMMIT_SHA = "a" * 40
NOW = datetime(2026, 7, 2, 5, tzinfo=UTC)
ARTIFACT_NAMES = (
    "canonical_enums",
    "agent_openapi",
    "eval_manifest",
    "eval_results",
    "policy_bundle",
    "prompt_bundle",
    "model_config",
    "business_config",
    "migration_manifest",
    "customer_corpus",
)


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


PrivateReleaseKey = Ed25519PrivateKey | EllipticCurvePrivateKey


def _fixture(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, TrustedReleaseSigner], Path, dict[str, PrivateReleaseKey]]:
    repository = tmp_path / "repository"
    evidence = repository / "evidence"
    evidence.mkdir(parents=True)
    artifacts: dict[str, dict[str, str]] = {}
    for name in ARTIFACT_NAMES:
        content = f"immutable-{name}".encode()
        (evidence / f"{name}.bin").write_bytes(content)
        artifacts[name] = {
            "uri": f"evidence/{name}.bin",
            "version": "v1",
            "sha256": f"sha256:{sha256(content).hexdigest()}",
        }
    gate_content = b"immutable-gate-evidence"
    (evidence / "gate.json").write_bytes(gate_content)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_id": "release-shadow-001",
        "commit_sha": COMMIT_SHA,
        "stage": "SHADOW",
        "capability": "INTERNAL_SHADOW",
        "artifacts": artifacts,
        "gate_evidence": [
            {
                "gate_id": "G1_INTERNAL_SHADOW_READY",
                "passed": True,
                "evidence_refs": [
                    {
                        "evidence_type": "TEST_RESULT",
                        "uri": "evidence/gate.json",
                        "sha256": f"sha256:{sha256(gate_content).hexdigest()}",
                    }
                ],
                "checked_at": "2026-07-02T01:00:00Z",
            }
        ],
        "eligible_case_definition": {
            "definition_version": "v1",
            "query_or_rule_hash": f"sha256:{'1' * 64}",
            "denominator_name": "all frozen shadow cases",
            "eligible_case_count": 100,
            "excluded_case_count": 0,
        },
        "observation_window": {
            "start_at": "2026-07-01T00:00:00Z",
            "end_at": "2026-07-02T00:00:00Z",
            "consecutive_days": 1,
        },
        "operational_evidence": {
            "shadow_consecutive_days": 1,
            "representative_interactions": 100,
            "completed_real_orders": 0,
            "production_load_logs": 0,
            "delivery_cost_logs": 0,
            "automated_sends_human_reviewed": 0,
        },
        "zero_tolerance_results": {
            "wrong_monetary_value_count": 0,
            "unauthorized_side_effect_count": 0,
            "cross_contact_disclosure_count": 0,
            "suppression_miss_count": 0,
            "critical_safety_error_count": 0,
            "fallback_paths_included": True,
        },
        "signoffs": [],
        "activation": {
            "percentage": 1,
            "maximum_daily_cases": 10,
            "feature_flag": "agent.internal_shadow",
            "starts_at": "2026-07-02T04:00:00Z",
        },
        "rollback": {
            "tested": True,
            "target_release_id": "manual-truth-v1",
            "kill_switch": "agent.internal_shadow.enabled=false",
            "runbook_ref": "evidence/runbook-v1.md",
        },
        "created_at": "2026-07-02T03:00:00Z",
        "expires_at": "2026-07-03T00:00:00Z",
    }
    owner = Ed25519PrivateKey.generate()
    security = generate_private_key(SECP256R1())
    operations = Ed25519PrivateKey.generate()
    private_keys: dict[str, PrivateReleaseKey] = {
        "owner-key": owner,
        "security-key": security,
        "operations-key": operations,
    }
    trusted = {
        "owner-key": TrustedReleaseSigner(
            "owner-key",
            ReleaseSignerFunction.OWNER,
            "owner-actor",
            ReleaseSignatureAlgorithm.ED25519,
            owner.public_key(),
        ),
        "security-key": TrustedReleaseSigner(
            "security-key",
            ReleaseSignerFunction.SECURITY,
            "security-actor",
            ReleaseSignatureAlgorithm.ECDSA_P256_SHA256,
            security.public_key(),
        ),
        "operations-key": TrustedReleaseSigner(
            "operations-key",
            ReleaseSignerFunction.OPERATIONS,
            "operations-actor",
            ReleaseSignatureAlgorithm.ED25519,
            operations.public_key(),
        ),
    }
    _resign(manifest, private_keys)
    return manifest, trusted, repository, private_keys


def _resign(manifest: dict[str, Any], private_keys: dict[str, PrivateReleaseKey]) -> None:
    payload = dict(manifest)
    payload.pop("signoffs", None)
    payload_hash = f"sha256:{sha256(rfc8785.dumps(payload)).hexdigest()}"
    signoffs: list[dict[str, str]] = []
    definitions = (
        ("OWNER", "owner-actor", "ED25519", "owner-key"),
        ("SECURITY", "security-actor", "ECDSA_P256_SHA256", "security-key"),
        ("OPERATIONS", "operations-actor", "ED25519", "operations-key"),
    )
    for function, actor_id, algorithm, key_id in definitions:
        key = private_keys[key_id]
        if algorithm == "ED25519":
            assert isinstance(key, Ed25519PrivateKey)
            signature = key.sign(payload_hash.encode("ascii"))
        else:
            assert isinstance(key, EllipticCurvePrivateKey)
            signature = key.sign(payload_hash.encode("ascii"), ECDSA(SHA256()))
        signoffs.append(
            {
                "function": function,
                "actor_id": actor_id,
                "decision": "APPROVE",
                "signed_at": "2026-07-02T02:00:00Z",
                "signature_algorithm": algorithm,
                "key_id": key_id,
                "signed_payload_hash": payload_hash,
                "signature": _encoded(signature),
            }
        )
    manifest["signoffs"] = signoffs


def _write_signer_registry(
    path: Path, private_keys: dict[str, PrivateReleaseKey]
) -> tuple[Path, str, dict[str, Any]]:
    definitions = (
        ("owner-key", "OWNER", "owner-actor", "ED25519"),
        ("security-key", "SECURITY", "security-actor", "ECDSA_P256_SHA256"),
        ("operations-key", "OPERATIONS", "operations-actor", "ED25519"),
    )
    registry: dict[str, Any] = {
        "schema_version": 1,
        "registry_id": "release-trust-test-v1",
        "signers": [
            {
                "key_id": key_id,
                "function": function,
                "actor_id": actor_id,
                "algorithm": algorithm,
                "public_key_pem": private_keys[key_id]
                .public_key()
                .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
                .decode("ascii"),
            }
            for key_id, function, actor_id, algorithm in definitions
        ],
    }
    content = json.dumps(registry, indent=2).encode() + b"\n"
    path.write_bytes(content)
    return path, f"sha256:{sha256(content).hexdigest()}", registry


def _add_supply_chain_evidence(
    manifest: dict[str, Any], repository: Path, private_keys: dict[str, PrivateReleaseKey]
) -> Path:
    evidence = repository / "evidence"
    uv_content = b"locked-python\n"
    node_content = b'{"lockfileVersion":3}\n'
    (repository / "uv.lock").write_bytes(uv_content)
    (evidence / "package-lock.json").write_bytes(node_content)
    sbom_content = b'{"spdxVersion":"SPDX-2.3"}\n'
    (evidence / "api.spdx.json").write_bytes(sbom_content)
    image_ref = f"registry.example/api@sha256:{'b' * 64}"
    scan = {
        "schema_version": 1,
        "evidence_id": "SCAN:RELEASE:01",
        "image_ref": image_ref,
        "scanner": "docker-scout",
        "scanner_version": "1.23.1",
        "scanned_at": "2026-07-02T02:30:00Z",
        "status": "PASSED",
        "vulnerabilities": {"critical": 0, "high": 0},
        "sbom": {
            "format": "SPDX_JSON",
            "uri": "evidence/api.spdx.json",
            "sha256": f"sha256:{sha256(sbom_content).hexdigest()}",
        },
    }
    scan_content = (json.dumps(scan, indent=2) + "\n").encode()
    (evidence / "api-scan.json").write_bytes(scan_content)
    report_content = b"[]\n"
    for report_name in ("gitleaks", "pip-audit", "npm-audit", "licenses"):
        (evidence / f"{report_name}.json").write_bytes(report_content)

    def report(name: str) -> dict[str, str]:
        return {
            "uri": f"evidence/{name}.json",
            "sha256": f"sha256:{sha256(report_content).hexdigest()}",
        }

    lockfiles = [
        {"uri": "uv.lock", "sha256": f"sha256:{sha256(uv_content).hexdigest()}"},
        {
            "uri": "evidence/package-lock.json",
            "sha256": f"sha256:{sha256(node_content).hexdigest()}",
        },
    ]
    scanner = {"scanner": "test", "scanner_version": "1", "status": "PASSED"}
    bundle = {
        "schema_version": 1,
        "evidence_id": "SUPPLY:RELEASE:01",
        "release_commit_sha": COMMIT_SHA,
        "generated_at": "2026-07-02T03:00:00Z",
        "expires_at": "2026-07-03T03:00:00Z",
        "status": "PASSED",
        "secret_scan": {
            **scanner,
            "scanned_commit_sha": COMMIT_SHA,
            "findings": 0,
            "report": report("gitleaks"),
        },
        "dependency_audit": {
            **scanner,
            "lockfiles": lockfiles,
            "reports": [report("pip-audit"), report("npm-audit")],
            "vulnerabilities": {"critical": 0, "high": 0},
        },
        "license_audit": {
            **scanner,
            "lockfiles": lockfiles,
            "forbidden_or_unknown_count": 0,
            "report": report("licenses"),
        },
        "images": [
            {
                "image_ref": image_ref,
                "scan_evidence": {
                    "uri": "evidence/api-scan.json",
                    "sha256": f"sha256:{sha256(scan_content).hexdigest()}",
                },
            }
        ],
        "waivers": [],
    }
    path = evidence / "supply-chain.json"
    content = (json.dumps(bundle, indent=2) + "\n").encode()
    path.write_bytes(content)
    manifest["gate_evidence"][0]["evidence_refs"].append(
        {
            "evidence_type": "CONFIG_SNAPSHOT",
            "uri": "evidence/supply-chain.json",
            "sha256": f"sha256:{sha256(content).hexdigest()}",
        }
    )
    _resign(manifest, private_keys)
    return path


def _verify(
    manifest: dict[str, Any], trusted: dict[str, TrustedReleaseSigner], repository: Path
) -> None:
    verify_release_manifest(
        root=ROOT,
        manifest=manifest,
        trusted_signers=trusted,
        expected_commit_sha=COMMIT_SHA,
        expected_stage="SHADOW",
        expected_capability=ReleaseCapability.INTERNAL_SHADOW,
        now=NOW,
        artifact_resolver=RepositoryArtifactResolver(repository),
    )


def test_release_manifest_verifies_complete_envelope_and_artifacts(tmp_path: Path) -> None:
    manifest, trusted, repository, _ = _fixture(tmp_path)

    authorization = verify_release_manifest(
        root=ROOT,
        manifest=manifest,
        trusted_signers=trusted,
        expected_commit_sha=COMMIT_SHA,
        expected_stage="SHADOW",
        expected_capability=ReleaseCapability.INTERNAL_SHADOW,
        now=NOW,
        artifact_resolver=RepositoryArtifactResolver(repository),
    )

    assert authorization.release_id == "release-shadow-001"
    assert authorization.capability is ReleaseCapability.INTERNAL_SHADOW
    assert len(authorization.verified_uris) == 11
    assert authorization.authorizes(
        commit_sha=COMMIT_SHA,
        stage="SHADOW",
        capability=ReleaseCapability.INTERNAL_SHADOW,
        now=NOW,
    )
    assert not authorization.authorizes(
        commit_sha="b" * 40,
        stage="SHADOW",
        capability=ReleaseCapability.INTERNAL_SHADOW,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("expires_at", "2026-07-02T04:30:00Z", "expired"),
        ("created_at", "2026-07-02T06:00:00Z", "timestamps are not chronological"),
        ("commit_sha", "b" * 40, "commit does not match"),
    ],
)
def test_release_manifest_rejects_stale_or_wrong_envelope(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    manifest, trusted, repository, _ = _fixture(tmp_path)
    manifest[field] = value

    with pytest.raises(ReleaseManifestError, match=message):
        _verify(manifest, trusted, repository)


def test_release_manifest_rejects_tampering_and_reused_identity(tmp_path: Path) -> None:
    manifest, trusted, repository, _ = _fixture(tmp_path)
    tampered = deepcopy(manifest)
    tampered["activation"]["maximum_daily_cases"] = 11
    with pytest.raises(ReleaseManifestError, match="payload hash"):
        _verify(tampered, trusted, repository)

    reused = deepcopy(manifest)
    reused["signoffs"][1]["actor_id"] = "owner-actor"
    with pytest.raises(ReleaseManifestError, match="distinct actors"):
        _verify(reused, trusted, repository)

    bad_signature = deepcopy(manifest)
    bad_signature["signoffs"][0]["signature"] = _encoded(b"x" * 64)
    with pytest.raises(ReleaseManifestError, match="signature is invalid"):
        _verify(bad_signature, trusted, repository)


def test_release_manifest_rejects_expected_capability_mismatch(tmp_path: Path) -> None:
    manifest, trusted, repository, _ = _fixture(tmp_path)

    with pytest.raises(ReleaseManifestError, match="capability does not match"):
        verify_release_manifest(
            root=ROOT,
            manifest=manifest,
            trusted_signers=trusted,
            expected_commit_sha=COMMIT_SHA,
            expected_stage="SHADOW",
            expected_capability=ReleaseCapability.PUBLIC_FAQ,
            now=NOW,
            artifact_resolver=RepositoryArtifactResolver(repository),
        )


def test_release_manifest_rejects_bad_artifacts_and_evidence_chronology(tmp_path: Path) -> None:
    manifest, trusted, repository, _ = _fixture(tmp_path)
    (repository / "evidence" / "gate.json").write_bytes(b"tampered")
    with pytest.raises(ReleaseManifestError, match="artifact hash mismatch"):
        _verify(manifest, trusted, repository)

    manifest, trusted, repository, _ = _fixture(tmp_path / "again")
    manifest["gate_evidence"][0]["checked_at"] = "2026-07-01T23:00:00Z"
    with pytest.raises(ReleaseManifestError, match="outside the closed evidence window"):
        _verify(manifest, trusted, repository)


def test_release_manifest_default_resolver_rejects_path_escape(tmp_path: Path) -> None:
    manifest, trusted, repository, private_keys = _fixture(tmp_path)
    manifest["artifacts"]["customer_corpus"]["uri"] = "../outside.bin"
    _resign(manifest, private_keys)

    with pytest.raises(ReleaseManifestError, match="escapes root"):
        _verify(manifest, trusted, repository)


def test_trusted_signer_registry_is_hash_pinned_and_public_key_only(tmp_path: Path) -> None:
    _, _, _, private_keys = _fixture(tmp_path)
    path, digest, registry = _write_signer_registry(tmp_path / "trusted-signers.json", private_keys)

    loaded = load_trusted_release_signers(root=ROOT, path=path, expected_sha256=digest)
    assert set(loaded) == {"owner-key", "security-key", "operations-key"}
    with pytest.raises(ReleaseManifestError, match="hash mismatch"):
        load_trusted_release_signers(root=ROOT, path=path, expected_sha256=f"sha256:{'0' * 64}")

    registry["signers"][1]["actor_id"] = "owner-actor"
    content = json.dumps(registry, indent=2).encode() + b"\n"
    path.write_bytes(content)
    with pytest.raises(ReleaseManifestError, match="distinct keys, actors, and functions"):
        load_trusted_release_signers(
            root=ROOT,
            path=path,
            expected_sha256=f"sha256:{sha256(content).hexdigest()}",
        )


def test_trusted_signer_registry_rejects_private_or_wrong_algorithm_key(tmp_path: Path) -> None:
    _, _, _, private_keys = _fixture(tmp_path)
    path, _, registry = _write_signer_registry(tmp_path / "trusted-signers.json", private_keys)
    owner = private_keys["owner-key"]
    assert isinstance(owner, Ed25519PrivateKey)
    registry["signers"][0]["public_key_pem"] = owner.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode("ascii")
    content = json.dumps(registry, indent=2).encode() + b"\n"
    path.write_bytes(content)
    with pytest.raises(ReleaseManifestError, match="violates schema"):
        load_trusted_release_signers(
            root=ROOT,
            path=path,
            expected_sha256=f"sha256:{sha256(content).hexdigest()}",
        )

    path, _, registry = _write_signer_registry(path, private_keys)
    registry["signers"][0]["algorithm"] = "ECDSA_P256_SHA256"
    content = json.dumps(registry, indent=2).encode() + b"\n"
    path.write_bytes(content)
    with pytest.raises(ReleaseManifestError, match="requires a P-256 public key"):
        load_trusted_release_signers(
            root=ROOT,
            path=path,
            expected_sha256=f"sha256:{sha256(content).hexdigest()}",
        )


def test_release_candidate_cli_verifies_sanitized_envelope(tmp_path: Path) -> None:
    manifest, _, repository, private_keys = _fixture(tmp_path)
    supply_chain_path = _add_supply_chain_evidence(manifest, repository, private_keys)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    signer_path, signer_hash, _ = _write_signer_registry(
        tmp_path / "trusted-signers.json", private_keys
    )

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_release_candidate.py"),
            "--manifest",
            str(manifest_path),
            "--trusted-signers",
            str(signer_path),
            "--trusted-signers-sha256",
            signer_hash,
            "--expected-commit-sha",
            COMMIT_SHA,
            "--stage",
            "SHADOW",
            "--capability",
            "INTERNAL_SHADOW",
            "--artifact-root",
            str(repository),
            "--supply-chain-evidence",
            str(supply_chain_path),
            "--at",
            "2026-07-02T05:00:00Z",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output == {
        "status": "VERIFIED_RELEASE_AUTHORIZATION",
        "release_id": "release-shadow-001",
        "commit_sha": COMMIT_SHA,
        "stage": "SHADOW",
        "capability": "INTERNAL_SHADOW",
        "starts_at": "2026-07-02T04:00:00Z",
        "expires_at": "2026-07-03T00:00:00Z",
        "payload_hash": output["payload_hash"],
        "verified_artifact_count": 12,
        "supply_chain_evidence_id": "SUPPLY:RELEASE:01",
        "verified_image_count": 1,
    }
    assert output["payload_hash"].startswith("sha256:")

    unsigned = deepcopy(manifest)
    unsigned["gate_evidence"][0]["evidence_refs"] = [
        reference
        for reference in unsigned["gate_evidence"][0]["evidence_refs"]
        if reference["uri"] != "evidence/supply-chain.json"
    ]
    _resign(unsigned, private_keys)
    unsigned_path = tmp_path / "release-manifest-without-supply-chain.json"
    unsigned_path.write_text(json.dumps(unsigned, indent=2) + "\n", encoding="utf-8")
    unsigned_command = list(result.args)
    manifest_index = unsigned_command.index("--manifest") + 1
    unsigned_command[manifest_index] = str(unsigned_path)
    rejected = subprocess.run(
        unsigned_command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected.returncode == 1
    assert "not hash-bound by the signed release manifest" in rejected.stderr
