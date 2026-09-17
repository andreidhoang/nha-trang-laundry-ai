# Customer Support & Acquisition Specification v1

**Issued:** 2026-09-17
**Status:** `DRAFT — NOT OWNER-APPROVED`. This document authorizes no implementation, no capability,
no channel connection and no send. It resolves no decision. It is deliberately **not** listed in
`context/CONTEXT_MAP.yaml`, following the precedent of `CUSTOMER_MEMORY_SPEC_V1.md`: a draft
specification is not an approved specification and must not enter authoritative context assembly.
**Method:** written against measured code, not against documentation. Every structural claim carries
a file reference and was verified on the tree at the time of writing.
**Scope:** how this shop answers customers daily, how it acquires them, and how AI integrates into
both without violating a single existing invariant.
**Supersedes nothing.** It composes `AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`, `CHANNEL_ADAPTER_SPEC_V1.md`,
`PUBLIC_CUSTOMER_POLICY_SPEC_V1.md`, ADR-0003, ADR-0004 and ADR-0005 at the seams they leave open.

---

## 0. What this specification authorizes

Nothing.

It is written because the shop is about to run a customer-facing operation and the integration
surface between the parts that exist has never been written down in one place. Its purpose is to
make the next implementation item obvious and the next defect impossible, not to permit anything.

Where this document and a machine-readable contract under `specs/contracts/` disagree, **the contract
wins and CI must report drift**. Where it and `delivery/WORK_QUEUE.yaml` disagree, the queue wins.

---

## 1. The problem, stated as a loop

The business needs customers. The gate ladder needs evidence. **Both are produced by the same act.**

```
      the shop trades on this software
                 │
                 ├──► real orders ──► G2's "30 real orders" ──► the AI ladder opens
                 │                     (specs/evals/eval-manifest-v1.yaml: shadow_exit)
                 │
                 └──► contribution margin measured ──► paid acquisition becomes rational
                                (SHOP-INSTRUMENT-001, BLOCKED, 4–6 weeks of physical measurement)
```

This is why "operate the console" and "build the AI" are not competing priorities. `DEC-027` already
found this: R1 is the deterministic staff console precisely because **only a trading shop produces
the evidence the agent gates spend.**

The corollary governs everything below: **support quality is an acquisition input, not a cost
centre.** A laundry's conversion rate is dominated by reply latency and by whether the first answer
was correct. Both are support properties.

---

## 2. Four invariants, and the function they imply

These are not new. They are extracted from `AGENTS.md`, `CLAUDE.md` and ADR-0002/0003 so that §4 can
be checked against them mechanically.

| | Invariant | Where it already lives |
|---|---|---|
| **I1** | **Authority.** Deterministic domain code computes money, policy, SLA, state and permission. The model drafts language and disambiguates wording. | `packages/domain`, `packages/policy`, ADR-0002 |
| **I2** | **Irreversibility.** Automate what is reversible. A human owns what is not: pressing send, making a promise, obtaining permission. | `manual_send_attestations`, ADR-0003 §3 |
| **I3** | **Unknown is a value.** Missing config, stale policy, unmappable provider behaviour and an unasked question all have a representation: `REQUIRE_HUMAN`, `NOT_SUPPORTED`, `UNKNOWN`, `HELD_REQUIRE_HUMAN`. | `packages/policy/.../decision.py`, `domain/consent.py` |
| **I4** | **Evidence, not assertion.** A capability is authorized by a signed, unexpired manifest. A feature flag never authorizes anything; it only fails closed. | `delivery/GATE_REGISTRY.yaml`, `release-gate-manifest-v2.schema.json` |

From these, the only automation rule this specification needs:

```
may_automate(action) =
      reversible(action)
  AND authority_for(action) is deterministic
  AND capability_of(action) is AUTHORIZED by a current signed manifest
```

All three conjuncts. Any one false means a human acts, or nothing happens. **`send` is never
reversible, so `send` is never automatable below G2, and only for the capability the manifest names.**

---

## 3. Five planes, and the write-direction rule

