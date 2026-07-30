"""Render the current machine-readable capability release status."""

from __future__ import annotations

from pathlib import Path

import yaml
from check_context_drift import validate_capability_status, validate_gate_registry

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    _, gate_requirements = validate_gate_registry()
    validate_capability_status(gate_requirements)
    with (ROOT / "delivery/CAPABILITY_STATUS.yaml").open(encoding="utf-8") as status_file:
        status = yaml.safe_load(status_file)
    if not isinstance(status, dict) or not isinstance(status.get("capabilities"), list):
        raise ValueError("CAPABILITY_STATUS.yaml must contain capabilities")

    print(f"Project stage: {status.get('project_stage', 'UNKNOWN')}")
    configured = {
        capability.get("id"): capability
        for capability in status["capabilities"]
        if isinstance(capability, dict)
    }
    for capability_id, required_gates in gate_requirements.items():
        capability = configured.get(capability_id)
        if capability is None:
            capability = {
                "id": capability_id,
                "authorization": status["default_authorization"],
                "code_status": "NOT_STARTED",
                "required_gates": required_gates,
                "evidence": {"completed": 0, "required": None},
                "eligible_real_cases": {"observed": 0, "required": None},
                "clean_days": {"observed": 0, "required": None},
                "kill_switch_drill": "NOT_RUN",
            }
        if not isinstance(capability, dict):
            raise ValueError("Capability status must be a mapping")
        evidence = capability.get("evidence", {})
        cases = capability.get("eligible_real_cases", {})
        days = capability.get("clean_days", {})
        if not all(isinstance(item, dict) for item in (evidence, cases, days)):
            raise ValueError("Capability progress sections must be mappings")
        evidence_required = evidence.get("required")
        cases_required = cases.get("required")
        days_required = days.get("required")
        print(
            " | ".join(
                (
                    str(capability.get("id")),
                    f"authorization={capability.get('authorization')}",
                    f"code={capability.get('code_status')}",
                    f"gates={','.join(str(gate) for gate in capability.get('required_gates', []))}",
                    "evidence="
                    f"{evidence.get('completed')}/"
                    f"{evidence_required if evidence_required is not None else 'UNSET'}",
                    "eligible_cases="
                    f"{cases.get('observed')}/"
                    f"{cases_required if cases_required is not None else 'UNSET'}",
                    "clean_days="
                    f"{days.get('observed')}/"
                    f"{days_required if days_required is not None else 'UNSET'}",
                    f"kill_switch={capability.get('kill_switch_drill')}",
                )
            )
        )


if __name__ == "__main__":
    main()
