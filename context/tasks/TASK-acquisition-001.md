# TASK-acquisition-001 — a customer acquired by any means can be recorded, and the channel that produced them can be counted

**Goal:** a person who decides to become a customer — however they found the shop — becomes a row in
this system rather than a note on paper, and the channel that produced them is recorded on the order
so that "which channel works" stops being an opinion.

**Domains:** `business_truth`, `privacy_consent`, `orders_audit`

**Stable work item:** `ACQUISITION-001`

**Stage:** M4B
**Risk:** MEDIUM as code, HIGH as privacy. Every part of this item touches a person who has not yet
agreed to anything.

**Status of this packet:** drafted, **not enqueued**. See §7.

---

## 1. Why this exists

Measured 2026-08-18 and provable from four files: **this system can record zero customers, from any
source.** Not "poorly" — zero.

1. `CreateOrderCommand.bound_contact_id` is required —
   `packages/db/src/nha_trang_laundry_db/orders.py:43`. There is no order without a contact binding.
2. The only writer of `contact_channel_bindings` is `ChannelBindingRepository.resolve_or_create` —
   `packages/db/src/nha_trang_laundry_db/channel.py:112` — keyed on `(provider, provider_user_ref)`.
   A binding exists only for an identity that arrived from a messaging provider.
3. `contact_channel_bindings.provider` is `CHECK`ed to exactly `ZALO_OA`, `TELEGRAM_SANDBOX`,
   `FACEBOOK_MESSENGER` — `packages/db/migrations/0020_channel_envelope.sql`. There is no fourth
   value, and in particular there is no counter.
4. No provider is connected. `FEATURE_PUBLIC_CHANNELS_ENABLED` is asserted `"false"` by
   `packages/evals/tests/test_staging_deployment_contract.py:15` and
   `packages/evals/tests/test_container_build_contract.py:72`. `DEC-005` records the decision (Zalo
   OA for production, Telegram for shadow) and records that **both artifacts still do not exist** —
   no bot token, no submitted OA business-verification application.

Both doors are therefore shut, for different reasons:

| How a customer arrives | What stops them | Owner of the blocker |
|---|---|---|
| Messages the shop first | there is no channel to message on | `CHANNEL-ZALO-APPLY-001` / `CHANNEL-TELEGRAM-001` — an artifact, not code |
| Walks in off the street | no identity can be created at the counter | `DEC-013`, OPEN, `BUSINESS_OWNER` |

**The consequence for growth is the whole point of this packet.** Every lead the business generates —
leaflet, front desk, Google, a knock on a hotel door — lands on paper. So the acquisition question
is not *how do we find clients*. It is *what happens to the first one who says yes*, and today the
answer is: a paper note nobody reconciles, and no evidence of which channel paid for itself.

## 2. What this item is not

**It is not a CRM.** `RESEARCH_BRIEF.md` §12 rules that out at this stage — "Không build CRM, form
hay automation engine từ đầu ở giai đoạn này" — and the repository already carries the alternative it
recommends. `templates/accounts.csv`, `templates/contacts-consent.csv`, `templates/interactions.csv`
and `templates/pilots-orders.csv` are the §10 minimal data model, already shaped, currently empty,
and read by no code (verified: nothing under `scripts/`, `packages/` or `apps/` parses them; only
`services-pricebook.csv` is imported).

The boundary this item defends:

> **The sheet holds prospects. The database holds customers.**
> A row crosses when a person agrees to become a customer, and that crossing is a consent event.

A hotel the shop has never spoken to does not belong in PostgreSQL. Putting it there would create a
personal-data processing record for a party that has not been asked, which is the exact failure
`DEC-013` exists to prevent — and the reasoning does not stop at the counter.

**It is not outbound automation.** `MARKETING_FOLLOWUP` is `NOT_AUTHORIZED`
(`packages/evals/tests/test_context_harness.py:90`), and `MarketingDeliveryRepository` holds every
claimed marketing send with `MARKETING_AUTHORIZATION_UNAVAILABLE` because "R1 has no positive consent
projection or authorized marketing send path"
(`packages/db/src/nha_trang_laundry_db/marketing_delivery.py`). That hold is correct and this item
does not lift it.

## 3. Scope

### 3.1 Gated on `DEC-013` — the counter identity

Whatever `DEC-013` resolves to is what gets built, and the options differ enormously in cost:

- **Ticket-only** (the recommendation on record): a counter-issued reference with no personal data.
  Needs a fourth binding source that is explicitly *not* a person — likely a distinct
  `contact_binding` origin rather than a fourth `provider` value, so that "we have no idea who this
  is, on purpose" stays legible in the schema rather than being disguised as a provider identity.
