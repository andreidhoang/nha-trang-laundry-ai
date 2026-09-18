# Unified customer agent — demand verification and build decision

**Assessed:** 2026-09-18 (Asia/Ho_Chi_Minh) · **Commit assessed:** `ccfca45`
**Method:** direct codebase measurement plus external verification; every external figure is cited in §11.
**Status of this document:** analysis. It is **not normative**, resolves no decision, authorizes no
capability, enqueues no work item and grants nothing. `delivery/` remains machine-readable truth.
**Question answered:** should this business build a unified, multi-channel, 24/7 customer-support AI
agent with per-customer profiles; is the demand and commercial value real; and should a pre-built
agent (OpenClaw) be integrated instead of the runtime this repository already owns.

---

## 0. Tóm tắt cho chủ tiệm

**Nên làm — nhưng việc tiếp theo không phải là AI.**

Hệ thống đã xây gần đủ phần *máy móc* của một trợ lý trả lời khách (kênh, đồng ý/từ chối nhận tin,
công cụ tra cứu đơn, bộ hãm an toàn của mô hình). Thứ còn thiếu không phải mã nguồn — là **bằng
chứng** và **năm quyết định chỉ chủ tiệm trả lời được**. Trong đó quyết định đắt nhất về thời gian
là hồ sơ Zalo OA: **2–8 tuần chờ xét duyệt, không kỹ thuật nào rút ngắn được.**

Năm việc nên bắt đầu hôm nay, theo thứ tự:

1. **Trả lời nốt `DEC-016`** — tài khoản kênh thuộc **CÔNG TY TNHH A & T CARE** hay thuộc cá nhân, và
   **ngoài 08:00–20:00 tiệm nói gì**. Chưa trả lời thì không được nối kênh nào.
2. **Nộp hồ sơ Zalo OA** theo MST `4202059758`, CCCD người đại diện **khớp tên trên giấy phép**
   (hồ sơ sai là lý do bị từ chối phổ biến nhất, đồng hồ 14 ngày chạy sẵn).
3. **Chốt `DEC-006`** — nhà cung cấp mô hình được dùng dữ liệu thế nào. **Chưa chốt thì không một lời
   gọi mô hình nào được phép chạy**, kể cả nội bộ.
4. **Chọn máy chủ** (`DECISION-HOSTING-001`) để `SHOP-CUTOVER-001` mở khoá và **tiệm bắt đầu chạy
   thật trên console**. Cổng G2 đòi **30 đơn thật** — chỉ tiệm đang bán mới sinh ra được.
5. **Mở lại `DEC-015`** (hồ sơ khách hàng) theo hình dạng ở §5.2: hồ sơ **do chính tin nhắn của khách
   tạo ra**, không do nhân viên gõ số điện thoại vào. Khách vãng lai vẫn chỉ có số phiếu.

**Về OpenClaw: không dùng cho khách.** Năm 2026 nó dính 9 lỗ hổng CVE trong 4 ngày, ~135.000 máy phơi
ra Internet, và **341/2.857 “skill” trong chợ của nó là mã độc (~12%)**. Cho một hệ thống mà toàn bộ
thiết kế là "chữ khách gõ vào không bao giờ được chạm tới quyền quyết định", đó là sai lầm về loại,
không phải về mức độ. Repo đã quyết đúng hai lần rồi (ADR-0003, ADR-0004). OpenClaw vẫn dùng được cho
**việc riêng của chủ tiệm** (tra cứu, soạn thảo, báo cáo) trong một ô tin cậy tách biệt.

**Một câu thật lòng:** đầu tư AI lúc này là xây tầng hai khi tầng một chưa đổ mái. Việc có đòn bẩy cao
nhất tuần này không nằm ở AI — nó là **đưa tiệm lên chạy thật trên console**.

---

## 1. Verdict

**GO — conditionally, in a fixed order, with three corrections to the proposal as stated.**

The proposal is architecturally correct and is, in substance, the ladder this repository was already
built to climb: `CHANNEL-ZALO-APPLY-001` → `CHANNEL-ZALO-001` → `PUBLIC-POLICY-001` → `CHANNEL-001`
→ `AUTONOMY-001`, behind `G2_PUBLIC_ASSISTED_ENTRY`. Nothing in the idea needs a new architecture.

What the idea does not survive is its own sequencing. Measured today:

