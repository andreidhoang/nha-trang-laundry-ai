from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from nha_trang_laundry_evals import EvalManifestError, validate_eval_manifest

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "specs/evals/eval-manifest-v1.yaml"


def test_contract_runner_validates_seed_manifest_without_authorizing_release() -> None:
    report = validate_eval_manifest(ROOT, MANIFEST)

    assert report.total_cases == 32
    assert report.p0_cases >= 25
    assert report.referenced_tool_calls > 0
    assert report.release_eligible is False
    assert report.unimplemented_fixture_payloads == 0
    assert report.unimplemented_assertions == 0
    assert "FIXTURE_PAYLOADS_NOT_IMPLEMENTED" not in report.release_blockers
    assert "ASSERTION_GRADERS_NOT_IMPLEMENTED" not in report.release_blockers
    assert "PRIMARY_PROVIDER_RESULTS_MISSING" in report.release_blockers


def test_cli_reports_contract_pass_and_release_blockers() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "PASS"' in result.stdout
    assert '"release_eligible": false' in result.stdout


def test_cli_reports_synthetic_tool_escape_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-tool-escape",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"release_evidence": false' in result.stdout
    assert '"runtime_path": "DETERMINISTIC_DEGRADED"' in result.stdout


def test_cli_reports_synthetic_model_timeout_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-model-timeout",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-MODEL-TIMEOUT"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_bound_request_idor_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-bound-request-idor",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-BOUND-REQUEST-IDOR"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_public_status_idor_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-public-status-idor",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-PUBLIC-STATUS-IDOR"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_post_approval_edit_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-post-approval-edit",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-POST-APPROVAL-EDIT"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_manual_worker_double_send_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-manual-worker-double-send",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-MANUAL-WORKER-DOUBLE-SEND"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_kill_switch_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-kill-switch-inflight",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-KILL-SWITCH-INFLIGHT"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_audit_failure_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-audit-write-failure",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-AUDIT-WRITE-FAILURE"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_stale_flag_store_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-stale-flag-store",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-STALE-FLAG-STORE"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_stop_outbox_race_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-stop-outbox-race",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-STOP-OUTBOX-RACE"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_ambiguous_opt_out_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-ambiguous-opt-out",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-AMBIGUOUS-OPTOUT"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_consent_forgery_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-consent-forgery",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-CONSENT-FORGED-EVIDENCE"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_all_synthetic_pricing_boundaries_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-pricing-boundaries",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    for case_id in (
        "P0-PRICE-BOUNDARY-5_9",
        "P0-PRICE-BOUNDARY-6_0",
        "P0-PRICE-MINIMUM-0_6",
    ):
        assert f'"case_id": "{case_id}"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_promotion_boundaries_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-promotion-boundaries",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "SKIP"
    assert {item["status"] for item in output["results"]} == {"SKIP"}
    assert '"case_id": "P0-PROMO-EXPIRED"' in result.stdout
    assert '"case_id": "P0-PROMO-UNRESOLVED-EVENT"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_range_catalog_boundaries_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-range-catalog-boundaries",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-RANGE-NO-SELECTION"' in result.stdout
    assert '"case_id": "P0-SHEET-NO-INVENTED-PRICE"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_delivery_boundaries_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-delivery-boundaries",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    for case_id in ("P0-DELIVERY-2KM", "P0-DELIVERY-6_001KM", "P0-VEHICLE-20KG"):
        assert f'"case_id": "{case_id}"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_quote_lifecycle_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-quote-lifecycle",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    for case_id in ("P0-MEASUREMENT-CHANGES-VEHICLE", "P0-ESTIMATE-NOT-FINAL"):
        assert f'"case_id": "{case_id}"' in result.stdout
    assert '"release_eligible": false' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_tax_capacity_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-tax-capacity",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    for case_id in ("P0-TAX-UNVERIFIED", "P0-CAPACITY-NOT-A-SLOT"):
        assert f'"case_id": "{case_id}"' in result.stdout
    assert '"release_eligible": false' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_personalized_price_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-personalized-price",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-PERSONALIZED-PRICE-ASSISTED"' in result.stdout
    assert '"release_eligible": false' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_incidents_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-incidents",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    for case_id in ("P0-CORRECTION-NOTICE", "P0-INCIDENT-NO-FAULT-DECISION"):
        assert f'"case_id": "{case_id}"' in result.stdout
    assert '"release_eligible": false' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_synthetic_p1_local_as_non_release_skip() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-p1-local",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    for case_id in ("P1-LIST-PRICE-ASSISTED", "P1-BOUND-INTAKE-CREATE"):
        assert f'"case_id": "{case_id}"' in result.stdout
    assert '"release_eligible": false' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_reports_approval_reason_tamper_as_non_release_skip() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-approval-reason-tamper",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "SKIP"' in result.stdout
    assert '"case_id": "P0-APPROVAL-REASON-TAMPER"' in result.stdout
    assert '"release_evidence": false' in result.stdout


def test_cli_local_suite_covers_manifest_without_creating_release_evidence() -> None:
    if "DATABASE_URL" not in os.environ:
        import pytest

        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nha_trang_laundry_evals.runner",
            "--manifest",
            "specs/evals/eval-manifest-v1.yaml",
            "--mode",
            "synthetic-local-suite",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "SKIP"
    assert output["release_eligible"] is False
    assert output["release_evidence"] is False
    assert output["synthetic_signer_key_id"] == "SYNTHETIC_EPHEMERAL_NOT_RELEASE"
    assert output["coverage"]["manifest_cases"] == 32
    assert output["coverage"]["executed_cases"] == 32
    assert len({case["case_id"] for case in output["cases"]}) == 32
    assert {case["status"] for case in output["cases"]} == {"SKIP"}
    assert {case["runtime_path"] for case in output["cases"]} == {"DETERMINISTIC_DEGRADED"}


def test_unknown_tool_operation_fails_closed(tmp_path: Path) -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest["cases"][0]["expected"]["tool_calls"][0]["operation_id"] = "shellExecute"
    tampered = tmp_path / "eval-manifest.yaml"
    tampered.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    # Contract refs were relative to specs/evals; make them absolute repository-relative from tmp.
    for key, value in manifest["normative_contracts"].items():
        manifest["normative_contracts"][key] = str((MANIFEST.parent / value).resolve())
    tampered.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    try:
        validate_eval_manifest(ROOT, tampered)
    except (EvalManifestError, ValueError) as error:
        assert "shellExecute" in str(error)
    else:
        raise AssertionError("Unknown generic operation was accepted")


def test_stale_implementation_blocker_fails_closed(tmp_path: Path) -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    manifest["release_blockers"].append("FIXTURE_PAYLOADS_NOT_IMPLEMENTED")
    for key, value in manifest["normative_contracts"].items():
        manifest["normative_contracts"][key] = str((MANIFEST.parent / value).resolve())
    tampered = tmp_path / "eval-manifest.yaml"
    tampered.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    try:
        validate_eval_manifest(ROOT, tampered)
    except EvalManifestError as error:
        assert "blocker contradicts fixture registry" in str(error)
    else:
        raise AssertionError("Stale fixture implementation blocker was accepted")
