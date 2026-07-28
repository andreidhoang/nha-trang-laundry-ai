from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


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
    assert "8 phases" in result.stdout


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
