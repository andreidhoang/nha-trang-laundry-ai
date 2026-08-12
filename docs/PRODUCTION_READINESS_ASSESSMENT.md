# Production Readiness Assessment

**Assessed:** 2026-08-12
**Commit assessed:** `6e93371` on `andreidhoang/main`
**Method:** direct codebase measurement, not documentation review
**Status of this document:** analysis. It is **not normative**, resolves no decision, and authorizes
nothing. `delivery/` remains the machine-readable truth.

## Verdict

The authority layer is production-grade. The product is not connected to anything and its evidence
base is effectively zero.

The distance to production is not where the prose documents imply. The hard, unusual engineering —
deterministic domain, atomic mutation semantics, the constrained tool boundary — is largely done. The
ordinary last mile is entirely undone: the agent runtime is unreachable, no channel exists, the model
has never been invoked, and the staff console has no Shadow surface.

## 1. What measurement shows

### 1.1 Genuinely strong

| Layer | Measured |
|---|---|
| Deterministic domain | 3,365 LOC, 85 tests — pricing, promotion, delivery, SLA, quotes, capacity |
| Data layer | 5,128 LOC, 19 forward-only migrations, atomic mutation + event + audit + outbox |
| `packages/policy` | 392 LOC — real, typed, fail-closed |
| `packages/observability` | 640 LOC across 7 modules — redaction, correlation, telemetry, events |
| Containers | 3 hardened non-root Dockerfiles, private staging topology |
| Tool Facade | 9 independent validation gates between model output and any mutation |

`PROGRESS.md` (2026-07-31) describes `packages/policy` and `packages/observability` as one-line
placeholders. **That is stale.** Both are implemented.

### 1.2 Findings that contradict an optimistic reading

**F1 — The agent runtime is an orphan.**
`AgentCycle` in `apps/worker/src/nha_trang_laundry_worker/host.py:24` is a bare type alias,
`Callable[[Any, AuthorityCheck], str]`, defaulting to `None`. `responses_runtime` is imported in
exactly one place — `__init__.py` — to re-export it. Nothing constructs a runtime, binds a context
loader, opens a bridge, or drains the agent queue. 1,216 lines of verified state machine that no code
path reaches.

**F2 — The eval corpus is at zero for every release-relevant layer.**

```
SEED                  32     FROZEN_REGRESSION      0    (manifest minimum: 200)
NORMAL_LANGUAGE        0     ADVERSARIAL            0
PUBLIC_CORPUS          0     PRODUCTION_REPLAY      0
SYNTHETIC_COMBINATORIAL 0
```

**F3 — The model has never been invoked.**
All 33 recorded results in `evidence/agent-shadow/local-synthetic-suite-v1.json` carry
`status: SKIP` and `runtime_path: DETERMINISTIC_DEGRADED`. The manifest declares its own five
release blockers, including `PRIMARY_PROVIDER_RESULTS_MISSING`.

**F4 — Test effort is allocated away from the product.**
Within `packages/evals/tests`: **175 tests exercise delivery, CI and supply-chain machinery; 33
exercise product behaviour.** Twelve of the machinery tests cover OpenClaw repackaging, a component
frozen by ADR-0004.

**F5 — The staff console has no Shadow surface.**
`apps/web` totals 617 lines (301 JavaScript). It has working screens for quotes, orders, approvals,
incidents, queue recovery and manual send. It has no conversation view, no agent-draft review, no
approve/edit/reject flow, no `UNKNOWN` reconciliation queue, no SLA risk board and no audit timeline.
`SHADOW-001` requires a human to approve every outbound message for 14 days; there is nowhere to do
that.

**F6 — No channel code exists.**
The API exposes 12 `/internal/v1/*` routes and no ingress path. Expected at this stage, but it means
the customer-facing path is at zero, not partially built.

## 2. Distance by layer

Percentages are engineering judgement, not measurement.

| Layer | Done | Gap |
|---|---|---|
| Deterministic authority | ~90% | polish |
| Control plane / internal API | ~75% | Shadow review surface |
| Agent product path | ~25% | transport, pipeline wiring, channel |
| Evidence base | ~2% | 0/10 G1 items, 0 provider runs, 0/200 frozen regression |
| Production infrastructure | ~15% | no production host, backup, monitoring or restore drill |
| Business readiness | ~10% | 11 of 12 pilot-gate boxes unchecked, 6 decisions open |

**To `G1_INTERNAL_SHADOW_READY`: ~25%. To `G2_PUBLIC_ASSISTED_ENTRY`: ~20%.**

## 3. Corrections to the 2026-08-12 plan

This assessment revises the plan authored the same day.

| Item | Correction |
|---|---|
| `EVAL-CORPUS-001` | Estimated at 2 weeks. Going 32 → 200+ graded Vietnamese cases with an adversarial layer, from consent-cleared mined history, is **4–6 weeks**. It gates `AGENT-002`, which carries G1 evidence. |
| Missing work item | Nothing wired the runtime into the worker. Added as `AGENT-PIPELINE-001`; `AGENT-002` now depends on it. |
| Missing work item | Nothing extended the staff console for Shadow. Added as `SHADOW-CONSOLE-001`; `SHADOW-001` now depends on it. |
| Timeline | 14–16 weeks to G2 was optimistic. **20–26 weeks** is defensible. |

The plan's sequencing holds — externally-gated items still dominate the critical path — but it
under-scoped two build items and one corpus item.

## 4. The structural observation

Disproportionate effort has gone into the machinery that governs delivery rather than the thing being
delivered: 175 machinery tests, a hosted supply-chain workflow debugged across seven commits,
byte-identical cross-platform rebuilds — for a system where the model has never run and no customer
has been served.

That effort is not waste. The authority layer is the reason this system could ever be pointed at real
customers, and most projects fail in the opposite direction, shipping an ungoverned agent onto a
payment path. But the marginal value has clearly flipped.

**The next quarter should be almost entirely product-path work:** wire the pipeline, build the
corpus, build the Shadow console, stand up the channel. Governance machinery should be touched only
where a specific gate demands it.

## 5. What would change this assessment

- `AGENT-PIPELINE-001` complete → agent product path moves from ~25% to ~45%.
- First PRIMARY provider eval run recorded → evidence base leaves zero for the first time.
- `EVAL-CORPUS-001` reaching the 200-case minimum → the single largest remaining gate input.
- `SHADOW-CONSOLE-001` complete → `SHADOW-001` becomes physically possible.
- Any of `DEC-001`–`DEC-006` resolved → business readiness moves.

Re-run this assessment after `AGENT-002`, not before. The intermediate states will not change the
verdict.
