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
from nha_trang_laundry_domain.promotion import PromotionReason
from nha_trang_laundry_domain.quote_composition import (
    APPROVAL_OUTSTANDING_PREFIX,
    PROMOTION_CHANGED_SINCE_QUOTE,
    PROMOTION_NOT_PUBLISHED,
    PROMOTION_PENDING_BAND_CLOSE,
    PROMOTION_PUBLISHED_SINCE_APPROVAL,
)

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
DOMAIN_SETTLEMENT = ROOT / "packages/domain/src/nha_trang_laundry_domain/settlement.py"
DOMAIN_RANGE_PRICES = ROOT / "packages/domain/src/nha_trang_laundry_domain/range_prices.py"
DOMAIN_REMEDIES = ROOT / "packages/domain/src/nha_trang_laundry_domain/remedies.py"

#: `DC_AO_DAI_TRADITIONAL` as `templates/services-pricebook.csv` publishes it, and the worked
#: example in the task packet and in `CORE_OPERATIONS_COMPLETION_SPEC_V1.md` §3. Held here so the
#: client-side checks below are exercised against a real published band rather than a round number
#: chosen to make them pass.
AO_DAI_BAND = (80_000, 240_000)

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


def test_both_ends_of_a_published_band_are_accepted_and_neither_step_outside_is() -> None:
    """The console's copy of the bound, checked against the band the pricebook really publishes.

    This is the client half of the property the domain suite pins over all twenty range services.
    It matters separately because the client check is what a staff member meets *first*: the server
    is the authority and refuses `RANGE_PRICE_OUT_OF_BAND` regardless, but a console that warned at
    80.000 ₫ -- a price the shop charges -- would teach the counter to ignore the warning, and then
    the one that mattered would be ignored too.

    Both ends are inside because the owner published both. `PriceBand` says so in as many words.
    """

    minimum, maximum = AO_DAI_BAND
    cases = [
        str(minimum),
        str(maximum),
        "150000",
        "150.000",
        str(minimum - 1),
        str(maximum + 1),
        "0",
        "250000",
        "150,000",
        "170000.5",
        "",
        "   ",
        "abc",
    ]
    got = _run(
        "import { bandVerdict } from './src/core/bands.js';\n"
        f"const cases = {json.dumps(cases)};\n"
        f"const band = {json.dumps(list(AO_DAI_BAND))};\n"
        "console.log(JSON.stringify(cases.map((value) => bandVerdict(value, band[0], band[1]))));\n"
    )
    assert [item["state"] for item in got] == [
        "INSIDE",
        "INSIDE",
        "INSIDE",
        "INSIDE",
        "BELOW",
        "ABOVE",
        "BELOW",
        "ABOVE",
        "NOT_AN_AMOUNT",
        "NOT_AN_AMOUNT",
        "EMPTY",
        "EMPTY",
        "NOT_AN_AMOUNT",
    ]
    # `150.000` is the amount the console itself prints, and it has to read back as the same
    # number the customer was told -- the defect `parseDong` was rewritten to end.
    assert got[2]["amount"] == got[3]["amount"] == 150_000
    # An empty box is empty. Not the minimum, not the maximum, not the midpoint: the whole item
    # exists to keep a named person responsible for the number.
    assert got[10]["amount"] is None and got[11]["amount"] is None


def test_the_console_refuses_to_send_a_half_closed_revision() -> None:
    """`close_range_prices` refuses the whole revision when one band is still open.

    The console asks the same question before it sends, so the operator is told which line is
    missing instead of pressing and reading `RANGE_PRICE_REQUIRES_HUMAN` back about the revision.
    A revision with no banded lines at all is not ready either -- there would be nothing to close,
    and answering "ready" would let a caller send an empty attestation.
    """

    minimum, maximum = AO_DAI_BAND
    lines = [
        {
            "service_code": "DC_AO_DAI_TRADITIONAL",
            "band_minimum_vnd": minimum,
            "band_maximum_vnd": maximum,
        },
        {"service_code": "DC_FUR_COAT", "band_minimum_vnd": 200_000, "band_maximum_vnd": 400_000},
    ]
    got = _run(
        "import { bandReadiness } from './src/core/bands.js';\n"
        f"const lines = {json.dumps(lines)};\n"
        "const half = bandReadiness(lines, {DC_AO_DAI_TRADITIONAL: '150000'});\n"
        "const whole = bandReadiness(lines, "
        "{DC_AO_DAI_TRADITIONAL: '150000', DC_FUR_COAT: '250000'});\n"
        "const outside = bandReadiness(lines, "
        "{DC_AO_DAI_TRADITIONAL: '150000', DC_FUR_COAT: '500000'});\n"
        "const none = bandReadiness([], {});\n"
        "console.log(JSON.stringify({half, whole, outside, none}));\n"
    )
    assert got["half"]["ready"] is False
    assert got["half"]["blocked"] == [{"serviceCode": "DC_FUR_COAT", "state": "EMPTY"}]
    assert got["whole"]["ready"] is True and got["whole"]["blocked"] == []
    assert got["outside"]["ready"] is False
    assert got["outside"]["blocked"] == [{"serviceCode": "DC_FUR_COAT", "state": "ABOVE"}]
    assert got["none"]["ready"] is False