```
┌─ PRESENCE PLANE ───────────────────────────────────────────── outside the software ──┐
│  Google Business Profile · listicles · shop front · counter QR · receipt QR           │
│  hotel front-desk QR · Zalo OA profile                                                │
└───────────────────────────────────────────────────────────────────────────────────────┘
                                   │ produces an inbound contact
                                   ▼
┌─ RECEPTION PLANE ─────────────────────────────── gated: G1 then G2 · PARTLY BUILT ───┐
│  channel adapter → webhook_events (persist + dedupe) → opt-out classification         │
│  → policy decision point → bounded runtime → 10-op Tool Facade → DRAFT                │
│  → human review (#/shadow) → approval → manual_send_envelope → attestation            │
└───────────────────────────────────────────────────────────────────────────────────────┘
                                   │ reads from / writes through
                                   ▼
┌─ RECORD PLANE ───────────────────────────────────────── BUILT · the authority ───────┐
│  counter_tickets · order_requests · quotes → quote_revisions → quote_acceptances      │
│  orders · delivery_legs · order_settlements · customer_incidents · audit_events       │
│  every mutation through commit_material_change (row + event + audit + outbox)         │
└───────────────────────────────────────────────────────────────────────────────────────┘
                                   │ emits
                                   ▼
┌─ EVIDENCE PLANE ─────────────────────────────────────── PARTLY BUILT · 5% ───────────┐
│  acquisition_source on every order · eval suites · gate manifests · telemetry         │
└───────────────────────────────────────────────────────────────────────────────────────┘

┌─ ASSIST PLANE ───────────────────────── UNGATED · UNBUILT · private trust cell ──────┐
│  prospect ranking · route sequencing · pre-visit brief · POST-VISIT DEBRIEF           │
│  follow-up clock · weekly channel scoreboard                                          │
│  reads: public data, the owner's own notes, EVIDENCE PLANE read models                │
│  writes: spreadsheets under templates/ ONLY                                           │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 The write-direction rule

> **The assist plane never writes to the record plane, never holds a channel credential, and never
> names a recipient. The reception plane never writes money, state or policy — it writes drafts and
> requests. Only the record plane is an authority, and only a human or an authorized capability
> causes a send.**

This one rule is the whole integration contract. Everything in §4 is a mechanical consequence of it.

---

## 4. The seams

`WORKFLOW-CONFORMANCE-001` established the finding this section is built on:

> *"the previous two audit rounds were organised by layer, and a layer audit cannot find a defect
> that lives between two correct layers. A workflow audit can, and this one did on its first pass."*
> — `context/PROJECT_CONTINUATION.md`, 2026-09-10

So this specification is organised by **seam**, not by layer. Each seam states the two sides, the
contract that binds them, the failure it exists to prevent, and the test that would catch that
failure. "Seamless" is not a feeling; it is this table being complete.

### S1 · Channel adapter → inbox

**Contract.** Authenticate and replay-check before any processing; persist to `webhook_events` and
deduplicate on `(provider, channel_account_id, provider_event_id)` **before any model call**;
acknowledge only after durable acceptance; never wait on a model response.
**Built.** `packages/db/src/nha_trang_laundry_db/inbox.py:73`, migration `0007` lines 33–60, and the
table is append-only by trigger.
**Failure prevented.** A provider retry producing a second reply; a slow model turning an
acknowledgement into a timeout and then a duplicate.
**Test.** Same `provider_event_id` twice with identical payload → `DUPLICATE`; with a different
payload → a row in `inbox_replay_conflicts`, never silent acceptance.

### S2 · Inbox → opt-out classification

**Contract.** `evaluate_opt_out` runs **before model dispatch**, against a published, unexpired
registry. A missing or stale registry yields `POLICY_UNAVAILABLE_BLOCKED`, not a guess.
**Built.** `packages/domain/src/nha_trang_laundry_domain/consent.py`; the disposition is a column on
`webhook_events`, so it is recorded as of ingress and cannot be re-derived later from a mutated
registry.
**Failure prevented.** A customer typing `DỪNG` and receiving a marketing reply because the STOP was
classified after the reasoning step.
**Test.** Unicode/casefold/`/`-prefix normalization; an expired registry blocks; an invalid published
pattern raises rather than defaults.

### S3 · Policy decision point → runtime

**Contract.** The PDP is typed and fail-closed, and capability status comes from a signed manifest —
never from a boolean in the environment. The worker refuses to start if any external-capability flag
is true.
**Built.** `packages/policy`, `delivery/GATE_REGISTRY.yaml`, `capability-status-v1.schema.json`.
**Failure prevented.** The classic "we enabled it in staging and it went to production" — impossible
here because deploying code never enables a capability.
**Test.** Every one of the 13 capabilities reports `NOT_AUTHORIZED` with no manifest present.

### S4 · Runtime → Tool Facade

**Contract.** Exactly ten operations cross this boundary
(`packages/contracts/src/nha_trang_laundry_contracts/tool_registry.py:20-29`). Each declares a
side-effect class of `NONE` or `REVERSIBLE_WRITE` — **there is no irreversible class, by
construction**. The model never supplies a contact binding, an order identifier or a price.
**Built and deliberately unwired.** `get_agent_facade_service()` returns
`AgentFacadeService(UnavailableAgentToolBackend())` at `apps/public-agent-tools/.../facade.py:152`.
A real backend exists. This is a capability-authorization fact, not an engineering gap.
**Failure prevented.** Tool sprawl. The day someone adds an eleventh operation that sends, every
guarantee above becomes a comment.
**Test.** The registry is hash-pinned; an operation absent from `agent-tools-v1.openapi.yaml` is not
routable; `authorize_operation` refuses an operation outside the bound runner's claims.

> **§10 of this document states the strongest conclusion available about this seam: nothing in this
> specification requires an eleventh tool.**

### S5 · Tool Facade → domain

**Contract.** The facade computes nothing. `quoteEstimate` calls the pricing engine; `deliveryEvaluate`
calls the delivery engine; `capacityCheck` returns a **non-binding advisory and never reserves**.
Range-priced services refuse to name a number. The 6 kg tier boundary is applied, never smoothed.
**Built.** `packages/domain`, exercised by `apps/public-agent-tools/.../backend.py`.
**Failure prevented.** A plausible-sounding wrong price. This is the zero-tolerance G2 defect class.
**Test.** `deterministic_domain_suite` requires `maximum_wrong_monetary_values: 0` at a required pass
rate of 1 (`specs/evals/eval-manifest-v1.yaml`).

### S6 · Draft → human attestation

**Contract.** A draft is content plus a `rendered_hash` and a `snapshot_hash` over the exact resource
version it was generated from. A human approves **that hash**. If the underlying resource moved, the
binding is stale and preparation fails.
**Built.** `manual_send_envelopes` / `manual_send_attestations`, migration `0011`;
`_require_approved_transactional_binding` at `packages/db/.../manual_sends.py:275`.
**Failure prevented.** Approve one price, send another — the oldest defect in human-in-the-loop
systems.
**Test.** Mutate the quote between approval and preparation → `manual-send content binding is stale`.

### S7 · Attestation → egress

**Contract.** Suppression is re-checked **inside the dispatching transaction**, under a
transaction-scoped advisory lock keyed by `(contact_binding_id, channel)` taken by both the ingress
writer and the egress guard — because on a contact's first STOP there is no row to lock.
**Built.** `packages/db/src/nha_trang_laundry_db/consent_egress.py`. The module is deliberately not a
sender; it takes the caller's cursor so the check cannot drift outside the send transaction.
**Failure prevented.** The claim-to-send race: a STOP arriving after an outbox row is claimed but
before dispatch. The eval manifest counts a suppression miss as a zero-tolerance G2 defect.
**Test.** `packages/evals/tests/test_synthetic_stop_race.py`.

### S8 · Egress → provider

**Contract.** One `channel_send_receipts` row per attempt, including failures. `TIMEOUT` and
`TRANSPORT_ERROR` may never be recorded as settled: the CHECK constraint forces
`reconciliation_state` into an `UNKNOWN`/`CONFIRMED` set, and leaving `UNKNOWN` requires
`resolved_by IN ('PROVIDER_CONFIRMATION','HUMAN_DECISION')` with an actor and a timestamp.
**An automatic retry can never satisfy that constraint.** That is the point.
**Built.** Migration `0020` lines 87–112.
**Failure prevented.** The duplicate send, which is the other zero-tolerance G2 defect.
**Test.** A timeout followed by a retry attempt must produce a second receipt and an unresolved
reconciliation, never a silent second delivery.

### S9 · Order → attribution

**Contract.** `acquisition_source` is attested by a staff member, written once at creation, never
updated, never inferred by a model, and `UNKNOWN` is a first-class value reachable without friction.
**Built.** `ACQUISITION-ATTRIBUTION-001`, COMPLETE 2026-09-08.
**Failure prevented.** A required field with no honest option gets filled with a lie, and the
resulting channel report is worse than no report.
**Test.** No agent-reachable operation in the hash-pinned contract can read or write it.

### S10 · Spreadsheet → database

**Contract.**

> **The sheet holds prospects. The database holds customers. A row crosses when a person agrees.**

An organisation researched from public listings lives in `templates/accounts.csv` (184 organisations,
125 tier A inside the 2 km free-delivery radius, **no person's name in any row**, by `DEC-015`). A
person enters PostgreSQL only via a counter ticket or a channel binding.
**Built as a decision, not as code.** `DEC-013`/`DEC-015` resolved that no customer-record layer
exists; `parties`, `contact_points` and `addresses` remain deliberately unbuilt.
**Failure prevented.** Creating a personal-data processing record for a party nobody has asked —
the exact exposure `Luật 91/2025` Điều 9 and `Nghị định 356/2025` price at up to 5% of turnover.
**Test.** Nothing under `scripts/`, `packages/` or `apps/` parses the prospect sheets. Keep it that
way and assert it.

### S11 · Assist plane → everything

**Contract.** Read-only with respect to the record plane. No channel credential. No recipient
selection. No send client. Output is a spreadsheet row or a document a human reads.
**Unbuilt.** ADR-0004 §5 preserves the slot: *"the separately isolated Private Owner OpenClaw trust
cell is out of scope and is not removed."* That is where a general-purpose harness belongs.
**Failure prevented.** A back-office convenience quietly acquiring production authority.
**Test.** The assist cell's egress allowlist contains no provider messaging endpoint; a test asserts
it cannot resolve one.

---

## 5. Daily support operations

### 5.1 The five loops

| Loop | Trigger | System state today |
|---|---|---|
| **Reception** | a stranger messages | **no channel connected.** `FEATURE_PUBLIC_CHANNELS_ENABLED = "false"`, asserted by two contract tests |
| **Counter** | a walk-in | **built and proven** — ticket → quote → attested acceptance → order |
| **Production** | intake accepted | **built** — `QUEUED` → `IN_PROCESS` → `QUALITY_CHECK` → `READY_AT_STORE` |
| **Exit** | goods handed back | **built for exact-payment self-collection**; delivery settlement resolved by `DEC-023` |
| **Follow-up** | order completed | **manual only.** Automation is `MARKETING_FOLLOWUP`, which requires G1+G2+G3+G4 |

### 5.2 Triage: what the assistant may do with an inbound message

Three dispositions, and the assignment is **deterministic policy, never a model judgement**:

| Disposition | Meaning | Authorized when |
|---|---|---|
| `AUTO` | composed and sent with no human in the path | only `LIST_PRICE_INFO`, and only at G2 (`GATE_REGISTRY.yaml`) |
| `DRAFT` | composed, queued in `#/shadow`, a human approves the exact hash and sends | G1 |
| `HANDOFF` | the assistant states it cannot answer and names when a human will | always available, and the default |

