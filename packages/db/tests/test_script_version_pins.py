"""The runner scripts pin the query versions the server publishes -- and must move with them.

`scripts/verify_workflow_conformance.py` is a black-box client of the real API: it does not import
the packages, so it names the version it expects (`startswith("report-v5:")`) as a literal. That is
deliberate -- a published figure changing its rule is something the gate should notice -- but it
means a slice that bumps an identifier and forgets the runner leaves the real-API gate red while
every pytest file is green. GOODS-AND-DRAWER-009 round 1 did exactly that: `report-v5`,
`store-day-orders-export-v5` and `daily-summary-v4` were published, the conformance run still
asserted `report-v4:`, `store-day-orders-export-v4:` and `daily-summary-v3:`, and ended
"643 ok, 5 failed".

This file closes that gap at pytest time. Every versioned label a runner script carries at the
start of a string literal (`<identifier>-v<N>:`) must name the identifier a package currently
publishes, at the version it currently publishes -- or be, verbatim, a label the packages keep as
explicitly retired (the stub suite's retired-shape check replays one on purpose).

The published set is read from the package sources rather than listed here, so a new family, or the
next bump of an old one, is covered without editing this file.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

#: The runner scripts that talk to (or stand in for) the server and pin its query versions.
RUNNER_SCRIPTS = (
    "scripts/verify_workflow_conformance.py",
    "scripts/verify_daily_operations.py",
    "scripts/verify_console_interaction.py",
)

#: A published identifier, e.g. `report-v5` -- the whole literal, nothing around it.
_IDENTIFIER = re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-v(\d+)")
#: A pinned label at the start of a literal: `report-v5:` or `report-v5:06bed9941d4e5cf3`.
_PINNED = re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-v(\d+):")
#: A full label the packages keep verbatim (only ever as a retired one).
_FULL_LABEL = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-v\d+:[0-9a-f]{16}")


def _string_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _package_sources() -> list[Path]:
    return sorted(
        [*ROOT.glob("packages/*/src/**/*.py"), *ROOT.glob("apps/*/src/**/*.py")],
    )


def _published() -> tuple[dict[str, set[int]], set[str]]:
    """Every identifier family the packages publish, with its version(s); and the retired labels."""
    families: dict[str, set[int]] = defaultdict(set)
    retired: set[str] = set()
    for path in _package_sources():
        for _, value in _string_literals(path):
            match = _IDENTIFIER.fullmatch(value)
            if match:
                families[match.group(1)].add(int(match.group(2)))
            elif _FULL_LABEL.fullmatch(value):
                retired.add(value)
    return families, retired


def _pins() -> list[tuple[str, int, str, str, int]]:
    pins = []
    for script in RUNNER_SCRIPTS:
        for line, value in _string_literals(ROOT / script):
            match = _PINNED.match(value)
            if match:
                pins.append((script, line, value, match.group(1), int(match.group(2))))
    return pins


def test_every_published_family_has_exactly_one_current_version() -> None:
    families, _ = _published()
    # Sanity: the families this file exists for are found, at one version each.
    for family in ("report", "store-day-orders-export", "daily-summary", "collected-today"):
        assert family in families, family
    assert {family: versions for family, versions in families.items() if len(versions) != 1} == {}


def test_the_runner_scripts_pin_at_least_the_versions_the_review_named() -> None:
    pinned = {(family, script) for script, _, _, family, _ in _pins()}
    conformance = "scripts/verify_workflow_conformance.py"
    for family in ("report", "store-day-orders-export", "daily-summary", "collected-today"):
        assert (family, conformance) in pinned, family


@pytest.mark.parametrize("script", RUNNER_SCRIPTS)
def test_every_pinned_query_version_is_the_one_the_packages_publish(script: str) -> None:
    families, retired = _published()
    stale = []
    for pin_script, line, value, family, version in _pins():
        if pin_script != script:
            continue
        if value in retired:
            continue
        current = families.get(family)
        if current is None:
            stale.append(f"{script}:{line}: {value!r} names no identifier a package publishes")
        elif version not in current:
            stale.append(
                f"{script}:{line}: {value!r} pins {family}-v{version}; "
                f"the packages publish {family}-v{max(current)}"
            )
    assert stale == []