def test_the_console_never_checks_an_amount_against_a_band_it_was_not_given() -> None:
    """A missing bound is "unknown", never "fine".

    `QuoteLineView` promises that `net_amount_vnd` and the band are mutually exclusive and never
    both absent, so an unbounded banded line means the read model or the screen is wrong. Treating
    that as acceptable would let an unchecked amount reach the propose button wearing a green
    field, and the operator would have no way to know the check had not run.
    """

    got = _run(
        "import { bandVerdict } from './src/core/bands.js';\n"
        "const cases = [bandVerdict('150000', null, null), "
        "bandVerdict('150000', 80000, null), bandVerdict('150000', null, 240000)];\n"
        "console.log(JSON.stringify(cases.map((item) => item.state)));\n"
    )
    assert got == ["UNBOUNDED", "UNBOUNDED", "UNBOUNDED"]


def test_every_range_price_refusal_the_counter_can_meet_is_glossed_for_them() -> None:
    """The settlement rule, applied to the second vocabulary that decides what a customer pays.

    `RangePriceRefusal` is read out of the domain rather than restated, so a fourth member added
    later fails here instead of reaching a counter as a bare English token. Two older codes that
    reach the same panel by the same route are asserted alongside it:
    `RANGE_PRICE_REQUIRES_HUMAN`, which the engine answers for a line nobody has priced, and
    `HUMAN_APPROVAL_REQUIRED`, which `core/errors.js` mints itself for a 409 and which nothing
    glossed until this item.
    """

    domain = DOMAIN_RANGE_PRICES.read_text(encoding="utf-8")
    block = domain.split("class RangePriceRefusal(StrEnum):", 1)[1].split("\n\n\n", 1)[0]
    members = [
        value
        for _name, value in re.findall(
            r'^\s{4}([A-Z][A-Z0-9_]+) = "([A-Z0-9_]+)"', block, re.MULTILINE
        )
    ]
    assert members, "the refusal enum was not found; this test is reading the wrong file"

    glossed = _run(
        "import { REASON_NOTE, warningFor } from './src/core/i18n.js';\n"
        "const codes = Object.keys(REASON_NOTE);\n"
        "const warnings = Object.fromEntries("
        # `warningFor` answers `null` for a code that states a settled fact rather than a risk,
        # which PROMO-FIX-001 introduced so that `PROMOTION_NOT_PUBLISHED` -- on every quote in a
        # shop with no programme running -- stops raising a permanent HUMAN REQUIRED badge. The
        # token is read optionally here for that reason; every assertion below still names the
        # exact token it expects, so a code losing its badge by accident still fails.
        "codes.map((code) => [code, warningFor(code)?.token ?? null]));\n"
        "console.log(JSON.stringify({codes, warnings}));\n"
    )
    expected = [*members, "RANGE_PRICE_REQUIRES_HUMAN", "HUMAN_APPROVAL_REQUIRED"]
    missing = sorted({code for code in expected if code not in glossed["codes"]})
    assert not missing, f"range-price refusals with no Vietnamese note in i18n.js: {missing}"

    # And each is badged as needing a person. None of the three is a transient fault, and none is
    # fixable by retyping the same number harder -- `HUMAN REQUIRED` is the mandated token for that.
    for code in members:
        assert glossed["warnings"][code] == "HUMAN REQUIRED", code


