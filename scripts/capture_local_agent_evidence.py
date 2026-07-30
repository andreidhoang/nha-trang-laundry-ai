"""Capture sanitized, explicitly non-release local agent-suite evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evidence/agent-shadow/local-synthetic-suite-v1.json"
PINNED_ARTIFACTS = (
    "specs/evals/eval-manifest-v1.yaml",
    "specs/evals/fixture-registry-v1.json",
    "specs/evals/assertion-registry-v1.json",
    "specs/contracts/agent-tools-v1.openapi.yaml",
    "specs/contracts/capability-status-v1.schema.json",
    "specs/contracts/container-scan-evidence-v1.schema.json",
    "specs/contracts/provider-data-evidence-v1.schema.json",
    "specs/contracts/release-gate-manifest-v1.schema.json",
    "specs/contracts/trusted-release-signers-v1.schema.json",
    "runtime/model-registry-v1.yaml",
    "runtime/openclaw/public-cell/openclaw.json5",
    "evidence/provider/openai-data-controls-review-v1.yaml",
    "packages/evals/src/nha_trang_laundry_evals/runner.py",
    "packages/evals/src/nha_trang_laundry_evals/graders.py",
    "packages/contracts/src/nha_trang_laundry_contracts/release_manifest.py",
    "apps/worker/src/nha_trang_laundry_worker/agent_runner.py",
    "scripts/capture_local_agent_evidence.py",
    "scripts/capture_openclaw_offline_evidence.py",
    "scripts/verify_agent_runtime.py",
    "scripts/verify_release_candidate.py",
    "evidence/agent-shadow/rollback-assessment-v1.yaml",
)


def _sha256(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _validate(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("local suite output must be an object")
    coverage = result.get("coverage")
    cases = result.get("cases")
    if (
        result.get("mode") != "synthetic-local-suite"
        or result.get("status") != "SKIP"
        or result.get("release_eligible") is not False
        or result.get("release_evidence") is not False
        or result.get("runtime_path") != "DETERMINISTIC_DEGRADED"
        or result.get("synthetic_signer_key_id") != "SYNTHETIC_EPHEMERAL_NOT_RELEASE"
        or not isinstance(coverage, dict)
        or coverage.get("manifest_cases") != coverage.get("executed_cases")
        or not isinstance(cases, list)
        or len(cases) != coverage.get("manifest_cases")
        or any(
            not isinstance(case, dict)
            or case.get("status") != "SKIP"
            or case.get("runtime_path") != "DETERMINISTIC_DEGRADED"
            for case in cases
        )
    ):
        raise ValueError("local suite output violates the non-release evidence contract")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output_path = Path(args.output).resolve()
    if ROOT not in output_path.parents:
        raise SystemExit("output must remain inside the repository")
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required for PostgreSQL-backed local evidence")
    command = [
        sys.executable,
        "-m",
        "nha_trang_laundry_evals.runner",
        "--manifest",
        "specs/evals/eval-manifest-v1.yaml",
        "--mode",
        "synthetic-local-suite",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or "local synthetic suite failed")
    result = _validate(json.loads(completed.stdout))
    evidence = {
        "schema_version": 1,
        "evidence_type": "LOCAL_SYNTHETIC_NON_RELEASE",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "command": "uv run python -m nha_trang_laundry_evals.runner --mode synthetic-local-suite",
        "release_effect": "NONE",
        "primary_provider_evidence": False,
        "artifact_hashes": {path: _sha256(ROOT / path) for path in PINNED_ARTIFACTS},
        "result": result,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(output_path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
