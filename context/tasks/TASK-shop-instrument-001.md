# TASK-shop-instrument-001 — instrument the physical shop

**Goal:** measure what the shop actually does, so that `SHADOW-001` has a denominator and the SLA
and capacity engines have inputs that are true.

**Domains:** `business_truth`, `promotion_delivery_sla`

**Stable work item:** `SHOP-INSTRUMENT-001`

**Stage:** M4B
**Risk:** HIGH to the schedule, low to the code. This is the longest calendar-bound item in the
project and it needs no engineering at all.

## Why this exists

`SHADOW-001` exits on 30 real orders, 10 batch logs and 20 delivery logs, with zero wrong monetary
values. Those numbers are meaningless without a measured baseline: without knowing how long a load
actually takes, what a delivery actually costs, and what capacity is actually safe, there is nothing
to compare the pilot against and no way to tell a good result from a lucky one.

`BUSINESS_TRUTH_INTAKE.md` and `MACHINE_INVENTORY.md` mark unmeasured fields `CẦN ĐO` and unfixed
policy `CẦN CHỐT`. Those markers are load-bearing: the `business_truth` domain prohibits letting an
agent commit to any of them.

**This takes 4–6 weeks of real operation and it starts before any code.** Every week it is not
started is a week added to the end of the project, because `SHADOW-001` cannot begin without it and
everything downstream of G1 waits on `SHADOW-001`.

## What must be measured

Use the templates already in the repository rather than inventing a format:
`templates/delivery-cost-log.csv`, `templates/machine-master.csv`, `templates/business-profile.csv`.

| Measurement | Minimum | Why this number |
|---|---|---|
| Timed wash/dry loads | **10** | establishes cycle time distribution, not a single anecdote |
| Delivery logs with distance and true cost | **20** | `DEC-003` (over 6 km, one-leg delivery) cannot be closed without a cost curve |
| Measured cost per kg | derived | the deterministic pricing engine currently prices against unverified cost |
| Owner-confirmed safe capacity | per machine | the capacity engine's R1 limit is currently an assumption |
| Item-count handoff checklist | one, in use | the intake path assumes a count that nobody has defined |

Record the date, the operator and the conditions for every entry. A load timed on a quiet Tuesday
and a load timed on Saturday afternoon are different measurements and both are needed.

## Constraints

- These are observations of the real shop. Do not simulate, estimate or back-fill a value that was
  not measured; an unmeasured field stays `CẦN ĐO`.
- Owner-confirmed values are business truth. Values observed in a document are evidence, not
  configuration — the distinction is a `business_truth` prohibition.
- Do not use customer names or phone numbers in the logs. Distance, weight, time and cost only.
- Publishing a measured value into deterministic configuration is a separate, versioned act; this
  item produces the measurement, not the configuration change.

## Done when

- ten timed loads, twenty delivery logs and a measured cost per kg exist with their conditions;
- the owner has confirmed a safe capacity per machine in writing;
- an item-count handoff checklist exists and the staff are using it;
- the `CẦN ĐO` fields these measurements cover are marked measured, with their source;
- `SHADOW-001` has a baseline it can be evaluated against.
