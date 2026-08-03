"""Reproducibly repackage pinned OpenClaw with exact compatible security fixes."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v1.json"
DIST_DIR = ROOT / "runtime/openclaw/repack/dist"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
REGISTRY_URL = re.compile(r"https://registry\.npmjs\.org/[a-z0-9@._/-]+\.tgz")


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _integrity(data: bytes) -> str:
    digest = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    return f"sha512-{digest}"


def _load_manifest() -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    if manifest.get("schema_version") != 1 or manifest.get("artifact_status") != "EVAL_ONLY":
        raise ValueError("OpenClaw repackage manifest must be schema v1 and EVAL_ONLY")
    activation = manifest.get("activation")
    if not isinstance(activation, dict) or any(activation.values()):
        raise ValueError("OpenClaw repackage activation flags must all remain false")
    output = manifest.get("output")
    if not isinstance(output, dict) or output.get("filename") != (
        "openclaw-2026.7.1-2-nha-trang-r1.tgz"
    ):
        raise ValueError("OpenClaw repackage output name is not the reviewed immutable artifact")
    for field in ("sha256", "integrity", "size"):
        if output.get(field) is None:
            raise ValueError(f"OpenClaw repackage output is missing {field}")
    image = manifest.get("image")
    if (
        not isinstance(image, dict)
        or image.get("target_platform") != "linux/amd64"
        or image.get("hosted_evidence_required") is not True
        or image.get("provenance_predicate_type") != "https://slsa.dev/provenance/v1"
    ):
        raise ValueError("OpenClaw image must require hosted Linux SLSA provenance")
    replacements = manifest.get("replacements")
    if not isinstance(replacements, list) or len(replacements) != 2:
        raise ValueError("OpenClaw repackage must contain exactly two reviewed replacements")
    if {item.get("path") for item in replacements if isinstance(item, dict)} != {
        "node_modules/brace-expansion",
        "node_modules/fast-uri",
    }:
        raise ValueError("OpenClaw repackage replacement paths drifted")
    for material in (
        manifest.get("source"),
        *(entry.get(kind) for entry in replacements for kind in ("source", "replacement")),
    ):
        if not isinstance(material, dict):
            raise ValueError("OpenClaw repackage material is malformed")
        if not isinstance(material.get("size"), int) or material["size"] <= 0:
            raise ValueError(f"OpenClaw material size is missing for {material.get('name')}")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(material.get("sha256"))) is None:
            raise ValueError(f"OpenClaw material SHA-256 is missing for {material.get('name')}")
    return manifest


def _download(material: dict[str, Any], destination: Path) -> bytes:
    url = material.get("url")
    if not isinstance(url, str) or REGISTRY_URL.fullmatch(url) is None:
        raise ValueError("material URL must be an exact npm registry tarball URL")
    request = urllib.request.Request(url, headers={"User-Agent": "nha-trang-laundry-repack/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.geturl() != url:
            raise ValueError("npm material download redirected away from its exact URL")
        data = cast(bytes, response.read(MAX_DOWNLOAD_BYTES + 1))
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("npm material exceeds the bounded download size")
    if not hmac.compare_digest(_integrity(data), str(material.get("integrity"))):
        raise ValueError(f"npm integrity mismatch for {material.get('name')}")
    expected_sha256 = str(material["sha256"])
    if not hmac.compare_digest(_sha256(data), expected_sha256):
        raise ValueError(f"source SHA-256 mismatch for {material.get('name')}")
    expected_size = int(material["size"])
    if len(data) != expected_size:
        raise ValueError(f"source size mismatch for {material.get('name')}")
    destination.write_bytes(data)
    return data


def _member_bytes(archive: Path, member_name: str) -> bytes:
    with tarfile.open(archive, mode="r:gz") as package:
        try:
            member = package.getmember(member_name)
        except KeyError as error:
            raise ValueError(f"npm tarball is missing {member_name}") from error
        extracted = package.extractfile(member)
        if extracted is None:
            raise ValueError(f"npm tarball member is not a regular file: {member_name}")
        return extracted.read()


def _verify_package_tarball(archive: Path, material: dict[str, Any]) -> None:
    package = json.loads(_member_bytes(archive, "package/package.json"))
    if package.get("name") != material.get("name") or package.get("version") != material.get(
        "version"
    ):
        raise ValueError("npm tarball package identity does not match the manifest")
    expected_dependencies = material.get("dependencies")
    if (
        expected_dependencies is not None
        and package.get("dependencies", {}) != expected_dependencies
    ):
        raise ValueError(f"replacement dependency metadata drifted for {material.get('name')}")


def _parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"unsupported semantic version: {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _satisfies_caret(version: str, requirement: str) -> bool:
    if not requirement.startswith("^"):
        raise ValueError(f"unsupported dependency range: {requirement}")
    candidate = _parse_version(version)
    lower = _parse_version(requirement[1:])
    if lower[0] > 0:
        upper = (lower[0] + 1, 0, 0)
    elif lower[1] > 0:
        upper = (0, lower[1] + 1, 0)
    else:
        upper = (0, 0, lower[2] + 1)
    return lower <= candidate < upper


def _archive_tree(archive: Path) -> dict[str, tuple[str, str]]:
    tree: dict[str, tuple[str, str]] = {}
    with tarfile.open(archive, mode="r:gz") as package:
        for member in package.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ValueError("npm tarball contains an unsafe path")
            if member.isfile():
                extracted = package.extractfile(member)
                if extracted is None:
                    raise ValueError(f"unable to read tar member: {member.name}")
                tree[member.name] = ("file", _sha256(extracted.read()))
            elif member.issym() or member.islnk():
                tree[member.name] = ("link", member.linkname)
            elif member.isdir():
                continue
            else:
                raise ValueError(f"npm tarball contains unsupported member type: {member.name}")
    return tree


def _verify_only_shrinkwrap_changed(source: Path, repacked: Path) -> None:
    source_tree = _archive_tree(source)
    repacked_tree = _archive_tree(repacked)
    shrinkwrap = "package/npm-shrinkwrap.json"
    if set(source_tree) != set(repacked_tree):
        raise ValueError("repacked OpenClaw file inventory differs from upstream")
    changed = {path for path in source_tree if source_tree[path] != repacked_tree[path]}
    if changed != {shrinkwrap}:
        raise ValueError(f"repacked OpenClaw changed unexpected files: {sorted(changed)}")


def _patch_shrinkwrap(package_root: Path, manifest: dict[str, Any]) -> None:
    shrinkwrap_path = package_root / "npm-shrinkwrap.json"
    shrinkwrap = json.loads(shrinkwrap_path.read_text(encoding="utf-8"))
    packages = shrinkwrap.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("OpenClaw shrinkwrap has no packages mapping")
    original = copy.deepcopy(shrinkwrap)
    for item in manifest["replacements"]:
        path = item["path"]
        source = item["source"]
        replacement = item["replacement"]
        record = packages.get(path)
        if not isinstance(record, dict):
            raise ValueError(f"OpenClaw shrinkwrap is missing {path}")
        for key in ("version", "resolved", "integrity"):
            expected = source["url"] if key == "resolved" else source[key]
            if record.get(key) != expected:
                raise ValueError(f"unexpected source {key} for {path}")
        parent = packages.get(item["required_by_path"])
        if (
            not isinstance(parent, dict)
            or parent.get("dependencies", {}).get(source["name"]) != item["required_range"]
        ):
            raise ValueError(f"declared dependency edge drifted for {path}")
        if not _satisfies_caret(replacement["version"], item["required_range"]):
            raise ValueError(f"replacement no longer satisfies the declared range for {path}")
        record.update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
    expected = copy.deepcopy(original)
    for item in manifest["replacements"]:
        replacement = item["replacement"]
        expected["packages"][item["path"]].update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
    if shrinkwrap != expected:
        raise ValueError("repackaging modified more than the approved shrinkwrap fields")
    shrinkwrap_path.write_text(json.dumps(shrinkwrap, indent=2) + "\n", encoding="utf-8")


def _npm_executable() -> str:
    suffix = ".cmd" if os.name == "nt" else ""
    resolved = shutil.which(f"npm{suffix}")
    if resolved is None:
        raise RuntimeError("npm is required to create the deterministic package")
    return resolved


def _build_once(manifest: dict[str, Any], work_root: Path) -> Path:
    source_tarball = work_root / "upstream.tgz"
    source_data = _download(manifest["source"], source_tarball)
    if _sha256(source_data) != manifest["source"]["sha256"]:
        raise ValueError("upstream OpenClaw digest validation failed")
    _verify_package_tarball(source_tarball, manifest["source"])
    for index, item in enumerate(manifest["replacements"]):
        vulnerable_tarball = work_root / f"vulnerable-{index}.tgz"
        _download(item["source"], vulnerable_tarball)
        _verify_package_tarball(vulnerable_tarball, item["source"])
        replacement_tarball = work_root / f"replacement-{index}.tgz"
        _download(item["replacement"], replacement_tarball)
        _verify_package_tarball(replacement_tarball, item["replacement"])
    extract_root = work_root / "source"
    extract_root.mkdir()
    with tarfile.open(source_tarball, mode="r:gz") as package:
        package.extractall(extract_root, filter="data")
    package_root = extract_root / "package"
    _patch_shrinkwrap(package_root, manifest)
    packed_root = work_root / "packed"
    packed_root.mkdir()
    result = subprocess.run(
        [
            _npm_executable(),
            "pack",
            str(package_root),
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            str(packed_root),
        ],
        cwd=work_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm pack failed:\n{result.stdout}\n{result.stderr}")
    try:
        pack_result = json.loads(result.stdout)
        packed_name = pack_result[0]["filename"]
        if not isinstance(packed_name, str):
            raise TypeError("npm pack filename must be a string")
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("npm pack returned malformed JSON") from error
    built = packed_root / packed_name
    _verify_only_shrinkwrap_changed(source_tarball, built)
    return built


def _output_summary(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": _sha256(data),
        "integrity": _integrity(data),
        "size": len(data),
    }


def main(argv: list[str] | None = None) -> int:
    candidate = argparse.ArgumentParser(description=__doc__)
    candidate.add_argument("--verify-reproducible", action="store_true")
    args = candidate.parse_args(argv)
    manifest = _load_manifest()
    build_count = 2 if args.verify_reproducible else 1
    built: list[Path] = []
    temporary_roots: list[tempfile.TemporaryDirectory[str]] = []
    try:
        for _ in range(build_count):
            temporary = tempfile.TemporaryDirectory(prefix="laundry-openclaw-repack-")
            temporary_roots.append(temporary)
            built.append(_build_once(manifest, Path(temporary.name)))
        first_data = built[0].read_bytes()
        if len(built) == 2 and not hmac.compare_digest(first_data, built[1].read_bytes()):
            raise ValueError("independent OpenClaw repackage builds are not byte-identical")
        expected = manifest["output"]
        actual = _output_summary(built[0])
        actual["filename"] = expected["filename"]
        for field in ("sha256", "integrity", "size"):
            if not hmac.compare_digest(str(actual[field]), str(expected[field])):
                raise ValueError(f"repacked OpenClaw output {field} does not match manifest")
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        destination = DIST_DIR / expected["filename"]
        destination.write_bytes(first_data)
        print(json.dumps({"status": "REPRODUCIBLE_REPACKAGE", "output": actual}, indent=2))
    finally:
        for temporary in temporary_roots:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
