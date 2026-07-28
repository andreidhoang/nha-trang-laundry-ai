# Domain, Data & API Specification v1

**Version:** 1.0-draft  
**Date:** 2026-07-27  
**Database:** PostgreSQL  
**Currency:** VND  
**Business timezone:** `Asia/Ho_Chi_Minh`

## 1. Purpose

Tài liệu này biến business truth hiện tại thành một executable contract:

- source of truth duy nhất;
- typed entities;
- deterministic calculations;
- state transitions;
- constraints;
- API commands;
- event contracts;
- migration path từ CSV;
- exact acceptance tests.

## 2. Canonical rules

### 2.1 System of record

PostgreSQL là source of truth. Markdown/CSV:

- dùng làm seed, evidence hoặc export;
- không được query trực tiếp trong runtime;
- không được dùng để replay business state;
- không được để agent tự diễn giải như code.

### 2.2 Data ownership

| Data | Authority |
|---|---|
| Price/promotion/SLA/delivery policy | Published configuration versions |
| Quote amounts | Pricing engine snapshot |
| Customer-provided intake | Customer message + confirmed structured fields |
| Actual weight | Staff measurement record |
| Distance | Verified distance measurement |
| Capacity/promise | Staff approval or future reservation engine |
| Order state | Domain command handler |
| Consent/suppression | Append-only consent events + derived projection |
| Payment/invoice | Finance commands/records |
| Agent memory | Never authoritative |

## 3. Canonical scalar types

### 3.1 IDs

- Internal primary IDs: UUIDv7 or ULID.
- Public order code: non-sequential, short human-readable code.
- Provider IDs stored as strings, never coerced to numeric.
- Every aggregate has `row_version BIGINT`.

### 3.2 Money

- VND amount: `BIGINT`.
- Currency: `CHAR(3)`, V1 only `VND`.
- Negative amount prohibited except explicit ledger entry types.
- Never use IEEE float.
- Rate: integer basis points; 30% = `3000`, 40% = `4000`.
- Rounding: `ROUND_HALF_UP` tới 1 VND.

### 3.3 Quantity

API transmits decimal values as strings.

Database:

- weight/area: `NUMERIC(12,3)`;
- count/pair/set/item: integer quantity;
- distance: meters integer for zone decisions; optional displayed km derived.

Every quantity includes a unit enum:

```text
KG
ITEM
PAIR
SET
ANIMAL_PLUSH_ITEM
M2
CASE
```

Unit compatibility is defined by service version. LLM/client cannot override it.

### 3.4 Time

- Store all instants as `timestamptz`/UTC.
- Evaluate business calendar in `Asia/Ho_Chi_Minh`.
- Effective periods are half-open `[start, end)`.
- Business-local date is stored only when it is itself the business fact.

## 4. Bounded contexts and aggregates

## 4.1 Organization

### `organizations`

- `id`
- `legal_name`
- `tax_code`
- `entity_type`
- `verification_status`

### `stores`

- `id`
- `organization_id`
- `brand_name`
- `hotline_display`
- `hotline_e164`
- `timezone`
- `service_address_id`
- `status`

### `store_addresses`

- `id`
- normalized address fields
- coordinates if approved
- provenance
- verification status

## 4.2 Calendar and staffing

### `business_calendar_versions`

- `id`
- `store_id`
- `version`
- `status: DRAFT|PUBLISHED|RETIRED`
- effective period

### `business_hours_rules`

- day of week
- open/close local times

### `closure_dates`

- local date
- label/reason
- all-day/partial hours

`06 ngày Tết` hoặc `02 ngày phát sinh` chỉ là planning rule. Một published calendar phải chứa ngày cụ thể.

### `staff_members`, `staff_shifts`

R1 dùng cho assignment/audit. Capacity automation không được suy ra chỉ từ số lượng staff.

## 4.3 Party and CRM

### `parties`

```text
PERSON
ORGANIZATION
```

### `customer_accounts`

Quan hệ thương mại của store với party:

- B2C hoặc B2B;
- segment;
- account status;
- B2B terms reference;
- owner/assignee.

### `contact_points`

- phone/Zalo/email/Facebook identity;
- normalized value;
- encrypted original where needed;
- verification state.

### `addresses`

- purpose;
- encrypted exact address;
- geocode token;
- normalized location;
- retention state.

### `account_contacts`

Join cho B2B contacts.

## 4.4 Consent and communications

### `consent_events`

Append-only:

- contact point;
- purpose;
- channel;
- `GRANT|WITHDRAW|LIMIT`;
- scope/frequency;
- wording version;
- evidence/source message;
- actor;
- occurred time.

### `suppression_entries`

- channel/contact identifier hash;
- purpose scope;
- source consent event;
- active/released state;
- release approval when applicable.

### `conversations`, `messages`

Messages are channel facts, not business state.

## 4.5 Catalog

### `services`

Stable commercial offering:

- stable code;
- store;
- lifecycle status.

### `service_versions`

Immutable:

- display name;
- category;
- processing family;
- unit;
- risk class;
- intake requirements;
- price resolution policy;
- SLA scope;
- public description status.

### Standard wash normalization

Current rows:

- `STD_WASH_DRY_LT6`;
- `STD_WASH_DRY_GE6`.

become one canonical service:

```text
STANDARD_WASH_DRY
```

with two price tiers. Old IDs remain in `service_aliases`.

Current 44 price rows therefore represent:

- 43 canonical services;
- 43 canonical `price_rules`;
- 42 non-tier price rules;
- 1 aggregate-tier rule for `STANDARD_WASH_DRY`;
- 2 `price_tiers` under that aggregate rule.

## 4.6 Pricebook

### `pricebooks`

Logical family, e.g. public B2C list.

### `pricebook_versions`

- `DRAFT|PUBLISHED|RETIRED`;
- audience/segment;
- currency;
- effective half-open range;
- approved/published actor/time;
- source hash.

Published version is immutable.

### `price_rules`

Rule type:

```text
FIXED_PER_UNIT
RANGE_PER_UNIT
AGGREGATE_TIER_PER_UNIT
FIXED_PER_ORDER
MANUAL
```

Fields:

- service version;
- unit;
- min/max unit amount;
- minimum billable quantity;
- tax treatment;
- price-resolution policy;
- effective audience;
- provenance.

