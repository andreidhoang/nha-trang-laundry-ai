from __future__ import annotations

import copy
import io
import json
import tarfile
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import build_openclaw_repackage as builder
from scripts import verify_openclaw_oci_attestations as oci_verifier
from scripts import verify_openclaw_repackage as verifier

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v1.json"
ARTIFACT_PATH = ROOT / "runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r1.tgz"


def _manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))


def test_repackage_manifest_and_committed_artifact_are_digest_bound() -> None:
    manifest = verifier._load_manifest()
    artifact = ARTIFACT_PATH.read_bytes()

    assert manifest["artifact_status"] == "EVAL_ONLY"
    assert all(value is False for value in manifest["activation"].values())
    assert verifier._sha256(artifact) == manifest["output"]["sha256"]
    assert verifier._integrity(artifact) == manifest["output"]["integrity"]
    assert len(artifact) == manifest["output"]["size"]


def test_manifest_schema_rejects_authority_or_unpinned_material(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["activation"]["provider_calls_enabled"] = True
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="violates schema"):
        verifier._load_manifest(unsafe)

    manifest = _manifest()
    del manifest["replacements"][0]["replacement"]["sha256"]
    unpinned = tmp_path / "unpinned.json"
    unpinned.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="violates schema"):
        verifier._load_manifest(unpinned)


def test_builder_rejects_source_drift_and_unsupported_ranges(tmp_path: Path) -> None:
    manifest = _manifest()
    package_root = tmp_path / "package"
    package_root.mkdir()
    shrinkwrap = {
        "packages": {
            "node_modules/brace-expansion": {
                "version": "5.0.6",
                "resolved": manifest["replacements"][0]["source"]["url"],
                "integrity": manifest["replacements"][0]["source"]["integrity"],
            },
            "node_modules/minimatch": {"dependencies": {"brace-expansion": "^5.0.5"}},
            "node_modules/fast-uri": {
                "version": "3.1.2",
                "resolved": manifest["replacements"][1]["source"]["url"],
                "integrity": manifest["replacements"][1]["source"]["integrity"],
            },
            "node_modules/ajv": {"dependencies": {"fast-uri": "^3.0.1"}},
        }
    }
    (package_root / "npm-shrinkwrap.json").write_text(json.dumps(shrinkwrap), encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected source version"):
        builder._patch_shrinkwrap(package_root, manifest)
    with pytest.raises(ValueError, match="unsupported dependency range"):
        builder._satisfies_caret("5.0.8", ">=5.0.5")


def test_plugin_lock_uses_only_the_repacked_fixed_tree() -> None:
    manifest = verifier._load_manifest()
    verifier._verify_plugin_binding(manifest)
    lock = json.loads(
        (ROOT / "runtime/openclaw/public-cell/plugin/package-lock.json").read_text(encoding="utf-8")
    )
    packages = lock["packages"]
    assert packages["node_modules/openclaw/node_modules/brace-expansion"]["version"] == "5.0.8"
    assert packages["node_modules/openclaw/node_modules/fast-uri"]["version"] == "3.1.4"


def _json_blob(value: object) -> tuple[str, bytes]:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return f"sha256:{sha256(raw).hexdigest()}", raw


def _oci_archive(path: Path, *, predicate_type: str = oci_verifier.PROVENANCE) -> None:
    config_digest, config = _json_blob({"architecture": "amd64", "os": "linux"})
    image_digest, image = _json_blob(
        {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [],
        }
    )
    provenance_digest, provenance = _json_blob(
        {
            "_type": oci_verifier.STATEMENT,
            "predicateType": predicate_type,
            "subject": [],
            "predicate": {
                "buildDefinition": {
                    "buildType": oci_verifier.BUILDKIT_BUILD_TYPE,
                    "resolvedDependencies": [{"uri": "pkg:npm/openclaw@2026.7.1-2"}],
                },
                "runDetails": {"builder": {"id": ""}},
            },
        }
    )
    attestation_digest, attestation = _json_blob(
        {
            "schemaVersion": 2,
            "config": {
                "mediaType": "application/vnd.unknown.config.v1+json",
                "digest": config_digest,
            },
            "layers": [
                {
                    "mediaType": "application/vnd.in-toto+json",
                    "digest": provenance_digest,
                    "size": len(provenance),
                    "annotations": {"in-toto.io/predicate-type": predicate_type},
                }
            ],
        }
    )
    platform_index = {
        "schemaVersion": 2,
        "mediaType": oci_verifier.OCI_INDEX,
        "manifests": [
            {
                "mediaType": oci_verifier.OCI_MANIFEST,
                "digest": image_digest,
                "size": len(image),
                "platform": {"architecture": "amd64", "os": "linux"},
            },
            {
                "mediaType": oci_verifier.OCI_MANIFEST,
                "digest": attestation_digest,
                "size": len(attestation),
                "platform": {"architecture": "unknown", "os": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": image_digest,
                },
            },
        ],
    }
    platform_index_digest, platform_index_blob = _json_blob(platform_index)
    index = {
        "schemaVersion": 2,
        "mediaType": oci_verifier.OCI_INDEX,
        "manifests": [
            {
                "mediaType": oci_verifier.OCI_INDEX,
                "digest": platform_index_digest,
                "size": len(platform_index_blob),
            }
        ],
    }
    members = {
        "index.json": json.dumps(index).encode(),
        oci_verifier._blob_name(config_digest): config,
        oci_verifier._blob_name(image_digest): image,
        oci_verifier._blob_name(provenance_digest): provenance,
        oci_verifier._blob_name(attestation_digest): attestation,
        oci_verifier._blob_name(platform_index_digest): platform_index_blob,
    }
    with tarfile.open(path, mode="w") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_oci_provenance_is_bound_to_the_linux_image(tmp_path: Path) -> None:
    archive = tmp_path / "openclaw.oci.tar"
    _oci_archive(archive)
    result = oci_verifier.verify(archive)
    assert result["predicate_type"] == "https://slsa.dev/provenance/v1"
    assert result["image_digest"].startswith("sha256:")
    assert result["platform_manifest_digest"].startswith("sha256:")
    assert result["config_digest"].startswith("sha256:")
    assert result["image_digest"] != result["platform_manifest_digest"]

    invalid = tmp_path / "invalid.oci.tar"
    _oci_archive(invalid, predicate_type="https://example.invalid/provenance")
    with pytest.raises(ValueError, match="no unique SLSA provenance"):
        oci_verifier.verify(invalid)


def test_image_workflow_requires_hosted_provenance_and_no_dev_audit_omission() -> None:
    manifest = verifier._load_manifest()
    verifier._verify_image_and_workflow(manifest)
    workflow = (ROOT / ".github/workflows/release-supply-chain.yml").read_text(encoding="utf-8")
    assert "--provenance=mode=max" in workflow
    assert "--input /work/openclaw-oci" in workflow
    assert "--input /work/openclaw.oci.tar" not in workflow
    assert "--omit=dev" not in workflow
    assert "artifact attestations: write" not in workflow
    assert "id-token: write" not in workflow
    dockerfile = (ROOT / "runtime/openclaw/repack/Dockerfile").read_text(encoding="utf-8")
    assert "libcrypto3=3.5.7-r0" in dockerfile
    assert "libssl3=3.5.7-r0" in dockerfile
    assert "/usr/local/lib/node_modules/npm" in dockerfile


def test_replacement_set_cannot_expand_silently(tmp_path: Path) -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["replacements"].append(copy.deepcopy(manifest["replacements"][0]))
    expanded = tmp_path / "expanded.json"
    expanded.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="violates schema"):
        verifier._load_manifest(expanded)
