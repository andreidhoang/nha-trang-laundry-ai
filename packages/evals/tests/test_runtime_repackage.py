from __future__ import annotations

import copy
import gzip
import io
import json
import tarfile
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import build_openclaw_repackage as builder
from scripts import verify_openclaw_oci_attestations as oci_verifier
from scripts import verify_openclaw_repackage as verifier

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v2.json"
ARTIFACT_PATH = ROOT / "runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r2.tgz"


def _manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))


def test_repackage_manifest_and_committed_artifact_are_digest_bound() -> None:
    manifest = verifier._load_manifest()
    artifact = ARTIFACT_PATH.read_bytes()

    assert manifest["artifact_origin"] == "DERIVED"
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
    entries = builder._read_archive(
        ROOT / "runtime/openclaw/repack/dist" / manifest["base"]["filename"]
    )
    shrinkwrap = json.loads(entries[builder.SHRINKWRAP][0])
    shrinkwrap["packages"]["node_modules/brace-expansion"]["version"] = "5.0.6"
    entries[builder.SHRINKWRAP] = (
        (json.dumps(shrinkwrap, indent=2) + "\n").encode(),
        entries[builder.SHRINKWRAP][1],
    )

    with pytest.raises(ValueError, match="unexpected source version"):
        builder._patch_entries(entries, manifest)
    with pytest.raises(ValueError, match="unsupported dependency range"):
        builder._satisfies_caret("5.0.8", ">=5.0.5")


def test_plugin_lock_uses_only_the_repacked_fixed_tree() -> None:
    manifest = verifier._load_manifest()
    verifier._verify_plugin_binding(manifest)
    lock = json.loads(
        (ROOT / "runtime/openclaw/public-cell/plugin/package-lock.json").read_text(encoding="utf-8")
    )
    packages = lock["packages"]
    assert packages["node_modules/openclaw/node_modules/brace-expansion"]["version"] == "5.0.9"
    assert packages["node_modules/openclaw/node_modules/fast-uri"]["version"] == "3.1.5"
    assert packages["node_modules/openclaw/node_modules/ip-address"]["version"] == "10.3.1"
    assert packages["node_modules/undici"]["version"] == "8.9.0"


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


def _write_archive(
    path: Path,
    members: list[tuple[str, bytes, bytes, int]],
    *,
    mtime: int = 0,
    uid: int = 0,
    gid: int = 0,
    gzip_filename: str = "",
) -> None:
    with (
        path.open("wb") as raw,
        gzip.GzipFile(
            filename=gzip_filename,
            mode="wb",
            compresslevel=9,
            fileobj=raw,
            mtime=mtime,
        ) as zipped,
        tarfile.open(fileobj=zipped, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        for name, data, member_type, mode in members:
            info = tarfile.TarInfo(name)
            info.type = member_type
            info.mode = mode
            info.mtime = mtime
            info.uid = uid
            info.gid = gid
            info.uname = "" if uid == 0 else "unsafe"
            info.gname = "" if gid == 0 else "unsafe"
            if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "package/package.json"
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))


def _minimal_members() -> list[tuple[str, bytes, bytes, int]]:
    return [
        ("package/package.json", b"{}", tarfile.REGTYPE, 0o644),
        ("package/npm-shrinkwrap.json", b"{}", tarfile.REGTYPE, 0o644),
    ]


@pytest.mark.parametrize(
    ("name", "member_type", "mode", "message"),
    [
        ("../escape", tarfile.REGTYPE, 0o644, "unsafe path"),
        ("/absolute", tarfile.REGTYPE, 0o644, "unsafe path"),
        ("package\\windows", tarfile.REGTYPE, 0o644, "platform-dependent"),
        ("package/link", tarfile.SYMTYPE, 0o644, "links are not permitted"),
        ("package/hardlink", tarfile.LNKTYPE, 0o644, "links are not permitted"),
        ("package/world-writable", tarfile.REGTYPE, 0o666, "unsafe permissions"),
        ("package/directory", tarfile.DIRTYPE, 0o755, "unsupported member type"),
    ],
)
def test_archive_reader_rejects_unsafe_members(
    tmp_path: Path, name: str, member_type: bytes, mode: int, message: str
) -> None:
    archive = tmp_path / "unsafe.tgz"
    _write_archive(archive, [(name, b"x", member_type, mode), *_minimal_members()])
    with pytest.raises(ValueError, match=message):
        builder._read_archive(archive)


