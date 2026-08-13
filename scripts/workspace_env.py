"""Put the workspace source roots on the import path without relying on `.pth` processing.

Every workspace package is installed as an editable whose only link to `sys.path` is a
`_editable_impl_*.pth` file in the virtual environment. CPython 3.12.11 hardened
`site.addpackage` so that it silently skips any `.pth` file carrying the BSD `UF_HIDDEN` flag; on a
macOS host where the repository sits under an iCloud-managed directory, the file provider applies
that flag to everything inside `.venv/` within seconds of creation. The result is that workspace
packages vanish from `sys.path` at an unpredictable moment, producing import failures that look like
product defects and are not.

Importing this module before any workspace import removes that dependency. Exporting `PYTHONPATH`
extends the guarantee to child processes, which matters because several test suites execute
`scripts/` entry points through `subprocess`.

The module is import-safe, idempotent, and has no effect beyond `sys.path` and `PYTHONPATH`. On
Linux and Windows the flag does not exist, so the detection helpers report nothing and CI behaviour
is unchanged.

Deliberately not bootstrapped: `capture_local_agent_evidence.py`,
`capture_openclaw_offline_evidence.py`, `verify_agent_runtime.py`, `verify_release_candidate.py`,
`build_openclaw_repackage.py`, `verify_openclaw_repackage.py` and
`verify_openclaw_cross_platform.py`. Recorded evidence hash-pins those files, so editing one
invalidates the evidence it produced — which is the pinning working as designed. They are deliberate
evidence-capture tools rather than gate commands; run them with `PYTHONPATH` from
`--print-pythonpath` if the interpreter is skipping `.pth` files.
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HIDDEN_FLAG = getattr(stat, "UF_HIDDEN", 0)


def workspace_source_roots(root: Path = ROOT) -> tuple[Path, ...]:
    """Return the existing `src` directory of every declared uv workspace member."""

    manifest = root / "pyproject.toml"
    if not manifest.is_file():
        return ()
    with manifest.open("rb") as handle:
        document = tomllib.load(handle)
    members = document.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
    roots = []
    for member in members:
        if not isinstance(member, str):
            continue
        candidate = root / member / "src"
        if candidate.is_dir():
            roots.append(candidate.resolve())
    return tuple(roots)


def ensure_workspace_importable(root: Path = ROOT) -> tuple[Path, ...]:
    """Add the workspace source roots to `sys.path` and `PYTHONPATH`; return what was added."""

    roots = workspace_source_roots(root)
    existing = {Path(entry).resolve() for entry in sys.path if entry}
    for candidate in roots:
        if candidate not in existing:
            sys.path.insert(0, str(candidate))
    inherited = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    ordered = [str(candidate) for candidate in roots]
    ordered.extend(entry for entry in inherited if entry not in ordered)
    if ordered:
        os.environ["PYTHONPATH"] = os.pathsep.join(ordered)
    return roots


def site_packages_directories(root: Path = ROOT) -> Iterator[Path]:
    """Yield every `site-packages` directory of the repository virtual environment."""

    for candidate in (root / ".venv" / "lib").glob("python*/site-packages"):
        if candidate.is_dir():
            yield candidate
    windows_candidate = root / ".venv" / "Lib" / "site-packages"
    if windows_candidate.is_dir():
        yield windows_candidate


def hidden_path_configuration_files(root: Path = ROOT) -> tuple[Path, ...]:
    """Return every `.pth` file the interpreter will skip because it is flagged hidden."""

    if not HIDDEN_FLAG:
        return ()
    hidden = []
    for directory in site_packages_directories(root):
        for candidate in sorted(directory.glob("*.pth")):
            try:
                flags = candidate.lstat().st_flags
            except (OSError, AttributeError):
                continue
            if flags & HIDDEN_FLAG:
                hidden.append(candidate)
    return tuple(hidden)


def clear_hidden_flags(root: Path = ROOT) -> tuple[Path, ...]:
    """Clear `UF_HIDDEN` from every affected `.pth` file; return the files that were changed.

    This is a diagnostic convenience, not the fix. The flag is re-applied by a process outside this
    repository, so `ensure_workspace_importable` remains the mechanism the gates depend on.
    """

    change_flags = getattr(os, "chflags", None)
    if change_flags is None:
        return ()
    cleared = []
    for candidate in hidden_path_configuration_files(root):
        try:
            flags = candidate.lstat().st_flags
            change_flags(candidate, flags & ~HIDDEN_FLAG)
        except (OSError, AttributeError):
            continue
        cleared.append(candidate)
    return tuple(cleared)


def _main(argv: list[str]) -> int:
    roots = workspace_source_roots()
    if "--print-pythonpath" in argv:
        print(os.pathsep.join(str(candidate) for candidate in roots))
        return 0
    if "--repair" in argv:
        cleared = clear_hidden_flags()
        print(f"Cleared the hidden flag from {len(cleared)} path configuration files.")
        return 0
    hidden = hidden_path_configuration_files()
    print(f"Workspace source roots: {len(roots)}")
    if hidden:
        print(
            f"WARNING: {len(hidden)} .pth files carry UF_HIDDEN and are being skipped by the "
            "interpreter. Editable workspace packages reach sys.path only through "
            "ensure_workspace_importable()."
        )
        for candidate in hidden:
            print(f"  hidden: {candidate.name}")
    else:
        print("No path configuration file is flagged hidden.")
    if "--check" in argv and hidden:
        return 1
    return 0


ensure_workspace_importable()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