**`HANDOFF` is the default for every capability, and the assignment is computed by the PDP before the
model is invoked.** A model that decides for itself whether it is confident enough to answer has
already taken an authority decision. `AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` §15 governs abstention; the
`normal_language_suite` requires a safe-abstention rate of **0.98 on ambiguous cases**.

### 5.3 Response-time policy

`BUSINESS_TRUTH_INTAKE.md` §4 commits to **5–10 minutes, 08:00–20:00**. `DEC-016` establishes that
one person — Hoài Ngọc, who is also the legal representative, a co-owner and the name on the sign —
answers inbound, in a shop where two staff cover twelve hours and also wash, fold and deliver.

**This is the strongest engineering argument in the project for the `DRAFT` disposition**, and it is
also why `DEC-016`'s two remaining questions must be answered *before* a channel is connected:
connecting a channel delivers strangers to a phone whose account ownership is undecided.

Out of hours, the contract is: **acknowledge, capture, promise nothing, name the hour a human will
reply.** Silence and a false promise are equally bad. The exact sentence is owner-owned — see §15.

### 5.4 What the assistant may never say

Independently of gate state, because these are authority claims, not phrasings:

- a price for a range-priced service (the range is published; the number inside it is not a fact);
- a confirmed pickup or delivery slot — AI-confirmable capacity is **0 kg/day**
  (`BUSINESS_TRUTH_INTAKE.md` §2), so every slot is a request pending human confirmation;
