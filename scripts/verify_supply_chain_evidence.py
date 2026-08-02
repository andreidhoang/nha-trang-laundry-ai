"""Verify a release-bound supply-chain evidence bundle without granting authority."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from nha_trang_laundry_contracts import verify_supply_chain_evidence

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    candidate = argparse.ArgumentParser(description=__doc__)
    candidate.add_argument("--evidence", type=Path, required=True)
    candidate.add_argument("--artifact-root", type=Path, default=ROOT)
    candidate.add_argument("--expected-commit-sha", required=True)
    candidate.add_argument("--at")
    return candidate


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    current = (
        datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else datetime.now(UTC)
    )
    verified = verify_supply_chain_evidence(
        artifact_root=args.artifact_root,
        schema_root=ROOT,
        path=args.evidence,
        expected_commit_sha=args.expected_commit_sha,
        now=current,
    )
    print(
        json.dumps(
            {
                "status": "VERIFIED_SUPPLY_CHAIN_EVIDENCE",
                "evidence_id": verified.evidence_id,
                "image_count": len(verified.image_refs),
                "artifact_count": len(verified.verified_uris),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
