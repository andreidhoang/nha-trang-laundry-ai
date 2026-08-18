# TASK-disclosure-contract-001 — govern the sentences that promise what the system does not do

**Goal:** make the staff console's honesty chrome a tested contract, so a behaviour change that
falsifies a rendered claim fails a check instead of shipping.

**Domains:** `platform`, `privacy_consent`

**Stable work item:** `DISCLOSURE-CONTRACT-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — the failure mode is silent. Nothing breaks; the console simply starts telling
operators something that is no longer true.

## Why this exists

`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md` §1 is explicit that these strings are not copy:

> No removal of mandated disclosures. The honesty chrome (gap notices, "not a KPI",
> `MANUAL_SEND_RECORDED` ≠ delivered, capability refusals) is **spec-mandated and contract-adjacent.**

Measured 2026-08-18 against `HEAD`: **58 disclosure slots** exist across `apps/web/src` — the values
of `guardrail:`, `missing:`, `why:`, `screen__lede` and `notice` — concentrated in `gaps.js` (19) and
`rbac.js` (15). **None of them appears in `apps/api/tests/test_staff_console_contract.py` or in
`scripts/verify_console_interaction.py`.** Coverage is zero, and those two files are the console's
entire safety net, because `apps/web` has no unit tests at 12,830 lines.

### The instance that makes this concrete, and how it was found

`apps/web/src/screens/assistant.js:487` renders, to operators, in Vietnamese:

> "chữ chạy là nhịp hiển thị của câu trả lời đã lưu, **không phải mô hình đang sinh từ**"
> *(the running text is display pacing of an already-saved answer, not a model generating words)*

Today that is exactly true. `answer_sse_frames` replays a persisted string; no model is called on
that path.

**It becomes false the moment a provider-backed brain is passed to `AssistantService(brain=…)`** —
a one-line change at `apps/api/src/nha_trang_laundry_api/assistant.py:376-382`, which is a
dependency-injection seam that exists precisely so that swap can happen. Nothing in the repository
would catch it. The string is in no test, and the swap is in a different package from the sentence.

That is the same class of defect the governance layer exists to prevent — a claim about system
behaviour with nothing binding it to the behaviour — sitting in the one layer the governance layer
does not reach.

### Why this was under-measured once already

An earlier note framed this as **one** string in `assistant.js`. That was true and unhelpfully small.
Fixing one string would have added a test for the sentence someone happened to notice and left 57
others in the same condition. The finding is the coverage number, not the example.

## Required design

- A machine-readable disclosure registry — a contract file, not a list in prose — where each entry
  binds a rendered string to **the code fact that makes it true**: a module, a constant, a default,
  or a flag. `assistant.js:487` binds to `AssistantService`'s default brain being
  `DeterministicAssistantBrain`.
- A check that fails when the bound fact changes and the string does not. For the assistant case
  that means: if the production factory stops defaulting to the deterministic brain, the test that
  guards the sentence fails until the sentence changes.
- Not every one of the 58 needs a code binding. Some are genuinely descriptive. The item must
  classify all 58 and state, per entry, whether it is **bindable** (a testable code fact),
  **policy-bound** (true because a decision says so — cite the `DEC-`), or **descriptive** (no
  binding needed, and say why).
- Vietnamese strings are the source of truth. Do not add an English translation layer to make
  testing convenient; test the string that renders.

## Constraints

- **No disclosure may be deleted or weakened by this item.** It adds bindings; it does not edit copy,
  except where a binding proves a string is already false — and that case is a finding to report,
  not a silent fix.
- No new framework, build step or npm tree — `DEC-012`.
- The registry is data. A test that hard-codes 58 strings inline is the same defect one layer up.
- If a string turns out to be false today, stop and report it before changing anything. A currently
  false disclosure is an incident, not a refactor.

## Required tests

- every disclosure slot in `apps/web/src` appears in the registry, proven by enumerating the source
  rather than by a maintained list;
- a bindable entry fails when its bound code fact changes, proven by a negative test that flips the
  fact in a fixture;
- adding a new disclosure slot without a registry entry fails the check;
- the assistant streaming disclosure specifically fails when `AssistantService` is constructed with a
  non-deterministic brain.

## Done when

- all 58 slots are classified and registered;
- every bindable entry has a failing-on-change test;
- `verify_console_interaction.py` or the console contract suite runs the check on every run;
- no disclosure string was deleted, and any found to be already false is recorded as a finding.