- a promotion that is not a current, in-force rule — note `templates/promotion-service-rules.csv`
  marks `category leather` as `UNCLEAR / HUMAN_CONFIRM`;
- fault, remedy, rewash, credit or compensation — `incidentOpen` stores an allegation and decides
  nothing;
- anything about an order without verified channel ownership — an `UNVERIFIED` binding carries no
  access to order status, quotes, or any customer-specific fact (migration `0020` header).

And one it must always say: **that it is an automated assistant.** Since `Luật 134/2025` Điều 11 this
is a statutory disclosure, not a courtesy. It is therefore compliance surface and must be
contract-tested like a contract — a lesson the repository already learned at
`apps/web/src/screens/assistant.js:487`, where a factual claim about streaming shipped with no test
behind it.

---

## 6. Acquisition

### 6.1 The permitted surfaces, and why the others are closed

Every mass-outbound path is closed, by three independent authorities. This is a finding, not a
policy choice, and it is recorded in `docs/CLIENT_ACQUISITION_EXECUTION_2026-08.md` §7.1:

| Closed path | Closed by |
|---|---|
| advertising SMS or calls from `0382 318 492` | **`NĐ 91/2020` Điều 13 khoản 8** — advertising requires an allocated `tên định danh`; a plain phone number may not be used |
| cold DM on Zalo | **the platform** — Zalo OA has no cold DM; broadcast reaches followers only |
| bulk messaging any list | **this system** — `MarketingDeliveryRepository.hold_if_not_authorized` holds every claimed marketing send with `MARKETING_AUTHORIZATION_UNAVAILABLE` (`marketing_delivery.py:66`) |
| posting to Russian Telegram groups | group rules; direct posting loses the account |
| cold-calling published business numbers | **unresolved** — whether a B2B service approach to a published business number is "quảng cáo" under `NĐ 91/2020` has no definitive answer. Default: do not. |

What remains open, and is sufficient:

1. **Google Business Profile** — the customer searches. Note messaging via Google died 2024-07-31;
   what remains is one Chat link to WhatsApp **or** SMS, and `Services` (not `Products`) is the
   correct surface for a laundry. Reviews may be requested, never incentivised.
2. **Local listicles** — the real local SEO layer; ≥7 competitors are present and this shop is not.
3. **In-person B2B** — 125 tier-A organisations within 2 km, already ranked.
4. **Partner referral** — the hotel introduces the guest, so the guest initiates. WashInCloud pays
   25% of revenue; verify that figure with two or three receptionists before matching it.
5. **The Zalo OA follow** — everything downstream opens once someone follows.

### 6.2 The growth primitive

> **In this market the acquisition primitive is a scan, not a send.**

Before a follow, no outbound surface is open at any price. After a follow, the 48-hour window, the
eight free messages, broadcast and the whole nurture cadence are open and free. Therefore the metric
the system optimises is **follow rate per presence surface**, and the instrumented surfaces are the
counter QR, the receipt QR, the leaflet QR and the per-partner QR.

The per-partner QR needs an organisation reference on the order — an organisation, never a person.
`PARTNER_FRONT_DESK` already ships as an `acquisition_source` value; the reference itself waits on
one physical fact, the exact string painted on the shop front, because Google requires the profile
name to match the storefront and renaming after verification means verifying again.

### 6.3 The funnel as a state machine

