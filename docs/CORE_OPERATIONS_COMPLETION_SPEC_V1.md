# Core operations completion — engineering specification V1

**Date:** 2026-09-18
**Author:** lead engineering, under the delegated authority recorded in `context/PROJECT_CONTINUATION.md`
**Status:** specification. Nothing here resolves a `DEC-*`; §7 says so item by item.

---

## 0. What this specification does, and does not do

It takes the six capabilities named as unbuilt in `docs/CORE_BUSINESS_WORKFLOWS_V1.md` §9 and in the
2026-09-18 Vietnamese operations summary, tests each one against `context/DECISION_REGISTRY.yaml`,
and produces either a build specification or a refusal with the decision that causes it.

It does **not** resolve any business policy. Four of the six are already decided by the owner and
merely unbuilt; two are decided *against* and are specified here as refusals so that the refusal is
visible rather than implied.

The distinction this document is built on comes from `DECISION_REQUEST_PRICING_POLICY_2026-08.md` §0:

> Signing this document is not "publishing configuration." […] each answer still has to be expressed
> as versioned config the pricing/promotion/delivery/incident engines read — that is a separate,
> small engineering item after signature, not before.

That separate item is what most of this specification is.

---

## 1. Method

Every gap was tested against three questions, in order:

1. **Is there a ratified decision?** `context/DECISION_REGISTRY.yaml` is normative.
2. **If yes, is the decision expressed as configuration the engine reads?** A ratified decision that
   lives only in a YAML register and a Markdown packet does not change behaviour.
3. **If the decision is "not yet", is its own reopening condition met?** Two decisions name theirs
   explicitly. Neither condition holds today.

A gap that fails (1) is a decision request, not an engineering task. A gap that passes (1) and fails
(2) is engineering. A gap that fails (3) is a refusal.

---

## 2. Verdicts

| # | Capability | Governing decision | Verdict |
|---|---|---|---|
| 1 | Quoting a range-priced service | none — deferred *scope*, not policy | **BUILD** — `RANGE-PRICE-001` |
| 2 | Rewash, damage compensation, credit | `DEC-004` RESOLVED with owner figures | **BUILD** — `REMEDY-001` |
| 3 | Promotions in the quote path | `DEC-002` RESOLVED (`accepted_at`) | **BUILD** — `PROMO-WIRING-001` |
| 4 | SLA board, day dashboard, export | none; invariant 18 governs | **BUILD** — `OPS-BOARD-001` |
| 5 | Customer records: name, phone, address | `DEC-015` RESOLVED — *chưa xây* | **REFUSE** — reopening condition unmet |
| 6 | Deposits, instalments, part payment | `DEC-010` RESOLVED — deferred | **REFUSE** — `NOT_SUPPORTED` by decision |

A seventh was checked and found already built: `EXACT_PAYMENT_PREPAID_DELIVERY`, the second
settlement shape `DEC-023` added, is present at `settlement.py:49`. It is not a gap.

---

## 3. `RANGE-PRICE-001` — a price band a staff member may close

### 3.1 Why this is first

Twenty of the forty-four published services carry a price band rather than a rate, and
`quote_composition.py:466` refuses to compose a revision for any of them:

```python
if result.requires_human or result.finality is not QuoteFinality.ESTIMATE:
    reasons.add(ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value)
```

Every one of those twenty is a garment or a job priced by inspection, and they are where the margin
is. Áo dài truyền thống is 80.000–240.000 ₫. Áo lông thú is 200.000–400.000 ₫. Vệ sinh sofa is
300.000–500.000 ₫. Giày da lộn cao cấp is 150.000–250.000 ₫. The shop can quote a 20.000 ₫/kg wash
through this system and cannot quote any of them.

The refusal was correct when it was written. `quote_composition.py:459` says so plainly — composing a
range revision "would mean deciding how a range interacts with totals, orders and settlement," and
that was not that item's scope. This item is that scope.

### 3.2 This is not a policy decision

Who may choose a number inside a published band is already answered twice over.

The band itself is the owner's authorisation. `min_price_vnd` and `max_price_vnd` are published
pricebook columns under an immutable version (invariant 4). Publishing 80.000–240.000 ₫ *is* the
owner saying every number in that interval is an acceptable price for that garment. Choosing inside
it exercises an authority already granted; it does not create one.

