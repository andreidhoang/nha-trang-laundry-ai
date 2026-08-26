"""Generate the console disclosure registry: every honesty-chrome claim, and what holds it true.

The registry is regenerable output, verified the same way the internal API contract is — a fresh
in-memory generation compared to the committed bytes, so it cannot drift and a new disclosure cannot
land unregistered.

What is NOT generated is the binding. Each entry's classification and the code fact behind it are
authored in `BINDINGS` below, because a binding derived from the sentence is a guess. An earlier
attempt extracted snake_case identifiers from the disclosure text and produced bindings against
`bound_contact_id`, `occurred_at` and `session_id` — all columns that exist — which would have
asserted the opposite of the truth in a file that looks authoritative.

Unbound entries are not a gap to hide. A disclosure classified `DESCRIPTIVE` is prose about how a
screen behaves that no single code fact governs; saying so is more useful than inventing a binding
for it. What the registry guarantees is that every slot is *accounted for*, and that rewording one
changes its identity and fails the check until someone looks at the sentence again.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from typing import Any

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
import yaml
from console_disclosures import REGISTRY_PATH, ROOT, DisclosureSlot, enumerate_slots

#: Console capability -> the server gate that decides it. Authored, and cross-checked against the
#: `x-authorization-dependencies` recorded per route in `internal-api-v1.openapi.yaml`: the shadow
#: reads really do gate on `current_principal`, settlements/today and the assistant on
#: `require_operations_staff`, the approval routes on `require_approval_staff`, and the staff-admin
#: routes on `require_owner`.
#:
#: The asserted property is deliberately one-directional. The console must never claim a capability
#: is open to someone the server would refuse, because that renders an enabled control that then
#: 403s. The console being *stricter* than the server is fail-safe and is allowed: `SHADOW_READ`
#: lists four roles where `current_principal` admits six, and that is a UX choice, not a defect.
#: Capabilities whose claim really is "any authenticated session". Binding these to a gate with the
#: subset assertion is vacuous for the same reason as the others, but the claim itself is sound, so
#: the fix is a different assertion rather than a different truthmaker: the console must list
#: EXACTLY every StaffRole. That fails if a role is dropped from the console, and it also fails if a
#: new StaffRole is added and the console is not updated - a direction subset checks never caught.
#:
#: STORES_READ was the fourth vacuous binding. The adversarial review named three; the generation
#: guard below found this one.
ALL_AUTHENTICATED_CAPABILITIES: frozenset[str] = frozenset({"STORES_READ"})

CAPABILITY_GATES: dict[str, str] = {
    # ORDERS_READ, SHADOW_READ and SHADOW_DECIDE are NOT here. Binding them to current_principal was
    # vacuous: that gate admits every StaffRole, so `console_roles <= server_roles` was a tautology
    # and a false console claim passed green. They bind to REPOSITORY_ROLES below instead.
    "ORDERS_WRITE": "require_operations_staff",
    "QUOTES_READ": "require_operations_staff",
    "QUOTES_WRITE": "require_operations_staff",
    "SETTLEMENTS_READ": "require_operations_staff",
    "INCIDENTS_READ": "require_operations_staff",
    "INCIDENTS_WRITE": "require_operations_staff",
    "APPROVALS_READ": "require_approval_staff",
    "QUEUE_READ": "require_approval_staff",
    "MANUAL_SEND": "require_operations_staff",
    "STAFF_ADMIN": "require_owner",
    "ASSISTANT": "require_operations_staff",
}

#: Console capability -> the exact role set that actually enforces it, at the repository layer
#: rather than the route gate. These three claims are decided here, not by a FastAPI dependency, and
#: because each is an exact frozenset the binding asserts EQUALITY, which is strictly stronger than
#: the subset property a permissive gate allows.
CAPABILITY_REPOSITORY_ROLES: dict[str, tuple[str, str]] = {
    # A callable rather than a frozenset: the set is inline in the guard. Probing it behaviourally
    # beats extracting a module-level constant to suit a test, which would change production code to
    # make verification convenient and would stop tracking the guard if the guard moved.
    "ORDERS_READ": ("nha_trang_laundry_db.orders", "_require_order_read"),
    "SHADOW_READ": ("nha_trang_laundry_db.shadow_console", "SHADOW_READ_ROLES"),
    "SHADOW_DECIDE": ("nha_trang_laundry_db.shadow_console", "SHADOW_DECIDE_ROLES"),
}

#: Authored bindings, keyed by slot id. A slot absent from this table is registered `DESCRIPTIVE`.
#: Every entry here is verified against the schema or the route contract, not against the sentence.
BINDINGS: dict[str, dict[str, Any]] = {
    "screens/gaps.js#missing:5bb9f3600337": {
        "kind": "ABSENT_TABLE",
        "tables": ["delivery_bundles", "distance_measurements"],
        "why": "The disclosure named three aggregates as absent and the binding asserted the "
        "schema lacked them. `delivery_legs` landed on 2026-08-26 under DEC-023 and this test "
        "failed the same day, which is what it is for; the sentence and the binding now name the "
        "two that are still absent. Building either makes the sentence false again.",
    },
    "screens/gaps.js#missing:af635d639b65": {
        "kind": "ABSENT_TABLE",
        "tables": ["custody_units", "custody_events", "batches"],
        "why": "Same shape: the custody context is measured empty and the disclosure says so.",
    },
    "screens/gaps.js#missing:4c53a8d9d8b5": {
        "kind": "ABSENT_TABLE",
        "tables": ["remedies", "credit_grants", "credit_ledger_entries"],
        "why": "Remedy and credit aggregates. The sentence also states that compensation is an "
        "approved command rather than a screen action, which stays true while these are absent.",
    },
    "screens/gaps.js#missing:c4f99531cea6": {
        "kind": "ABSENT_TABLE",
        "tables": ["parties", "contact_points", "addresses"],
        "why": "The party/CRM aggregates. This is the disclosure DEC-015 is about, and it must "
        "change the day a customer record exists.",
    },
    "screens/assistant.js#screen__lede:96537d9c63dd": {
        "kind": "MODEL_SEAM",
        "service": "AssistantService",
        "default_brain": "DeterministicAssistantBrain",
        "why": "The second model-seam claim in the same file, invisible to the first enumerator "
        "because it renders as a screen__lede. It says every answer comes only from governed "
        "operational data and that what the assistant does not know, it says it does not know. The "
        "same one-line brain swap falsifies it.",
    },
    "screens/gaps.js#missing:9834ff94fcd7": {
        "kind": "ABSENT_TABLE",
        "tables": ["charges", "payments", "payment_allocations"],
        "why": "Named literally in the sentence and structurally identical to the four gap notices "
        "already bound. It was parked DESCRIPTIVE, which is the misclassification this item exists "
        "to correct.",
    },
    "screens/gaps.js#missing:6bc3b51a36d1": {
        "kind": "ABSENT_ROUTE",
        "patterns": ["intake"],
        "why": "Claims the intake transitions have no HTTP route. Checked against the generated "
        "internal API contract rather than the source, so it reads the same governed artifact a "
        "reviewer would.",
    },
    "screens/gaps.js#missing:0621c03d85a6": {
        "kind": "ABSENT_ROUTE",
        "patterns": ["production"],
        "why": "Same shape for production status. Both become false the day FULFILMENT-001 lands, "
        "which is decision-clear and unenqueued - exactly when a disclosure needs to notice.",
    },
    "screens/gaps.js#missing:5f121897a2aa": {
        "kind": "RESPONSE_SHAPE",
        "module": "nha_trang_laundry_api.main",
        "model": "SessionResponse",
        "absent_field": "session_id",
        "why": "Claims /internal/v1/session does not return session_id, which is why the "
        "operations "
        "table cannot name a session to revoke. Verified: the model declares staff_user_id, roles "
        "and mfa_verified and nothing else.",
    },
    "screens/approvals.js#guardrail:2807c7bbd43b": {
        "kind": "READ_ONLY_MODULE",
        "module": "screens/approvals.js",
        "why": "Claims the screen writes nothing to the server. Checked by scanning the module for "
        "a mutating request; it issues one GET and nothing else.",
    },
    "screens/assistant.js#notice:383d2d025f90": {
        "kind": "MODEL_SEAM",
        "service": "AssistantService",
        "default_brain": "DeterministicAssistantBrain",
        "why": "The disclosure this item exists for. It tells operators the streamed text is "
        "display pacing of an already-saved answer, not a model generating words. That holds only "
        "while AssistantService defaults to the "
        "deterministic brain. Passing a provider-backed brain to AssistantService(brain=...) is a "
        "one-line change at a seam that exists so the swap can happen, and it makes the sentence "
        "false with nothing else in the repository noticing. The test asserts the default.",
    },
}

#: Slots whose truth rests on a decision rather than on a code fact. Recorded with the decision so a
#: reader can check the sentence against the register instead of against the code.
POLICY_BOUND: dict[str, str] = {
    "screens/orderDetail.js#guardrail:d58009f5dc6c": "DEC-010",
}


def _decision_status(decision_id: str) -> str:
    """The decision's current status, read from the register rather than from the sentence."""
    registry = yaml.safe_load((ROOT / "context/DECISION_REGISTRY.yaml").read_text(encoding="utf-8"))
    for entry in registry["decisions"]:
        if entry["id"] == decision_id:
            return str(entry["status"])
    raise ValueError(f"disclosure cites {decision_id}, which is not in the decision registry")


