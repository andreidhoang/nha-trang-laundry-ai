# Production Readiness Assessment

**Assessed:** 2026-08-14
**Commit assessed:** `8e5395d` (the tree as merged to `main` on 2026-08-14)
**Supersedes:** the 2026-08-12 assessment of `6e93371` (same path; see git history)
**Method:** direct codebase measurement, not documentation review
**Status of this document:** analysis. It is **not normative**, resolves no decision, and authorizes
nothing. `delivery/` remains the machine-readable truth.

## Verdict

Two of the six findings in the 2026-08-12 assessment are now false, and measuring what replaced them
exposed a larger one that the previous pass missed entirely.

**The authority layer is production-grade and almost nothing in the running system can reach it.**
~~The deterministic pricing engine — 3,365 lines and the single most valuable asset here — has no
caller outside tests and synthetic harnesses.~~ **Pricing is reachable as of 2026-08-14**
(`QUOTE-COMMAND-001`): a staff member prices a garment through the engine and the result is committed
as an immutable revision, priced against a pricebook published through `CONFIG-001` rather than read
from a file. The verdict above still holds for everything else, and the pricing sentence is left
struck through rather than deleted so the distance travelled stays visible. The ten-operation Tool Facade is wired in production to
a backend that returns `TOOL_UNAVAILABLE` for every operation. The three facts that make a laundry
order finished in the physical world — money received, goods handed back, delivery run completed —
are insert-time constants with no write path, with the provable consequence that **no order this
system creates can ever reach `COMPLETED`** (§3, G2).

Against the project's own specification, **14 of the 63 aggregates named in
`specs/DOMAIN_DATA_API_SPEC_V1.md` §4 exist as tables.** The 49 absent ones are not evenly spread.
Every table in Finance, Custody/Production, Delivery, Catalog, Pricebook, Promotion, CRM and
Organization is missing. What is built is the governance spine: approvals, audit, outbox, agent
ledger, consent, identity — 9 of 9 in §4.14.

That is a coherent thing to have built first, and it is the hard part. But the previous assessment's
"deterministic authority ~90%, gap: polish" row was measuring the engine, not the system, and the
engine is not the system.

## 1. Corrections to the 2026-08-12 assessment

| Old finding | Status |
|---|---|
| **F1** — the agent runtime is an orphan, 1,216 lines no code path reaches | **Partly fixed — this row was too generous, corrected 2026-08-14.** `AGENT-PIPELINE-001` built the composition and proved it: a job traverses queue → claim → runtime → persisted evidence, and claim exclusivity holds under a real two-worker race. But `build_agent_cycle` still has **no caller outside tests**. `apps/worker/.../main.py` constructs `WorkerSupervisor(settings)` with no cycle injected, so the deployed worker process cannot run the pipeline — setting `WORKER_AGENT_QUEUE_ENABLED` yields `AGENT_RUNTIME_UNAVAILABLE`, not a running agent. `AGENT-PIPELINE-001`'s own evidence says the module "is referenced by no process entry point", framed there as a rollback property. The runtime is assembled and testable; it is not reachable in a running system. Found by `DEMO-STACK-001`. |
| **F5** — the staff console has no Shadow surface | **Fixed.** `SHADOW-CONSOLE-001` complete. `agent_drafts` persists the proposal (previously only a character count was kept, so there was literally nothing to review); approve/edit/reject is attributed and single-shot by unique constraint; the `UNKNOWN` reconciliation queue has exactly one exit and it requires a named human; RBAC and IDOR are proven at the repository, not the route. |
| **F2** — eval corpus at zero for every release-relevant layer | **Still true, with one correction.** The five gated layers total 1,300 required cases — frozen regression 200, normal language 300, adversarial 200, synthetic combinatorial 500, public corpus 100 — and every one of them stands at **0**. The 32 `SEED` cases count toward none of them. But 669 synthetic combinatorial cases *are* built, priced by the deterministic engine and content-hashed at `specs/evals/synthetic-combinatorial-v1.json`; the manifest inventory reads `0` only because publishing the count was reverted during the hash-pin freeze. See §5. |
| **F3** — the model has never been invoked | **Still true.** `evidence/agent-shadow/local-synthetic-suite-v2.json`: `status: SKIP`, `runtime_path: DETERMINISTIC_DEGRADED`, `primary_provider_evidence: false`, `release_effect: NONE`, five declared release blockers. |
| **F4** — test effort allocated away from the product | **Still true.** `packages/evals` is 5,911 source + 8,019 test lines, larger than the domain and data layers combined. |
| **F6** — no channel code exists | **Partly fixed.** `CHANNEL-ENVELOPE-001` and `CONSENT-STOP-001` are complete: the canonical inbound envelope, outbound receipt, `webhook_events`, `channel_send_receipts`, consent and suppression are built and tested. No **adapter** exists — no provider is on the other end. |

