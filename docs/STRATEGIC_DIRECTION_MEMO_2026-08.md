# Strategic Direction Memo — Commercial Path to an Irreplaceable Product

**Authored:** 2026-08-13
**Method:** repository measurement + 2026 market and competitive research
**Status of this document:** analysis. Like `PRODUCTION_READINESS_ASSESSMENT.md` it is **not normative**,
resolves no decision, authorizes no capability, and completes no delivery item. `delivery/` remains the
machine-readable truth and `context/DECISION_REGISTRY.yaml` remains the decision authority.

**Reading order:** this memo assumes `docs/company/` (strategy suite),
`docs/PRODUCTION_READINESS_ASSESSMENT.md`, and `delivery/WORK_QUEUE.yaml`.

---

## 0. The question

> What must be built, for whom, at what price, so that a Vietnamese business cannot rationally stop
> using it — and what does that answer change about the current plan?

Three findings answer it.

**F1 — The architecture is already the market-winning shape.** The 2026 evidence from US agent
companies converges on one conclusion: agents that draft and let a human approve retain; agents that
act autonomously churn. This repository's most-constraining invariant is the market's proven product
shape. The constraint is not a tax on speed — it is the product. What is missing is that nothing
currently *shows a buyer the outcome*.

**F2 — The urgency source is mispriced.** The strategy suite sells operational efficiency. Efficiency
adoption decays — the suite's own World Bank evidence shows Vietnamese SME software use falling 80% →
40% → 35%. Vietnam has, since 2026-01-01, made a *compliance-grade revenue record* legally compulsory
for millions of businesses. Compulsory records do not decay. The same product, repriced against a
legal deadline instead of a productivity promise, has a materially stronger demand engine.

**F3 — The binding constraint is evidence, and the largest evidence input is unblocked today.** The
eval manifest requires **1,300 cases** across five suites, not the 200 quoted in the readiness
assessment. Current inventory is 32 seed cases and zero everywhere else. Two of the three corpus items
depend on exactly one thing: `CORPUS-CONSENT-001` — zero dependencies, no code, no authorization, not
started.

---

## 1. What the US winners and losers actually prove

The discriminating variable is not model quality, agent count, or framework. It is whether the
**business outcome is deterministically verifiable** and whether **a human owns the last mile**.

| Company / category | Shape | Result | Discriminating property |
|---|---|---|---|
| Sierra | Enterprise support agents, charges **per resolution** | ~$100M ARR in under two years | Gets paid only when the outcome is verified |
| Decagon | Same category, charges **per conversation** | ~$50M+ ARR | Paid for effort, not result — weaker alignment |
| Harvey | Legal workflow inside the firm's document estate | ~$300M ARR | Owns one workflow end-to-end in an existing system of record |
| Abridge / OpenEvidence | Clinical documentation and evidence | Top-tier consistent institutional capital | Output is a record someone with authority must have |
| **AI SDR category** | Autonomous outbound, set-and-forget | **50–70% churn within three months** | No downstream verification; optimized volume, not outcome |

*ARR figures and the reported ~$0.50/resolution rate come from vendor and secondary blog sources.
Treat as directional, self-reported, and unverified — not as benchmarks.*

The category-level consensus on why AI SDRs failed is worth quoting in its own terms: they optimized
output volume rather than outcome quality, and the 2026 correction is **"AI drafts, a human approves,
and a real send lands."**

That sentence is a description of `ConstrainedAgentRuntime` → Tool Facade → policy → approval → outbox
→ sole sender. This repository built the correction before the market finished making the mistake.

**But the moat only pays if the outcome is metered and shown.** Sierra's defensibility is not its
model — it is that both parties can agree a resolution occurred. This repository can prove an outcome
occurred more rigorously than Sierra can, through atomic mutation + domain event + audit + outbox. It
currently exposes that to nobody. There is no buyer-facing outcome surface anywhere in `apps/web`.

> **Recommended decision R1.** Treat "the customer can see the verified outcome and its evidence" as a
> **P0 product requirement**, not a reporting nicety. It is the mechanism that converts the governance
> investment into price.
> *Reverses if:* design-partner interviews show owners do not look at outcome evidence and buy on
> gut-feel responsiveness alone.

---

## 2. The demand reframe — from efficiency to compliance-grade record

