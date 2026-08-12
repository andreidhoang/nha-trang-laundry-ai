"""Build the canonical, derived, EVAL_ONLY OpenClaw r2 package."""

from __future__ import annotations

import argparse
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

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v2.json"
SCHEMA_PATH = ROOT / "specs/contracts/openclaw-repackage-manifest-v2.schema.json"
DIST_DIR = ROOT / "runtime/openclaw/repack/dist"
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
    digest = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    return f"sha512-{digest}"


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return _sha256(encoded)


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
    if any(manifest["activation"].values()):
        raise ValueError("OpenClaw repackage activation flags must all remain false")
    return manifest


def _download(material: dict[str, Any], destination: Path) -> bytes:
    url = material.get("url")
    if not isinstance(url, str) or REGISTRY_URL.fullmatch(url) is None:
        raise ValueError("material URL must be an exact npm registry tarball URL")
    request = urllib.request.Request(url, headers={"User-Agent": "nha-trang-laundry-repack/2"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.geturl() != url:
            raise ValueError("npm material download redirected away from its exact URL")
        data = cast(bytes, response.read(MAX_DOWNLOAD_BYTES + 1))
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("npm material exceeds the bounded download size")
    for expected, actual, label in (
        (str(material["integrity"]), _integrity(data), "integrity"),
        (str(material["sha256"]), _sha256(data), "SHA-256"),
        (str(material["size"]), str(len(data)), "size"),
    ):
        if not hmac.compare_digest(expected, actual):
            raise ValueError(f"npm material {label} mismatch for {material.get('name')}")
    destination.write_bytes(data)
    return data


def _safe_member_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise ValueError("npm tarball contains a platform-dependent or empty path")
    if unicodedata.normalize("NFC", name) != name:
        raise ValueError("npm tarball path is not NFC-normalized")
    try:
        name.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("npm tarball path is not valid UTF-8") from error
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("npm tarball contains an unsafe path")
    if not name.startswith("package/"):
        raise ValueError("npm tarball member is outside the package root")
    return name


def _normalized_mode(mode: int) -> int:
    if mode & 0o7000 or mode & 0o002:
        raise ValueError("npm tarball contains unsafe permissions")
    return 0o755 if mode & 0o111 else 0o644


def _read_archive(
    archive: Path, *, required_members: tuple[str, ...] = (PACKAGE_JSON, SHRINKWRAP)
) -> ArchiveEntries:
    entries: ArchiveEntries = {}
    casefolded: dict[str, str] = {}
    total_size = 0
    with gzip.open(archive, mode="rb") as compressed:
        tar_data = compressed.read(MAX_ARCHIVE_BYTES + 1)
    if len(tar_data) > MAX_ARCHIVE_BYTES:
        raise ValueError("npm tarball expands beyond the bounded size")
    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:") as package:
        members = package.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("npm tarball contains too many members")
        for member in members:
            name = _safe_member_name(member.name)
            folded = name.casefold()
            if name in entries:
                raise ValueError(f"npm tarball contains duplicate member: {name}")
            if folded in casefolded:
                raise ValueError(
                    f"npm tarball contains a case-colliding member: {casefolded[folded]} / {name}"
                )
            casefolded[folded] = name
            if member.issym() or member.islnk():
                raise ValueError("npm tarball links are not permitted")
            if not member.isfile():
                raise ValueError("npm tarball contains an unsupported member type")
            end = member.offset_data + member.size
            if end > len(tar_data):
                raise ValueError(f"npm tarball member exceeds archive bounds: {name}")
            data = tar_data[member.offset_data : end]
            total_size += len(data)
            if len(data) > MAX_ARCHIVE_BYTES or total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("npm tarball expands beyond the bounded size")
            entries[name] = (data, _normalized_mode(member.mode))
    for required in required_members:
        if required not in entries:
            raise ValueError(f"npm tarball is missing {required}")
    return entries


def _member_bytes(archive: Path, member_name: str) -> bytes:
    return _read_archive(archive, required_members=(member_name,))[member_name][0]


def _verify_package_tarball(archive: Path, material: dict[str, Any]) -> None:
    raw = _member_bytes(archive, PACKAGE_JSON)
    if _sha256(raw) != material["package_json_sha256"]:
        raise ValueError(f"npm package metadata digest drifted for {material['name']}")
    package = json.loads(raw)
    if package.get("name") != material["name"] or package.get("version") != material["version"]:
        raise ValueError(f"npm package identity mismatch for {material['name']}")
    if "dependencies" in material and package.get("dependencies", {}) != material["dependencies"]:
        raise ValueError(f"npm package dependency metadata drifted for {material['name']}")


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
    upper = (
        (lower[0] + 1, 0, 0)
        if lower[0] > 0
        else (0, lower[1] + 1, 0)
        if lower[1] > 0
        else (0, 0, lower[2] + 1)
    )
    return lower <= candidate < upper


def _json_file(entries: ArchiveEntries, path: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(entries[path][0]))


def _encoded_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _verify_base_against_upstream(upstream: Path, base: Path, manifest: dict[str, Any]) -> None:
    base_manifest_path = ROOT / manifest["base"]["manifest_path"]
    base_manifest_raw = base_manifest_path.read_bytes()
    if _sha256(base_manifest_raw) != manifest["base"]["manifest_sha256"]:
        raise ValueError("r1 rollback manifest digest drifted")
    base_manifest = json.loads(base_manifest_raw)
    source_entries = _read_archive(upstream)
    base_entries = _read_archive(base)
    if set(source_entries) != set(base_entries):
        raise ValueError("r1 rollback artifact inventory differs from upstream")
    changed = {path for path in source_entries if source_entries[path][0] != base_entries[path][0]}
    if changed != {SHRINKWRAP}:
        raise ValueError(f"r1 rollback artifact changed unexpected files: {sorted(changed)}")
    source = _json_file(source_entries, SHRINKWRAP)
    expected = copy.deepcopy(source)
    for item in base_manifest["replacements"]:
        replacement = item["replacement"]
        record = expected["packages"].get(item["path"])
        if not isinstance(record, dict):
            raise ValueError("r1 rollback manifest path is missing upstream")
        record.update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
    if _json_file(base_entries, SHRINKWRAP) != expected:
        raise ValueError("r1 rollback artifact is not the reviewed predecessor")


def _patch_entries(entries: ArchiveEntries, manifest: dict[str, Any]) -> ArchiveEntries:
    result = dict(entries)
    package_json = _json_file(entries, PACKAGE_JSON)
    shrinkwrap = _json_file(entries, SHRINKWRAP)
    packages = shrinkwrap.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("OpenClaw shrinkwrap has no packages mapping")
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
        if not isinstance(parent, dict):
            raise ValueError(f"OpenClaw shrinkwrap is missing parent {item['required_by_path']!r}")
        dependency_name = source["name"]
        if parent.get("dependencies", {}).get(dependency_name) != item["required_range"]:
            raise ValueError(f"declared dependency edge drifted for {path}")
        policy = item["range_policy"]
        if policy == "SATISFIES_DECLARED_RANGE":
            if "package_dependency_mutation" in item:
                raise ValueError("range-compatible replacement cannot mutate package metadata")
            if not _satisfies_caret(replacement["version"], item["required_range"]):
                raise ValueError(f"replacement no longer satisfies the declared range for {path}")
        elif policy == "REVIEWED_EXACT_METADATA_CHANGE":
            mutation = item.get("package_dependency_mutation")
            if dependency_name != "undici" or not isinstance(mutation, dict):
                raise ValueError("only the reviewed undici metadata mutation is permitted")
            if package_json.get("dependencies", {}).get("undici") != mutation["source_value"]:
                raise ValueError("OpenClaw package.json undici source pin drifted")
            if mutation["source_value"] != item["required_range"]:
                raise ValueError("OpenClaw reviewed exact range source drifted")
            package_json["dependencies"]["undici"] = mutation["replacement_value"]
            parent["dependencies"]["undici"] = mutation["replacement_value"]
        else:
            raise ValueError(f"unsupported replacement range policy: {policy}")
        record.update(
            version=replacement["version"],
            resolved=replacement["url"],
            integrity=replacement["integrity"],
        )
    result[PACKAGE_JSON] = (_encoded_json(package_json), entries[PACKAGE_JSON][1])
    result[SHRINKWRAP] = (_encoded_json(shrinkwrap), entries[SHRINKWRAP][1])
    changed = {path for path in entries if entries[path][0] != result[path][0]}
    if changed != set(ALLOWED_MUTATIONS):
        raise ValueError(f"repackaging changed an unexpected file set: {sorted(changed)}")
    return result


def _write_canonical_archive(entries: ArchiveEntries, destination: Path) -> None:
    with (
        destination.open("wb") as raw,
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


def _verify_local_material(path: Path, material: dict[str, Any]) -> None:
    data = path.read_bytes()
    for expected, actual, label in (
        (str(material["integrity"]), _integrity(data), "integrity"),
        (str(material["sha256"]), _sha256(data), "SHA-256"),
        (str(material["size"]), str(len(data)), "size"),
    ):
        if not hmac.compare_digest(expected, actual):
            raise ValueError(f"local material {label} mismatch for {path.name}")


def _build_once(manifest: dict[str, Any], work_root: Path) -> Path:
    upstream = work_root / "upstream.tgz"
    _download(manifest["upstream"], upstream)
    _verify_package_tarball(upstream, manifest["upstream"])
    base = DIST_DIR / manifest["base"]["filename"]
    _verify_local_material(base, manifest["base"])
    _verify_base_against_upstream(upstream, base, manifest)
    for index, item in enumerate(manifest["replacements"]):
        for kind in ("source", "replacement"):
            package = work_root / f"{index}-{kind}.tgz"
            _download(item[kind], package)
            _verify_package_tarball(package, item[kind])
    output_filename = manifest["output"]["filename"]
    if not isinstance(output_filename, str):
        raise ValueError("output filename must be a string")
    output = work_root / output_filename
    _write_canonical_archive(_patch_entries(_read_archive(base), manifest), output)
    return output


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
            temporary = tempfile.TemporaryDirectory(prefix="laundry-openclaw-repack-r2-")
            temporary_roots.append(temporary)
            built.append(_build_once(manifest, Path(temporary.name)))
        first_data = built[0].read_bytes()
        if len(built) == 2 and not hmac.compare_digest(first_data, built[1].read_bytes()):
            raise ValueError("independent OpenClaw repackage builds are not byte-identical")
        actual = _output_summary(built[0])
        expected = manifest["output"]
        for field in ("filename", "sha256", "integrity", "size"):
            if not hmac.compare_digest(str(actual[field]), str(expected[field])):
                raise ValueError(
                    "repacked OpenClaw output "
                    f"{field} does not match manifest; actual={actual[field]}"
                )
        destination = DIST_DIR / expected["filename"]
        destination.write_bytes(first_data)
        print(
            json.dumps(
                {
                    "status": "REPRODUCIBLE_DERIVED_EVAL_ONLY_REPACKAGE",
                    "output": actual,
                },
                indent=2,
            )
        )
    finally:
        for temporary in temporary_roots:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
