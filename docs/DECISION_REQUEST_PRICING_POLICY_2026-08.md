# Decision request — weight rounding, promotion event, delivery >6km, and settlement scope
# (DEC-001, DEC-002, DEC-003, DEC-004, DEC-010)

**Date:** 2026-08-18
**Status:** Decisions A, B, C, E ratified 2026-08-18 by the business owner against the
recommendations in §§2, 3, 4, 6 — see `context/DECISION_REGISTRY.yaml` (DEC-001, DEC-002, DEC-003,
DEC-010). **Decision D (DEC-004) remains OPEN** — the 24h report/response windows are accepted, but
no compensation ceiling or staff-approval tier has been supplied; see §5. Signing does not itself
publish configuration — see §0.
**Trigger:** `DECISION-BUSINESS-001` (`delivery/WORK_QUEUE.yaml:1319-1352`), the queue item that bundles
DEC-001–004, plus `DEC-010` (`context/tasks/TASK-settlement-001.md`), which SETTLEMENT-001 opened and
explicitly left out of its own scope.
**Assessments this rests on:** `PRICEBOOK_V1.md`, `BUSINESS_TRUTH_INTAKE.md`,
`DELIVERY_POLICY_DRAFT.md`, `CUSTOMER_SERVICE_POLICY_DRAFT.md`, `PROMOTION_2026_08.md`,
`specs/TEAM_REVIEW_REPORT_V1.md` §6.

---

## 0. What this request does not do

- It does not touch `packages/domain` pricing/promotion/delivery code, any migration, or any
  published configuration table.
- It does not authorize `QUOTE_ESTIMATE`, `BOOKING`, `DELIVERY_ADVISORY`, `MARKETING_FOLLOWUP`, or
  `INCIDENT_RECEIPT` — closing these decisions removes the wrong-reason fail-closed; the gate ladder
  (G1–G4) still governs whether the capability may run.
- Signing this document is not "publishing configuration." Per `DECISION-BUSINESS-001`'s own
  definition of done, each answer still has to be expressed as versioned config the pricing/promotion/
  delivery/incident engines read — that is a separate, small engineering item after signature, not
  before.
- It does not decide `DEC-008` (retention — `RESOLVED` separately) or anything about channels,
  providers, or hosting.

## 1. What is already decided (and is not being re-opened)

- The ≤6 km delivery-fee schedule is owner-confirmed and live in `DELIVERY_POLICY_DRAFT.md`: `≤2km =
  0đ`, `>2–6km = 10,000đ` total for one pickup + one return leg.
- The standard wash-dry tier formula is owner-confirmed: `bill = max(actual_kg, 1) × 25,000đ` under
  6 kg, `bill = actual_kg × 20,000đ` at 6 kg and above. The 6 kg pricing cliff (5.9 kg = 147,500đ,
  6.0 kg = 120,000đ) is a real, confirmed rule — not something any of the decisions below may smooth.
- The promotion program (`PROMO_WET30_DRY40_20260717_20260831`, 30%/40% off wet/dry, 17 Jul–31 Aug
  2026) is confirmed and out of scope here.
- SETTLEMENT-001's exact-payment, self-collection path is built and does not wait on DEC-010; DEC-010
  only gates every *other* settlement shape.

## 2. Decision A — DEC-001, weight precision and rounding

**Owner:** `BUSINESS_OWNER` · **Fail-closed today:** `REQUIRE_HUMAN` on `QUOTE_ESTIMATE`, `BOOKING`

The confirmed formula already multiplies the *exact* measured kilogram figure by the tier rate — the
worked examples (5.9 kg → 147,500đ, 6.1 kg → 122,000đ) use unrounded weight. Nothing today rounds a
weight or a price; the open question is narrower than "rounding policy" sounds: does the shop's scale
resolve to a precision the system should also round to, and in which direction, before billing?

| Answer | Consequence |
|---|---|
| **No rounding — bill the exact scale reading** (recommended) | Matches the confirmed worked examples exactly. Requires only stating the scale's actual resolution (assumed 0.1 kg — confirm or correct) so `QUOTE_ESTIMATE` knows how many decimal places are legitimate input, not a rounding rule. |
| **Round to a stated increment before billing** | Needs three numbers this document does not supply: the increment (e.g., nearest 0.1 kg), the direction (always up / nearest / always down), and whether rounding happens before or after the 6 kg tier check — the last of these can move an order across the pricing cliff, so it is not a detail. |

