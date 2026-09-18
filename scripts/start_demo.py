"""One command from a fresh clone to a running staff console, with synthetic data.

`CLONE-AND-RUN-001`. The steps this runs already existed and already worked; what did not exist was
anything that executed them in order on a clean checkout. `RUNBOOK-TRUTH-001` and `-002` are both in
this repository's history because a bring-up written only as prose stops being true and nobody finds
out until somebody is trying to open a shop.

**This starts a synthetic demo. It is not a shop.** Every credential it uses is minted fresh into
`.demo/`, which is gitignored, and none of it is reusable anywhere. A real till is
`scripts/bootstrap_shop_local.py` plus `docs/runbooks/shop-till-mac.md`, and it is deliberately not
wrapped into a single command: it needs an `age` public key generated on the owner's own device,
because `DEC-026` keeps the private half off the machine that writes the archive. One command must
not become a way to skip a decision.

**Two machines each running this are two independent demos, and that is fine. Two machines each
running a real shop database are two competing sources of truth about money, and that is not** --
see `docs/CODEX_CROSS_MACHINE_HANDOFF.md` §2. Several devices serving one shop is a different
topology and is already built: `compose.r1.yaml` puts the database and the API on one machine and
the console in a browser on the rest.

Usage:
    uv run python scripts/start_demo.py
    uv run python scripts/start_demo.py --preflight-only
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import shutil
import subprocess

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path

ROOT = _Path(__file__).resolve().parents[1]
CONSOLE_URL = "http://localhost:8081/"
NETWORK = "nha-trang-laundry-staging-database-private"

#: Directory names that mean a file provider is synchronising this checkout. `CLAUDE.md` records the
#: measured consequence: the provider sets `UF_HIDDEN` on everything inside `.venv/` within seconds,
#: CPython silently skips the flagged `.pth` files, and the workspace packages vanish from
#: `sys.path` mid-run -- 37 phantom failures where the true count was 0. Deleting `.venv` does not
#: help; the flag returns in under ten seconds. Refusing here costs a person one `mv`; not refusing
#: costs them an afternoon of failures that are not theirs.
SYNCED_DIRECTORIES = (
    "icloud drive",
    "mobile documents",
    "onedrive",
    "dropbox",
    "google drive",
    "desktop",
    "documents",
    "downloads",
)

#: The bring-up, in order. `docs/runbooks/demo-stack.md` documents the same sequence, and
#: `test_start_demo.py` asserts the two agree so that neither can drift into being wrong alone.
STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("install the workspace", ("uv", "sync", "--all-packages", "--all-groups")),
    ("mint synthetic credentials", ("uv", "run", "python", "scripts/generate_demo_material.py")),
    (
        "start the stack",
        (
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.production.yaml",
            "-f",
            "compose.demo.yaml",
            "up",
            "-d",
            "--wait",
            "--build",
        ),
    ),
    ("verify the stack", ("uv", "run", "python", "scripts/verify_demo_stack.py")),
)


class PreflightFailure(RuntimeError):
    """A prerequisite is missing or wrong. The message is the product; a traceback is not."""


def _tool(name: str, hint: str) -> None:
    if shutil.which(name) is None:
        raise PreflightFailure(f"{name} is not installed or not on PATH. {hint}")


def preflight(root: _Path = ROOT) -> None:
    """Refuse early, once, in a sentence a person can act on."""

    _tool("uv", "Install it from https://docs.astral.sh/uv/ and reopen the terminal.")
    _tool("docker", "Install Docker Desktop, or the Docker Engine on Linux, and start it.")

    location = str(root).casefold()
    for directory in SYNCED_DIRECTORIES:
        if f"/{directory}/" in f"{location}/":
            raise PreflightFailure(
                f"this checkout is inside a synchronised folder ({directory}). A file provider "
                "sets the BSD hidden flag on everything in .venv/ within seconds, CPython then "
                "skips the path files, and the workspace packages disappear from sys.path in the "
                "middle of a run. Move the clone somewhere plain -- ~/laundry is fine -- and run "
                "this again. See the environment note in CLAUDE.md."
            )

    probe = subprocess.run(
        ("docker", "info"), capture_output=True, text=True, check=False, timeout=60
    )
    if probe.returncode != 0:
        raise PreflightFailure(
            "Docker is installed but not running, or this user cannot reach its socket. "
            "Start Docker Desktop, or add yourself to the docker group on Linux, then run this "
            "again."
        )


def ensure_network() -> None:
    """Create the internal network the compose files expect, only if it is absent.

    Never deleted and never recreated: it may be shared, and removing a network to make a command
    quiet is how somebody else's stack stops working.
    """

    existing = subprocess.run(
        ("docker", "network", "ls", "--format", "{{.Name}}"),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if NETWORK in existing.stdout.split():
        return
    subprocess.run(
        ("docker", "network", "create", "--internal", NETWORK),
        check=True,
        timeout=120,
        cwd=ROOT,
    )


def run_steps() -> int:
    for index, (label, command) in enumerate(STEPS, start=1):
        print(f"[{index}/{len(STEPS)}] {label}")
        if command[0] == "docker":
            ensure_network()
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(
                f"\nStopped at step {index}, {label}. The command above printed why; nothing after "
                "this step ran, and nothing outside .demo/ was changed.",
                file=_sys.stderr,
            )
            return completed.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="check the prerequisites and stop, changing nothing",
    )
    arguments = parser.parse_args()

    try:
        preflight()
    except PreflightFailure as failure:
        print(f"Cannot start: {failure}", file=_sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as failure:
        print(f"Cannot start: {failure}", file=_sys.stderr)
        return 1
    if arguments.preflight_only:
        print("Prerequisites are in place. Run without --preflight-only to start the stack.")
        return 0

    code = run_steps()
    if code != 0:
        return code

    print()
    print(f"The staff console is at {CONSOLE_URL}")
    print("Synthetic credentials were written to .demo/ and are gitignored.")
    print("Every AI capability is NOT_AUTHORIZED. That is the correct day-one state.")
    print("This is a demo with invented data. A real till is docs/runbooks/shop-till-mac.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