`DEC-021` supplies the mechanism for recording the choice: a quote is final when a named staff member
attests that the customer agreed to the price read aloud, recorded as an attributed, immutable
attestation. A staff-chosen in-band price is exactly a price read aloud.

### 3.3 Required design

**The server owns the bound, the human owns the number.** Staff submit one exact integer VND amount
per range-priced line. The server refuses anything outside `[min_price_vnd, max_price_vnd]` of the
line's published pricebook version, and refuses a band whose service is not range-priced. Invariant 3
holds: deterministic code decides what is permissible, a human supplies a fact inside it.

**The approval is the one already in the table.** `ApprovalAction.SET_RANGE_PRICE` exists, maps to
`_OWNER_FINANCIAL` and to resource type `QUOTE_REVISION` (`approvals.py:103`, `:119`). Use it
unchanged. Do not retune the policy table: who may approve a financial action is policy, and §7.3
raises the operational cost of this particular mapping as a decision request rather than deciding it
here.

**The chosen amount binds a rendered hash.** Invariant 8. The approval envelope binds the exact
revision and its rendered content; editing any line invalidates it. This is what stops a price
approved for one garment from being reused for another.

**The refusal stays for what is still unresolved.** `RANGE_PRICE_REQUIRES_HUMAN` does not disappear.
It is emitted when a range line has no approved amount yet — which is the truth, and is what makes the
console able to say *cần nhân viên chốt giá trong khoảng* instead of showing nothing.

**Finality becomes real.** A revision all of whose range lines carry an approved in-band amount is
`QuoteFinality.APPROVED_EXACT`, not `ESTIMATE`. `QuoteFinality.RANGE` remains for a revision
presented to a customer as a band before any amount is chosen, which is a legitimate thing to show
and today cannot be persisted at all.

### 3.4 Refusals this must emit

| Condition | Code |
|---|---|
| amount below `min_price_vnd` or above `max_price_vnd` | `RANGE_PRICE_OUT_OF_BAND` |
| amount supplied for a service that is not range-priced | `RANGE_PRICE_NOT_APPLICABLE` |
| range line with no approved amount | `RANGE_PRICE_REQUIRES_HUMAN` (existing) |
| approval envelope does not bind this revision's `rendered_hash` | existing envelope refusal |

Each new code is registered with the `DEC-*` or invariant that causes it, following the precedent of
`REFUSAL_DECISIONS` in `settlement.py:69`, whose completeness is pinned by
`test_settlement_policy.py:103`.

### 3.5 Console

`#/quotes` already renders a band: `format.moneyRange(min, max)` and
`components.priceStateBadge(finality)` exist. What it cannot do is let staff close one. Add, per
range line: the published band shown as the bound, one money input validated against it client-side
*and* server-side, and the resulting total. Out-of-band entry is refused by the server and rendered
through the existing `reasonCodeList` path so staff see the code and the reason in Vietnamese.

The `pricingCliffNotice` precedent applies: warn before submitting, not after refusing.

### 3.6 Done when

- A quote containing `DC_AO_DAI_TRADITIONAL` with an approved 150.000 ₫ persists, presents a total,
  reaches `ACCEPTED_FINAL`, converts to an order, and settles through the existing exact-payment path.
- The same quote with 250.000 ₫ is refused `RANGE_PRICE_OUT_OF_BAND` and nothing is persisted.
- A range line with no amount still yields `UnresolvedQuote` carrying `RANGE_PRICE_REQUIRES_HUMAN`.
- Editing a line after approval invalidates the approval (invariant 8).
- No arithmetic on money appears in the route layer.

---

## 4. `REMEDY-001` — DEC-004 expressed as configuration and a surface

### 4.1 Why

`DEC-004` is RESOLVED with figures the owner supplied on 2026-08-18, and **no code reads any of them**.
A grep for refund, credit, compensation, đền bù or giảm giá finds a `REMEDY_PROPOSAL` resource type,
an `APPROVE_REMEDY` approval action, an `AdjustmentDirection.CREDIT` enum member — and no
implementation behind any of them.

The complaint arrives, `INCIDENT-INTAKE-001` now records it, and then the thread stops: an incident
cannot lead to any outcome. The shop resolves it verbally and writes nothing down.