| Layer the proposal needs | State | Distance |
|---|---|---|
| Canonical inbound envelope, outbox, receipts | `CHANNEL-ENVELOPE-001` **COMPLETE** | none |
| Consent capture, STOP suppression, opt-out/in-flight race | `CONSENT-STOP-001` **COMPLETE** | none |
| Retention that actually disposes | `RETENTION-STORE-001` **COMPLETE** (2026-09-17) | none |
| Bounded agent runtime (Responses FSM, budgets, bridge) | `RESPONSES-RUNTIME-001`, `AGENT-PIPELINE-001` **COMPLETE** | none |
| Ten typed tool operations with a real backend | `TOOL-BACKEND-001` **COMPLETE** | wired to `UnavailableAgentToolBackend` **by design** (`facade.py:153`) |
| Deterministic money/SLA/promotion authority | `packages/domain` production-grade | none |
| **A channel with a customer on the other end** | **zero adapters, zero providers** | Zalo OA verification: **2–8 weeks, external** |
| **A customer record** | **does not exist** — `DEC-015` resolved as *not yet* | owner decision |
| **Authority to call a model at all** | `DEC-006` **OPEN** | owner/legal decision |
| **Evidence: real orders** | **0** — shop not cut over | `G2` needs **30** |
| **Evidence: eval corpus** | 669/1300; normal-language 0/300, adversarial 0/200, frozen 0/200, public 0/100 | agent-buildable now |
| **Capability authorization** | 13/13 `NOT_AUTHORIZED` | signed 3-actor manifest |

So the answer is not "build it" or "don't". It is: **the mechanism is ~70% built and the authority
layer is 0% built, and only one of those two is accelerated by engineering effort.** Spending the
next month on agent features buys nothing that is not already gated; spending it on the eval corpus
and on the owner's five decisions buys the whole ladder.

---

## 2. First principles — what the agent physically needs

Strip the framing. For an AI to tell a Nha Trang customer at 02:00 where their laundry is, six things
must be simultaneously true. Nothing else matters, and each is separately falsifiable.

**1. A channel the customer already uses, authenticated both ways.**
Zalo, not Telegram, not a web widget. 77M monthly actives, ~85–87% national penetration, ~2bn
messages/day [S1][S2]. The repo's envelope is already provider-generic and the adapter is a mapping
exercise; what is missing is the *provider*, and the provider is an external 2–8 week verification
against the business licence [S3]. **Engineering cannot compress this. Only starting it earlier can.**

**2. An identity binding: this Zalo user ↔ this physical bag of laundry.**
This is the hardest structural fact in the whole proposal, and it is invisible from outside the code.
`contact_channel_bindings` is keyed on `(provider, provider_user_ref)` — a *messaging* identity. The
counter's walk-in identity, under `DEC-013`, is a **ticket number and nothing else**: no name, no
phone, no address. So a person who drops off laundry and a person who sends a message are, today,
two unrelated rows with no join between them. **"Track customers and their profiles" is not a feature
request — it is the missing spine**, and §5.2 says what shape it must take.

**3. A fact source that is true.**
This exists and is the repository's best asset: `orders` with four independent status dimensions,
immutable quote snapshots with calculation traces, settlement that can actually close an order,
atomic mutation + domain event + audit + outbox. The 6 kg pricing cliff (5.9 kg = 147,500đ,
6.0 kg = 120,000đ) is encoded as a confirmed rule the model may never smooth.

**4. A bounded decision surface.**
`ConstrainedAgentRuntime` + the Responses FSM + `AgentToolBridgeSession` + the typed PDP. Ten fixed
operations, ≤3 model calls, ≤6 tool calls, 20s ceiling, draft-or-`REQUIRE_HUMAN`, no channel
credential in the reasoning cell. Built, tested on every negative path, never yet given a provider.

**5. A lawful basis.**
Vietnam's PDPL (Law 91/2025/QH15) has been in force since **2026-01-01**, with a separate opt-in /
opt-out regime for marketing, a prohibition on bundled consent, an explicit rule that silence is not
consent, and fines up to **VND 3bn** — or 10× the gain, or 5% of prior-year revenue for cross-border
violations [S4][S5]. For a two-person shop, that is not a compliance line item; it is an existential
number. It is also why `DEC-013`'s "no name, no phone, no address for walk-ins" is not timidity —
it is the cheapest lawful posture available, and it is already the one in force.

