"""A disclosure may not outlive the fact that made it true.

`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md` §1 calls the console's honesty chrome "spec-mandated and
contract-adjacent". Measured before this item: 59 such slots across `apps/web/src`, and not one
appeared in `test_staff_console_contract.py` or `verify_console_interaction.py` — the console's
whole safety net, since `apps/web` has no unit tests at 12,830 lines. Coverage was zero.

Registration alone is the weaker half: it guarantees a reworded disclosure changes its slot id and
fails the check until someone re-reads the sentence. The stronger half is here — the bound entries,
where a *code* change falsifies the sentence and a test says so.

Two binding kinds carry that weight today.

`SERVER_GATE` asserts the direction that matters. The console must never claim a capability is open
to someone the server refuses, because that renders an enabled control that then 403s. Stricter is
fail-safe and allowed: `SHADOW_READ` lists four roles where `current_principal` admits six.

`ABSENT_TABLE` binds the gap notices, which are the most falsifiable claims on the console — "there
is no `delivery_bundles`" stops being true the moment somebody writes the migration, and that is
exactly the day the screen must stop saying it.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import nha_trang_laundry_api.main as api_main
import pytest
import yaml
from fastapi import HTTPException
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "specs/contracts/console-disclosures-v1.yaml"
MIGRATIONS = ROOT / "packages/db/migrations"
RBAC = ROOT / "apps/web/src/core/rbac.js"

#: The console writes role names in its own vocabulary; the server uses `StaffRole`.
CONSOLE_ROLE_ALIASES = {
    "OWNER": "OWNER_ADMIN",
    "APPROVER": "OPS_APPROVER",
    "OPERATOR": "OPERATOR",
    "AUDITOR": "AUDITOR",
    "DRIVER": "DRIVER",
    "ACCOUNTANT": "ACCOUNTANT",
}


def _module(name: str, path: Path) -> Any:
    """Load a `scripts/` module by path, registering it before execution.

    Registration is not optional here: `DisclosureSlot` is a `slots=True` dataclass, and dataclass
    field resolution looks its own module up in `sys.modules`. A module executed without being
    registered resolves to `None` there and raises inside the standard library rather than in
    anything this test wrote.
    """
    if name in sys.modules:
        return sys.modules[name]
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _registry() -> dict[str, Any]:
    loaded = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _entries(kind: str) -> list[dict[str, Any]]:
    return [e for e in _registry()["disclosures"] if e["binding"]["kind"] == kind]


def _probe_gate(gate_name: str) -> tuple[set[str], bool]:
    """What the server gate actually admits, established behaviourally rather than by parsing.

    Probing survives a refactor that changes how the gate is written; a source-text assertion does
    not, and would give a false green exactly when the rule moved.
    """
    if gate_name == "current_principal":
        return {role.value for role in StaffRole}, False

    gate = getattr(api_main, gate_name)
    admitted: set[str] = set()
    admits_without_mfa = False
    for role in StaffRole:
        for mfa_verified in (True, False):
            principal = StaffPrincipal(
                staff_user_id=uuid4(),
                oidc_subject="probe",
                roles=frozenset({role}),
                mfa_verified=mfa_verified,
                session_id=uuid4(),
            )
            try:
                gate(principal)
            except HTTPException:
                continue
            admitted.add(role.value)
            admits_without_mfa = admits_without_mfa or not mfa_verified
    return admitted, not admits_without_mfa


def _console_capabilities() -> dict[str, tuple[set[str], bool]]:
    source = RBAC.read_text(encoding="utf-8")
    declared: dict[str, tuple[set[str], bool]] = {}
    for match in re.finditer(r"(\w+):\s*\{\s*roles:\s*\[([^\]]*)\],\s*mfa:\s*(true|false)", source):
        roles = {
            CONSOLE_ROLE_ALIASES[token.strip().strip('"')]
            for token in match.group(2).split(",")
            if token.strip()
        }
        declared[match.group(1)] = (roles, match.group(3) == "true")
    return declared


def test_every_disclosure_slot_is_registered() -> None:
    """Enumerated from source, so a new disclosure cannot be added without an entry."""
    disclosures = _module("console_disclosures", ROOT / "scripts/console_disclosures.py")
    source_ids = {slot.slot_id for slot in disclosures.enumerate_slots()}
    registered = {entry["slot_id"] for entry in _registry()["disclosures"]}
    assert source_ids == registered


def test_the_committed_registry_matches_a_fresh_generation() -> None:
    generator = _module(
        "generate_console_disclosure_registry",
        ROOT / "scripts/generate_console_disclosure_registry.py",
    )
    assert REGISTRY.read_text(encoding="utf-8") == generator.generate()


def test_rewording_a_disclosure_changes_its_identity() -> None:
    """Identity includes the text hash, which is what forces a re-read rather than a silent edit."""
    disclosures = _module("console_disclosures", ROOT / "scripts/console_disclosures.py")
    slot = disclosures.enumerate_slots()[0]
    reworded = disclosures.DisclosureSlot(
        module=slot.module, line=slot.line, key=slot.key, text=slot.text + " Và thêm một câu."
    )
    assert reworded.slot_id != slot.slot_id

    reflowed = disclosures.DisclosureSlot(
        module=slot.module, line=slot.line + 3, key=slot.key, text="  ".join(slot.text.split())
    )
    assert reflowed.slot_id == slot.slot_id, "reformatting is not a content change"


@pytest.mark.parametrize("entry", _entries("SERVER_GATE"), ids=lambda e: e["binding"]["capability"])
def test_the_console_is_never_more_permissive_than_the_server(entry: dict[str, Any]) -> None:
    """The dangerous direction: an enabled control the server would refuse.

    A console stricter than the server merely hides something; a console looser than the server
    promises an operator an action that 403s in front of a waiting customer.
    """
    capability = entry["binding"]["capability"]
    console_roles, console_mfa = _console_capabilities()[capability]
    server_roles, server_requires_mfa = _probe_gate(entry["binding"]["gate"])

    assert console_roles <= server_roles, (
        f"{capability}: the console offers {sorted(console_roles - server_roles)} "
        f"which {entry['binding']['gate']} refuses"
    )
    if server_requires_mfa:
        assert console_mfa, f"{capability}: the server demands MFA and the console does not say so"


@pytest.mark.parametrize("entry", _entries("ABSENT_TABLE"), ids=lambda e: e["slot_id"])
def test_a_disclosure_claiming_a_table_is_absent_is_still_true(entry: dict[str, Any]) -> None:
    """The gap notices are the most falsifiable claims the console makes; hold them to it."""
    migrations = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )
    for table in entry["binding"]["tables"]:
        assert not re.search(rf"CREATE TABLE\s+{re.escape(table)}\b", migrations), (
            f"{entry['module']} still tells operators {table} does not exist, "
            "but a migration now creates it. The disclosure is false and must change."
        )


def test_bound_entries_are_not_a_rounding_error() -> None:
    """Guard the honest framing: most slots are descriptive, and that is stated rather than hidden.

    This fails if bindings are silently dropped, which is the cheap way to make a red suite green.
    """
    counts = _registry()["counts"]
    # 12 since APPROVAL-DECIDE-001: `APPROVALS_DECIDE` joined `core/rbac.js`, and its role claim
    # binds to the same `require_approval_staff` gate the queue read does. The floor moves up
    # because a new console capability without a server gate behind it is exactly what this count
    # exists to notice.
    #
    # 13 and 5 since OPS-BOARD-001 added three capabilities. `DAY_SUMMARY_READ` is the SERVER_GATE
    # one: `require_operations_staff` is the whole of its rule, so 12 + 1 = 13. `SLA_BOARD_READ`
    # and `EXPORT_DATA` are REPOSITORY_ROLES, so 3 + 2 = 5 -- the board route depends on
    # `current_principal` and the set that decides it is `SHADOW_READ_ROLES`, and the export's
    # `EXPORT_ROLES` is re-checked inside the repository where a route rewrite cannot drop it.
    #
    # 6 REPOSITORY_ROLES since API-INTEGRITY-002: `UNKNOWN_SENDS_READ` is the unknown-send queue,
    # now store-scoped and MFA-gated inside `list_unknown_sends`. Its route depends on
    # `current_principal`, so it binds to `SHADOW_READ_ROLES` for the same reason SHADOW_READ does.
    #
    # 14 since CONSENT-TRANSACTIONAL-001: `SERVICE_MESSAGING_RELEASE` (`DEC-033`) binds to
    # `require_approval_staff`, the release route's own gate. 13 + 1 = 14.
    assert counts.get("SERVER_GATE") == 14
    assert counts.get("REPOSITORY_ROLES") == 6
    assert counts.get("ALL_AUTHENTICATED") == 1
    # 4 since REMEDY-001's console half. The entry that went said there was no `remedies`,
    # `credit_grants` or `credit_ledger_entries` table and, in the same sentence, that every
    # incident therefore stands still at OPEN. Both halves stopped being true together:
    # `remedy_proposals` and `remedy_credits` landed with the server half, and
    # `RemedyProposalRepository.execute` writes `customer_incidents.status = 'CLOSED'` inside the
    # same transaction as the credit. The three tables it *named* are still absent, so this test
    # would have stayed green over a false sentence — which is the reason the entry and its
    # binding were deleted together rather than the binding being left to outlive its claim.
    assert counts.get("ABSENT_TABLE") == 4
    # ABSENT_ROUTE is gone entirely: both slots claimed the intake and production transitions
    # had no route, and both became false on 2026-08-29 when the routes were added.
    assert "ABSENT_ROUTE" not in counts
    assert counts.get("RESPONSE_SHAPE") == 1
    # READ_ONLY_MODULE is gone entirely, like ABSENT_ROUTE and ABSENT_WRITER before it, and for the
    # same reason: its one slot said the approvals screen writes nothing to the server, which was
    # true only because `GET /internal/v1/approvals` withheld the three fields a decision needs.
    # APPROVAL-DECIDE-001 projected them, the screen decides, and the claim died with the
    # limitation. Asserted absent rather than deleted from this list so that re-adding a read-only
    # claim has to be a deliberate act.
    assert "READ_ONLY_MODULE" not in counts
    assert counts.get("MODEL_SEAM") == 2
    # ABSENT_WRITER is gone entirely, not reduced. Both slots claimed that nothing raises a
    # quote revision to APPROVED_EXACT; DEC-021 resolved on 2026-08-25 and the acceptance path
    # landed, so both claims became false on the same day and were retired together.
    assert "ABSENT_WRITER" not in counts
    # 160 since the intake and production routes landed on 2026-08-29: two ABSENT_ROUTE entries
    # said those transitions had no HTTP route, a nine-lens verification pass found that this
    # made every order stop dead at CONFIRMED, and the routes were built. The class is now empty.
    # 162 since DEC-023 was resolved and FULFILMENT-001 landed: the entry saying delivery
    # orders cannot be settled or closed was deleted, because they now can. The M3 delivery
    # entry was narrowed rather than deleted -- delivery_legs exists, delivery_bundles and
    # distance_measurements still do not.
    # 164 since DEC-023 was opened: a `#/gaps` entry says delivery orders cannot be settled or
    # closed, which the 2026-08-26 lifecycle measurement found by running both fulfilment modes
    # end to end. Two gaps closed and one opened in three days; the register tracks reality.
    # 161 since COUNTER-TICKET-001: the `#/gaps` entry saying a walk-in cannot be taken in was
    # deleted too, because DEC-013 resolved and the counter can now issue a ticket. Two gaps
    # closed in two days, and both entries went rather than being reworded.
    # 164 since QUOTE-ACCEPT-001: the `#/gaps` entry claiming no path raises a quote to
    # APPROVED_EXACT was deleted rather than reworded, because the gap it described is closed --
    # DEC-021 resolved and the acceptance path landed. Its slots went with it. The guard that
    # caught this is CONSOLE-ORDER-GAP-001's, which was written to fail on exactly this day.
    # 165 since the review log landed: one new DESCRIPTIVE guardrail on `screens/shadow.js`
    # telling the reader that nothing in that panel was ever sent to a customer.
    # 161 since BOUNDS-AND-TRUTH-001 and the 2026-08-31 re-verification: three settlement-panel
    # sentences were corrected, and two more that had gone false as the system caught up with
    # them -- the approvals screen saying non-order approvals "never appear here" (the queue reads
    # the approval's own store since migration 0034, so they all do), and the order form saying
    # "duong duyet gia chua ton tai" (the acceptance path landed in QUOTE-ACCEPT-001, and orders
    # are created through it daily). The order-form correction splits into one more slot than the
    # sentence it replaced, which is the whole of the delta.
    # 175 since CONSOLE-LIFECYCLE-001 added `blockedBy` to DISCLOSURE_KEYS: fourteen `#/gaps`
    # entries name what stops a capability, and until now the key that names *decisions* was the
    # one key the registry did not read. That is how the same defect landed twice -- an entry
    # saying a RESOLVED decision was a pending blocker, invisible to every check. The count rose
    # by exactly the number of long-enough `blockedBy` values; the short ones still cannot bind,
    # which is why the guard for that class reads the module source instead.
    # 179 since ACQUISITION-ATTRIBUTION-001: one new `#/gaps` entry, four of whose keys are long
    # enough to bind. The gap is a real one and is the honest half of that item -- an order now
    # records where the customer came from and the value can never be changed, and no screen reads
    # it back, so a mis-tap at the counter is invisible from the moment it is made. Naming that
    # here was cheaper than a read path threaded through the order list and the board, and the
    # counter is warned in the field's own hint that the entry is final.
    # 183 since WORKFLOW-CONFORMANCE-001, and the delta is +5 new against -1 re-keyed. The five are
    # the settlement refusal vocabulary: `MESSAGES.NOT_SUPPORTED` and one `REASON_NOTE` gloss for
    # each member of the domain's `SettlementRefusal`. They exist because driving the counter found
    # that a part payment -- the commonest thing a customer does wrong at a till -- reached the
    # screen as "Dữ liệu nhập không hợp lệ", which is both untrue and the one sentence that makes a
    # staff member retype a number that was never wrong.
    #
    # The one that went is `ui/components.js#notice:9525c4bc0839`, re-keyed rather than deleted: the
    # slot extractor's capture window shifted when `errorNotice` gained a paragraph, and the same
    # sentence is now registered whole ("…đã được lưu trong khi không có gì được lưu cả") instead of
    # cut off mid-clause. The source sentence did not change; what it is registered as did, which is
    # this registry doing its job.
    # 187 with the same item's second finding: opening an incident cannot be done by anybody. The
    # request requires `contact_scope_hash` and `evidence_summary_hash` and nothing in this
    # repository produces either, so the form was unfinishable while its hints told staff to "chép
    # nguyên văn" from somewhere. Four slots: the two paragraphs of the new warning on `#/incidents`
    # and the `missing`/`blockedBy` of the `#/gaps` entry that now names it.
    # 191 after the reconciliation round found the first fix half-done. `record_settlement` raises
    # four reason codes that are not members of `SettlementRefusal` -- ORDER_NOT_FOUND,
    # ORDER_NOT_ACTIVE, ALREADY_SETTLED, STALE_VERSION -- and they reach the same panel by the same
    # route with `decision: null`. Glossing only the enum shipped a fix that looked complete and
    # still put a bare English token in front of a counter holding somebody's money.
    # 194 after APPROVAL-DECIDE-001. The approvals screen stopped being read-only, so its copy was
    # rewritten where it had described the limitation rather than the screen: the eyebrow, the lede,
    # the guardrail, the per-card refusal and the "why is Duyệt disabled" notice. The refusal did
    # not disappear -- it narrowed from "this screen cannot decide anything" to "this screen cannot
    # show you a MESSAGE_DRAFT, so it will not let you approve one" -- and a narrower true sentence
    # costs more slots than a broad one. Net +3 against the READ_ONLY_MODULE slot that went.
    # 191 after INCIDENT-INTAKE-001 implemented DEC-028: net -3, being -4 removed, +1 added and one
    # re-keyed. The four that went are the four this file's note above counted in at 187 -- the two
    # paragraphs of the "biểu mẫu này chưa dùng được" warning on `#/incidents` and the
    # `missing`/`blockedBy` of the `#/gaps` entry naming the decision request. They were deleted
    # rather than reworded because the claim they made -- nothing in this repository produces
    # `contact_scope_hash` or `evidence_summary_hash`, so the form cannot be completed -- stopped
    # being true: the server derives both, and the counter sends the complaint in words. This is a
    # disclosure retiring because the limitation behind it closed, which is the only legitimate way
    # one leaves. The one added is its replacement in kind: an incident whose evidence was purged at
    # 365 days, or which the agent path opened with no summary, now renders a sentence saying the
    # description is no longer kept and that this is the retention schedule rather than data loss.
    # The re-keyed one is the screen lede, which no longer promises two hash fields.
    # 194 after ASSISTANT-RETENTION-001: net +3, all additions, nothing reworded and nothing
    # retired. `ASSISTANT_TRANSCRIPT` became a real 180-day PURGE class, so `question` and `answer`
    # are `str | None` on the assistant routes and the stream route answers 410 for a turn whose
    # words are gone. A null used to render an empty chat bubble -- a silent gap, which is the
    # failure this console treats as worse than an error. The three new slots are the two bubbles'
    # replacement sentences on `#/assistant` and `MESSAGES.DISPOSED` in `core/errors.js`, and all
    # three say the same true thing in different registers: the turn is still on the books with its
    # intent, its reason codes, its actor and its timestamp, and only the text was disposed of on a
    # published schedule. This is the same kind of slot INCIDENT-INTAKE-001 added for a purged
    # `evidence_summary`, for the same reason, which is why they are worded to match.
    # 213 after RANGE-PRICE-001's console half: +22 added, -3 retired, 6 re-keyed, net +19.
    #
    # The additions are what a screen costs once it performs a procedure it previously only
    # refused, and they divide into three:
    #   * 4 in `core/i18n.js` -- one `REASON_NOTE` gloss per member of the domain's new
    #     `RangePriceRefusal`, plus `HUMAN_APPROVAL_REQUIRED`, which `core/errors.js` has minted
    #     itself for a 409 since long before this item and which no gloss covered. All four follow
    #     the settlement vocabulary's rule: a refusal a staff member meets with a customer in front
    #     of them may not arrive as a bare English token.
    #   * 3 in `ui/components.js` -- the three sentences `bandInput` can show while an amount is
    #     being typed (below the band, above it, and not a readable amount). This is the
    #     `pricingCliffNotice` property applied to money: the warning arrives before the press,
    #     not after the refusal.
    #   * 15 in `screens/quotes.js` -- the band-mode offer, the three-step explanation, the
    #     "do not leave this screen" warning, the half-closed-revision refusal, the two read-back
    #     failures, the store-scope not-found notice on the read-only revision panel, and that
    #     panel's guardrail.
    #
    # The three retired are one `#/gaps` entry, "Doc lai mot ban bao gia", deleted rather than
    # reworded because what it claimed -- no GET for a single revision, no endpoint returning a
    # quote's lines -- stopped being true when `GET /internal/v1/stores/{store}/quotes/{quote}`
    # landed. That is the only legitimate way a disclosure leaves, and it is the same move
    # QUOTE-ACCEPT-001 and INCIDENT-INTAKE-001 made before it.
    #
    # The six re-keyed are sentences whose content changed, which is this registry working rather
    # than churn: the approvals screen's "why is Duyet disabled" notice and the neighbouring
    # `#/gaps` entry both named an order as the only viewable resource type, and a quote revision
    # is now viewable too; that `#/gaps` group lede counted four entries and now counts three; and
    # the `#/quotes` lede no longer describes a screen that only reads prices back.
    # 277 after REMEDY-001's console half: +68 added, -4 removed, of which 2 of the removals are
    # re-keyed sentences and 2 are a retirement. Net +64, and it divides into four:
    #
    #   * 23 in `core/i18n.js` -- one `REASON_NOTE` gloss for each of the fourteen members of the
    #     domain's `RemedyRefusal`, plus the nine reason codes `RemedyProposalRepository` raises
    #     that are states rather than policy answers (`REMEDY_APPROVAL_REQUIRED`,
    #     `REMEDY_APPROVAL_EXPIRED`, `REMEDY_APPROVAL_NOT_BOUND`, `REMEDY_ALREADY_EXECUTED`,
    #     `REMEDY_PROPOSAL_NOT_FOUND`, `REMEDY_CREDIT_NOT_FOUND`, `REMEDY_INCIDENT_NOT_FOUND`,
    #     `REMEDY_ORDER_REVISION_UNREADABLE`, `REMEDY_ORDER_TIMESTAMP_INVALID`). This is the rule
    #     the settlement and range-price vocabularies already set: `_raise_remedy_error` answers
    #     422 with a bare code, and a refusal a staff member meets with a customer in front of them
    #     may not arrive as a bare English token.
    #   * 36 in `screens/remedies.js`, a screen that performs a procedure the console previously
    #     could not reach at all: 3 panel guardrails, 1 lede, 4 notice bodies with 2 titles, 9
    #     hints, the loss refusal's `missing`/`blockedBy`/`today` under `components.unsupported`,
    #     and -- new in kind -- 10 `PLAN_NOTE` and 4 `KIND_NOTE` entries. Those last fourteen are
    #     why `CLAIM_TABLES` in `scripts/console_disclosures.py` grew past `core/`: this screen
    #     renders its refusal sentences through a lookup (`hint: KIND_NOTE[draft.kind]`,
    #     `planNotice(plan)`), so every literal-scanning pass in the enumerator saw a variable and
    #     registered nothing. Ten refusal sentences and the loss claim would otherwise have been
    #     the least-covered honesty chrome on the console, in its newest screen.
    #   * 6 in `screens/gaps.js` -- two new entries at three bindable keys each, replacing the one
    #     that was retired. They are narrower and still true: there is no route listing an order's
    #     or a ticket's unredeemed credits, and none listing an incident's proposals or the
    #     `POLICY_UNRESOLVED` loss cases waiting on the owner.
    #   * 3 across `screens/incidents.js` and `screens/orderDetail.js` -- 2 re-keyed and 1 added.
    #     The incidents screen said "trong API này không có route nào đặt hai giá trị đó" and
    #     linked the remedy workflow as a gap; `remedy_decided` now has exactly one writer, so both
    #     sentences were rewritten to say what is still true (`fault_decided` has none) and where
    #     the remedy is decided. The addition is order detail's line explaining why the cross-link
    #     is a link and not a button: a remedy is keyed by `incident_id`, and no route lists an
    #     order's incidents, so the screen cannot pick the complaint for the operator.
    #
    # The two genuine retirements are the `#/gaps` "Bồi hoàn sự cố" entry's `missing` and
    # `blockedBy`, deleted rather than reworded because the limitation they described closed --
    # the same move QUOTE-ACCEPT-001, INCIDENT-INTAKE-001 and RANGE-PRICE-001 each made before it.
    #
    # 286 after the promotion work: +10 added, -1 retired, all in `core/i18n.js`. Net +9.
    #
    # The retirement is `REASON_NOTE.PROMOTION_NOT_EVALUATED`, deleted rather than reworded, because
    # PROMO-WIRING-001 deleted the code it glossed from the domain: a promotion is assessed on every
    # revision now, so no server can send "chưa xét khuyến mãi" and a gloss for it could only ever
    # mislead. That is the same legitimate exit the four items above took.
    #
    # The ten additions are one `REASON_NOTE` sentence for each promotion answer a quote can now
    # carry, and counting them is the measure of how wide the hole was: replacing one code with
    # nine left the console with a gloss for none of them, so `reasonCodeList` rendered a bare
    # SCREAMING_SNAKE_CASE token to a Vietnamese counter on every quote in the shop -- strictly
    # worse than the sentence it removed. Six come from the engine's own `PromotionReason`
    # (`PROMOTION_APPLIED`, `PROMOTION_OUTSIDE_INTERVAL`, `PROMOTION_NOT_TARGETED`,
    # `PROMOTION_TARGET_REQUIRES_HUMAN`, `PROMOTION_STACKING_REQUIRES_HUMAN`,
    # `PROMOTION_ELIGIBILITY_UNRESOLVED`); four are the codes `quote_composition.py` emits on the
    # engine's behalf (`PROMOTION_NOT_PUBLISHED`, `PROMOTION_PENDING_BAND_CLOSE`,
    # `PROMOTION_CHANGED_SINCE_QUOTE`, `PROMOTION_PUBLISHED_SINCE_APPROVAL`).
    #
    # The total did not move when PROMO-FIX-002 rewrote them, and that is a coincidence worth
    # stating rather than hiding: it deleted `APPROVAL_OUTSTANDING_APPLY_PROMOTION` and added
    # `PROMOTION_PUBLISHED_SINCE_APPROVAL`, one out and one in. The deletion is the same rule as
    # the retirement above, applied to the item's own work: nothing populates `required_approvals`
    # any more, so no server can send that code, and a gloss for it would be the exact dead entry
    # PROMO-WIRING-001 cited as its reason for deleting `PROMOTION_NOT_EVALUATED`. Several slot ids
    # below it also changed, because two more sentences were rewritten where the behaviour they
    # described moved -- which is the identity check doing its job.
    #
    # 289 after PROMO-FIX-003: +3, all on `screens/quotes.js`, none in `core/i18n.js`. The
    # promotion vocabulary itself did not grow -- `REASON_NOTE.PROMOTION_STACKING_REQUIRES_HUMAN`
    # was rewritten in place, so its slot id changed and its count did not, which is the identity
    # check doing exactly what it is for. The three additions are the promotion statement the
    # quotes screen did not draw: PROMO-WIRING-001 put the programme's interval on the wire and
    # nothing rendered it, so the screen showed 0 d with the reason only in a code list. One
    # sentence says what `null` means (no programme was assessed at all, which is why the row is
    # "--" and not "0 d"), one says why a zero inside a published programme is a zero with a
    # reason, and one states that the end bound is exclusive and is printed as the server sent it.
    # That third one is load-bearing rather than decoration: the difference between "runs until
    # 01/09" and "ran through 31/08" is one day at the boundary, and the screen must not be the
    # place that quietly picks one.
    #
    # 291 after PROMO-FIX-004: +2, both in `core/i18n.js`, and one more sentence rewritten in place.
    # The rewrite is `REASON_NOTE.PROMOTION_STACKING_REQUIRES_HUMAN` again -- its slot id moved and
    # its count did not -- because the behaviour it described was reversed: PROMO-FIX-003 had the
    # server withdraw a non-stacking promotion as a credit landed, and the sentence said so. That
    # raised the bill above the total the customer had just been read, so the withdrawal is gone and
    # the code now only ever means "the programme was not counted into this price".
    #
    # The two additions are the codes that behaviour left behind. One is
    # `REMEDY_CREDIT_PROMOTION_NOT_STACKABLE`, the refusal that replaced the withdrawal, and its
    # sentence is the whole of what the counter must be told: the credit was NOT used, it is still
    # good, and a person picks between it and the programme for this order. Rendering that as
    # anything vaguer would leave a staff member believing a bearer instrument had been spent when
    # it had not. The other is `APPROVAL_OUTSTANDING_APPLY_PROMOTION`, which PROMO-FIX-002 deleted
    # on the ground that no server can send it while the same change shipped a test watching
    # `accept_quote_revision` send exactly that. The narrower true statement -- that no composer
    # populates `required_approvals` today -- is now in the gloss itself, which tells the counter it
    # is looking at a data fault and must not press again.
    #
    # 301 after RANGE-APPROVAL-VISIBILITY-001: +10, in three screens, and one lede re-keyed. The
    # arithmetic is 291 + 10 = 301, and every one of the ten is a sentence the console could not
    # say before because the fact behind it did not exist.
    #
    # The item is a defect fix: the owner approving a `SET_RANGE_PRICE` envelope could not see the
    # amount. The envelope carries a digest, the linked quote screen renders the published BAND,
    # and the proposed number was persisted nowhere -- so a staff member could agree 150.000 d with
    # the customer, propose 240.000 d, and the owner's approval, the only second-party control over
    # that number, passed it through unread.
    #
    #   * 5 in `screens/approvals.js`. Two are the disclosure itself -- the notice title that names
    #     the revision the amounts belong to, and the line saying that pressing Duyet approves this
    #     exact figure and that it is not yet `tien da thu`. Three are refusals, and they are the
    #     more important half: a proposal that comes back with no lines, a proposal whose
    #     `rendered_hash` disagrees with the queue row's, and a read that failed. All three leave
    #     the approve control shut, so each needs a sentence saying why -- a disabled button with
    #     no reason is how the last version of this screen taught approvers to press on regardless.
    #   * 3 in `screens/gaps.js`, one new entry at three bindable keys. It is narrower than the one
    #     RANGE-PRICE-001 retired and it is *not* the same claim: the money half is closed, and what
    #     remains is that an `ORDER` envelope's link opens the order as it is now, with no proof
    #     that the version on screen is the version the envelope binds. The group lede moved from
    #     "Ba muc" to "Bon muc" and re-keyed, which is the count the entry changed.
    #   * 2 in `screens/quotes.js`, both on the refusal for a malformed `&revision=` in the link the
    #     approvals queue now builds. `#/quotes?quote=<id>` used to open whichever revision is
    #     newest, so an approver could read revision 3 while signing revision 1's digest; the link
    #     carries the bound revision now, and a value this screen cannot read opens nothing at all
    #     rather than falling back to the newest one.
    #
    # 340 after OPS-BOARD-001: +39 on top of the 301 above, and the arithmetic is 301 + 39 = 340.
    # The item put the SLA board -- which already existed as a query and was reachable only as two
    # counts inside an assistant answer -- on a screen a shift can work from, rendered the day's
    # order counts on `#/today`, and built the sanitized export.
    #
    #   * 20 in `core/i18n.js`, all in `REASON_NOTE`. Thirteen are the `SlaReason` vocabulary, which
    #     was never glossed because no screen had ever shown it: every row of the new board carries
    #     these codes as its evidence, and `SLA_BREACHED` on its own tells a staff member nothing
    #     about whether the shop owes the customer anything. The notes draw that line explicitly --
    #     `ELAPSED_EIGHT_HOUR_INTERNAL_RISK` is the shop's own mark, and
    #     `GUIDANCE_DOES_NOT_CREATE_BREACH` says a guidance range is not a promise and must not be
    #     rendered as a broken one. The other seven are the export refusals: five are conditions a
    #     person can resolve, `EXPORT_REQUEST_STORE_MISMATCH` says the console is pointed at the
    #     wrong shop and that the approval is still good, and `EXPORT_CELL_NOT_SAFE` is the
    #     spreadsheet-formula guard, which is a data fault and says so rather than telling a staff
    #     member to retype something.
    #   * 7 in `screens/slaBoard.js`. The rule notice's title, the empty-board line, the no-mark
    #     line, the lede stating that the order is the server's, the read-only guardrail, and the
    #     two halves of the strange-shape notice.
    #   * 6 in `screens/exports.js`. What the file carries and what it withholds, the wait-for-owner
    #     notice, the guardrail naming separation of duty, the lede, and the line saying a
    #     downloaded file has left every retention schedule the system runs.
    #   * 3 in `core/rbac.js`, one per new capability: `SLA_BOARD_READ`, `DAY_SUMMARY_READ` and
    #     `EXPORT_DATA`. Two are bound to repository role sets and one to a route gate; see the
    #     split pinned above.
    #   * 2 in `screens/today.js`: the day-summary card's total line, and the refusal note for a
    #     role the server would turn away.
    #   * 1 net in `screens/gaps.js` -- five sentences added, four removed -- and the removals are
    #     the point. "Không có endpoint xuất dữ liệu nào" and "chưa có route nào phơi nó ra" both
    #     stopped being true the moment this item shipped, which is exactly the day a gap register
    #     has to stop saying them. Neither entry was deleted: the narrow half is built and the wide
    #     half is not, so both now name what is still missing -- a report over a chosen date range,
    #     and a per-order SLA policy nobody has decided -- and say what can be done today instead.
    #     The CSV formula note went because it became code: `_cell` refuses a cell a spreadsheet
    #     would execute, so the warning is enforced rather than remembered.
    #
    # 348 after EXPORT-FIX-001: +8 on top of the 340 above, and the arithmetic is 340 + 8 = 348.
    # The item fixed the export OPS-BOARD-001 shipped, and every new sentence belongs to one of the
    # two defects that made the capability unusable rather than merely imperfect.
    #
    #   * 6 in `screens/approvals.js`. `VIEWABLE_RESOURCES` had no `EXPORT_REQUEST` entry, so the
    #     `EXPORT_SANITIZED_DATA` envelope `#/exports` raises reached this queue permanently
    #     undecidable -- not a locked button but a dead end, in which a staff member could request
    #     an export that no owner in the shop was able to release. Adding the type alone would have
    #     traded that for blind approval of a digest and a UUID, so the card follows the standard
    #     RANGE-APPROVAL-VISIBILITY-001 set in this same tree: two are the disclosure itself, the
    #     notice title naming what is about to leave the building and the line saying that pressing
    #     Duyet releases this exact column list for this exact day and that a downloaded file
    #     cannot be recalled. The other four are refusals that leave the approve control shut -- a
    #     read that returned no columns, one whose re-derived digest disagrees with the queue row,
    #     and a read that failed -- each with the sentence that says why. A disabled button with no
    #     reason is how the last version of this screen taught approvers to press on regardless.
    #   * 2 in `core/i18n.js`, both `REASON_NOTE` glosses for export refusals that had none.
    #     `EXPORT_REQUEST_CORRUPT` was the gap the review named: the five other refusal
    #     vocabularies on this console are bound to their producing enum by a test and this one was
    #     not, so a staff member meeting it got a bare English token. The second is
    #     `EXPORT_APPROVAL_SELF_DECIDED`, which this item creates: separation of duty now binds to
    #     the person who defined the export rather than to whoever raised the envelope, and the
    #     refusal it produces needs a sentence saying that another owner has to decide.
    #   * 0 net in `screens/exports.js` -- three sentences re-keyed, none added or removed, and the
    #     re-keying is the point. The lede said the file carries "tiền đã thu", which is also what
    #     `#/today` calls a different number: that box sums when money was taken and this file is
    #     cut on when an order was opened, so an order opened yesterday and paid this morning is in
    #     one and not the other. The lede now says which. The guardrail and the wait-for-owner
    #     notice both said the envelope must be decided by somebody other than "người vừa tạo",
    #     which named the wrong act now that the rule measures against the export request's own
    #     requester.
    #
    # 347 after ORDER-LOOKUP-001: -2 retired, +1 added, 3 re-keyed. Net -1.
    #
    #   * The two retirements are order detail's eyebrow and guardrail, "Ghép từ bảng đơn · máy chủ
    #     chưa có cách đọc một đơn riêng lẻ" and "Máy chủ chưa có cách đọc một đơn riêng lẻ…". Both
    #     stopped being true when `GET /internal/v1/orders/{order_id}` shipped, so they were deleted
    #     rather than reworded -- the same exit QUOTE-ACCEPT-001 and REMEDY-001 took.
    #   * The addition is the detail screen's 404 notice, which says only what the server's single
    #     answer for "missing" and "another store's" allows: the id is wrong or the order is in a
    #     store you do not work in.
    #   * Re-keyed: the board's read-model note (it said the server stores no amount for an order,
    #     and now the board shows one; what remains true is that no customer name is stored, by
    #     DEC-013), order detail's four-axes hint (it pointed transitions at the board because only
    #     the board held a row version), and the create panel's guardrail, whose text is unchanged
    #     but whose quote marks are now literal characters rather than `“` escapes -- the
    #     registry used to record the escape sequence itself as the sentence.
    #
    # 373 on its own branch after the pre-staging console defect round (B2/M1-M6): +25 added,
    # 2 re-keyed, 0 retired.
    #   * +24 in `core/errors.js`: the new `REFUSAL` table is registered through `CLAIM_TABLES`.
    #     `classify` used to put the server's English 409 prose ("this order request already has a
    #     quote; add a revision instead") in the notice title; each entry is the Vietnamese sentence
    #     now shown instead, and several say what the server did ("Không có gì được ghi"), so they
    #     are claims and belong on the register. The prose itself is kept, collapsed, as detail.
    #   * +1 in `screens/exports.js`: the in-progress export now survives a trip to `#/approvals`
    #     in memory only, and a hint says exactly that -- it is remembered across screens, and not
    #     across a reload or a sign-out (the stash is cleared when the session loses its principal).
    #   * 2 re-keyed. `MESSAGES.NOT_SUPPORTED` no longer tells every refused payment "dữ liệu bạn
    #     nhập không sai" -- false for a cashier who typed 13.200 against 132.000 -- and
    #     `REASON_NOTE.AMOUNT_IS_NOT_THE_EXACT_TOTAL` now says to check and retype, and names
    #     DEC-010 as the owner's (resolved) decision rather than implying the case is still open.
    #
    # 372 once both land: the two rounds touched disjoint sentences, so 348 - 2 + 1 + 25.
    #
    # 352 on its own branch after the remedy money fixes (split-claim accumulation and the credit
    # lifecycle): +4, all
    # `REASON_NOTE` glosses in `core/i18n.js`, and 348 + 4 = 352. Three are refusals the counter can
    # now meet and `test_every_remedy_refusal_the_counter_can_meet_is_glossed_for_them` requires:
    # `REMEDY_INCIDENT_NOT_OPEN` (a proposal against an incident that already has its outcome),
    # `REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED` (one 10% credit per late delivery) and
    # `REMEDY_CREDIT_ALREADY_ON_QUOTE` (the same credit presented twice on one bill). The fourth is
    # `REMEDY_CREDIT_RELEASED`, the reason code a re-priced revision carries when it could not keep
    # a credit its parent reserved -- a staff member has to tell the customer before they agree.
    # One sentence was re-keyed without changing the count: `REMEDY_CEILING_EXCEEDED` now says the
    # ceiling counts every earlier proposal on the same garment, because it does.
    #
    # 376 with all three rounds landed: disjoint sentences again, so 372 + 4.
    #
    # 381 after the real-API browser run on the merged tree: +5 `REFUSAL` entries for the
    # cancellation refusals, none of which had a Vietnamese title. Two are the contradictions
    # DEC-024 lets the record check ("the order records custody of the goods", "production has
    # begun"): the first Vietnamese pass fell back to the generic title for them and hid the fact
    # on record under "Chi tiết kỹ thuật", which verify_workflow_conformance.py caught. Three
    # arrived with CANCEL-REFUND-001 (a paid order whose resolution does not say the money went
    # back; an unsupported balance shape; a resolution not yet chosen).
    #
    # 383 after API-INTEGRITY-002: +1 the `UNKNOWN_SENDS_READ` capability's `why`, +1 the
    # manual-send hint saying there is no recipient field and why. Two sentences were re-keyed
    # without changing the count, both because they had become false: the exceptions notice that
    # said the queue showed every store's receipts, and SHADOW_READ's `why`, which no longer covers
    # the exceptions queue now that it asks for MFA. 381 + 2.
    #
    # On its own branch:
    # 382 after RANGE-COUNTER-ATTEST-001 (`DEC-029`): +1, and four sentences re-keyed in
    # `screens/quotes.js`. The staff member on duty now chooses inside a published band without the
    # owner, so every sentence telling the counter "chủ tiệm duyệt" was reworded -- the band notice,
    # the refusal on a band revision, the band-mode offer and the screen lede. The one addition is
    # the hint under the band notice saying the chooser's name is recorded with the number, cannot
    # be altered, and is reviewed by the owner. It replaces the "mười phút ... gọi chủ tiệm" hint,
    # which was a template literal the scanner does not register, so removing it moved nothing.
    #
    # 387 after PREPAID-DROPOFF-001 (`DEC-032`): 382 + 5, and one sentence re-keyed.
    #   * 4 `REASON_NOTE` entries in `core/i18n.js`, one per new refusal a counter can meet: laundry
    #     not finished (`GOODS_NOT_READY_FOR_HANDOVER`, from both the pickup command and the
    #     staging review's rule on ticking "collected"), and the pickup command's three states --
    #     not paid yet, not a walk-in prepayment, already collected.
    #   * 1 hint on the board card in `screens/orders.js`: paid at drop-off, not yet collected,
    #     because "Đã thu" alone reads as "nothing left to do" on the list searched at pickup.
    #   * The settlement guardrail in `screens/orderDetail.js` re-keyed: it names three moments the
    #     exact total may be paid instead of two. Its `POLICY_BOUND` binding to DEC-010 moved with
    #     it. The pickup panel's own lines are conditional expressions the scanner does not
    #     register, which is why the count moved by five and not more.
    #
    # 389 with both landed: disjoint sentences, so 381 + 2 (API-INTEGRITY-002)
    # + 6 (DEC-029, DEC-032).
    #
    # 392 after the owner's range-price review panel on #/approvals: +3 -- the withheld notice, the
    # panel's hint naming DEC-029, and its empty-list sentence. DEC-029 moved the choice of a price
    # inside a band to the counter on the strength of this review, which had no screen.
    #
    # On the REMEDY-ITEM-FEE-001 branch:
    # 385 after REMEDY-ITEM-FEE-001 (DEC-031, and DEC-030's copy): -3 retired, +7 added, 17
    # re-keyed. 381 - 3 + 7 = 385.
    #   * -3 in `screens/remedies.js`: the loss wall's `missing`, `blockedBy` and `today`. They
    #     said the owner had not decided loss and that the screen has no form for it; DEC-031
    #     decided it on 2026-09-25 and the form exists, so the `unsupported` block went and its
    #     three slots with it -- the exit REMEDY-001 and ORDER-LOOKUP-001 took for false claims.
    #   * +4 `OWNER_REASON_NOTE`, newly in `CLAIM_TABLES`: the sentence for each `OwnerReason` the
    #     server can send (loss, refunded order, item fee not recorded, above the staff limit).
    #   * +3 in `screens/remedies.js`: the loss notice's body ("chỉ chủ tiệm duyệt mới được trả,
    #     kể cả số nhỏ"), and the refunded-order notice's title and body.
    #   * 17 re-keyed, none added or removed: six `REASON_NOTE` glosses (the two DEC-030 ones now
    #     state the rule -- the promotion applies, the credit waits unspent -- and
    #     `PROMOTION_STACKING_REQUIRES_HUMAN` says this bill is the one case the system does not
    #     follow it; `LOSS_POLICY_UNRESOLVED` now describes only pre-DEC-031 records;
    #     `REMEDY_CEILING_EXCEEDED`, `REMEDY_AMOUNT_NOT_APPLICABLE` and `REMEDY_APPROVAL_REQUIRED`
    #     name loss and the per-piece fee), the `#/gaps` entry's `missing` and `today` (no loss
    #     queue waits on a policy any more), and nine on the remedies screen whose claims about
    #     the 5x basis, loss and the owner notice changed with the ruling.
    #
    # Still 385 after the two follow-up rulings on the same branch: 0 added, 0 retired, 5
    # re-keyed. DEC-030 made pricing release a reserved credit when a non-stacking programme
    # applies, so `PROMOTION_STACKING_REQUIRES_HUMAN` lost its "ask the owner" clause and
    # `REMEDY_CREDIT_RELEASED` names the programme as a reason. The per-item ceiling ruling reworded
    # `REMEDY_CEILING_EXCEEDED` and `PLAN_NOTE.ABOVE_CEILING` (one claim per item, a total per
    # line) and `OWNER_REASON_NOTE.ABOVE_STAFF_LIMIT` (the staff limit is the line's).
    #
    # 396 with all founder-ruling items landed: disjoint sentences, so 392 + 4
    # (REMEDY-ITEM-FEE-001's
    # -3 retired / +7 added). Predicted before regenerating; the registry came out at 396.
    #
    # Still 396 after PICKUP-ONLY-SETTLE-001 (the `DEC-032` addendum): 0 added, 0 retired, 1
    # re-keyed -- the settlement guardrail on `screens/orderDetail.js`, which now names paying at
    # the counter before the laundry is finished and says no courier takes money. Its `DEC-010`
    # binding moved with it. The two pickup hints that changed are conditional expressions, which
    # the enumerator does not register as slots.
    # 401 after MESSAGE-DRAFT-BINDING-001: -3 retired, +8 added, 5 re-keyed. 396 - 3 + 8 = 401.
    #   * -3 in `screens/gaps.js`: the "Duyệt một tin nhắn soạn sẵn" entry's `missing`,
    #     `blockedBy` and `today`. They said the message body is stored nowhere and `rendered_hash`
    #     is checked against nothing; the body is `agent_drafts`, API-INTEGRITY-002 made the server
    #     derive and verify the binding from it, and this item added the read the approvals card
    #     prints. The entry was deleted rather than reworded -- the exit ORDER-LOOKUP-001 took.
    #   * +6 in `screens/approvals.js`, the `SEND_MESSAGE` card. Two are the disclosure: the line
    #     saying pressing Duyet allows exactly these words to exactly this recipient and sends
    #     nothing, and the hint saying Tu choi still works on a card whose approve control is shut.
    #     Four are refusals that keep approve shut, each saying why: a queue row with no store, a
    #     draft edited after the envelope was raised (its text withheld, not shown with a caveat),
    #     a draft rejected since, and a read that failed.
    #   * +2 in `screens/manualSend.js`, step 0: the recipient is the draft's and nobody picks it,
    #     and asking for approval is not approval and approval is not sending.
    #   * 5 re-keyed: the `#/gaps` group lede (four entries became three), the "Tạo yêu cầu duyệt"
    #     entry's `blockedBy` and `today` (a send and an export are both raised from the console
    #     now, from values a server read returned), and the manual-send panel's guardrail and its
    #     four-values hint (step 0 fills them).
    # 406 after API-INTEGRITY-003, merged on top of MESSAGE-DRAFT-BINDING-001: 401 + 5, nothing
    # re-keyed or retired, all DESCRIPTIVE.
    #   * `MESSAGES.BUSY` in `core/errors.js`: the title a counter reads for a 503 `DATABASE_BUSY`
    #     -- "hệ thống đang bận ... thử lại" -- which used to arrive as a 500 and FAULT's "Đừng thử
    #     lại". A claim that the retry is safe, so it is registered and re-read if it is reworded.
    #   * `REFUSAL.DATABASE_UNAVAILABLE`: that kind's sentence when no connection could be opened.
    #   * `REFUSAL.RESOURCE_CHANGED_SINCE_REQUEST`: the approval refusal decision-time re-resolution
    #     added -- the resource moved on after the phiếu was raised, so the phiếu is finished.
    #   * 2 `REASON_NOTE` glosses in `core/i18n.js`, one per 503 reason code, saying what happened:
    #     the timed-out transaction rolled back whole; the connection was never opened.
    #   The registry diff adds exactly those five slot ids and removes none.
    # 400 after REMEDY-GARMENT-001 (the DEC-031 addendum): +4 added, 0 retired, 4 re-keyed.
    #   * +3 `REASON_NOTE` glosses in `core/i18n.js`, one per new `RemedyRefusal` the counter can
    #     meet and `test_every_remedy_refusal_the_counter_can_meet_is_glossed_for_them` requires:
    #     `REMEDY_GARMENT_REQUIRED`, `REMEDY_GARMENT_NOT_APPLICABLE`, `REMEDY_GARMENT_OUT_OF_RANGE`.
    #   * +1 `PLAN_NOTE.GARMENT_NOT_CHOSEN` in `screens/remedies.js`: the form stops at "món thứ
    #     mấy" on a line of several garments, and says why before a money box exists.
    #   * 4 re-keyed because the ruling made them false: `REMEDY_CEILING_EXCEEDED` and
    #     `PLAN_NOTE.ABOVE_CEILING` (the ceiling is cumulative per garment, line-level claims
    #     counted against each), `OWNER_REASON_NOTE.ABOVE_STAFF_LIMIT` (the staff limit is the
    #     garment's, not the line's), and `REMEDY_APPROVAL_EXPIRED` (a remedy envelope is open
    #     to the end of the next business day, not "a short time"). The garment picker's own hint
    #     and the per-garment owner sentence are template literals the scanner does not register.
    # 396 + 4 = 400, predicted before regenerating.
    # 410 with REMEDY-GARMENT-001 merged on top of the two above: disjoint slots, 406 + 4.
    # Still 396 after READ-PATHS-001, and the zero is a sum, not an absence: -10 retired, +10 added,
    # 7 re-keyed. 396 - 10 + 10 = 396.
    #   * -10 in `screens/gaps.js`: the three entries whose gap closed -- the unused credits of an
    #     order (`missing`, `blockedBy`, `today`), an incident's remedy proposals (the same three)
    #     and the acquisition source readback (those three and its `note`). Deleted, not reworded:
    #     the routes exist, which is the exit REMEDY-001 and ORDER-LOOKUP-001 took.
    #   * +4 in `screens/orderDetail.js`: the credits panel's hint (a bearer code, found again
    #     through the order's ticket; no expiry is recorded), its truncation line, its wrong-role
    #     notice title, and the hint beside the acquisition source saying it cannot be corrected.
    #   * +2 in `screens/remedies.js`: the recorded-proposals panel's hint and its empty prompt.
    #   * +4 in `screens/staff.js`: the eyebrow (the old one was below the scanner's threshold), the
    #     directory hint (roles are the person's everywhere; revocations stay thirty days), and the
    #     no-store notice and the wrong-role notice title.
    #   * 7 re-keyed because they had become false: the remedies execution notice's title and body
    #     ("không tra lại được" -- a lost code is found again on the order detail now), and five on
    #     the staff screen -- the lede, the role guardrail, the create card's hint and the two 204
    #     notices -- which all said the server had no way to read staff back.
    # With READ-PATHS-001 merged on top of the four above: its -10/+10 are disjoint slots, so
    # the total is unchanged at 410 -- re-derived by regenerating, not by arithmetic.
    # 419 after REMEDY-OWNER-DECIDE-001: +9 added, 0 retired, 1 re-keyed, all DESCRIPTIVE.
    #   * +8 in `screens/approvals.js`, the `APPROVE_REMEDY` card: its title ("Khoản bồi hoàn bạn
    #     đang được đề nghị duyệt"), the hint that approving pays nothing until the counter
    #     executes, the missing-summary line, the no-store notice's title and body, the stale
    #     notice body (the figures withheld, only a refusal), the not-found body, and the
    #     unreadable-read body.
    #   * +1 in `screens/remedies.js`: the recorded list's "đề nghị lại" hint on a row whose owner
    #     envelope was refused or ran out.
    #   * 1 re-keyed in `screens/remedies.js`: the recorded-proposals panel's hint, which now says
    #     an approved, unexecuted row carries its own execute press.
    #   410 + 9 = 419, predicted from the registry diff before pinning.
    # 430 after CONSENT-TRANSACTIONAL-001 (`DEC-033`): +11 added, 0 retired, 0 re-keyed.
    #   * +7 `REFUSAL` entries in `core/errors.js`, all DESCRIPTIVE: the Vietnamese sentence for
    #     each reason the server now refuses a service send or a release with -- SUPPRESSED
    #     ("Khách đã yêu cầu dừng nhận tin trên kênh này..."), PENDING_REVIEW, SUPPRESSION_UNKNOWN,
    #     MESSAGING_POLICY_UNPUBLISHED ("Chủ tiệm chưa công bố chính sách tin dịch vụ..."),
    #     NO_SERVICE_BASIS, RELEASE_EVIDENCE_INVALID and NOTHING_TO_RELEASE.
    #   * +1 `why` in `core/rbac.js`, SERVER_GATE: `SERVICE_MESSAGING_RELEASE`, bound to
    #     `require_approval_staff` in the generator's `CAPABILITY_GATES`.
    #   * +3 in `screens/manualSend.js`, DESCRIPTIVE: the service-messaging card's allowed-notice
    #     body (the server checks again at the lock and at the attestation), and the no-evidence
    #     notice's title and body (a release rests only on the customer's own later message).
    #   419 + 11 = 430, predicted from the registry diff before pinning.
    # 437 after CONSOLE-REDESIGN-006 (430 + 7) — Nhân sự, Hệ thống, Xuất dữ liệu, Việc chưa hỗ
    # trợ on the V2
    # kit): +7 net, all DESCRIPTIVE, all in those four modules. Long V1 guardrails/ledes moved
    # verbatim behind ⓘ as `hint` (re-keyed, not reworded: exports guardrail + lede, system lede,
    # staff store guardrail, staff disable-confirm body); new tier-1 one-liners beside the control
    # (staff: create, role, disable; exports: separation of duties; system: observe-only, sign-out
    # scope; gaps: "not listed is not supported"). Retired: the staff eyebrow, the create card's
    # "chép lại mã này" hint and the three 204 notices (nobody copies an id any more; their facts
    # are in the role/store/disable ⓘ), and the wrong-role notice title (now an inline alert).
    # One staff sentence corrected, not only moved: a taken OIDC subject is a 409 refusal now,
    # not the "lỗi máy chủ" the V1 field hint claimed.
    # 418 after CONSOLE-REDESIGN-003 (Hôm nay, Duyệt, Bảng trễ hạn on the V2 kit): -2 +1.
    #   * -1 `approvals.js#screen__lede`: its fact (a type the console cannot show keeps Duyệt shut
    #     and the card names the type) is stated on every such card and in the ⓘ "Tại sao nút
    #     Duyệt đang tắt?"; -1 `today.js#screen__lede`, a lede with no fact the screen lost.
    #   * +1 `slaBoard.js#hint`: the visible one-line "position is not urgency" note; the V1 lede
    #     it summarises moved verbatim into the ⓘ and keeps its slot.
    # 436 after merging the concurrent slices above (lead, at integration).
    # CONSOLE-REDESIGN-005, counted on its base of 430 (436 there) — shadow, assistant,
    #   exceptions, manual send: +6 added,
    #   0 retired, 3 re-keyed, 4 reworded. Re-keyed (same words, `guardrail:` -> `notice`/`hint`
    #   inside an ⓘ sheet): exceptions c542174e4775, shadow add42213ce89 and 4202f9908977.
    #   Reworded because the stepper renumbered the steps (1-4, was 0-2) and the old numbers would
    #   be false: manualSend 48b2712c7485, 85e4f0f7a3be, e715fa3e867b, 9b57c14c8b63. Added tier-1
    #   lines at the action: exceptions "Không bao giờ gửi lại tự động…", manualSend "Chỉ khoá khi
    #   chính bạn sẽ gửi…" and "Một người khác sẽ duyệt…", shadow "Duyệt chỉ ghi quyết định…" and
    #   the review-log truncation line; and the release scope sentence ("Tin quảng cáo vẫn bị
    #   chặn…"), which was a variable before and is now a literal the registry can see.
    # 442 after merging the concurrent slices above (lead, at integration).
    # 437 after CONSOLE-REDESIGN-006 (430 + 7) (Nhân sự, Hệ thống, Xuất dữ liệu, Việc chưa hỗ trợ on
    # the V2 kit): +7 net, all DESCRIPTIVE, all in those four modules. Long V1 guardrails/ledes
    # moved verbatim behind ⓘ as `hint` (re-keyed, not reworded: exports guardrail + lede, system
    # lede, staff store guardrail, staff disable-confirm body); new tier-1 one-liners beside the
    # control (staff: create, role, disable; exports: separation of duties; system: observe-only,
    # sign-out scope; gaps: "not listed is not supported"). Retired: the staff eyebrow, the create
    # card's "chép lại mã này" hint and the three 204 notices (nobody copies an id any more; their
    # facts are in the role/store/disable ⓘ), and the wrong-role notice title (now an inline alert).
    # One staff sentence corrected, not only moved: a taken OIDC subject is a 409 refusal now, not
    # the "lỗi máy chủ" the V1 field hint claimed.
    # 443 after CONSOLE-REDESIGN-001 (437 + 6) (Nhận đồ; Tiếp nhận and Báo giá as lists), all
    # DESCRIPTIVE. Re-keyed by module, text unchanged: the band closer, promotion, replay and
    # line-read sentences moved from `screens/quotes.js` to the shared `ui/quoting.js`; the
    # acceptance hint, empty-pricebook notice, band offer, "a band is not one number" notice and the
    # unknown-request notice to `screens/newOrder.js`; two quotes guardrails to ⓘ `hint`s. Retired
    # (their subject no longer exists on screen): the intake created-card replay notice and its
    # technical-code explain, the intake picker's empty and bad-prefill notices, the "choose an
    # intake above first" hand-off hint and the "Đã chốt" record (the flow creates the order
    # itself), both screens' ledes (merged into ⓘ), and `#/orders`' manual create-form guardrail
    # (the form is gone; it told staff to press a button that no longer exists). Added: tier-1
    # one-liners and ⓘ bodies of the flow (no PII kept, no contact search, delivery fee rules,
    # credit use, source question).
    # 448 after merging the concurrent slices above (lead, at integration).
    # 432 after CONSOLE-REDESIGN-004 (437 - 5) (Khiếu nại + bồi hoàn on the V2 kit; the remedy flow
    # moved onto the incident page): -5 net, all DESCRIPTIVE, all in incidents.js / remedies.js.
    # Re-keyed verbatim behind ⓘ as `hint` (not reworded): the record-only notice body, the
    # SERVICE_QUALITY guardrail, the remedies lede and "never type a ceiling" guardrail, the loss,
    # refunded-order, owner-envelope, bearer-credit and both replay notices. New: the tier-1
    # "chưa quyết ai lỗi" line, the fault-attestation one-liner and its ⓘ, the /remedies ⓘ.
    # Reworded because the behaviour changed: the category note (no picker, said without tokens),
    # "remedy is a separate command on the Bồi hoàn screen" (it is on the complaint's own page),
    # "re-read by pressing" (there is no read button) and "propose again below" (the form is above).
    # Retired: the "read an incident above" placeholder and the options card's null-credit hint
    # (the thing no longer exists; the fact stays in PLAN_NOTE.CREDIT_UNAVAILABLE), the options
    # guardrail (merged into the figures' ⓘ hint, same fact), the incident replay notice (its fact
    # is the toast), and the notice titles now rendered as alert titles / ⓘ topics.
    # 430 after CONSOLE-REDESIGN-004's second step (432 - 2): the transitional credit-redemption
    # panel left `#/remedies` (spending a credit moves onto the quote receipt in `#/new`,
    # CONSOLE-REDESIGN-001), taking its guardrail and its "phiếu đã dùng xong" hint with it.
    # 441 after merging the concurrent slices above (lead, at integration).
    # 444 after CONSOLE-FIXES-S8 (441 + 3), all DESCRIPTIVE. Added: the resumed "Bạn đã xin duyệt"
    # hint, the "filled from the envelope you locked, per the server" notice (manualSend.js), and
    # errorNotice's owner-rule line, which no longer names the decision id (moved to its technical
    # drawer). Reworded because the behaviour changed: the manual-entry hint (the four values now
    # fill from the server, MANUAL-SEND-RESUME) and NOT_SUPPORTED's "Mã lý do bên dưới" (the notes
    # are visible, the codes are in the drawer). None removed.
    assert sum(counts.values()) == _registry()["total"] == 444

    # The four capabilities moved out of SERVER_GATE are the vacuous bindings DISCLOSURE-BIND-002
    # corrected. Pinning the split keeps a future change from quietly parking one back on a gate
    # that admits everyone.
    #
    # 19 after OPS-BOARD-001: 16 + 3, one per new capability.
    # 20 after API-INTEGRITY-002: + `UNKNOWN_SENDS_READ`.
    # 21 after CONSENT-TRANSACTIONAL-001: + `SERVICE_MESSAGING_RELEASE`, on its route's gate.
    assert (
        counts.get("SERVER_GATE", 0)
        + counts.get("REPOSITORY_ROLES", 0)
        + counts.get("ALL_AUTHENTICATED", 0)
        == 21
    )


@pytest.mark.parametrize("entry", _entries("MODEL_SEAM"), ids=lambda e: e["slot_id"])
def test_the_assistant_streaming_disclosure_still_matches_the_seam(entry: dict[str, Any]) -> None:
    """The disclosure this item exists for, bound to the one line that can falsify it.

    `assistant.js` tells operators the streamed text is display pacing of an already-saved answer,
    "không phải mô hình đang sinh từ". True only while `AssistantService` defaults to the
    deterministic brain. Handing it a provider-backed brain is a one-line change at a seam that
    exists so the swap can happen; before this test nothing in the repository would have noticed
    the sentence going false.
    """
    from nha_trang_laundry_api.assistant import AssistantService

    signature = inspect.signature(AssistantService.__init__)
    assert signature.parameters["brain"].default is None, (
        "the brain is injected, so the default the disclosure relies on is the fallback below"
    )

    source = inspect.getsource(AssistantService.__init__)
    expected = entry["binding"]["default_brain"]
    assert f"brain or {expected}()" in source, (
        f"{entry['module']} tells operators no language model is called. That holds because "
        f"{entry['binding']['service']} falls back to {expected}. The fallback changed, so either "
        "the disclosure is now false or this binding is stale — resolve it, do not relax the test."
    )


@pytest.mark.parametrize(
    "entry", _entries("REPOSITORY_ROLES"), ids=lambda e: e["binding"]["capability"]
)
def test_repository_role_sets_match_the_console_exactly(entry: dict[str, Any]) -> None:
    """Equality, not subset — and this is the binding that replaced a vacuous one.

    Binding these three to `current_principal` was a tautology: that gate admits every StaffRole, so
    `console_roles <= server_roles` could not fail, and adding DRIVER to ORDERS_READ passed green.
    The claims are really decided by exact role sets in the repository layer, so the assertion is
    equality and a role added on either side fails.
    """
    import importlib

    binding = entry["binding"]
    module = importlib.import_module(binding["module"])
    symbol = getattr(module, binding["symbol"])

    if callable(symbol):
        enforced = set()
        for role in StaffRole:
            principal = StaffPrincipal(
                staff_user_id=uuid4(),
                oidc_subject="probe",
                roles=frozenset({role}),
                mfa_verified=True,
                session_id=uuid4(),
            )
            try:
                symbol(principal)
            except Exception:
                continue
            enforced.add(role.value)
    else:
        enforced = {role.value for role in symbol}

    console_roles, _ = _console_capabilities()[binding["capability"]]
    assert console_roles == enforced, (
        f"{binding['capability']}: the console says {sorted(console_roles)} and "
        f"{binding['symbol']} enforces {sorted(enforced)}"
    )


def test_no_server_gate_binding_can_pass_vacuously() -> None:
    """A binding whose gate admits everyone asserts nothing, and must not exist.

    This is the structural guard for the defect that made DISCLOSURE-CONTRACT-001 a false
    completion: three bindings named a gate that admits every role, so the subset assertion was a
    tautology and the suite stayed green under a false console claim.
    """
    every_role = {role.value for role in StaffRole}
    for entry in _entries("SERVER_GATE"):
        admitted, _ = _probe_gate(entry["binding"]["gate"])
        assert admitted != every_role, (
            f"{entry['binding']['capability']} binds to {entry['binding']['gate']}, which admits "
            "every role. That assertion can never fail — bind to the set that really decides it."
        )


@pytest.mark.parametrize(
    "entry", _entries("ALL_AUTHENTICATED"), ids=lambda e: e["binding"]["capability"]
)
def test_an_any_session_claim_lists_exactly_every_role(entry: dict[str, Any]) -> None:
    """Equality in both directions, which is what a subset check could never give.

    This claim is "any valid session may do this". Asserting the console is a subset of everyone is
    trivially true; asserting equality fails if the console drops a role, and — the direction that
    matters more — fails if a new StaffRole is added and nobody updates the console.
    """
    console_roles, _ = _console_capabilities()[entry["binding"]["capability"]]
    assert console_roles == {role.value for role in StaffRole}


@pytest.mark.parametrize("entry", _entries("POLICY_BOUND"), ids=lambda e: e["binding"]["decision"])
def test_a_policy_bound_disclosure_cites_a_real_decision(entry: dict[str, Any]) -> None:
    """The cited decision must exist, and the registry must record its live status."""
    registry = yaml.safe_load((ROOT / "context/DECISION_REGISTRY.yaml").read_text(encoding="utf-8"))
    statuses = {d["id"]: d["status"] for d in registry["decisions"]}
    decision = entry["binding"]["decision"]
    assert decision in statuses, f"the disclosure cites {decision}, which is not registered"
    assert entry["binding"]["decision_status"] == statuses[decision]


def test_no_policy_bound_disclosure_describes_a_resolved_decision_as_open() -> None:
    """A sentence about a decision must agree with the register, not with when it was written.

    This was `xfail(strict=True)` from the day it was written until 2026-08-22: `orderDetail.js`
    told operators that partial payment, deposits and credit are refused with a decision code that
    was *still open*, when `DEC-010` had been resolved on 2026-08-18 as deliberately deferred. The
    console was telling the shop owner their own decision was unmade. The wording now says the
    decision was taken, so the marker is gone and this guards the class rather than recording one
    instance of it.
    """
    open_markers = ("đang mở", "chưa chốt", "chưa quyết")
    for entry in _entries("POLICY_BOUND"):
        if entry["binding"]["decision_status"] != "RESOLVED":
            continue
        assert not any(marker in entry["text"] for marker in open_markers), (
            f"{entry['module']} describes {entry['binding']['decision']} as still open, "
            f"but the register says {entry['binding']['decision_status']}"
        )


def test_no_gap_entry_names_a_settled_decision_as_a_pending_blocker() -> None:
    """Read from the source, not the registry, because the worst instance was too short to register.

    `#/gaps` is a compliance surface: staff read it to learn what the shop cannot do and what to do
    on paper instead. Twice now an entry has named a RESOLVED decision under "Bị chặn bởi", which
    an operator reads as "no policy yet" -- so a counter dispute got improvised while the owner had
    already set a 7-day window, a 5x cap and a 100,000d approval ceiling.

    `test_no_policy_bound_disclosure_describes_a_resolved_decision_as_open` guards the same class,
    but only over POLICY_BOUND entries, of which there is one. The entry that caused this read
    `blockedBy: "DEC-004"` -- seven characters, under `MINIMUM_LENGTH`, so no slot existed to bind.
    This reads every `blockedBy` in the module regardless of length.

    Naming a settled decision is allowed and often right -- the figures it settled are what staff
    should apply today. What is forbidden is naming it *as though the decision were still coming*.
    """

    registry = yaml.safe_load((ROOT / "context/DECISION_REGISTRY.yaml").read_text(encoding="utf-8"))
    statuses = {d["id"]: d["status"] for d in registry["decisions"]}

    source = (ROOT / "apps/web/src/screens/gaps.js").read_text(encoding="utf-8")
    values = re.findall(
        r'blockedBy:\s*((?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+)',
        source,
    )
    assert values, "no blockedBy entries were found; the pattern or the module changed"

    offenders: list[str] = []
    for value in values:
        text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', value))
        for decision in set(re.findall(r"DEC-\d+", text)):
            if statuses.get(decision) != "RESOLVED":
                continue
            # The entry must say the decision was taken. Both forms are in use -- the DEC-010
            # entry COUNTER-DEFECTS-001 corrected says "đã quyết", the register says "đã chốt" --
            # and narrowing to one would fail a sentence that is already right.
            if not any(marker in text for marker in ("đã chốt", "đã quyết")):
                offenders.append(f"{decision} ({statuses.get(decision)}): {text[:80]}")

    assert not offenders, (
        "these gap entries name a settled decision as a pending blocker:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("entry", _entries("ABSENT_ROUTE"), ids=lambda e: e["slot_id"])
def test_a_disclosure_claiming_no_route_exists_is_still_true(entry: dict[str, Any]) -> None:
    """Checked against the generated route contract, not the source.

    Both of these become false the day FULFILMENT-001 lands, which is decision-clear and unenqueued.
    That is exactly the moment a gap notice needs to stop being true out loud.
    """
    contract = yaml.safe_load(
        (ROOT / "specs/contracts/internal-api-v1.openapi.yaml").read_text(encoding="utf-8")
    )
    for pattern in entry["binding"]["patterns"]:
        matched = [path for path in contract["paths"] if pattern in path]
        assert not matched, (
            f"{entry['module']} tells operators there is no {pattern} route, but the contract now "
            f"holds {matched}. The disclosure is false and must change."
        )


@pytest.mark.parametrize("entry", _entries("RESPONSE_SHAPE"), ids=lambda e: e["slot_id"])
def test_a_disclosure_about_a_response_shape_is_still_true(entry: dict[str, Any]) -> None:
    """The claim is that a named field is absent from a named model."""
    import importlib

    model = getattr(importlib.import_module(entry["binding"]["module"]), entry["binding"]["model"])
    assert entry["binding"]["absent_field"] not in model.model_fields, (
        f"{entry['module']} says {entry['binding']['model']} does not carry "
        f"{entry['binding']['absent_field']}, and it now does."
    )


@pytest.mark.parametrize("entry", _entries("READ_ONLY_MODULE"), ids=lambda e: e["slot_id"])
def test_a_screen_claiming_to_be_read_only_issues_no_write(entry: dict[str, Any]) -> None:
    """A read-only claim is checkable against the module's own request calls."""
    source = (ROOT / "apps/web/src" / entry["binding"]["module"]).read_text(encoding="utf-8")
    mutating = re.findall(r'method:\s*"(POST|PUT|PATCH|DELETE)"', source)
    assert not mutating, (
        f"{entry['module']} tells operators nothing here writes to the server, but the module "
        f"issues {sorted(set(mutating))}."
    )