The old document closed with "re-run this assessment after `AGENT-002`, not before." That was wrong
in one direction: the intermediate states did not change the verdict, but measuring against the
specification rather than against the previous document did.

## 2. What measurement shows now

### 2.1 Size

| Area | Source LOC | Test LOC |
|---|---|---|
| `packages/domain` | 3,365 | 1,689 |
| `packages/db` | 6,865 | 4,994 |
| `packages/contracts` | 2,168 | 864 |
| `packages/evals` | 5,911 | 8,019 |
| `packages/policy` | 392 | 297 |
| `packages/observability` | 640 | 261 |
| `apps/api` | 2,189 | 909 |
| `apps/worker` | 3,348 | 2,513 |
| `apps/public-agent-tools` | 416 | 246 |
| `apps/web` | 744 (JS/HTML/CSS) | — |

23 forward-only migrations, 38 tables, 25 API routes (24 under `/internal/v1`, enumerated from the
built `app` rather than by grep), 10 agent tool operations, 722 tests passing and 1
platform-conditional skip. 70 queue items: **35 COMPLETE, 20 PENDING, 15 BLOCKED.** 13 capabilities
`NOT_AUTHORIZED`. 8 open decisions.

### 2.2 Specification coverage — the headline number

`specs/DOMAIN_DATA_API_SPEC_V1.md` §4 names 63 aggregates across 14 bounded contexts.

| §4 context | Built | Missing |
|---|---|---|
| 4.1 Organization | 0/3 | `organizations`, `stores`, `store_addresses` |
| 4.2 Calendar and staffing | 0/5 | `business_calendar_versions`, `business_hours_rules`, `closure_dates`, `staff_members`, `staff_shifts` |
| 4.3 Party and CRM | 0/5 | `parties`, `customer_accounts`, `contact_points`, `addresses`, `account_contacts` |
| 4.4 Consent and communications | 2/4 | `conversations`, `messages` |
| 4.5 Catalog | 0/2 | `services`, `service_versions` |
| 4.6 Pricebook | 0/4 | `pricebooks`, `pricebook_versions`, `price_rules`, `price_tiers` |
| 4.7 Promotion | 0/3 | `promotion_versions`, `promotion_targets`, `promotion_applications` |
| 4.8 Quote | 2/4 | `quote_lines`, `quote_adjustments` |
| 4.9 Orders | 1/2 | `order_lines` |
| 4.10 Custody and production | 0/5 | `custody_units`, `custody_events`, `batches`, `batch_operations`, `batch_allocations` |
| 4.11 Delivery | 0/4 | `delivery_bundles`, `delivery_legs`, `distance_measurements`, `delivery_cost_events` |
| 4.12 Finance | 0/7 | `charges`, `payments`, `payment_allocations`, `refunds`, `invoice_requests`, `invoices`, `accounts_receivable_entries` |
| 4.13 Incidents, remedies, credits | 0/6 | `incidents`, `incident_events`, `evidence_assets`, `remedies`, `credit_grants`, `credit_ledger_entries` |
| 4.14 Approval, integration, audit | **9/9** | — |

Counting generously, four more have narrower stand-ins: `staff_users` for `staff_members`,
`customer_incidents` for a much smaller `incidents`, `order_requests` for the intake draft, and
`webhook_events` + `channel_send_receipts` for part of `messages`. That is **~18 of 63**.

The shape is unambiguous. The governance context is complete; the eight contexts that describe
running a laundry are empty.

## 3. Findings

### G1 — The deterministic authority has no production write path

`QuoteRepository.create_revision` (`packages/db/src/nha_trang_laundry_db/quotes.py:65`) is called
from `packages/evals/src/.../synthetic_quote_lifecycle.py`, `synthetic_incidents.py` and three test
modules. **It is called from no application.** `apps/api` imports `QuoteRepository` and uses exactly
one method: `list_for_store` (`apps/api/src/nha_trang_laundry_api/operations.py:337`).

The route table agrees: `GET /internal/v1/stores/{store_id}/quotes` exists and there is no `POST`
counterpart. Staff can list quotes and create an order *from* an accepted quote id; nothing in the
system produces that quote. The pricing engine, the promotion engine, the delivery engine and the SLA
engine — the four things this project is actually for — are reachable only from test harnesses.

