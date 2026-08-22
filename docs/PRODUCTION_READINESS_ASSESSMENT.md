# Production Readiness Assessment

**Assessed:** 2026-08-18
**Commit assessed:** `1d5be44` (the tree on `main` after today's decision-ratification round)
**Supersedes:** the 2026-08-14 assessment of `8e5395d` (same path; see git history)
**Method:** direct codebase measurement, not documentation review
**Status of this document:** analysis. It is **not normative**, resolves no decision, and authorizes
nothing. `delivery/` remains the machine-readable truth.

## Verdict

The 2026-08-14 verdict was "the authority layer is production-grade and almost nothing in the running
system can reach it." Half of that is now false, and precisely half — which is itself informative,
because the half that changed is the half that was pure wiring, and the half that did not is the half
guarded by a deliberate fail-closed default.

~~The ten-operation Tool Facade is wired in production to a backend that returns `TOOL_UNAVAILABLE` for
every operation.~~ **Still true, verified again today** (`facade.py:152-153`) — but no longer for lack
of an implementation. `TOOL-BACKEND-001` built a real backend; the production factory function still
constructs `AgentFacadeService(UnavailableAgentToolBackend())` regardless, because the item's own
acceptance criteria required it to default to unavailable behind capability flags. This is now a
capability-authorization fact, not an engineering gap — the distinction matters for anyone estimating
distance to G1, because closing it is a flag flip guarded by evidence, not a code change.

~~The three facts that make a laundry order finished in the physical world — money received, goods
handed back, delivery run completed — are insert-time constants with no write path, with the provable
consequence that no order this system creates can ever reach `COMPLETED`.~~ **False as of
`SETTLEMENT-001`, for the exact-payment/self-collection case.** `packages/db/src/.../settlement.py:202`
commits an atomic `UPDATE orders SET balance_status = 'PAID', self_collection_recorded = TRUE ...
WHERE id = %s AND row_version = %s AND balance_status = 'UNPAID'` through `commit_material_change`, and
`packages/db/tests/test_settlement.py::test_an_order_reaches_completed_after_settlement` proves an
order reaches `COMPLETED` through every intermediate transition. §3 G2 is rewritten below rather than
struck through, because the finding didn't get weaker — it got narrower and more precise: every
settlement shape *other than* exact-payment-in-full-at-handover (partial payment, deposits,
`ON_ACCOUNT`) is `DEC-010`, and `DEC-010` is now `RESOLVED` as **deliberately** `NOT_SUPPORTED` — a
decided scope boundary, not an open gap.

Against the specification, the headline number **did not move**: still **14 of 63** aggregates in
`specs/DOMAIN_DATA_API_SPEC_V1.md` §4 exist as direct-fulfillment tables (§2.2). `order_settlements`
(new since 08-14) is a fifth narrow stand-in, not a sixth full one — it satisfies neither `payments` nor
`payment_allocations` as specified, because §4.12 explicitly requires "support partial/mixed payments"
and `order_settlements` deliberately does not, by `DEC-010`. Counting generously: **~19 of 63**, up one
stand-in from ~18.

The larger movement this cycle is not in the codebase at all — it is in `context/DECISION_REGISTRY.yaml`.
**11 of 14 registered decisions are now `RESOLVED`**, up from 6 of 12 on 08-14 (then: DEC-007, DEC-008
only were resolved of the eight then registered — corrected below, see the note on the 08-14 document's
own "8 open decisions" figure, which undercôunted). Three decision-shaped items remain: `DEC-006` (a
risk-acceptance *stance* is recorded, but registry status stays `OPEN` — it needs a verified provider
account setting and a legal check neither of which exists), `DEC-013` and `DEC-014` (both opened today
by the console rebuild, both fail closed, neither blocks anything yet), plus `DEC-HOSTING`, which was
never a registry entry and cannot be one — an agent may assemble its admissibility packet
(`docs/DECISION_REQUEST_HOSTING_2026-08.md`, drafted today, every cost/residency figure marked
`UNVERIFIED`) but may not select a vendor or accept terms.

That is a coherent thing to have happened — decisions are cheaper to close than code is to write, and
today closed nine of them in one sitting — but it changes almost nothing measured in this document,
because **none of §5.2's proposed items were actually enqueued.** A resolved decision that gates an
unenqueued item removes a future blocker, not a present one. §5 below states plainly what is now
unlocked versus what remains a proposal.

## 1. Corrections to the 2026-08-12 assessment

*(Historical — unchanged from the 08-14 revision, preserved for continuity.)*

| Old finding | Status |
|---|---|
| **F1** — the agent runtime is an orphan, 1,216 lines no code path reaches | **Partly fixed — this row was too generous, corrected 2026-08-14.** `AGENT-PIPELINE-001` built the composition and proved it: a job traverses queue → claim → runtime → persisted evidence, and claim exclusivity holds under a real two-worker race. But `build_agent_cycle` still has **no caller outside tests**. `apps/worker/.../main.py` constructs `WorkerSupervisor(settings)` with no cycle injected, so the deployed worker process cannot run the pipeline — setting `WORKER_AGENT_QUEUE_ENABLED` yields `AGENT_RUNTIME_UNAVAILABLE`, not a running agent. `AGENT-PIPELINE-001`'s own evidence says the module "is referenced by no process entry point", framed there as a rollback property. The runtime is assembled and testable; it is not reachable in a running system. Found by `DEMO-STACK-001`. **Reconfirmed 2026-08-18, byte-identical**: `main.py:19` still reads `effective_supervisor = supervisor or WorkerSupervisor(effective_settings)`, no `agent_cycle=` argument passed, though `WorkerSupervisor.__init__` (`host.py:79`) has accepted one since before 08-14. |
| **F5** — the staff console has no Shadow surface | **Fixed.** `SHADOW-CONSOLE-001` complete. `agent_drafts` persists the proposal (previously only a character count was kept, so there was literally nothing to review); approve/edit/reject is attributed and single-shot by unique constraint; the `UNKNOWN` reconciliation queue has exactly one exit and it requires a named human; RBAC and IDOR are proven at the repository, not the route. |
| **F2** — eval corpus at zero for every release-relevant layer | **Still true, with one correction.** The five gated layers total 1,300 required cases — frozen regression 200, normal language 300, adversarial 200, synthetic combinatorial 500, public corpus 100 — and every one of them stands at **0**. The 32 `SEED` cases count toward none of them. But 669 synthetic combinatorial cases *are* built, priced by the deterministic engine and content-hashed at `specs/evals/synthetic-combinatorial-v1.json`; the manifest inventory reads `0` only because publishing the count was reverted during the hash-pin freeze. Unchanged as of 08-18 — see §5. |
| **F3** — the model has never been invoked | **Still true, unchanged.** `evidence/agent-shadow/local-synthetic-suite-v2.json`: `status: SKIP`, `runtime_path: DETERMINISTIC_DEGRADED`, `primary_provider_evidence: false`, `release_effect: NONE`, five declared release blockers. |
| **F4** — test effort allocated away from the product | **Still true, and the gap widened.** `packages/evals` was 5,911/8,019 (source/test) on 08-14; it is unchanged at 5,911/8,958 on 08-18 (the test-line delta is unrelated churn, not new eval surface) — still larger than `packages/domain` + `packages/db` combined were on 08-14, and both of those grew too (see §2.1). |
| **F6** — no channel code exists | **Partly fixed, unchanged since 08-14.** `CHANNEL-ENVELOPE-001` and `CONSENT-STOP-001` are complete. No **adapter** exists — no provider is on the other end. `CHANNEL-TELEGRAM-001` remains the cheapest unblock (§5.4); `DEC-005` is now `RESOLVED` (Telegram for Shadow, Zalo OA for production) but the bot token and the Zalo verification artifact each still do not exist. |

The old document closed with "re-run this assessment after `AGENT-002`, not before." That was wrong
in one direction: the intermediate states did not change the verdict, but measuring against the
specification rather than against the previous document did.

## 1a. Corrections to the 2026-08-14 assessment

| Old finding | Status |
|---|---|
| **G2** — no order created by this system can ever be completed | **False for the exact-payment/self-collection case, `SETTLEMENT-001`.** See Verdict above and the rewritten §3 G2 below. |
| G1, quote half — the pricing/promotion/delivery/SLA engines are reachable only from test harnesses | **False.** `POST /internal/v1/stores/{store_id}/quotes` now exists (`QUOTE-COMMAND-001`, already noted as reachable in the 08-14 verdict but not yet reflected in the §3 G1 write-up, which still described the old repository-level fact as current). Corrected in §3 G1 below. |
| G1, Tool Facade half — `apps/public-agent-tools/.../facade.py:131` wires `UnavailableAgentToolBackend` unconditionally | **Unchanged in effect, changed in cause.** `TOOL-BACKEND-001` (complete) built a real backend; `get_agent_facade_service()` (now `facade.py:152-153`, the module gained lines) still constructs `UnavailableAgentToolBackend()`. The reachable behavior is identical; the reason is now "gated by design" rather than "not built." |
| "8 open decisions" (08-14 §2.1 headline count) | **Was already imprecise on 08-14** — the registry held 12 entries then (DEC-001–006, 008–012, minus gaps), of which DEC-007 and DEC-008 were `RESOLVED`; "8 open" undercounted by not naming which were which. As of 08-18 the registry holds 14 entries, 11 `RESOLVED`, 3 `OPEN` (`DEC-006`, `DEC-013`, `DEC-014`) — see Verdict and §2.1. |
| §4.12 Finance, 0/7 | **Still 0/7 by direct fulfillment.** `order_settlements` (new) is a fifth *narrow stand-in* (§2.2), not a sixth direct table — it does not support partial/mixed payment, which §4.12 requires of `payments`/`payment_allocations` and which `DEC-010` deliberately declined to build. |
| §5.2's `REMEDY-001` (gated on `DEC-004`), `CATALOG-PRICEBOOK-001` (gated on `DEC-001`), `FULFILMENT-001` (gated on `DEC-003`) | **All three decisions are now `RESOLVED`.** None of the three items has been enqueued. See §5.2. |

## 2. What measurement shows now

### 2.1 Size

| Area | Source LOC | Test LOC | Change since 08-14 |
|---|---|---|---|
| `packages/domain` | 3,922 | 2,086 | +557 / +397 |
| `packages/db` | 8,156 | 6,264 | +1,291 / +1,270 |
| `packages/contracts` | 2,168 | 864 | unchanged |
| `packages/evals` | 5,911 | 8,958 | +0 / +939 |
| `packages/policy` | 392 | 297 | unchanged |
| `packages/observability` | 651 | 261 | +11 / unchanged |
| `apps/api` | 4,070 | 4,019 | +1,881 / +3,110 |
| `apps/worker` | 3,348 | 2,908 | unchanged / +395 |
| `apps/public-agent-tools` | 1,392 | 1,187 | +976 / +941 |
| `apps/web` | 12,148 (JS/CSS/HTML) | — (browser-verified, not unit-tested) | see note |

**Correction to the 08-14 row, not organic growth:** the prior `apps/web` figure of 744 was measured
incorrectly or by a materially different method. A direct `find apps/web/src apps/web/styles -name
"*.js" -o -name "*.css" -o -name "*.html" \| xargs wc -l` today returns 12,148, and the console already
had roughly a dozen screens (`quotes.js`, `today.js`, `orders.js`, `shadow.js`, `manualSend.js`,
`incidents.js`, `assistant.js`, `orderDetail.js`, `orderRequests.js`, `gaps.js`, `exceptions.js`,
`staff.js`) before today's rebuild touched any of them further. Treat 12,148 as the first reliable
measurement of this area, not as 16× growth in four days.

27 forward-only migrations (`0001`–`0027`), **40 tables** (up from 38 on 08-14; new:
`retention_class_configurations`, `retention_legal_holds`, `retention_purge_runs`,
`order_settlements`, `assistant_turns`), **37 routes** under `/internal/v1` (up from 24, enumerated
from the built `app` object, not by grep — new since 08-14: `POST .../quotes`, `POST
.../orders/{id}/settlement`, `GET .../settlements/today`, `GET .../pricebook/services`, three
`.../assistant/turns` routes), 10 agent tool operations (unchanged), **972 passed, 1 skipped**
(platform-conditional, not evidence-relevant) — re-run fresh today with real PostgreSQL, up from 722
on 08-14. 77 queue items: **42 COMPLETE, 20 PENDING, 15 BLOCKED** (up from 70/35/20/15 — seven items
completed, none newly added). 13 capabilities, all `NOT_AUTHORIZED`, unchanged. **14 registered
decisions, 11 `RESOLVED`, 3 `OPEN`** (`DEC-006`, `DEC-013`, `DEC-014`), plus the unregistrable
`DEC-HOSTING` — see Verdict.

### 2.2 Specification coverage — the headline number, unchanged

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
| 4.12 Finance | 0/8 | `charges`, `payments`, `payment_allocations`, `refunds`, `invoice_requests`, `invoices`, `b2b_credit_terms`, `accounts_receivable_entries` |
| 4.13 Incidents, remedies, credits | 0/6 | `incidents`, `incident_events`, `evidence_assets`, `remedies`, `credit_grants`, `credit_ledger_entries` |
| 4.14 Approval, integration, audit | **9/9** | — (verified against the spec's own 9 named aggregates: `approval_requests`/`approval_decisions`, `webhook_events`, `outbox_events`/`delivery_attempts`/`dead_letter_events`, `agent_runs`/`agent_tool_calls`, `audit_events` — unchanged, no new table lands here) |

**Correction:** 4.12 is renumbered 0/8, not 0/7 — the spec names `b2b_credit_terms` as its own
sub-heading alongside `accounts_receivable_entries`, which the 08-14 table's missing-list omitted.
Still 0 either way.

Counting generously, **five** now have narrower stand-ins (up from four): `staff_users` for
`staff_members`, `customer_incidents` for a much smaller `incidents`, `order_requests` for the intake
draft, `webhook_events` + `channel_send_receipts` for part of `messages`, and — new since 08-14 —
`order_settlements` for a slice of `payments`/`payment_allocations`, covering only exact payment in
full at handover and explicitly not the partial/mixed case the spec asks for. That is **~19 of 63**.
`assistant_turns` and the three `retention_*` tables are deliberately **not** counted anywhere in this
table — they are real, tested, and outside the 63-aggregate list entirely (staff-internal memory and
data-lifecycle control, not a business-domain aggregate).

The shape is unchanged from 08-14. The governance context is complete (4.14, 9/9); the eight contexts
that describe running a laundry are still functionally empty, net of the stand-ins above.

## 3. Findings

### G1 — The deterministic authority now has a partial production write path

**Corrected from 08-14.** `QuoteRepository.create_revision` is now called in production:
`POST /internal/v1/stores/{store_id}/quotes` exists and is one of the 37 enumerated routes
(`QUOTE-COMMAND-001`, complete). The pricing, promotion, delivery and SLA engines are reachable by a
staff member through the console's Báo giá screen, not only from test harnesses. This half of the
08-14 finding is retired.

The Tool Facade half is **not** retired, but the reason changed. `get_agent_facade_service()`
(`apps/public-agent-tools/src/nha_trang_laundry_agent_tools/facade.py:152-153`) is the sole production
wiring:

```python
def get_agent_facade_service() -> AgentFacadeService:
    return AgentFacadeService(UnavailableAgentToolBackend())
```

All ten operations still return `TOOL_UNAVAILABLE` in production. But `TOOL-BACKEND-001` (complete)
built a real `AgentToolBackend` implementation over the deterministic domain — the queue item's own
acceptance language required it to default to unavailable, behind the existing capability flags. So
where 08-14 measured "the only implemented backend lives in `packages/evals` and serves exactly one
operation," 08-18 measures "a real backend exists, and is deliberately not wired as the default." This
is fail-closed and correct; it is also still the reason the agent path cannot serve a customer even
with every decision resolved, because flipping it requires a capability authorization this document
does not have the standing to grant.

### G2 — No order created by this system can reach `COMPLETED` **except by exact payment and self-collection, which now works**

**Rewritten, not struck through — the finding narrowed rather than disappeared.**

**The completion rule, unchanged.** `transition_commercial` (`packages/domain/src/.../orders.py:106-114`)
requires `ACTIVE`, `production = RELEASED`, `required_delivery_legs_succeeded OR
self_collection_recorded`, and `balance IN {PAID, ON_ACCOUNT}`. Migration
`0007_operations_control.sql:245-250` repeats all four as a table CHECK.

**The new write path.** `SettlementRepository` (`packages/db/src/nha_trang_laundry_db/settlement.py`,
backing migration `0025_order_settlement.sql`, new table `order_settlements`) supplies the missing
proof for exactly the case the domain permits without a delivery leg:

```sql
UPDATE orders
SET balance_status = 'PAID', self_collection_recorded = TRUE, ...
WHERE id = %s AND row_version = %s AND balance_status = 'UNPAID'
```

committed atomically through `commit_material_change` (`settlement.py:202`) alongside the settlement
row, its domain event, audit entry and outbox event. The `WHERE ... balance_status = 'UNPAID'` clause
makes the write idempotent under the row-version compare-and-swap: recording the same settlement twice
produces one row, not two.
`packages/db/tests/test_settlement.py::test_an_order_reaches_completed_after_settlement` walks an order
from creation through every intermediate transition to `COMPLETED` and is the empirical proof this
finding relies on, not the SQL alone.

**What is still true, deliberately.** Every settlement shape other than exact-payment-in-full at
handover — partial payment, deposits, instalments, `ON_ACCOUNT` — is `DEC-010`, and `DEC-010` is now
`RESOLVED` as **deliberately deferred**: those shapes stay `NOT_SUPPORTED` by decision, not by a gap
nobody noticed. There is no B2B credit relationship operating yet, so nothing today is blocked by this.
`PaymentStatus` and `DeliveryLegStatus` still exist in the canonical enum registry
(`packages/domain/src/.../catalog.py`) and are still persisted nowhere — `order_settlements` proves
one binary fact (paid in full, collected in person), not a payment ledger. `delivery_attempts` remains
the outbox's message-delivery ledger, unrelated to a motorbike; a delivered-leg-completes-order path
still does not exist and is gated by `DEC-003`'s further engineering (`FULFILMENT-001`, unenqueued —
see §5.2). `incident_open` is likewise still insert-only.

### G3 — There is no customer, no address, no shop, no price list

**Unchanged since 08-14** — verified again today: no `stores`, `parties`, `pricebooks`, or
`organizations` table exists in any of the 27 migrations (`grep -l "CREATE TABLE stores\|CREATE TABLE
parties\|CREATE TABLE pricebooks\|CREATE TABLE organizations" packages/db/migrations/*.sql` returns
nothing). `store_id UUID NOT NULL` is still an unvalidated UUID with no foreign key anywhere.
`bound_contact_id` still references nothing; `contact_channel_bindings` still holds no name, phone, or
order history. The delivery engine still takes `verified_distance_m`, an integer a staff member
supplies.

The two readings from 08-14 still both apply — correct-and-deliberate for shadow-phase design,
unbuilt-not-descoped against the specification — and the decision about which reading stands is still
absent from `context/DECISION_REGISTRY.yaml`. This is worth naming precisely: **it is not currently
any of DEC-001 through DEC-014.** If a rider is ever dispatched against a `stores`/`parties`/`addresses`
layer, that is a new, currently-unregistered decision — not something today's ratification round
touched.

**Sharper than 08-14 stated it: the system can record zero customers from any source today, and
`DEC-013` is the reason, not a UI gap.** `CreateOrderCommand.bound_contact_id`
(`packages/db/src/nha_trang_laundry_db/orders.py:43`) is required and non-nullable. The only writer of
`contact_channel_bindings` is `ChannelBindingRepository.resolve_or_create`
(`packages/db/src/nha_trang_laundry_db/channel.py:74`), keyed on a provider identity, and the
`provider` CHECK constraint (migration `0020`) admits only `ZALO_OA`, `TELEGRAM_SANDBOX`,
`FACEBOOK_MESSENGER` — none of which is connected (verified §5.4: `CHANNEL-TELEGRAM-001` still needs a
bot token). So a customer who messages the shop has no channel to message it through, and a walk-in
customer who never messaged at all has no path to a binding regardless — which is exactly what
`DEC-013` (open, `docs/DECISION_REQUEST_WALKIN_IDENTITY_2026-08.md`) is about. Read together with G1's
production-write-path finding, the order pipeline is reachable end to end (create → quote → settle →
`COMPLETED`) but has no legitimate way to acquire the `bound_contact_id` it requires as its first
argument, from either a connected channel or a counter walk-in. This raises `DEC-013` from "a console
UX question" to the binding constraint on whether this system can serve its first real customer at
all, independent of every gate and every other open decision.

### G4 — The price list is still not data

**Unchanged since 08-14.** `import_pricebook_csv` still parses an owner-confirmed CSV into an
in-process `CanonicalPricebook`. No `pricebooks`, `pricebook_versions`, `price_rules` or `price_tiers`
table exists (confirmed in the same grep as G3). Changing a price is still a file change and a
redeploy. `quote_revisions` still stores an immutable JCS-SHA256 snapshot hash — the integrity half of
§4.6 remains genuinely solved; the publication half does not.

Newly relevant: `DEC-001` (weight precision and rounding, which the 08-14 doc's §5.2 identified as
touching every stored price) is now `RESOLVED` (no rounding, exact scale reading). That removes the
decision-gate on `CATALOG-PRICEBOOK-001` — it does not build the table. See §5.2.

### G5 — Items marked `BLOCKED` by conditions their own text says have lifted, plus one newly stale field

The queue-staleness pattern from 08-14 persists, and today's decision round added a new instance of it
— not in `blocking_condition` prose this time, but in the `blocked_by_decisions` list itself.

| Item | Its own text now says |
|---|---|
| `EVAL-SYNTHETIC-COMBINATORIAL-001` | Unchanged since 08-14 — still `BLOCKED`, still says the sequencing conflict is resolved and the 500 cases are built, frozen, and re-priced by `verify_contracts.py` on every run (confirmed again today: `verify_contracts.py` output still reports 669 synthetic combinatorial cases). |
| `SIGNER-REGISTRY-001` | Unchanged since 08-14 — still `BLOCKED`, `blocking_condition` text re-read verbatim today, still says only the owner key ceremony remains. |
| `MODEL-ROUTE-001` | Unchanged since 08-14 — still `BLOCKED`, `blocking_condition` text re-read verbatim today, still says only ADR-0008 acceptance remains. |
| `MULTIMODAL-PERCEPTION-001` — **new finding, 08-18** | `blocked_by_decisions: [DEC-009, DEC-006, DEC-008]` in `delivery/WORK_QUEUE.yaml` (line 1642-1645). **Two of the three are now `RESOLVED`** (`DEC-009` — staff-supplied media only; `DEC-008` — retention schedule). Only `DEC-006` is still genuinely `OPEN`. This field is machine truth and this document does not edit it (see header), but a reader relying on it without cross-checking the registry will overcount this item's blockers by two. |

`EVAL-SYNTHETIC-COMBINATORIAL-001` still matters most for the same reason as 08-14: 500 of 1,300
required eval cases, blocking `EVAL-CORPUS-001`, which blocks `AGENT-002`, which carries all G1 agent
evidence.

## 4. Distance by layer

Percentages are engineering judgement, not measurement. Columns are shown side by side to make
movement visible.

| Layer | 08-12 | 08-14 | 08-18 | Why it moved, or why it did not |
|---|---|---|---|---|
| Deterministic engines (pricing, promotion, delivery, SLA) | ~90% | ~90% | ~90% | Unchanged and genuinely strong. |
| Domain persistence and command surface | *(n/a)* | ~30% | **~40%** | The quote write path (08-14) plus the settlement write path (08-18) are the first two commands that produce a durable monetary/fulfilment artefact. Still 14/63 direct aggregates — the movement is in *reachability* of what exists, not in aggregate count. |
| Control plane / internal API | ~75% | ~80% | **~85%** | 37 routes (up from 24); create-quote and settlement are wired; console rebuilt around the owner's actual morning workflow. Still no payment-ledger, custody, or delivery-leg command. |
| Agent product path | ~25% | ~45% | ~45% | Unchanged — pipeline still not reachable from the deployed worker process (G1), facade backend still `Unavailable` by design, no channel adapter landed. |
| Evidence base | ~2% | ~5% | ~5% | Unchanged — 669 combinatorial cases still unpublished, still 0 provider runs, all five gated layers still 0/1,300. |
| Production infrastructure | ~15% | ~15% | ~15% | Unchanged — no host, no monitoring, no backup, no restore drill. All still behind `DEC-HOSTING`, for which an admissibility *framework* now exists (`docs/DECISION_REQUEST_HOSTING_2026-08.md`) but zero figures are verified. |
| Business readiness | ~10% | ~10% | **~35%** | The largest single-day movement in this document's history. 11 of 14 decisions resolved today, including all four decisions the 08-14 assessment's §5.2 identified as the gate on `REMEDY-001`, `CATALOG-PRICEBOOK-001`, and `FULFILMENT-001`. This is a decisions-cleared number, not a shipped-feature number — see the caveat in §5. |

**To `G1_INTERNAL_SHADOW_READY`: ~35%** (up from ~30%, driven by control-plane and business-readiness
movement; G1's own evidence gates — PITR, incident/kill-switch drills, `SHOP-INSTRUMENT-001`,
`EVAL-CORPUS-001` — are all still at their 08-14 state). **To `G2_PUBLIC_ASSISTED_ENTRY`: ~22%** (up
slightly from ~20% — G3 and G4, which sit on the G2 path, are both unchanged).

## 5. What "a full-featured core for real-world deploy" requires

Grouped by what actually gates each one. Nothing below is enqueued — proposing work is analysis,
adding it to `delivery/WORK_QUEUE.yaml` is a change to machine truth and needs the owner's word. This
section changed more than any other since 08-14, because decisions closed today remove gates from
three of the six §5.2 rows without building anything.

### 5.1 Buildable now, no decision required

| Proposed item | Status since 08-14 |
|---|---|
| `QUOTE-COMMAND-001` | **Built.** Complete in the queue, verified as the new `POST .../quotes` route. |
| `EVAL-PUBLISH-001` | Still proposed, still not enqueued. Unblocks 500 of 1,300 cases in one change — the fastest remaining move on the evidence base. |
| `SIGNER-VERIFIER-LAND-001` | Still proposed, still not enqueued. |
| `TOOL-BACKEND-001` | **Built**, and its own acceptance criteria intentionally kept it wired to `Unavailable` — see G1. |

### 5.2 Buildable once one decision lands — three of six are now decision-clear

| Proposed item | Gate as of 08-14 | Gate as of 08-18 |
|---|---|---|
| `FULFILMENT-001` — `delivery_bundles`, `delivery_legs`, `distance_measurements`, the write path that flips `required_delivery_legs_succeeded` | `DEC-003` | **`DEC-003` is `RESOLVED`** (staff-negotiated >6km ratified as policy). Decision-clear; still not enqueued. |
| `FINANCE-001` — `charges`, `payments`, `payment_allocations`, a real `balance_status` transition beyond exact payment | recommended opening `DEC-010` | **`DEC-010` is `RESOLVED`** — and resolved as *deliberately deferred*. This item is now explicitly **not** decision-clear in the direction that would build it; the decision says wait, not proceed. `SETTLEMENT-001` already covers the case that mattered before a B2B credit relationship exists. |
| `CUSTODY-001` — `custody_units`, `custody_events`, `batches` | `SHOP-INSTRUMENT-001` | Unchanged — still gated by the 4–6 week physical measurement, not yet started. |
| `REMEDY-001` — `remedies`, `credit_grants`, `credit_ledger_entries` | `DEC-004` | **`DEC-004` is `RESOLVED`** (7-day rewash window, 5× cleaning-fee compensation cap, 100,000đ staff-approval ceiling; loss policy as distinct from damage explicitly still unresolved). Decision-clear for the damage/rewash/compensation case; still not enqueued; the loss-policy carve-out means `REMEDY-001` as originally scoped would need to explicitly exclude loss or the decision extended first. |
| `CATALOG-PRICEBOOK-001` — persist and version the price list; `stores`, `organizations` | `DEC-001` | **`DEC-001` is `RESOLVED`** (no rounding). Decision-clear; still not enqueued. |
| `PARTY-001` — decide whether a CRM exists at all | recommended opening `DEC-011` | `DEC-011` was in fact opened and resolved — **but for a different question** (staff identity provider, not customer CRM). `PARTY-001`'s actual gate — whether a customer-record layer exists at all — remains genuinely unregistered. Do not read `DEC-011`'s resolution as touching this row. |

**The caveat this section exists to state plainly:** three items are now buildable-once-decided in the
purest sense — the decision text exists, is signed, and names no missing fact. None of the three has
been enqueued. Business-readiness's jump to ~35% in §4 reflects decisions cleared, not code shipped;
do not read it as schedule compression until these are actually queue items with task packets.

### 5.3 Calendar-bound, no code shortens them — unchanged since 08-14

- `SHOP-INSTRUMENT-001` — 4–6 weeks of real measurement, not started. Without it `SHADOW-001` has no
  denominator. Its measurement templates (`templates/delivery-cost-log.csv`,
  `templates/capacity-cycle-log.csv`) exist and are ready; `templates/machine-master.csv` is already
  populated with the shop's four machines.
- `CHANNEL-ZALO-APPLY-001` — 2–8 weeks of external OA verification, not started. `DEC-005` is now
  `RESOLVED` (Telegram-Shadow, Zalo-OA-production), which sequences this correctly but does not start
  the clock — that needs the owner's own Zalo Business account.
- `CORPUS-CONSENT-001` — lawful basis for using message history; gates 300 eval cases. Unchanged.
- `PROVIDER-ACCESS-001` + `DEC-006` — `DEC-006` now has a recorded stance
  (`PROCEED_TOWARD_VERIFICATION`) but is still registry `OPEN`; the evidence base stays at zero until
  an actual dedicated credential and legal check land.
- `DEC-HOSTING` — gates deploy target, monitoring, backup, restore drill, SLO verification. An
  admissibility framework now exists (§5.4 below is unaffected; see the Verdict) but every commercial
  figure is `UNVERIFIED`.

### 5.4 Cheapest unblocking action available today — unchanged

`CHANNEL-TELEGRAM-001` still needs only a bot token and a reachable sandbox endpoint. Its contract is
complete and tested. `DEC-005`'s resolution today makes this the *sequenced first step* of the official
channel decision rather than a standalone sandbox exercise — proving the full inbound → envelope →
runtime → draft → human approval → outbound → receipt path against a real provider now has a decided
purpose, not just a nice-to-have.

## 6. Honest timeline

| Milestone | 08-14 estimate | 08-18 estimate | Why |
|---|---|---|---|
| Domain command surface usable by staff (§5.1) | 5–7 weeks | **Substantially delivered** | `QUOTE-COMMAND-001` and `SETTLEMENT-001` both landed; remaining §5.1 items (`EVAL-PUBLISH-001`, `SIGNER-VERIFIER-LAND-001`) are evidence/release plumbing, not staff-facing command surface. |
| `G1_INTERNAL_SHADOW_READY` | 5–7 months | **5–7 months, unchanged** | The binding constraints — `SHOP-INSTRUMENT-001`, `EVAL-CORPUS-001`, provider access — are all calendar- or evidence-bound and none moved today. Decisions closing does not compress a 4–6 week physical measurement or a 0/1,300 eval corpus. |
| `G2_PUBLIC_ASSISTED_ENTRY` | 9–13 months | **9–13 months, unchanged** | Same reasoning — Zalo verification (2–8 weeks, not started), the public policy bundle, and §5.2's now-decision-clear-but-unbuilt items all still gate on calendar time or enqueued engineering, neither of which today's session changed. |

The honest statement this cycle is that **a large, real amount of decision work happened today and it
does not show up in the timeline**, because none of the timeline's binding constraints are decisions
anymore except `DEC-006` and `DEC-HOSTING`. The remaining distance is measurement weeks, verification
weeks, and engineering that has not been asked for yet.

## 7. The structural observation, updated

08-14's observation was that the system is a governance layer wrapped around an empty middle, with the
connective tissue — commands that write a quote, take a payment, record a handover — mostly absent.
Two of those commands now exist: quote and exact-payment settlement. The observation should narrow to
match: **the connective tissue for the single most common transaction (walk-in, pay in full, collect
in person) is built and tested. The connective tissue for every other transaction shape — partial
payment, delivery legs, custody handoff, remedies — is still absent, and three of those four are now
explicitly decision-clear rather than decision-blocked.**

The newer observation is about decision velocity versus engineering velocity. Nine decisions closed in
one working session; two engineering commands took roughly a week each. That ratio will invert as the
project moves further from "the owner has an opinion" work and further into "someone has to write and
test the code" work — §5.2's now-unblocked-but-unbuilt items are exactly that inversion point.

## 8. What would change this assessment

- `EVAL-PUBLISH-001` complete → the gated corpus goes 0 → 669 of 1,300 in one change, and the first of
  the five layer minima is satisfied (669 ≥ 500). Still the single fastest evidence-base move
  available.
- Any of `FULFILMENT-001`, `REMEDY-001`, `CATALOG-PRICEBOOK-001` actually enqueued and built → moves
  the §2.2 aggregate count for the first time since this document started measuring it, because all
  three are now decision-clear rather than decision-blocked.
- `SHOP-INSTRUMENT-001` measurement started → the calendar clock for G1 actually begins; right now it
  has not, regardless of how many decisions are resolved.
- `DEC-006` resolved with a verified provider credential → the evidence base leaves zero for the first
  time.
- `DEC-HOSTING` resolved with a named, verified candidate → `DEPLOY-TARGET-001` and four other
  infrastructure items unblock at once.

Re-run after §5.1's remaining two items or after any §5.2 item is actually enqueued — not on a fixed
calendar. Decisions resolving without corresponding queue entries, as happened today, will not move
this document's numbers again.

---

## 9. Corrections to this document — measured 2026-08-22

Four of this assessment's findings are stale or misprescribed, found while planning work against it.
They are appended rather than edited in place, per this document's own convention. Three of them
would have misdirected engineering effort, which is the reason for writing them down rather than
quietly building the right thing.

Measured on `main` at `2752dcf` plus this session's branch. Queue is now **83 items: 50 COMPLETE, 18
PENDING, 15 BLOCKED**; registry **21 decisions, 11 RESOLVED, 10 OPEN**.

| Finding as written | Correction |
|---|---|
| §1 F2 and §5.1 — "the five gated layers total 1,300 required cases (…) every one of them stands at **0**", and `EVAL-PUBLISH-001` is "still proposed, still not enqueued (…) the fastest remaining move on the evidence base" | **Both stale. The count was published.** `specs/evals/eval-manifest-v1.yaml` reads `SYNTHETIC_COMBINATORIAL: 669`, and `verify_contracts.py:200-243` checks the declared inventory against the corpus and the corpus against the domain engines on every run. The gated corpus is **669 of 1,300**, not 0, and the largest single minimum (500) is already satisfied. `EVAL-PUBLISH-001` is not available work — `EVAL-SYNTHETIC-COMBINATORIAL-001` did it. The four layers still at zero are `FROZEN_REGRESSION` (200), `ADVERSARIAL` (200), `NORMAL_LANGUAGE` (300) and `PUBLIC_CORPUS` (100). **All four are queue-gated**: the first three are carried by `EVAL-CORPUS-001` and `EVAL-LANGUAGE-CORPUS-001`, both of which depend on `CORPUS-CONSENT-001` (BLOCKED, legal basis); the fourth is carried by `EVAL-PUBLIC-CORPUS-001`, which depends on `PUBLIC-POLICY-001`. Worth noting for sequencing rather than acting on unilaterally: the *content* of the adversarial layer — prompt injection, cross-contact IDOR, approval and hash tamper, consent bypass, tool-schema abuse, resource exhaustion — tests deterministic guardrails and has no intrinsic need for consented customer messages, unlike the normal-language layer sharing its carrier. Splitting it out would be a queue change and the owner's call, not an assessment's. |
| §3 G4 — "Changing a price is still a file change and a redeploy" | **False on the redeploy half.** `OperationsService._resolve_published_pricebook` (`operations.py:561-575`) calls `ConfigurationRepository.latest_published(cursor, "PRICEBOOK")` **per request**, verifies the stored payload against the digest recorded at publication, and refuses rather than degrades on any mismatch. `scripts/publish_pricebook.py` publishes a new version against a running database, attributed to a named staff member. Changing a price is a CSV and one command; no process restarts. What remains true is narrower and should be stated that way: the normalized §4.6 aggregates (`pricebooks`, `pricebook_versions`, `price_rules`, `price_tiers`) still do not exist, publication is a CLI with no console screen, and the CSV lives in the repository by default. **`CATALOG-PRICEBOOK-001` as scoped in §5.2 is largely already delivered** — by `CONFIG-001`, `DOMAIN-002` and `QUOTE-COMMAND-001` — and should not be enqueued as written. |
| §1 F1 — "the runtime is assembled and testable; it is not reachable in a running system", read as a wiring gap to close | **The observation holds; the prescription is wrong.** `main.py:19` does still construct `WorkerSupervisor(effective_settings)` with no `agent_cycle=`. But `build_agent_pipeline` (`pipeline.py:166-170`) raises `PipelineConfigurationError` for any `provider_backed` transport, so the only cycle constructible today carries `ScriptedResponsesTransport`. Wiring the deployed worker now would put a script-replaying transport into a production entry point and let `WORKER_AGENT_QUEUE_ENABLED=true` report a running agent that never calls a model — the substitution `AGENTS.md` forbids. `AGENT_RUNTIME_UNAVAILABLE` is therefore **correct fail-closed behaviour, not a defect**. The fix for F1 is `PROVIDER-TRANSPORT-001`, which is blocked on `PROVIDER-ACCESS-001`, which is blocked on a credential and `DEC-006`. |
| §3 G3 — "the order pipeline is reachable end to end (create → quote → settle → `COMPLETED`) but has no legitimate way to acquire the `bound_contact_id`" | **False in its first clause, and the reason is worse than the one given.** `DEC-021` (opened 2026-08-21, `docs/DECISION_REQUEST_QUOTE_APPROVAL_2026-08.md`) establishes that **no command path can produce a quote revision order creation will accept** — `compose_quote_revision` hardcodes `ESTIMATE`/`REVIEW_REQUIRED`/`approval_id=None` on every path, and a trigger forbids promoting a revision afterwards. So the pipeline is not reachable end to end for a *second*, independent reason, and the customer-identity gap is not the binding constraint it is described as here; it is one of two. |

### One repair landed against these findings

`QUOTE-APPROVAL-INTEGRITY-001` (complete, this session, migration `0029`). It is the part of
`DEC-021` that is not a policy question, and the decision packet identifies it as such. Before it,
`quote_revisions.approval_id` carried no foreign key, and the shipped eval fixture generator
`synthetic_incidents.py` — reachable from a CLI that connects to whatever `DATABASE_URL` names —
wrote an order at `COMPLETED` citing an approval envelope that had never been requested. Measured
before the change: 1 `APPROVED_EXACT` revision, 1 completed order, **0 rows in
`approval_requests`**. Measured after: 0 orphan references, and the migration itself refuses to apply
to a database holding one.

It decides nothing. `DEC-021` remains `OPEN`, no producer of `APPROVED_EXACT` was added, and
`POST /internal/v1/stores/{store_id}/orders` still returns 409 for every quote this system can
produce.

### What this changes about §5 and §8

- §8's first bullet — "`EVAL-PUBLISH-001` complete → the gated corpus goes 0 → 669" — has already
  happened and should be struck. No equivalent unblocked move replaces it: every remaining
  layer is carried by an item that depends on `CORPUS-CONSENT-001` or `PUBLIC-POLICY-001`. The
  evidence base's next movement is an owner action, not an engineering one.
- §5.2's `CATALOG-PRICEBOOK-001` row should be rescoped to what is actually missing (a console
  publication surface, and the normalized aggregates if they are wanted at all) rather than
  enqueued as "persist and version the price list", which is done.
- Neither correction moves the G1 or G2 timelines. Both binding constraints — the 4–6 week shop
  measurement and a provider credential — are exactly where §6 left them, and neither has started.