@pytest.mark.parametrize("entry", _entries("ABSENT_WRITER"), ids=lambda e: e["slot_id"])
def test_a_disclosure_claiming_no_writer_exists_is_still_true(entry: dict[str, Any]) -> None:
    """No production module may write the symbol the disclosure says nothing writes.

    The order path is refused because `quote_composition.py` hardcodes ESTIMATE/REVIEW_REQUIRED,
    `quotes.py` holds the only INSERT and there is no UPDATE anywhere, and `orders.py` demands
    APPROVED_EXACT. Two screens now say so. The day someone builds the promotion path this test
    fails, which is exactly when both sentences must come down — the failure is the feature.

    Reads and evals are excluded deliberately: `orders.py` compares against the value and the eval
    fixtures generate corpora, neither of which is a production writer.
    """
    symbol = entry["binding"]["symbol"]

    # Half one: the producer still emits the non-orderable state. A concurrent session pointed out
    # that the live 409 alone is a symptom, not the claim -- the same refusal would appear for a
    # merely expired quote -- so the binding asserts the cause on both sides.
    producer = ROOT / "packages/domain/src/nha_trang_laundry_domain/quote_composition.py"
    composition = producer.read_text(encoding="utf-8")
    assert "finality=QuoteFinality.ESTIMATE" in composition, (
        "the only quote producer no longer hardcodes ESTIMATE; if it can now emit an orderable "
        "finality, the disclosure is false"
    )

    # Half two: nothing in production writes the orderable state.
    writers: list[str] = []
    for path in sorted((ROOT / "packages").rglob("*.py")) + sorted((ROOT / "apps").rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "evals" in parts or path.name == "catalog.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if symbol not in line or "!=" in line or "is not" in line:
                continue
            if re.search(rf"=\s*\w*\.?{symbol}\b|{symbol}\s*,", line):
                writers.append(f"{path.relative_to(ROOT)}:{number}")
    assert not writers, (
        f"{entry['module']} tells operators nothing sets {symbol}, but production code now does: "
        f"{writers}. The refusal it describes may no longer happen — take the disclosure down."
    )