**6. Evidence that it is right, and a way to stop it.**
`G2_PUBLIC_ASSISTED_ENTRY` requires 14 shadow days, 100 representative interactions, 30 real orders,
zero wrong-money / unauthorized-action / disclosure / suppression-miss / duplicate-send incidents, a
published public corpus, and a signed release manifest. The eval manifest requires 200 frozen
regression + 300 normal-language + 200 adversarial + 500 synthetic + 100 public cases, with
`required_policy_violation_rate: 0` on the adversarial suite and
`required_fact_citation_accuracy: 1` on the public suite. **This is the actual price of the idea**,
and it is the right price for a system that quotes money.

---

## 3. Is the demand real? — verified

**Yes, and it is more specific than "customers want faster replies".**

**3.1 The channel is real.** Zalo is not a preference, it is the default surface for Vietnamese
consumer-to-shop contact: ~85–87% penetration, 155,000+ business accounts [S1][S2]. `ADR-0005`
already reasoned to this conclusion and its reasoning holds: *"Telegram would ship fastest and produce
evidence that represents nobody."*

**3.2 Responsiveness is already a competitive axis in this exact market.** From
`docs/CLIENT_ACQUISITION_EXECUTION_2026-08.md`, verified 2026-08-18: **Giặt Ủi 2H** publishes 40+
hotel clients, 2–4 hour turnaround, 30-minute pickup, and serves **VI/EN/RU/KO**. **WashInCloud** pays
hotels a **25% revenue commission**, activates by QR, and contracts within 24 hours. **VIKHACO** runs
24 tonnes/day from an office on the same street. The shop appears in **no** "top 10/20 laundries in
Nha Trang" listing while at least seven competitors do. Competitors are not being beaten on wash
quality; they are being beaten on **being reachable**.

**3.3 The shop has already sold a promise it cannot staff.** `BUSINESS_TRUTH_INTAKE.md` §4 commits to
a **5–10 minute response, 08:00–20:00**. §1 records that the person answering inbound is **Hoài Ngọc**
— who is also the legal representative, a co-owner, and one of only **two** staff who also wash, fold,
iron and deliver. `DEC-016` records the consequence in the registry's own words: the commitment
"hiện dựa hoàn toàn vào một người". **This is the demand.** Not growth, not novelty: a promise already
made to customers that one person cannot keep through a wash cycle.

**3.4 The out-of-hours and foreign-language segment is measurable and counter-cyclical.** Khánh Hòa
in Nov 2025: 727,000 stays, of which **411,000 international vs 316,000 domestic** — international
arrivals *exceed* domestic precisely in the domestic low season. First seven months of 2026: ~15.1M
arrivals (+38.95%), ~5.5M international (+67.24%). The shop opens 08:00–20:00; **half the clock is
unstaffed**, and it is the half in which a Russian or Korean guest checks their phone. A competitor
already advertises four languages. An LLM is *genuinely* differentiated here in a way it is not on
"answer FAQs faster" — multilingual coverage is otherwise a hiring problem this shop cannot solve.

**3.5 What the demand is NOT.** It is not 24/7 *service*. The machines, the two staff and the delivery
motorbike are 08:00–20:00. `RESEARCH_BRIEF.md` §8 already said this correctly: an agent can be a
**24/7 receptionist**; it cannot make washing 24/7. An agent that answers at 02:00 with anything other
than acknowledgement, status, published facts and a queued request is manufacturing a promise no human
is behind — which is exactly the second open half of `DEC-016`.

---

## 4. Commercial value — unit economics, and where the real cost is

### 4.1 Marginal runtime cost is negligible, and that is not the interesting part

A bounded Concierge turn is capped at 3 model calls and 6 tool calls. Assume ~4,000 input tokens per
call (system + tool schemas + signed context packet + recent turns) and ~500 output tokens.

| Model | Input $/1M | Output $/1M | Per conversation |
|---|---:|---:|---:|
| gpt-5-mini | 0.25 | 2.00 | ~$0.006 ≈ **160đ** |
| gpt-5-nano | 0.05 | 0.40 | ~$0.0012 ≈ **31đ** |
| Gemini Flash-Lite | 0.125 | 0.75 | ~$0.0026 ≈ **68đ** |

