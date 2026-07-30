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


def main() -> None:
    registry = load_public_runtime_registry(REGISTRY_PATH)
    artifacts = verify_public_runtime_artifacts(ROOT, registry)
    tool_registry = load_agent_tool_registry(ROOT / registry.tool_contract_path)
    generated = PLUGIN_ROOT / "src/operation-contracts.ts"
    if generated.read_text(encoding="utf-8") != typescript_source():
        raise RuntimeError("Generated OpenClaw tool schemas drifted from the normative OpenAPI")
    openclaw = executable("openclaw")
    npm = executable("npm")
    version_output = run_checked([openclaw, "--version"], cwd=ROOT, env=os.environ.copy())
    openclaw_revision = verify_openclaw_cli_version(version_output, registry.openclaw.version)

    with tempfile.TemporaryDirectory(prefix="laundry-openclaw-audit-") as state_dir:
        env = os.environ.copy()
        env.update(
            {
                "OPENCLAW_STATE_DIR": state_dir,
                "OPENCLAW_CONFIG_PATH": str(CONFIG_PATH),
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
            raise RuntimeError(f"OpenClaw security audit has critical findings: {audit_raw}")

    run_checked([npm, "run", "plugin:check"], cwd=PLUGIN_ROOT, env=os.environ.copy())
    run_checked([npm, "test"], cwd=PLUGIN_ROOT, env=os.environ.copy())
    run_checked(
        [npm, "audit", "--omit=dev", "--audit-level=high"],
        cwd=PLUGIN_ROOT,
        env=os.environ.copy(),
    )
    print(
        json.dumps(
            {
                "status": "EVAL_ONLY_VERIFIED",
                "openclaw_version": registry.openclaw.version,
                "openclaw_build_revision": openclaw_revision,
                "tool_count": len(tool_registry.operations),
                "artifact_count": len(artifacts),
                "security_audit_critical": 0,
                "release_blockers": registry.release_blockers(),
                "real_customer_data_allowed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