def test_every_promotion_answer_the_counter_can_meet_is_glossed_for_them() -> None:
    """The settlement and range-price rule, applied to the vocabulary PROMO-WIRING-001 created.

    That item replaced one reason code with nine and glossed none of them, so `REASON_NOTE` returned
    nothing and `warningFor` fell through to `HUMAN REQUIRED` for all nine: every quote in the shop
    showed a bare SCREAMING_SNAKE_CASE token to a Vietnamese counter under a false badge. Replacing
    a code made the console strictly worse than leaving it alone, which is the failure this test
    exists to make impossible to repeat.

    The engine's own codes are read out of the domain rather than restated here, so one added later
    fails on this line instead of at a counter. The module-level codes are named, because they are
    what `quote_composition.py` emits on the engine's behalf and there is no enum to read them from.

    `APPROVAL_OUTSTANDING_APPLY_PROMOTION` is deliberately not in this list and deliberately not in
    `REASON_NOTE`. `_outstanding_approvals` can still mint it, but no path populates
    `required_approvals`, so no server can send it -- and glossing a code no server can send is the
    dead entry PROMO-WIRING-001 named as its reason for deleting `PROMOTION_NOT_EVALUATED`. The rule
    has to bind to this item's own work or it is not a rule. `test_reason_notes_gloss_only_codes_a_
    server_can_send` is the assertion in the other direction.
    """

    expected = [
        *(str(reason) for reason in PromotionReason),
        PROMOTION_NOT_PUBLISHED,
        PROMOTION_PENDING_BAND_CLOSE,
        PROMOTION_CHANGED_SINCE_QUOTE,
        PROMOTION_PUBLISHED_SINCE_APPROVAL,
    ]
    glossed = _run(
        "import { REASON_NOTE, warningFor } from './src/core/i18n.js';\n"
        "const codes = Object.keys(REASON_NOTE);\n"
        "const warnings = Object.fromEntries("
        "codes.map((code) => [code, warningFor(code)?.token ?? null]));\n"
        "console.log(JSON.stringify({codes, warnings}));\n"
    )
    missing = sorted({code for code in expected if code not in glossed["codes"]})
    assert not missing, f"promotion answers with no Vietnamese note in i18n.js: {missing}"

    # And each is badged for what it is. The split is the one `REASON_TO_WARNING` documents: an
    # answer nobody can act on raises no badge, an open question raises the mandated token for that
    # question. `PROMOTION_NOT_PUBLISHED` is the entry that forces the distinction to exist -- it is
    # on every quote the shop writes today, so badging it would mean a permanent warning on every
    # price.
    #
    # `PROMOTION_TARGET_REQUIRES_HUMAN` moved to that side in PROMO-FIX-002 and the name is now the
    # misleading part rather than the badge: the promotion does not apply to an unconfirmed service,
    # the line is charged at the published price, and the quote sells. Nothing is pending, so a
    # HUMAN REQUIRED badge sent staff hunting for an approval screen that does not exist -- and then
    # stayed on the accepted, paid, immutable revision for ever.
    assert glossed["warnings"]["PROMOTION_APPLIED"] is None
    assert glossed["warnings"][PROMOTION_NOT_PUBLISHED] is None
    assert glossed["warnings"]["PROMOTION_OUTSIDE_INTERVAL"] is None
    assert glossed["warnings"]["PROMOTION_NOT_TARGETED"] is None
    assert glossed["warnings"]["PROMOTION_TARGET_REQUIRES_HUMAN"] is None
    # `PROMOTION_STACKING_REQUIRES_HUMAN` moved to that side in PROMO-FIX-003, and the reason is
    # the same one that moved the line above it: there is no longer a question attached to it. The
    # badge asked the counter to choose between a `DEC-004` remedy credit and the programme's
    # discount, and the domain refused the choice it offered -- acceptance was blocked either way,
    # on a credit the db layer had already burnt. `redeem_remedy_credit` now settles it where the
    # two meet and always the same way: the debt the shop owes this customer is kept and the
    # programme's offer is withdrawn. Nothing is pending, the quote sells, and a HUMAN REQUIRED
    # badge would send staff looking for an approval screen that does not exist -- and would then
    # sit on the accepted, paid, immutable revision for ever.
    assert glossed["warnings"]["PROMOTION_STACKING_REQUIRES_HUMAN"] is None
    assert glossed["warnings"]["PROMOTION_ELIGIBILITY_UNRESOLVED"] == "PROMOTION PROVISIONAL"
    assert glossed["warnings"][PROMOTION_PENDING_BAND_CLOSE] == "PROMOTION PROVISIONAL"
    for code in (
        PROMOTION_CHANGED_SINCE_QUOTE,
        PROMOTION_PUBLISHED_SINCE_APPROVAL,
    ):
        assert glossed["warnings"][code] == "HUMAN REQUIRED", code


