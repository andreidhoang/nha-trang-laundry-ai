# Project status

**Last updated:** 2026-08-13
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
| `OPERATIONS_CONTROL` | In progress | Staff PWA slice, approvals, inbox/outbox, idempotency, audit and operational workflows are built. The command surface is not: no route creates a quote, and no path records payment or collection, so no order can reach `COMPLETED` — `QUOTE-COMMAND-001`, `SETTLEMENT-001` |
| `AGENT_SHADOW` | In progress | bounded Responses runtime, fixed Tool Facade, short-lived Runner bridge and durable run/tool ledger exist, and `AGENT-PIPELINE-001` wired the runtime into the worker — a job now travels queue to persisted evidence. The Facade's production backend is still `UnavailableAgentToolBackend` (`TOOL-BACKEND-001`). The OpenClaw evidence track is frozen by ADR-0004; `AGENT-002` carries G1 agent evidence and is blocked on `DEC-006` |
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

`DEMO-STACK-001`, selected by the controller. On 2026-08-14 the owner authorized four build items
that the 2026-08-14 readiness re-measurement identified as buildable without any open decision:
`DEMO-STACK-001`, `QUOTE-COMMAND-001`, `SETTLEMENT-001` and `TOOL-BACKEND-001`. Before they were
enqueued the controller selected nothing, and the queue read as fully decision-bound — which was
true of the items in it and false of the work the specification still requires.

`OPERATIONS_CONTROL` returned to `IN_PROGRESS` as a consequence. It had been marked `COMPLETE` while
no route in the system could create a quote.

## What is actually holding the customer-facing path

Engineering is not the binding constraint on any *customer-facing* milestone. Six of the ten
highest-priority externally-gated items need an owner action, and three of those are calendar-bound
and independent of each other:

- `SHOP-INSTRUMENT-001` — 4–6 weeks of real shop measurement. Without it `SHADOW-001` has no
  denominator and G1 cannot be evaluated at all.
- `CHANNEL-ZALO-APPLY-001` — 2–8 weeks of external OA verification that no code shortens.
- `PROVIDER-ACCESS-001` / `DEC-006` — days once decided, and until then the model has never been
  invoked and the evidence base stays at zero.

One further decision costs minutes and unblocks retention: `DEC-008` (the retention schedule).
`EVIDENCE-REPIN-001` was the other; the owner chose re-derivation on 2026-08-13 and it is complete,
so the six items behind the pin no longer wait on it, each retaining its own remaining blocker.

Each has a task packet under `context/tasks/` written to be actionable without an engineer present.
[`PATH_TO_PRODUCTION_REVIEW.md`](PATH_TO_PRODUCTION_REVIEW.md) §5 is the full owner action table.

Read the [engineering continuation brief](../context/PROJECT_CONTINUATION.md) before resuming and run
`uv run python scripts/run_delivery_loop.py` for the authoritative work brief.