### `price_tiers`

- lower/upper quantity;
- inclusive/exclusive boundaries;
- unit price;
- minimum billable quantity.

Conditions are typed fields. Do not execute `pricing_rule` prose from CSV.

## 4.7 Promotion

### `promotion_versions`

- name/code;
- effective half-open range;
- eligibility event enum;
- stacking mode;
- status;
- timezone;
- approval.

### `promotion_targets`

Resolve category selectors to explicit service versions at publish time.

Expansion order is deterministic:

1. normalize source aliases to the canonical service code;
2. expand category selectors against the catalog snapshot being published;
3. apply an explicit `service_id` rule over a category rule for the same canonical service;
4. require duplicate aliases for one canonical service to have identical rate, applicability and
   permission;
5. reject publication if two equal-precedence rules disagree.

Published targets contain no category wildcard. `PARTIAL_SCOPE`, `UNCLEAR` and `OUT_OF_SCOPE`
remain explicit targets with `REQUIRE_HUMAN`/`DENY` behavior; expansion does not make them eligible.

### `promotion_applications`

Immutable application snapshot:

- quote revision;
- promotion version;
- eligible subtotal;
- rate;
- discount;
- allocation;
- eligibility timestamp/event;
- reason codes.

## 4.8 Quote

### `quotes`

Container:

- customer account;
- conversation/order request;
- current revision pointer;
- lifecycle.

### `quote_revisions`

Append-only:

- revision number;
- finality;
- pricing time;
- commercial eligibility time/event;
- pricebook version;
- promotion versions;
- SLA/delivery policy versions;
- line snapshots;
- amount snapshots;
- warnings/blockers;
- calculation engine version/hash;
- valid until;
- status.

Finality:

```text
ESTIMATE
RANGE
EXACT_PENDING_APPROVAL
APPROVED_EXACT
```

### `quote_lines`

- service/version;
- quantity basis;
- quantity/unit;
- unit price or range;
- list amount/range;
- applied adjustments;
- net amount/range;
- selection/approval if range.

### `quote_adjustments`

- promotion;
- manual discount;
- surcharge;
- delivery;

Every quote adjustment has:

- `direction: DEBIT|CREDIT`;
- nonnegative `amount_vnd`;
- type, reason, actor, source version and approval.

Quote scope ends at list charges, promotion, delivery and approved surcharges:

- prior-order credit may be reserved during quote, but is redeemed only against finalized order charges;
- refund is a post-payment financial command and is never a quote adjustment.

## 4.9 Orders

### `orders`

Do not use one overloaded status. Store orthogonal states:

- commercial;
- intake;
- production;
- fulfillment;
- payment;
- incident flag.

Core timestamps:

- `inquiry_received_at`;
- `quote_presented_at`;
- `customer_estimate_acknowledged_at`;
- `customer_final_quote_accepted_at`;
- `store_commercial_accepted_at`;
- `intake_received_at`;
- `weighed_at`;
- `production_accepted_at`;
- `production_started_at`;
- `promised_ready_at_store`;
- `ready_at_store`;
- `promised_delivery_window_start/end`;
- `delivered_at`;
- `closed_at`.

Do not create a generic `accepted_at`.

Quote references:

- `order_request_quote_revision_id`: acknowledged estimate/range used to create the request;
- `current_commercial_quote_revision_id`: latest exact approved commercial terms.

Acknowledging an estimate does not accept final price and does not create financial order-line history. Final order charges come only from an `APPROVED_EXACT` revision accepted by the customer.

### `order_lines`

Snapshot from accepted quote revision. Supports mixed services.

## 4.10 Custody and production

### `custody_units`

```text
BAG
ITEM
PAIR
BUNDLE
```

Weighted standard laundry:

- tagged bags;
- total weight;
- bag count;
- dark/light/wet/risk flags;
- no false garment-level inventory claim.

Special/high-value:

- itemized;
- condition notes/photos;
- care label;
- customer acknowledgement.

### `custody_events`

```text
RECEIVED
TAGGED
TRANSFERRED
LOADED
UNLOADED
QC_CHECKED
PACKAGED
RELEASED
DELIVERED
EXCEPTION
```

### `batches`

Physical processing unit, not order.

### `batch_operations`

- machine/resource;
- operation;
- start/end;
- actual load;
- staff minutes;
- downtime;
- rework;
- state.

### `batch_allocations`

Many-to-many between batch/operation and order lines/custody units.

## 4.11 Delivery

### `delivery_bundles`

Customer-facing arrangement:

```text
SELF_DROP_SELF_COLLECT
PICKUP_AND_RETURN
PICKUP_ONLY
RETURN_ONLY
```

Current confirmed 0/10.000 policy applies only to `PICKUP_AND_RETURN`. One-leg modes are `HUMAN_INPUT_REQUIRED`.

### `delivery_legs`

```text
PICKUP
RETURN
RETRY
EXTRA_TRIP
```

Each leg stores:

- planned/actual vehicle;
- window;
- assigned staff;
- start/complete;
- proof;
- exception.

### `distance_measurements`

- store origin;
- destination token;
- meters;
- source;
- measured time;
- actor;
- optional route evidence expiry.

### `delivery_cost_events` and allocations

Internal cost is separate from customer fee.

Customer projection remains:

```text
delivery_fee_vnd
```

for one successful pickup + one successful return.

## 4.12 Finance

### `charges`

Immutable charge lines:

- service;
- delivery;
- surcharge;
- tax;
- adjustment.

### `payments`, `payment_allocations`

Support partial/mixed payments.

### `refunds`

Separate approved financial action.

### `invoice_requests`, `invoices`

R1 tracks request/manual external issue. Auto issuance disabled.

### `b2b_credit_terms`, `accounts_receivable_entries`

Disabled until approved per account.

## 4.13 Incidents, remedies and credits

### `incidents`, `incident_events`, `evidence_assets`

Customer allegation is not fault decision.

Fault:

```text
UNKNOWN
CONFIRMED_STORE
NOT_CONFIRMED
SHARED
```

### `remedies`

- rewash;
- refund;
- compensation;
- apology/no financial;
- other.

Requires approval according to matrix.

### `credit_grants`, `credit_ledger_entries`