Prices [S6][S7]; ≈26,000đ/USD assumed. Prompt-caching the static prefix cuts input roughly an order of
magnitude. Channel cost on Zalo: inbound is free; advisory replies carry **8 free messages per 48h
window from the customer's last interaction**, then ~**55đ/message** inside the window; a proactive
"your laundry is ready" outside the window is a **ZBS template message at ~200–300đ** (ZBS replaced
ZNS/UID tag messages on 2026-01-01) [S8][S9][S10].

**All-in marginal cost per customer conversation: under ~500đ.** Against a typical order of
120,000–150,000đ that is **~0.3–0.4% of order value.** Inference cost is not a constraint on this
decision and should not appear in the business case at all.

### 4.2 Break-even is stated in recovered orders, not in saved minutes

Labour saving is small in absolute terms: at ~20 inbound messages/day × ~2 minutes, the agent frees
~20 hours/month of a staff member — real relief inside a 2-person shop, but not a number that funds a
project. The value is elsewhere, and all four parts are order-recovery, not cost-reduction:

- an inbound message that goes unanswered during a wash cycle is a **lost order**, not a delayed one;
- status questions answered without interrupting the counter mid-cycle;
- the **12 unstaffed hours** of every day;
- the RU/EN/KO segment the shop currently cannot serve at all.

Monthly marginal cost, at ~600 conversations: ~300,000đ of inference and messaging, plus the Zone P /
Zone A isolated host that `ADR-0007` requires before public ingress. Call the envelope
**500,000–1,500,000đ/month**.

**Break-even ≈ 1–4 recovered orders per week.** Two cautions that must travel with that number:

1. The contribution margin behind it is **not measured**. `BUSINESS_TRUTH_INTAKE.md` §3 records the
   owner's ~30% processing-cost figure as `PLANNING_ESTIMATE — CHƯA ĐO` and states explicitly that
   70% profit **must not** be inferred from it. Use **4 orders/week** as the planning figure and
   measure before committing.
2. Published AI-support benchmarks are worse than vendor marketing: median tier-1 deflection 41.2%
   (top quartile 58.7%), but the **median team's true year-1 deflection is 10–15%**, and **67% of
   deployments miss their projected targets in the first six months** [S11][S12]. Plan for the low
   end. The break-even above survives it — which is the actual reason this is a GO.

### 4.3 The real cost is evidence, and it is not optional

800 eval cases are missing (300 normal-language Vietnamese + 200 adversarial + 200 frozen regression
+ 100 public corpus), each needing a case, an expected behaviour and a grader. Then 14 shadow days,
100 representative interactions, 30 real orders, a published `PUBLIC_CUSTOMER` bundle with a tested
correction workflow, a Zone P host, a provider credential, a model release pin, and a manifest signed
by three distinct actors. **That is the bill.** It is measured in engineering weeks and calendar
weeks, not in token spend, and any plan that quotes the token cost as "the cost of the AI" is lying
by omission.

---

## 5. Three corrections to the proposal as stated

### 5.1 "Connect all channels" → connect exactly one

Facebook Messenger and Google Business are inboxes a human already reads. Adding them now multiplies
the consent surface, the PDPL exposure, the adapter security contracts, and — decisively — **the
denominator of every evidence gate**, in exchange for near-zero incremental reach in a market where
Zalo is at 85%+. The canonical envelope is already provider-generic (`provider` admits `ZALO_OA`,
`TELEGRAM_SANDBOX`, `FACEBOOK_MESSENGER`), so a second channel later is an adapter, not a rewrite.

**Unify the *inbox*, not the *ingress*.** The staff console is already the single operational surface;
a second channel adds a second adapter behind the same envelope whenever it is worth it.

### 5.2 "Unified customer profiles" → this is the blocking item, and its shape decides the liability

This is the part of the proposal with real engineering content, and it is currently forbidden by a
decision the owner already made. `DEC-015` is **RESOLVED as "not yet"**, with a named reopen trigger:
*"mở lại khi có kênh liên lạc chính thức"* — reopen when an official channel exists. `DEC-013` is
**RESOLVED as ticket-only**: *"Hệ thống không lưu tên, số điện thoại hay địa chỉ của khách vãng lai.
Khi nào tiệm cần nhắn tin cho khách, đó là một quyết định mới kèm lời đồng ý."*

The proposal is therefore exactly the event that reopens `DEC-015`. The shape it must take:

> **Channel-identity-first.** A customer record is created by **the customer's own message**, which
> supplies both a provider identity and a lawful basis, with a **versioned consent string** recorded
> at that moment. It is **never** created by a staff member typing a phone number at the counter.
> A walk-in stays ticket-only until they message. Linking a ticket to a profile is an explicit,
> audited, customer-initiated act — never an inference, never a fuzzy name/phone match.

This is simultaneously the cheapest to build (the binding table already works this way), the most
defensible under PDPL (consent is contemporaneous with collection, purpose-specific, and evidenced),
and the only version that does not require inventing a consent record for people who never gave one.
The aggregate — `parties` / `contact_points` / `addresses` with consent version and retention class —
can be **designed now** and **must not be migrated** until `DEC-015` is reopened and answered.

### 5.3 "24/7 AI agent" → 24/7 acknowledgement, never 24/7 promises

Out-of-hours behaviour is not an engineering default; it is literally the second unanswered half of
`DEC-016`. The defensible envelope outside 08:00–20:00 is: acknowledge, answer from the published
corpus, report order status the deterministic system already knows, queue a request for the morning,
and escalate anything else to `REQUIRE_HUMAN`. It may never confirm a slot, quote a negotiated price,
commit an ETA, or accept an incident resolution. Note that `promised_ready_at_store` is staff-set per
item and `HUMAN_ETA_REQUIRED` covers the special-item classes — the deterministic layer already
refuses to let the model do this, which is the design working as intended.

---

## 6. Build, buy, or integrate a pre-built agent?

### 6.1 OpenClaw — **No**, for the customer path. This is not a close call.

The repository reached this conclusion twice, on internal grounds, before the external evidence
existed. Both lines of reasoning are now independently confirmed.

**The workload argument (`ADR-0003`).** The public workload requires one Concierge, ten fixed typed
tools, ≤3 model calls, ≤6 tool calls, a 20-second ceiling and a draft-or-handoff result. It explicitly
does **not** require browser, shell, filesystem, generic web, runtime plugin installation, channel
send, or multi-agent delegation. OpenClaw supplies all of those. In this workload they are not
features — **they are attack surface with no corresponding benefit**, on the exact path where
untrusted Vietnamese text arrives.

**The maintenance argument (`ADR-0004`).** Reaching zero high-severity findings required replacing four
transitive dependencies inside an embedded npm shrinkwrap, one of which changes upstream's exact pin.
Sustaining that means **maintaining a derived OpenClaw fork in perpetuity** — re-deriving on every
upstream release and every advisory, proving cross-platform byte identity, regenerating SBOM, SLSA
provenance and scan evidence each time. For a two-person laundry. `OPENCLAW-REPACK-001` is frozen.

**The 2026 external evidence, which neither ADR had.** Nine CVEs in four days; ~135,000 exposed
instances; `CVE-2026-25253` at CVSS 8.8 with a one-click RCE and two command-injection advisories
(patched in 2026.1.29); and **341 of 2,857 marketplace skills confirmed malicious — ~12% of the
registry** [S13][S14][S15][S16]. For a system whose entire premise is that customer-supplied language
must never reach authority, adopting a runtime whose extension marketplace was 12% compromised is a
**category error, not a risk trade**.

**And it would not even solve the stated problem.** OpenClaw's Zalo Bot plugin is the **Zalo Bot API,
not Zalo OA** — a different product. `ADR-0005` §2 keeps Zalo Personal automation permanently
prohibited (account-ban risk, and no lawful business identity). Integrating OpenClaw would leave the
channel problem exactly where it is.

**The one honest counter-argument, and why it fails.** The strongest case for keeping OpenClaw is
rollback. `ADR-0004` already refuted it and the refutation is correct: *"A second agent runtime that
has never produced provider-backed evidence, has never been deployed, and whose own release blockers
are unresolved is not a rollback target. It is a second thing that can fail. The honest rollback
target for a reasoning component is no reasoning component plus a human."* That degraded mode is
built, tested and evidenced — all 32 manifest cases already execute as `DETERMINISTIC_DEGRADED`.

**Where OpenClaw stays legitimate:** private owner-side work — research, drafting, reporting,
scheduling — in a **separate trust cell** with no reach into PostgreSQL, channel credentials or the
public path. `ADR-0003` §7 explicitly does not remove that, and this document does not either.

### 6.2 Buy the vertical SaaS instead? — the fair comparison, honestly stated