### 2.1 Ranking Vietnam's forcing functions

A forcing function is stronger the less discretion the buyer has.

| Rank | Forcing function | Discretion | Evidence quality |
|---|---|---|---|
| 1 | **Legal deadline** — lump-sum tax (`thuế khoán`) abolished 2026-01-01; business households move to self-declaration on actual revenue, filing monthly/quarterly instead of annually. Decree 70/2025 requires e-invoices from connected cash registers above the stated revenue threshold. | None | Government and law-firm sources; **exact thresholds conflict across sources and must be verified with counsel** |
| 2 | **Labor cost** — a customer-service employee costs roughly 81–100M VND/year gross, i.e. ~7–8M VND/month, meaningfully more fully loaded | Medium | Salary aggregators; directional only |
| 3 | **Missed revenue** — inbound messages and calls unanswered during peak hours become lost orders | High | US SMB voice-agent data; not Vietnam-specific |

The suite currently sells on #2 and #3. #1 is stronger than both and it arrived seven months ago.

### 2.2 The reframe

When a household or small business leaves `thuế khoán`, the binding constraint is not the tax form.
The tax authority is deploying free declaration software, and MISA, Viettel, VNPT and FPT own e-invoice
distribution. The constraint is that **self-declaration requires a complete, reconcilable revenue
record, and in Vietnam revenue arrives as chat, cash, livestream and bank transfer.**

That is precisely the gap `01_VISION_AND_STRATEGIC_THESIS.md` names — "the low-friction path between
fragmented customer messages and trustworthy execution." Vietnam has just made closing that gap legally
compulsory and expensive to get wrong.

The product's job statement changes from:

> *"We save your staff time."*  (decays — World Bank evidence)

to:

> *"Every order you take through chat becomes a clean, priced, evidenced record your accountant
> and the tax authority will accept. You do not retype anything."*  (does not decay)

**HYPOTHESIS status.** This is an unproven belief, in the sense the strategy suite defines. It is not
a decision.

### 2.3 The dependency this reframe carries — name it or the pitch is naive

The tax authority accepts records through **certified e-invoice providers**, not through this
repository's order and price-snapshot chain. The bridge from "our verified record" to "what the
authority accepts" runs through a provider integration.

`02_PRODUCT_AND_MARKET_STRATEGY.md` ranked household-business compliance-to-cash **priority 4, "only
with strong integrations"** for exactly this reason. This memo does **not** overturn that ranking. It
splits the workflow:

- **The record half is Horizon 1** — build it; it is the existing domain plus a channel.
- **The filing half stays Horizon 2+** — partner, never build. We complete and hand over; we do not
  file, compute tax, or advise on tax.

That partnership sits on the wedge's critical path and is commercial work, not engineering work.

> **Recommended decision R2.** Adopt the compliance-grade-record framing as the primary commercial
> message for Horizon 1, while keeping laundry as the reference pack and keeping all tax filing,
> calculation and advice explicitly out of scope (`NOT_SUPPORTED`).
> *Disconfirmation test:* ten structured interviews with service SMEs in Khánh Hòa. **If fewer than
> three name declaration or record-keeping as a problem they are currently spending money or manager
> hours on, drop the compliance framing** and revert to the efficiency framing. Run this before any
> code changes; it costs two weeks and no engineering.

---

## 3. The feature set, ranked by forcing-function strength

**First principle for irresistibility.** A product becomes impossible to stop using when three
conditions hold simultaneously:

1. **The work arrives through it** — it is on the path the customer already uses.
2. **The record it produces is needed by someone with authority** — tax authority, owner, or customer.
3. **Not using it costs more than using it** — in money, not in sentiment.

Most SME software satisfies only (1). Efficiency tools satisfy (1) and weakly (3). The tax reform is
what makes (2) available. Every feature below is scored against all three.