### 4.2 The ratified figures

Verbatim from the register and `DECISION_REQUEST_PRICING_POLICY_2026-08.md` §5:

- Report window for visible defects: **24 hours** after receiving goods.
- Store proposes an initial resolution within **24 hours** of a report.
- Late delivery >2 hours by store fault: **10% credit on the next bill**.
- Free rewash: requestable within **7 days** of pickup, when staff determines store fault.
- Damage compensation: capped at **5× that item's cleaning fee** — what the store charged, not
  retail or replacement value.
- Staff may approve compensation up to **100.000 ₫**; above that the **owner** must approve.

### 4.3 Loss is not damage, and must refuse

The decision packet is explicit:

> **Loss policy:** not covered by the figures above […] Treat loss as **not yet resolved** even though
> damage now is; if a loss case reaches the 5×/100.000đ figures above by analogy, confirm that reading
> with the owner before relying on it, since it was not explicitly asked.

So `REMEDY_KIND = LOST_ITEM` is accepted as a *record* and returns `REQUIRE_HUMAN` with
`LOSS_POLICY_UNRESOLVED`. It must not inherit the damage ceiling. This is "unknown means stop" applied
to the one sub-case the owner did not answer, and it is the single most likely place for this item to
go wrong by being helpful.

### 4.4 Money direction, under invariant 2

Invariant 2 is *money is non-negative integer VND*. A remedy is money owed **to** the customer, and
must not be modelled as a negative settlement.

A remedy proposal therefore carries a non-negative `amount_vnd` and an explicit
`AdjustmentDirection.CREDIT`. The enum member already exists (`catalog.py:72`). The settlement ledger
is untouched: an approved remedy does not rewrite a settled order, because the ledger is append-only
and `reject_ledger_mutation()` would refuse it anyway. It creates a separate, forward-looking
obligation.

### 4.5 The four remedy kinds

| Kind | Ceiling | Authority | Window |
|---|---|---|---|
| `FREE_REWASH` | none (no money moves) | staff attestation of store fault | 7 days from pickup |
| `DAMAGE_COMPENSATION` | 5× that line's cleaning fee | ≤100.000 ₫ staff; above, `APPROVE_REMEDY` | 24h report window |
| `LATE_DELIVERY_CREDIT` | 10% of the order total | staff attestation; server computes the 10% | store fault, >2h late |
| `LOST_ITEM` | **unresolved** | `REQUIRE_HUMAN`, always | — |

The server computes every ceiling from published data. Staff never type a cap. `5×` is computed from
the order line's own priced amount; `10%` from the order's settled total. A proposal exceeding its
computed ceiling is refused, not truncated.

### 4.6 A credit lands on the next quote, and never on a settlement

`LATE_DELIVERY_CREDIT` is "10% credit on the next bill". There is no next bill yet.

The primitive for it already exists. `QuoteAdjustmentSnapshot` (`quotes.py:89`) carries exactly the
right shape:

| field | use |
|---|---|
| `kind` | a new `QuoteAdjustmentKind.REMEDY_CREDIT`, beside the existing `PROMOTION`, `MANUAL_DISCOUNT`, `SURCHARGE`, `DELIVERY` |
| `direction` | `AdjustmentDirection.CREDIT` |
| `amount_min_vnd` / `amount_max_vnd` | non-negative integers — the **direction** carries the sign, not the amount, so invariant 2 holds without a special case |
| `source_version_id` | the published remedy-policy version the figure came from |
| `approval_id` | the `APPROVE_REMEDY` envelope, where one was required |

So a credit is an adjustment on the **next quote**, not an adjustment to a settlement. This matters
more than it looks: the settlement path keeps accepting only the exact quoted total, in full, in one
payment. `DEC-010` is untouched, because the credit changes what the total *is* before the customer
is ever told it — it does not change what may be paid against a total already agreed.

**What the credit attaches to.** Not a customer record — `DEC-015` refuses to build one (§7.1). It is
issued against the counter ticket or channel binding the order already carries, the same two sources
`OrderRepository.create` already checks per `DEC-015`'s own consequence clause, and redeemed by
presenting that ticket at the counter. This is deliberately a bearer instrument, like a paper voucher:
whoever holds the ticket number can redeem it. At 10% of a laundry bill the exposure is proportionate,
staff apply it by hand, and the alternative is the customer ledger this system has decided not to
build.

