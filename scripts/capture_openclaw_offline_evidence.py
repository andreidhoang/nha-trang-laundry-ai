"""Capture sanitized, explicitly non-release OpenClaw offline verification evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from nha_trang_laundry_contracts import (
    load_public_runtime_registry,
    verify_public_runtime_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evidence/agent-shadow/openclaw-offline-verification-v1.json"
PINNED_ARTIFACTS = (
    "runtime/model-registry-v1.yaml",
    "runtime/openclaw/public-cell/openclaw.json5",
    "runtime/openclaw/public-cell/plugin-inventory-v1.json",
    "runtime/openclaw/public-cell/plugin/package-lock.json",
    "runtime/openclaw/repack/manifest-v1.json",
    "runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r1.tgz",
    "runtime/openclaw/repack/manifest-v2.json",
    "runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r2.tgz",
    "evidence/provider/openai-data-controls-review-v1.yaml",
    "specs/contracts/capability-status-v1.schema.json",
    "specs/contracts/container-scan-evidence-v1.schema.json",
    "specs/contracts/openclaw-cross-platform-result-v1.schema.json",
    "specs/contracts/provider-data-evidence-v1.schema.json",
    ".github/workflows/release-supply-chain.yml",
    "scripts/verify_agent_runtime.py",
    "scripts/build_openclaw_repackage.py",
    "scripts/verify_openclaw_repackage.py",
    "scripts/verify_openclaw_cross_platform.py",
    "scripts/capture_openclaw_offline_evidence.py",
)


def _sha256(path: Path) -> str:
    """Hash repository bytes after Git has enforced the declared checkout line endings."""

    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _validated_result(value: Any, *, returncode: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("OpenClaw verification output must be an object")
    registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v1.yaml")
    expected_artifact_count = len(verify_public_runtime_artifacts(ROOT, registry))
    base_blockers = list(registry.release_blockers())
    common_invalid = (
        value.get("openclaw_version") != registry.openclaw.version
        or not isinstance(value.get("openclaw_build_revision"), str)
        or re.fullmatch(r"[0-9a-f]{7,40}", value["openclaw_build_revision"]) is None
        or value.get("tool_count") != 10
        or value.get("artifact_count") != expected_artifact_count
        or value.get("security_audit_critical") != 0
        or value.get("real_customer_data_allowed") is not False
        or not isinstance(value.get("dependency_audit_critical"), int)
        or not isinstance(value.get("dependency_audit_high"), int)
    )
    verified = (
        returncode == 0
        and value.get("status") == "EVAL_ONLY_VERIFIED"
        and value.get("dependency_audit_critical") == 0
        and value.get("dependency_audit_high") == 0
        and value.get("release_blockers") == base_blockers
    )
    blocked = (
        returncode == 2
        and value.get("status") == "EVAL_ONLY_BLOCKED"
        and (
            value.get("dependency_audit_critical", 0) > 0
            or value.get("dependency_audit_high", 0) > 0
        )
        and value.get("release_blockers") == [*base_blockers, "OPENCLAW_DEPENDENCY_AUDIT_HIGH"]
    )
    if common_invalid or not (verified or blocked):
        raise ValueError("OpenClaw verification output violates the non-release evidence contract")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        raise SystemExit("output must remain inside the repository")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_agent_runtime.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode not in {0, 2}:
        raise SystemExit("OpenClaw offline verification failed without a capturable result")
    try:
        raw_result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit("OpenClaw offline verification returned malformed JSON") from error
    result = _validated_result(raw_result, returncode=completed.returncode)
    evidence = {
        "schema_version": 1,
        "evidence_type": "OPENCLAW_OFFLINE_NON_RELEASE",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "command": "uv run python scripts/verify_agent_runtime.py",
        "release_effect": "NONE",
        "provider_request_executed": False,
        "verification_exit_code": completed.returncode,
        "artifact_hashes": {path: _sha256(ROOT / path) for path in PINNED_ARTIFACTS},
        "result": result,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