def test_archive_reader_rejects_duplicates_and_case_collisions(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.tgz"
    _write_archive(duplicate, [*_minimal_members(), _minimal_members()[0]])
    with pytest.raises(ValueError, match="duplicate member"):
        builder._read_archive(duplicate)

    collision = tmp_path / "collision.tgz"
    _write_archive(
        collision,
        [*_minimal_members(), ("package/Package.json", b"{}", tarfile.REGTYPE, 0o644)],
    )
    with pytest.raises(ValueError, match="case-colliding"):
        builder._read_archive(collision)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mtime": 1}, "gzip timestamp|canonical tar metadata"),
        ({"uid": 1}, "canonical tar metadata"),
        ({"gid": 1}, "canonical tar metadata"),
        ({"gzip_filename": "platform.tgz"}, "gzip header flags"),
    ],
)
def test_canonical_verifier_rejects_metadata_drift(
    tmp_path: Path, kwargs: dict[str, int | str], message: str
) -> None:
    archive = tmp_path / "metadata-drift.tgz"
    _write_archive(archive, _minimal_members(), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        verifier._tree(archive, require_canonical=True)


def test_canonical_verifier_rejects_unstable_order_and_unexpected_files(tmp_path: Path) -> None:
    unstable = tmp_path / "unstable.tgz"
    _write_archive(unstable, _minimal_members())
    with pytest.raises(ValueError, match="ordering"):
        verifier._tree(unstable, require_canonical=True)

    manifest = verifier._load_manifest()
    base = ROOT / "runtime/openclaw/repack/dist" / manifest["base"]["filename"]
    entries = builder._patch_entries(builder._read_archive(base), manifest)
    entries["package/unexpected.txt"] = (b"unexpected", 0o644)
    unexpected = tmp_path / "unexpected.tgz"
    builder._write_canonical_archive(entries, unexpected)
    with pytest.raises(ValueError, match="inventory differs"):
        verifier._verify_repack_delta(base, unexpected, manifest)


def test_manifest_rejects_every_unreviewed_binding_or_metadata_field(tmp_path: Path) -> None:
    def add_unexpected(value: dict[str, Any]) -> None:
        value["unexpected"] = True

    mutations: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda value: value["replacements"][0].update(path="node_modules/not-reviewed"),
        lambda value: value["replacements"][0].update(required_by_path="node_modules/other"),
        lambda value: value["replacements"][0].update(required_range="^5.0.6"),
        lambda value: value["replacements"][0]["source"].update(version="5.0.7"),
        lambda value: value["replacements"][0]["replacement"].update(version="5.0.10"),
        lambda value: value["replacements"][0]["replacement"].update(
            url="https://registry.npmjs.org/brace-expansion/-/brace-expansion-5.0.10.tgz"
        ),
        lambda value: value["replacements"][0]["replacement"].update(integrity="sha512-AA=="),
        lambda value: value["replacements"][0]["replacement"].update(sha256="sha256:" + "0" * 64),
        lambda value: add_unexpected(value["replacements"][0]),
        lambda value: value["allowed_file_mutations"].append("package/unexpected.txt"),
    )
    for index, mutate in enumerate(mutations):
        manifest = copy.deepcopy(_manifest())
        mutate(manifest)
        candidate = tmp_path / f"unreviewed-{index}.json"
        candidate.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError, match=r"violates schema|reviewed|allowed file mutation"):
            verifier._load_manifest(candidate)
