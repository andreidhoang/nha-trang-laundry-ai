# TASK-public-policy-001 — published PUBLIC_CUSTOMER bundle and correction workflow

**Goal:** define exactly what the assistant is allowed to say to a customer, publish it as one
hashed artifact, and rehearse taking it back when it is wrong.

**Domains:** `channel_operations`, `business_truth`, `privacy_consent`

**Stable work item:** `PUBLIC-POLICY-001`

**Stage:** M5
**Risk:** HIGH — this bundle is the boundary between internal knowledge and a public statement about
price.

## Why this exists

`G2_PUBLIC_ASSISTED_ENTRY` requires an "applicable customer policy" and a published public corpus.
`CHANNEL-001` depends on this item; `EVAL-PUBLIC-CORPUS-001` grades against the bundle this item
produces. `specs/PUBLIC_CUSTOMER_POLICY_SPEC_V1.md` and
`specs/contracts/public-policy-bundle-v1.schema.json` are the normative shape.

## Required design

- **One published bundle, content-hashed and versioned.** Everything the public runtime may state
  comes from it. A fact that is not in the bundle is not sayable, however true it is.
- **Internal content is unreachable from the public runtime.** `POLICY_RISK_REVIEW.md` and every
  other internal risk document are prohibited from any customer-facing retrieval path — a
  `business_truth` prohibition, and one that a retrieval index makes easy to violate by accident.
- **A prohibited-content scan** that rejects a bundle seeded with internal material, run as a gate
  rather than as a review habit.
- **A deterministic dry run** that rejects a bundle whose stated price disagrees with the pricing
  engine. The bundle is prose; the engine is authority; they must agree before publication.
- **Expiry degrades to `REQUIRE_HUMAN`.** A bundle past its validity does not keep answering from
  stale content.
- **A correction workflow, executed once.** Publishing a wrong price and then correcting it is the
  drill; a workflow that has never been run is a diagram.

## Constraints

- Depends on `DECISION-BUSINESS-001`. Until `DEC-001` through `DEC-004` are published configuration,
  the bundle cannot state a rule for weight rounding, promotion eligibility, delivery beyond 6 km or
  incident compensation — and must not invent one.
- Values marked `CẦN ĐO` or `CẦN CHỐT` are unmeasured. The bundle may say the assistant declines to
  state them; it may not state them.
- The bundle is customer-facing content, not configuration authority. Money still comes from the
  deterministic engines at answer time.
- No capability is authorized by publishing a bundle.

## Required tests

- exactly one bundle is published, hashed, and versioned;
- internal risk material is unreachable from the public runtime, proven by attempt;
- the prohibited-content scan rejects a deliberately seeded bundle;
- the deterministic dry run rejects a bundle containing a wrong price;
- an expired bundle degrades to `REQUIRE_HUMAN` rather than answering;
- the correction drill was executed and its timeline recorded.

## Done when

- one hashed bundle is published and every check above passes;
- the correction drill has been run end to end at least once;
- `EVAL-PUBLIC-CORPUS-001` can grade against a real bundle hash;
- rollback is unpublishing the bundle, which returns the public path to `REQUIRE_HUMAN`.
