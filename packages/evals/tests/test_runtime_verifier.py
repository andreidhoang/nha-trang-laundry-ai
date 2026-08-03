from __future__ import annotations

import sys
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))


def _load_verifier() -> ModuleType:
    spec = spec_from_file_location(
        "verify_agent_runtime_for_test",
        ROOT / "scripts/verify_agent_runtime.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load runtime verifier")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_capture() -> ModuleType:
    spec = spec_from_file_location(
        "capture_openclaw_offline_evidence_for_test",
        ROOT / "scripts/capture_openclaw_offline_evidence.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load runtime evidence capture")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pinned_runtime_executable_never_falls_back_to_global_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    global_bin = tmp_path / "global-bin"
    global_bin.mkdir()
    (global_bin / "openclaw").write_text("global executable", encoding="utf-8")
    monkeypatch.setenv("PATH", str(global_bin))
    monkeypatch.setattr(verifier, "PLUGIN_ROOT", tmp_path / "plugin")

    with pytest.raises(RuntimeError, match="Pinned plugin executable is unavailable"):
        verifier.plugin_executable("openclaw")

    local_bin = verifier.PLUGIN_ROOT / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    expected = local_bin / "openclaw"
    expected.write_text("pinned executable", encoding="utf-8")

    assert verifier.plugin_executable("openclaw") == str(expected)


def test_runtime_audit_includes_pinned_development_tree() -> None:
    verifier = _load_verifier()

    command = verifier.runtime_audit_command("npm")

    assert command == ["npm", "audit", "--audit-level=high", "--json"]
    assert "--omit=dev" not in command


def test_blocked_evidence_requires_consistent_counts_exit_and_blocker() -> None:
    capture = _load_capture()
    registry = capture.load_public_runtime_registry(ROOT / "runtime/model-registry-v1.yaml")
    base_blockers = list(registry.release_blockers())
    blocked = {
        "status": "EVAL_ONLY_BLOCKED",
        "openclaw_version": registry.openclaw.version,
        "openclaw_build_revision": "0790d9f",
        "tool_count": 10,
        "artifact_count": 12,
        "security_audit_critical": 0,
        "dependency_audit_critical": 0,
        "dependency_audit_high": 2,
        "release_blockers": [*base_blockers, "OPENCLAW_DEPENDENCY_AUDIT_HIGH"],
        "real_customer_data_allowed": False,
    }

    assert capture._validated_result(blocked, returncode=2) == blocked

    for mutation in (
        {"dependency_audit_high": 0},
        {"status": "EVAL_ONLY_VERIFIED"},
        {"release_blockers": base_blockers},
    ):
        invalid = deepcopy(blocked)
        invalid.update(mutation)
        with pytest.raises(ValueError, match="violates the non-release evidence contract"):
            capture._validated_result(invalid, returncode=2)

    with pytest.raises(ValueError, match="violates the non-release evidence contract"):
        capture._validated_result(blocked, returncode=0)