A credit is redeemable **exactly once**, enforced by the server, and an unredeemed credit expires with
the order financial record's retention schedule. It is not a liability the system tracks forever.

### 4.7 Windows are checked against recorded facts, never "now" alone

The 7-day rewash window runs from **pickup**, which is `RELEASED` on the production dimension, and the
24-hour defect window from **receipt of goods**. Both timestamps already exist on the order. The
server computes elapsed time from the recorded event, not from a staff-supplied date. An out-of-window
request is refused with the window that was missed named in the refusal, so staff can tell the
customer why rather than just that.

### 4.8 Configuration, not constants

The six figures in §4.2 are published as one immutable configuration document through the CONFIG-001
primitive, versioned and hash-addressed, exactly as the pricebook is. Invariant 11 applies: if no
remedy policy version is published, every remedy request fails closed with
`REMEDY_POLICY_UNPUBLISHED`. Hardcoding `100_000` in Python would make the shop's liability ceiling a
code deploy, and would repeat the mistake `CURRENT_PROMOTION` made (§5.2).

### 4.9 Console

A remedy is proposed from the incident it answers — `#/incidents` — and from the order detail screen.
The form shows: the kind, the computed ceiling *before* staff type an amount, the window and whether
it is still open, and whether this proposal will need the owner. Staff must never discover that the
owner is required after filling the form in.

### 4.10 Done when

- Each of the four kinds has an end-to-end test from incident to outcome.
- `LOST_ITEM` returns `REQUIRE_HUMAN`/`LOSS_POLICY_UNRESOLVED` and **no test asserts a loss ceiling**.
- A 100.001 ₫ damage proposal requires `APPROVE_REMEDY`; 100.000 ₫ does not.
- A proposal above 5× the line fee is refused with the computed ceiling in the refusal.
- Unpublishing the remedy policy makes every remedy request fail closed.
- The settlement ledger is byte-identical before and after a remedy is approved.

---

## 5. `PROMO-WIRING-001` — a resolved decision the engine still calls open

### 5.1 The current state, exactly

`packages/domain/.../promotion.py` is a complete, careful, well-tested engine: basis-point rates,
largest-remainder allocation across lines, an explicit calculation trace, integer VND throughout. It
has **zero production call sites**. Every import outside its own tests is either
`packages/evals/.../synthetic_pricing.py` or the package `__init__`.

Meanwhile `quote_composition.py` stamps `PROMOTION_NOT_EVALUATED` on every revision ever composed, and
says why in a comment that is also this item's ticket:

> `DEC-002` resolved 2026-08-18; evaluating promotions is unbuilt work, not an open decision, and this
> code says so until that work lands.

### 5.2 Two things are stale, and one is a design defect

`CURRENT_PROMOTION` carries:

```text
# DEC-002 remains open. A generic accepted_at is prohibited.
eligibility_event=None,
```

DEC-002 has not been open since 2026-08-18. The comment is false and the `None` it justifies makes
every evaluation return `PROVISIONAL` / `REQUIRE_HUMAN` forever. Correcting it means setting
`PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED`, which is `accepted_at` in the enum's own
vocabulary.

The design defect is larger: `CURRENT_PROMOTION` is a **module-level Python constant**. The shop
cannot run a promotion without a code deploy, and an expired program cannot be replaced by the owner.
Invariant 4 wants published configurations immutable and versioned; a constant is neither. Promotion
policy moves to CONFIG-001 publication, same as §4.8.

### 5.3 Today's honest answer is zero, and the console must say so

`CURRENT_PROMOTION` runs 17 Jul – 1 Sep 2026 exclusive. Today is 18 Sep 2026. The only confirmed
program **has expired**.

So wiring the engine correctly produces a 0 ₫ discount today. That is the correct answer and it is
*not* the same thing as the current behaviour, which also shows 0 ₫. The difference is the whole value
of this item:

- today: 0 ₫ because **nothing was assessed** (`PROMOTION_NOT_EVALUATED`)
- after: 0 ₫ because **the program ended on 31/08/2026** (`PROMOTION_OUTSIDE_INTERVAL`)

