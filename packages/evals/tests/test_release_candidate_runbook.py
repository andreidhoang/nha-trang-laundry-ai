"""The published verifier command must satisfy the real parser before deploy day."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from scripts import verify_release_candidate


def test_release_candidate_runbook_supplies_every_required_verifier_argument() -> None:
    root = Path(__file__).resolve().parents[3]
    runbook = (root / "docs/runbooks/release-candidate-verification.md").read_text()
    match = re.search(
        r"```text\n(uv run python scripts/verify_release_candidate\.py.*?)```",
        runbook,
        re.DOTALL,
    )
    assert match is not None
    command = match.group(1).replace("\\\n", " ")
    command = command.replace("<rfc3339-verification-time>", "2026-10-16T00:00:00Z")
    arguments = verify_release_candidate.parser().parse_args(shlex.split(command)[4:])
    assert arguments.supply_chain_evidence is not None