def test_reason_notes_gloss_only_codes_a_server_can_send() -> None:
    """The other direction of the rule above, for the promotion vocabulary this item owns.

    A gloss for a code no server can emit is not harmless decoration. It is the failure
    PROMO-WIRING-001 named when it deleted `PROMOTION_NOT_EVALUATED`: the console keeps answering a
    question nobody asks, and the entry that is genuinely missing is hidden behind a table that
    looks complete. PROMO-FIX-001 then created a fresh one --
    `APPROVAL_OUTSTANDING_APPLY_PROMOTION`, glossed and registered as a disclosure while no path
    populated `required_approvals` at all.

    So every `PROMOTION`-prefixed and `APPROVAL_OUTSTANDING_`-prefixed key in `REASON_NOTE` has to
    be a string the domain can actually produce. The domain's side of that list is built from the
    engine's enum plus the module constants, not typed out here, so the two cannot drift apart
    silently.
    """

    emittable = {
        *(str(reason) for reason in PromotionReason),
        PROMOTION_NOT_PUBLISHED,
        PROMOTION_PENDING_BAND_CLOSE,
        PROMOTION_CHANGED_SINCE_QUOTE,
        PROMOTION_PUBLISHED_SINCE_APPROVAL,
    }
    glossed = _run(
        "import { REASON_NOTE } from './src/core/i18n.js';\n"
        "console.log(JSON.stringify({codes: Object.keys(REASON_NOTE)}));\n"
    )
    dead = sorted(
        code
        for code in glossed["codes"]
        if (code.startswith("PROMOTION_") or code.startswith(APPROVAL_OUTSTANDING_PREFIX))
        and code not in emittable
    )
    assert not dead, f"i18n.js glosses promotion codes no server can send: {dead}"


def test_the_incident_scope_and_evidence_hash_are_produced_and_only_by_the_server() -> None:
    """The inverse of the test this replaces, which is what that test asked for.

    Until `DEC-028` nothing in this repository produced `contact_scope_hash` or
    `evidence_summary_hash`, both of which `POST /internal/v1/stores/{id}/incidents` required, so a
    staff member facing a customer could not complete the form. A test asserted that absence and its
    docstring said: "The day a producer is built, this test fails and that copy has to be rewritten
    — which is the point." A producer was built; this is the rewrite.

    Two claims now, and the second matters more than the first. A producer exists, and it is the
    server. A staff member who could name a contact scope could file an incident against a customer
    of their choosing, which is why invariant 9 keeps server-derived contact binding out of client
    control and why the field left the request model rather than becoming optional.
    """

    incidents = (ROOT / "packages/db/src/nha_trang_laundry_db/incidents.py").read_text(
        encoding="utf-8"
    )
    assert "def contact_scope_digest(" in incidents
    assert "def evidence_summary_digest(" in incidents
    assert "sha256(" in incidents

    # The staff request model carries neither hash, so neither can arrive from a client.
    main = (ROOT / "apps/api/src/nha_trang_laundry_api/main.py").read_text(encoding="utf-8")
    request_model = main[main.index("class IncidentOpenRequest(") :]
    request_model = request_model[: request_model.index("\nclass ")]
    assert "evidence_summary: str" in request_model
    assert "contact_scope_hash" not in request_model
    assert "evidence_summary_hash" not in request_model

    # And the console computes nothing: no digest call anywhere in the staff application.
    for path in (ROOT / "apps/web/src").rglob("*.js"):
        source = path.read_text(encoding="utf-8")
        for field in ("contact_scope_hash", "evidence_summary_hash"):
            assert field not in source, (
                f"{path.relative_to(ROOT)} names {field}; DEC-028 makes both digests server-side "
                "and a console that carries one has become a place to forge a contact scope"
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


def test_the_counter_guide_only_quotes_words_the_console_really_says() -> None:
    """`docs/HUONG_DAN_CA_LAM_VIEC_VI.md` is printed and taped next to the till.

    It tells the person at the counter what to do when the screen says a particular thing. A guide
    that quotes a sentence the console does not say is worse than no guide: staff look for words
    that are not there, decide the page is wrong, and stop using it — including for the two
    procedures that only exist on paper this week.

    So the quotes are checked against the client source. Rewording a message in `apps/web` fails
    here until the guide is updated with it, which is the only way a printed page stays true.
    """

    guide = (ROOT / "docs/HUONG_DAN_CA_LAM_VIEC_VI.md").read_text(encoding="utf-8")
    assert guide.strip(), "the counter guide is missing"

    client = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "apps/web").rglob("*.js"))
        if "sw.js" not in path.name
    )

    # Every control the guide tells staff to press, and every message it tells them how to react to.
    quoted = [
        "Phát phiếu",
        "Ghi nhận tiếp nhận",
        "Tính giá",
        # RANGE-PRICE-001. The three presses that close a published band, in the order the counter
        # meets them. They are pinned for the same reason as the rest: the guide is printed and
        # taped next to the till, and this procedure is the one a staff member has never done
        # before -- if the words on the page and the words on the screen drift apart, they will
        # fall back to the paper ticket the guide used to tell them to keep.
        "Lập bản khoảng giá",
        "Gửi giá cho chủ duyệt",
        "Áp dụng giá đã duyệt",
        "Khách đã chốt giá",
        "Tạo đơn",
        # REMEDY-001. The five presses that take an incident to an outcome, plus the three
        # sentences the guide tells staff to react to. Pinned for the same reason as the band
        # procedure: this one is new to every staff member, it is performed with an unhappy
        # customer at the counter, and it is the one place where reading the screen a step early
        # -- the ceiling and the owner requirement, before speaking -- is the whole procedure. If
        # the words on the taped page and the words on the screen drift apart they will go back to
        # settling it verbally, which is the state this item ended.
        "Đề xuất bồi hoàn",
        "Đọc mức trần và thời hạn",
        "Gửi đề nghị bồi hoàn",
        "Thực hiện bồi hoàn",
        "Áp dụng khoản giảm trừ",
        "Chép mã giảm trừ này lại ngay",
        "Chờ chủ tiệm duyệt",
        "Mất đồ — chưa có chính sách để áp dụng",
        "Đã duyệt lịch",
        "Ghi nhận tất toán",
        "Khách đã tự lấy đồ",
        "Đã thu tại quầy",
        "Đang ngoại tuyến",
        "Chưa biết lệnh có tới máy chủ hay không",
        "Đơn này vừa được người khác đổi",
        "Máy chủ không ghi nhận khoản này",
        "Cần người duyệt trước khi chuyển",
        "Phiên đăng nhập đã kết thúc",
        "Máy chủ gặp lỗi",
        "Chưa đọc được danh sách cửa hàng",
        "Chưa hỗ trợ",
    ]

    absent_from_guide = [phrase for phrase in quoted if phrase not in guide]
    assert not absent_from_guide, (
        "this test's list has drifted from the guide it checks; add or remove the phrase in both: "
        f"{absent_from_guide}"
    )

    absent_from_console = [phrase for phrase in quoted if phrase not in client]
    assert not absent_from_console, (
        "the counter guide quotes words the console no longer says, so a staff member following it "
        f"would look for text that is not on screen: {absent_from_console}"
    )


