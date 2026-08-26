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
    assert counts.get("SERVER_GATE") == 11
    assert counts.get("REPOSITORY_ROLES") == 3
    assert counts.get("ALL_AUTHENTICATED") == 1
    assert counts.get("ABSENT_TABLE") == 5
    assert counts.get("ABSENT_ROUTE") == 2
    assert counts.get("RESPONSE_SHAPE") == 1
    assert counts.get("READ_ONLY_MODULE") == 1
    assert counts.get("MODEL_SEAM") == 2
    # ABSENT_WRITER is gone entirely, not reduced. Both slots claimed that nothing raises a
    # quote revision to APPROVED_EXACT; DEC-021 resolved on 2026-08-25 and the acceptance path
    # landed, so both claims became false on the same day and were retired together.
    assert "ABSENT_WRITER" not in counts
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
    assert sum(counts.values()) == _registry()["total"] == 164

    # The four capabilities moved out of SERVER_GATE are the vacuous bindings DISCLOSURE-BIND-002
    # corrected. Pinning the split keeps a future change from quietly parking one back on a gate
    # that admits everyone.
    assert (
        counts.get("SERVER_GATE", 0)
        + counts.get("REPOSITORY_ROLES", 0)
        + counts.get("ALL_AUTHENTICATED", 0)
        == 15
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
