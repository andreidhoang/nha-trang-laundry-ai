# 00 — Spec ledger

## 1. Honest scope statement

**This ledger is partial, and the partiality is deliberate rather than an omission.**

The specification pack is nine `specs/*_SPEC_V1.md` documents plus 20 machine-readable contracts,
and the requirement set runs to several hundred rows. Rather than produce a large ledger whose
rows are mostly `INFERRED` — which §1.4 of the directive would make worthless — this ledger covers
**only the requirements exercised at runtime during this audit**, where the status is real evidence.

Everything else is `NOT_ASSESSED`, stated as such, and is the first thing to extend if you want the
full §13.1 exit predicate met.

What partially substitutes for the missing rows, and is better than a hand-written ledger: this
repository already machine-checks a large part of its own specification conformance —
`scripts/verify_contracts.py`, `scripts/verify_workflow_conformance.py`,
`apps/api/tests/test_internal_api_contract.py`, `test_staff_console_contract.py`,
`test_console_disclosure_contract.py`, and `packages/evals/tests/test_store_scope_enumeration.py`.
The gap in that machinery is B-03 (87% of disclosures unfalsifiable), which is why R-07 is P1.

## 2. Assessed requirements

| ID | Source | Requirement | Surface | Actor | Status | Evidence |
|---|---|---|---|---|---|---|
| SEC-1 | `SECURITY_RELIABILITY_SPEC_V1.md` §295-313 | UI visibility is not authorization; server re-checks every call | API | both | **VERIFIED_OK** | 12 forged cross-store requests → `403`; auditor writes refused server-side |
| SEC-2 | same | Cross-tenant isolation | API | both | **VERIFIED_OK** | `02-BACKEND.md` §1 |
| SEC-3 | same | No tenant enumeration oracle | API | both | **VERIFIED_OK** | nonexistent store and unauthorized store return identical bytes |
| SEC-4 | same | CSRF double-submit on every mutation | API | human | **VERIFIED_OK** | `403 CSRF validation failed` |
| SEC-5 | same | No token in browser storage | UI | human | **VERIFIED_OK** | session cookie `HttpOnly; SameSite=strict; Secure`; `core/session.js:3-7` |
| REL-1 | `CHANNEL_ADAPTER_SPEC_V1.md:145` | A write is never auto-retried | UI | both | **VERIFIED_OK** | `core/api.js:20-30`; no retry observed on any failure path |
| REL-2 | `DOMAIN_DATA_API_SPEC_V1.md` | Idempotency key on every mutation, replay not duplicate | API | both | **VERIFIED_OK** | same key twice → identical `ticket_id`, one row |
| REL-3 | same | Optimistic locking; no last-write-wins | API | both | **VERIFIED_OK** | stale `If-Match` → `409 STALE_VERSION`; missing → `428` |
| FR-QTE-010 | quote lifecycle | Expired quote cannot be accepted, with a recoverable message | UI | human | **VERIFIED_OK** | `quotes.js:378-385`; message names the next action |
| DEC-021 | quote attestation | `Khách đã chốt giá` records who attested | UI | human | **VERIFIED_OK** | `201 …/acceptance`, `finality: APPROVED_EXACT` |
| DEC-013 | walk-in identity | Counter ticket is the customer reference | UI | human | **VERIFIED_OK** | `Phiếu số 1 — đọc số này cho khách` |
| PRICE-1 | `PRICEBOOK_V1.md` | 6kg cliff is a rule, disclosed, not smoothed | UI | human | **VERIFIED_OK** | cliff panel rendered at qty 6 with the counter-intuitive consequence spelled out |
| MONEY-1 | `CLAUDE.md` | Model never computes money; server decides | UI | machine | **VERIFIED_OK** | `120.000 ₫` from `POST /quotes`; screen states it does not add or round |
| FMT-1 | console spec | `dd/MM/yyyy`, 24h, `Asia/Ho_Chi_Minh`, `1.000.000 ₫` | UI | human | **VERIFIED_OK** | `06-VI-COPY.md` §8 |
| HITL-1 | §8 A2 | Human must approve before any customer-facing effect | UI | human | **VERIFIED_BROKEN** | **F-01** — decision controls permanently disabled |
| HITL-2 | §8 | Manual-send envelope is the only sanctioned send path | UI | human | **VERIFIED_BROKEN** | **D-05** — same root cause |
| HITL-3 | §8 | AI action attributable to model + version | data | machine | **NOT_IMPLEMENTED** | `assistant_turns` has no model column (B-06); N/A until a provider exists |
| M3-INC | `IMPLEMENTATION_ROADMAP_V1.md` M3 | Staff can open an incident at the counter | UI | human | **VERIFIED_BROKEN** | **D-04** — form unfillable; blocked on an owner decision |
| M3-ORD | M3 | Staff can create an order from an accepted quote | UI | human | **VERIFIED_BROKEN** | **D-01** — route works (`201`), form unfillable |
| M3-GAP | `IMPLEMENTATION_ROADMAP_V1.md:394` | No hidden unsupported default | UI | human | **VERIFIED_OK** | `/gaps` — 21 concrete entries, each naming route/table/decision |
| CAP-1 | `CAPABILITY_STATUS.yaml` | All 13 capabilities `NOT_AUTHORIZED` on a fresh clone | data | machine | **VERIFIED_OK** | read at runtime; no provider call made |
| IME-1 | §9.7 | Vietnamese IME composition does not drop diacritics | UI | human | **VERIFIED_OK** | `06-VI-COPY.md` §1 — real composition events |
| I18N-1 | §9.2 | One concept, one term, app-wide | UI | human | **VERIFIED_OK** | `06-VI-COPY.md` §6 |
| I18N-2 | §9.7 | Diacritic-insensitive search | UI | human | **VERIFIED_BROKEN** | G-02 — four call sites, no folding |
| I18N-3 | §9.7 | Vietnamese string expansion does not break layout | UI | human | **VERIFIED_BROKEN** | U-04 — 3 screens overflow at 390px |
| OBS-1 | `PRODUCTION_OPERATIONS_SPEC_V1.md` | One conversation reconstructible from logs | data | both | **VERIFIED_OK** | `assistant_turns` row + `correlation_id`/`trace_id` in structured logs |
| CI-1 | `CLAUDE.md` commands | Documented commands pass | repo | machine | **VERIFIED_BROKEN** | B-01, B-02 — pytest and mypy both fail on Linux |
| CI-2 | `CLAUDE.md` | A green test run is not completion evidence | repo | machine | **VERIFIED_BROKEN** | B-03 — two contract tests assert nothing |

