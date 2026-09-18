# TASK-remedy-001 — DEC-004 expressed as configuration, and a surface to act on it

**Goal:** let an incident reach an outcome. Publish the owner's ratified remedy figures as versioned
configuration, and build the proposal, ceiling, approval and credit primitives that read them.

**Domains:** `orders_audit`, `business_truth`

**Stable work item:** `REMEDY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH — it sets the shop's liability exposure per incident and is the first path that
creates money owed to a customer.

## Why this exists

`DEC-004` is RESOLVED with three figures the owner supplied on 2026-08-18, and **no code reads any of
them**. The vocabulary exists and nothing is behind it: `ApprovalAction.APPROVE_REMEDY`
(`catalog.py:292`), resource type `REMEDY_PROPOSAL` (`approvals.py:125`), `AdjustmentDirection.CREDIT`
(`catalog.py:72`).

`INCIDENT-INTAKE-001` now lets a staff member record the complaint. Then the thread stops: an incident
cannot lead to any money or order outcome. The shop resolves it verbally and writes nothing down —
which means the 5× ceiling and the 100.000 ₫ escalation the owner decided are not enforced by
anything.

## The ratified figures

Verbatim from `context/DECISION_REGISTRY.yaml` DEC-004 and
`docs/DECISION_REQUEST_PRICING_POLICY_2026-08.md` §5:

- Report window for visible defects: **24 hours** after receiving goods.
- Store proposes an initial resolution within **24 hours** of a report.
- Late delivery >2 hours by store fault: **10% credit on the next bill**.
- Free rewash: within **7 days** of pickup, when staff determines store fault.
- Damage compensation: capped at **5× that item's cleaning fee** — what the store charged, not retail
  or replacement value.
- Staff may approve up to **100.000 ₫**; above that the **owner** must approve.

## Loss must refuse. This is the most likely way this item goes wrong.

The decision packet is explicit:

> **Loss policy:** not covered by the figures above […] Treat loss as **not yet resolved** even though
> damage now is; if a loss case reaches the 5×/100.000đ figures above by analogy, confirm that reading
> with the owner before relying on it, since it was not explicitly asked.

`RemedyKind.LOST_ITEM` is accepted as a **record** and returns `REQUIRE_HUMAN` with
`LOSS_POLICY_UNRESOLVED`. It must not inherit the damage ceiling, the staff ceiling, or any window.
No test may assert a loss ceiling. "Unknown means stop", applied to the one sub-case the owner did not
answer.

## Required design

**Four kinds, each with a server-computed ceiling.**

| Kind | Ceiling | Authority | Window |
|---|---|---|---|
| `FREE_REWASH` | none; no money moves | staff attestation of store fault | 7 days from pickup |
| `DAMAGE_COMPENSATION` | 5× that order line's priced amount | ≤100.000 ₫ staff; above, `APPROVE_REMEDY` | 24h from receipt |
| `LATE_DELIVERY_CREDIT` | 10% of the order's settled total | staff attestation; server computes | store fault, >2h late |
| `LOST_ITEM` | **unresolved** | `REQUIRE_HUMAN` always | — |

Staff never type a ceiling. The server computes 5× from the order line's own priced amount and 10%
from the order's settled total, both already stored. A proposal above its computed ceiling is
**refused with the ceiling named**, never truncated to it.

**Money direction, under invariant 2.** Money is non-negative integer VND. A remedy is money owed
*to* the customer and must not be a negative settlement. A proposal carries a non-negative
`amount_vnd` plus an explicit `AdjustmentDirection.CREDIT`.

**The settlement ledger is not touched.** It is append-only and `reject_ledger_mutation()` would
refuse a rewrite anyway. An approved remedy creates a separate forward obligation. A test must assert
the ledger is byte-identical before and after.

**A credit is a quote adjustment, never a settlement adjustment.** Use the primitive that already
exists: `QuoteAdjustmentSnapshot` (`quotes.py:89`) carries `kind`, `direction`, a non-negative
`amount_min_vnd`/`amount_max_vnd` pair, `source_version_id` and `approval_id`. Add
`QuoteAdjustmentKind.REMEDY_CREDIT` beside the existing `PROMOTION`, `MANUAL_DISCOUNT`, `SURCHARGE`
and `DELIVERY`. The **direction** carries the sign; the amount stays non-negative, so invariant 2
needs no special case.

The credit therefore changes what the next quote's total *is*, before the customer is told it. It does
not change what may be paid against a total already agreed, so `DEC-010` is untouched and the
settlement path keeps accepting only the exact quoted total in full.

Attaching it to a customer record is impossible — `DEC-015` refuses to build one. It is issued against
the counter ticket or channel binding the order already carries, the same two sources
`OrderRepository.create` checks per `DEC-015`'s consequence clause, and redeemed by presenting that
ticket. This is deliberately a bearer instrument, like a paper voucher. It is redeemable **exactly
once**, enforced by the server, and an unredeemed credit expires with the order financial record's
retention schedule.

**Windows run from recorded events, not from staff input.** 7 days from pickup is 7 days from
`RELEASED` on the production dimension; 24 hours from receipt is from the recorded intake event. Both
timestamps already exist on the order. An out-of-window request is refused **naming the window that
was missed**, so staff can tell the customer why rather than only that.

**Configuration, not constants.** The six figures are published as one immutable, hash-addressed
configuration document through the CONFIG-001 primitive, exactly as the pricebook is. Invariant 11:
with no published remedy policy version, every remedy request fails closed with
`REMEDY_POLICY_UNPUBLISHED`. Hardcoding `100_000` would make the shop's liability ceiling a code
deploy and would repeat the `CURRENT_PROMOTION` mistake.

**Approval, unchanged.** `APPROVE_REMEDY` already maps to `_OWNER_FINANCIAL` with `REMEDY_PROPOSAL`.
Use it as-is; do not edit `APPROVAL_POLICIES`.

**Atomicity.** Mutation, domain event, audit event and any required outbox record are written in one
transaction; invariant 5. Follow `transactions.py`.

## Console

A remedy is proposed from the incident it answers (`#/incidents`) and from order detail. Before staff
type an amount the form must already show: the kind, the **computed ceiling**, the window and whether
it is still open, and whether this proposal will need the owner. Staff must never discover that the
owner is required after filling the form in.

`LOST_ITEM` renders through `components.unsupported` with the reason that loss policy is not yet
decided — not as a broken form.

## Required tests

- Each of the four kinds, end to end from incident to outcome.
- `LOST_ITEM` returns `REQUIRE_HUMAN`/`LOSS_POLICY_UNRESOLVED`. **No test asserts a loss ceiling.**
- 100.000 ₫ damage does not require `APPROVE_REMEDY`; 100.001 ₫ does.
- A proposal above 5× the line's priced amount is refused with the computed ceiling in the refusal.
- A rewash requested on day 7 is allowed; day 8 is refused naming the 7-day window.
- Unpublishing the remedy policy makes every remedy request fail closed.
- The settlement ledger is byte-identical before and after a remedy is approved.
- A credit is redeemable exactly once against its ticket, and a second redemption is refused.

## Done when

All of the above pass, `uv run mypy apps packages` is clean, `verify_contracts.py` and
`check_context_drift.py` pass, and a browser run completes a damage-compensation proposal against a
real API including the owner-approval branch.
