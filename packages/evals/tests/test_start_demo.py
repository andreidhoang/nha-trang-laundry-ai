"""CLONE-AND-RUN-001: the one-command bring-up, and the guard that keeps its claim true.

`RUNBOOK-TRUTH-001` and `-002` exist in this repository's history because a bring-up written only as
prose stops being true and nobody finds out until somebody is trying to open a shop. This file is
the guard for the sentence "one command and you have a running console".

It deliberately needs neither Docker nor a database: a test that only runs where the thing already
works is not a guard.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "start_demo.py"
RUNBOOK = ROOT / "docs" / "runbooks" / "demo-stack.md"


def _module() -> ModuleType:
    specification = importlib.util.spec_from_file_location("start_demo", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_entry_point_exists_and_names_its_steps_in_order() -> None:
    start_demo = _module()
    labels = [label for label, _ in start_demo.STEPS]

    assert labels == [
        "install the workspace",
        "mint synthetic credentials",
        "start the stack",
        "verify the stack",
    ]
    # The order is the safety property: credentials are minted before the stack starts, and the
    # stack is verified after it starts. A reordering that still ran every step would produce a
    # stack running on material from a previous run.
    assert labels.index("mint synthetic credentials") < labels.index("start the stack")
    assert labels.index("start the stack") < labels.index("verify the stack")


def test_the_script_and_the_runbook_cannot_drift_apart() -> None:
    """Both describe the same bring-up, so a change to one that is not a change to the other is a
    change that makes one of them wrong. This asserts every command the script runs is named in the
    runbook."""

    start_demo = _module()
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for _, command in start_demo.STEPS:
        if command[0] == "docker":
            assert "docker compose" in runbook
            continue
        script_name = command[-1]
        assert script_name in runbook or " ".join(command[:3]) in runbook, (
            f"{script_name} is run by scripts/start_demo.py and appears nowhere in "
            f"{RUNBOOK.relative_to(ROOT)}; one of the two is now wrong"
        )
    assert start_demo.NETWORK in runbook


def test_preflight_names_a_missing_prerequisite_instead_of_raising_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure message is the product. A person meeting Docker-not-installed on their first
    five minutes with this repository gets a sentence they can act on, or they give up."""

    start_demo = _module()
    monkeypatch.setattr(start_demo.shutil, "which", lambda name: None)

    with pytest.raises(start_demo.PreflightFailure) as failure:
        start_demo.preflight()

    message = str(failure.value)
    assert "uv" in message
    assert "PATH" in message or "Install" in message


def test_preflight_refuses_a_checkout_inside_a_synchronised_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `ENV-INTEGRITY-001` defect, refused at the door.

    Measured, not theorised: a file provider sets `UF_HIDDEN` on everything in `.venv/` within
    seconds, CPython skips the flagged `.pth` files, and the workspace packages vanish from
    `sys.path` mid-run -- 37 phantom failures where the true count was 0. Deleting `.venv` does not
    help. A person who hits that spends an afternoon on failures that are not theirs.
    """

    start_demo = _module()
    monkeypatch.setattr(start_demo.shutil, "which", lambda name: f"/usr/bin/{name}")
    synced = tmp_path / "Library" / "Mobile Documents" / "laundry"
    synced.mkdir(parents=True)

    with pytest.raises(start_demo.PreflightFailure) as failure:
        start_demo.preflight(synced)

    message = str(failure.value)
    assert "synchronised folder" in message
    assert "CLAUDE.md" in message


def test_running_the_preflight_leaves_the_working_tree_clean() -> None:
    """A bring-up that leaves a diff behind is a bring-up that cannot be run twice with confidence.

    `--preflight-only` is the part that is safe to execute in any environment, including one with no
    Docker, so it is the part this asserts on.
    """

    before = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    subprocess.run(
        (sys.executable, str(SCRIPT), "--preflight-only"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    after = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout

    assert before == after


def test_the_synthetic_stack_cannot_be_mistaken_for_a_shop() -> None:
    """`CODEX_CROSS_MACHINE_HANDOFF.md` §2 separates two modes and this entry point starts one.

    Two machines each running this are two independent demos. Two machines each running a real shop
    database are two competing sources of truth about money. The script must say which it is, and
    must not become a way to skip `DEC-026`'s requirement that the backup identity is generated off
    the machine that writes the archive.
    """

    source = SCRIPT.read_text(encoding="utf-8")

    assert "It is not a shop" in source
    assert "shop-till-mac.md" in source
    assert "DEC-026" in source
    # It starts no real-shop topology and wraps no real-shop bootstrap.
    assert (
        "bootstrap_shop_local" not in source.replace("scripts/bootstrap_shop_local.py", "")
        or "not wrapped into a single command" in source
    )
    assert "compose.r1.yaml" not in source.split("Usage:")[1]
