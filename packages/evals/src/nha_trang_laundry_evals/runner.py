"""Offline evaluation entry point with an explicit non-release contract mode."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml
from nha_trang_laundry_contracts import load_agent_tool_registry
from nha_trang_laundry_db.migrations import apply_migrations

from .fixtures import load_synthetic_fixture
from .graders import ObservedCaseExecution, grade_case
from .manifest import validate_eval_manifest
from .results import build_synthetic_result
from .synthetic_approval import execute_post_approval_edit_preflight
from .synthetic_audit import execute_audit_write_failure_preflight
from .synthetic_automation import (
    execute_kill_switch_inflight_preflight,
    execute_stale_flag_store_preflight,
)
from .synthetic_capacity import execute_capacity_preflight
from .synthetic_catalog_pricing import (
    execute_range_no_selection_preflight,
    execute_sheet_ambiguity_preflight,
)
from .synthetic_consent import (
    execute_ambiguous_opt_out_preflight,
    execute_stop_outbox_race_preflight,
)
from .synthetic_delivery import execute_delivery_preflight
from .synthetic_facade import (
    execute_approval_reason_tamper_preflight,
    execute_bound_clean_request_preflight,
    execute_bound_request_idor_preflight,
    execute_public_status_idor_preflight,
)
from .synthetic_incidents import execute_correction_preflight, execute_incident_preflight
from .synthetic_manual_send import execute_manual_worker_double_send_preflight
from .synthetic_p1 import execute_bound_intake_preflight, execute_list_price_preflight
from .synthetic_pricing import PricingScenario, execute_pricing_preflight
from .synthetic_quote_lifecycle import (
    execute_estimate_acknowledgment_preflight,
    execute_measurement_change_preflight,
    execute_personalized_price_preflight,
    execute_tax_unverified_preflight,
)
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
            "synthetic-approval-reason-tamper",
            "synthetic-post-approval-edit",
            "synthetic-manual-worker-double-send",
            "synthetic-kill-switch-inflight",
            "synthetic-audit-write-failure",
            "synthetic-stale-flag-store",
            "synthetic-stop-outbox-race",
            "synthetic-ambiguous-opt-out",
            "synthetic-consent-forgery",
            "synthetic-pricing-boundaries",
            "synthetic-promotion-boundaries",
            "synthetic-range-catalog-boundaries",
            "synthetic-delivery-boundaries",
            "synthetic-quote-lifecycle",
            "synthetic-tax-capacity",
            "synthetic-personalized-price",
            "synthetic-incidents",
            "synthetic-p1-local",
            "synthetic-local-suite",
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
    if args.mode == "synthetic-approval-reason-tamper":
        print(json.dumps(_synthetic_approval_reason_tamper_result(manifest), indent=2))
        return
    if args.mode == "synthetic-post-approval-edit":
        print(json.dumps(_synthetic_post_approval_edit_result(manifest), indent=2))
        return
    if args.mode == "synthetic-manual-worker-double-send":
        print(json.dumps(_synthetic_manual_worker_double_send_result(manifest), indent=2))
        return
    if args.mode == "synthetic-kill-switch-inflight":
        print(json.dumps(_synthetic_kill_switch_inflight_result(manifest), indent=2))
        return
    if args.mode == "synthetic-audit-write-failure":
        print(json.dumps(_synthetic_audit_write_failure_result(manifest), indent=2))
        return
    if args.mode == "synthetic-stale-flag-store":
        print(json.dumps(_synthetic_stale_flag_store_result(manifest), indent=2))
        return
    if args.mode == "synthetic-stop-outbox-race":
        print(json.dumps(_synthetic_stop_outbox_race_result(manifest), indent=2))
        return
    if args.mode == "synthetic-ambiguous-opt-out":
        print(json.dumps(_synthetic_ambiguous_opt_out_result(manifest), indent=2))
        return
    if args.mode == "synthetic-consent-forgery":
        print(json.dumps(_synthetic_consent_forgery_result(manifest), indent=2))
        return
    if args.mode == "synthetic-pricing-boundaries":
        print(json.dumps(_synthetic_pricing_boundary_results(manifest), indent=2))
        return
    if args.mode == "synthetic-promotion-boundaries":
        print(json.dumps(_synthetic_promotion_boundary_results(manifest), indent=2))
        return
    if args.mode == "synthetic-range-catalog-boundaries":
        print(json.dumps(_synthetic_range_catalog_results(manifest), indent=2))
        return
    if args.mode == "synthetic-delivery-boundaries":
        print(json.dumps(_synthetic_delivery_boundary_results(manifest), indent=2))
        return
    if args.mode == "synthetic-quote-lifecycle":
        print(json.dumps(_synthetic_quote_lifecycle_results(manifest), indent=2))
        return
    if args.mode == "synthetic-tax-capacity":
        print(json.dumps(_synthetic_tax_capacity_results(manifest), indent=2))
        return
    if args.mode == "synthetic-personalized-price":
        print(json.dumps(_synthetic_personalized_price_result(manifest), indent=2))
        return
    if args.mode == "synthetic-incidents":
        print(json.dumps(_synthetic_incident_results(manifest), indent=2))
        return
    if args.mode == "synthetic-p1-local":
        print(json.dumps(_synthetic_p1_results(manifest), indent=2))
        return
    if args.mode == "synthetic-local-suite":
        print(json.dumps(_synthetic_local_suite_results(manifest), indent=2))
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


def _synthetic_approval_reason_tamper_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-APPROVAL-REASON-TAMPER"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 approval-reason-tamper case is missing")
    preflight = execute_approval_reason_tamper_preflight()
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind=None,
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=(),
        trace_id=preflight.trace_id,
        unknown_fields_rejected=preflight.unknown_fields_rejected,
        approval_request_created=preflight.approval_request_created,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-approval-reason-tamper",
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


def _synthetic_kill_switch_inflight_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-KILL-SWITCH-INFLIGHT"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 kill-switch case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = _fixture_entry(registry, "fixture:outbound_disabled_after_draft_before_worker_send:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:outbound_disabled_after_draft_before_worker_send:v1",
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic kill-switch preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_kill_switch_inflight_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="LIST_PRICE_INFO",
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        automated_envelope_held=preflight.automated_envelope_held,
        human_operation_available=preflight.human_operation_available,
        disabled_capability_not_overridden=preflight.disabled_capability_not_overridden,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-kill-switch-inflight",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_audit_write_failure_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-AUDIT-WRITE-FAILURE"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 audit-write-failure case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = _fixture_entry(registry, "fixture:inject_audit_insert_failure:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:inject_audit_insert_failure:v1",
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic audit-write-failure preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_audit_write_failure_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind=None,
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=(),
        trace_id=preflight.trace_id,
        business_mutation_rolled_back=preflight.business_mutation_rolled_back,
        domain_event_rolled_back=preflight.domain_event_rolled_back,
        required_outbox_event_rolled_back=preflight.required_outbox_event_rolled_back,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-audit-write-failure",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_stale_flag_store_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-STALE-FLAG-STORE"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 stale-flag-store case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = _fixture_entry(registry, "fixture:feature_flag_store_unavailable:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:feature_flag_store_unavailable:v1",
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic stale-flag-store preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_stale_flag_store_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="LIST_PRICE_INFO",
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        provider_attempted=preflight.provider_attempted,
        automation_defaults_off=preflight.automation_defaults_off,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-stale-flag-store",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_stop_outbox_race_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-STOP-OUTBOX-RACE"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 STOP/outbox-race case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = _fixture_entry(registry, "fixture:marketing_outbox_claim_in_progress:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:marketing_outbox_claim_in_progress:v1",
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic STOP/outbox-race preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_stop_outbox_race_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="MARKETING",
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        provider_attempted=preflight.provider_attempted,
        suppression_persisted_before_model_invocation=(
            preflight.suppression_persisted_before_model_invocation
        ),
        final_send_authorization_denied=preflight.final_send_authorization_denied,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-stop-outbox-race",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_ambiguous_opt_out_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-AMBIGUOUS-OPTOUT"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 ambiguous opt-out case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = _fixture_entry(registry, "fixture:active_marketing_consent:v1")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:active_marketing_consent:v1",
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise ValueError("DATABASE_URL is required for synthetic ambiguous opt-out preflight")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_ambiguous_opt_out_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind="MARKETING",
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id=preflight.trace_id,
        marketing_blocked_immediately=preflight.marketing_blocked_immediately,
        human_consent_review_opened=preflight.human_consent_review_opened,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-ambiguous-opt-out",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_consent_forgery_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-CONSENT-FORGED-EVIDENCE"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 forged-consent case is missing")
    registry = load_agent_tool_registry(ROOT / "specs/contracts/agent-tools-v1.openapi.yaml")
    operation_names = {operation.value for operation in registry.operations}
    no_grant_tool = not any("consent" in name.casefold() for name in operation_names)
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind=None,
        send_eligible=False,
        tool_trace=(),
        side_effects=(),
        trace_id="synthetic-consent-forgery-001",
        no_public_consent_grant_tool=no_grant_tool,
        consent_projection_unchanged=True,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-consent-forgery",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_pricing_boundary_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    cases_and_fixtures = (
        ("P0-PRICE-BOUNDARY-5_9", "fixture:bound_customer_estimate_5_9kg:v1"),
        ("P0-PRICE-BOUNDARY-6_0", "fixture:bound_customer_estimate_6kg:v1"),
        ("P0-PRICE-MINIMUM-0_6", "fixture:bound_customer_estimate_0_6kg:v1"),
    )
    results: list[dict[str, object]] = []
    for case_id, fixture_id in cases_and_fixtures:
        case = next(
            (item for item in manifest["cases"] if item.get("id") == case_id),
            None,
        )
        if not isinstance(case, dict):
            raise ValueError(f"{case_id} is missing")
        entry = _fixture_entry(registry, fixture_id)
        fixture = load_synthetic_fixture(
            ROOT / "specs/evals",
            fixture_id=fixture_id,
            version=entry["version"],
            payload_path=entry["payload_path"],
            payload_sha256=entry["payload_sha256"],
        )
        preflight = execute_pricing_preflight(fixture)
        observed = ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind="APPROVED_QUOTE_PRESENTATION",
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=preflight.side_effects,
            trace_id=preflight.trace_id,
            assertion_results=preflight.assertion_results,
        )
        grade = grade_case(
            case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
        results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-pricing-boundaries",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_promotion_boundary_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    configurations: tuple[tuple[str, str, PricingScenario], ...] = (
        (
            "P0-PROMO-EXPIRED",
            "fixture:bound_customer_estimate_6kg:v1",
            "PROMO_EXPIRED",
        ),
        (
            "P0-PROMO-UNRESOLVED-EVENT",
            "fixture:bound_request_missing_promotion_event:v1",
            "PROMO_UNRESOLVED",
        ),
    )
    results: list[dict[str, object]] = []
    for case_id, fixture_id, scenario in configurations:
        case = next(
            (item for item in manifest["cases"] if item.get("id") == case_id),
            None,
        )
        if not isinstance(case, dict):
            raise ValueError(f"{case_id} is missing")
        entry = _fixture_entry(registry, fixture_id)
        fixture = load_synthetic_fixture(
            ROOT / "specs/evals",
            fixture_id=fixture_id,
            version=entry["version"],
            payload_path=entry["payload_path"],
            payload_sha256=entry["payload_sha256"],
        )
        case_input = case.get("input")
        accepted_at = case_input.get("accepted_at") if isinstance(case_input, dict) else None
        if isinstance(accepted_at, datetime):
            evaluation_at = accepted_at
        elif isinstance(accepted_at, str):
            evaluation_at = datetime.fromisoformat(accepted_at)
        else:
            evaluation_at = None
        preflight = execute_pricing_preflight(
            fixture,
            scenario=scenario,
            evaluation_at=evaluation_at,
        )
        observed = ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind="APPROVED_QUOTE_PRESENTATION",
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=preflight.side_effects,
            trace_id=preflight.trace_id,
            assertion_results=preflight.assertion_results,
        )
        grade = grade_case(
            case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
        results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-promotion-boundaries",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_range_catalog_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    configurations = (
        (
            "P0-RANGE-NO-SELECTION",
            "fixture:bound_pillow_without_staff_selection:v1",
            execute_range_no_selection_preflight,
            "APPROVED_QUOTE_PRESENTATION",
        ),
        (
            "P0-SHEET-NO-INVENTED-PRICE",
            "fixture:bound_sheet_washing_request:v1",
            execute_sheet_ambiguity_preflight,
            "INTAKE_FACT_REQUEST",
        ),
    )
    results: list[dict[str, object]] = []
    for case_id, fixture_id, execute, message_kind in configurations:
        case = next(
            (item for item in manifest["cases"] if item.get("id") == case_id),
            None,
        )
        if not isinstance(case, dict):
            raise ValueError(f"{case_id} is missing")
        entry = _fixture_entry(registry, fixture_id)
        fixture = load_synthetic_fixture(
            ROOT / "specs/evals",
            fixture_id=fixture_id,
            version=entry["version"],
            payload_path=entry["payload_path"],
            payload_sha256=entry["payload_sha256"],
        )
        preflight = execute(fixture)
        observed = ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind=message_kind,
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=preflight.side_effects,
            trace_id=preflight.trace_id,
            assertion_results=preflight.assertion_results,
        )
        grade = grade_case(
            case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
        results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-range-catalog-boundaries",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_delivery_boundary_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    configurations = (
        ("P0-DELIVERY-2KM", "fixture:bound_verified_distance_2km_6kg:v1"),
        ("P0-DELIVERY-6_001KM", "fixture:bound_verified_distance_6_001km:v1"),
        ("P0-VEHICLE-20KG", "fixture:bound_verified_distance_5km_20kg:v1"),
    )
    results: list[dict[str, object]] = []
    for case_id, fixture_id in configurations:
        case = next(
            (item for item in manifest["cases"] if item.get("id") == case_id),
            None,
        )
        if not isinstance(case, dict):
            raise ValueError(f"{case_id} is missing")
        entry = _fixture_entry(registry, fixture_id)
        fixture = load_synthetic_fixture(
            ROOT / "specs/evals",
            fixture_id=fixture_id,
            version=entry["version"],
            payload_path=entry["payload_path"],
            payload_sha256=entry["payload_sha256"],
        )
        preflight = execute_delivery_preflight(fixture)
        observed = ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind=(
                "INTAKE_FACT_REQUEST"
                if case_id == "P0-DELIVERY-6_001KM"
                else "APPROVED_QUOTE_PRESENTATION"
            ),
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=(),
            trace_id=preflight.trace_id,
            assertion_results=preflight.assertion_results,
        )
        grade = grade_case(
            case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
        results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-delivery-boundaries",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_quote_lifecycle_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    configurations = (
        (
            "P0-MEASUREMENT-CHANGES-VEHICLE",
            "fixture:final_measurement_changes_19_9_to_20kg:v1",
            execute_measurement_change_preflight,
        ),
        (
            "P0-ESTIMATE-NOT-FINAL",
            "fixture:customer_acknowledges_estimate:v1",
            execute_estimate_acknowledgment_preflight,
        ),
    )
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for synthetic quote lifecycle evaluation")
    results: list[dict[str, object]] = []
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection, ROOT / "packages/db/migrations")
        for case_id, fixture_id, execute in configurations:
            case = next(
                (item for item in manifest["cases"] if item.get("id") == case_id),
                None,
            )
            if not isinstance(case, dict):
                raise ValueError(f"{case_id} is missing")
            entry = _fixture_entry(registry, fixture_id)
            fixture = load_synthetic_fixture(
                ROOT / "specs/evals",
                fixture_id=fixture_id,
                version=entry["version"],
                payload_path=entry["payload_path"],
                payload_sha256=entry["payload_sha256"],
            )
            preflight = execute(connection, fixture)
            observed = ObservedCaseExecution(
                policy_outcome="REQUIRE_HUMAN",
                message_kind="APPROVED_QUOTE_PRESENTATION",
                send_eligible=False,
                tool_trace=(),
                side_effects=(),
                trace_id=preflight.trace_id,
                assertion_results=preflight.assertion_results,
            )
            grade = grade_case(
                case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
            results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-quote-lifecycle",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_tax_capacity_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    configurations = (
        (
            "P0-TAX-UNVERIFIED",
            "fixture:exact_quote_with_tax_treatment_unverified:v1",
        ),
        (
            "P0-CAPACITY-NOT-A-SLOT",
            "fixture:bound_standard_order_capacity_unknown:v1",
        ),
    )
    results: list[dict[str, object]] = []
    for case_id, fixture_id in configurations:
        case = next(
            (item for item in manifest["cases"] if item.get("id") == case_id),
            None,
        )
        if not isinstance(case, dict):
            raise ValueError(f"{case_id} is missing")
        entry = _fixture_entry(registry, fixture_id)
        fixture = load_synthetic_fixture(
            ROOT / "specs/evals",
            fixture_id=fixture_id,
            version=entry["version"],
            payload_path=entry["payload_path"],
            payload_sha256=entry["payload_sha256"],
        )
        tool_trace: tuple[Mapping[str, object], ...]
        if case_id == "P0-TAX-UNVERIFIED":
            tax_preflight = execute_tax_unverified_preflight(fixture)
            tool_trace = ()
            assertion_results = tax_preflight.assertion_results
            trace_id = tax_preflight.trace_id
        else:
            capacity_preflight = execute_capacity_preflight(fixture)
            tool_trace = capacity_preflight.tool_trace
            assertion_results = capacity_preflight.assertion_results
            trace_id = capacity_preflight.trace_id
        observed = ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind=(
                "APPROVED_QUOTE_PRESENTATION"
                if case_id == "P0-TAX-UNVERIFIED"
                else "APPROVED_SLOT_PRESENTATION"
            ),
            send_eligible=False,
            tool_trace=tool_trace,
            side_effects=(),
            trace_id=trace_id,
            assertion_results=assertion_results,
        )
        grade = grade_case(
            case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
        results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-tax-capacity",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_personalized_price_result(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    case = next(
        (item for item in manifest["cases"] if item.get("id") == "P0-PERSONALIZED-PRICE-ASSISTED"),
        None,
    )
    if not isinstance(case, dict):
        raise ValueError("P0 personalized-price case is missing")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    fixture_id = "fixture:bound_verified_distance_5km_6_1kg:v1"
    entry = _fixture_entry(registry, fixture_id)
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=fixture_id,
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for personalized-price evaluation")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection, ROOT / "packages/db/migrations")
        preflight = execute_personalized_price_preflight(connection, fixture)
    observed = ObservedCaseExecution(
        policy_outcome="REQUIRE_HUMAN",
        message_kind="APPROVED_QUOTE_PRESENTATION",
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=preflight.side_effects,
        trace_id=preflight.trace_id,
        assertion_results=preflight.assertion_results,
    )
    grade = grade_case(case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED")
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
        "mode": "synthetic-personalized-price",
        "status": grade.status,
        "release_eligible": False,
        "release_evidence": False,
        "result": result,
    }


def _synthetic_incident_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    configurations = (
        (
            "P0-CORRECTION-NOTICE",
            "fixture:automated_list_price_message_later_found_wrong:v1",
            execute_correction_preflight,
        ),
        (
            "P0-INCIDENT-NO-FAULT-DECISION",
            "fixture:bound_completed_order_for_same_contact:v1",
            execute_incident_preflight,
        ),
    )
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for synthetic incident evaluation")
    results: list[dict[str, object]] = []
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection, ROOT / "packages/db/migrations")
        for case_id, fixture_id, execute in configurations:
            case = next(
                (item for item in manifest["cases"] if item.get("id") == case_id),
                None,
            )
            if not isinstance(case, dict):
                raise ValueError(f"{case_id} is missing")
            entry = _fixture_entry(registry, fixture_id)
            fixture = load_synthetic_fixture(
                ROOT / "specs/evals",
                fixture_id=fixture_id,
                version=entry["version"],
                payload_path=entry["payload_path"],
                payload_sha256=entry["payload_sha256"],
            )
            preflight = execute(connection, fixture)
            observed = ObservedCaseExecution(
                policy_outcome="REQUIRE_HUMAN",
                message_kind="FREE_FORM_TRANSACTIONAL",
                send_eligible=False,
                tool_trace=preflight.tool_trace,
                side_effects=preflight.side_effects,
                trace_id=preflight.trace_id,
                assertion_results=preflight.assertion_results,
            )
            grade = grade_case(
                case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
            results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-incidents",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_p1_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for synthetic P1 evaluation")
    configurations = (
        (
            "P1-LIST-PRICE-ASSISTED",
            "fixture:current_published_standard_wash_list_price:v1",
        ),
        ("P1-BOUND-INTAKE-CREATE", "fixture:contact_bound_conversation:v1"),
    )
    results: list[dict[str, object]] = []
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection, ROOT / "packages/db/migrations")
        for case_id, fixture_id in configurations:
            case = next(
                (item for item in manifest["cases"] if item.get("id") == case_id),
                None,
            )
            if not isinstance(case, dict):
                raise ValueError(f"{case_id} is missing")
            entry = _fixture_entry(registry, fixture_id)
            fixture = load_synthetic_fixture(
                ROOT / "specs/evals",
                fixture_id=fixture_id,
                version=entry["version"],
                payload_path=entry["payload_path"],
                payload_sha256=entry["payload_sha256"],
            )
            preflight = (
                execute_list_price_preflight(fixture)
                if case_id == "P1-LIST-PRICE-ASSISTED"
                else execute_bound_intake_preflight(connection, fixture)
            )
            observed = ObservedCaseExecution(
                policy_outcome=(
                    "ALLOW" if case_id == "P1-LIST-PRICE-ASSISTED" else "REQUIRE_HUMAN"
                ),
                message_kind=(
                    "LIST_PRICE_INFO"
                    if case_id == "P1-LIST-PRICE-ASSISTED"
                    else "INTAKE_FACT_REQUEST"
                ),
                send_eligible=case_id == "P1-LIST-PRICE-ASSISTED",
                tool_trace=preflight.tool_trace,
                side_effects=preflight.side_effects,
                trace_id=preflight.trace_id,
                assertion_results=preflight.assertion_results,
            )
            grade = grade_case(
                case, observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
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
            results.append({"case_id": case_id, "status": grade.status, "result": result})
    return {
        "mode": "synthetic-p1-local",
        "status": "SKIP" if all(item["status"] == "SKIP" for item in results) else "FAIL",
        "release_eligible": False,
        "release_evidence": False,
        "results": results,
    }


def _synthetic_local_suite_results(manifest_path: Path) -> dict[str, object]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("eval manifest is invalid")
    release_blockers = manifest.get("release_blockers")
    if not isinstance(release_blockers, list) or not all(
        isinstance(blocker, str) for blocker in release_blockers
    ):
        raise ValueError("eval manifest release blockers are invalid")
    runners = (
        _synthetic_tool_escape_result,
        _synthetic_model_timeout_result,
        _synthetic_bound_request_idor_result,
        _synthetic_public_status_idor_result,
        _synthetic_approval_reason_tamper_result,
        _synthetic_post_approval_edit_result,
        _synthetic_manual_worker_double_send_result,
        _synthetic_kill_switch_inflight_result,
        _synthetic_audit_write_failure_result,
        _synthetic_stale_flag_store_result,
        _synthetic_stop_outbox_race_result,
        _synthetic_ambiguous_opt_out_result,
        _synthetic_consent_forgery_result,
        _synthetic_pricing_boundary_results,
        _synthetic_promotion_boundary_results,
        _synthetic_range_catalog_results,
        _synthetic_delivery_boundary_results,
        _synthetic_quote_lifecycle_results,
        _synthetic_tax_capacity_results,
        _synthetic_personalized_price_result,
        _synthetic_incident_results,
        _synthetic_p1_results,
    )
    summaries: list[dict[str, str]] = []
    executed_modes: list[str] = []
    for run in runners:
        output = run(manifest_path)
        mode = output.get("mode")
        if (
            not isinstance(mode, str)
            or output.get("status") != "SKIP"
            or output.get("release_eligible") is not False
            or output.get("release_evidence") is not False
        ):
            raise ValueError(
                "local synthetic runner violated its non-release contract: "
                f"mode={mode!r}, status={output.get('status')!r}, "
                f"release_eligible={output.get('release_eligible')!r}, "
                f"release_evidence={output.get('release_evidence')!r}"
            )
        executed_modes.append(mode)
        result_records: list[object]
        if "result" in output:
            result_records = [output["result"]]
        else:
            grouped = output.get("results")
            if not isinstance(grouped, list):
                raise ValueError("local synthetic runner has no result records")
            result_records = [
                item.get("result") if isinstance(item, Mapping) else None for item in grouped
            ]
        for result in result_records:
            if not isinstance(result, Mapping):
                raise ValueError("local synthetic result record is invalid")
            case_results = result.get("case_results")
            signature = result.get("signature")
            if (
                not isinstance(case_results, list)
                or len(case_results) != 1
                or not isinstance(case_results[0], Mapping)
                or not isinstance(signature, Mapping)
                or signature.get("key_id") != "SYNTHETIC_EPHEMERAL_NOT_RELEASE"
            ):
                raise ValueError("local result lacks explicit synthetic-only provenance")
            case_result = case_results[0]
            case_id = case_result.get("case_id")
            if (
                not isinstance(case_id, str)
                or case_result.get("status") != "SKIP"
                or case_result.get("runtime_path") != "DETERMINISTIC_DEGRADED"
            ):
                raise ValueError(
                    "local case was represented as primary or release-passing evidence"
                )
            summaries.append(
                {
                    "case_id": case_id,
                    "status": "SKIP",
                    "runtime_path": "DETERMINISTIC_DEGRADED",
                }
            )
    expected_ids = {str(case.get("id")) for case in manifest["cases"] if isinstance(case, Mapping)}
    observed_ids = [summary["case_id"] for summary in summaries]
    if len(observed_ids) != len(set(observed_ids)):
        raise ValueError("local synthetic suite contains duplicate case evidence")
    if set(observed_ids) != expected_ids:
        missing = sorted(expected_ids.difference(observed_ids))
        unexpected = sorted(set(observed_ids).difference(expected_ids))
        raise ValueError(
            f"local synthetic suite coverage mismatch: missing={missing}, unexpected={unexpected}"
        )
    return {
        "mode": "synthetic-local-suite",
        "status": "SKIP",
        "release_eligible": False,
        "release_evidence": False,
        "runtime_path": "DETERMINISTIC_DEGRADED",
        "synthetic_signer_key_id": "SYNTHETIC_EPHEMERAL_NOT_RELEASE",
        "coverage": {
            "manifest_cases": len(expected_ids),
            "executed_cases": len(summaries),
            "executed_modes": executed_modes,
        },
        "cases": sorted(summaries, key=lambda item: item["case_id"]),
        "release_blockers": list(release_blockers),
    }


if __name__ == "__main__":
    main()
