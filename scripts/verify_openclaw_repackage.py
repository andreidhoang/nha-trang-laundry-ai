"""Independently verify the canonical, derived, EVAL_ONLY OpenClaw r2 package."""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import hmac
import io
import json
import re
import tarfile
import tempfile
import unicodedata
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v2.json"
SCHEMA_PATH = ROOT / "specs/contracts/openclaw-repackage-manifest-v2.schema.json"
PLUGIN_ROOT = ROOT / "runtime/openclaw/public-cell/plugin"
DIST_DIR = ROOT / "runtime/openclaw/repack/dist"
CAPABILITY_STATUS = ROOT / "delivery/CAPABILITY_STATUS.yaml"
WORKFLOW_PATH = ROOT / ".github/workflows/release-supply-chain.yml"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
REGISTRY_URL = re.compile(r"https://registry\.npmjs\.org/[a-z0-9@._/-]+\.tgz")
REVIEWED_REPLACEMENTS_SHA256 = (
    "sha256:a27db62433925ff6fbd945f2dc45d31dc5d244a24ab6219ef23b3b62c11ac234"
)
REVIEWED_BASE_SHA256 = "sha256:f314660d9492829c39b0dde8849131bd0bb65df7d8ebba3cb556bc6a36518f4d"
REVIEWED_UPSTREAM_SHA256 = "sha256:bb39dd49f6e1b5413a5b9e4cc364801f55c2e579a4adef8c4ddf04c4c4303f7f"
PACKAGE_JSON = "package/package.json"
SHRINKWRAP = "package/npm-shrinkwrap.json"
ALLOWED_MUTATIONS = (PACKAGE_JSON, SHRINKWRAP)