Recommendation on record: **no rounding**, because it is what the business already confirmed via the
worked examples, and it removes a place where a rounding-direction choice could quietly move an order
across the 6 kg cliff. Confirm the scale's actual precision so the input validation matches reality.

## 3. Decision B — DEC-002, promotion eligibility event

**Owner:** `BUSINESS_OWNER` · **Fail-closed today:** `REQUIRE_HUMAN` on `QUOTE_ESTIMATE`,
`MARKETING_FOLLOWUP`

`PROMOTION_2026_08.md` §1 already states the system's eligibility clock: `accepted_at`, the timestamp
the store accepts the order. This is channel-agnostic by construction — order acceptance always
happens at the store regardless of whether the order originated in person, by phone, or through a
future public channel, so there is no channel-dependent disagreement to design out.

Recommendation on record: **ratify `accepted_at` (store-side order acceptance) as DEC-002's formal
answer.** This is not a new rule — it is recording a rule the promotion draft already uses, so the
decision and the implementation stop disagreeing about whether it was ever actually decided.

## 4. Decision C — DEC-003, delivery pricing beyond 6 km

**Owner:** `BUSINESS_OWNER` · **Fail-closed today:** `REQUIRE_HUMAN` on `DELIVERY_ADVISORY`, `BOOKING`

`DELIVERY_POLICY_DRAFT.md` §4 already specifies the >6 km behaviour precisely: staff negotiates a
total pickup+return fee with the customer, enters it into `delivery_fee_vnd`, and the store proceeds
only after the customer agrees. `SHOP-INSTRUMENT-001`'s twenty delivery logs are what would let a
*formula* replace staff judgment for this band; they do not exist yet.

| Answer | Consequence |
|---|---|
| **Ratify the current staff-negotiated behaviour as the formal policy now** (recommended) | Ships as `HUMAN_INPUT_REQUIRED` + customer-agreement-required, i.e. no change to what already happens today — this converts a currently-unratified fail-closed default into a signed decision, without inventing a per-km number the shop has no cost data to justify. |
| **Wait for SHOP-INSTRUMENT-001, then set a formula** | Matches `DECISION-BUSINESS-001`'s stated preferred order, but DEC-003 stays `OPEN` and >6 km orders keep needing a human every time (which, per the row above, is also what happens if you sign now — so waiting costs the signature, not the behaviour). |

Recommendation on record: **sign now, ratifying staff-negotiated pricing as the intentional >6 km
policy** — it costs nothing behaviourally (staff already does this), and it is explicitly not a
one-way door: `DECISION-BUSINESS-001`'s task packet allows this decision to be superseded by a formula
once the delivery-cost log exists.

## 5. Decision D — DEC-004, rewash, loss, damage, compensation, and credit

**Owner:** `BUSINESS_OWNER` · **Fail-closed today:** `HUMAN_APPROVAL_REQUIRED` on `INCIDENT_RECEIPT`

`CUSTOMER_SERVICE_POLICY_DRAFT.md` §7 marks this explicitly `CẦN CHỐT` (still to be finalized): the
report window, rewash conditions, and — the part with real money attached — a compensation matrix.
This is the one decision in this packet I am **not** comfortable reducing to a single recommended
number, because a wrong ceiling is a real liability exposure, not a UX preference. What follows is the
narrowest possible framing plus industry-typical reference points, not a number to rubber-stamp.

**Already effectively confirmed and low-risk to ratify as-is:**
- Report window for visible defects: **24 hours** after receiving goods (already in the draft).
- Store reviews and proposes an initial resolution within **24 hours** of a report (already in the
  draft).
- Late-delivery credit: **>2 hours late by store fault → 10% credit on the next bill** (already
  confirmed, already implemented per `specs/TEAM_REVIEW_REPORT_V1.md` P0 matrix row "Consent/STOP" —
  actually see the same report's row "Post-approval edit" area; the 10% figure itself traces to
  `CUSTOMER_SERVICE_POLICY_DRAFT.md` §5, worth a one-line reconfirmation, not a new decision).

**Genuinely open — needs the owner's number, not mine:**
- **Rewash condition and window:** e.g., "free rewash offered when staff determines store fault,
  requested within N days of pickup." N is not stated anywhere in the drafts.
- **Compensation ceiling and approval tiers:** e.g., "staff may approve compensation up to X VND
  without escalation; above X requires owner sign-off," and a basis for the amount itself — capped at
  a multiple of the service price (common in-market practice is 5–10× the cleaning fee, since laundries
  rarely insure for full replacement value) versus capped at a customer-declared item value (only
  workable if declared-value intake is also adopted, which is currently `HUMAN`-gated per
  `PRICEBOOK_V1.md` §1 for high-value items).
