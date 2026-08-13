# TASK-autonomy-001 — capability-specific bounded automation canary

**Goal:** take one narrow action automatically, for one eligible population, with a canary and a
rehearsed way back.

**Domains:** `runtime_architecture`, `orders_audit`, `evaluation_release`, `business_truth`

**Stable work item:** `AUTONOMY-001`

**Stage:** M6
**Risk:** HIGH — the first time the system does something rather than says something.

## Why this exists

`G4_BOUNDED_CAPABILITY_ENTRY` is the last gate in `delivery/GATE_REGISTRY.yaml`. It is where
`QUOTE_ESTIMATE`, `BOOKING`, `DELIVERY_ADVISORY` and `MARKETING_FOLLOWUP` become possible — the
capabilities that change state rather than describe it.

It depends on `CHANNEL-001` and is blocked by `DEC-001` through `DEC-004`, because a bounded action
that touches money, promotion, delivery or an incident cannot execute against an unresolved policy.

## Required evidence

From `delivery/GATE_REGISTRY.yaml` and the manifest's `bounded_capability` policy:

| Requirement | Minimum |
|---|---:|
| Clean days in the directly preceding stage | 30 |
| Eligible capability-specific real cases | 100 |
| Critical safety or price errors | **0** |
| Material correction rate | ≤ 5% of automated messages delivered for the capability |

Plus measured economics and capacity where the capability touches them, current published
measurement, promotion calendar, cutoff and customer policies, and canary, correction, kill-switch,
incident, provider-outage, restore and rollback drills.

## Constraints

- **One named capability and one eligible population.** Not a stage everything graduates into.
  Each capability enters separately with its own evidence.
- A canary is a fraction of eligible traffic with an automatic stop condition, not a soft launch
  someone watches.
- The correction rate denominator is `automated_messages_delivered_for_capability`. Do not widen it
  to flatter the rate.
- The model still never calculates money, decides policy, selects a customer or sends. A bounded
  action is deterministic code executing a decision the domain made.
- `DEC-001` through `DEC-004` must be `RESOLVED` and published. An unresolved policy fails closed,
  and a bounded action on a fail-closed policy is a contradiction.

## Done when

- 30 clean days in the preceding stage and 100 eligible real cases for the named capability;
- zero critical safety or price errors and a correction rate within the declared ceiling;
- every listed drill executed and recorded;
- a signed G4 manifest naming exactly one capability and one eligible population;
- rollback is disabling the capability flag, rehearsed and timed before entry.