#: One `RemedyOptionsResponse` as the server really sends it, with figures that are `DEC-004`'s own
#: rather than round numbers chosen to make an assertion pass: the 100.000 ₫ staff ceiling, a
#: 24-hour defect window and a 7-day rewash window both still open, and two priced lines whose 5x
#: caps straddle the staff ceiling — 90.000 ₫ below it, 600.000 ₫ above. That straddle is the point
#: of the fixture, because "will this need the owner?" has to be answerable per line before an
#: amount exists.
REMEDY_OPTIONS = {
    "incident_id": "aaaaaaaa-1111-4333-8444-555555555555",
    "order_id": "bbbbbbbb-2222-4333-8444-555555555555",
    "policy_published": True,
    "staff_approval_ceiling_vnd": 100_000,
    "goods_returned_at": "2026-09-17T03:00:00+00:00",
    "rewash_window_closes_at": "2026-09-24T03:00:00+00:00",
    "rewash_window_open": True,
    "defect_window_closes_at": "2026-09-18T03:00:00+00:00",
    "defect_window_open": True,
    # `line-small` was charged 18.000 ₫, so 5x is 90.000 ₫ — inside what staff may approve.
    # `line-large` was charged 120.000 ₫, so 5x is 600.000 ₫ — an amount on it may need the owner.
    "damage_line_ceilings_vnd": {"line-large": 600_000, "line-small": 90_000},
    "late_delivery_credit_vnd": 17_000,
    "late_delivery_threshold_minutes": 120,
    "loss_reason_code": "LOSS_POLICY_UNRESOLVED",
}


def _plan(overrides: dict[str, Any], options: dict[str, Any] | None = None) -> Any:
    """Run `core/remedies.js` in Node against one draft and return the plan it produced."""

    draft = {"options": options if options is not None else REMEDY_OPTIONS, **overrides}
    return _run(
        "import { remedyPlan } from './src/core/remedies.js';\n"
        f"const plan = remedyPlan({json.dumps(draft)});\n"
        "console.log(JSON.stringify(plan));\n"
    )