Separate from incident:

```text
PROPOSED
APPROVED
ISSUED
PARTIALLY_REDEEMED
REDEEMED
EXPIRED
VOIDED
```

## 4.14 Approval, integration and audit

### `approval_requests`, `approval_decisions`

Bind exact payload hash and resource version.

### `webhook_events`

Durable inbox with provider dedupe.

### `outbox_events`, `delivery_attempts`, `dead_letter_events`

At-least-once processing; exactly-once business effects via idempotency.

### `agent_runs`, `agent_tool_calls`

Store model/prompt/tool versions, structured result and safe summary—not hidden chain-of-thought.

### `audit_events`

Append-only material action ledger.

## 5. Permission model

Replace inconsistent one-dimensional enums with three dimensions.

### 5.1 Price resolution

```text
AUTO_FIXED
SHOW_RANGE
HUMAN_EXACT
NOT_PRICED
```

### 5.2 Promotion resolution

```text
AUTO_IF_TARGETED
HUMAN_CONFIRM
NOT_ELIGIBLE
```

### 5.3 Commitment authority

```text
INFORMATION_ONLY
DRAFT_ONLY
HUMAN_CONFIRM
AUTO_WITHIN_ENVELOPE
PROHIBITED
```

Effective action uses the most restrictive result across:

- service;
- price;
- promotion;
- SLA;
- delivery;
- stage;
- actor;
- customer/account;
- risk flags.

### 5.4 Canonical enum registry

All code, database checks, OpenAPI schemas, prompts and eval manifests are generated from
[`contracts/canonical-enums-v1.json`](contracts/canonical-enums-v1.json). The lists below are
human-readable projections only; CI fails if they drift from the registry.

Deployment stage:

```text
MANUAL_TRUTH
SHADOW
ASSISTED
BOUNDED
```

Actor role:

```text
OWNER_ADMIN
OPS_APPROVER
OPERATOR
DRIVER
ACCOUNTANT
AUDITOR
PUBLIC_AGENT
PRIVATE_AGENT
AGENT_RUNNER
OUTBOX_WORKER
```

Policy outcome:

```text
ALLOW
REQUIRE_HUMAN
DENY
```

Do not create aliases such as `S1_SHADOW`, `OWNER`, `ACCOUNTING` or `PUBLIC AGENT` in implementation contracts.

## 6. Deterministic pricing engine

## 6.1 Standard wash rule

Aggregate all `STANDARD_WASH_DRY` quantities across the order before selecting tier.

Use `pricing_quantity_kg` with an explicit basis:

```text
CUSTOMER_ESTIMATE
STAFF_MEASUREMENT
APPROVED_MANUAL
```

Only `STAFF_MEASUREMENT` under a `PUBLISHED` measurement policy may produce a final quantity-based quote. Other bases remain `ESTIMATE`.

```text
require pricing_quantity_kg > 0

if pricing_quantity_kg < 6:
    billable_kg = max(pricing_quantity_kg, 1)
    unit_price_vnd = 25_000
else:
    billable_kg = pricing_quantity_kg
    unit_price_vnd = 20_000

list_amount_vnd =
    round_half_up(billable_kg * unit_price_vnd)
```

Do not split bags/lines to manipulate the tier.

Examples:

| Actual kg | Billable kg | List amount |
|---:|---:|---:|
| 0.6 | 1.0 | 25.000 |
| 1.0 | 1.0 | 25.000 |
| 5.9 | 5.9 | 147.500 |
| 6.0 | 6.0 | 120.000 |
| 6.1 | 6.1 | 122.000 |

The non-monotonic 5.9→6.0 cliff is a confirmed rule, not a software defect. UI must disclose it near the boundary.

## 6.2 Fixed per unit

```text
line_list_amount =
    round_half_up(quantity * unit_price_vnd)
```

Count units require integer quantity.

## 6.3 Range per unit

Before exact staff selection:

```text
line_min = quantity * min_unit_price
line_max = quantity * max_unit_price
finality = RANGE
```

An exact customer-acceptable quote requires:

- selected unit price within bounds;
- selector actor;
- reason/inspection note;
- approval.

## 6.4 Calculation order

1. Validate service/unit/quantity.
2. Aggregate tiered-service quantities.
3. Compute list amounts.
4. Resolve promotion targets and eligibility.
5. Compute promotion discount per eligible group.
6. Allocate discount to lines using largest remainder.
7. Sum net service subtotal.
8. Add delivery/approved surcharges.
9. Record an optional prior-credit reservation; do not redeem it yet.
10. Apply tax only after published tax policy.
11. Store full calculation trace and hash.

```text
list_service_subtotal_vnd = sum(list_line_amount_vnd)

discount_amount_vnd = sum(promotion_adjustments_vnd)

net_service_subtotal_vnd =
  list_service_subtotal_vnd
  - discount_amount_vnd

display_total_vnd =
  net_service_subtotal_vnd
  + delivery_fee_vnd
  + approved_surcharge_vnd
```

After order charges are finalized, finance separately computes:

```text
amount_due_vnd =
  finalized_charge_total_vnd
  - confirmed_payment_allocations_vnd
  - redeemed_credit_vnd
```

Refunds create post-payment reversing ledger entries and never change historical quote math.

Adjustment invariants:

- every amount is nonnegative; `direction` carries debit/credit meaning;
- discount cannot exceed its eligible subtotal;
- reserved/redeemed credit cannot exceed available balance;
- quote/final charge total cannot become negative without a separate approved payout workflow;
- largest-remainder allocation breaks ties by stable ascending line ID so replay produces the same result.

Until tax treatment is verified:

- `tax_treatment = UNVERIFIED`;
- `tax_vnd = NULL`;
- invoice request is human-blocked;
- every final R1 quote carries `TAX_TREATMENT_UNVERIFIED` and requires human commercial approval;
- bounded final quote/invoice automation remains disabled;
- listed customer amount remains the storefront display total and must not be labeled tax-inclusive/exclusive.

## 7. Promotion engine

## 7.1 Current promotion interval

```text
[2026-07-17T00:00:00+07:00,
 2026-09-01T00:00:00+07:00)
```

This safely includes all of 31/08/2026.

## 7.2 Eligibility event

Generic `accepted_at` is prohibited.

