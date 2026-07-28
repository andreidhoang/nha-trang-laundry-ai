"""Schema-valid, explicitly non-release synthetic evaluation result records."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator, FormatChecker

from .graders import CaseGrade, ObservedCaseExecution


class EvalResultError(ValueError):
    """An evaluation result cannot be made schema-valid or internally coherent."""


class EphemeralSyntheticSigner:
    """In-memory signer for test records only; it is intentionally not release evidence."""

    key_id = "SYNTHETIC_EPHEMERAL_NOT_RELEASE"

    def __init__(self) -> None:
        self._private_key = Ed25519PrivateKey.generate()

    def sign(self, payload_hash: str) -> str:
        value = self._private_key.sign(payload_hash.encode("ascii"))
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def build_synthetic_result(
    *,
    root: Path,
    case: Mapping[str, Any],
    observed: ObservedCaseExecution,
    grade: CaseGrade,
    release_artifacts: Mapping[str, Path],
    signer: EphemeralSyntheticSigner | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Build a schema-valid record without representing it as signed release evidence."""

    if len(release_artifacts) < 6:
        raise EvalResultError("result schema requires at least six pinned runtime artifacts")
    current_time = now or datetime.now(UTC)
    finished_at = current_time.isoformat().replace("+00:00", "Z")
    case_result = {
        "case_id": case.get("id"),
        "case_version": case.get("version"),
        "runtime_path": "DETERMINISTIC_DEGRADED",
        "status": grade.status,
        "grader_results": [asdict(item) for item in grade.grader_results],
        "actual_tool_trace": [dict(item) for item in observed.tool_trace],
        "actual_side_effects": list(observed.side_effects),
        "trace_id": observed.trace_id,
    }
    summary = {
        "total": 1,
        "passed": int(grade.status == "PASS"),
        "failed": int(grade.status == "FAIL"),
        "skipped": int(grade.status == "SKIP"),
        "p0_failures": int(grade.status == "FAIL" and case.get("severity") == "P0"),
    }
    result: dict[str, Any] = {
        "schema_version": 1,
        "run_id": str(uuid4()),
        "manifest_hash": _file_hash(root / "specs/evals/eval-manifest-v1.yaml"),
        "case_schema_hash": _file_hash(root / "specs/evals/eval-case-v1.schema.json"),
        "fixture_registry_hash": _file_hash(root / "specs/evals/fixture-registry-v1.json"),
        "assertion_registry_hash": _file_hash(root / "specs/evals/assertion-registry-v1.json"),
        "release_artifacts": {
            artifact_id: _file_hash(path) for artifact_id, path in release_artifacts.items()
        },
        "started_at": finished_at,
        "finished_at": finished_at,
        "summary": summary,
        "case_results": [case_result],
    }
    payload_hash = f"sha256:{sha256(rfc8785.dumps(result)).hexdigest()}"
    active_signer = signer or EphemeralSyntheticSigner()
    result["signature"] = {
        "algorithm": "ED25519",
        "key_id": active_signer.key_id,
        "signed_payload_hash": payload_hash,
        "value": active_signer.sign(payload_hash),
    }
    validate_eval_result(root, result)
    return result


def validate_eval_result(root: Path, result: Mapping[str, object]) -> None:
    """Validate result shape and result-summary arithmetic; cryptographic trust is external."""

    schema_path = root / "specs/evals/eval-result-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(result), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise EvalResultError(f"eval result violates schema: {details}")
    summary = result.get("summary")
    case_results = result.get("case_results")
    if not isinstance(summary, Mapping) or not isinstance(case_results, Sequence):
        raise EvalResultError("eval result summary/case_results is invalid")
    statuses = [entry.get("status") for entry in case_results if isinstance(entry, Mapping)]
    if len(statuses) != len(case_results):
        raise EvalResultError("eval result case record is invalid")
    expected = {
        "total": len(statuses),
        "passed": statuses.count("PASS"),
        "failed": statuses.count("FAIL"),
        "skipped": statuses.count("SKIP"),
        "p0_failures": sum(
            entry.get("status") == "FAIL" and str(entry.get("case_id", "")).startswith("P0-")
            for entry in case_results
            if isinstance(entry, Mapping)
        ),
    }
    if any(summary.get(key) != value for key, value in expected.items()):
        raise EvalResultError("eval result summary does not match case results")


def _file_hash(path: Path) -> str:
    if not path.is_file():
        raise EvalResultError(f"required pinned artifact is missing: {path}")
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"