- **Loss policy:** same shape as damage — the drafts say "hai bên thỏa thuận" (negotiated) with no
  ceiling or method stated.

Recommendation on record: **do not sign a specific VND figure from this document alone.** Confirm the
24-hour windows as written (low risk, already the working assumption), and separately decide the
compensation ceiling and staff-approval tier with real numbers — this is worth five minutes of owner
judgment rather than an inherited default, because it sets the shop's actual liability exposure per
incident.

## 6. Decision E — DEC-010, settlement shapes beyond exact payment in full at handover

**Owner:** `BUSINESS_OWNER` · **Fail-closed today:** `NOT_SUPPORTED` on `ORDER_STATUS`, `BOOKING`

`SETTLEMENT-001` built and tested the exact-payment, self-collection path only; partial payment,
deposits, instalments, and `ON_ACCOUNT` B2B credit terms were explicitly left as `DEC-010`, newly
opened, out of scope.

| Answer | Consequence |
|---|---|
| **Defer — stay `NOT_SUPPORTED`** (recommended) | Costs nothing today: the built path already covers the ordinary retail case (cash/transfer in full at handover). No B2B credit relationship exists yet per `BUSINESS_TRUTH_INTAKE.md` §4 ("Công nợ B2B: CẦN CHỐT"), so there is no live case this blocks. |
| **Define deposit/partial-payment terms now** | Needs a deposit percentage or amount, a rule for what happens if the balance is never paid, and how it interacts with the 20-day/60-day storage-and-disposal policy in `BUSINESS_TRUTH_INTAKE.md` §6. Worth doing only once a real case (e.g., a large B2B order) makes it necessary. |

Recommendation on record: **defer.** Signing "defer" is itself a valid, complete answer to DEC-010 —
it converts an accidentally-open decision into a deliberately-deferred one, with no behaviour change.

## 7. What each answer unblocks

```
Decision A (DEC-001) signed → QUOTE_ESTIMATE, BOOKING lose their DEC-001 fail-closed reason
Decision B (DEC-002) signed → QUOTE_ESTIMATE, MARKETING_FOLLOWUP lose their DEC-002 fail-closed reason
Decision C (DEC-003) signed → DELIVERY_ADVISORY, BOOKING lose their DEC-003 fail-closed reason
Decision D (DEC-004) signed → INCIDENT_RECEIPT loses its DEC-004 fail-closed reason
Decision E (DEC-010) signed "defer" → DEC-010 moves from accidentally-open to deliberately-deferred;
                                        no capability changes
All five (A–E) → PUBLIC-POLICY-001 buildable → CHANNEL-001 buildable
Unsigned → each capability keeps failing closed for exactly the reason it does today; nothing breaks
```

None of the above authorizes a capability by itself — G1 through G4 still gate `QUOTE_ESTIMATE`,
`BOOKING`, `DELIVERY_ADVISORY`, `MARKETING_FOLLOWUP`, and `INCIDENT_RECEIPT` independently.

## 8. Signature block

Fill and commit. Unsigned rows keep their fail-closed default, which is the current behaviour.

```yaml
decision_a_dec_001_rounding:
  answer: NO_ROUNDING
  scale_precision_kg: 0.1        # assumed, not separately confirmed by owner — correct if wrong
  owner: BUSINESS_OWNER
  decided_at: '2026-08-18'
decision_b_dec_002_promotion_event:
  answer: ACCEPTED_AT_STORE_SIDE
  owner: BUSINESS_OWNER
  decided_at: '2026-08-18'
decision_c_dec_003_delivery_over_6km:
  answer: RATIFY_STAFF_NEGOTIATED_NOW
  owner: BUSINESS_OWNER
  decided_at: '2026-08-18'
decision_d_dec_004_rewash_damage_compensation:
  report_window_confirmed: 24H_AS_DRAFTED
  rewash_window_days:             # STILL OPEN — owner's number, no default
  compensation_ceiling_vnd:       # STILL OPEN — owner's number, no default
  staff_approval_ceiling_vnd:     # STILL OPEN — owner's number, no default
  owner: BUSINESS_OWNER
  decided_at:                     # not decided — DEC-004 stays OPEN in the registry
decision_e_dec_010_settlement_shapes:
  answer: DEFER
  owner: BUSINESS_OWNER
  decided_at: '2026-08-18'
```