## 3. Spec contradictions

**None blocking found.** Two near-misses, both resolved in the code's favour and both already
documented by the repository itself:

1. `SECURITY_RELIABILITY_SPEC_V1.md:295` publishes a role × capability matrix that differs from
   what the API enforces (AUDITOR reads the order board but is refused quotes, incidents, approvals
   and queue recovery). `core/rbac.js:1-25` states the divergence explicitly and implements the
   enforced reality rather than the spec's intent, on the stated grounds that "showing an operator a
   button that always fails is worse than not showing it." That is the right call, and it is
   written down. **Recommend the spec be amended to match**, so the next reader does not re-derive
   it — `NEEDS_HUMAN_DECISION`, non-blocking.
2. `gaps.js:298` claims the approval decision buttons *"hiện ra nhưng bị vô hiệu hoá kèm lý do"*.
   With an empty queue no such buttons exist. Verified accurate once an item is present — see
   `10-RETRACTED.md` R-03. Not a contradiction.

## 4. Spec gaps — behaviour the product needs that no document defines

- What an approver is shown **before** approving (F-01b). The A2 gate is specified; its human
  interface is not.
- What happens to an approval that **expires** unactioned — no document states whether the proposed
  action is abandoned, retried, or escalated.
- Incident intake hash semantics — open by name in
  `docs/DECISION_REQUEST_INCIDENT_INTAKE_2026-09.md`.
- Model identity capture for AI actions (B-06) — §8 requires it; no schema slot exists.
- Concurrent-edit UX beyond the `409`: the server is correct, but no document says what two
  operators at two tablets should *see*.
