"""Verify an externally supplied release candidate without granting or generating authority."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from nha_trang_laundry_contracts import (
    ReleaseCapability,
    RepositoryArtifactResolver,
    SupplyChainEvidenceError,
    load_and_verify_release_manifest,
    load_trusted_release_signers,
    verify_supply_chain_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--at must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--at must include a timezone")
    return parsed.astimezone(UTC)


def parser() -> argparse.ArgumentParser:
    candidate = argparse.ArgumentParser(description=__doc__)
    candidate.add_argument("--manifest", type=Path, required=True)
    candidate.add_argument("--trusted-signers", type=Path, required=True)
    candidate.add_argument("--trusted-signers-sha256", required=True)
    candidate.add_argument("--expected-commit-sha", required=True)
    candidate.add_argument("--stage", choices=("SHADOW", "ASSISTED", "BOUNDED"), required=True)
    candidate.add_argument(
        "--capability",
        choices=tuple(capability.value for capability in ReleaseCapability),
        required=True,
    )
    candidate.add_argument("--artifact-root", type=Path, default=ROOT)
    candidate.add_argument("--supply-chain-evidence", type=Path, required=True)
    candidate.add_argument("--at", type=_timestamp)
    return candidate


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    current = args.at or datetime.now(UTC)
    trusted_signers = load_trusted_release_signers(
        root=ROOT,
        path=args.trusted_signers.resolve(),
        expected_sha256=args.trusted_signers_sha256,
    )
    authorization = load_and_verify_release_manifest(
        root=ROOT,
        path=args.manifest.resolve(),
        trusted_signers=trusted_signers,
        expected_commit_sha=args.expected_commit_sha,
        expected_stage=args.stage,
        expected_capability=ReleaseCapability(args.capability),
        now=current,
        artifact_resolver=RepositoryArtifactResolver(args.artifact_root),
    )
    artifact_root = args.artifact_root.resolve()
    evidence_path = args.supply_chain_evidence.resolve()
    try:
        evidence_relative = evidence_path.relative_to(artifact_root).as_posix()
    except ValueError as error:
        raise SupplyChainEvidenceError(
            "supply-chain evidence escapes the release artifact root"
        ) from error
    signed_paths = {_repository_path(uri) for uri in authorization.verified_uris}
    if evidence_relative not in signed_paths:
        raise SupplyChainEvidenceError(
            "supply-chain evidence is not hash-bound by the signed release manifest"
        )
    supply_chain = verify_supply_chain_evidence(
        artifact_root=artifact_root,
        schema_root=ROOT,
        path=evidence_path,
        expected_commit_sha=args.expected_commit_sha,
        now=current,
    )
    print(
        json.dumps(
            {
                "status": "VERIFIED_RELEASE_AUTHORIZATION",
                "release_id": authorization.release_id,
                "commit_sha": authorization.commit_sha,
                "stage": authorization.stage,
                "capability": authorization.capability.value,
                "starts_at": authorization.starts_at.isoformat().replace("+00:00", "Z"),
                "expires_at": authorization.expires_at.isoformat().replace("+00:00", "Z"),
                "payload_hash": authorization.payload_hash,
                "verified_artifact_count": len(authorization.verified_uris),
                "supply_chain_evidence_id": supply_chain.evidence_id,
                "verified_image_count": len(supply_chain.image_refs),
            },
            indent=2,
        )
    )
    return 0


def _repository_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "repo" and not parsed.netloc:
        return parsed.path.lstrip("/")
    if not parsed.scheme:
        return uri.replace("\\", "/").lstrip("/")
    return ""


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"release candidate verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