```
PROSPECT ──(a human asks, face to face)──► PERMITTED ──(wording + version recorded)──► CONTACT
   │                                                                                     │
   │ templates/accounts.csv                        templates/contacts-consent.csv        │
   │ organisations only                            first row that names a person         │
   └─────────────────────────── the spreadsheet ────────────────────────────────────────┘
                                                                                         │
                                                                    (a small paid pilot) │
                                                                                         ▼
                                            PostgreSQL ◄────────────────────────── CUSTOMER
                                            counter ticket or channel binding             │
                                                                                          ▼
                                                                                    FIXED ROUTE
```

**Three transitions a human must perform and may not delegate: asking permission, making a promise,
pressing send.** Each is irreversible by I2.

---

## 7. Nurture and conversion

### 7.1 The integration proof: every message maps to an existing kind

`channel_send_receipts.message_kind` (migration `0020` lines 44–48) already enumerates the taxonomy.
Nurture is correctly integrated **if and only if** every message the business wants to send maps to
one of these nine kinds — or is declared unsendable. It does:

| Business message | `message_kind` | Path today |
|---|---|---|
| welcome / intake prompt | `INTAKE_FACT_REQUEST` | draft → human |
| answer a price question | `LIST_PRICE_INFO` | draft → human (the only `AUTO` candidate, at G2) |
| "we still need X to quote" (+2h) | `INTAKE_FACT_REQUEST` | draft → human |
| present an approved quote (+24h) | `APPROVED_QUOTE_PRESENTATION` | requires an approval |
| present a confirmed slot | `APPROVED_SLOT_PRESENTATION` | requires an approval |
| order status | `ORDER_STATUS` | verified binding only |
| acknowledge a complaint | `INCIDENT_RECEIPT` | stores the allegation, decides nothing |
| close the loop (+72h) | `FREE_FORM_TRANSACTIONAL` | draft → human |
| review request · seasonal offer · B2B cadence | **`MARKETING`** | **held — see §7.3** |

> **No new message kind is required.** The taxonomy anticipated this work. That is what a good schema
> looks like two months later.

### 7.2 B2C: a reactive cadence inside the platform window

The customer messaged first, so this is lawful and platform-permitted. The cadence from
`SALES_AND_NURTURE_PLAYBOOK.md` — immediate, +2h, +24h, +72h close-loop — fits inside Zalo's 48-hour
free window and inside the 7-day OpenAPI consultation window.

**The window is a deterministic computation, never a model judgement.** `messaging_window` is already
a typed value with three states — `IN_WINDOW`, `TEMPLATE_APPROVED`, `HELD_REQUIRE_HUMAN` — recorded
on the receipt and declared as `messaging_window_fails_closed` in `CHANNEL-ZALO-001`'s required
evidence. See §9.1 for the one input it still lacks.

The close-loop message matters more than it looks. It ends the thread on the shop's terms and leaves
a re-open key:

> Em xin đóng yêu cầu này để không làm phiền. Khi cần, anh/chị chỉ cần nhắn **"ĐẶT GIẶT"** để mở lại.

### 7.3 B2B: permission-first, and why it does not run through this system in R1

The cadence — Day 0 offer, Day 2 qualification, Day 5 pilot slot, Day 10 close, Day 30 seasonal —
is `MARKETING` in every message. Measured today, **a marketing message cannot leave this system by
any path**, and it is refused twice, independently:

1. the automated outbox holds it with `MARKETING_AUTHORIZATION_UNAVAILABLE`
   (`marketing_delivery.py:66`), because R1 has no positive consent projection;
2. `ManualSendRepository.prepare` raises `manual marketing send is not supported` — the repository
   accepts only `purpose == "TRANSACTIONAL"` (`manual_sends.py:75`), even though the table's CHECK
   admits `MARKETING`.

That double refusal is correct and this specification does not lift it. **So in R1, B2B nurture
happens on the owner's own phone, and the system's role is to draft and to remember, not to send.**

When it does become representable, the order is fixed and the narrow step comes first:

> **A human-attested marketing send with recorded consent is not a capability authorization.** Its
> `authorization_source` is `HUMAN_APPROVAL`, not `CAPABILITY_AUTHORIZED`, and the gate ladder's
> `permits_automatic_send` does not govern it — G1 *is* the human-approval stage. What blocks it is
> the absence of a positive consent projection and an owner decision, **not G4**. `MARKETING_FOLLOWUP`
> at G4 is the *automated* follow-up, which is a different thing and much further away.

Two requirements bind whoever lifts the narrowing:

- the egress suppression guard must run **in the prepare transaction**, and be re-checked at
  attestation, because unlike an automated send the human-send window is unbounded in time;
- `suppression_entries.purpose` and `consent_events.purpose` are both CHECK-constrained to exactly
  `'MARKETING'`. `Luật 91/2025` Điều 9 requires consent **expressed per purpose**, so the day the
  shop wants a review request to be separable from a promotional offer, that enum grows. Until then,
  one purpose is honest: everything marketing-shaped shares one consent.

### 7.4 Suppression precedence

Absolute, and evaluated at both ends:

```
STOP at ingress  >  consent state  >  messaging window  >  cadence schedule  >  content
```

`NĐ 91/2020` Điều 13 khoản 4 requires an immediate stop with **no grace period**. The schema already
encodes the right default: an absent `suppression_entries` row means *unknown*, and unknown blocks.
`CLEAR` exists only as a deliberate, audited act.

