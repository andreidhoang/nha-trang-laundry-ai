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
| `OPERATIONS_CONTROL` | Complete locally | Staff PWA slice, approvals, inbox/outbox, idempotency, audit and operational workflows |
| `AGENT_SHADOW` | In progress | bounded Responses runtime, fixed Tool Facade, short-lived Runner bridge and durable run/tool ledger exist. The OpenClaw evidence track is frozen by ADR-0004; `AGENT-002` now carries G1 agent evidence and is blocked on `DEC-006`. The runtime is not yet wired into the worker — `AGENT-PIPELINE-001` |
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

`ENV-INTEGRITY-001` is `IN_PROGRESS`. Its implementation is complete and verified; its declared
acceptance still includes `uv run pytest --require-postgres-integration`, which has not run because
no PostgreSQL service is available in the current environment. That check is not recorded as passing.

Once it closes, the controller offers, in order: `CHANNEL-ENVELOPE-001` (the fan-out node for four
downstream channel items), `SIGNER-REGISTRY-001`, `AGENT-PIPELINE-001` (the highest single-item
value — it wires the bounded Responses runtime into the worker for the first time),
`SHADOW-CONSOLE-001` and `EVAL-SYNTHETIC-COMBINATORIAL-001`. None authorizes provider use, public
ingress or send.

## What is actually holding the project

Engineering is not the binding constraint. Six of the ten highest-priority ready items need an owner
action, and three of those are calendar-bound and independent of each other:

- `SHOP-INSTRUMENT-001` — 4–6 weeks of real shop measurement. Without it `SHADOW-001` has no
  denominator and G1 cannot be evaluated at all.
- `CHANNEL-ZALO-APPLY-001` — 2–8 weeks of external OA verification that no code shortens.
- `PROVIDER-ACCESS-001` / `DEC-006` — days once decided, and until then the model has never been
  invoked and the evidence base stays at zero.

Each has a task packet under `context/tasks/` written to be actionable without an engineer present.
[`PATH_TO_PRODUCTION_REVIEW.md`](PATH_TO_PRODUCTION_REVIEW.md) §5 is the full owner action table.

Read the [engineering continuation brief](../context/PROJECT_CONTINUATION.md) before resuming and run
`uv run python scripts/run_delivery_loop.py` for the authoritative work brief.
