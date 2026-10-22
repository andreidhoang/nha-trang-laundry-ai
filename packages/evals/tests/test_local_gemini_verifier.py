"""The optional live evaluator must stay local and preserve its five-call plan."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def verifier() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "verify_local_gemini.py"
    specification = importlib.util.spec_from_file_location("local_gemini_verifier", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.invalid",
        "http://127.0.0.1.attacker.invalid",
        "http://user:secret@localhost",
        "http://localhost?key=secret",
        "http://localhost/#fragment",
        "file:///tmp/demo",
        "http://localhost/staff/",
    ],
)
def test_evaluation_refuses_nonlocal_or_credential_bearing_urls(
    verifier: ModuleType, url: str
) -> None:
    with pytest.raises(ValueError, match="loopback"):
        verifier.local_url(url)


def test_offline_plan_needs_exactly_five_classifications(verifier: ModuleType) -> None:
    assert verifier.check_plan() == {
        "mode": "offline-plan-check",
        "cases": 5,
        "provider_calls": 0,
    }
    assert verifier.refusal_baseline()["intent"] == "UNSUPPORTED"
    assert "ASSISTANT_GEMINI_CLASSIFIED" not in verifier.refusal_baseline()["reason_codes"]
    assert verifier.local_url("http://127.0.0.1:8083/") == "http://127.0.0.1:8083"


def test_live_execution_requires_explicit_verified_fresh_budget_flag(
    verifier: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["verify_local_gemini.py", "--output", "/tmp/unused.json"])

    def unexpected(_: object) -> None:
        raise AssertionError("missing budget confirmation must not reach a browser or provider")

    monkeypatch.setattr(verifier, "run", unexpected)
    with pytest.raises(SystemExit) as error:
        verifier.main()
    assert error.value.code == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this host")
def test_store_setup_never_reads_storage_before_the_expected_origin(verifier: ModuleType) -> None:
    """Run the actual injected JavaScript with a throwing opaque-origin storage getter."""
    harness = """
const vm = require('node:vm');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  const {script, store} = JSON.parse(input);
  for (const origin of ['null', 'http://localhost:8084', 'https://example.invalid']) {
    const context = {location: {origin}};
    Object.defineProperty(context, 'localStorage', {
      get() { throw new Error('unexpected storage access'); }
    });
    vm.runInNewContext(script, context);
  }
  const writes = [];
  vm.runInNewContext(script, {
    location: {origin: 'http://localhost:8083'},
    localStorage: {setItem: (...args) => writes.push(args)}
  });
  assert.deepEqual(writes, [['staff_store_id', store]]);
});
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(
            {"script": verifier.store_init_script("http://localhost:8083"), "store": verifier.STORE}
        ),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
