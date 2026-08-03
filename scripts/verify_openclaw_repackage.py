"""Independently verify the immutable, eval-only OpenClaw repackage."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v1.json"
SCHEMA_PATH = ROOT / "specs/contracts/openclaw-repackage-manifest-v1.schema.json"
PLUGIN_ROOT = ROOT / "runtime/openclaw/public-cell/plugin"
CAPABILITY_STATUS = ROOT / "delivery/CAPABILITY_STATUS.yaml"
WORKFLOW_PATH = ROOT / ".github/workflows/release-supply-chain.yml"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
REGISTRY_URL = re.compile(r"https://registry\.npmjs\.org/[a-z0-9@._/-]+\.tgz")


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _integrity(data: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    return f"sha512-{encoded}"


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
    expected = {
        ("node_modules/brace-expansion", "5.0.7", "5.0.8", "^5.0.5"),
        ("node_modules/fast-uri", "3.1.2", "3.1.4", "^3.0.1"),
    }
    observed = {
        (
            item["path"],
            item["source"]["version"],
            item["replacement"]["version"],
            item["required_range"],
        )
        for item in manifest["replacements"]
    }
    if observed != expected:
        raise ValueError("OpenClaw reviewed replacement set drifted")
    if any(manifest["activation"].values()):
        raise ValueError("OpenClaw repackage must remain EVAL_ONLY with activation disabled")
    return manifest


def _download(material: dict[str, Any], destination: Path) -> None:
    url = str(material["url"])
    if REGISTRY_URL.fullmatch(url) is None:
        raise ValueError("OpenClaw material URL is not an exact npm registry tarball")
    request = urllib.request.Request(url, headers={"User-Agent": "laundry-repack-verifier/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.geturl() != url:
            raise ValueError("OpenClaw material redirected away from its exact URL")
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("OpenClaw material exceeds the bounded download size")
    checks = (
        (str(material["integrity"]), _integrity(data), "integrity"),
        (str(material["sha256"]), _sha256(data), "SHA-256"),
        (str(material["size"]), str(len(data)), "size"),
    )
    for expected, actual, label in checks:
        if not hmac.compare_digest(expected, actual):
            raise ValueError(f"OpenClaw material {label} mismatch for {material['name']}")
    destination.write_bytes(data)


def _member_bytes(archive: Path, member_name: str) -> bytes:
    with tarfile.open(archive, mode="r:gz") as package:
        try:
            member = package.getmember(member_name)
        except KeyError as error:
            raise ValueError(f"OpenClaw package is missing {member_name}") from error
        extracted = package.extractfile(member)
        if extracted is None:
            raise ValueError(f"OpenClaw package member is not a file: {member_name}")
        return extracted.read()


def _verify_package(archive: Path, material: dict[str, Any]) -> None:
    package = json.loads(_member_bytes(archive, "package/package.json"))
    if package.get("name") != material["name"] or package.get("version") != material["version"]:
        raise ValueError(f"npm package identity mismatch for {material['name']}")
    if "dependencies" in material and package.get("dependencies", {}) != material["dependencies"]:
        raise ValueError(f"npm package dependency metadata drifted for {material['name']}")


def _tree(archive: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    with tarfile.open(archive, mode="r:gz") as package:
        for member in package.getmembers():
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise ValueError("npm package contains an unsafe path")
            if member.isfile():
                extracted = package.extractfile(member)
                if extracted is None:
                    raise ValueError(f"unable to read npm package member: {member.name}")
                result[member.name] = ("file", _sha256(extracted.read()))
            elif member.issym() or member.islnk():
                link_parts = Path(member.linkname).parts
                if member.linkname.startswith("/") or ".." in link_parts:
                    raise ValueError("npm package contains an unsafe link")
                result[member.name] = ("link", member.linkname)
            elif not member.isdir():
                raise ValueError("npm package contains an unsupported member type")
    return result


def _verify_repack_delta(upstream: Path, repacked: Path, manifest: dict[str, Any]) -> None:
    source_tree = _tree(upstream)
    output_tree = _tree(repacked)
    if set(source_tree) != set(output_tree):
        raise ValueError("repacked OpenClaw file inventory differs from upstream")
    changed = {path for path in source_tree if source_tree[path] != output_tree[path]}
    if changed != {"package/npm-shrinkwrap.json"}:
        raise ValueError(f"repacked OpenClaw changed unexpected files: {sorted(changed)}")

    source = json.loads(_member_bytes(upstream, "package/npm-shrinkwrap.json"))
    output = json.loads(_member_bytes(repacked, "package/npm-shrinkwrap.json"))
    expected = copy.deepcopy(source)
    for item in manifest["replacements"]:
        source_record = source["packages"].get(item["path"])
        parent = source["packages"].get(item["required_by_path"])
        if not isinstance(source_record, dict) or not isinstance(parent, dict):
            raise ValueError("reviewed OpenClaw shrinkwrap path is missing")
        vulnerable = item["source"]
        replacement = item["replacement"]
        if any(
            source_record.get(key) != (vulnerable["url"] if key == "resolved" else vulnerable[key])
            for key in ("version", "resolved", "integrity")
        ):
            raise ValueError("upstream vulnerable shrinkwrap record drifted")
        if parent.get("dependencies", {}).get(vulnerable["name"]) != item["required_range"]:
            raise ValueError("upstream dependency range drifted")
        expected["packages"][item["path"]].update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
    if output != expected:
        raise ValueError("repacked shrinkwrap contains changes beyond the approved fields")


def _verify_plugin_binding(manifest: dict[str, Any]) -> None:
    relative = "file:../../repack/dist/" + manifest["output"]["filename"]
    package = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((PLUGIN_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    if package["devDependencies"].get("openclaw") != relative:
        raise ValueError("plugin package is not bound to the repacked OpenClaw artifact")
    if package["peerDependencies"].get("openclaw") != manifest["source"]["version"]:
        raise ValueError("plugin peer compatibility drifted from the upstream OpenClaw version")
    root = lock["packages"].get("")
    installed = lock["packages"].get("node_modules/openclaw")
    if not isinstance(root, dict) or root.get("devDependencies", {}).get("openclaw") != relative:
        raise ValueError("plugin lock root is not bound to the repackage")
    if not isinstance(installed, dict) or any(
        installed.get(key) != expected
        for key, expected in {
            "version": manifest["source"]["version"],
            "resolved": relative,
            "integrity": manifest["output"]["integrity"],
        }.items()
    ):
        raise ValueError("plugin lock OpenClaw package binding drifted")
    for item in manifest["replacements"]:
        record = lock["packages"].get(f"node_modules/openclaw/{item['path']}")
        replacement = item["replacement"]
        if not isinstance(record, dict) or any(
            record.get(key) != (replacement["url"] if key == "resolved" else replacement[key])
            for key in ("version", "resolved", "integrity")
        ):
            raise ValueError(f"plugin lock did not install the fixed {replacement['name']}")


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
        "npm audit --prefix runtime/openclaw/public-cell/plugin --audit-level=high",
        "runtime/openclaw/repack/Dockerfile",
        "--provenance=mode=max",
        "https://slsa.dev/provenance/v1",
        "--input /work/openclaw-oci",
        "--severity CRITICAL,HIGH --exit-code 1",
    )
    if any(token not in workflow for token in required_workflow):
        raise ValueError("hosted OpenClaw image/audit/provenance workflow is incomplete")
    if "npm audit --prefix runtime/openclaw/public-cell/plugin --omit=dev" in workflow:
        raise ValueError("hosted OpenClaw audit excludes the pinned runtime tree")
    if "--input /work/openclaw.oci.tar" in workflow:
        raise ValueError("hosted OpenClaw scan must use the extracted OCI layout")


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
    artifact = ROOT / "runtime/openclaw/repack/dist" / output["filename"]
    data = artifact.read_bytes()
    for expected, actual, label in (
        (output["sha256"], _sha256(data), "SHA-256"),
        (output["integrity"], _integrity(data), "integrity"),
        (str(output["size"]), str(len(data)), "size"),
    ):
        if not hmac.compare_digest(str(expected), str(actual)):
            raise ValueError(f"repacked OpenClaw output {label} mismatch")
    _verify_package(artifact, manifest["source"])
    with tempfile.TemporaryDirectory(prefix="laundry-openclaw-verify-") as temporary:
        root = Path(temporary)
        upstream = root / "upstream.tgz"
        _download(manifest["source"], upstream)
        _verify_package(upstream, manifest["source"])
        for index, item in enumerate(manifest["replacements"]):
            for kind in ("source", "replacement"):
                package = root / f"{index}-{kind}.tgz"
                _download(item[kind], package)
                _verify_package(package, item[kind])
        _verify_repack_delta(upstream, artifact, manifest)
    _verify_plugin_binding(manifest)
    _verify_image_and_workflow(manifest)
    _verify_authority_disabled()
    print(
        json.dumps(
            {
                "status": "VERIFIED_EVAL_ONLY_REPACKAGE",
                "openclaw_version": manifest["source"]["version"],
                "artifact_sha256": output["sha256"],
                "replacement_count": len(manifest["replacements"]),
                "hosted_image_evidence_required": True,
                "release_effect": "NONE",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
