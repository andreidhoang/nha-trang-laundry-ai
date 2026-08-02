"""Normalize Docker Scout SARIF and an SPDX SBOM into strict container-scan evidence."""

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
    sbom_bytes = args.sbom.read_bytes()
    sbom = json.loads(sbom_bytes)
    if sbom.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("SBOM is not SPDX 2.3 JSON")
    expected_digest = args.image_ref.rsplit("@", maxsplit=1)[1]
    locators = {
        reference.get("referenceLocator")
        for package in sbom.get("packages", [])
        if isinstance(package, dict)
        for reference in package.get("externalRefs", [])
        if isinstance(reference, dict)
    }
    if not any(
        isinstance(locator, str) and f"@{expected_digest}" in locator for locator in locators
    ):
        raise ValueError("SBOM is not bound to --image-ref digest")
    scanned_at = args.scanned_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    evidence = {
        "schema_version": 1,
        "evidence_id": args.evidence_id,
        "image_ref": args.image_ref,
        "scanner": "docker-scout",
        "scanner_version": args.scanner_version,
        "scanned_at": scanned_at,
        "status": "PASSED",
        "vulnerabilities": {"critical": 0, "high": 0},
        "sbom": {
            "format": "SPDX_JSON",
            "uri": args.sbom_uri,
            "sha256": f"sha256:{sha256(sbom_bytes).hexdigest()}",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