The repository-level fact is the stronger one and does not depend on route enumeration: the write
method has no caller in any application.

The Tool Facade is the other half of the same fact.
`apps/public-agent-tools/src/.../facade.py:131` is the sole production wiring:

```python
return AgentFacadeService(UnavailableAgentToolBackend())
```

All ten operations return `TOOL_UNAVAILABLE`. The only implemented backend, `_BoundFactBackend`,
lives in `packages/evals` and serves exactly one operation.

This is fail-closed and therefore safe. It is also the reason the system cannot serve a customer even
with every decision resolved and every credential in hand.

### G2 — No order created by this system can ever be completed

This is provable from three files, and it is the clearest single measure of the distance to a real
deploy.

**The completion rule.** Both the domain engine and the database enforce the same four conditions for
`COMPLETED`. `transition_commercial` in `packages/domain/src/.../orders.py:106-114` requires
`ACTIVE`, `production = RELEASED`, `required_delivery_legs_succeeded OR self_collection_recorded`,
and `balance IN {PAID, ON_ACCOUNT}`. Migration `0007_operations_control.sql:245-250` repeats all four
as a table CHECK. Money and custody must be proven before an order is finished — exactly right.

**The insert.** `OrderRepository.create` (`packages/db/src/.../orders.py:143-153`) hardcodes
`balance_status = 'UNPAID'` and omits both fulfilment booleans, which take their `DEFAULT FALSE`.
`CreateOrderCommand` has no field for any of the three, so a caller cannot supply them.

**The update.** The only `UPDATE orders` statement in the codebase (same file, line 294) sets
`commercial_status`, `intake_status`, `production_status`, `production_resume_status`,
`production_accepted_at`, `closed_at` and `row_version`. **It touches none of the three.** Nothing
else in the repository writes them.

Every order is therefore born `UNPAID` with both fulfilment flags false, and no code path in the
system can change that. It can move through intake and production and then stops: the domain raises
`INVALID_STATE_TRANSITION: fulfillment is incomplete`, and if that were bypassed the CHECK constraint
would reject the row.

This is fail-closed and correct — the system refuses to call an order finished when it has no
evidence the customer paid or collected. But it means the order lifecycle is a three-quarters arc.
The missing quarter is not a feature; it is the settlement and custody commands that would supply the
proof the guard is asking for.

Consistent with that, `PaymentStatus` and `DeliveryLegStatus` exist in the canonical enum registry
(`packages/domain/src/.../catalog.py`) and are persisted nowhere. `delivery_attempts` is the outbox's
message-delivery ledger, unrelated to a motorbike. `incident_open` is likewise insert-only.

### G3 — There is no customer, no address, no shop, no price list

`store_id UUID NOT NULL` appears eight times across six migrations with **no foreign key anywhere and
no `stores` table**. A store is an unvalidated UUID. There is no shop record, no opening hours, no
closure calendar.

`bound_contact_id` is a UUID referencing nothing. `contact_channel_bindings` holds
`(provider, provider_user_ref)` and a verification state — no name, no phone, no order history.
Grepping the entire source tree for an address, street, ward or district field returns nothing. The
delivery engine takes `verified_distance_m`, an integer a staff member supplies.

Two readings, and the difference matters:

- **As shadow-phase design** this is correct and deliberate. Holding opaque bindings and content
  hashes rather than personal data is why `CONSENT-STOP-001` and the redaction layer are clean, and
  it is consistent with the prohibition on raw PII fixtures.
- **Against the specification** it is unbuilt, not descoped. §4.3 names `parties`,
  `customer_accounts`, `contact_points`, `addresses`; §4.11 names `distance_measurements`.

Someone has to decide which reading stands before a rider is dispatched anywhere. That decision is
not in `context/DECISION_REGISTRY.yaml`.

### G4 — The price list is not data

`import_pricebook_csv` parses an owner-confirmed CSV into an in-process `CanonicalPricebook`. No
`pricebooks`, `pricebook_versions`, `price_rules` or `price_tiers` table exists, and no repository or
route references one. Changing a price is a file change and a redeploy, not an operation.

`quote_revisions` does store an immutable JCS-SHA256 snapshot hash, so a quote is reproducible — the
integrity half of §4.6 is genuinely solved. The publication half is not: there is no published price
version an auditor could point at, and §5.1 of the spec (price resolution) has no runtime.