### 7.5 What actually converts

Ranked by expected effect for a shop of this size, and each one is a system property:

1. **Reply latency** inside 08:00–20:00 — the dominant B2C variable, and the reason `DRAFT` exists.
2. **A quote in the first reply** — a range, with the honest caveat that the final price follows
   actual weight.
3. **The 6 kg tier boundary surfaced, never smoothed** — *"you are at 5.4 kg; at 6 kg the rate
   changes"* is a genuine service moment, and the deterministic engine already knows it.
4. **Selling the differentiated line, not weight laundry.** Leather, shoes, suits and bags are
   2–3× cheaper than the nearest published competitor and rest on machines the shop actually owns;
   weight laundry is a commodity at 25–30,000đ/kg. The advantage holds on list price, so it does not
   expire with a promotion.
5. **A paid pilot, small, with no supplier switch required** — the only B2B ask that converts on a
   first visit.

---

## 8. The assist plane

The one tier that is buildable now, is gated by nothing, and is not built.

| Agent | Input | Output | Must never |
|---|---|---|---|
| **Prospect ranker** | public listings | rows in `templates/accounts.csv` | contact anyone; store a person |
| **Route sequencer** | the day's account list | an ordered walk | promise a time |
| **Pre-visit briefer** | one account | one page: who they are, what to ask | state anything as a promise |
| **Post-visit debriefer** | a 60-second Vietnamese voice note | a row in `templates/interactions.csv` | invent a fact not in the note |
| **Follow-up clock** | the cadence config | a due list and a draft | send |
| **Scoreboard** | `acquisition_source` + `pilots-orders.csv` | contribution by channel | recommend spend before `SHOP-INSTRUMENT-001` |

**The debriefer is the highest-value automation in the whole system** and the least obvious, because
the step that actually fails in small-business sales is not the message — it is the CRM write after
the conversation. Nobody does it. A machine that does it costs nothing and touches no gate.

**One honest constraint.** A voice note about a sales visit names a third party and their role, which
is personal data. So the assist plane is *not* free of `DEC-006`. Either run it on a local model on
the shop's Mac, or scope `DEC-006` narrowly to this use. It is a far cheaper version of that decision
than customer message history, but it is the same decision and must not be skipped by calling this
tier "internal".

---

## 9. Concrete deltas

Everything else in this document describes what exists. This section is what does not.

### 9.1 `messaging_window` has a type, a column and an obligation — but no input

Measured: `MessagingWindowState` is defined at
`packages/contracts/.../channel_envelope.py:91`, the column exists at migration `0020:56`, the
receipt contract enumerates it at `channel-outbound-receipt-v1.schema.json:79`, and
`CHANNEL-ZALO-001` declares `messaging_window_fails_closed` as required evidence.

**Nothing computes it.** `ChannelSendReceiptRepository.record_attempt` writes whatever the caller
passes (`channel.py:218`). Computing it requires *the timestamp of the contact's last interaction*,
and:

- `webhook_events` carries `contact_binding_id` and `received_at` and is append-only, so the value
  **is derivable** — `MAX(received_at)` per `(contact_binding_id, channel)`;
- but the only index is partial on `(processing_status, received_at) WHERE processing_status =
  'DISPATCH_PENDING'` (`0007:54`), so that query degrades to a scan over a ledger that only grows.

**Requirement.** `CHANNEL-ZALO-001` owns this. It must add a supporting index or a derived
projection, and — this is the part that is easy to get wrong — **"interaction" is the provider's
definition, not this system's.** Zalo counts following the OA, clicking a menu, a CTA or a widget,
commenting on a post, and calling, in addition to messaging. Per ADR-0005 §5, each is verified
against current official documentation at implementation time; **any interaction type the adapter
cannot map fails closed to `HELD_REQUIRE_HUMAN`.** A window computed from messages alone is wrong in
the direction that sends messages the shop is not entitled to send.

### 9.2 The cadence is code-shaped but belongs in configuration

`SALES_AND_NURTURE_PLAYBOOK.md` carries the intervals as prose and marks itself *"template — chỉ dùng
sau khi pricebook, zone, capacity và policy được chủ tiệm phê duyệt."* They are therefore unapproved.

**Requirement.** The cadence becomes a published, versioned, digest-checked configuration — the same
shape the pricebook already uses, which sits at the top of the source-of-truth order in
`specs/README.md`. Sketch, to be created by the implementing item, not by this document:

```yaml
# specs/contracts/nurture-cadence-v1.yaml  (PROPOSED)
version: "1"
published_at: <RFC3339>
expires_at: <RFC3339>          # an expired cadence fails closed, like the opt-out registry
audiences:
  B2C_INBOUND:
    trigger: INBOUND_MESSAGE
    steps:
      - at: PT0S     kind: INTAKE_FACT_REQUEST     disposition: DRAFT
      - at: PT2H     kind: INTAKE_FACT_REQUEST     disposition: DRAFT   condition: FACTS_MISSING
      - at: P1D      kind: LIST_PRICE_INFO         disposition: DRAFT   condition: QUOTE_VALID
      - at: P3D      kind: FREE_FORM_TRANSACTIONAL disposition: DRAFT   terminal: true
  B2B_PERMITTED:
    trigger: RECORDED_PERMISSION
    requires_consent_purpose: MARKETING
    steps: [ P0D, P2D, P5D, P10D, P30D ]   # every step kind: MARKETING → held in R1
```

