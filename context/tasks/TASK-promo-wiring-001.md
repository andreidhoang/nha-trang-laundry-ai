# TASK-promo-wiring-001 — a resolved decision the engine still calls open

**Goal:** publish promotion policy as versioned configuration, evaluate it in the quote path, and
make the shop able to run a promotion without a code deploy.

**Domains:** `pricing`, `promotion_delivery_sla`

**Stable work item:** `PROMO-WIRING-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — it changes the number a customer is told. The freeze-and-reverify rule in
"Required design" is what keeps that safe.

**Depends on:** `RANGE-PRICE-001` — both modify `compose_quote_revision`.

## Why this exists

`packages/domain/.../promotion.py` is a complete, careful, tested engine: basis-point rates,
largest-remainder allocation across lines, an explicit calculation trace, integer VND throughout. It
has **zero production call sites**. Every import outside its own tests is either
`packages/evals/.../synthetic_pricing.py` or the package `__init__`.

`quote_composition.py` stamps `PROMOTION_NOT_EVALUATED` on every revision it has ever composed, and
its comment is this item's ticket:

> `DEC-002` resolved 2026-08-18; evaluating promotions is unbuilt work, not an open decision, and this
> code says so until that work lands.

## Two stale statements and one design defect

`promotion.py:118` carries:

```text
# DEC-002 remains open. A generic accepted_at is prohibited.
eligibility_event=None,
```

DEC-002 has not been open since 2026-08-18. The comment is false, and the `None` it justifies makes
every evaluation return `PROVISIONAL`/`REQUIRE_HUMAN` forever. Correct it to
`PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED` — `accepted_at` in the enum's own vocabulary.

The defect is larger: `CURRENT_PROMOTION` is a **module-level Python constant**. The shop cannot run a
promotion without a code deploy, and an expired program cannot be replaced by the owner. Invariant 4
wants published configuration immutable and versioned; a constant is neither. Promotion policy moves
to CONFIG-001 publication, hash-addressed, exactly as the pricebook and the remedy policy are.

## Today's honest answer is zero, and the console must say so

`CURRENT_PROMOTION` runs 17 Jul – 1 Sep 2026 exclusive. Today is 18 Sep 2026: the only confirmed
program **has expired**. Wiring the engine correctly therefore shows a 0 ₫ discount today.

That is the correct answer and it is not the current behaviour, even though both show zero:

- today: 0 ₫ because **nothing was assessed** — `PROMOTION_NOT_EVALUATED`
- after: 0 ₫ because **the program ended on 31/08/2026** — `PROMOTION_OUTSIDE_INTERVAL`

The console renders the second as a Vietnamese sentence, not as a silent zero. A zero with no
explanation is the same failure as a `null` total rendered as `0`.

## Required design

**Freeze at quote, re-verify at acceptance.** A promotion keyed on `accepted_at` is evaluated at quote
time, before acceptance exists. The engine already handles that honestly: with no `eligibility_at`,
`eligibility_resolved` is false and the result is `PROVISIONAL`/`REQUIRE_HUMAN`.

But `DEC-021` says the customer agreed to a price read aloud. If the number moves between quote and
acceptance, the shop charges something the customer did not agree to. So:

1. Evaluate at quote time; freeze the full `PromotionResult` into the immutable revision, including
   the policy code, the rate, the allocation trace and the resulting total.
2. At acceptance, re-evaluate with the real `accepted_at`.
3. If the discount differs by even 1 ₫, **refuse the acceptance** with `PROMOTION_CHANGED_SINCE_QUOTE`
   and require a re-quote.

The system never silently re-prices an agreed total, and never lets a program expire between reading a
price and taking the laundry without somebody seeing it.

**Delete `PROMOTION_NOT_EVALUATED`** from `BASE_REASON_CODES`. It will no longer be true, and a false
reason code on an immutable revision is permanent misinformation.

**The publication vehicle, named precisely.** `ConfigurationRepository` in
`packages/db/src/nha_trang_laundry_db/configurations.py` is the CONFIG-001 primitive: generic,
versioned, hash-addressed, with a per-type validator registry and
`ConfigurationRepository.latest_published(cursor, config_type)`. `config_type` matches
`^[A-Z][A-Z0-9_]{1,62}$` and the `(config_type, version)` pair is unique (migration `0002`). Publish
under `config_type = 'PROMOTION_POLICY'` and register a validator for it, so a malformed policy is refused at
publication rather than discovered at the counter.

**Fail closed with no policy.** Invariant 11. With no promotion configuration published, quotes
compose normally at list price and carry a reason code stating that no program is running — which is a
different fact from "not evaluated".

**Stacking and human-confirm targets are already handled** by the engine
(`PROMOTION_STACKING_REQUIRES_HUMAN`, `PROMOTION_TARGET_REQUIRES_HUMAN`). Surface them; do not
reimplement them.

**`ApplyPromotion` approval unchanged.** `ApplyPromotion → _OWNER_FINANCIAL`, resource
`QUOTE_REVISION`, already in the table. Required only where the engine returns `REQUIRE_HUMAN`; an
automatic in-interval discount on an `AUTO_IF_TARGETED` line needs no per-quote approval, because
publishing the program was the approval.

## A wall RANGE-PRICE-001 left for you, deliberately

`close_range_prices` returns `VALIDATION_ERROR` if a stored band line carries a non-zero discount,
because "the amount" would then be ambiguous between list and net. No path can produce that today —
promotions are not evaluated — so it is a wall for this item to walk into rather than a bug.

Decide it explicitly and write the reasoning down. The question is whether a promotion applies to
the **list** amount of a band line or to the **staff-chosen** amount inside the band, and they are
different numbers with different authority behind them: the list band is the owner's published
authorisation, and the chosen amount is a staff attestation under `DEC-021`.

The safer reading, and the recommendation unless you can show otherwise: a promotion applies to the
**closed** amount, because that is the price the customer was read, and `DEC-002` keys eligibility to
`accepted_at`, which is necessarily after the band was closed. A promotion computed against a band
that was never charged would discount a price nobody paid.

Whatever you decide, the `VALIDATION_ERROR` must be replaced by a stated rule and a test, not by
removing the check.

## Constraints

- Do not modify the allocation arithmetic in `promotion.py`. It is tested and correct.
- Integer VND; no float. The engine's `ROUND_HALF_UP_1_VND_THEN_LARGEST_REMAINDER_LINE_ID_ASC`
  rounding is the authority and its trace must reach the revision.
- No arithmetic on money in the route layer.
- The 6 kg pricing cliff is untouched. A promotion applies to the list amount the pricing engine
  produced, whichever side of the cliff it fell on.

## Required tests

- A quote for a targeted service inside a published live program carries a provisional discount, a
  rate and a named policy code.
- Accepting it with `accepted_at` inside the interval finalises the same number to the dong.
- Accepting it after the interval ends refuses `PROMOTION_CHANGED_SINCE_QUOTE`.
- With no program published, quotes compose at list price with a reason code saying none is running.
- A published-but-expired program yields `PROMOTION_OUTSIDE_INTERVAL`, and the console renders the
  end date.
- The frozen trace on the revision reproduces the discount exactly on recomputation.
- `PROMOTION_NOT_EVALUATED` appears nowhere in the codebase.

## Done when

All of the above pass, `uv run mypy apps packages` is clean, `verify_contracts.py` and
`check_context_drift.py` pass, and the owner can publish a new promotion program through
configuration without a code change — demonstrated by a test that publishes one and sees it applied.
