# Project status

**Last updated:** 2026-08-18
**Authoritative machine status:** [`delivery/CAPABILITY_STATUS.yaml`](../delivery/CAPABILITY_STATUS.yaml)
**Measured distance to production:** [`PRODUCTION_READINESS_ASSESSMENT.md`](PRODUCTION_READINESS_ASSESSMENT.md)
then [`PATH_TO_PRODUCTION_REVIEW.md`](PATH_TO_PRODUCTION_REVIEW.md)

## Current decision

The project is authorized to build the internal deterministic control plane. No customer-facing agent,
public channel, automated send, autonomous quote, booking, delivery decision, or remedy is authorized.

## Stable phase position

| Phase | Status | Evidence |
|---|---|---|
| `FOUNDATION` | Complete locally | workspace, contracts, context/delivery harness, PostgreSQL transaction and configuration primitives |
| `IDENTITY_CONTROL` | Complete locally | named staff, DB-authoritative RBAC, MFA/session boundaries, audit/outbox, and negative authorization evidence |
| `DOMAIN_CORE` | Complete locally | canonical registry, exact pricebook import, pricing, promotion/delivery/SLA boundaries, immutable quote snapshots and calculation traces |
| `OPERATIONS_CONTROL` | In progress | `QUOTE-COMMAND-001` and `SETTLEMENT-001` are both now Complete. `POST /internal/v1/stores/{store_id}/quotes` creates an immutable priced quote revision, and `POST /internal/v1/orders/{order_id}/settlement` records the exact-payment, self-collection case — `packages/db/tests/test_settlement.py::test_an_order_reaches_completed_after_settlement` proves an order reaches `COMPLETED` end to end. **Every other settlement shape (partial payment, deposits, `ON_ACCOUNT` B2B credit) stays `NOT_SUPPORTED` by decision** — `DEC-010`, resolved 2026-08-18 as deliberately deferred, not an oversight. The staff console was rebuilt the same day around a "today" view (`GET /internal/v1/stores/{id}/settlements/today`) and picks services by name rather than typed code |
| `AGENT_SHADOW` | In progress | bounded Responses runtime, fixed Tool Facade, short-lived Runner bridge and durable run/tool ledger exist, and `AGENT-PIPELINE-001` wired the runtime into the worker — a job now travels queue to persisted evidence. `TOOL-BACKEND-001` (Complete) built a real deterministic backend, but **the production wiring still defaults to `UnavailableAgentToolBackend`** (`apps/public-agent-tools/src/nha_trang_laundry_agent_tools/facade.py:153`, verified 2026-08-18) — by design, gated behind capability flags that are all `NOT_AUTHORIZED`, not a gap. The OpenClaw evidence track is frozen by ADR-0004; `AGENT-002` carries G1 agent evidence and is blocked on `DEC-006`, the one decision that did not resolve in the 2026-08-18 session |
| `PRODUCTION_HARDENING` | In progress | CI, observability, policy, container, supply-chain, worker hosting, staff workflows, HTTP security, telemetry and private staging are complete. Remaining: the channel envelope, the Shadow console, retention, runbooks, SLO verification, and everything behind the hosting decision |
| `REAL_SHADOW_READINESS` | Not authorized | `G1_INTERNAL_SHADOW_READY` evidence absent |
| `PUBLIC_ASSISTED` | Not authorized | G1/G2 and capability-specific evidence absent |
| `BOUNDED_AUTONOMY` | Not authorized | cumulative G1–G4 evidence absent |

`delivery/PROGRAM_PLAN.yaml` is the stable phase vocabulary. Older M-number headings are explanatory
only; queue items use stable domain IDs.

## Current production authorization

```text
INTERNAL_SHADOW: NOT AUTHORIZED
PUBLIC_FAQ:      NOT AUTHORIZED
LIST_PRICE_INFO: NOT AUTHORIZED
```

Run `uv run python scripts/report_delivery_status.py` for the current machine-readable report. The
release decision is driven by evidence and a signed gate manifest, never by this prose status page.

## Next controlled task

`RETENTION-001`, selected by `uv run python scripts/run_delivery_loop.py` as of 2026-08-18 —
verified fresh, not carried forward. `DEC-008` (the retention schedule) resolved the same day and
was its sole blocker; the control mechanism (`packages/db/src/nha_trang_laundry_db/retention.py`)
already exists and is tested, so this item is purge targets and schedule publication, not new
architecture.

`DEMO-STACK-001`, `QUOTE-COMMAND-001`, `SETTLEMENT-001` and `TOOL-BACKEND-001` — the four items the
2026-08-14 readiness re-measurement found buildable without any open decision — are all `Complete`.
`OPERATIONS_CONTROL` returned to `IN_PROGRESS` when that work started (it had been marked `COMPLETE`
while no route in the system could create a quote) and stays `IN_PROGRESS` today: the settlement
path only covers exact-payment self-collection, per `DEC-010`'s deliberate deferral above.

## What is actually holding the customer-facing path

Engineering is not the binding constraint on any *customer-facing* milestone. As of 2026-08-18, nine
of the ten decisions that used to sit on this list resolved in one session — `DEC-001` through
`DEC-005`, `DEC-009` through `DEC-012` — leaving three genuinely calendar-bound or external items and
one real remaining decision:

- `SHOP-INSTRUMENT-001` — 4–6 weeks of real shop measurement. Without it `SHADOW-001` has no
  denominator and G1 cannot be evaluated at all.
- `CHANNEL-ZALO-APPLY-001` — 2–8 weeks of external OA verification that no code shortens. `DEC-005`
  (resolved) names Telegram for Shadow validation in parallel — a bot token costs minutes and needs
  no verification, but no token has been created yet.
- `PROVIDER-ACCESS-001` / `DEC-006` — the one decision left on the AGENT_SHADOW critical path. A
  stance toward proceeding is recorded (`docs/DECISION_REQUEST_PROVIDER_DATA_2026-08.md`), but real
  resolution needs a verified OpenAI account setting (`store:false`, Zero Data Retention approval)
  and a legal check on cross-border customer PII — neither exists yet, so the model has never been
  invoked and the evidence base stays at zero.

`DEC-008` (the retention schedule) was the decision that used to be called out here as "costs
minutes" — it is resolved now, and `RETENTION-001` above is the direct result. There is no longer a
decision on this board that costs minutes; what remains is either calendar-bound or the DEC-006
verification-and-legal-check pair.

Two new decisions opened 2026-08-18, surfaced by the console rebuild rather than by a specification
gap, and block nothing today: `DEC-013` (how a walk-in customer with no prior channel message is
identified) and `DEC-014` (which staff roles may see the day's takings). See
[`docs/DECISION_REQUEST_WALKIN_IDENTITY_2026-08.md`](DECISION_REQUEST_WALKIN_IDENTITY_2026-08.md).

Three more opened the same day from the client-acquisition session — `DEC-015` (what a customer
record is, and when a person becomes one — **this is the CRM decision §5.2 of the readiness
assessment recommends opening as `DEC-011`, which was taken by the staff IdP**), `DEC-016` (who
staffs the inbound channel and whose account it is), `DEC-017` (which name the shop markets under,
given that the brand is a 500+ store national franchise whose directory lists no Khánh Hòa store).
See [`docs/DECISION_REQUEST_ACQUISITION_2026-08.md`](DECISION_REQUEST_ACQUISITION_2026-08.md) and
the plan at [`docs/CLIENT_ACQUISITION_EXECUTION_2026-08.md`](CLIENT_ACQUISITION_EXECUTION_2026-08.md).
**The registry now holds 17 decisions, 6 `OPEN`.** `DEC-013` is no longer only a console gap: it is
the binding constraint on acquiring a `bound_contact_id` from any source, so the system can record
no customer at all today.

Each has a task packet under `context/tasks/` written to be actionable without an engineer present.
[`PATH_TO_PRODUCTION_REVIEW.md`](PATH_TO_PRODUCTION_REVIEW.md) §5 is the full owner action table
(pre-dates the 2026-08-18 resolutions; read its structure, not its decision-status claims).

## How to drive this repository

An owner-directed 2026-08-18 session produced
[`AGENTIC_PRODUCTION_HARNESS_PLAYBOOK_2026-08.md`](AGENTIC_PRODUCTION_HARNESS_PLAYBOOK_2026-08.md) —
analysis, not normative. Its frame: **every gate spends evidence, so evidence production is the only
accelerator this architecture recognizes.** The line reads 0 of 1,300 eval cases, 0 provider runs,
0 reachable agent processes, 0 recordable customers; two are agent-movable, two are owner-only, and
that division is the schedule. It also carries a prompt library, the context-tier doctrine, a
proposal-object spec for the agentic console surface, and a sequencing table with an actor on every
row.

Two live findings from it, neither yet enqueued: `apps/web/src/screens/assistant.js:487` renders a
factual claim about the system with **no test behind it**, which becomes false when a provider-backed
brain is wired; and two routes the console depends on
(`GET .../settlements/today`, `GET /internal/v1/pricebook/services`) have **no `specs/` contract**.

The same session added `.claude/agents/console-engineer.md`, `.claude/agents/eval-engineer.md`, three
skills under `.claude/skills/`, and one `PostToolUse` hook that regenerates `sw.js` on `apps/web`
writes. Nothing was enqueued; no capability, gate or decision changed.

Executing against the queue in the same session completed **`RETENTION-001`** (its `DEC-008` blocker
had resolved four days after the work was built and parked) and **`EVAL-SYNTHETIC-COMBINATORIAL-001`**,
which moves the eval corpus from **0 to 669 of 1,300** and meets the first of five suite minima. The
second was `BLOCKED` by a condition its own text said had lifted; it was unblocked on the verified
changed condition that `EVIDENCE-REPIN-001` is `COMPLETE`, and the hash-pinned manifest changed through
that item's sanctioned re-derivation rather than by hand. **No release blocker was removed** —
`REQUIRED_DATASET_MINIMA_NOT_MET` stays while four suites sit at zero — and all 13 capabilities remain
`NOT_AUTHORIZED`. Queue: 42 → 44 `COMPLETE`.

Read the [engineering continuation brief](../context/PROJECT_CONTINUATION.md) before resuming and run
`uv run python scripts/run_delivery_loop.py` for the authoritative work brief.