def test_every_remedy_refusal_the_counter_can_meet_is_glossed_for_them() -> None:
    """The settlement rule, applied to the vocabulary that decides what the shop owes a customer.

    `RemedyRefusal` is read out of the domain rather than restated, so a fifteenth member added
    later fails here instead of reaching a counter as a bare English token. The nine codes
    `RemedyProposalRepository` raises that are not members of the enum are asserted alongside it,
    for the reason `record_settlement`'s four taught: they arrive by the same route, in the same
    envelope, in front of the same customer, and glossing only the enum ships a fix that looks
    complete.
    """

    domain = DOMAIN_REMEDIES.read_text(encoding="utf-8")
    block = domain.split("class RemedyRefusal(StrEnum):", 1)[1].split("\n\n\n", 1)[0]
    members = [
        value
        for _name, value in re.findall(
            r'^\s{4}([A-Z][A-Z0-9_]+) = "([A-Z0-9_]+)"', block, re.MULTILINE
        )
    ]
    assert len(members) >= 14, f"the refusal enum was not found or shrank: {members}"

    repository = (ROOT / "packages/db/src/nha_trang_laundry_db/remedies.py").read_text(
        encoding="utf-8"
    )
    raised = {
        code
        for code in re.findall(r'reason_code="([A-Z][A-Z0-9_]+)"', repository)
        # `STALE_VERSION` is already glossed for the settlement path and means the same thing here.
        if code != "STALE_VERSION"
    }

    glossed = _run(
        "import { REASON_NOTE, warningFor } from './src/core/i18n.js';\n"
        "const codes = Object.keys(REASON_NOTE);\n"
        "const warnings = Object.fromEntries("
        # `warningFor` answers `null` for a code that states a settled fact rather than a risk,
        # which PROMO-FIX-001 introduced so that `PROMOTION_NOT_PUBLISHED` -- on every quote in a
        # shop with no programme running -- stops raising a permanent HUMAN REQUIRED badge. The
        # token is read optionally here for that reason; every assertion below still names the
        # exact token it expects, so a code losing its badge by accident still fails.
        "codes.map((code) => [code, warningFor(code)?.token ?? null]));\n"
        "console.log(JSON.stringify({codes, warnings}));\n"
    )
    missing = sorted({code for code in [*members, *raised] if code not in glossed["codes"]})
    assert not missing, f"remedy refusals with no Vietnamese note in i18n.js: {missing}"

    # And each is badged as needing a person. None is a transient fault and none is fixable by
    # typing the same number harder — `HUMAN REQUIRED` is the mandated token for that.
    for code in members:
        assert glossed["warnings"][code] == "HUMAN REQUIRED", code


def test_a_remedy_refusal_arrives_as_a_refusal_and_not_as_bad_input() -> None:
    """`_raise_remedy_error` sends a 422 with `reason_code` and no `outcome` at all.

    Before this item that shape fell through to `INVALID`, and a closed 7-day window reached the
    counter as "Dữ liệu nhập không hợp lệ" — the exact sentence WORKFLOW-CONFORMANCE-001 removed
    from the settlement path, for the exact reason that it is untrue and sends a staff member to
    retype a number that was never wrong.
    """

    got = _run(
        "import { classify } from './src/core/errors.js';\n"
        "const closed = classify(422, {reason_code: 'REMEDY_WINDOW_CLOSED', authority: 'DEC-004', "
        "window_closes_at: '2026-09-18T03:00:00+00:00'}, {});\n"
        "const ceiling = classify(422, {reason_code: 'REMEDY_CEILING_EXCEEDED', "
        "authority: 'DEC-004', ceiling_vnd: 600000}, {});\n"
        "const stale = classify(422, {reason_code: 'STALE_VERSION'}, {});\n"
        "const junk = classify(422, {detail: 'nothing machine readable'}, {});\n"
        "console.log(JSON.stringify({closed: closed.kind, closedCodes: closed.reasonCodes, "
        "closedMessage: closed.message, ceiling: ceiling.kind, stale: stale.kind, "
        "junk: junk.kind}));\n"
    )
    assert got["closed"] == "NOT_SUPPORTED"
    assert got["closedCodes"] == ["REMEDY_WINDOW_CLOSED"]
    assert "không hợp lệ" not in got["closedMessage"]
    assert got["ceiling"] == "NOT_SUPPORTED"
    # The one remedy reason code that is a race rather than a policy answer keeps its own kind, so
    # the operator is told to reload rather than told the shop does not do this.
    assert got["stale"] == "STALE"
    # And a 422 with no machine-readable reason is still a validation failure. This branch narrows
    # one shape; it does not turn every 422 into a policy refusal.
    assert got["junk"] == "INVALID"


def test_the_ceiling_and_the_owner_requirement_are_known_before_an_amount_is_typed() -> None:
    """The point of the whole screen, asserted on the module that decides it.

    `TASK-remedy-001`: staff must never discover that the owner is required after filling the form
    in. With a line chosen and the money box still empty, the plan already carries that line's
    server-computed cap and says whether an amount on it can reach the owner.
    """

    small = _plan(
        {"kind": "DAMAGE_COMPENSATION", "storeFaultAttested": True, "lineId": "line-small"}
    )
    large = _plan(
        {"kind": "DAMAGE_COMPENSATION", "storeFaultAttested": True, "lineId": "line-large"}
    )

    assert small["state"] == "AMOUNT_MISSING" and large["state"] == "AMOUNT_MISSING"
    assert small["ceilingVnd"] == 90_000 and large["ceilingVnd"] == 600_000
    assert small["ownerThresholdVnd"] == 100_000 and large["ownerThresholdVnd"] == 100_000
    # 90.000 ₫ is the whole of what this line can reach and it is under the staff ceiling, so no
    # amount on it can need the owner. 600.000 ₫ can, and the form says so before a digit is typed.
    assert small["ownerPossible"] is False
    assert large["ownerPossible"] is True
    # And the window is on screen with it, not discovered afterwards.
    assert large["windowClosesAt"] == REMEDY_OPTIONS["defect_window_closes_at"]
    assert large["windowOpen"] is True


