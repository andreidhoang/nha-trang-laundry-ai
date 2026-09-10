"""What the console's two decision-making modules actually do, executed rather than read.

`test_staff_console_contract.py` reads the client's source text and `node --check` parses it. Both
are useful and neither can tell whether `parseDong("170000.5")` returns a number ten times too
large, or whether a refusal the server phrased precisely arrives at the screen as "invalid input".
Those are behaviours, and both were wrong until WORKFLOW-CONFORMANCE-001 drove the counter by hand.

So this module runs the real modules under Node. The client tree is copied to a temporary directory
with a `package.json` declaring `"type": "module"`, because that is the only way Node will treat
`.js` files as the ES modules the browser already treats them as; nothing is transformed, and the
files under test are byte-for-byte the ones the server serves.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
DOMAIN_SETTLEMENT = ROOT / "packages/domain/src/nha_trang_laundry_domain/settlement.py"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed on this host"
)


def _run(script: str) -> Any:
    """Execute an ES module against a copy of the client tree and return what it prints as JSON."""
    with tempfile.TemporaryDirectory() as directory:
        target = pathlib.Path(directory)
        shutil.copytree(WEB / "src", target / "src")
        (target / "package.json").write_text(json.dumps({"type": "module"}))
        entry = target / "check.mjs"
        entry.write_text(script)
        result = subprocess.run(
            ["node", str(entry)], capture_output=True, text=True, cwd=target, timeout=60
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)


def test_parse_dong_reads_grouping_and_refuses_a_decimal() -> None:
    """A dot groups thousands; a dot that is not grouping is a mistake, not a tenfold amount.

    `170000.5` used to parse as 1_700_005 -- the amount times ten, silently -- while the settlement
    field's own hint says decimals are not accepted. No wrong amount could reach the ledger, because
    the server compares against the quote to the đồng, but the operator was refused for a number
    they had never typed.
    """

    cases = [
        "170.000",
        "170000",
        "  170.000 ₫ ",
        "1.234.500",
        "0",
        "170000.5",
        "70.5",
        "170.00",
        "1.2345",
        "12.34.567",
        "170,000",
        "",
        "abc",
    ]
    got = _run(
        "import { parseDong } from './src/core/format.js';\n"
        f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map((value) => parseDong(value))));\n"
    )
    assert got == [
        170000,
        170000,
        170000,
        1234500,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_a_settlement_refusal_arrives_as_a_refusal_and_not_as_bad_input() -> None:
    """`record_settlement` sends `reason_code` and `decision`; both used to be dropped.

    `main.py:1125-1131` answers a part payment with
    `{"outcome": "NOT_SUPPORTED", "reason_code": ..., "decision": "DEC-010"}` and its comment says
    the reason travels intact. It travelled as far as `classify`, which recognised only the plural
    `reason_codes` of `create_quote`, so the counter saw "Dữ liệu nhập không hợp lệ" -- the one
    sentence guaranteed to make a staff member holding a customer's money retype a correct number.
    """

    got = _run(
        "import { classify } from './src/core/errors.js';\n"
        "const error = classify(422, {outcome: 'NOT_SUPPORTED', "
        "reason_code: 'AMOUNT_IS_NOT_THE_EXACT_TOTAL', decision: 'DEC-010'}, {});\n"
        "console.log(JSON.stringify({kind: error.kind, codes: error.reasonCodes, "
        "decision: error.decision, message: error.message, retryable: error.retryable}));\n"
    )
    assert got["kind"] == "NOT_SUPPORTED"
    assert got["codes"] == ["AMOUNT_IS_NOT_THE_EXACT_TOTAL"]
    assert got["decision"] == "DEC-010"
    assert "không ghi nhận khoản này" in got["message"]
    # Nothing about this is transient, so nothing offers to send the same command again.
    assert got["retryable"] is False


def test_the_quote_engines_require_human_is_still_classified_as_before() -> None:
    """The plural shape must keep working: it is the one every pricing refusal uses."""

    got = _run(
        "import { classify } from './src/core/errors.js';\n"
        "const error = classify(422, {outcome: 'REQUIRE_HUMAN', "
        "reason_codes: ['RANGE_PRICE_REQUIRES_HUMAN']}, {});\n"
        "console.log(JSON.stringify({kind: error.kind, codes: error.reasonCodes}));\n"
    )
    assert got == {"kind": "REQUIRE_HUMAN", "codes": ["RANGE_PRICE_REQUIRES_HUMAN"]}


def test_every_settlement_refusal_the_counter_can_meet_is_glossed_for_them() -> None:
    """A refusal code with no Vietnamese note is a code the counter cannot act on.

    Both sources are read rather than restated, so a new refusal without a gloss fails here instead
    of shipping a bare English token to a person holding somebody's money.

    The first version of this test read only `SettlementRefusal`, which is the set of refusals that
    name an open *decision*. `record_settlement` also raises four that name a *state* --
    `ORDER_NOT_FOUND`, `ORDER_NOT_ACTIVE`, `ALREADY_SETTLED`, `STALE_VERSION` -- and those reach the
    same panel by the same route. Missing them is how a fix for one half of a problem ships looking
    complete.
    """

    domain = DOMAIN_SETTLEMENT.read_text(encoding="utf-8")
    block = domain.split("class SettlementRefusal(StrEnum):", 1)[1].split("\n\n\n", 1)[0]
    members = [
        value
        for _name, value in re.findall(
            r'^\s{4}([A-Z][A-Z0-9_]+) = "([A-Z0-9_]+)"', block, re.MULTILINE
        )
    ]
    assert members, "the refusal enum was not found; this test is reading the wrong file"

    repository = (ROOT / "packages/db/src/nha_trang_laundry_db/settlement.py").read_text(
        encoding="utf-8"
    )
    raised = re.findall(r'reason_code="([A-Z][A-Z0-9_]+)"', repository)
    assert raised, "no literal reason codes found; this test is reading the wrong file"

    glossed = _run(
        "import { REASON_NOTE } from './src/core/i18n.js';\n"
        "console.log(JSON.stringify(Object.keys(REASON_NOTE)));\n"
    )
    missing = sorted({code for code in [*members, *raised] if code not in glossed})
    assert not missing, f"settlement refusals with no Vietnamese note in i18n.js: {missing}"


def test_nothing_in_this_system_produces_an_incident_scope_or_evidence_hash() -> None:
    """The claim `#/incidents` and `#/gaps` now make, kept true by a check rather than by memory.

    `POST /internal/v1/stores/{id}/incidents` requires `contact_scope_hash` and
    `evidence_summary_hash`, both `^sha256:[0-9a-f]{64}$`. Driving the console as a member of staff
    found there is nowhere to get either: they are agent-pipeline values (a fact bound to the
    contact it was resolved for, `agent-tools-v1.openapi.yaml`), and that pipeline is
    `NOT_AUTHORIZED`. So the console says the form cannot be completed and the gap register names
    the decision that would unblock it.

    The day a producer is built, this test fails and that copy has to be rewritten — which is the
    point. A search for the literal is enough: a producer has to name the field to fill it.
    """

    producers: list[str] = []
    searched = 0
    for directory in ("apps", "packages", "scripts"):
        for path in (ROOT / directory).rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
                continue
            searched += 1
            source = path.read_text(encoding="utf-8")
            for field in ("contact_scope_hash", "evidence_summary_hash"):
                if field not in source:
                    continue
                # Declaring the field on a request model, a command or an INSERT is carrying a
                # value somebody else supplied. Producing one means computing a digest.
                for line in source.splitlines():
                    if field not in line:
                        continue
                    if any(
                        marker in line
                        for marker in ("sha256(", "hashlib", "canonical_document", "digest(")
                    ):
                        producers.append(f"{path.relative_to(ROOT)}: {line.strip()[:100]}")
    assert searched > 50, "the search walked almost nothing; it is looking in the wrong place"
    assert not producers, (
        "something now produces an incident scope or evidence hash, so the console's warning on "
        f"#/incidents and the #/gaps entry are out of date: {producers}"
    )


def test_a_stale_settlement_is_classified_as_stale_and_not_as_an_unsupported_case() -> None:
    """`record_settlement` wraps a lost race in the same envelope as its policy refusals.

    Every refusal from that route answers `422 {"outcome": "NOT_SUPPORTED", ...}`, including
    `STALE_VERSION`, which is not a policy question: somebody else changed the order while this
    screen had it open. Under the NOT_SUPPORTED heading the operator is told the shop does not do
    this, when the only move that helps is the reload `STALE` already offers.
    """

    got = _run(
        "import { classify } from './src/core/errors.js';\n"
        "const stale = classify(422, {outcome: 'NOT_SUPPORTED', reason_code: 'STALE_VERSION', "
        "decision: null}, {});\n"
        "const policy = classify(422, {outcome: 'NOT_SUPPORTED', "
        "reason_code: 'AMOUNT_IS_NOT_THE_EXACT_TOTAL', decision: 'DEC-010'}, {});\n"
        "console.log(JSON.stringify({stale: stale.kind, staleCodes: stale.reasonCodes, "
        "policy: policy.kind}));\n"
    )
    assert got["stale"] == "STALE"
    assert got["staleCodes"] == ["STALE_VERSION"]
    # And the policy refusals keep their own kind: this narrows one code, it does not collapse them.
    assert got["policy"] == "NOT_SUPPORTED"