def _capability_for(slot: DisclosureSlot, source: str) -> str | None:
    """The CAPABILITIES key whose block encloses this `why`, for `core/rbac.js` slots."""
    if slot.module != "core/rbac.js":
        return None
    import re

    owners = {
        index + 1: match.group(1)
        for index, line in enumerate(source.splitlines())
        if (match := re.match(r"  ([A-Z][A-Z_]+):", line))
    }
    enclosing = [line for line in owners if line <= slot.line]
    return owners[max(enclosing)] if enclosing else None


def build_registry() -> dict[str, Any]:
    slots = enumerate_slots()
    rbac_source = (ROOT / "apps/web/src/core/rbac.js").read_text(encoding="utf-8")

    entries: list[dict[str, Any]] = []
    for slot in slots:
        entry: dict[str, Any] = {
            "slot_id": slot.slot_id,
            "module": slot.module,
            "key": slot.key,
            "text": slot.text,
        }
        capability = _capability_for(slot, rbac_source)
        if capability is not None and capability in ALL_AUTHENTICATED_CAPABILITIES:
            entry["binding"] = {
                "kind": "ALL_AUTHENTICATED",
                "capability": capability,
                "why": "The claim is that any valid session may do this. Asserted as equality with "
                "the full StaffRole enumeration, so it fails if the console drops a role and also "
                "if a new role is added and the console is not updated.",
            }
        elif capability is not None and capability in CAPABILITY_REPOSITORY_ROLES:
            module, symbol = CAPABILITY_REPOSITORY_ROLES[capability]
            entry["binding"] = {
                "kind": "REPOSITORY_ROLES",
                "capability": capability,
                "module": module,
                "symbol": symbol,
                "why": "The route gate for this capability admits every role, so binding to it "
                "could "
                "never fail. The claim is really enforced by an exact role set in the repository "
                "layer, and the test asserts the console's list equals it.",
            }
        elif capability is not None and capability in CAPABILITY_GATES:
            entry["binding"] = {
                "kind": "SERVER_GATE",
                "capability": capability,
                "gate": CAPABILITY_GATES[capability],
                "why": "The console declares which roles and MFA state this capability needs. The "
                "test probes the named server gate and requires the console to be no more "
                "permissive than it is.",
            }
        elif slot.slot_id in BINDINGS:
            entry["binding"] = BINDINGS[slot.slot_id]
        elif slot.slot_id in POLICY_BOUND:
            decision = POLICY_BOUND[slot.slot_id]
            entry["binding"] = {
                "kind": "POLICY_BOUND",
                "decision": decision,
                "decision_status": _decision_status(decision),
                "why": "True because a registered decision says so, not because of a code fact. "
                "The "
                "recorded status is regenerated from context/DECISION_REGISTRY.yaml, so a decision "
                "resolving after the sentence was written shows up here as a registry diff.",
            }
        else:
            entry["binding"] = {
                "kind": "DESCRIPTIVE",
                "why": "Prose about how this screen behaves. No single code fact governs it; it is "
                "registered so that rewording it fails the check and forces a fresh reading.",
            }
        entries.append(entry)

    unfalsifiable = sorted(
        capability for capability, gate in CAPABILITY_GATES.items() if gate == "current_principal"
    )
    if unfalsifiable:
        raise ValueError(
            f"SERVER_GATE bindings on current_principal are vacuous: {unfalsifiable}. That gate "
            "admits every StaffRole, so the subset assertion can never fail and a false console "
            "claim passes green. Bind to the exact role set that really decides the capability, as "
            "CAPABILITY_REPOSITORY_ROLES does."
        )

    registered = {slot.slot_id for slot in slots}
    invented = (set(BINDINGS) | set(POLICY_BOUND)) - registered
    if invented:
        raise ValueError(
            "authored bindings reference slot ids that no disclosure has: "
            f"{sorted(invented)}. A binding keyed to nothing is silently inert, which is worse "
            "than an unbound disclosure because the registry looks more complete than it is."
        )

    counts: dict[str, int] = {}
    for entry in entries:
        kind = entry["binding"]["kind"]
        counts[kind] = counts.get(kind, 0) + 1

    return {
        "schema_version": 1,
        "purpose": (
            "The staff console's honesty-chrome disclosures, with what holds each one true. "
            "Coverage is stated precisely rather than absolutely, because the first version of "
            "this "
            "registry claimed to hold 'every' disclosure while its enumerator could only see six "
            "object keys and the first paragraph of a notice - a claim that was true of the "
            "enumerator, not of the console. It now enumerates those keys, strings rendered with a "
            "props object under the claim classes, notice bodies, and the MESSAGES and REASON_NOTE "
            "tables in core/. A string rendered some other way is still outside it. "
            "Generated by scripts/generate_console_disclosure_registry.py and verified by "
            "scripts/verify_contracts.py against a fresh generation, so a new disclosure cannot "
            "land unregistered and a reworded one cannot pass unnoticed: a slot's identity "
            "includes the hash of its text."
        ),
        "counts": dict(sorted(counts.items())),
        "total": len(entries),
        "disclosures": entries,
    }


def render(registry: dict[str, Any]) -> str:
    return yaml.safe_dump(registry, sort_keys=False, allow_unicode=True, width=100)


def generate() -> str:
    return render(build_registry())


def main() -> None:
    rendered = generate()
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(rendered, encoding="utf-8")
    registry = yaml.safe_load(rendered)
    print(f"Wrote {REGISTRY_PATH.relative_to(ROOT)}")
    print(f"{registry['total']} disclosure slots: {registry['counts']}")


if __name__ == "__main__":
    main()
