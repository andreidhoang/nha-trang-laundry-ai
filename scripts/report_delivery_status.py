"""Render the current machine-readable capability release status."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with (ROOT / "delivery/CAPABILITY_STATUS.yaml").open(encoding="utf-8") as status_file:
        status = yaml.safe_load(status_file)
    if not isinstance(status, dict) or not isinstance(status.get("capabilities"), list):
        raise ValueError("CAPABILITY_STATUS.yaml must contain capabilities")

    print(f"Project stage: {status.get('project_stage', 'UNKNOWN')}")
    for capability in status["capabilities"]:
        if not isinstance(capability, dict):
            raise ValueError("Capability status must be a mapping")
        evidence = capability.get("evidence", {})
        cases = capability.get("eligible_real_cases", {})
        days = capability.get("clean_days", {})
        if not all(isinstance(item, dict) for item in (evidence, cases, days)):
            raise ValueError("Capability progress sections must be mappings")
        print(
            " | ".join(
                (
                    str(capability.get("id")),
                    f"authorization={capability.get('authorization')}",
                    f"code={capability.get('code_status')}",
                    f"gates={','.join(str(gate) for gate in capability.get('required_gates', []))}",
                    f"evidence={evidence.get('completed')}/{evidence.get('required')}",
                    f"eligible_cases={cases.get('observed')}/{cases.get('required')}",
                    f"clean_days={days.get('observed')}/{days.get('required')}",
                    f"kill_switch={capability.get('kill_switch_drill')}",
                )
            )
        )


if __name__ == "__main__":
    main()