### G5 — Three items are marked `BLOCKED` by conditions their own text says have lifted

The queue is stale in a way that hides buildable engineering.

| Item | Its own `blocking_condition` now says |
|---|---|
| `EVAL-SYNTHETIC-COMBINATORIAL-001` | *"the sequencing decision is made and the conflict is resolved for all four items… `manifest_inventory_updated` can now be satisfied truthfully… Reverting the change was the correct call at the time; **it no longer is**."* The 669 cases are built, frozen and re-priced by `verify_contracts.py` on every run. |
| `SIGNER-REGISTRY-001` | *"the change-freeze half is lifted… the parked verifier on `spike/signer-registry-v2-verifier` can land."* Only the owner key ceremony remains, and that gates signing, not landing the verifier. |
| `MODEL-ROUTE-001` | The hash-pin blocker is lifted; only ADR-0008 acceptance remains — and that decides whether the item exists at all. |

`EVAL-SYNTHETIC-COMBINATORIAL-001` matters most: it is 500 of the 1,300 required eval cases, it
blocks `EVAL-CORPUS-001`, and `EVAL-CORPUS-001` blocks `AGENT-002`, which carries all G1 agent
evidence. This is a real critical-path item currently invisible to the controller.

`docs/STATUS.md` line 21 also still reads "the runtime is not yet wired into the worker —
`AGENT-PIPELINE-001`", and line 43 says "None is buildable… 21 pending items". Both predate this
week's completions.

## 4. Distance by layer

Percentages are engineering judgement, not measurement. The 2026-08-12 column is shown to make the
movement visible — and to show where the earlier number was measuring the wrong thing.

| Layer | 08-12 | 08-14 | Why it moved, or why it did not |
|---|---|---|---|
| Deterministic engines (pricing, promotion, delivery, SLA) | ~90% | ~90% | Unchanged and genuinely strong. |
| **Domain persistence and command surface** | *(not separated)* | **~30%** | The row that was missing. 14/63 aggregates. The quote write path exists as of 2026-08-14 (`QUOTE-COMMAND-001`), and is the first command in the system that produces a monetary artefact; settlement is next. |
| Control plane / internal API | ~75% | ~80% | Store scoping and the Shadow surface landed; still no create-quote, payment or fulfilment command. |
| Agent product path | ~25% | ~45% | Pipeline wired end to end; facade backend still `Unavailable`; no channel adapter. |
| Evidence base | ~2% | ~5% | 669 combinatorial cases exist unpublished; still 0 provider runs, and all five gated layers at 0/1,300. |
| Production infrastructure | ~15% | ~15% | No host, no monitoring, no backup, no restore drill. All behind `DECISION-HOSTING-001`. |
| Business readiness | ~10% | ~10% | 8 decisions open, 2 more than in August; shop baseline not started. |

**To `G1_INTERNAL_SHADOW_READY`: ~30%. To `G2_PUBLIC_ASSISTED_ENTRY`: ~20%.**

The G1 number moved; G2 did not, because G3 and G4 sit on the G2 path and were not previously
counted at all.

## 5. What "a full-featured core for real-world deploy" requires

Grouped by what actually gates each one. Nothing below is enqueued — proposing work is analysis,
adding it to `delivery/WORK_QUEUE.yaml` is a change to machine truth and needs the owner's word.

### 5.1 Buildable now, no decision required (~5–7 weeks)

| Proposed item | What it is | Why it is safe to build now |
|---|---|---|
| `QUOTE-COMMAND-001` | The create-quote command path: route → domain engine → `quote_revisions` with its snapshot hash. Refuses on any unresolved policy rather than guessing. | The engine, the snapshot table and the atomic-commit primitive all exist and are tested. This is wiring, and it is the single highest-value missing piece. |
| `EVAL-PUBLISH-001` | Publish the 669 combinatorial cases into `eval-manifest-v1.yaml` and re-derive the evidence bundle, retaining the superseded one. | `EVIDENCE-REPIN-001` established exactly this procedure. Unblocks 500 of 1,300 cases and clears one critical-path node. |
| `SIGNER-VERIFIER-LAND-001` | Land the parked v2 verifier from `spike/signer-registry-v2-verifier`, split from the key ceremony. | Verifier code and key ceremony are separable; only the ceremony is the owner's. |
| `TOOL-BACKEND-001` | A real `AgentToolBackend` over the deterministic domain, behind the existing capability flags, defaulting to unavailable. | The nine validation gates and the contract are complete; the backend is the missing implementation, not a new boundary. |

