"""Create and verify typed OpenClaw hosted cross-platform build results."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import shutil
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "runtime/openclaw/repack/manifest-v2.json"
SCHEMA_PATH = ROOT / "specs/contracts/openclaw-cross-platform-result-v1.schema.json"
MANIFEST_SCHEMA_PATH = ROOT / "specs/contracts/openclaw-repackage-manifest-v2.schema.json"
EXPECTED_ARTIFACT = "openclaw-2026.7.1-2-nha-trang-r2.tgz"


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _integrity(data: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    return f"sha512-{encoded}"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"typed result must be an object: {path.name}")
    return cast(dict[str, Any], value)


def _schema() -> dict[str, Any]:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return schema


def _validate(value: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(_schema()).iter_errors(value), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ValueError(f"OpenClaw cross-platform result violates schema: {details}")


def _manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = _load_json(path)
    manifest_schema = _load_json(MANIFEST_SCHEMA_PATH)
    Draft202012Validator.check_schema(manifest_schema)
    errors = sorted(
        Draft202012Validator(manifest_schema).iter_errors(value), key=lambda error: list(error.path)
    )
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ValueError(f"OpenClaw comparison manifest violates schema: {details}")
    if value.get("schema_version") != 2:
        raise ValueError("OpenClaw comparison requires manifest-v2")
    if value.get("artifact_origin") != "DERIVED" or value.get("artifact_status") != "EVAL_ONLY":
        raise ValueError("OpenClaw comparison requires a DERIVED EVAL_ONLY manifest")
    activation = value.get("activation")
    if not isinstance(activation, dict) or any(activation.values()):
        raise ValueError("OpenClaw comparison manifest activation must remain disabled")
    return value, _sha256(raw)


def _artifact(path: Path) -> dict[str, Any]:
    if path.name != EXPECTED_ARTIFACT:
        raise ValueError("OpenClaw comparison artifact filename drifted")
    data = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": _sha256(data),
        "integrity": _integrity(data),
        "size": len(data),
    }


def _verify_manifest_artifact(artifact: dict[str, Any], manifest: dict[str, Any]) -> None:
    output = manifest.get("output")
    upstream = manifest.get("upstream")
    if not isinstance(output, dict) or artifact != output:
        raise ValueError("OpenClaw hosted artifact does not match manifest-v2 output")
    if not isinstance(upstream, dict) or not isinstance(upstream.get("sha256"), str):
        raise ValueError("OpenClaw manifest-v2 upstream source digest is missing")


def platform_result(
    *,
    artifact_path: Path,
    manifest_path: Path,
    commit_sha: str,
    runner_os: str,
    runner_arch: str,
    runner_name: str,
    runner_image: str,
    runner_image_version: str,
) -> dict[str, Any]:
    manifest, manifest_sha256 = _manifest(manifest_path)
    artifact = _artifact(artifact_path)
    _verify_manifest_artifact(artifact, manifest)
    result = {
        "schema_version": 1,
        "record_type": "PLATFORM_RESULT",
        "status": "VERIFIED_DERIVED_EVAL_ONLY_REPACKAGE",
        "release_effect": "NONE",
        "release_commit_sha": commit_sha,
        "runner": {
            "os": runner_os,
            "arch": runner_arch,
            "name": runner_name,
            "image_os": runner_image,
            "image_version": runner_image_version,
        },
        "manifest_sha256": manifest_sha256,
        "upstream_source_sha256": manifest["upstream"]["sha256"],
        "artifact": artifact,
    }
    _validate(result)
    return result


def comparison_result(
    *,
    windows_result_path: Path,
    windows_artifact_path: Path,
    linux_result_path: Path,
    linux_artifact_path: Path,
    manifest_path: Path,
    expected_commit: str,
) -> dict[str, Any]:
    windows = _load_json(windows_result_path)
    linux = _load_json(linux_result_path)
    _validate(windows)
    _validate(linux)
    if windows.get("record_type") != "PLATFORM_RESULT" or linux.get("record_type") != (
        "PLATFORM_RESULT"
    ):
        raise ValueError("comparison inputs must both be platform results")
    if windows.get("runner", {}).get("os") != "Windows":
        raise ValueError("Windows platform result identity is missing")
    if linux.get("runner", {}).get("os") != "Linux":
        raise ValueError("Linux platform result identity is missing")
    if (
        windows.get("release_commit_sha") != expected_commit
        or linux.get("release_commit_sha") != expected_commit
    ):
        raise ValueError("platform results are not bound to the expected Git commit")
    manifest, manifest_sha256 = _manifest(manifest_path)
    windows_artifact = _artifact(windows_artifact_path)
    linux_artifact = _artifact(linux_artifact_path)
    _verify_manifest_artifact(windows_artifact, manifest)
    _verify_manifest_artifact(linux_artifact, manifest)
    windows_bytes = windows_artifact_path.read_bytes()
    linux_bytes = linux_artifact_path.read_bytes()
    if not hmac.compare_digest(windows_bytes, linux_bytes):
        raise ValueError("Windows and Linux OpenClaw artifacts are not byte-identical")
    shared = {
        "manifest_sha256": manifest_sha256,
        "upstream_source_sha256": manifest["upstream"]["sha256"],
        "artifact": windows_artifact,
    }
    for label, platform in (("Windows", windows), ("Linux", linux)):
        if any(platform.get(key) != value for key, value in shared.items()):
            raise ValueError(f"{label} platform metadata does not match manifest-bound bytes")
    result = {
        "schema_version": 1,
        "record_type": "CROSS_PLATFORM_COMPARISON",
        "status": "BYTE_IDENTICAL",
        "release_effect": "NONE",
        "release_commit_sha": expected_commit,
        **shared,
        "windows": windows,
        "linux": linux,
        "comparison": {
            "method": "BYTE_FOR_BYTE",
            "bytes_equal": True,
            "metadata_equal": True,
            "compared_size": len(windows_bytes),
        },
    }
    _validate(result)
    return result


def verify_comparison(
    *, comparison_path: Path, artifact_path: Path, manifest_path: Path, expected_commit: str
) -> dict[str, Any]:
    result = _load_json(comparison_path)
    _validate(result)
    if result.get("record_type") != "CROSS_PLATFORM_COMPARISON":
        raise ValueError("hosted comparison record type is invalid")
    if result.get("release_commit_sha") != expected_commit:
        raise ValueError("hosted comparison is not bound to the expected Git commit")
    manifest, manifest_sha256 = _manifest(manifest_path)
    artifact = _artifact(artifact_path)
    _verify_manifest_artifact(artifact, manifest)
    expected = {
        "manifest_sha256": manifest_sha256,
        "upstream_source_sha256": manifest["upstream"]["sha256"],
        "artifact": artifact,
    }
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("hosted comparison does not bind the supplied r2 artifact")
    for label, expected_os in (("windows", "Windows"), ("linux", "Linux")):
        platform = result.get(label)
        if not isinstance(platform, dict):
            raise ValueError(f"hosted comparison {label} result is missing")
        if platform.get("release_commit_sha") != expected_commit:
            raise ValueError(f"hosted comparison {label} commit binding drifted")
        if platform.get("runner", {}).get("os") != expected_os:
            raise ValueError(f"hosted comparison {label} runner identity drifted")
        if any(platform.get(key) != value for key, value in expected.items()):
            raise ValueError(f"hosted comparison {label} metadata binding drifted")
    if result.get("comparison") != {
        "method": "BYTE_FOR_BYTE",
        "bytes_equal": True,
        "metadata_equal": True,
        "compared_size": artifact["size"],
    }:
        raise ValueError("hosted comparison proof is incomplete")
    return result


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    emit = subparsers.add_parser("emit")
    emit.add_argument("--artifact", type=Path, required=True)
    emit.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    emit.add_argument("--commit-sha", required=True)
    emit.add_argument("--runner-os", choices=("Windows", "Linux"), required=True)
    emit.add_argument("--runner-arch", required=True)
    emit.add_argument("--runner-name", required=True)
    emit.add_argument("--runner-image", required=True)
    emit.add_argument("--runner-image-version", required=True)
    emit.add_argument("--output", type=Path, required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--windows-result", type=Path, required=True)
    compare.add_argument("--windows-artifact", type=Path, required=True)
    compare.add_argument("--linux-result", type=Path, required=True)
    compare.add_argument("--linux-artifact", type=Path, required=True)
    compare.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    compare.add_argument("--expected-commit", required=True)
    compare.add_argument("--output-result", type=Path, required=True)
    compare.add_argument("--output-artifact", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--comparison", type=Path, required=True)
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    verify.add_argument("--expected-commit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "emit":
        result = platform_result(
            artifact_path=args.artifact,
            manifest_path=args.manifest,
            commit_sha=args.commit_sha,
            runner_os=args.runner_os,
            runner_arch=args.runner_arch,
            runner_name=args.runner_name,
            runner_image=args.runner_image,
            runner_image_version=args.runner_image_version,
        )
        _write(args.output, result)
    elif args.command == "compare":
        result = comparison_result(
            windows_result_path=args.windows_result,
            windows_artifact_path=args.windows_artifact,
            linux_result_path=args.linux_result,
            linux_artifact_path=args.linux_artifact,
            manifest_path=args.manifest,
            expected_commit=args.expected_commit,
        )
        _write(args.output_result, result)
        args.output_artifact.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.windows_artifact, args.output_artifact)
    elif args.command == "verify":
        verify_comparison(
            comparison_path=args.comparison,
            artifact_path=args.artifact,
            manifest_path=args.manifest,
            expected_commit=args.expected_commit,
        )
    else:  # pragma: no cover - argparse constrains this branch
        raise ValueError("unsupported cross-platform command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