| # | Feature | Forcing function | 1 · 2 · 3 | Repository delta |
|---|---|---|---|---|
| 1 | **Zero-typing intake → verified quote.** Customer messages in Vietnamese on the official channel; the model extracts items/service/weight; the deterministic engine prices it; staff approves in one tap; an immutable price snapshot is returned. | Labor + missed revenue | ✓ ✓ ✓ | Channel + `AGENT-PIPELINE-001` + `SHADOW-CONSOLE-001`. Domain and pricing exist. |
| 2 | **Exception queue with pre-assembled context.** Everything the agent cannot resolve becomes a typed human task carrying the authoritative facts, the policy result, and the reason it stopped. | Labor | ✓ ✓ ✓ | `SHADOW-CONSOLE-001`. Policy layer exists. |
| 3 | **Daily close for the owner.** Deterministic read model: promised, delivered, charged, corrected, outstanding. Opened every day; this is the retention hook, and the answer to `09`'s owner/finance JTBD. | Cash control | ✓ ✓ ✓ | New read models on existing events. No agent involvement required. |
| 4 | **Payment-evidence reconciliation (`công nợ`).** Match bank-transfer evidence to orders; surface unmatched money as an exception, not a spreadsheet. | Cash | ✓ ✓ ✓ | New connector + reconciliation logic. Outbox/evidence semantics exist. |
| 5 | **Compliance-grade record export.** The revenue record in the shape an accountant or e-invoice provider accepts, with provenance intact. | **Legal deadline** | ✓ ✓ ✓ | Export layer + partner mapping. Depends on R2 surviving its test. |

Features 1–3 are the minimum coherent product. Feature 4 is the strongest single differentiator against
KiotViet/Sapo/Pancake, none of which own the message-to-money chain. Feature 5 is the demand engine
if R2 survives.

### 3.1 Explicitly not now — and why

| Not building | Reason |
|---|---|
| **Voice / AI receptionist** | Fastest-growing SMB category in the US and a real Vietnam pain — but this repository has *no channel at all* yet. Adding ASR latency, transcription error and a real-time turn budget before a single text message has been handled is sequencing failure. Horizon 2 candidate, revisit after G2. |
| **Multi-agent execution** | `STG-005` already bars it without measured superiority on an independent parallelizable task. Nothing in the customer path qualifies. `RSK-011`. |
| **Autonomous send** | The single behaviour that destroyed the AI SDR category. `AUTONOMY-001` stays last in the queue and stays capability-specific. |
| **Own e-invoice / tax engine** | Regulated, provider-gated, and directly against MISA/Viettel/VNPT distribution. Partner. |
| **Second vertical pack** | `11_EXECUTION_ROADMAP.md` Phase 3 gate. Zero evidence exists for the first pack. |
| **Shared multi-tenant SaaS** | `STG-007`. Not before isolation evidence. |
| **Own or self-hosted model** | `STG-012`. The bottleneck is customer evidence, not inference. |

---

## 4. Pricing — the docs anchor to the wrong number

### 4.1 The two anchors

| Anchor | Value | Source quality |
|---|---|---|
| **Vietnamese SME software price** | KiotViet ≈ 270k VND/month; Sapo 170k / 249k / 600k / 999k VND/month by tier | Secondary listing sites; directional |
| **Vietnamese CSKW labour** | ~81–100M VND/year gross ⇒ ~7–8M VND/month, higher fully loaded | Salary aggregators; directional |

`02` and `09` propose 0.5–1.0M VND/month for micro businesses and 2–5M for SME. Against the *software*
anchor that is 2–18× the market. Against the *labour* anchor, 2–5M is roughly a quarter to a half of
one headcount — entirely defensible.

**Conclusion: never sell against software. Sell against a fraction of a person.** This is the same
move that let Sierra and Harvey price 10–100× above the SaaS they displaced, and it is only credible
if the product verifiably removes or prevents work — which is exactly what the verified-outcome metric
measures. The pricing model and the North Star metric are the same object.

### 4.2 Three specific corrections

**(a) Kill the direct micro tier.** 0.5–1.0M VND/month sits above the software anchor a micro business
recognises and below the cost of the dedicated trust cell `STG-007` requires. `ASM-006` fails in that
segment. Do not sell it.

**(b) Charge per verified outcome, never per conversation.** Sierra bills resolutions; Decagon bills
conversations. Conversation counts are gameable by both parties and reward the wrong behaviour. Define
the billable unit as *an approved customer communication bound to an order state transition that a
human would otherwise have produced*, metered deterministically from domain events, with reversals
excluded. `15_METRICS_AND_EXPERIMENTATION.md` already specifies the anti-gaming rules for this.