Promotion versions reference one explicit enum:

```text
QUOTE_PRESENTED
CUSTOMER_ESTIMATE_ACKNOWLEDGED
CUSTOMER_FINAL_QUOTE_ACCEPTED
STORE_COMMERCIAL_ACCEPTED
PRODUCTION_ACCEPTED
```

Current campaign source must be owner-approved against one enum before public auto-application across the boundary.

R1 behavior:

- before approval: mark promotion result `PROVISIONAL`;
- crossing campaign boundary or changing eligibility facts: reprice;
- if event semantics remain unresolved: `HUMAN_CONFIRM`;
- accepted order snapshot never changes when campaign later expires.

## 7.3 Current rates

- targeted wet services: 3000 basis points;
- targeted dry-cleaning services: 4000 basis points;
- delivery excluded;
- no stacking by default;
- ambiguous targets stay `HUMAN_CONFIRM`.

At publish time, category rules resolve to explicit service version IDs.

## 7.4 Discount calculation

```text
group_discount_vnd =
  round_half_up(eligible_group_list_subtotal_vnd * rate_bps / 10_000)
```

Allocate to lines so allocated sum exactly equals group discount.

## 8. Delivery engine

## 8.1 Zone

Use verified one-way routed distance in meters:

```text
if distance_m <= 2_000:
    delivery_fee_vnd = 0
elif distance_m <= 6_000:
    delivery_fee_vnd = 10_000
else:
    delivery_fee_vnd = HUMAN_INPUT_REQUIRED
```

No verified distance → human.

## 8.2 Vehicle

Owner rule:

```text
planned_transport_weight_kg < 20 -> MOTORCYCLE
planned_transport_weight_kg >= 20 -> CAR
```

Pickup normally precedes store measurement:

- pickup uses staff-confirmed planned/declared transport weight;
- actual vehicle can be overridden with reason;
- return uses actual packed/transport weight;
- estimate crossing 20kg invalidates prior vehicle plan and creates task;
- vehicle does not change confirmed <=6km customer fee.

R1 never auto-dispatches.

## 8.3 One-leg and extras

- `SELF_DROP_SELF_COLLECT`: no delivery fee/job.
- `PICKUP_AND_RETURN`: confirmed 0/10.000/manual rules.
- `PICKUP_ONLY`, `RETURN_ONLY`: human pricing.
- retry/extra/giao gấp/address change: separate approved charge and new delivery leg.

## 9. SLA engine

## 9.1 Policy types

```text
COMMITMENT
GUIDANCE_RANGE
RESPONSE_TARGET
```

### Standard wash

- type: `COMMITMENT`;
- target: <=8 elapsed hours;
- start: `production_accepted_at`;
- end: `ready_at_store`;
- delivery excluded.

If calculated target crosses closing/closure or cutoff is not configured:

- system may calculate internal risk;
- it must not auto-promise;
- staff sets `promised_ready_at_store`.

### Shoes, curtains, blankets, sheets

- type: `GUIDANCE_RANGE`;
- 24–48 hours;
- exact promise human-confirmed;
- guidance alone does not create a breach event.

### Other special items

- `HUMAN_ETA_REQUIRED`.

## 9.2 Concrete SLA instance

Use two dimensions.

Lifecycle:

```text
DRAFT
-> SCHEDULED
-> RUNNING
-> COMPLETED|CANCELLED
```

Outcome:

```text
PENDING
-> MET|BREACHED|WAIVED
```

An overdue unfinished order is `RUNNING + BREACHED`; completion later records the actual end.

Fields:

- policy version;
- order/line scope;
- clock event names;
- scheduled/promised target/window;
- actual start/end;
- exception/waiver;
- causality decision.

Production and delivery are always separate instances.

The owner has confirmed an 8-hour customer commitment, but elapsed-vs-operating-hour/cutoff semantics are not yet published for automation. Until then:

- human sets the concrete promise;
- system may show an internal elapsed-risk calculation;
- automated breach/remedy remains human-reviewed.

## 10. State machines

## 10.1 Quote container and revision

Quote container:

```text
OPEN
-> CONVERTED|CLOSED
```

Revision workflow:

```text
DRAFT
-> PROVISIONAL
-> REVIEW_REQUIRED
-> APPROVED
-> PRESENTED
-> ACKNOWLEDGED_ESTIMATE|ACCEPTED_FINAL

Any nonterminal -> SUPERSEDED|EXPIRED|REJECTED
```

Quote revision is immutable. Edit creates new revision and invalidates approval.

Invariants:

- `ESTIMATE` may become only `ACKNOWLEDGED_ESTIMATE`, never `ACCEPTED_FINAL`;
- `RANGE` cannot become final until staff selects an exact base price;
- only `APPROVED_EXACT` may become `ACCEPTED_FINAL`;
- estimate acknowledgement and final commercial acceptance have separate timestamps.

## 10.2 Commercial order

```text
DRAFT
-> REQUESTED
-> STORE_CONFIRMATION_PENDING
-> CONFIRMED
-> ACTIVE
-> COMPLETED

DRAFT|REQUESTED|STORE_CONFIRMATION_PENDING|CONFIRMED
  -> CANCELLED

ACTIVE
  -> CANCELLATION_REVIEW
  -> CANCELLED|ACTIVE
```

Direct `ACTIVE -> CANCELLED` is prohibited.

Cross-state invariants:

- `ACTIVE` requires intake `ACCEPTED`;
- production cannot leave `NOT_STARTED` before intake `ACCEPTED`;
- `COMPLETED` requires production `RELEASED`, required delivery legs succeeded/self-collection recorded, and balance `PAID|ON_ACCOUNT`;
- cancellation after production acceptance requires human decision and financial/custody resolution.

## 10.3 Intake

```text
AWAITING_HANDOFF
-> RECEIVED_PENDING_INSPECTION
-> WAITING_PRICE_APPROVAL
-> WAITING_CUSTOMER_RECONFIRMATION
-> WAITING_SLOT_APPROVAL
-> ACCEPTED

RECEIVED_PENDING_INSPECTION|WAITING_PRICE_APPROVAL|
WAITING_CUSTOMER_RECONFIRMATION|WAITING_SLOT_APPROVAL
-> REJECTED
```

`ACCEPTED` requires:

- custody recorded;
- actual measurement or approved quantity basis;
- service classification;
- exact/approved price resolution;
- customer reconfirmation if amount/risk changed;
- human slot approval in R1.

This transition sets `production_accepted_at` once.

States may skip a waiting step only when its blocker does not exist. Recording intake/measurement never starts the SLA by itself.

## 10.4 Production

```text
NOT_STARTED
-> QUEUED
-> IN_PROCESS
-> QUALITY_CHECK
-> READY_AT_STORE
-> RELEASED

active -> ON_HOLD|EXCEPTION
ON_HOLD -> prior valid state
```

## 10.5 Fulfillment

Each required `delivery_leg` has its own state:

```text
PLANNED
-> ASSIGNED
-> IN_PROGRESS
-> SUCCEEDED|FAILED|CANCELLED
```

The delivery bundle derives its status from required legs:

- `SELF_DROP_SELF_COLLECT`: no delivery legs; custody handoff/release events drive completion;
- `PICKUP_AND_RETURN`: both pickup and return legs must succeed;
- `PICKUP_ONLY`: pickup leg only;
- `RETURN_ONLY`: return leg only;
- retry/extra trip is a new leg and never rewrites the failed original.

## 10.6 Payment

Payment state:

```text
PENDING
-> CONFIRMED
PENDING -> FAILED
CONFIRMED -> REVERSED
```

Allocations are separate immutable rows and may be partial. Reversal creates reversing allocation/ledger entries.

Derived order balance:

```text
UNPAID
PARTIALLY_PAID
PAID
OVERPAID
ON_ACCOUNT
```

## 10.7 Incident

```text
OPEN
-> ACKNOWLEDGED
-> INVESTIGATING
-> REMEDY_PROPOSED
-> RESOLVED
-> CLOSED

active -> ESCALATED
```

## 11. Cost and margin

Unknown cost is `NULL`, never zero.

### 11.1 Cost events

- component;
- estimated/actual;
- amount;
- source;
- model/version;
- confidence;
- timestamp;
- allocation method;
- target order/line/batch/delivery leg.

Components:

- electricity;
- water;
- chemical;
- direct labor;
- packaging;
- vehicle/fuel;
- toll/parking;
- payment fee;
- rewash/damage;
- maintenance;
- depreciation;
- tax;
- overhead.

### 11.2 Proposed temporary 30% planning model

The owner confirmed only an approximate cost equal to 30% of service revenue; the percentage base and included components are unresolved. Engineering proposes the following conservative proxy for evaluation:

```text
estimated_processing_cost_vnd =
  round_half_up(0.30 * list_service_subtotal_vnd)
```

Reason: physical processing cost does not fall 30–40% merely because promotion reduces customer price.

Tag:

```text
ENGINEERING_PROPOSAL_30PCT_LIST_V1
```

This model is `DRAFT_UNPUBLISHED` until the owner approves the basis. Do not call remaining amount profit. Until publication, contribution is `ESTIMATE_ONLY` or `INCOMPLETE`.

```text
net_sales_before_tax =
  net_service_subtotal
  + delivery_fee
  + surcharge
  - refunds

contribution =
  net_sales_before_tax
  - processing_variable_cost
  - allocated_delivery_cost
  - payment_fee
  - rewash_damage_cost
```

Completeness:

```text
COMPLETE_ACTUAL
PARTIAL_ACTUAL
ESTIMATE_ONLY
INCOMPLETE
```

UI always displays completeness beside contribution/margin.

## 12. Database constraints

P0:

- amounts >=0 except typed ledger reversal;
- quantity >0;
- min price <= max price;
- rate 0..10000 bps;
- effective end > start;
- published effective ranges do not overlap for same scope;
- exact 6kg only upper tier;
- exact 20kg recommends car;
- exact 2km free;
- exact 6km 10.000;
- exact range price needs selector/approval;
- adjustment amount is nonnegative and direction is `DEBIT|CREDIT`;
- discount <= eligible subtotal;
- credit reservation/redemption <= available balance;
- quote/finalized charge total cannot be negative without approved payout workflow;
- far delivery fee needs human input + customer acknowledgement;
- `ready_at_store >= production_accepted_at`;
- `delivered_at >= out_for_delivery_at`;
- promise cannot precede clock start;
- final quotes/issued financial records immutable;
- no hard delete for orders, payments, approvals, consent, incidents, audit;
- idempotency key unique within its logical operation scope;
- state transition checked in transaction;
- stale aggregate version rejected.
- material mutation, domain event, audit event and required outbox event commit in one transaction; audit failure rolls back the command.
- application runtime DB role may insert audit rows but cannot update/delete them.

Published effective periods should use `tstzrange` + GiST exclusion constraint.

## 13. Command API

Use narrow commands, not generic CRUD for material state.

### 13.1 Common headers

```text
Authorization: Bearer <scoped token>
Idempotency-Key: <opaque>
If-Match: "<row_version>"   # mutations of existing aggregate
Traceparent: <W3C trace>
```

### 13.2 Error envelope

```json
{
  "ok": false,
  "trace_id": "tr_...",
  "error": {
    "code": "HUMAN_APPROVAL_REQUIRED",
    "message": "Safe user-facing message",
    "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
    "field_errors": []
  }
}
```

Stable errors:

```text
VALIDATION_ERROR
AMBIGUOUS_SERVICE
INCOMPATIBLE_UNIT
MISSING_REQUIRED_FACT
PRICE_RULE_UNRESOLVED
MEASUREMENT_POLICY_UNRESOLVED
PROMOTION_ELIGIBILITY_UNRESOLVED
RANGE_PRICE_REQUIRES_HUMAN
DELIVERY_DISTANCE_UNVERIFIED
DELIVERY_FEE_REQUIRES_HUMAN
SLOT_APPROVAL_REQUIRED
CUSTOMER_RECONFIRMATION_REQUIRED
HUMAN_APPROVAL_REQUIRED
INVALID_STATE_TRANSITION
STALE_VERSION
IDEMPOTENCY_CONFLICT
SUPPRESSED_CONTACT
POLICY_DENIED
TOOL_UNAVAILABLE
RATE_LIMITED
```

## 13.3 Agent-safe endpoints