The console must render the second as a sentence, not as a silent zero. A zero with no explanation is
the same failure as a `null` total rendered as `0`.

### 5.4 The quote/acceptance timing problem, and its resolution

A promotion keyed on `accepted_at` is evaluated at quote time, before acceptance exists. The engine
already handles this honestly: with no `eligibility_at`, `eligibility_resolved` is false and the
result is `PROVISIONAL` / `REQUIRE_HUMAN`.

But `DEC-021` says the customer agreed to a price that was read aloud. If the number moves between
quote and acceptance, the shop charges something the customer did not agree to.

**Resolution.** Evaluate at quote time and freeze the result into the immutable revision. At
acceptance, re-evaluate with the real `accepted_at`. If the discount differs, **refuse the acceptance**
with `PROMOTION_CHANGED_SINCE_QUOTE` and require a re-quote. The system never silently re-prices an
agreed total, and never lets a promotion expire between reading a price and taking the laundry without
somebody seeing it.

### 5.5 Done when

- A quote for a targeted service inside a published, live program carries a provisional discount, a
  rate, and a named policy code.
- Accepting it with `accepted_at` inside the interval finalises the same number.
- Accepting it after the interval ends refuses `PROMOTION_CHANGED_SINCE_QUOTE`.
- With no promotion published, quotes compose normally, with a reason code that says no program is
  running — not `PROMOTION_NOT_EVALUATED`.
- `PROMOTION_NOT_EVALUATED` is deleted from `BASE_REASON_CODES`, because it will no longer be true.

---

## 6. `OPS-BOARD-001` — the day, as versioned deterministic queries

### 6.1 Constraint first

Invariant 18: *dashboard numbers, SLA flags, and operational priorities are computed by versioned
deterministic queries/rules; AI may explain them but cannot originate or mutate them.*

So the board is not a screen that adds things up. It is a named, versioned query whose output is the
number, and a screen that renders it. The version identifier travels with the result, so a figure on a
printout can be traced to the rule that produced it.

### 6.2 Scope

Three things, in value order:

1. **SLA board** — every open order against its deadline, ordered by time remaining, with breaches
   first. The SLA engine exists (`domain/sla.py`); what does not exist is a query that asks it about
   every open order at once.
2. **Day summary** — counts by the four status dimensions, plus takings already shown on `#/today`.
   The takings figure keeps the `DEC-014` role gate exactly as it is: `OWNER_ADMIN`, `OPS_APPROVER`,
   `OPERATOR`. Wording rules are unchanged and non-negotiable: *tiền đã thu*, never *doanh thu*, never
   *lợi nhuận*.
3. **Export** — `ApprovalAction.EXPORT_SANITIZED_DATA` already exists and maps to `_OWNER_FINANCIAL`
   with resource type `EXPORT_REQUEST`. An export is an owner-approved, audited act, not a button.
   The word *sanitized* in the enum is a requirement: the export carries order, money and status
   facts, and carries no incident free text and no evidence summary.

### 6.3 What this must not become

No trend lines, no forecasts, no *doanh thu*, no per-staff productivity metric. A number the shop
cannot act on today is a number that will be wrong tomorrow and believed anyway. The board answers
*what needs a person right now* and *what did we take today*, and stops.

### 6.4 Done when

- Every number on the board is produced by a query carrying a version identifier, and a test pins the
  identifier.
- A year of orders does not degrade the board: pagination and index coverage are demonstrated, not
  asserted.
- The takings figure is invisible to a role outside the `DEC-014` set, verified against a real API.
- An export without an `EXPORT_SANITIZED_DATA` approval is refused, and the produced file contains no
  incident free text.

---

## 7. Refusals — what this specification declines to build, and why

### 7.1 Customer records (name, phone, address)

`DEC-015` is RESOLVED, in the owner's own words:

> "Chưa xây lớp hồ sơ khách hàng. Khách vãng lai dùng số phiếu (DEC-013); khách nhắn tin dùng ràng
> buộc kênh đã có. […] Quyết định này mở lại khi có kênh liên lạc chính thức."

The decision names its own reopening condition — the day `CHANNEL-TELEGRAM-001` or an official Zalo OA
carries a real conversation. `CHANNEL-TELEGRAM-001` is BLOCKED. `CHANNEL-ZALO-APPLY-001` is BLOCKED.
The condition is not met.