CleanCloud ($99 Lite / $129 Pro / $189 Grow / $335 Grow+ per month) now ships a Voice AI that takes
pickup details, confirms slots, creates orders and processes payments [S17][S18]. **If the only goal
were "answer customers", buying it and staffing it with a human would be faster and cheaper than
anything in this repository.** That deserves to be said plainly rather than argued around.

It is still the wrong buy here, for four reasons that are specific rather than generic:

1. **No Zalo OA.** It is SMS/email-first for a North American market. The channel the customers are
   actually on is not supported, which makes the comparison moot on point 1 of §2.
2. **The pricing cliff.** `bill = max(kg,1)×25,000` below 6 kg and `kg×20,000` at or above it is a
   confirmed business rule producing a deliberate discontinuity (5.9 kg = 147,500đ, 6.0 kg =
   120,000đ). No generic laundry POS encodes that, and the failure mode of "close enough" pricing is
   a wrong number handed to a customer at the counter.
3. **PDPL execution.** The repo has an executing per-class retention schedule with a disposable-payload
   store separated from an append-only ledger. A foreign SaaS adds a cross-border transfer question
   with a 5%-of-revenue penalty band attached [S4][S5].
4. **It would discard a production-grade asset.** `packages/domain` + `packages/db` already encode
   this shop's confirmed pricing, promotion, delivery, SLA and settlement rules with atomic
   mutation/event/audit/outbox semantics and a workflow-conformance pass behind them.

**Rule of thumb that survives this analysis:** buy the parts that are commodity and generic; build the
parts where being wrong costs money or trust. Channel transport and template messaging are commodity —
use the provider's. Pricing, policy, SLA, order state and consent are not — those stay here.

### 6.3 Swap the runtime for LangGraph or the OpenAI Agents SDK? — **No, and `ADR-0003` says when to revisit**

LangGraph is the most battle-tested production choice for stateful, interrupt-heavy agent workflows
[S19][S20], and if this project were starting today with no runtime, it would be a serious candidate.
It is not starting today. The Responses FSM already exists, is bounded, is typed, and has full
negative-path coverage for unknown tools, identity substitution, parallel calls, malformed arguments,
deadline exhaustion, late calls after bridge revocation, cancellation and provider ambiguity. Swapping
it would trade a small owned state machine for a large dependency, restart the security review, and
move nothing on any gate.

`ADR-0003` states its own falsification condition: revisit if requirements expand to *"broad
personal-assistant channels, browser/host tools, dynamic plugins, resumable multi-agent orchestration
or many independent agent profiles."* **The proposal as corrected in §5 does not meet it** — one
concierge, one channel, ten fixed tools. But note precisely: the four-agent team sketched in
`RESEARCH_BRIEF.md` §7 (Lead Scout / Sales & Concierge / Operations Coordinator / Retention Analyst)
**would** meet it. If that is where this is heading, it needs a **new ADR**, not a quiet expansion of
the existing runtime — and it should be proposed as one rather than arrived at by accretion.

---

## 7. How it integrates with what exists

No new architecture. Six seams already exist; the work is filling them, in this order.

```text
Zalo OA webhook
  │  authenticate (signature + replay + payload limit) — contract exists, adapter missing
  ▼
canonical inbound envelope ───────────────► durable PostgreSQL inbox + audit     [COMPLETE]
  │   deterministic STOP / suppression                                            [COMPLETE]
  ▼
contact_channel_bindings (provider, provider_user_ref) → contact_binding_id      [COMPLETE]
  │   ── §5.2: the profile aggregate hangs HERE, created by the message,
  │      never by staff typing a phone number                              [DEC-015 — reopen]
  ▼
Zone P isolated reasoning cell                                            [host not provisioned]
  │   ConstrainedAgentRuntime → Responses FSM → AgentToolBridgeSession           [COMPLETE]
  │   no channel credential · no DB · no shell · no browser · store=false
  ▼
Agent Tool Facade — 10 fixed typed operations                                    [COMPLETE]
  │   catalogResolve · orderRequestCreate · recordCustomerFacts · quoteEstimate
  │   deliveryEvaluate · capacityCheck · messageDraftCreate · publicOrderStatusGet
  │   incidentOpen · approvalRequestCreate
  │   production factory returns UnavailableAgentToolBackend  [facade.py:153 — capability flag]
  ▼
packages/domain + packages/policy — money, SLA, promotion, delivery, PDP         [COMPLETE]
  │   the model reads results; it never computes, decides, selects or sends
  ▼
draft ──► staff console review queue ──► approval ──► transactional outbox ──► sender worker
          [COMPLETE]                                   [COMPLETE]              [adapter missing]
```

