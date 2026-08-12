from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from scripts import verify_openclaw_cross_platform as cross_platform

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/release-supply-chain.yml"
MANIFEST_PATH = ROOT / "runtime/openclaw/repack/manifest-v2.json"
ARTIFACT_PATH = ROOT / "runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r2.tgz"
COMMIT = "a" * 40


def _workflow() -> dict[str, Any]:
    value = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _job(name: str) -> dict[str, Any]:
    value = _workflow()["jobs"][name]
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    value = job.get("steps")
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, Any]], value)


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _action_inputs(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], step.get("with", {}))
        for step in _steps(job)
        if step.get("uses") == action
    ]


def _platform_result(tmp_path: Path, runner_os: str) -> tuple[Path, Path]:
    root = tmp_path / runner_os.lower()
    artifact = root / ARTIFACT_PATH.name
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(ARTIFACT_PATH.read_bytes())
    result = cross_platform.platform_result(
        artifact_path=artifact,
        manifest_path=MANIFEST_PATH,
        commit_sha=COMMIT,
        runner_os=runner_os,
        runner_arch="X64",
        runner_name=f"hosted-{runner_os.lower()}",
        runner_image=f"{runner_os.lower()}-image",
        runner_image_version="20260808.1",
    )
    result_path = root / "result.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result_path, artifact


def test_workflow_has_independent_windows_linux_and_comparison_graph() -> None:
    windows = _job("openclaw-repackage-windows")
    linux = _job("openclaw-repackage-linux")
    comparison = _job("openclaw-cross-platform-compare")
    supply_chain = _job("supply-chain")

    assert windows["runs-on"] == "windows-latest"
    assert linux["runs-on"] == "ubuntu-latest"
    assert set(comparison["needs"]) == {
        "openclaw-repackage-windows",
        "openclaw-repackage-linux",
    }
    assert supply_chain["needs"] == ["openclaw-cross-platform-compare"]

    windows_upload = _action_inputs(windows, "actions/upload-artifact@v4")
    linux_upload = _action_inputs(linux, "actions/upload-artifact@v4")
    assert windows_upload[0]["name"] == "openclaw-r2-windows-${{ github.sha }}"
    assert linux_upload[0]["name"] == "openclaw-r2-linux-${{ github.sha }}"
    assert windows_upload[0]["name"] != linux_upload[0]["name"]


@pytest.mark.parametrize(
    ("job_name", "runner_os", "shell"),
    (
        ("openclaw-repackage-windows", "Windows", "pwsh"),
        ("openclaw-repackage-linux", "Linux", "bash"),
    ),
)
def test_platform_jobs_pin_tools_commit_materials_and_emit_typed_results(
    job_name: str, runner_os: str, shell: str
) -> None:
    job = _job(job_name)
    steps = _steps(job)
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"] == {
        "ref": "${{ github.sha }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }
    uv = next(step for step in steps if step.get("uses") == "astral-sh/setup-uv@v7")
    assert uv["with"] == {
        "version": "0.11.32",
        "python-version": "3.12.13",
        "enable-cache": False,
    }
    node = next(step for step in steps if step.get("uses") == "actions/setup-node@v4")
    assert node["with"]["node-version"] == "24.18.0"
    assert node["with"]["cache-dependency-path"] == (
        "runtime/openclaw/public-cell/plugin/package-lock.json"
    )
    run_step = next(
        step for step in steps if "Build and independently verify" in step.get("name", "")
    )
    assert run_step["shell"] == shell
    command = str(run_step["run"])
    for token in (
        "uv sync --all-packages --all-groups --frozen",
        "npm ci --prefix runtime/openclaw/public-cell/plugin --ignore-scripts",
        "scripts/build_openclaw_repackage.py --verify-reproducible",
        "scripts/verify_openclaw_repackage.py",
        "scripts/verify_openclaw_cross_platform.py emit",
        "OPENCLAW_MANIFEST",
    ):
        assert token in command
    assert f"openclaw-r2-{runner_os.lower()}.json" in command


