from __future__ import annotations

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
    assert report.unimplemented_fixture_payloads > 0
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