**(c) Reach micro through `đại lý thuế` / accounting service firms, not directly.** The abolition of
lump-sum tax converts these firms' clients from one annual filing into monthly or quarterly
declarations. Each firm serves tens of businesses, holds real budget, is geographically concentrated,
and is now acutely bottlenecked on turning messy client records into declarable ones. They are a buyer
*and* a distribution channel to thousands of household businesses.

This is a different ICP from `02`'s lighthouse profile and would be a Horizon-2 pack. Start discovery
interviews now — they cost nothing and inform R2's test. Do not build for it before Phase 2 exit.

> **Recommended decision R3.** Adopt: no direct micro tier; SME platform fee 2–3M VND/month plus a
> deterministic verified-outcome unit; outcome defined against order state transitions; `đại lý thuế`
> discovery opens immediately as research only.
> *Reverses if:* paid-pilot negotiation shows buyers reject a variable unit, or metering disputes
> exceed the cost of a flat fee. In that case revert to flat platform pricing with a volume ceiling —
> not to per-conversation billing.

---

## 5. Plan mechanics — what actually changes in the queue

Nothing in this section adds a work item, reorders the controller, or changes a gate. It changes
*where effort goes* within the existing queue.

### 5.1 The evidence requirement is 6.5× larger than the headline

`specs/evals/eval-manifest-v1.yaml` requires:

| Suite | Minimum | Actual |
|---|---:|---:|
| Frozen regression | 200 | 0 |
| Normal language | 300 | 0 |
| Adversarial | 200 | 0 |
| Synthetic combinatorial | 500 | 0 |
| Public corpus | 100 | 0 |
| **Total** | **1,300** | **32 seed** |

Plus `shadow_exit`: **30 real orders**, 10 batch logs, 20 delivery logs.

The normal-language suite carries a mandated distribution — 25% colloquial Vietnamese, 15% missing
diacritics, 15% typo/abbreviation, 10% multi-intent, 15% ambiguous service. **That distribution cannot
be credibly synthesised.** It has to be mined from real Vietnamese message history.

`SYNTHETIC_COMBINATORIAL` (500 cases, the largest single number) can be generated from the
deterministic domain without any customer data — `EVAL-SYNTHETIC-COMBINATORIAL-001` is therefore safe,
unblocked, high-volume local work available immediately. The adversarial (200) and public-corpus (100)
suites are also largely authorable without consent-cleared history. The consent dependency binds hardest
on the normal-language and frozen-regression suites.

### 5.2 The keystone item

`CORPUS-CONSENT-001` — *"Consent basis and reviewed anonymization of real customer message history"* —
has **`depends_on: []`**. Both `EVAL-CORPUS-001` and `EVAL-LANGUAGE-CORPUS-001` depend on it. It
requires no code, no provider call, no credential, no deployment, and no capability authorization.

It is the single highest-leverage unstarted item in the program, and it is legal work plus a review
process, not engineering. **Start it this week.**

### 5.3 The scoped observation period — the largest available schedule compression

Baseline measurement, workflow observation and corpus accumulation can all begin **before** any agent
exists, using the shop that already operates.

Precisely scoped, so this cannot be read as gate evasion:

- Staff continue working **exactly as today, on their own accounts**. No behaviour change is requested
  of the customer.
- The system **records, measures and accumulates**. It does not draft.
- **No model invocation** — `DEC-006` is open.
- **No system-mediated send** — manual-send storage is restricted to `SHADOW` plus synthetic
  `INTERNAL_TEST` pending `DEC-005`.
- **No public ingress, no credential, no capability change.**

What it produces: the `SHADOW-001` denominator, real cycle and delivery-cost baselines, the message
corpus, and console hardening against real data shapes rather than fixtures. Three of the five gate
inputs, obtained during weeks that are otherwise spent waiting on external decisions.

`SHOP-INSTRUMENT-001` (4–6 weeks, `depends_on: []`) is this work. It is already in the queue and
already flagged as calendar-bound. It has not started.

### 5.4 Effort rebalance

The readiness assessment measured 175 delivery/CI/supply-chain tests against 33 product tests, twelve
of them covering a component frozen by ADR-0004. Its conclusion stands and this memo reinforces it:
**the next quarter is product-path work.** Governance machinery is touched only where a named gate
demands it.

### 5.5 Calendar-bound items, restated with their cost of delay

