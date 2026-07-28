"""Offline evaluation entry point with an explicit non-release contract mode."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg
import yaml
from nha_trang_laundry_db.migrations import apply_migrations

from .fixtures import load_synthetic_fixture
from .graders import ObservedCaseExecution, grade_case
from .manifest import validate_eval_manifest
from .results import build_synthetic_result
from .synthetic_approval import execute_post_approval_edit_preflight
from .synthetic_facade import (
    execute_bound_clean_request_preflight,
    execute_bound_request_idor_preflight,
    execute_public_status_idor_preflight,
)
from .synthetic_manual_send import execute_manual_worker_double_send_preflight
from .synthetic_timeout import execute_model_timeout_preflight

ROOT = Path(__file__).resolve().parents[4]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default="specs/evals/eval-manifest-v1.yaml",
        help="Repository-relative evaluation manifest",
    )
    parser.add_argument(
        "--mode",
        choices=(
            "contract",
            "synthetic-tool-escape",
            "synthetic-model-timeout",
            "synthetic-bound-request-idor",
            "synthetic-public-status-idor",
            "synthetic-post-approval-edit",
            "synthetic-manual-worker-double-send",
        ),
        default="contract",
        help=(
            "Contract validation or one fixed-facade synthetic preflight; "
            "neither authorizes release"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = (ROOT / args.manifest).resolve()
    if args.mode == "synthetic-tool-escape":
        print(json.dumps(_synthetic_tool_escape_result(manifest), indent=2))
        return
    if args.mode == "synthetic-model-timeout":
        print(json.dumps(_synthetic_model_timeout_result(manifest), indent=2))
        return
    if args.mode == "synthetic-bound-request-idor":
        print(json.dumps(_synthetic_bound_request_idor_result(manifest), indent=2))
        return
    if args.mode == "synthetic-public-status-idor":
        print(json.dumps(_synthetic_public_status_idor_result(manifest), indent=2))
        return
    if args.mode == "synthetic-post-approval-edit":
        print(json.dumps(_synthetic_post_approval_edit_result(manifest), indent=2))
        return
    if args.mode == "synthetic-manual-worker-double-send":
        print(json.dumps(_synthetic_manual_worker_double_send_result(manifest), indent=2))
        return
    report = validate_eval_manifest(ROOT, manifest)
    print(json.dumps({"mode": args.mode, "status": "PASS", **asdict(report)}, indent=2))


def _synthetic_tool_escape_result(manifest_path: Path) -> dict[str, object]:
    """Run one local facade proof, preserving its SKIP status for unavailable primary grading."""

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (
            candidate
            for candidate in manifest["cases"]
            if isinstance(candidate, dict) and candidate.get("id") == "P0-PROMPT-INJECTION-TOOLS"
        ),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 prompt-injection case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    fixture_entry = _fixture_entry(registry, "fixture:bound_clean_request:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_clean_request:v1",
        version=fixture_entry["version"],
        payload_path=fixture_entry["payload_path"],
        payload_sha256=fixture_entry["payload_sha256"],
    )
    preflight = execute_bound_clean_request_preflight(fixture)
    observed = ObservedCaseExecution(
        policy_outcome="REQUIRE_HUMAN",
        message_kind="INTAKE_FACT_REQUEST",
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=(),
        trace_id=preflight.trace_id,
        cross_contact_path_rejected=preflight.cross_contact_path_rejected,
    )
    grade = grade_case(case, observed)
    result = build_synthetic_result(
        root=ROOT,
        case=case,
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )
    return {
        "mode": "synthetic-tool-escape",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _fixture_entry(registry: Any, fixture_id: str) -> dict[str, Any]:
    if not isinstance(registry, dict) or not isinstance(registry.get("fixtures"), list):
        raise ValueError("fixture registry is invalid")
    entry = next(
        (
            candidate
            for candidate in registry["fixtures"]
            if isinstance(candidate, dict) and candidate.get("fixture_id") == fixture_id
        ),
        None,
    )
    if (
        not isinstance(entry, dict)
        or entry.get("status") != "IMPLEMENTED"
        or not isinstance(entry.get("version"), int)
        or not isinstance(entry.get("payload_path"), str)
        or not isinstance(entry.get("payload_sha256"), str)
    ):
        raise ValueError(f"fixture is not implemented and pinned: {fixture_id}")
    return entry


def _synthetic_model_timeout_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (
            candidate
            for candidate in manifest["cases"]
            if isinstance(candidate, dict) and candidate.get("id") == "P0-MODEL-TIMEOUT"
        ),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 model-timeout case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    fixture_entry = _fixture_entry(registry, "fixture:model_wall_clock_exceeds_20_seconds:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:model_wall_clock_exceeds_20_seconds:v1",
        version=fixture_entry["version"],
        payload_path=fixture_entry["payload_path"],
        payload_sha256=fixture_entry["payload_sha256"],
    )
    preflight = execute_model_timeout_preflight(fixture)
    observed = ObservedCaseExecution(
        policy_outcome="REQUIRE_HUMAN",
        message_kind=None,
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        timed_out_within_limit=preflight.timed_out_within_limit,
        automatic_fallback_message_created=preflight.automatic_fallback_message_created,
        inbox_event_recoverable=preflight.inbox_event_recoverable,
    )
    grade = grade_case(
        case,
        observed,
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )
    result = build_synthetic_result(
        root=ROOT,
        case=case,
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )
    return {
        "mode": "synthetic-model-timeout",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_bound_request_idor_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (
            candidate
            for candidate in manifest["cases"]
            if isinstance(candidate, dict) and candidate.get("id") == "P0-BOUND-REQUEST-IDOR"
        ),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 bound-request IDOR case is missing")
    preflight = execute_bound_request_idor_preflight()
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind=None,
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=(),
        trace_id=preflight.trace_id,
        bound_request_path_rejected=preflight.bound_request_path_rejected,
        quote_revision_created=preflight.quote_revision_created,
    )
    grade = grade_case(
        case,
        observed,
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )
    result = build_synthetic_result(
        root=ROOT,
        case=case,
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )
    return {
        "mode": "synthetic-bound-request-idor",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_public_status_idor_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (
            candidate
            for candidate in manifest["cases"]
            if isinstance(candidate, dict) and candidate.get("id") == "P0-PUBLIC-STATUS-IDOR"
        ),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 public-status IDOR case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    attacker_entry = _fixture_entry(registry, "fixture:attacker_contact:v1")
    other_order_entry = _fixture_entry(registry, "fixture:valid_code_owned_by_other_contact:v1")
    attacker_fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:attacker_contact:v1",
        version=attacker_entry["version"],
        payload_path=attacker_entry["payload_path"],
        payload_sha256=attacker_entry["payload_sha256"],
    )
    other_order_fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:valid_code_owned_by_other_contact:v1",
        version=other_order_entry["version"],
        payload_path=other_order_entry["payload_path"],
        payload_sha256=other_order_entry["payload_sha256"],
    )
    preflight = execute_public_status_idor_preflight(attacker_fixture, other_order_fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="ORDER_STATUS",
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=(),
        trace_id=preflight.trace_id,
        generic_unavailable_response=preflight.generic_unavailable_response,
        ownership_fact_leaked=preflight.ownership_fact_leaked,
        public_code_redacted_from_trace=preflight.public_code_redacted_from_trace,
    )
    grade = grade_case(
        case,
        observed,
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )
    result = build_synthetic_result(
        root=ROOT,
        case=case,
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )
    return {
        "mode": "synthetic-public-status-idor",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_post_approval_edit_result(manifest_path: Path) -> dict[str, object]:
    """Run the real PostgreSQL pre-provider approval gate against a pinned synthetic fixture."""

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (
            candidate
            for candidate in manifest["cases"]
            if isinstance(candidate, dict) and candidate.get("id") == "P0-POST-APPROVAL-EDIT"
        ),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 post-approval-edit case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    fixture_entry = _fixture_entry(registry, "fixture:approved_message_then_content_edited:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:approved_message_then_content_edited:v1",
        version=fixture_entry["version"],
        payload_path=fixture_entry["payload_path"],
        payload_sha256=fixture_entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic post-approval-edit preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_post_approval_edit_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="FREE_FORM_TRANSACTIONAL",
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        rendered_hash_mismatch_detected=preflight.rendered_hash_mismatch_detected,
        new_revision_and_approval_required=preflight.new_revision_and_approval_required,
        provider_attempted=preflight.provider_attempted,
    )
    grade = grade_case(
        case,
        observed,
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )
    result = build_synthetic_result(
        root=ROOT,
        case=case,
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )
    return {
        "mode": "synthetic-post-approval-edit",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_manual_worker_double_send_result(manifest_path: Path) -> dict[str, object]:
    """Run the PostgreSQL manual-send lock against a post-attestation worker claim."""

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (
            candidate
            for candidate in manifest["cases"]
            if isinstance(candidate, dict) and candidate.get("id") == "P0-MANUAL-WORKER-DOUBLE-SEND"
        ),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 manual-worker-double-send case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    fixture_entry = _fixture_entry(
        registry, "fixture:manual_send_attestation_exists_then_worker_claim:v1"
    )
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:manual_send_attestation_exists_then_worker_claim:v1",
        version=fixture_entry["version"],
        payload_path=fixture_entry["payload_path"],
        payload_sha256=fixture_entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic manual-send preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_manual_worker_double_send_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="APPROVED_QUOTE_PRESENTATION",
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        provider_attempted=preflight.provider_attempted,
        worker_execution_rejected_by_exclusive_token=(
            preflight.worker_execution_rejected_by_exclusive_token
        ),
        exactly_one_execution_path_recorded=preflight.exactly_one_execution_path_recorded,
        manual_send_recorded=preflight.manual_send_recorded,
    )
    grade = grade_case(
        case,
        observed,
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )
    result = build_synthetic_result(
        root=ROOT,
        case=case,
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )
    return {
        "mode": "synthetic-manual-worker-double-send",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


if __name__ == "__main__":
    main()
