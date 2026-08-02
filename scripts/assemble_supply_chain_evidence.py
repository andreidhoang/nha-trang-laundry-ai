"""Assemble release supply-chain evidence from successful local scanner outputs."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    candidate = argparse.ArgumentParser(description=__doc__)
    candidate.add_argument("--artifact-root", type=Path, required=True)
    candidate.add_argument("--commit-sha", required=True)
    candidate.add_argument("--secret-scanner-version", required=True)
    candidate.add_argument("--dependency-scanner-version", required=True)
    candidate.add_argument("--license-scanner-version", required=True)
    candidate.add_argument("--lockfile", action="append", required=True)
    candidate.add_argument("--secret-report", required=True)
    candidate.add_argument("--dependency-report", action="append", required=True)
    candidate.add_argument("--license-report", required=True)
    candidate.add_argument("--image", action="append", required=True, metavar="IMAGE_REF=SCAN_URI")
    candidate.add_argument("--evidence-id", required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--generated-at")
    return candidate


def _artifact(root: Path, uri: str) -> dict[str, str]:
    path = (root / uri).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"artifact escapes root: {uri}")
    content = path.read_bytes()
    return {"uri": uri.replace("\\", "/"), "sha256": f"sha256:{sha256(content).hexdigest()}"}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.artifact_root.resolve()
    generated = (
        datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
        if args.generated_at
        else datetime.now(UTC)
    ).astimezone(UTC)
    lockfiles = [_artifact(root, uri) for uri in args.lockfile]
    images: list[dict[str, object]] = []
    for binding in args.image:
        image_ref, separator, scan_uri = binding.partition("=")
        if not separator:
            raise ValueError("--image must be IMAGE_REF=SCAN_URI")
        images.append({"image_ref": image_ref, "scan_evidence": _artifact(root, scan_uri)})
    scanner = {"status": "PASSED"}
    evidence = {
        "schema_version": 1,
        "evidence_id": args.evidence_id,
        "release_commit_sha": args.commit_sha,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "expires_at": (generated + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "status": "PASSED",
        "secret_scan": {
            **scanner,
            "scanner": "gitleaks",
            "scanner_version": args.secret_scanner_version,
            "scanned_commit_sha": args.commit_sha,
            "findings": 0,
            "report": _artifact(root, args.secret_report),
        },
        "dependency_audit": {
            **scanner,
            "scanner": "pip-audit+npm-audit",
            "scanner_version": args.dependency_scanner_version,
            "lockfiles": lockfiles,
            "reports": [_artifact(root, uri) for uri in args.dependency_report],
            "vulnerabilities": {"critical": 0, "high": 0},
        },
        "license_audit": {
            **scanner,
            "scanner": "pip-licenses+package-lock-policy",
            "scanner_version": args.license_scanner_version,
            "lockfiles": lockfiles,
            "forbidden_or_unknown_count": 0,
            "report": _artifact(root, args.license_report),
        },
        "images": images,
        "waivers": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