- **Name + phone with a spoken consent line**: needs a consent event, a wording version, a retention
  class under `DEC-008`, and a deletion path. Materially larger.

**Do not choose between these in code.** The packet is written so either can be built without
rewriting the other's foundations.

### 3.2 Gated on one channel artifact — the messaged identity

Nothing to build. `CHANNEL-TELEGRAM-001` and `CHANNEL-ZALO-APPLY-001` are already queued and already
blocked on artifacts only the owner can produce. This item inherits them.

### 3.3 Decision-free, but worthless before 3.1 or 3.2 — acquisition attribution

An `acquisition_source` recorded on the order, from a closed enum, written once at creation and never
inferred by a model:

`WALK_IN` · `GOOGLE_MAPS` · `ZALO` · `FACEBOOK` · `PARTNER_FRONT_DESK` · `REFERRAL_CUSTOMER` ·
`LEAFLET_QR` · `RETURNING` · `UNKNOWN`

`UNKNOWN` is a first-class value and must stay one. A staff member who did not ask must be able to
say so; a required field with no honest option is a field that gets filled with a lie, and the
resulting channel report is worse than no report.

This is pure additive order data — no personal data, no policy, no money. It is listed last because
it is genuinely decision-free and genuinely useless today: `orders` cannot receive rows at all until
3.1 or 3.2 lands, and attribution over an empty table measures nothing.

`PARTNER_FRONT_DESK` additionally needs a partner reference to make the per-partner QR of
`RESEARCH_BRIEF.md` §5 measurable. That reference is an **organisation**, not a person, and is the
one prospect-side field that plausibly belongs in the database rather than the sheet.

## 4. Constraints

- **The model never selects a customer and never sends.** This is `CLAUDE.md`'s first
  non-negotiable and this item does not touch it. Discovery, ranking, routing and drafting are
  research-side work whose output is read by a person. Nothing in this item gives an agent a
  recipient.
- **Attribution is attested, never inferred.** A staff member records where the customer came from.
  No model classifies it, and no heuristic guesses it from a message body.
- **Consent is never inferred from silence.** `suppression_entries` already models this correctly —
  absence of a row means unknown and unknown blocks; `CLEAR` must be written by a deliberate audited
  act (`packages/db/migrations/0022_consent_egress_guard.sql`). Any consent this item records follows
  that shape, including the wording version the customer actually agreed to.
- **Through `commit_material_change`.** Row, domain event, audit entry and outbox event commit
  together, as everywhere else.
- **Capacity is not a marketing input.** AI-confirmable capacity is **0 kg/day** at Stage 0
  (`BUSINESS_TRUTH_INTAKE.md` §2). Nothing in this item may promise a slot, and no acquisition
  surface may imply one.

## 5. What unblocks what

```
DEC-013 (owner, OPEN) ──────────► counter identity ──┐
                                                     ├──► an order can exist ──► attribution means something
Zalo OA application (owner) ─────► messaged identity ┘                                    │
        │                                                                                  ▼
        └──► inbound concierge (PUBLIC_FAQ, LIST_PRICE_INFO, INTAKE_QUESTION)      CAC by channel is computable
                     │                                                             (RESEARCH_BRIEF §15)
                     └──► DEC-015 (consent projection) ──► MARKETING_FOLLOWUP could ever be authorized
```

`SHOP-INSTRUMENT-001` is not in this diagram on purpose. It gates whether growth is *profitable*, not
whether it is *recordable*. Both must land; they are independent.

## 6. Acceptance checks

```bash
uv run pytest
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus, specific to this item and not satisfiable by a green suite:

- a customer created by the resolved `DEC-013` path cannot be created by any *other* path;
- an order carries exactly one `acquisition_source`, written at creation, never updated;
- `UNKNOWN` is reachable from the console without a warning that pressures staff away from it;
- no agent-reachable tool in the hash-pinned contract can read or write an acquisition source.

## 7. Why this is not enqueued

Adding a row to `delivery/WORK_QUEUE.yaml` is a scheduling act, and the item's first slice is gated on
`DEC-013`, which is the business owner's decision and is open. Enqueueing it now would either sit
`BLOCKED` as noise or imply the decision is expected to go a particular way.

**The owner enqueues this**, after `DEC-013` and `DEC-015` are signed, with
`blocked_by_decisions: [DEC-013, DEC-015]` and `depends_on: [CHANNEL-001]` where the messaged path is
concerned.
