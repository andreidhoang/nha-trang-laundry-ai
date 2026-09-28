"""`OPENCLAW-RETIRE-001` (ADR-0009): the public OpenClaw runtime is gone and its history is not.

What is proved here, each by reading the tree rather than trusting the record:

* every retired file is absent, and no live code, workflow or deployment file names its path;
* the retirement record is complete and every hash in it re-verifies against the commit it names
  (skipped only on a shallow checkout that lacks that commit);
* the preserved repackage manifests and plugin inventory are byte-identical to what was retired and
  still validate against the schemas they were written for;
* the owner-side `.openclaw/` automation, a different system (ADR-0004 section 5), is untouched.
"""

from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
RECORD_PATH = ROOT / "evidence/openclaw-retirement/retired-artifacts-v1.yaml"
RECORD = yaml.safe_load(RECORD_PATH.read_text(encoding="utf-8"))
BASE = RECORD["last_commit_containing_every_file"]

#: Retired path -> where its bytes are kept in the tree, and the schema that governs it.
PRESERVED = {
    "runtime/openclaw/repack/manifest-v1.json": (
        "evidence/openclaw-retirement/repackage-manifest-v1.json",
        "specs/contracts/openclaw-repackage-manifest-v1.schema.json",
    ),
    "runtime/openclaw/repack/manifest-v2.json": (
        "evidence/openclaw-retirement/repackage-manifest-v2.json",
        "specs/contracts/openclaw-repackage-manifest-v2.schema.json",
    ),
    "runtime/openclaw/public-cell/plugin-inventory-v1.json": (
        "evidence/openclaw-retirement/plugin-inventory-v1.json",
        None,
    ),
}

#: Where a live dependency on a retired path would have to be written down.
LIVE_TREES = ("apps", "packages", "scripts", "deploy", ".github", "runtime")
LIVE_FILES = (*ROOT.glob("compose*.yaml"), ROOT / "pyproject.toml")


def _digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _recorded() -> dict[str, dict[str, object]]:
    return {entry["path"]: entry for entry in RECORD["files"]}


def test_the_record_names_the_decision_and_every_retired_file_is_gone() -> None:
    assert RECORD["work_item"] == "OPENCLAW-RETIRE-001"
    assert (ROOT / RECORD["decision"]).is_file()
    assert len(RECORD["files"]) == len(_recorded())
    present = [path for path in _recorded() if (ROOT / path).exists()]
    assert present == []
    assert not (ROOT / "runtime/openclaw").exists()


def test_no_live_file_depends_on_a_retired_path() -> None:
    retired = set(_recorded())
    offenders: list[str] = []
    candidates = [
        path
        for tree in LIVE_TREES
        for path in (ROOT / tree).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    for path in [*candidates, *LIVE_FILES]:
        if path == Path(__file__):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        offenders.extend(f"{path.relative_to(ROOT)} -> {name}" for name in retired if name in text)
    assert offenders == []


def test_every_recorded_hash_re_verifies_against_the_named_commit() -> None:
    probe = subprocess.run(["git", "cat-file", "-e", f"{BASE}^{{commit}}"], cwd=ROOT, check=False)
    if probe.returncode != 0:
        pytest.skip("shallow checkout without the retirement base commit")
    for path, entry in _recorded().items():
        data = subprocess.run(
            ["git", "show", f"{BASE}:{path}"], cwd=ROOT, check=True, capture_output=True
        ).stdout
        assert _digest(data) == entry["sha256"], path
        assert len(data) == entry["bytes"], path
    # The pre-retirement bytes of files this change modified: retained evidence that pinned them
    # trusts these entries, so they are checked against the commit exactly as the retired files are.
    assert RECORD["prior_versions"]
    for entry in RECORD["prior_versions"]:
        data = subprocess.run(
            ["git", "show", f"{BASE}:{entry['path']}"], cwd=ROOT, check=True, capture_output=True
        ).stdout
        assert _digest(data) == entry["sha256"], entry["path"]
        blob = subprocess.run(
            ["git", "rev-parse", f"{BASE}:{entry['path']}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert blob == entry["git_blob"], entry["path"]


@pytest.mark.parametrize("retired_path", sorted(PRESERVED))
def test_preserved_manifests_are_the_retired_bytes_and_still_valid(retired_path: str) -> None:
    kept, schema_path = PRESERVED[retired_path]
    data = (ROOT / kept).read_bytes()
    assert _digest(data) == _recorded()[retired_path]["sha256"]
    if schema_path is not None:
        schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(json.loads(data)))
        assert errors == []


def test_the_owner_side_automation_is_not_retired() -> None:
    """`.openclaw/` is the delivery-automation state, not the public runtime (ADR-0004 s5)."""

    assert (ROOT / ".openclaw/README.md").is_file()
    assert (ROOT / "scripts/manage_automation_state.py").is_file()
    assert not any(path.startswith(".openclaw/") for path in _recorded())