def test_the_staff_approval_ceiling_is_read_as_the_owner_published_it() -> None:
    """`DEC-004`: staff may approve *up to* 100.000 ₫; above that the owner must.

    The boundary is inclusive on the staff side, which is what `requires_owner_approval =
    amount_vnd > policy.staff_approval_ceiling_vnd` says in the domain. One đồng decides it, so
    both sides of that đồng are asserted rather than a comfortable distance either way.
    """

    base = {"kind": "DAMAGE_COMPENSATION", "storeFaultAttested": True, "lineId": "line-large"}
    at = _plan({**base, "typedAmount": "100000"})
    over = _plan({**base, "typedAmount": "100001"})

    assert at["state"] == "READY" and at["requiresOwner"] is False
    assert over["state"] == "READY" and over["requiresOwner"] is True


def test_an_amount_above_the_line_ceiling_is_refused_with_the_ceiling_and_never_reduced_to_it() -> (
    None
):
    """Refused, not truncated. Truncating pays the customer less than the counter agreed."""

    plan = _plan(
        {
            "kind": "DAMAGE_COMPENSATION",
            "storeFaultAttested": True,
            "lineId": "line-small",
            "typedAmount": "90001",
        }
    )
    assert plan["state"] == "ABOVE_CEILING"
    assert plan["ceilingVnd"] == 90_000, "the refusal must name the ceiling"
    # The amount is carried as typed so the screen can show what was asked for beside what is
    # allowed. It is emphatically not silently replaced by the ceiling.
    assert plan["amountVnd"] == 90_001
    assert (
        _run(
            "import { remedyPlan, remedyProposalBody } from './src/core/remedies.js';\n"
            "const plan = remedyPlan("
            + json.dumps(
                {
                    "options": REMEDY_OPTIONS,
                    "kind": "DAMAGE_COMPENSATION",
                    "storeFaultAttested": True,
                    "lineId": "line-small",
                    "typedAmount": "90001",
                }
            )
            + ");\n"
            "console.log(JSON.stringify(remedyProposalBody(plan, {lineId: 'line-small'})));\n"
        )
        is None
    ), "a refused plan must produce no request body at all"


def test_a_loss_carries_no_ceiling_no_window_and_no_owner_threshold() -> None:
    """The inverse assertion the packet asks for: no test may assert a loss ceiling.

    So this asserts the absence of every figure instead. `DEC-004` carries loss forward as
    undecided and forbids reaching the damage numbers by analogy, and the most likely way this item
    goes wrong is a helpful console filling one of these in.
    """

    plan = _plan({"kind": "LOST_ITEM", "storeFaultAttested": True, "typedAmount": "500000"})

    assert plan["state"] == "LOSS_UNRESOLVED"
    assert plan["ceilingVnd"] is None and plan["hasCeiling"] is False
    assert plan["windowClosesAt"] is None and plan["windowOpen"] is None
    assert plan["ownerThresholdVnd"] is None and plan["ownerPossible"] is False
    assert plan["requiresOwner"] is None
    # Even with an amount typed into the box before the kind was changed, none of it survives onto
    # a loss: there is nothing on this plan a caller could read a figure out of.
    assert plan["amountVnd"] is None


def test_an_unpublished_policy_fails_every_kind_closed_including_the_free_one() -> None:
    """Invariant 11, and the arm that is easy to miss.

    A rewash moves no money, so it looks like the one kind that could proceed without figures. It
    cannot: the 7-day window is itself one of the owner's published numbers, and the server refuses
    it with `REMEDY_POLICY_UNPUBLISHED` like every other kind.
    """

    unpublished = {
        "incident_id": REMEDY_OPTIONS["incident_id"],
        "order_id": REMEDY_OPTIONS["order_id"],
        "policy_published": False,
        "loss_reason_code": "LOSS_POLICY_UNRESOLVED",
    }
    for kind in ("FREE_REWASH", "DAMAGE_COMPENSATION", "LATE_DELIVERY_CREDIT"):
        plan = _plan({"kind": kind, "storeFaultAttested": True}, options=unpublished)
        assert plan["state"] == "POLICY_UNPUBLISHED", kind
        assert plan["ceilingVnd"] is None, kind
        assert plan["ownerThresholdVnd"] is None, kind