[`contracts/agent-tools-v1.openapi.yaml`](contracts/agent-tools-v1.openapi.yaml) is the single
machine-readable tool/API contract. Every generated agent tool schema, server validator and SDK
must come from that file. Hand-written prompt schemas are prohibited. Every agent endpoint returns
the common `trace_id + data + decision` envelope, enforces contact/conversation/store scope, and
rejects unknown fields.

### Resolve service candidates

```http
POST /agent/v1/catalog:resolve
```

```json
{
  "query": "giặt chăn",
  "locale": "vi-VN",
  "known_attributes": {
    "estimated_quantity": "3.0",
    "unit": "KG"
  }
}
```

Returns candidates; never authorizes a final service by model score alone.

### Create intake draft

```http
POST /agent/v1/order-requests
```

The authenticated channel binding supplies contact/conversation/store. The model cannot select a customer account.

### Record customer facts

```http
POST /agent/v1/order-requests/{id}:record-customer-facts
```

Typed command fields:

- allowed customer-provided facts;
- source provider message IDs;
- expected row version;
- idempotency key.

Generic `PATCH` is prohibited.

### Compute estimate for the bound request

```http
POST /agent/v1/order-requests/{id}/quotes:estimate
```

```json
{
  "lines": [
    {
      "service_code": "STANDARD_WASH_DRY",
      "quantity_basis": "CUSTOMER_ESTIMATE",
      "quantity": "6.100",
      "unit": "KG"
    }
  ],
  "fulfillment": {
    "mode": "PICKUP_AND_RETURN",
    "planned_transport_weight_kg": "6.100"
  }
}
```

The server derives:

- contact/customer/store;
- address binding;
- latest valid distance evidence;
- applicable policies;
- stage and approvals.

The public agent may compute estimates only. Exact revisions are created by internal intake/measurement commands and may only be retrieved/presented after signing/approval.

Example current-campaign response:

```json
{
  "ok": true,
  "trace_id": "tr_...",
  "data": {
    "quote_id": "uuid",
    "revision": 1,
    "finality": "ESTIMATE",
    "pricebook_version": "PB-1",
    "promotion_version": "PROMO-2026-08-v1",
    "list_service_subtotal_vnd": 122000,
    "discount_amount_vnd": 36600,
    "net_service_subtotal_vnd": 85400,
    "delivery_fee_vnd": 10000,
    "display_total_vnd": 95400,
    "tax_treatment": "UNVERIFIED",
    "promotion_status": "PROVISIONAL",
    "promotion_eligibility_event": null,
    "promotion_eligibility_at": null,
    "vehicle_recommendation": "MOTORCYCLE",
    "required_approvals": [
      "PROMOTION_ELIGIBILITY",
      "TAX_TREATMENT_UNVERIFIED",
      "SLOT_CONFIRMATION",
      "OUTBOUND_MESSAGE"
    ],
    "assumptions": [
      "CUSTOMER_ESTIMATED_WEIGHT",
      "FINAL_PRICE_AFTER_STORE_MEASUREMENT",
      "PRODUCTION_SLA_EXCLUDES_DELIVERY"
    ],
    "snapshot_hash": "sha256:..."
  },
  "decision": {
    "outcome": "REQUIRE_HUMAN",
    "reason_codes": [
      "PROMOTION_ELIGIBILITY_UNRESOLVED",
      "TAX_TREATMENT_UNVERIFIED",
      "SHADOW_MODE_ALL_SENDS"
    ],
    "obligations": [
      "DISCLOSE_ESTIMATE",
      "CUSTOMER_RECONFIRM_AFTER_FINAL_MEASUREMENT"
    ],
    "policy_version": "pol_...",
    "snapshot_hash": "sha256:..."
  }
}
```

### Evaluate delivery

```http
POST /agent/v1/order-requests/{id}/delivery:evaluate
```

No arbitrary distance/customer ID is accepted. Server resolves the request-bound address and evidence.

### Check capacity

```http
POST /agent/v1/order-requests/{id}/capacity:check
```

R1/R2 is read-only advisory and always requires human slot approval.

### Prepare message draft

```http
POST /agent/v1/order-requests/{id}/message-drafts
```

Creates a draft revision only. It cannot send.

### Public order status

```http
GET /agent/v1/orders/{public_code}/status
```

Security contract:

- public code contains at least 80 bits of randomness and is a reference, not authentication;
- contact/conversation identity is derived from verified channel context;
- both ownership binding and service scope are required;
- generic not-found/denied response;
- per-contact/IP rate limits;
- public code is redacted from unrestricted proxy logs.

### Open incident

```http
POST /agent/v1/order-requests/{id}/incidents:open
```

Agent may store statement/evidence token only. Fault/remedy fields are not accepted.

### Request approval

```http
POST /agent/v1/order-requests/{id}/approvals:request
```

Public input contains only action enum, resource ID/version, snapshot/rendered hash and idempotency key. Policy derives reason codes, required role, TTL, obligations and execution capability. Agent cannot decide or weaken approval.

### Consent

Explicit STOP/withdrawal is a synchronous deterministic ingress command, not an LLM tool. Consent grant is accepted only by a server-side pending-consent state machine bound to the exact contact, wording version, scope, channel and affirmative provider message. No public agent endpoint may select consent scope/evidence.

## 13.4 Internal endpoints

```text
POST /internal/v1/quotes/{revision}/approve
POST /internal/v1/quotes/{revision}/present
POST /internal/v1/quotes/{revision}:acknowledge-estimate
POST /internal/v1/quotes/{revision}:accept-final
POST /internal/v1/orders/{id}/store-confirm
POST /internal/v1/orders/{id}/intake-receive
POST /internal/v1/orders/{id}/measurements
POST /internal/v1/orders/{id}:advance-intake
POST /internal/v1/orders/{id}:production-accept
POST /internal/v1/orders/{id}/transition
POST /internal/v1/orders/{id}/promises
POST /internal/v1/orders/{id}/delivery-fee
POST /internal/v1/delivery-legs/{id}/transition
POST /internal/v1/payments
POST /internal/v1/invoice-requests
POST /internal/v1/incidents/{id}/events
POST /internal/v1/remedies
POST /internal/v1/credits
POST /internal/v1/approvals/{id}/decisions
POST /internal/v1/config/{type}/publish
```