def test_comparison_checks_exact_bytes_commit_manifest_and_source_metadata() -> None:
    comparison = _job("openclaw-cross-platform-compare")
    command = _run_text(comparison)
    assert "scripts/verify_openclaw_cross_platform.py compare" in command
    for token in (
        "--windows-result",
        "--windows-artifact",
        "--linux-result",
        "--linux-artifact",
        "--manifest",
        "--expected-commit",
        "--output-result",
        "--output-artifact",
    ):
        assert token in command
    downloads = _action_inputs(comparison, "actions/download-artifact@v4")
    assert {item["name"] for item in downloads} == {
        "openclaw-r2-windows-${{ github.sha }}",
        "openclaw-r2-linux-${{ github.sha }}",
    }
    upload = _action_inputs(comparison, "actions/upload-artifact@v4")[0]
    assert upload["name"] == "openclaw-r2-compared-${{ github.sha }}"


def test_oci_and_final_evidence_are_gated_on_and_bound_to_compared_r2() -> None:
    supply_chain = _job("supply-chain")
    command = _run_text(supply_chain)
    assert supply_chain["needs"] == ["openclaw-cross-platform-compare"]
    assert "scripts/verify_openclaw_cross_platform.py verify" in command
    assert 'cmp --silent "$compared_artifact" "$committed_artifact"' in command
    assert 'install -m 0644 "$compared_artifact" "$committed_artifact"' in command
    assert command.index("install -m 0644") < command.index("docker buildx build")
    assert "scripts/build_openclaw_repackage.py" not in command
    assert "--dependency-report artifacts/supply-chain/openclaw-cross-platform-comparison.json" in (
        command
    )
    assert "--dependency-report artifacts/supply-chain/openclaw-runtime-audit.json" in command
    assert "--dependency-report artifacts/supply-chain/openclaw-provenance.json" in command
    assert "--provenance=mode=max --sbom=true" in command
    assert "--severity CRITICAL,HIGH --exit-code 1" in command


def test_supply_chain_builds_plugin_before_runtime_verification() -> None:
    supply_chain = _job("supply-chain")
    bind_step = next(
        step
        for step in _steps(supply_chain)
        if step.get("name") == "Bind compared r2 to the OCI build context"
    )
    command = str(bind_step["run"])
    install = "npm ci --prefix runtime/openclaw/public-cell/plugin --ignore-scripts"
    build = "npm --prefix runtime/openclaw/public-cell/plugin run build"
    verify = "uv run python scripts/verify_agent_runtime.py"

    assert command.index(install) < command.index(build) < command.index(verify)


def test_supply_chain_uses_attestation_capable_buildx_driver() -> None:
    supply_chain = _job("supply-chain")
    command = _run_text(supply_chain)
    create = "docker buildx create --driver docker-container"
    bootstrap = "docker buildx inspect --bootstrap"
    driver_check = "grep -Eq '^Driver:[[:space:]]+docker-container$'"
    build = "docker buildx build --pull=false"

    assert command.index(create) < command.index(bootstrap) < command.index(build)
    assert command.index(driver_check) < command.index(build)


def test_workflow_is_fail_closed_and_least_privilege() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert job["permissions"] == {"contents": "read"}
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in (
        "continue-on-error",
        "id-token: write",
        "attestations: write",
        "contents: write",
        "secrets.",
        "|| true",
        "--omit=dev",
        "--ignore-unfixed",
        "--exit-code 0",
        "--exclude",
        ".trivyignore",
        "openclaw@latest",
        "openclaw@next",
        "gemini",
        "model_ref",
        "provider_calls_enabled:",
        "credential:",
        "public_ingress_enabled:",
        "automatic_send_enabled:",
        "direct_send_enabled:",
        "public_ingress_enabled=true",
        "automatic_send_enabled=true",
        "direct_send_enabled=true",
    ):
        assert forbidden not in lowered