def test_no_recorded_handover_stops_the_window_rather_than_starting_one_now() -> None:
    """Migration 0042's honest consequence, on the console side.

    Every order released before it carries `production_released_at = NULL`, and the only truthful
    reading of that is "the shop has no record of when this customer collected". Substituting now,
    the row's creation or the settlement would invent a measurement nobody made — so the plan stops
    and carries no window at all.
    """

    blind = {**REMEDY_OPTIONS, "goods_returned_at": None}
    for kind in ("FREE_REWASH", "DAMAGE_COMPENSATION"):
        plan = _plan(
            {"kind": kind, "storeFaultAttested": True, "lineId": "line-small"}, options=blind
        )
        assert plan["state"] == "WINDOW_EVIDENCE_MISSING", kind

    # The late-delivery credit is not measured from the handover at all -- `DEC-004` gates it on
    # the lateness and the fault -- so it is unaffected, which is the distinction this asserts.
    credit = _plan(
        {"kind": "LATE_DELIVERY_CREDIT", "storeFaultAttested": True, "typedLateness": "180"},
        options=blind,
    )
    assert credit["state"] == "READY"


def test_a_null_late_delivery_credit_is_unavailable_and_never_zero() -> None:
    """House style: a null total is never rendered as 0, and `—` means unknown.

    The server sends `null` when no delivery it recorded could have been late or nothing was ever
    settled. Rendering that as "0 ₫" would tell a customer the shop owes them nothing, which is a
    different statement from "this system cannot compute a figure here".
    """

    plan = _plan(
        {"kind": "LATE_DELIVERY_CREDIT", "storeFaultAttested": True, "typedLateness": "180"},
        options={**REMEDY_OPTIONS, "late_delivery_credit_vnd": None},
    )
    assert plan["state"] == "CREDIT_UNAVAILABLE"
    assert plan["amountVnd"] is None and plan["ceilingVnd"] is None


def test_each_kind_sends_exactly_the_keys_its_own_shape_owns() -> None:
    """`RemedyProposalRequest` is a `StrictRequest` and `_refuse_wrong_shape` refuses a stray field.

    A rewash carrying an amount would be accepted by a lenient server and the staff member would
    reasonably believe the shop had agreed to pay it. Here the body is built from the kind, so
    there is no field to leave behind when the picker changes.
    """

    bodies = _run(
        "import { remedyPlan, remedyProposalBody } from './src/core/remedies.js';\n"
        f"const options = {json.dumps(REMEDY_OPTIONS)};\n"
        "const make = (draft) => remedyProposalBody(remedyPlan({options, ...draft}), draft);\n"
        "console.log(JSON.stringify({\n"
        "  rewash: make({kind: 'FREE_REWASH', storeFaultAttested: true}),\n"
        "  damage: make({kind: 'DAMAGE_COMPENSATION', storeFaultAttested: true, "
        "lineId: 'line-small', typedAmount: '50.000'}),\n"
        "  late: make({kind: 'LATE_DELIVERY_CREDIT', storeFaultAttested: true, "
        "typedLateness: '180'}),\n"
        "  loss: make({kind: 'LOST_ITEM', storeFaultAttested: true}),\n"
        "}));\n"
    )

    assert bodies["rewash"] == {"kind": "FREE_REWASH", "store_fault_attested": True}
    # `50.000` is how this console prints an amount and how a staff member writes one; `parseDong`
    # reads the dot as grouping, which is the defect COUNTER-DEFECTS-001 fixed for settlements.
    assert bodies["damage"] == {
        "kind": "DAMAGE_COMPENSATION",
        "store_fault_attested": True,
        "order_line_id": "line-small",
        "amount_vnd": 50_000,
    }
    assert bodies["late"] == {
        "kind": "LATE_DELIVERY_CREDIT",
        "store_fault_attested": True,
        "attested_late_by_minutes": 180,
    }
    # And loss produces nothing to send. The console records the complaint on `#/incidents` and
    # stops; it does not raise a proposal nothing could show back.
    assert bodies["loss"] is None


def test_nobody_attesting_store_fault_blocks_every_kind() -> None:
    """`DEC-004` rests every remedy on a staff member determining the store was at fault."""

    for kind, extra in (
        ("FREE_REWASH", {}),
        ("DAMAGE_COMPENSATION", {"lineId": "line-small", "typedAmount": "50000"}),
        ("LATE_DELIVERY_CREDIT", {"typedLateness": "180"}),
    ):
        plan = _plan({"kind": kind, "storeFaultAttested": False, **extra})
        assert plan["state"] == "FAULT_NOT_ATTESTED", kind
