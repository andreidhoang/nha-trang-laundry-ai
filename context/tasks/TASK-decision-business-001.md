# TASK-decision-business-001 — owner decision session closing DEC-001 through DEC-004

**Goal:** convert four open policy questions into versioned, published configuration in one session.

**Domains:** `business_truth`, `pricing`, `promotion_delivery_sla`

**Stable work item:** `DECISION-BUSINESS-001`

**Stage:** M4B
**Risk:** MEDIUM — the risk is answering in conversation and never publishing, which changes
nothing.

## Why this exists

Four decisions are `OPEN` in `context/DECISION_REGISTRY.yaml`, each with a fail-closed behaviour
that is currently active:

| Decision | Question | Fail-closed today | Capabilities held |
|---|---|---|---|
| `DEC-001` | Weight precision and rounding | `REQUIRE_HUMAN` | `QUOTE_ESTIMATE`, `BOOKING` |
| `DEC-002` | Definitive promotion eligibility event across channels | `REQUIRE_HUMAN` | `QUOTE_ESTIMATE`, `MARKETING_FOLLOWUP` |
| `DEC-003` | Delivery pricing over 6 km and one-leg delivery | `REQUIRE_HUMAN` | `DELIVERY_ADVISORY`, `BOOKING` |
| `DEC-004` | Rewash, loss, damage, compensation and credit | `HUMAN_APPROVAL_REQUIRED` | `INCIDENT_RECEIPT` |

`PUBLIC-POLICY-001` depends on this item, and `CHANNEL-001` depends on `PUBLIC-POLICY-001`. Every
week these stay open, the public path stays closed and the assistant keeps handing routine questions
to a human.

The system behaves correctly while they are open — that is the point of fail-closed — but it
behaves *narrowly*, and the narrowness is the cost of the delay rather than a defect.

## What each decision needs

Not a preference; a rule that deterministic code can execute without asking again.

- **`DEC-001`** — the rounding increment, the direction, and the point in the flow where rounding is
  applied. "Round to the nearest 0.1 kg, up, at weigh-in" is a rule. "Be fair about it" is not. The
  6 kg tier boundary is a real cliff, so the rounding rule and the tier interact and must be decided
  together.
- **`DEC-002`** — which single event makes a customer promotion-eligible, and how it is observed on
  each channel. Two channels disagreeing about eligibility is the failure mode to design out.
- **`DEC-003`** — the price beyond 6 km as a formula, and whether one-leg delivery is offered at
  all. `SHOP-INSTRUMENT-001`'s twenty delivery logs are the input; deciding before they exist means
  deciding without a cost curve.
- **`DEC-004`** — the rewash, loss, damage, compensation and credit policy, including the monetary
  ceiling a staff member may approve without escalation.

## Constraints

- The output is **versioned published configuration**, not a meeting note. An answer that is not
  published changes no behaviour and closes no decision.
- `DEC-003` should follow `SHOP-INSTRUMENT-001`'s delivery logs. Deciding earlier is permitted and
  is the owner's call, but record that it was decided without the cost data.
- Resolving a decision does not authorize a capability. `QUOTE_ESTIMATE` still requires G1 through
  G4; closing `DEC-001` only stops it failing closed for the wrong reason.
- No agent may resolve any of these, infer them from a draft document, or treat a draft as decided.
  `CUSTOMER_SERVICE_POLICY_DRAFT.md` and `DELIVERY_POLICY_DRAFT.md` are drafts, and their names say
  so.

## Done when

- all four decisions read `RESOLVED` in `context/DECISION_REGISTRY.yaml` with the owner and date;
- each is expressed as a rule deterministic code can execute, published as versioned configuration;
- the pricing, promotion, delivery and incident engines read the published values;
- `PUBLIC-POLICY-001` can start.