The owner's reasoning is the binding part: *"A customer record should exist when there is both a use
for it and a lawful basis, and today there is neither: no channel is connected and nobody has been
asked."* Building a contact store before anyone has been asked for consent is a legal exposure, not a
missing feature. `DEC-027` relies on this: R1 "stores no customer personal data at all: no address
column exists in any migration."

Building it would break that claim, and every retention argument resting on it.

**Not built.** If the owner wants it anyway, that is a reopening of `DEC-015`, and it needs a consent
basis before it needs a schema.

### 7.2 Deposits, instalments, partial payment, B2B credit

`DEC-010` is RESOLVED as a deliberate deferral. The register records *why*: no B2B credit relationship
exists, and the built exact-payment path covers the ordinary retail case. `OrderBalanceStatus` already
contains `PARTIALLY_PAID`, `OVERPAID` and `ON_ACCOUNT` — they are deliberately unreachable, and
`test_settlement_policy.py` pins that.

The decision packet names what defining it would need: a deposit percentage, a rule for an unpaid
balance, and an interaction with the 20/60-day storage-and-disposal policy. None of the three exists.

**Not built.** A second money shape with no live case adds refusal surface and audit burden and earns
nothing.

### 7.3 One thing this specification asks the owner to look at

Not a refusal — an operational cost worth a signature.

`ApprovalAction.SET_RANGE_PRICE` maps to `_OWNER_FINANCIAL`: `OWNER_ADMIN`, MFA required, separation
of duty, ten-minute TTL. Under §3, quoting an áo dài therefore needs the owner, with MFA, inside ten
minutes, at the counter, per garment. Twenty of forty-four services are range-priced, so this is not a
rare path.

The argument for relaxing it to `_COUNTER_ATTESTATION` — the `DEC-021` policy, `OPERATOR`, attribution
rather than a second signature — is that the published band *is* the owner's authorisation, so an
in-band choice exercises an authority already granted. The argument against is that it is still the
only place a staff member sets a price with real money attached.

`DEC-021`'s own reasoning is the precedent that matters: *"a shift with one person on duty is a real
shift and a system that blocks it would be worked around on paper — which would cost the shop both the
control and the record."*

**This specification builds the existing mapping unchanged.** Changing who may approve a financial
action is policy. The recommendation is recorded in
`docs/DECISION_REQUEST_RANGE_PRICE_AUTHORITY_2026-09.md` for the owner to sign or decline; either way
`RANGE-PRICE-001` ships and works.

---

## 8. Sequencing

`RANGE-PRICE-001` and `PROMO-WIRING-001` both modify `compose_quote_revision`. They are sequential,
not parallel, and range goes first because it unblocks 45% of the catalogue while promotions currently
resolve to zero.

```
RANGE-PRICE-001  ──▶  PROMO-WIRING-001      (both touch quote_composition.py)
REMEDY-001                                   (independent; incidents + approvals + config)
OPS-BOARD-001                                (independent; read-only + export approval)
```

`REMEDY-001` and `OPS-BOARD-001` may run alongside the quote chain.

---

## 9. Stale statements in the codebase, corrected by this work

Each is a comment that was true when written and is false now. Leaving a false comment in place is
worse than leaving a gap, because the next reader trusts it.

| Location | Says | Truth |
|---|---|---|
| `promotion.py:118` | "DEC-002 remains open" | RESOLVED 2026-08-18 |
| `quote_composition.py:88` | `PROMOTION_NOT_EVALUATED` | fixed by `PROMO-WIRING-001` |
| `quote_composition.py:25` | "`DEC-001` […] is open" | RESOLVED 2026-08-18 |
| `quote_composition.py:204` | "while DEC-001 is open" | RESOLVED 2026-08-18 |
| `WORK_QUEUE.yaml` `DECISION-BUSINESS-001` | blocked awaiting DEC-001–004 | all four RESOLVED; the publication half is engineering |

---

## 10. What this does not move

`SHOP-CUTOVER-001` stays blocked. Nothing in this specification is software that a machine, a TLS
certificate, DNS, an off-host archive, an age keypair or a timed restore drill can be substituted for.
This work makes the shop's daily operations expressible in the system. It does not make the system
deployable to the shop.