Two properties of this diagram are worth stating because they are what make the whole idea safe, and
they are already true in code rather than aspirational:

- **The model is never on the money path.** It calls `quoteEstimate`; `packages/domain` computes the
  number. The 6 kg cliff survives because the model never touches it.
- **The reasoning cell holds no send capability.** It returns a draft or `REQUIRE_HUMAN`. Sending is a
  separate worker reading a transactional outbox, gated by consent and suppression state. Auto-send
  is a capability authorization (`G2`+), not a code path that can be switched on by accident.

**Do not build a second orchestration layer.** The delivery controller with lease + generation CAS
already exists (`scripts/run_delivery_loop.py`, `record_delivery_evidence.py --expected-generation`).
`CLAUDE.md` forbids wrapping it, and that prohibition is correct.

---

## 8. Sequence, by owner

### Owner-only, calendar-bound — start today, nothing engineering shortens these

| # | Action | Unblocks | Clock |
|---|---|---|---|
| 1 | Answer `DEC-016`: company-owned channel account, and out-of-hours behaviour | everything | days |
| 2 | Submit Zalo OA verification (MST `4202059758`, rep CCCD matching the licence) | `CHANNEL-ZALO-APPLY-001` → `CHANNEL-ZALO-001` | **2–8 weeks** |
| 3 | Resolve `DEC-006` (provider data use, retention, region, deletion) | **every model call, including internal** | days–weeks |
| 4 | Select a host (`DECISION-HOSTING-001`) | `SHOP-CUTOVER-001` → real orders → `G2`'s 30 | days |
| 5 | Reopen and answer `DEC-015` in the §5.2 shape | the profile aggregate | days, after #2 starts |

### Agent-buildable now — no new authority, in dependency order

| # | Item | Why it is first | External dependency |
|---|---|---|---|
| A | **Eval corpus**: 300 normal-language VI, 200 adversarial, 200 frozen regression, 100 public | the binding constraint on `G1` **and** `G2`; the only work that makes the agent *safe* rather than merely *present* | **none** |
| B | `PUBLIC-POLICY-001` — published `PUBLIC_CUSTOMER` bundle + tested correction workflow | `DEC-001`–`DEC-004` are all RESOLVED; this is writable today | none |
| C | `CHANNEL-TELEGRAM-001` | proves the adapter contract end to end, so the Zalo adapter becomes a mapping exercise the week verification lands | a sandbox bot token |
| D | Profile aggregate **design only** (`parties`/`contact_points`/`addresses` + consent version) | design is free; **the migration is not written** until `DEC-015` reopens | none (design) |
| E | `PROVIDER-TRANSPORT-001`, `MODEL-PIN-001`, then `RUNTIME-PARITY-001` | first real provider evidence; `G1` needs it | credential + `DEC-006` |

**Enqueueing any of these is a delivery-queue mutation** requiring `run_delivery_loop.py` and a fresh
generation digest. This document recommends; it does not enqueue.

### Never, on this path

Auto-sending anything that quotes money before `G4`. Customer-supplied media to an inference endpoint
(`DEC-009`: staff-supplied media only). Zalo Personal automation (`ADR-0005` §2). Relabelling a
synthetic `SKIP` as provider-backed evidence. Collapsing the Zone P boundary because one host is
cheaper — `ADR-0007` §1 already ruled: *"if cost forces one host, the correct response is to delay
public ingress, not to collapse the boundary."*

---

## 9. The strongest argument against, stated at full strength

**The shop is not trading on this system.** `SHOP-CUTOVER-001` is `BLOCKED` on a machine, a TLS
certificate from a private CA trusted on every tablet, DNS, an off-host archive in a separate failure
domain, an age key pair generated on the owner's own device, and a timed restore drill. Until that
clears, the number of real orders this system has recorded is **zero**, and `G2` requires **thirty**.

Every hour spent on the agent before cutover compounds on an empty ledger. The counter journey is
software-complete and proven through a browser against a real API and a real database — it is waiting
on physical artifacts, not on code. `DEC-027` already recorded the reasoning and it is the correct
reasoning: the deterministic console *is* the first rung of the AI ladder, not an alternative to it,
**because only a trading shop produces the evidence the next rung requires.**

