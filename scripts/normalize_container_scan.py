"""Normalize scanner SARIF and a standard SBOM into strict container-scan evidence."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


def parser() -> argparse.ArgumentParser:
    candidate = argparse.ArgumentParser(description=__doc__)
    candidate.add_argument("--sarif", type=Path, required=True)
    candidate.add_argument("--sbom", type=Path, required=True)
    candidate.add_argument("--sbom-uri", required=True)
    candidate.add_argument("--image-ref", required=True)
    candidate.add_argument("--scanner", default="docker-scout")
    candidate.add_argument("--scanner-version", required=True)
    candidate.add_argument("--evidence-id", required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--scanned-at")
    return candidate


def _severity(result: dict[str, Any]) -> str:
    message = result.get("message", {})
    text = message.get("text", "") if isinstance(message, dict) else ""
    match = re.search(r"(?m)^Severity\s*:\s*(CRITICAL|HIGH)\s*$", str(text))
    return match.group(1) if match else "UNKNOWN"


def _sbom_binding(sbom: dict[str, Any]) -> tuple[str, set[str]]:
    if sbom.get("spdxVersion") == "SPDX-2.3":
        locators = {
            reference.get("referenceLocator")
            for package in sbom.get("packages", [])
            if isinstance(package, dict)
            for reference in package.get("externalRefs", [])
            if isinstance(reference, dict)
        }
        return "SPDX_JSON", {value for value in locators if isinstance(value, str)}
    if sbom.get("bomFormat") == "CycloneDX" and sbom.get("specVersion") in {
        "1.5",
        "1.6",
        "1.7",
    }:
        metadata = sbom.get("metadata", {})
        component = metadata.get("component", {}) if isinstance(metadata, dict) else {}
        properties = component.get("properties", []) if isinstance(component, dict) else []
        image_ids = {
            str(prop.get("value"))
            for prop in properties
            if isinstance(prop, dict) and prop.get("name") == "aquasecurity:trivy:ImageID"
        }
        return "CYCLONEDX_JSON", image_ids
    raise ValueError("SBOM is not supported SPDX 2.3 or CycloneDX JSON")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    sarif = json.loads(args.sarif.read_text(encoding="utf-8"))
    try:
        results = sarif["runs"][0].get("results", [])
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("scanner SARIF has no valid run") from error
    if not isinstance(results, list):
        raise ValueError("scanner SARIF results must be an array")
    severities = [_severity(result) for result in results if isinstance(result, dict)]
    unknown = severities.count("UNKNOWN") + (len(results) - len(severities))
    critical = severities.count("CRITICAL")
    high = severities.count("HIGH")
    if unknown:
        raise ValueError("scanner SARIF contains findings with unknown severity")
    if critical or high:
        raise ValueError(
            f"container scan failed: critical={critical}; high={high}; no waiver is automatic"
        )
    if not re.fullmatch(r"[a-z0-9._/-]+@sha256:[0-9a-f]{64}", args.image_ref):
        raise ValueError("--image-ref must be an immutable lowercase sha256 reference")
    expected_digest = args.image_ref.rsplit("@", maxsplit=1)[1]
    run_properties = sarif["runs"][0].get("properties", {})
    sarif_image_id = run_properties.get("imageID") if isinstance(run_properties, dict) else None
    if args.scanner == "trivy" and sarif_image_id is None:
        raise ValueError("Trivy SARIF has no imageID binding")
    if sarif_image_id is not None and sarif_image_id != expected_digest:
        raise ValueError("scanner SARIF is not bound to --image-ref digest")
    sbom_bytes = args.sbom.read_bytes()
    sbom = json.loads(sbom_bytes)
    sbom_format, bindings = _sbom_binding(sbom)
    if not any(
        binding == expected_digest or f"@{expected_digest}" in binding for binding in bindings
    ):
        raise ValueError("SBOM is not bound to --image-ref digest")
    scanned_at = args.scanned_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    evidence = {
        "schema_version": 1,
        "evidence_id": args.evidence_id,
        "image_ref": args.image_ref,
        "scanner": args.scanner,
        "scanner_version": args.scanner_version,
        "scanned_at": scanned_at,
        "status": "PASSED",
        "vulnerabilities": {"critical": 0, "high": 0},
        "sbom": {
            "format": sbom_format,
            "uri": args.sbom_uri,
            "sha256": f"sha256:{sha256(sbom_bytes).hexdigest()}",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