### 5.2 Buildable once one decision lands

| Proposed item | Gate |
|---|---|
| `FULFILMENT-001` — `delivery_bundles`, `delivery_legs`, `distance_measurements`, and the write path that flips `required_delivery_legs_succeeded` | `DEC-003` (delivery pricing beyond 6 km, one-leg policy) |
| `FINANCE-001` — `charges`, `payments`, `payment_allocations`, and a `balance_status` transition | An owner decision that does not yet exist. Partial payment, overpayment and `ON_ACCOUNT` are business policy, not engineering. **Recommend opening `DEC-010`.** |
| `CUSTODY-001` — `custody_units`, `custody_events`, `batches` (item-count handoff, the thing that resolves "you lost my shirt") | `SHOP-INSTRUMENT-001` produces the checklist this models |
| `REMEDY-001` — `remedies`, `credit_grants`, `credit_ledger_entries` | `DEC-004` (rewash, loss, damage, compensation) |
| `CATALOG-PRICEBOOK-001` — persist and version the price list; `stores`, `organizations` | `DEC-001` (weight precision and rounding) touches every stored price |
| `PARTY-001` — decide whether a CRM exists at all, then `parties` / `contact_points` / `addresses` | Not currently a registered decision. **Recommend opening `DEC-011`.** |

### 5.3 Calendar-bound, no code shortens them

- `SHOP-INSTRUMENT-001` — 4–6 weeks of real measurement. Without it `SHADOW-001` has no denominator.
- `CHANNEL-ZALO-APPLY-001` — 2–8 weeks of external OA verification.
- `CORPUS-CONSENT-001` — lawful basis for using message history; gates 300 eval cases.
- `PROVIDER-ACCESS-001` + `DEC-006` — days once decided; until then the evidence base stays at zero.
- `DECISION-HOSTING-001` — gates deploy target, monitoring, backup, restore drill, SLO verification.

### 5.4 Cheapest unblocking action available today

`CHANNEL-TELEGRAM-001` needs a bot token and a reachable sandbox endpoint. Its contract is complete
and tested. A token from BotFather costs minutes and proves the full inbound → envelope → runtime →
draft → human approval → outbound → receipt path against a real provider, months before Zalo
verification returns. It is the only way to retire the "no provider has ever been on the other end"
class of risk early.

## 6. Honest timeline

| Milestone | Estimate | Binding constraint |
|---|---|---|
| Domain command surface usable by staff (§5.1) | 5–7 weeks | engineering only |
| `G1_INTERNAL_SHADOW_READY` | 5–7 months | `SHOP-INSTRUMENT-001` + `EVAL-CORPUS-001` + provider access, partly parallel |
| `G2_PUBLIC_ASSISTED_ENTRY` | 9–13 months | Zalo verification, public policy bundle, §5.2 in full |

The 2026-08-12 figure of 20–26 weeks to G2 assumed the missing pieces were the channel and the
corpus. It did not count 49 absent aggregates. **9–13 months is the defensible number**, and most of
the added time is §5.2 work that no external party gates — it gates on decisions the owner can make
in an afternoon.

## 7. The structural observation, updated

August's observation was that effort had gone into delivery machinery rather than the product. That
has begun to correct: this week wired the runtime, built the Shadow console, landed store scoping,
consent and the channel envelope, and made the test suite tell the truth about itself.

The sharper observation now is different. **The system is a governance layer wrapped around an empty
middle.** Both ends are excellent — the deterministic engines below, the audit/approval/outbox spine
above — and the connective tissue between them, the commands that write a quote, take a payment,
record a handover, dispatch a leg, is absent. That tissue is ordinary engineering. It is also, at
14/63, the majority of the specification.

The right next quarter is §5.1 in full, `DEC-010` and `DEC-011` opened, and one real channel token
obtained.

## 8. What would change this assessment

- `QUOTE-COMMAND-001` complete → the domain authority is reachable for the first time; domain
  persistence moves ~25% → ~40%.
- `EVAL-PUBLISH-001` complete → the gated corpus goes 0 → 669 of 1,300 in one change, and the first
  of the five layer minima is satisfied (669 ≥ 500).
- First PRIMARY provider eval run recorded → the evidence base leaves zero.
- `FINANCE-001` and `FULFILMENT-001` complete → an order can be truthfully described as finished.
- `DECISION-HOSTING-001` resolved → five infrastructure items unblock at once.

Re-run after §5.1, not after `AGENT-002`. §5.1 is what the previous re-run condition missed.
