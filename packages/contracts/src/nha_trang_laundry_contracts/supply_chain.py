"""Fail-closed verification of release-bound software supply-chain evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker


class SupplyChainEvidenceError(ValueError):
    """Supply-chain evidence is malformed, stale, mismatched, or incomplete."""


@dataclass(frozen=True, slots=True)
class VerifiedSupplyChainEvidence:
    evidence_id: str
    release_commit_sha: str
    generated_at: datetime
    expires_at: datetime
    image_refs: tuple[str, ...]
    verified_uris: tuple[str, ...]


def verify_supply_chain_evidence(
    *,
    artifact_root: Path,
    schema_root: Path,
    path: Path,
    expected_commit_sha: str,
    now: datetime,
    maximum_age: timedelta = timedelta(hours=24),
) -> VerifiedSupplyChainEvidence:
    """Verify schema, freshness, commit/image bindings, and every referenced hash."""
    repository = artifact_root.resolve()
    evidence_path = path.resolve()
    _require_contained_file(repository, evidence_path, "supply-chain evidence")
    evidence = _load_json(evidence_path, "supply-chain evidence")
    _validate(evidence, schema_root / "specs/contracts/supply-chain-evidence-v2.schema.json")
    _require_every_lockfile_audited(repository, evidence)

    current = _aware(now, "verification time")
    generated_at = _timestamp(evidence["generated_at"], "generated_at")
    expires_at = _timestamp(evidence["expires_at"], "expires_at")
    if generated_at > current:
        raise SupplyChainEvidenceError("supply-chain evidence is from the future")
    if current >= expires_at:
        raise SupplyChainEvidenceError("supply-chain evidence is expired")
    if expires_at <= generated_at or expires_at - generated_at > maximum_age:
        raise SupplyChainEvidenceError("supply-chain evidence freshness window is invalid")
    if current - generated_at > maximum_age:
        raise SupplyChainEvidenceError("supply-chain evidence is stale")
    if evidence["release_commit_sha"] != expected_commit_sha:
        raise SupplyChainEvidenceError("supply-chain evidence commit does not match release")

    secret_scan = cast(dict[str, Any], evidence["secret_scan"])
    if secret_scan["scanned_commit_sha"] != expected_commit_sha:
        raise SupplyChainEvidenceError("secret scan commit does not match release")

    verified_uris: list[str] = []
    secret_report = cast(dict[str, str], secret_scan["report"])
    _verify_artifact(repository, secret_report)
    verified_uris.append(secret_report["uri"])
    for audit_name in ("dependency_audit", "license_audit"):
        audit = cast(dict[str, Any], evidence[audit_name])
        for artifact in cast(list[dict[str, str]], audit["lockfiles"]):
            _verify_artifact(repository, artifact)
            verified_uris.append(artifact["uri"])
        report_values = audit["reports"] if audit_name == "dependency_audit" else [audit["report"]]
        for report in cast(list[dict[str, str]], report_values):
            _verify_artifact(repository, report)
            verified_uris.append(report["uri"])

    image_refs: list[str] = []
    for image in cast(list[dict[str, Any]], evidence["images"]):
        image_ref = cast(str, image["image_ref"])
        if image_ref in image_refs:
            raise SupplyChainEvidenceError("duplicate image reference in supply-chain evidence")
        scan_artifact = cast(dict[str, str], image["scan_evidence"])
        scan_path = _verify_artifact(repository, scan_artifact)
        scan = _load_json(scan_path, "container scan evidence")
        _validate(scan, schema_root / "specs/contracts/container-scan-evidence-v1.schema.json")
        if scan["image_ref"] != image_ref:
            raise SupplyChainEvidenceError("container scan image digest does not match bundle")
        scanned_at = _timestamp(scan["scanned_at"], "container scan scanned_at")
        if scanned_at > generated_at or generated_at - scanned_at > maximum_age:
            raise SupplyChainEvidenceError("container scan evidence is stale or future-dated")
        sbom = cast(dict[str, str], scan["sbom"])
        _verify_artifact(repository, sbom)
        image_refs.append(image_ref)
        verified_uris.extend((scan_artifact["uri"], sbom["uri"]))

    for waiver in cast(list[dict[str, Any]], evidence["waivers"]):
        approved_at = _timestamp(waiver["approved_at"], "waiver approved_at")
        waiver_expiry = _timestamp(waiver["expires_at"], "waiver expires_at")
        if approved_at > generated_at or waiver_expiry <= current:
            raise SupplyChainEvidenceError("supply-chain waiver is future-dated or expired")

    return VerifiedSupplyChainEvidence(
        evidence_id=cast(str, evidence["evidence_id"]),
        release_commit_sha=expected_commit_sha,
        generated_at=generated_at,
        expires_at=expires_at,
        image_refs=tuple(image_refs),
        verified_uris=tuple(dict.fromkeys(verified_uris)),
    )


#: Every dependency lockfile format this repository could carry, and the ecosystem whose advisory
#: audit must accompany it. A tree in any of them is audited by a report for that ecosystem.
LOCKFILE_ECOSYSTEMS: dict[str, str] = {
    "uv.lock": "python",
    "poetry.lock": "python",
    "Pipfile.lock": "python",
    "package-lock.json": "node",
    "pnpm-lock.yaml": "node",
    "yarn.lock": "node",
}
LOCKFILE_NAMES = frozenset(LOCKFILE_ECOSYSTEMS)
#: Skipped wherever they appear: version control, installed environments and caches, never sources.
_UNSCANNED_DIRECTORIES = frozenset({".git", ".venv", "node_modules", "__pycache__"})
#: Skipped only at the repository root: the release workflow's own generated output.
_UNSCANNED_ROOT_DIRECTORIES = frozenset({"artifacts"})


def repository_lockfiles(repository: Path) -> frozenset[str]:
    """Every lockfile under `repository`, as repository-relative POSIX paths."""

    found: set[str] = set()
    for path in repository.rglob("*"):
        relative = path.relative_to(repository)
        if _UNSCANNED_DIRECTORIES.intersection(relative.parts) or (
            relative.parts[0] in _UNSCANNED_ROOT_DIRECTORIES
        ):
            continue
        if path.name in LOCKFILE_NAMES and path.is_file():
            found.add(relative.as_posix())
    return frozenset(found)


def _require_every_lockfile_audited(repository: Path, evidence: Mapping[str, Any]) -> None:
    """The audited lockfiles are exactly the repository's: none missing, none stale.

    `OPENCLAW-RETIRE-001`. Schema version 1 asked for "at least two", which was the number of
    dependency trees the repository happened to have. That count could not notice a third tree
    added without an audit, and could never be met once the second was retired.
    """

    present = repository_lockfiles(repository)
    if not present:
        raise SupplyChainEvidenceError("no dependency lockfile found in the repository")
    for audit_name in ("dependency_audit", "license_audit"):
        audit = cast(dict[str, Any], evidence[audit_name])
        audited = {artifact["uri"] for artifact in cast(list[dict[str, str]], audit["lockfiles"])}
        if audited != present:
            missing = sorted(present - audited)
            stale = sorted(audited - present)
            raise SupplyChainEvidenceError(
                f"{audit_name} does not cover the repository's lockfiles; "
                f"missing={missing}, not in repository={stale}"
            )
    # Listing a lockfile is not auditing it: every ecosystem present needs its own advisory report.
    needed = {LOCKFILE_ECOSYSTEMS[Path(uri).name] for uri in present}
    dependency_audit = cast(dict[str, Any], evidence["dependency_audit"])
    reported = {report["ecosystem"] for report in dependency_audit["reports"]}
    if needed - reported:
        raise SupplyChainEvidenceError(
            f"dependency_audit has no advisory report for ecosystem(s) {sorted(needed - reported)}"
        )


def _validate(value: Mapping[str, object], schema_path: Path) -> None:
    schema = _load_json(schema_path, "JSON schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise SupplyChainEvidenceError("supply-chain JSON schema is invalid") from error
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise SupplyChainEvidenceError(f"supply-chain evidence violates schema: {details}")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SupplyChainEvidenceError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise SupplyChainEvidenceError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _verify_artifact(root: Path, artifact: Mapping[str, str]) -> Path:
    uri = artifact["uri"]
    if "://" in uri or uri.startswith("repo:"):
        raise SupplyChainEvidenceError("supply-chain artifacts must use repository-relative URIs")
    path = (root / uri).resolve()
    _require_contained_file(root, path, uri)
    actual = f"sha256:{sha256(path.read_bytes()).hexdigest()}"
    if actual != artifact["sha256"]:
        raise SupplyChainEvidenceError(f"supply-chain artifact hash mismatch: {uri}")
    return path


def _require_contained_file(root: Path, path: Path, label: str) -> None:
    if path == root or root not in path.parents or not path.is_file():
        raise SupplyChainEvidenceError(f"{label} is missing or escapes repository")


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise SupplyChainEvidenceError(f"{label} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SupplyChainEvidenceError(f"{label} is not a valid timestamp") from error
    return _aware(parsed, label)


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplyChainEvidenceError(f"{label} must include a timezone")
    return value.astimezone(UTC)
