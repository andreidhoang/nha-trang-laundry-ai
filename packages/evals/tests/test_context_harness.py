from __future__ import annotations

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load_context_drift_module() -> ModuleType:
    spec = spec_from_file_location(
        "check_context_drift_for_test", ROOT / "scripts/check_context_drift.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load context drift validator")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_context_drift_check_passes() -> None:
    result = run_script("scripts/check_context_drift.py")

    assert result.returncode == 0, result.stderr
    assert "Context drift check passed" in result.stdout
    assert "4 gates" in result.stdout
    assert "9 phases" in result.stdout
    assert "36 work items" in result.stdout
    assert "13 capabilities" in result.stdout


def test_context_packet_contains_continuation_protocol() -> None:
    result = run_script(
        "scripts/assemble_context.py", "--task-id", "DOMAIN-001", "--domain", "pricing"
    )

    assert result.returncode == 0, result.stderr
    assert "context/CONTINUATION_PROTOCOL.md" in result.stdout


def test_context_packet_contains_agent_tool_contract() -> None:
    result = run_script(
        "scripts/assemble_context.py",
        "--task-id",
        "TASK-agent-001",
        "--domain",
        "agent_tools",
        "--domain",
        "runtime_architecture",
    )

    assert result.returncode == 0, result.stderr
    assert "specs/contracts/agent-tools-v1.openapi.yaml" in result.stdout
    assert "direct-send capability" in result.stdout
    assert "docs/adr/0002-production-agent-runtime-and-trust-boundaries.md" in result.stdout


def test_delivery_status_report_marks_capabilities_unauthorized() -> None:
    result = run_script("scripts/report_delivery_status.py")

    assert result.returncode == 0, result.stderr
    assert "INTERNAL_SHADOW | authorization=NOT_AUTHORIZED" in result.stdout
    assert "MARKETING_FOLLOWUP | authorization=NOT_AUTHORIZED" in result.stdout
    assert len([line for line in result.stdout.splitlines() if " | authorization=" in line]) == 13


def test_authorized_capability_requires_cryptographic_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_context_drift = load_context_drift_module()
    _, gate_requirements = check_context_drift.validate_gate_registry()
    status = yaml.safe_load((ROOT / "delivery/CAPABILITY_STATUS.yaml").read_text(encoding="utf-8"))
    status["capabilities"][0]["authorization"] = "AUTHORIZED"
    status["capabilities"][0]["release_manifest"] = (
        "specs/contracts/release-gate-manifest-v1.schema.json"
    )
    monkeypatch.setattr(check_context_drift, "load_yaml", lambda _: status)

    with pytest.raises(ValueError, match="requires verified release metadata"):
        check_context_drift.validate_capability_status(gate_requirements)


def test_authorized_capability_requires_out_of_band_deployment_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_context_drift = load_context_drift_module()
    _, gate_requirements = check_context_drift.validate_gate_registry()
    status = yaml.safe_load((ROOT / "delivery/CAPABILITY_STATUS.yaml").read_text(encoding="utf-8"))
    status["capabilities"][0].update(
        authorization="AUTHORIZED",
        release_manifest="specs/contracts/release-gate-manifest-v1.schema.json",
        trusted_signers="specs/contracts/trusted-release-signers-v1.schema.json",
    )
    monkeypatch.setattr(check_context_drift, "load_yaml", lambda _: status)
    for variable in (
        check_context_drift.RELEASE_COMMIT_ENV,
        check_context_drift.RELEASE_STAGE_ENV,
        check_context_drift.RELEASE_TRUST_PIN_ENV,
    ):
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ValueError, match="requires out-of-band deployment release context"):
        check_context_drift.validate_capability_status(gate_requirements)