| Item | Lead time | Every week not started |
|---|---|---|
| `SHOP-INSTRUMENT-001` | 4–6 weeks | adds a week to `SHADOW-001`, which has no valid denominator without it |
| `CHANNEL-ZALO-APPLY-001` | 2–8 weeks external verification | adds a week to every public capability |
| `PROVIDER-ACCESS-001` | resolves `DEC-006` | adds a week to the first model invocation in project history |
| `CORPUS-CONSENT-001` | legal/review | adds a week to 500 of the 1,300 required cases |

All four have `depends_on: []`. None requires code. None is started.

---

## 6. Signature page — decisions only the owner can make

Each row is a recommendation with its reversal condition. None of these is decided by this memo.

| ID | Question | Recommendation | Reverses if |
|---|---|---|---|
| `DEC-001` | Weight precision and rounding | Round to a published increment in the customer's favour; display measured and billed weight separately; the 6 kg cliff stays deterministic and visible in the quote | Measurement equipment cannot support the increment reliably |
| `DEC-002` | Promotion eligibility event | Bind eligibility to a single immutable domain event at quote creation, not to conversational claim | Cross-channel promotions require an earlier binding point |
| `DEC-003` | Delivery beyond 6 km, one-leg | Publish an explicit distance band table with `NOT_SUPPORTED` beyond the last band; never interpolate | Real delivery-cost data from `SHOP-INSTRUMENT-001` shows bands mis-priced |
| `DEC-004` | Rewash / loss / damage / credit | Keep `HUMAN_APPROVAL_REQUIRED` permanently for compensation; publish only the *eligibility* rules deterministically | Compensation volume makes per-case approval infeasible — then bound by amount ceiling, never remove approval |
| `DEC-005` | Official production channel | Confirm Zalo OA per ADR-0005 and close the registry entry, which still reads `OPEN` — this inconsistency should be reconciled either way | Zalo OA verification fails or terms are unacceptable; Telegram remains sandbox-only regardless |
| `DEC-006` | Provider data terms | Dedicated non-personal organization and credential, verified `store:false`, immutable model release pin, no real PII until all four hold | Provider terms cannot be verified — then the data path stays disabled and the project stays synthetic |
| `DEC-008` | Retention / redaction / deletion | Set the schedule **before** `CORPUS-CONSENT-001` mines any history — the corpus is the first real retention decision the project makes | — |
| `DEC-HOSTING` | Hosting provider for ADR-0007 | Owner decision; unresolved, and currently blocking the entire controller loop | — |
| `STG-007` | Dedicated tenancy first | Accept as proposed; it is also what makes the compliance-record pitch contractually credible | Dedicated-cell cost exceeds the price a 2–3M/month tier can carry |
| `STG-010` | Hybrid outcome pricing | Accept with the R3 corrections (no micro tier, outcome not conversation) | Buyers reject the variable unit |
| `STG-011` | Initial segment | Keep service order-to-cash; add `đại lý thuế` as a Horizon-2 discovery track only | R2's ten-interview test fails |
| `STG-014` | No shared raw production learning | Accept as proposed, and note it is a *sales asset* in the compliance framing, not only a restriction | — |

---

## 7. What would make this memo wrong

Stated in the suite's own evidence discipline, because it is the suite's signature and a memo that
breaks it undercuts itself.

1. **Several load-bearing figures are vendor, blog, or aggregator sourced** — ARR rankings, the
   ~$0.50/resolution rate, the 50–70% AI SDR churn figure, Vietnamese SaaS prices, and salary bands.
   They are directional context. None of them should be quoted externally or used in a customer claim
   without primary verification, per `14_RESEARCH_EVIDENCE_REGISTER.md`.
2. **The Vietnamese tax thresholds conflict across sources** — a 200M VND/year VAT floor and a 1B
   VND/year e-invoice-from-POS threshold both appear, alongside a claim that ~80% of households under
   1B remain outside VAT and PIT. These are not reconciled here. **Qualified Vietnamese counsel must
   establish the applicable thresholds before any customer-facing claim.** Section 2 depends on the
   *direction* of the reform, which is unambiguous, not on the exact numbers.