`advance-intake` atomically:

1. validates custody;
2. reads actual measurement;
3. resolves service;
4. recalculates exact quote revision;
5. detects price/promo/vehicle changes;
6. moves to `WAITING_PRICE_APPROVAL`, `WAITING_CUSTOMER_RECONFIRMATION` or `WAITING_SLOT_APPROVAL`;
7. emits required approval/reconfirmation events;
8. never starts production SLA.

`production-accept` is a separate command. It succeeds only after exact final terms, customer reconfirmation, slot approval and all blockers are satisfied; then it sets `production_accepted_at` once and starts the SLA.

## 14. Domain events

Event envelope:

```json
{
  "event_id": "uuid",
  "event_type": "order.production_accepted.v1",
  "aggregate_type": "order",
  "aggregate_id": "uuid",
  "aggregate_version": 8,
  "occurred_at": "2026-07-27T07:00:00Z",
  "actor": {
    "type": "USER",
    "id": "uuid"
  },
  "trace_id": "tr_...",
  "payload": {},
  "pii_classification": "INTERNAL"
}
```

Minimum events:

```text
quote.revision_created.v1
quote.approved.v1
quote.presented.v1
quote.estimate_acknowledged.v1
quote.final_accepted.v1
order.requested.v1
order.store_confirmed.v1
order.intake_received.v1
order.measurement_recorded.v1
order.production_accepted.v1
order.production_started.v1
order.ready_at_store.v1
order.released.v1
delivery.leg_planned.v1
delivery.leg_completed.v1
payment.confirmed.v1
incident.opened.v1
approval.requested.v1
approval.decided.v1
consent.changed.v1
message.approved.v1
message.delivered.v1
message.manual_send_attested.v1
```

## 15. Idempotency

### Canonical hash

Use `JCS-SHA256-V1`:

- JSON Canonicalization Scheme (RFC 8785) or an implementation proven byte-compatible;
- decimal strings normalized without exponent or redundant trailing zeros;
- timestamps normalized to UTC RFC 3339 with a fixed precision;
- strings normalized to Unicode NFC;
- object keys sorted by JCS;
- SHA-256 output with algorithm/version prefix.

Exclude only declared volatile transport fields such as `trace_id`, retry attempt, network metadata and generated observation time. Include resource ID/version, business payload, policy/version and rendered content where the hash authorizes a side effect.

### Logical idempotency scopes

```text
inbound:
  (provider, channel_account_id, provider_event_id)

order creation:
  (store_id, bound_contact_or_conversation_id, operation, client_key)

payment:
  (store_id, payment_method, external_transaction_reference)

message send:
  (channel_account_id, approved_action_id)

approval execution:
  (approval_request_id)

other internal command:
  (store_id, aggregate_id, operation, client_key)
```

Rules:

- same key + same normalized input → return original result;
- same key + different input hash → `IDEMPOTENCY_CONFLICT`;
- creation/payment/send/approval keys remain for the lifetime of the business record;
- service-account credential rotation does not change the logical scope;
- provider event ID is also unique at ingress;
- manual DLQ replay keeps logical key.

## 16. CSV migration

### Phase A — evidence preservation

- hash every source;
- record filename, SHA-256, encoding, row count and import time;
- never mutate source during import.

### Phase B — staging

Each row:

- source file;
- row number;
- raw JSON;
- normalized JSON;
- validation errors.

### Phase C — normalization

Expected current structured counts:

- business profile: 1;
- calendar rules: 5;
- machines: 8;
- delivery rules: 8;
- price rows: 44;
- canonical services: 43;
- canonical price rules: 43;
  - non-tier price rules: 42;
  - aggregate standard-wash rule: 1;
  - tiers beneath the aggregate standard-wash rule: 2;
- promotion: 1;
- promotion source rules: 18;
- expanded promotion targets: exactly 43 unique canonical service targets, each preserving
  `CONFIRMED|PARTIAL_SCOPE|UNCLEAR|OUT_OF_SCOPE` and its effective agent permission;
- SLA rows: 5;
- operational logs: 0.

Mappings:

- two standard wash IDs → one service/two tiers;
- both standard-wash promotion aliases → the one canonical `STANDARD_WASH_DRY` target;
- category promotion rules → explicit immutable service-version targets at publication;
- delivery vehicle duplicate rules → one zone policy + vehicle selector;
- machine evidence separate from physical asset;
- incidents separate from credits;
- mutable consent row → event history/projection;
- mentions of `ga`/bed sheets in promotion or SLA evidence do **not** create a priced service and
  do not map to `DRY_BEDDING`; a sheet-washing price remains `HUMAN_ONLY` until owner-published;
- empty operational files produce no invented rows.

### Phase D — validate and publish

- exact source and canonical count checks above;
- canonical service code, price-rule code and source-alias uniqueness;
- aggregate tier coverage/non-overlap: `<6kg` and `>=6kg`, with exactly one owner-approved boundary;
- 18 promotion source rules expand to exactly 43 unique canonical service-version targets;
- no unresolved category selector remains in a published promotion version;
- no invented sheet service or accidental `ga` → `DRY_BEDDING` mapping;
- every canonical service has explicit price-resolution, promotion-resolution and commitment-authority
  permissions; missing mapping blocks publication;
- all publication foreign keys reference the same immutable catalog/pricebook/promotion/SLA snapshot;
- SLA service mapping;
- dry-run golden calculations;
- atomic publish test: version, targets, audit event and required cache/outbox invalidation either all
  commit or none commit;
- idempotent re-import yields no new business rows and the same canonical snapshot hash;
- human publish approval;
- launch effective time explicit.

### Phase E — cutover

- app writes DB;
- compatibility exports for CSV;
- compare the first 30 real customer orders and all of their quote revisions;
- freeze CSV as seed/reference;
- rollback by retiring unpublished/new version, not deleting transactions.

## 17. Reporting definitions

- **Production on-time:** `MET / (MET + BREACHED)` for due production SLA instances whose lifecycle
  is `COMPLETED`; report `WAIVED` separately and never silently exclude a breach by changing lifecycle.