def test_typed_comparison_accepts_only_identical_manifest_bound_bytes(tmp_path: Path) -> None:
    windows_result, windows_artifact = _platform_result(tmp_path, "Windows")
    linux_result, linux_artifact = _platform_result(tmp_path, "Linux")
    output_result = tmp_path / "compared" / "comparison.json"
    output_artifact = tmp_path / "compared" / ARTIFACT_PATH.name

    assert (
        cross_platform.main(
            [
                "compare",
                "--windows-result",
                str(windows_result),
                "--windows-artifact",
                str(windows_artifact),
                "--linux-result",
                str(linux_result),
                "--linux-artifact",
                str(linux_artifact),
                "--manifest",
                str(MANIFEST_PATH),
                "--expected-commit",
                COMMIT,
                "--output-result",
                str(output_result),
                "--output-artifact",
                str(output_artifact),
            ]
        )
        == 0
    )
    result = json.loads(output_result.read_text(encoding="utf-8"))
    assert result["status"] == "BYTE_IDENTICAL"
    assert result["comparison"] == {
        "method": "BYTE_FOR_BYTE",
        "bytes_equal": True,
        "metadata_equal": True,
        "compared_size": ARTIFACT_PATH.stat().st_size,
    }
    assert output_artifact.read_bytes() == ARTIFACT_PATH.read_bytes()
    assert (
        cross_platform.verify_comparison(
            comparison_path=output_result,
            artifact_path=output_artifact,
            manifest_path=MANIFEST_PATH,
            expected_commit=COMMIT,
        )
        == result
    )
    result["windows"]["artifact"]["sha256"] = "sha256:" + "0" * 64
    output_result.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError, match="windows metadata binding drifted"):
        cross_platform.verify_comparison(
            comparison_path=output_result,
            artifact_path=output_artifact,
            manifest_path=MANIFEST_PATH,
            expected_commit=COMMIT,
        )


def test_comparison_rejects_byte_metadata_commit_and_runner_drift(tmp_path: Path) -> None:
    windows_result, windows_artifact = _platform_result(tmp_path, "Windows")
    linux_result, linux_artifact = _platform_result(tmp_path, "Linux")

    linux_artifact.write_bytes(linux_artifact.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="manifest-v2 output"):
        cross_platform.comparison_result(
            windows_result_path=windows_result,
            windows_artifact_path=windows_artifact,
            linux_result_path=linux_result,
            linux_artifact_path=linux_artifact,
            manifest_path=MANIFEST_PATH,
            expected_commit=COMMIT,
        )

    linux_artifact.write_bytes(windows_artifact.read_bytes())
    changed = json.loads(linux_result.read_text(encoding="utf-8"))
    changed["artifact"]["sha256"] = "sha256:" + "0" * 64
    linux_result.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata"):
        cross_platform.comparison_result(
            windows_result_path=windows_result,
            windows_artifact_path=windows_artifact,
            linux_result_path=linux_result,
            linux_artifact_path=linux_artifact,
            manifest_path=MANIFEST_PATH,
            expected_commit=COMMIT,
        )

    changed["artifact"] = json.loads(windows_result.read_text(encoding="utf-8"))["artifact"]
    changed["release_commit_sha"] = "b" * 40
    linux_result.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="expected Git commit"):
        cross_platform.comparison_result(
            windows_result_path=windows_result,
            windows_artifact_path=windows_artifact,
            linux_result_path=linux_result,
            linux_artifact_path=linux_artifact,
            manifest_path=MANIFEST_PATH,
            expected_commit=COMMIT,
        )

    changed["release_commit_sha"] = COMMIT
    changed["runner"]["os"] = "Windows"
    linux_result.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="Linux platform result identity"):
        cross_platform.comparison_result(
            windows_result_path=windows_result,
            windows_artifact_path=windows_artifact,
            linux_result_path=linux_result,
            linux_artifact_path=linux_artifact,
            manifest_path=MANIFEST_PATH,
            expected_commit=COMMIT,
        )