3. **R2 is a hypothesis with a cheap, fast, falsifiable test.** If the ten interviews fail it, sections
   2, 3 (feature 5) and 4(c) fall, and the efficiency framing returns. Sections 1, 3 (features 1–4),
   4(a)(b) and 5 survive independently — they rest on repository measurement and on the
   winner/loser pattern, not on the tax reform.
4. **The strongest counter-argument to this entire memo** is that the project has one shop, one owner,
   and no customers, and that any commercial framing is premature relative to `AGENT-PIPELINE-001`.
   That argument is largely correct about *sequencing* and largely wrong about *cost*: every
   recommendation in section 5 is work that is already in the queue, already unblocked, and already
   waiting.

---

## 8. If only three things happen

1. **`CORPUS-CONSENT-001` and `SHOP-INSTRUMENT-001` start this week.** Both have no dependencies,
   need no code, and gate the two largest remaining evidence requirements.
2. **The ten-interview test for R2 runs before any code changes direction.** Two weeks, no
   engineering, and it decides whether the product's message is efficiency or compliance.
3. **`DEC-HOSTING` is resolved.** The controller's last run terminated `BLOCKED` on it, and it is the
   only blocker in the program that no amount of engineering can remove. Other local items —
   `AGENT-PIPELINE-001`, `EVAL-SYNTHETIC-COMBINATORIAL-001` — remain independently workable; this
   memo does not claim the whole queue is frozen.

---

## Appendix — external sources

Recorded in the format of `14_RESEARCH_EVIDENCE_REGISTER.md`. Accessed 2026-08-13. None of these is a
primary legal or financial authority, and none may be used in a customer-facing claim without the
verification each row names.

### Vietnam tax reform and household-business obligations

