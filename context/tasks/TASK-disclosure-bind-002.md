# TASK-disclosure-bind-002 — correct a false completion in DISCLOSURE-CONTRACT-001

**Goal:** make the console disclosure registry's own claims true. Three of its bindings are vacuous,
its completeness claim is false by roughly forty per cent, and a third of its `DESCRIPTIVE` entries
carry a rationale that is untrue of them.

**Domains:** `platform`, `privacy_consent`

**Stable work item:** `DISCLOSURE-BIND-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. The artifact being corrected currently provides false assurance, which is worse
than providing none — a green suite that cannot fail is read as a check that passed.

## Why this exists

`DISCLOSURE-CONTRACT-001` was marked `COMPLETE` and it should not have been. Its evidence record
admits it shipped without a fresh-context adversarial pass because two reviewer agents died. The
retry ran three lenses and **all three refuted it**. `context/CONTINUATION_PROTOCOL.md` makes a
`COMPLETE` item immutable planning history unless a corrective item is created; this is that item.

### Defect 1 — three of fifteen `SERVER_GATE` bindings cannot fail

**Proved by mutation.** Adding `DRIVER` to `ORDERS_READ` in `rbac.js` — a false claim about who may
read the order board — leaves all 24 tests green and `verify_contracts.py` passing.

The cause is structural. `_probe_gate` returns all six `StaffRole` values for the `current_principal`
gate, and the console vocabulary has exactly those six aliases, so `console_roles <= server_roles` is
a tautology. `ORDERS_READ`, `SHADOW_READ` and `SHADOW_DECIDE` are bound to that gate.

**The right truthmakers exist and were not used.** These claims are enforced at the repository layer,
not the route gate:

| Capability | Console claims | Actually enforced by |
|---|---|---|
| `ORDERS_READ` | OWNER, APPROVER, OPERATOR, AUDITOR | `_require_order_read` in `packages/db/.../orders.py:438-446` — the same four |
| `SHADOW_READ` | OWNER, APPROVER, OPERATOR, AUDITOR | `SHADOW_READ_ROLES`, `shadow_console.py:35-37` — the same four |
| `SHADOW_DECIDE` | OWNER, APPROVER | `SHADOW_DECIDE_ROLES`, `shadow_console.py:39` — the same two |

Because these are exact sets rather than a permissive gate, the binding can assert **equality**, which
is strictly stronger than the subset property used elsewhere.

### Defect 2 — the completeness claim is false

The registry says it holds "every honesty-chrome disclosure the staff console renders" and
`test_every_disclosure_slot_is_registered` asserts set equality against the enumerator, so the check
is true of the enumerator rather than of the console. The enumerator reads six object keys plus the
first `null,` paragraph of a notice. Every string rendered with a props object is invisible.

Measured by the reviewer: roughly **62 rendered behavioural claims outside a registry of 87**, in
`notice__title`, `screen__lede`, `hint` and `eyebrow`, plus the whole of `core/errors.js` `MESSAGES`
and `core/i18n.js` `REASON_NOTE`.

Two of the misses matter more than their count. `manualSend.js` renders
**"MANUAL_SEND_RECORDED không có nghĩa là khách đã nhận"** as a `notice__title` — one of the four
chrome examples the UX spec names by name, and one of the four the enumerator's own docstring quotes.
And `assistant.js` renders a **second** model-seam claim as a `screen__lede`, in the same file as the
registered one, falsified by the same one-line `AssistantService(brain=...)` swap.

This is the same miss class that took the registry from 59 to 87 slots during the original item. It
was patched once and not generalized, which is the more useful lesson than the count.

### Defect 3 — a rationale that is untrue of about twenty entries

Every `DESCRIPTIVE` entry is stamped "no single code fact governs it". For roughly 20 of 66 a single
already-committed code fact does: read-only-screen claims are checkable against the module's own
request calls, and route-does-not-exist claims are checkable against
`specs/contracts/internal-api-v1.openapi.yaml`, which landed in the immediately preceding commit.
`gaps.js` names `charges`, `payments` and `payment_allocations` literally and was parked
`DESCRIPTIVE` while four structurally identical gap notices got `ABSENT_TABLE` from a five-line regex.

### Defect 4 — `POLICY_BOUND` has no test of any kind

One entry, no assertion. See the incident below for why that mattered.

## The incident this item must NOT fix

`screens/orderDetail.js` tells operators that partial payment, deposits, instalments and credit are
refused **"kèm mã quyết định đang mở"** — with an *open* decision code. `DEC-010` is `RESOLVED`
(`context/DECISION_REGISTRY.yaml`, resolved 2026-08-18, deliberately deferred).

**The console is telling the shop owner a decision is still open that was decided.** That is a
currently false disclosure, and `TASK-disclosure-contract-001.md` is explicit: a string found false
today is an incident to report, not a refactor. **Do not edit the copy.** Record it, bind the entry
so it cannot recur silently, and leave the wording to the owner — it is customer-facing text about a
decision they made.

## Required design

- New binding kind `REPOSITORY_ROLES`: console roles must **equal** a named `frozenset` in a named
  module. Replaces the three vacuous `current_principal` bindings.
- Make a vacuous binding impossible rather than merely absent: any `SERVER_GATE` binding whose probed
  role set equals the full `StaffRole` enumeration is a generation failure, because such a binding
  can never fail.
- Extend the enumerator to strings rendered with a props object, and to `core/errors.js` and
  `core/i18n.js`. Where extension is genuinely infeasible, **narrow the registry's stated purpose to
  match what it covers** — an accurate smaller claim beats an inaccurate larger one.
- Reclassify the bindable `DESCRIPTIVE` entries, adding `ABSENT_ROUTE`, `RESPONSE_SHAPE` and
  `READ_ONLY_MODULE` kinds.
- Give `POLICY_BOUND` a test: the cited decision must exist in the registry, and the entry records
  its status so a resolved decision described as open fails.

## Required tests

- each replaced binding fails under mutation, demonstrated the way the original four were;
- a `SERVER_GATE` binding covering all six roles fails generation;
- the enumerator captures the `manualSend.js` `notice__title` and the `assistant.js` `screen__lede`;
- a `POLICY_BOUND` entry whose decision status contradicts its text fails.

## Done when

- no binding in the registry can pass vacuously, proven by mutation for every kind;
- the registry's stated purpose is true of what it contains;
- the `DEC-010` disclosure is bound and its falsity is recorded as an owner finding, with the copy
  unchanged;
- the full gate battery passes with no required skips.
