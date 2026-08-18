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
CAPABILITY_GATES: dict[str, str] = {
    "STORES_READ": "current_principal",
    "ORDERS_READ": "current_principal",
    "ORDERS_WRITE": "require_operations_staff",
    "QUOTES_READ": "require_operations_staff",
    "QUOTES_WRITE": "require_operations_staff",
    "SETTLEMENTS_READ": "require_operations_staff",
    "INCIDENTS_READ": "require_operations_staff",
    "INCIDENTS_WRITE": "require_operations_staff",
    "APPROVALS_READ": "require_approval_staff",
    "QUEUE_READ": "require_approval_staff",
    "SHADOW_READ": "current_principal",
    "SHADOW_DECIDE": "require_operations_staff",
    "MANUAL_SEND": "require_operations_staff",
    "STAFF_ADMIN": "require_owner",
    "ASSISTANT": "require_operations_staff",
}

#: Authored bindings, keyed by slot id. A slot absent from this table is registered `DESCRIPTIVE`.
#: Every entry here is verified against the schema or the route contract, not against the sentence.
BINDINGS: dict[str, dict[str, Any]] = {
    "screens/gaps.js#missing:0dcee188fd27": {
        "kind": "ABSENT_TABLE",
        "tables": ["delivery_bundles", "delivery_legs", "distance_measurements"],
        "why": "The disclosure names three aggregates as absent; the binding asserts the schema "
        "still lacks them. Building any of them makes the sentence false.",
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
    "screens/orderDetail.js#guardrail:91c046d0c7ed": "DEC-010",
}


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
        if capability is not None and capability in CAPABILITY_GATES:
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
            entry["binding"] = {
                "kind": "POLICY_BOUND",
                "decision": POLICY_BOUND[slot.slot_id],
                "why": "True because a registered decision says so, not because of a code fact.",
            }
        else:
            entry["binding"] = {
                "kind": "DESCRIPTIVE",
                "why": "Prose about how this screen behaves. No single code fact governs it; it is "
                "registered so that rewording it fails the check and forces a fresh reading.",
            }
        entries.append(entry)

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
            "Every honesty-chrome disclosure the staff console renders, with what holds it true. "
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