- **Delivery on-time:** successful delivery at/before promised window end / completed delivery promises.
- **Rewash rate:** orders with confirmed rewash remedy / released orders.
- **Lost/damaged:** confirmed incidents / released orders.
- **Quote engine error:** deterministic rule corrections / presented quote revisions.
- **Human override:** decisions differing from engine recommendation / approval decisions.
- **Promotion spend:** sum issued promotion adjustments.
- **Delivery subsidy:** allocated actual delivery cost − customer delivery fee.
- **Contribution:** show completeness flag.
- **Batch utilization:** actual load / nominal load, separate from daily sellable capacity.
- **Consent compliance:** blocked sends, unauthorized sends, withdrawal latency.

Every report declares:

- numerator;
- denominator;
- exclusions;
- time window;
- timezone;
- data completeness.

Metric queries are versioned artifacts. A dashboard label without its query version, observation
window and minimum sample size is informational only and cannot satisfy a stage gate.

## 18. Acceptance tests

### Pricing

1. `0kg` → validation error.
2. `0.6kg` → list 25.000; current wet promo net 17.500.
3. `1.0kg` → list 25.000.
4. `5.9kg` → list 147.500; promo net 103.250.
5. `6.0kg` → list 120.000; promo net 84.000.
6. `6.1kg` → list 122.000; promo net 85.400.
7. `3kg + 3kg` same service → aggregate 6kg and list 120.000.
8. Range item without selected price → `RANGE_PRICE_REQUIRES_HUMAN`.
9. Pillow during eligible 30% promo → range 21.000–63.000.
10. Wedding dress 400.000 during eligible 40% promo → 240.000.
11. Old quote unchanged after pricebook publish.
12. Customer estimate and staff measurement produce separate immutable revisions; estimate cannot
    become final merely by changing `quantity_basis`.
13. Half-VND discount remainder uses largest-remainder allocation with stable line-ID tie-break.
14. Large multi-line remainder allocation preserves exact order-level total.
15. Credit-direction adjustments cannot make a quote or finalized charge total negative.

### Promotion

16. One instant before start → ineligible.
17. Exact start → eligible.
18. `2026-08-31T23:59:59.999+07:00` → eligible.
19. `2026-09-01T00:00:00+07:00` → ineligible.
20. Eligibility event after expiry → reprice/reconfirm.
21. B2B price + promo → no stacking unless targeted/approved.
22. Delivery fee never discounted.
23. Unresolved eligibility returns a provisional revision with eligibility fields `null` and
    `PROMOTION_ELIGIBILITY` approval; it cannot be presented as final.

### Delivery

24. Exactly 2.000km → 0.
25. 2.001km → 10.000.
26. Exactly 6.000km → 10.000.
27. 6.001km → human input.
28. Self-drop/self-collect → no job/fee.
29. Pickup-only → human pricing.
30. 19.999kg planned weight → motorcycle recommendation.
31. 20.000kg → car recommendation.
32. Actual weight crosses threshold → return plan recomputed and prior approval invalidated.
33. >6km fee cannot finalize without human + customer acknowledgement.
34. A changed address, verified distance or vehicle class invalidates the applicable delivery-fee
    approval and creates a new quote revision.
35. Bundle state is derived from immutable leg states; a failed pickup cannot be represented as a
    completed bundle.

### Workflow/SLA

36. Customer estimate acknowledgement does not equal final commercial acceptance.
37. Quote acceptance does not start production SLA.
38. `advance-intake` records/calculates and moves to a waiting state when any blocker exists.
39. SLA starts only at successful `production-accept` and immutable `production_accepted_at`.
40. Ready exactly +8h → met; later can be `RUNNING + BREACHED`; lifecycle and outcome are independent.
41. 24–48 guidance alone does not create automatic breach.
42. Near closing with no cutoff rule → human promise.
43. Illegal state transition rejected.
44. Stale row version → `STALE_VERSION`.
45. Quantity/address edit invalidates approval.
46. Cancellation of an active order requires the cancellation-review path and cannot use a generic
    transition command.

### Finance/consent/audit

47. Partial cash + transfer create immutable allocations and `PARTIALLY_PAID`.
48. Reversal produces a new finance event and recomputes balance; it never edits the original payment.
49. Overpayment yields `OVERPAID`; it is not silently converted into a quote discount.
50. Unknown tax policy adds a blocker and prevents bounded finalization/invoice automation.
51. Unknown cost stays `NULL`.
52. The 30% list-based proxy uses `ENGINEERING_PROPOSAL_30PCT_LIST_V1`, remains unpublished by
    default and is labeled estimate—not owner truth or profit.
53. Consent withdrawal blocks queued marketing at final send, including a STOP racing an outbox job.
54. Material mutation, domain event, audit event and required outbox event commit atomically.
55. Injected audit-write failure rolls back the business mutation.
56. CSV export neutralizes `=`, `+`, `-`, `@`.
57. Retried create-order with same logical key returns the original order.
58. Same idempotency key/different canonical payload conflicts.
59. Public tool call cannot read or mutate another contact's bound order request, even with a valid UUID.
60. Public status rejects enumeration, cross-contact access and forged client-supplied binding.

### Migration and contract parity

61. Import asserts 44 source price rows → 43 canonical services → 43 canonical price rules:
    42 non-tier + 1 aggregate rule with exactly 2 tiers.
62. The 18 promotion source rules expand to exactly 43 unique canonical service-version targets.
63. Re-import is idempotent and preserves the canonical snapshot hash.
64. No priced sheet service is invented and no sheet reference maps to `DRY_BEDDING`.
65. Atomic publication failure leaves no partial version, target, audit or invalidation event.
66. Database enum checks, generated SDK types, OpenAPI and eval manifest values equal
    `canonical-enums-v1.json`.
67. Every published canonical service has all three permission dimensions.

## 19. P0 unresolved decisions encoded safely

| Decision | R1 behavior |
|---|---|
| Weight precision/rounding | record staff input; no model/client rounding; final auto-price disabled |
| Promo eligibility event | explicit field; boundary cases human |
| Tax treatment | `UNVERIFIED`; invoice human |
| One-leg delivery price | human |
| Pickup vehicle before actual weight | staff plan/recommendation only |
| Exact closure/cutoff | human promise |
| Rewash/credit/compensation | incident + owner approval |
| Cost components | NULL + completeness |
| B2B price/credit | human/account contract only |
