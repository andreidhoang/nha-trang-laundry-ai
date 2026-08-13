"""Workspace imports must not depend on `.pth` processing or on host file attributes."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import workspace_env

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_PACKAGES = (
    "nha_trang_laundry_contracts",
    "nha_trang_laundry_domain",
    "nha_trang_laundry_db",
    "nha_trang_laundry_evals",
    "nha_trang_laundry_observability",
    "nha_trang_laundry_policy",
    "nha_trang_laundry_agent_tools",
    "nha_trang_laundry_api",
    "nha_trang_laundry_worker",
)


def test_every_declared_workspace_member_resolves_to_an_existing_source_root() -> None:
    roots = workspace_env.workspace_source_roots(ROOT)

    assert len(roots) == 9
    assert all(root.is_dir() for root in roots)
    assert all(root.name == "src" for root in roots)


def test_ensure_workspace_importable_is_idempotent() -> None:
    workspace_env.ensure_workspace_importable(ROOT)
    first = list(sys.path)
    exported = os.environ["PYTHONPATH"]

    workspace_env.ensure_workspace_importable(ROOT)

    assert sys.path == first
    assert os.environ["PYTHONPATH"] == exported


def test_exported_pythonpath_preserves_inherited_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/inherited/entry")

    workspace_env.ensure_workspace_importable(ROOT)

    assert "/inherited/entry" in os.environ["PYTHONPATH"].split(os.pathsep)


def test_subprocess_without_pythonpath_imports_every_workspace_package() -> None:
    """A child process must import the workspace even with no inherited path help.

    `packages/evals/tests` executes `scripts/` entry points through `subprocess`. Those children
    previously failed whenever the parent had started while the `.pth` files were momentarily
    readable and the flag was re-applied before the child launched.
    """
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    program = (
        "import sys; sys.path.insert(0, 'scripts'); import workspace_env; "
        f"import {', '.join(WORKSPACE_PACKAGES)}; print('ok')"
    )

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


@pytest.mark.skipif(not hasattr(stat, "UF_HIDDEN"), reason="UF_HIDDEN is a BSD file flag")
def test_hidden_path_configuration_files_are_reported(tmp_path: Path) -> None:
    site_packages = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    visible = site_packages / "visible.pth"
    hidden = site_packages / "hidden.pth"
    visible.write_text("/tmp\n", encoding="utf-8")
    hidden.write_text("/tmp\n", encoding="utf-8")
    os.chflags(hidden, stat.UF_HIDDEN)

    reported = workspace_env.hidden_path_configuration_files(tmp_path)

    assert reported == (hidden,)

    cleared = workspace_env.clear_hidden_flags(tmp_path)

    assert cleared == (hidden,)
    assert workspace_env.hidden_path_configuration_files(tmp_path) == ()


@pytest.mark.skipif(hasattr(stat, "UF_HIDDEN"), reason="platforms without the BSD hidden flag")
def test_hidden_detection_is_inert_without_the_flag(tmp_path: Path) -> None:
    assert workspace_env.hidden_path_configuration_files(tmp_path) == ()
    assert workspace_env.clear_hidden_flags(tmp_path) == ()