Two properties matter more than the values: **an expired cadence fails closed** (the opt-out registry
already works this way), and **a step never overrides §7.4's precedence.**

### 9.3 Nothing else is missing

No new table, no new agent operation, no new trust boundary, no new gate. Stated plainly because the
tempting conclusion from a document this long is the opposite one.

---

## 10. Contracts

**No new agent-reachable tool is required by this specification.** The ten operations are sufficient
for every support and nurture behaviour described above. Recorded explicitly so that a future reader
who wants an eleventh has to argue against a written finding rather than against a habit.

| Contract | Change |
|---|---|
| `agent-tools-v1.openapi.yaml` | **none** |
| `channel-inbound-envelope-v1.schema.json` | **none** |
| `channel-outbound-receipt-v1.schema.json` | **none** — `messaging_window` already typed |
| `canonical-enums-v1.json` | none in R1; grows only if `consent_events.purpose` splits (§7.3) |
| `nurture-cadence-v1.yaml` | **new, proposed** (§9.2), owner-published, versioned, expiring |

---

## 11. Test obligations

Beyond the standing gates, and none satisfiable by a green generic suite:

1. **S1** duplicate `provider_event_id` → `DUPLICATE`; differing payload → a replay conflict row.
2. **S2** STOP classified before dispatch; expired registry blocks; the disposition recorded at
   ingress is not re-derivable from a later registry.
3. **S3** all 13 capabilities `NOT_AUTHORIZED` with no manifest; the worker refuses to start with any
   external-capability flag true.
4. **S4** the eleventh operation does not exist: an operation absent from the hash-pinned contract is
   not routable, and no agent-reachable operation can read `acquisition_source`.
5. **S5** no wrong monetary value on the full deterministic suite; a range-priced service refuses to
   name a number; the 6 kg boundary is applied exactly.
6. **S6** mutate the resource between approval and preparation → stale binding refusal.
7. **S7** STOP arriving inside the claim-to-send window holds the send, on a contact with **no prior
   suppression row** — the advisory-lock case, not the row-lock case.
8. **S8** a timeout followed by a retry produces a second receipt and an unresolved reconciliation;
   no code path can set `CONFIRMED_*` without `resolved_by` and `resolved_at`.
9. **S9** `acquisition_source` is write-once and `UNKNOWN` is reachable from the console without a
   warning that pressures staff away from it.
10. **S10** nothing under `scripts/`, `packages/` or `apps/` parses the prospect sheets.
11. **S11** the assist cell cannot resolve a provider messaging endpoint.
12. **§7.3** `ManualSendRepository.prepare` refuses `MARKETING`; a test asserts the refusal, so
    lifting it becomes a deliberate, reviewed act rather than a diff nobody noticed.
13. **§5.4** the AI-disclosure string is contract-tested, in both the console and any channel
    template — it is a statutory disclosure, not copy.
14. **§9.1** a window that cannot be computed yields `HELD_REQUIRE_HUMAN`, never `IN_WINDOW`.

---

## 12. Gate and capability mapping

| This specification's section | Authorized by | Status |
|---|---|---|
| §5.1 counter, production, exit loops | no gate — deterministic staff operation | **buildable / built** |
| §8 assist plane | no gate; needs `DEC-006` scoped | **buildable, unbuilt** |
| §5.2 `DRAFT` disposition | `G1_INTERNAL_SHADOW_READY` | not started; `AGENT-002` carries the evidence |
| §5.2 `AUTO` for `LIST_PRICE_INFO` only | `G2_PUBLIC_ASSISTED_ENTRY` | needs 14 Shadow days, 100 interactions, **30 real orders** |
| §5.2 `AUTO` for FAQ, intake, status, SLA | `G3` | after 200 human-reviewed sends and 14 capability-days |
| §7.3 automated `MARKETING_FOLLOWUP` | `G4` | 30 clean days, 100 eligible cases, measured economics |
| §7.3 **human-attested** marketing send | **no gate** — `HUMAN_APPROVAL`, not a capability | blocked on a consent projection + an owner decision |

The last row is the one worth re-reading. It is the only place in this document where something
useful is much closer than the gate ladder suggests.

---

## 13. Rollout sequence

**Owner-only. Nothing engineering does shortens any of these.**

1. **Answer `DEC-016`'s two open questions** — whose account the OA is, and the out-of-hours
   sentence. Highest leverage in the project: OA business verification has an external lead time
   measured in weeks, and `CHANNEL-ZALO-APPLY-001` cannot start without it.
2. **Photograph the shop front.** Gates every printed asset and the Google profile name.
3. **Scan the three Zalo documents before creating the OA** — full-page business licence with seal,
   the legal representative's ID, and the CVXT template downloaded from OA Manager itself. The
   14-day submission clock starts at OA creation and a missed resubmission locks the account
   **permanently**.
