"""Offline proof for the pinned, isolated, eval-only Public OpenClaw cell."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from generate_agent_tool_plugin import typescript_source
from nha_trang_laundry_contracts import (
    load_agent_tool_registry,
    load_public_runtime_registry,
    verify_openclaw_cli_version,
    verify_public_runtime_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "runtime/model-registry-v1.yaml"
PLUGIN_ROOT = ROOT / "runtime/openclaw/public-cell/plugin"
CONFIG_PATH = ROOT / "runtime/openclaw/public-cell/openclaw.json5"


def run_checked(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(command)}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def executable(name: str) -> str:
    candidates = (f"{name}.cmd", name) if os.name == "nt" else (name,)
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    raise RuntimeError(f"Required executable is unavailable: {name}")


def plugin_executable(name: str) -> str:
    """Resolve the platform shim installed by the plugin lockfile, never global PATH."""

    suffix = ".cmd" if os.name == "nt" else ""
    candidate = PLUGIN_ROOT / "node_modules" / ".bin" / f"{name}{suffix}"
    if not candidate.is_file():
        raise RuntimeError(f"Pinned plugin executable is unavailable; run npm ci: {name}")
    return str(candidate)


def runtime_audit_command(npm: str) -> list[str]:
    """Audit the complete pinned verification tree, including the OpenClaw dev dependency."""

    return [npm, "audit", "--audit-level=high", "--json"]


def verify_npm_dependency_audit(npm: str) -> dict[str, int]:
    """Return sanitized counts while rejecting malformed or inconsistent audit execution."""

    result = subprocess.run(
        runtime_audit_command(npm),
        cwd=PLUGIN_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        report = json.loads(result.stdout)
        counts = report["metadata"]["vulnerabilities"]
        sanitized = {
            severity: int(counts[severity])
            for severity in ("critical", "high", "moderate", "low", "info", "total")
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("npm dependency audit returned malformed JSON") from error
    blocked = bool(sanitized["critical"] or sanitized["high"])
    expected_returncode = 1 if blocked else 0
    if result.returncode != expected_returncode:
        raise RuntimeError("npm dependency audit failed without a high-severity finding")
    return sanitized


def main() -> int:
    registry = load_public_runtime_registry(REGISTRY_PATH)
    artifacts = verify_public_runtime_artifacts(ROOT, registry)
    tool_registry = load_agent_tool_registry(ROOT / registry.tool_contract_path)
    generated = PLUGIN_ROOT / "src/operation-contracts.ts"
    if generated.read_text(encoding="utf-8") != typescript_source():
        raise RuntimeError("Generated OpenClaw tool schemas drifted from the normative OpenAPI")
    openclaw = plugin_executable("openclaw")
    npm = executable("npm")
    version_output = run_checked([openclaw, "--version"], cwd=ROOT, env=os.environ.copy())
    openclaw_revision = verify_openclaw_cli_version(version_output, registry.openclaw.version)

    with tempfile.TemporaryDirectory(prefix="laundry-openclaw-audit-") as state_dir:
        private_config = Path(state_dir) / "openclaw.json5"
        shutil.copyfile(CONFIG_PATH, private_config)
        private_config.chmod(0o600)
        env = os.environ.copy()
        env.update(
            {
                "OPENCLAW_STATE_DIR": state_dir,
                "OPENCLAW_CONFIG_PATH": str(private_config),
                "OPENCLAW_GATEWAY_TOKEN": "validation-only-gateway-token-000000000000",
                "AGENT_RUNNER_BRIDGE_BASE_URL": "http://127.0.0.1:19091",
                "AGENT_RUNNER_BRIDGE_TOKEN": "validation-only-bridge-token-0000000000000",
                "AGENT_TOOL_PLUGIN_PATH": str(PLUGIN_ROOT),
                "OPENAI_API_KEY": "validation-only-not-a-real-provider-key",
            }
        )
        run_checked([openclaw, "config", "validate"], cwd=ROOT, env=env)
        audit_raw = run_checked([openclaw, "security", "audit", "--json"], cwd=ROOT, env=env)
        audit = json.loads(audit_raw)
        summary = audit.get("summary", {})
        if summary.get("critical") != 0:
            raise RuntimeError(
                "OpenClaw security audit blocked: "
                f"critical={summary.get('critical')}; warn={summary.get('warn')}"
            )

    run_checked([npm, "run", "plugin:check"], cwd=PLUGIN_ROOT, env=os.environ.copy())
    run_checked([npm, "test"], cwd=PLUGIN_ROOT, env=os.environ.copy())
    dependency_audit = verify_npm_dependency_audit(npm)
    dependency_blocked = bool(dependency_audit["critical"] or dependency_audit["high"])
    release_blockers = list(registry.release_blockers())
    if dependency_blocked:
        release_blockers.append("OPENCLAW_DEPENDENCY_AUDIT_HIGH")
    print(
        json.dumps(
            {
                "status": ("EVAL_ONLY_BLOCKED" if dependency_blocked else "EVAL_ONLY_VERIFIED"),
                "openclaw_version": registry.openclaw.version,
                "openclaw_build_revision": openclaw_revision,
                "tool_count": len(tool_registry.operations),
                "artifact_count": len(artifacts),
                "security_audit_critical": 0,
                "dependency_audit_critical": dependency_audit["critical"],
                "dependency_audit_high": dependency_audit["high"],
                "release_blockers": release_blockers,
                "real_customer_data_allowed": False,
            },
            indent=2,
        )
    )
    return 2 if dependency_blocked else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
