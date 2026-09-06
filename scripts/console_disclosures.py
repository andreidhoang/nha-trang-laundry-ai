"""Enumerate the staff console's honesty chrome, and the registry that binds it to code.

`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md` §1 says these strings are not copy:

    No removal of mandated disclosures. The honesty chrome (gap notices, "not a KPI",
    MANUAL_SEND_RECORDED != delivered, capability refusals) is spec-mandated and contract-adjacent.

The repository classified them correctly and then tested none of them. `apps/web` has no unit tests
at 12,830 lines, and not one of these strings appeared in `test_staff_console_contract.py` or in
`verify_console_interaction.py`. A rendered claim about what the system does not do, with nothing
binding it to what the system actually does, is the same defect class the governance layer exists to
prevent — sitting in the one layer that layer does not reach.

The worked example: `assistant.js` tells operators the streamed text is display pacing of a saved
answer, "không phải mô hình đang sinh từ". That is true today and becomes false the moment a
provider-backed brain is passed to `AssistantService(brain=...)`, which is a one-line change at a
dependency-injection seam that exists so the swap can happen.

A slot's identity is its file, its key, and the hash of its normalized text. Rewording a disclosure
therefore changes its identity and fails the check until it is re-registered — which is the point.
Re-registration is where someone has to look at the sentence again and say whether it is still true.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SOURCE = ROOT / "apps/web/src"
REGISTRY_PATH = ROOT / "specs/contracts/console-disclosures-v1.yaml"

#: The object keys that carry honesty chrome. `guardrail` and `missing` are the spec's gap notices;
#: `why` is the capability-refusal reason a disabled control shows; `caveat`, `note` and `lede` are
#: the screen-level disclosures. A key added here without a registry entry fails the check.
DISCLOSURE_KEYS = (
    "guardrail",
    "missing",
    "why",
    "caveat",
    "note",
    "lede",
    "today",
    "blockedBy",
)

#: `blockedBy` was missing until CONSOLE-LIFECYCLE-001, and it is the key that names *decisions* --
#: so `#/gaps` twice told staff a settled decision was still being made, under a "Bị chặn bởi"
#: label, and neither the registry nor any test could see it. Length is why the worst instance
#: stayed invisible even in principle: the entry read `blockedBy: "DEC-004"`, seven characters,
#: far under `MINIMUM_LENGTH`. Registering the long ones is worth doing, but the guard that
#: actually catches the class reads the source directly and is in the contract test.

#: `today` was missing from that tuple until CONSOLE-ORDER-GAP-001, found by noticing the slot count
#: rose by two when three strings were added. It is the key on every `#/gaps` entry that tells an
#: operator what to do *instead* -- "ghi tay như trước" -- which makes
#: it the most operationally load-bearing string there, and the one most likely to go stale
#: silently when the gap it describes closes.

#: Keyed slots are not the whole population, and assuming they were would have missed the example
#: this item exists for. The assistant's streaming disclosure - the sentence that becomes false the
#: moment `AssistantService` is given a provider-backed brain - is rendered positionally as
#: `h("p", null, "...")` inside a `notice` element, so no key names it. Notice bodies are therefore
#: enumerated as well, under the synthetic key below.
NOTICE_KEY = "notice"

#: A notice shorter than this is an inline status line, not a disclosure.
NOTICE_MINIMUM_LENGTH = 40

#: Element classes that carry a rendered claim. Keying only on object properties and the first
#: `null,` paragraph of a notice left roughly sixty claims invisible, including two that mattered
#: more than their count: the `notice__title` carrying "MANUAL_SEND_RECORDED không có nghĩa là khách
#: đã nhận" -- one of the four chrome examples the UX spec names -- and a second model-seam claim in
#: `assistant.js` rendered as a `screen__lede`, falsified by the same one-line brain swap as the
#: registered one. A string passed after a props object is not less of a disclosure for it.
CLAIM_CLASSES = ("notice__title", "screen__lede", "hint", "eyebrow", "notice")

#: Object literals in `core/` that are pure claim vocabulary rather than screen prose.
CLAIM_TABLES = (
    ("core/errors.js", "MESSAGES"),
    ("core/i18n.js", "REASON_NOTE"),
)

#: Below this length a string is a label, not a disclosure. Measured: the shortest genuine
#: disclosure on the console is 46 characters.
MINIMUM_LENGTH = 25

_SLOT = re.compile(
    r"(?P<key>" + "|".join(DISCLOSURE_KEYS) + r")\s*:\s*"
    r'(?P<value>(?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+)'
)
_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
_NOTICE = re.compile(r'class:\s*"notice[^"]*"')
_NOTICE_BODY = re.compile(r'null,\s*((?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+)')

_CLASSED = re.compile(
    r'h\(\s*"[a-z0-9]+"\s*,\s*\{[^{}]*class:\s*"([a-z_]+(?:__[a-z-]+)?)[^"]*"[^{}]*\}\s*,\s*'
    r'((?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+)'
)
_TABLE_ENTRY = re.compile(r'(\w+)\s*:\s*((?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+)')

#: How far past a `notice` marker to look for its body. Measured: the longest gap in this console is
#: under 900 characters; 2500 is slack without reaching the next notice.
_NOTICE_WINDOW = 2500


@dataclass(frozen=True, slots=True)
class DisclosureSlot:
    """One rendered claim, identified by where it lives and what it says."""

    module: str
    line: int
    key: str
    text: str

    @property
    def slot_id(self) -> str:
        """Stable across reformatting, unstable across rewording — deliberately."""
        return f"{self.module}#{self.key}:{text_digest(self.text)[:12]}"


def normalize(text: str) -> str:
    """Collapse whitespace and normalize Unicode so reflowing a string is not a content change.

    NFC matters here: Vietnamese diacritics have more than one valid encoding, and a copy-paste
    through a different editor can change the bytes without changing a single visible character.
    """
    return unicodedata.normalize("NFC", " ".join(text.split()))


def text_digest(text: str) -> str:
    return sha256(normalize(text).encode("utf-8")).hexdigest()


def enumerate_slots(source_root: Path = WEB_SOURCE) -> list[DisclosureSlot]:
    """Every disclosure slot the console declares, keyed and positional, deterministically ordered.

    Deduplicated by identity rather than by position: the same sentence rendered twice in a module
    is one claim, and registering it twice would mean re-reading it twice for no gain.
    """
    seen: set[str] = set()
    slots: list[DisclosureSlot] = []

    def add(module: str, line: int, key: str, text: str) -> None:
        slot = DisclosureSlot(module=module, line=line, key=key, text=normalize(text))
        if slot.slot_id in seen:
            return
        seen.add(slot.slot_id)
        slots.append(slot)

    for path in sorted(source_root.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        module = path.relative_to(source_root).as_posix()

        for match in _SLOT.finditer(source):
            text = "".join(_LITERAL.findall(match.group("value")))
            if len(text) >= MINIMUM_LENGTH:
                add(module, source[: match.start()].count("\n") + 1, match.group("key"), text)

        for classed in _CLASSED.finditer(source):
            if classed.group(1) not in CLAIM_CLASSES:
                continue
            text = "".join(_LITERAL.findall(classed.group(2)))
            if len(text) >= NOTICE_MINIMUM_LENGTH:
                add(
                    module,
                    source[: classed.start()].count("\n") + 1,
                    classed.group(1),
                    text,
                )

        for table_module, table_name in CLAIM_TABLES:
            if module != table_module:
                continue
            start = source.find(f"{table_name} =")
            if start == -1:
                continue
            body = source[start : source.find("\n};", start) + 1]
            for item in _TABLE_ENTRY.finditer(body):
                text = "".join(_LITERAL.findall(item.group(2)))
                if len(text) >= NOTICE_MINIMUM_LENGTH:
                    add(
                        module,
                        source[: start + item.start()].count("\n") + 1,
                        f"{table_name}.{item.group(1)}",
                        text,
                    )

        for marker in _NOTICE.finditer(source):
            window = source[marker.end() : marker.end() + _NOTICE_WINDOW]
            body = _NOTICE_BODY.search(window)
            if body is None:
                continue
            text = "".join(_LITERAL.findall(body.group(1)))
            if len(text) >= NOTICE_MINIMUM_LENGTH:
                add(module, source[: marker.start()].count("\n") + 1, NOTICE_KEY, text)

    return sorted(slots, key=lambda s: (s.module, s.line, s.key))


#: How a disclosure is held true. The distinction matters: a bindable claim gets a test that fails
#: when the code changes, a policy-bound one cites the decision that makes it true, and a
#: descriptive one is prose about the screen that no code fact governs.
BINDING_KINDS = ("ABSENT_TABLE", "ABSENT_ROUTE", "SERVER_GATE", "POLICY_BOUND", "DESCRIPTIVE")

#: Bindings are AUTHORED, never derived from the disclosure text. An earlier attempt extracted
#: snake_case identifiers automatically and bound to `bound_contact_id`, `occurred_at` and
#: `session_id` — all columns that exist — which would have asserted the opposite of the truth. A
#: binding nobody verified is a guess laundered into a contract, so each one is written by hand
#: against the schema or the route contract and the check proves it.