ArchiveEntries = dict[str, tuple[bytes, int]]


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _integrity(data: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    return f"sha512-{encoded}"


def _canonical_json_sha256(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return _sha256(raw)


def _load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: list(e.path))
    if errors:
        raise ValueError(
            "OpenClaw repackage manifest violates schema: "
            + "; ".join(error.message for error in errors)
        )
    if _canonical_json_sha256(manifest["replacements"]) != REVIEWED_REPLACEMENTS_SHA256:
        raise ValueError("OpenClaw reviewed replacement set or material binding drifted")
    if _canonical_json_sha256(manifest["base"]) != REVIEWED_BASE_SHA256:
        raise ValueError("OpenClaw reviewed r1 rollback binding drifted")
    if _canonical_json_sha256(manifest["upstream"]) != REVIEWED_UPSTREAM_SHA256:
        raise ValueError("OpenClaw reviewed upstream binding drifted")
    if tuple(manifest["allowed_file_mutations"]) != ALLOWED_MUTATIONS:
        raise ValueError("OpenClaw allowed file mutation set drifted")
    mutations = [item for item in manifest["replacements"] if "package_dependency_mutation" in item]
    if len(mutations) != 1 or mutations[0]["source"]["name"] != "undici":
        raise ValueError("only the reviewed undici package metadata mutation is permitted")
    if any(manifest["activation"].values()):
        raise ValueError("OpenClaw repackage must remain EVAL_ONLY with activation disabled")
    return manifest


def _download(material: dict[str, Any], destination: Path) -> None:
    url = str(material["url"])
    if REGISTRY_URL.fullmatch(url) is None:
        raise ValueError("OpenClaw material URL is not an exact npm registry tarball")
    request = urllib.request.Request(url, headers={"User-Agent": "laundry-repack-verifier/2"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.geturl() != url:
            raise ValueError("OpenClaw material redirected away from its exact URL")
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("OpenClaw material exceeds the bounded download size")
    for expected, actual, label in (
        (str(material["integrity"]), _integrity(data), "integrity"),
        (str(material["sha256"]), _sha256(data), "SHA-256"),
        (str(material["size"]), str(len(data)), "size"),
    ):
        if not hmac.compare_digest(expected, actual):
            raise ValueError(f"OpenClaw material {label} mismatch for {material['name']}")
    destination.write_bytes(data)


def _safe_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise ValueError("npm package contains a platform-dependent or empty path")
    if unicodedata.normalize("NFC", name) != name:
        raise ValueError("npm package path is not NFC-normalized")
    try:
        name.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("npm package path is not valid UTF-8") from error
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("npm package contains an unsafe path")
    if not name.startswith("package/"):
        raise ValueError("npm package member is outside the package root")
    return name


def _normalized_mode(mode: int) -> int:
    if mode & 0o7000 or mode & 0o002:
        raise ValueError("npm package contains unsafe permissions")
    return 0o755 if mode & 0o111 else 0o644


def _tree(
    archive: Path,
    *,
    require_canonical: bool = False,
    required_members: tuple[str, ...] = (PACKAGE_JSON, SHRINKWRAP),
) -> ArchiveEntries:
    raw_archive = archive.read_bytes()
    if require_canonical:
        if len(raw_archive) < 10 or raw_archive[:4] != b"\x1f\x8b\x08\x00":
            raise ValueError("canonical gzip header flags drifted")
        if raw_archive[4:8] != b"\x00\x00\x00\x00" or raw_archive[9] != 255:
            raise ValueError("canonical gzip timestamp or platform byte drifted")
    result: ArchiveEntries = {}
    casefolded: dict[str, str] = {}
    total_size = 0
    order: list[str] = []
    with gzip.open(archive, mode="rb") as compressed:
        tar_data = compressed.read(MAX_ARCHIVE_BYTES + 1)
    if len(tar_data) > MAX_ARCHIVE_BYTES:
        raise ValueError("npm package expands beyond the bounded size")
    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as package:
        members = package.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("npm package contains too many members")
        for member in members:
            name = _safe_name(member.name)
            folded = name.casefold()
            if name in result:
                raise ValueError(f"npm package contains duplicate member: {name}")
            if folded in casefolded:
                raise ValueError(
                    f"npm package contains a case-colliding member: {casefolded[folded]} / {name}"
                )
            casefolded[folded] = name
            if member.issym() or member.islnk():
                raise ValueError("npm package links are not permitted")
            if not member.isfile():
                raise ValueError("npm package contains an unsupported member type")
            end = member.offset_data + member.size
            if end > len(tar_data):
                raise ValueError(f"npm package member exceeds archive bounds: {name}")
            data = tar_data[member.offset_data : end]
            total_size += len(data)
            if len(data) > MAX_ARCHIVE_BYTES or total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("npm package expands beyond the bounded size")
            mode = _normalized_mode(member.mode)
            if require_canonical:
                allowed_pax = {} if len(name.encode("utf-8")) <= 100 else {"path": name}
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.uname != ""
                    or member.gname != ""
                    or member.mtime != 0
                    or member.mode != mode
                    or member.pax_headers != allowed_pax
                ):
                    raise ValueError(f"canonical tar metadata drifted for {name}")
            order.append(name)
            result[name] = (data, mode)
    if require_canonical and order != sorted(order, key=lambda value: value.encode("utf-8")):
        raise ValueError("canonical tar member ordering drifted")
    for required in required_members:
        if required not in result:
            raise ValueError(f"npm package is missing {required}")
    if require_canonical and not hmac.compare_digest(raw_archive, _canonical_archive_bytes(result)):
        raise ValueError("canonical archive byte construction drifted")
    return result


def _canonical_archive_bytes(entries: ArchiveEntries) -> bytes:
    raw = io.BytesIO()
    with (
        gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        for name in sorted(entries, key=lambda value: value.encode("utf-8")):
            data, mode = entries[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def _json(entries: ArchiveEntries, path: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(entries[path][0]))


def _verify_package(archive: Path, material: dict[str, Any]) -> None:
    raw = _tree(archive, required_members=(PACKAGE_JSON,))[PACKAGE_JSON][0]
    if _sha256(raw) != material["package_json_sha256"]:
        raise ValueError(f"npm package metadata digest drifted for {material['name']}")
    package = json.loads(raw)
    if package.get("name") != material["name"] or package.get("version") != material["version"]:
        raise ValueError(f"npm package identity mismatch for {material['name']}")
    if "dependencies" in material and package.get("dependencies", {}) != material["dependencies"]:
        raise ValueError(f"npm package dependency metadata drifted for {material['name']}")


def _verify_local_material(path: Path, material: dict[str, Any]) -> None:
    data = path.read_bytes()
    for expected, actual, label in (
        (str(material["integrity"]), _integrity(data), "integrity"),
        (str(material["sha256"]), _sha256(data), "SHA-256"),
        (str(material["size"]), str(len(data)), "size"),
    ):
        if not hmac.compare_digest(expected, actual):
            raise ValueError(f"OpenClaw local material {label} mismatch for {path.name}")


def _verify_base_delta(upstream: Path, base: Path, manifest: dict[str, Any]) -> None:
    base_manifest_path = ROOT / manifest["base"]["manifest_path"]
    base_manifest_raw = base_manifest_path.read_bytes()
    if _sha256(base_manifest_raw) != manifest["base"]["manifest_sha256"]:
        raise ValueError("r1 rollback manifest digest drifted")
    base_manifest = json.loads(base_manifest_raw)
    source_tree = _tree(upstream)
    base_tree = _tree(base)
    if set(source_tree) != set(base_tree):
        raise ValueError("r1 rollback inventory differs from upstream")
    changed = {path for path in source_tree if source_tree[path][0] != base_tree[path][0]}
    if changed != {SHRINKWRAP}:
        raise ValueError(f"r1 rollback changed unexpected files: {sorted(changed)}")
    expected = copy.deepcopy(_json(source_tree, SHRINKWRAP))
    for item in base_manifest["replacements"]:
        record = expected["packages"].get(item["path"])
        replacement = item["replacement"]
        if not isinstance(record, dict):
            raise ValueError("r1 rollback path is missing upstream")
        record.update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
    if _json(base_tree, SHRINKWRAP) != expected:
        raise ValueError("r1 rollback artifact is not the reviewed predecessor")


def _verify_repack_delta(base: Path, repacked: Path, manifest: dict[str, Any]) -> None:
    source_tree = _tree(base)
    output_tree = _tree(repacked, require_canonical=True)
    if set(source_tree) != set(output_tree):
        raise ValueError("repacked OpenClaw file inventory differs from r1 rollback")
    changed = {path for path in source_tree if source_tree[path][0] != output_tree[path][0]}
    if changed != set(ALLOWED_MUTATIONS):
        raise ValueError(f"repacked OpenClaw changed unexpected files: {sorted(changed)}")
    source_package = _json(source_tree, PACKAGE_JSON)
    expected_package = copy.deepcopy(source_package)
    source_lock = _json(source_tree, SHRINKWRAP)
    expected_lock = copy.deepcopy(source_lock)
    for item in manifest["replacements"]:
        source = item["source"]
        replacement = item["replacement"]
        source_record = source_lock["packages"].get(item["path"])
        expected_record = expected_lock["packages"].get(item["path"])
        source_parent = source_lock["packages"].get(item["required_by_path"])
        expected_parent = expected_lock["packages"].get(item["required_by_path"])
        if not all(
            isinstance(value, dict)
            for value in (
                source_record,
                expected_record,
                source_parent,
                expected_parent,
            )
        ):
            raise ValueError("reviewed OpenClaw lock path is missing")
        assert isinstance(source_record, dict)
        assert isinstance(expected_record, dict)
        assert isinstance(source_parent, dict)
        assert isinstance(expected_parent, dict)
        if any(
            source_record.get(key) != (source["url"] if key == "resolved" else source[key])
            for key in ("version", "resolved", "integrity")
        ):
            raise ValueError("r1 source dependency record drifted")
        if source_parent.get("dependencies", {}).get(source["name"]) != item["required_range"]:
            raise ValueError("r1 dependency edge drifted")
        expected_record.update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
        mutation = item.get("package_dependency_mutation")
        if mutation is not None:
            if (
                source["name"] != "undici"
                or item["range_policy"] != "REVIEWED_EXACT_METADATA_CHANGE"
            ):
                raise ValueError("unreviewed package metadata mutation")
            if expected_package.get("dependencies", {}).get("undici") != mutation["source_value"]:
                raise ValueError("r1 package.json undici source pin drifted")
            expected_package["dependencies"]["undici"] = mutation["replacement_value"]
            expected_parent["dependencies"]["undici"] = mutation["replacement_value"]
    if _json(output_tree, PACKAGE_JSON) != expected_package:
        raise ValueError("repacked package.json contains an unreviewed mutation")
    if _json(output_tree, SHRINKWRAP) != expected_lock:
        raise ValueError("repacked shrinkwrap contains changes beyond the approved fields")


def _verify_plugin_binding(manifest: dict[str, Any]) -> None:
    relative = "file:../../repack/dist/" + manifest["output"]["filename"]
    package = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((PLUGIN_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    if package["devDependencies"].get("openclaw") != relative:
        raise ValueError("plugin package is not bound to the r2 OpenClaw artifact")
    if package["peerDependencies"].get("openclaw") != manifest["upstream"]["version"]:
        raise ValueError("plugin peer compatibility drifted from upstream OpenClaw")
    root = lock["packages"].get("")
    installed = lock["packages"].get("node_modules/openclaw")
    if not isinstance(root, dict) or root.get("devDependencies", {}).get("openclaw") != relative:
        raise ValueError("plugin lock root is not bound to r2")
    if not isinstance(installed, dict) or any(
        installed.get(key) != expected
        for key, expected in {
            "version": manifest["upstream"]["version"],
            "resolved": relative,
            "integrity": manifest["output"]["integrity"],
        }.items()
    ):
        raise ValueError("plugin lock OpenClaw r2 binding drifted")
    if installed.get("dependencies", {}).get("undici") != "8.9.0":
        raise ValueError("plugin lock OpenClaw package metadata did not adopt undici 8.9.0")
    for item in manifest["replacements"]:
        record = lock["packages"].get(item["plugin_lock_path"])
        replacement = item["replacement"]
        if not isinstance(record, dict) or any(
            record.get(key) != (replacement["url"] if key == "resolved" else replacement[key])
            for key in ("version", "resolved", "integrity")
        ):
            raise ValueError(f"plugin lock did not install fixed {replacement['name']}")


def _verify_image_and_workflow(manifest: dict[str, Any]) -> None:
    image = manifest["image"]
    dockerfile = (ROOT / image["dockerfile"]).read_text(encoding="utf-8")
    required_docker = (
        image["base_image"],
        manifest["output"]["filename"],
        "npm ci --ignore-scripts",
        "libcrypto3=3.5.7-r0",
        "libssl3=3.5.7-r0",
        "/usr/local/lib/node_modules/npm",
        "USER node",
        'REAL_CUSTOMER_DATA_ALLOWED="false"',
        'PUBLIC_INGRESS_ENABLED="false"',
        'PROVIDER_CALLS_ENABLED="false"',
        'AUTOMATIC_SEND_ENABLED="false"',
        'DIRECT_SEND_ENABLED="false"',
    )
    if any(token not in dockerfile for token in required_docker):
        raise ValueError("OpenClaw image contract is incomplete")
    if "COPY . ." in dockerfile or "USER root" in dockerfile:
        raise ValueError("OpenClaw image broadens the reviewed build or privilege boundary")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    required_workflow = (
        "scripts/build_openclaw_repackage.py --verify-reproducible",
        "scripts/verify_openclaw_repackage.py",
        "runs-on: windows-latest",
        "runs-on: ubuntu-latest",
        "scripts/verify_openclaw_cross_platform.py compare",
        "scripts/verify_openclaw_cross_platform.py verify",
        "openclaw-r2-compared-${{ github.sha }}",
        'cmp --silent "$compared_artifact" "$committed_artifact"',
        "npm audit --prefix runtime/openclaw/public-cell/plugin --audit-level=high",
        "runtime/openclaw/repack/Dockerfile",
        "--provenance=mode=max",
        "https://slsa.dev/provenance/v1",
        "--input /work/openclaw-oci",
        "--severity CRITICAL,HIGH --exit-code 1",
    )
    if any(token not in workflow for token in required_workflow):
        raise ValueError("hosted OpenClaw image/audit/provenance workflow is incomplete")
    if (
        "--omit=dev" in workflow
        or "--input /work/openclaw.oci.tar" in workflow
        or "continue-on-error" in workflow
    ):
        raise ValueError("hosted OpenClaw workflow weakens the complete-tree or OCI-layout check")


def _verify_authority_disabled() -> None:
    status = yaml.safe_load(CAPABILITY_STATUS.read_text(encoding="utf-8"))
    if status.get("default_authorization") != "NOT_AUTHORIZED":
        raise ValueError("default capability authorization is not fail-closed")
    capabilities = status.get("capabilities")
    if not isinstance(capabilities, list) or any(
        not isinstance(capability, dict) or capability.get("authorization") != "NOT_AUTHORIZED"
        for capability in capabilities
    ):
        raise ValueError("OpenClaw repackage must not authorize any capability")


def main() -> int:
    manifest = _load_manifest()
    output = manifest["output"]
    artifact = DIST_DIR / output["filename"]
    _verify_local_material(artifact, output)
    package = _tree(artifact, require_canonical=True)
    identity = _json(package, PACKAGE_JSON)
    if (
        identity.get("name") != manifest["upstream"]["name"]
        or identity.get("version") != "2026.7.1-2"
    ):
        raise ValueError("derived OpenClaw package identity drifted")
    if identity.get("dependencies", {}).get("undici") != "8.9.0":
        raise ValueError("derived OpenClaw package did not apply reviewed undici metadata")
    with tempfile.TemporaryDirectory(prefix="laundry-openclaw-verify-r2-") as temporary:
        root = Path(temporary)
        upstream = root / "upstream.tgz"
        _download(manifest["upstream"], upstream)
        _verify_package(upstream, manifest["upstream"])
        base = DIST_DIR / manifest["base"]["filename"]
        _verify_local_material(base, manifest["base"])
        _verify_base_delta(upstream, base, manifest)
        for index, item in enumerate(manifest["replacements"]):
            for kind in ("source", "replacement"):
                material = root / f"{index}-{kind}.tgz"
                _download(item[kind], material)
                _verify_package(material, item[kind])
        _verify_repack_delta(base, artifact, manifest)
    _verify_plugin_binding(manifest)
    _verify_image_and_workflow(manifest)
    _verify_authority_disabled()
    print(
        json.dumps(
            {
                "status": "VERIFIED_DERIVED_EVAL_ONLY_REPACKAGE",
                "openclaw_version": manifest["upstream"]["version"],
                "artifact_sha256": output["sha256"],
                "replacement_count": len(manifest["replacements"]),
                "rollback_artifact": manifest["base"]["filename"],
                "hosted_image_evidence_required": True,
                "security_supply_chain_review_required": True,
                "release_effect": "NONE",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