| Source | Claim it supports | Limitation |
|---|---|---|
| [Ecovis — Business Households in Vietnam: New 2026 Tax Regulations](https://global.ecovis.com/business-households-in-vietnam-new-2026-tax-regulations/) | Lump-sum tax abolished from 2026-01-01; move to self-declaration | Advisory-firm summary; verify thresholds with counsel |
| [Bizzi — Officially Abolishing Business Household Tax From January 1, 2026](https://bizzi.vn/en/business-tax-write-off/) | Same reform; Decree 70/2025 e-invoice-from-cash-register obligations | Vendor content with a commercial interest in the conclusion |
| [Viet An Law — New 2026 Rules for Household Enterprises Tax](https://vietanlaw.com/new-2026-rules-for-household-enterprises-tax-in-vietnam/) | VAT/PIT-only structure from 2026; revenue bands | Law-firm marketing content, not an engagement opinion |
| [Vietnam News — Việt Nam puts an end to lump-sum tax](https://vietnamnews.vn/economy/business-beat/1728186/viet-nam-puts-an-end-to-lump-sum-tax-aiming-for-a-level-playing-ground.html) | Policy direction and Resolution 68-NQ/TW context | State media summary |
| [MISA SME — Thuế hộ kinh doanh từ 2026](https://sme.misa.vn/334301/thue-ho-kinh-doanh/) | Declaration mechanics; 200M VND VAT floor | **Competitor** content; also demonstrates MISA already owns this narrative |
| [einvoice.vn — Hộ kinh doanh chuyển sang kê khai thuế từ 2026](https://einvoice.vn/tin-tuc/hkd-chuyen-sang-ke-khai-thue-tu-nam-2026) | 1B VND e-invoice-from-POS threshold; declaration frequency | E-invoice vendor; **this and the MISA figure are the conflicting thresholds noted in §7.2** |
| [VietnamNet — Hộ kinh doanh lo khó kê khai thuế](https://vietnamnet.vn/ho-kinh-doanh-lo-kho-ke-khai-thue-bo-tai-chinh-noi-gi-2539969.html) | Household businesses report difficulty; state deploying free declaration software | News report; the free-software point is a direct competitive risk to any paid filing product |

### Vietnamese market, channel, and price anchors

| Source | Claim it supports | Limitation |
|---|---|---|
| [NPLG — Bảng giá phần mềm KiotViet 2026](https://nplgcorp.com/gia-phan-mem-kiotviet-bao-nhieu/) | KiotViet ≈ 270k VND/month | Reseller listing, not the vendor pricing page |
| [software-listing.com — Sapo Review 2026](https://software-listing.com/blog/sapo-review-vietnam-omnichannel-retail-smes-2026) | Sapo tiers 170k / 249k / 600k / 999k VND/month | Third-party listing; verify on sapo.vn before use |
| [Vietnam Briefing — E-Commerce Sector Outlook](https://www.vietnam-briefing.com/news/vietnam-e-commerce-sector-2026.html/) | Shopee + TikTok Shop ≈ VND 280tn H1 2026; TikTok Shop >40% share | Consultancy analysis |
| [vietnam.vn — Livestream "factory" of top fashion SMEs on TikTok Shop](https://www.vietnam.vn/en/cong-xuong-livestream-cua-smes-top-dau-nganh-thoi-trang-tren-tiktok-shop) | Order concentration at high-volume sellers (≈4,000 orders/day example) | Single-company illustration, not a distribution |
| [MISA — Top AI companies in Vietnam](https://amis.misa.vn/en/263543/top-ai-companies-in-vietnam/) and [MISA Agentic AI](https://amis.misa.vn/256613/dich-vu-trien-khai-agentic-ai/) | MISA AVA already ships AI accounting/reconciliation; FPT.AI commercialising agentic AI | **Competitor self-description**; establishes the incumbent threat, not its quality |
| [ERI — Customer Service Representative Salary in Vietnam 2026](https://www.erieri.com/salary/job/customer-service-representative-general-calls/vietnam) and [WorldSalaries](https://worldsalaries.com/average-customer-service-representative-salary-in-vietnam/) | ~81–100M VND/year gross CSKH labour anchor | Salary aggregators; modelled not surveyed; excludes employer on-costs |

### US agent-company patterns

| Source | Claim it supports | Limitation |
|---|---|---|
| [ValueAdd VC — How Sierra AI makes money](https://valueaddvc.com/blog/how-does-sierra-ai-make-money-outcome-based-pricing-enterprise-agents-and-the-business-model-breakdown) and [Lorikeet — Sierra AI pricing](https://www.lorikeetcx.ai/articles/sierra-ai-pricing-alternatives) | Outcome/per-resolution pricing; reported ~$0.50/resolution plus platform fee | Sierra publishes no pricing; figures are second-hand, and Lorikeet is a competitor |
| [witn — AI agent pricing 2026: Fin, Sierra, Decagon](https://www.thewitn.com/blog/ai-agent-pricing-in-2026-what-fin-sierra-decagon-and-agentforce-actually-charge) | Decagon bills per conversation vs Sierra per resolution | Blog analysis of private contracts |
| [firstsales.io — Why AI SDRs Fail: The 3-Month Churn Problem](https://firstsales.io/blog/why-ai-sdrs-fail/) and [ziellab — AI SDR reality check](https://ziellab.com/post/ai-sdr-what-works-after-the-hype-2026-guide) | 50–70% churn within three months; the "AI drafts, human approves, real send lands" correction | Vendor blogs in the same category; the churn figure is repeated across sources but not independently audited |
| [Capital Signal — Top AI Agent Startups 2026](https://capitalsignal.org/top-ai-agent-startups-2026/) and [tryanalyze — 50 fastest-growing AI companies](https://www.tryanalyze.ai/blog/fastest-growing-ai-companies) | Sierra ~$100M ARR <2y; Harvey ~$300M ARR; Decagon ~$50M+ | Aggregator rankings; self-reported ARR, inconsistent definitions |
| [saasmag — Vertical AI agents eating horizontal SaaS](https://www.saasmag.com/vertical-ai-agents-eating-horizontal-saas/) | Workflow ownership + permissioned proprietary data as the 2026 moat thesis | Trade publication; directionally consistent with Doc 14's own synthesis |
| [Menlo/a16z/Bessemer 2026 predictions roundup](https://www.theaiopportunities.com/p/the-full-2026-vc-ai-predictions-where) | Enterprise GenAI spend $37B in 2025; agents ≈$750M of $8.4B horizontal apps | Investor predictions; forward figures are forecasts, not measurements |
| [AInora — State of AI Voice Agents 2026](https://ainora.lt/blog/state-of-ai-voice-agents-report-2026) and [SchedulingKit statistics](https://schedulingkit.com/statistics/ai-receptionist-statistics) | Voice-agent SMB adoption crossing 30%; missed-call revenue loss | Vendor-adjacent statistics pages; US market, not Vietnam |