4. **Claim the Google profile**, then request real reviews with Google's own QR. Never incentivised.
5. **Host, TLS, first staff accounts, signed gate manifest** → `SHOP-CUTOVER-001` → real orders.
6. **`DEC-006` + `PROVIDER-ACCESS-001`** → the first model call this project has ever made.
7. **`SHOP-INSTRUMENT-001`** — 4–6 weeks of physical measurement. Until it lands, every growth
   number is traffic, not profit, and no advertising spend is rational.

**Engineering, in parallel, requiring no decision:** the eval corpora (`EVAL-CORPUS-001` at 669 of
1,300 cases, `EVAL-LANGUAGE-CORPUS-001`, `EVAL-PUBLIC-CORPUS-001`), `PROVIDER-TRANSPORT-001`,
`MODEL-PIN-001`, `PUBLIC-POLICY-001`, and the §8 assist plane.

That split is the schedule. Every gate spends evidence; the evidence base is at ~5%; there are
exactly two evidence streams — the eval corpus and real orders — and only one of them is movable by
an agent.

---

## 14. What this specification deliberately does not build

| Not built | Why |
|---|---|
| a CRM in PostgreSQL | `DEC-015` decided against it; the sheets are the right shape and create no processing record for people nobody has asked |
| an eleventh agent tool | §10 |
| a second agent runtime | ADR-0004 measured the perpetual-fork cost and closed it; the fallback for a failing runtime is deterministic degraded mode plus a new ADR, never another runtime |
| outbound campaign machinery | §6.1 — every path is closed by law, platform or this system |
| automatic retry of an unknown send outcome | §S8 — the CHECK constraint makes it unrepresentable, on purpose |
| paid acquisition tooling | `SHOP-INSTRUMENT-001` is BLOCKED; spending against an unknown contribution margin is burning money with a dashboard |
| smoothing the 6 kg tier boundary | it is a confirmed business rule |

---

## 15. Open decisions this specification depends on

Existing and open:

- **`DEC-016`** — two of three questions unanswered: whose account, and the out-of-hours sentence.
  Blocks `CHANNEL-ZALO-APPLY-001` and `CHANNEL-TELEGRAM-001`.
- **`DEC-006`** — model-provider data use, retention, region and deletion. Blocks every model call,
  including the assist plane (§8).

**Proposed, not registered** — an agent may assemble the request but may not take the number or the
answer. Recommended as one decision, because the three questions share one owner and one answer set:

> **Proposed `DEC-028` — the nurture cadence, the consent sentence, and what counts as marketing.**
>
> 1. **Are the cadence intervals the shop's policy?** B2C immediate/+2h/+24h/+72h and B2B Day
>    0/2/5/10/30 exist only in an unapproved template. Confirm, change, or delete.
> 2. **What exactly does a B2B contact agree to, and in what words?** The sentence and its version
>    are what `templates/contacts-consent.csv` records and what would later become a consent
>    projection. `Luật 91/2025` Điều 9 requires it to be reproducible as evidence.
> 3. **Is a post-order review request `MARKETING`?** Recommended answer: **yes**, and therefore
>    consent-gated and held. It promotes the business, so treating it as transactional is the kind
>    of convenient reading that becomes an enforcement problem. Confirm or overrule.
>
> Until signed, all three fail closed: no cadence is published, no consent wording is recorded, and
> every review request is `MARKETING` and held.

---

## 16. Tóm tắt cho chủ tiệm

**Hệ thống hỗ trợ khách hàng hằng ngày đã xây xong phần quầy** — từ lúc khách bước vào cửa đến lúc
đơn đóng, phần mềm chạy được toàn bộ. Cái còn thiếu không phải là code, mà là **máy chủ, bản sao lưu
đã thử phục hồi, và tài khoản nhân viên đầu tiên.**

**AI chưa từng chạy một lần nào trong dự án này**, và hôm nay hành vi đúng của nó là **từ chối**. Đó
là thiết kế, không phải hỏng.

**Không có con đường nào cho máy tự nhắn tin cho người lạ** — luật cấm, Zalo cấm, và chính hệ thống
này chặn hai lớp độc lập. Con đường còn lại là để khách chủ động: **một lần quét mã QR có giá trị hơn
một nghìn tin nhắn gửi đi**, vì sau khi khách quan tâm OA thì cửa sổ 48 giờ, 8 tin miễn phí và toàn bộ
nhịp chăm sóc mới mở ra — miễn phí và hợp pháp.

**Ba việc chỉ chủ tiệm làm được, theo đúng thứ tự này:**

1. **Trả lời `DEC-016`** — tài khoản Zalo là của công ty hay cá nhân, và ngoài giờ 08:00–20:00 thì
   nói gì. Làm trước tiên, vì xác thực OA mất vài tuần và không có cách nào rút ngắn.
2. **Chụp ảnh biển hiệu.** Tên trên Google phải đúng tên trên biển; đổi tên sau khi xác thực là phải
   xác thực lại từ đầu.
3. **Quét đủ ba giấy tờ TRƯỚC KHI tạo OA.** Đồng hồ 14 ngày chạy ngay lúc tạo, nộp trễ là **khoá tài
   khoản vĩnh viễn**.

**Ba việc không bao giờ giao cho máy:** xin phép, đưa ra lời hứa, bấm nút gửi.