So the ruthless version of this verdict: **the AI decision is GO, and it is not the next action.**
The next action is items 1–4 of §8, of which exactly one (item 4) makes the shop start trading, and
none of which are engineering. The highest-leverage engineering available in parallel is the eval
corpus — which is also the only agent work that needs no external party at all.

---

## 10. What would falsify this decision

State the disconfirming evidence in advance, so it is recognised rather than argued with:

- **Zalo OA verification is refused, or takes longer than ~8 weeks.** Then `ADR-0005`'s premise fails
  and the channel question genuinely reopens — Facebook Messenger via Meta Business Suite becomes the
  candidate, with materially worse reach.
- **The measured 30-day inbound volume after cutover is below ~50 conversations/month.** Then §4.2's
  break-even is not reachable and the honest answer is a human on a shared inbox, plus templates.
  **Measure this during the first 30 trading days** — it is cheap and it is decisive.
- **`DEC-006` resolves as "no acceptable provider data posture".** Then the entire AI ladder stops at
  the deterministic console and the console is, by itself, a good product.
- **The adversarial suite cannot reach `required_policy_violation_rate: 0`** on Vietnamese prompt
  injection against the 10-op facade. That is a hard gate, and failing it means the answer is no.
- **The proposal grows into the four-agent team of `RESEARCH_BRIEF.md` §7.** That triggers
  `ADR-0003`'s own falsification clause and requires a new ADR, not an extension of this one.

---

## 11. Sources

External figures cited above, retrieved 2026-09-18. Provider policy and pricing change; workflows must
read policy live rather than hard-coding anything from this document.

[S1] Zalo used by 85% of Vietnamese — https://vietnamnet.vn/en/zalo-used-by-85-of-vietnamese-surpassing-global-apps-2406688.html
[S2] Zalo for Business: 2026 Master Guide — https://www.salesmartly.com/en/blog/docs/zalo-for-business-guide-vietnam-2026
[S3] Zalo compliance and verification — https://www.infobip.com/docs/zalo/compliance-guidelines
[S4] Vietnam's PDPL in focus (IAPP) — https://iapp.org/news/a/vietnams-pdpl-in-focus
[S5] Vietnam's New Personal Data Protection Law (Tilleke & Gibbins) — https://www.tilleke.com/insights/vietnams-new-personal-data-protection-law-a-closer-look/
[S6] OpenAI API pricing 2026 — https://www.cloudzero.com/blog/openai-pricing/
[S7] Gemini vs OpenAI API cost comparison 2026 — https://www.solvimon.com/pricing-guides/openai-vs-gemini
[S8] ZBS Template Message (Zalo OA official) — https://oa.zalo.me/home/documents/guides/zbs-template-message
[S9] Zalo OA message-type and fee policy — https://oa.zalo.me/home/resources/news/thong-bao-chinh-sach-gui-tin-va-quy-dinh-phi-gui-tin_1433049880779375099
[S10] ZNS price list 2026 — https://www.smsthuonghieu.com/gia-zns/
[S11] ROI of AI customer service: 2026 benchmarks — https://fin.ai/learn/roi-ai-customer-service-agents-benchmarks
[S12] Ticket deflection rate benchmarks 2026 — https://happysupport.ai/blog/support-ticket-deflection-rate-benchmarks
[S13] OpenClaw: the AI agent security crisis — https://www.reco.ai/blog/openclaw-the-ai-agent-security-crisis-unfolding-right-now
[S14] OpenClaw security risks (Barracuda) — https://blog.barracuda.com/2026/04/09/openclaw-security-risks-agentic-ai
[S15] OpenClaw malicious skills — https://blog.cyberdesserts.com/openclaw-malicious-skills-security/
[S16] OpenClaw security architecture and hardening — https://nebius.com/blog/posts/openclaw-security
[S17] CleanCloud pricing — https://cleancloudapp.com/pricing
[S18] CleanCloud AI voice for laundromats — https://cleancloudapp.com/ai-voice-laundromats-and-dry-cleaners
[S19] Best AI agent SDKs compared 2026 — https://www.requesty.ai/blog/best-ai-agent-sdks-compared-2026-langchain-crewai-openai-anthropic-google
[S20] Agentic AI frameworks 2026: production comparison — https://uvik.net/blog/agentic-ai-frameworks/

Internal sources are cited inline by path and are authoritative over this document wherever they
disagree with it.
