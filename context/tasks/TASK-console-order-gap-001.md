# TASK-console-order-gap-001 — say out loud that no order can be created

**Goal:** disclose, on the screen where an operator tries it, that the running system refuses every
order-creation command — and bind the disclosure so it stops being shown the day that changes.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `CONSOLE-ORDER-GAP-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW to build. The condition it discloses is severe and currently silent.

## Why this exists

Found by a concurrent session driving the live stack, and verified independently here at every link
before being acted on. `POST /internal/v1/stores/{id}/orders` returns **409 "accepted exact quote is
missing, stale, or expired"** for a quote the engine had just priced successfully — and it will do
that for every quote this system can produce.

| Link | Evidence |
|---|---|
| The only quote producer hardcodes a non-orderable state | `packages/domain/.../quote_composition.py:232-233,255` — `finality=ESTIMATE`, `status=REVIEW_REQUIRED`, `approval_id=None` |
| It feeds the only writer | `packages/db/.../quotes.py:133` is the sole `INSERT INTO quote_revisions`; there is **no `UPDATE quote_revisions` anywhere in production code** |
| Order creation demands the opposite | `packages/db/.../orders.py:126-137` refuses unless `APPROVED_EXACT` **and** `ACCEPTED_FINAL` **and** `approval_id IS NOT NULL` |
| Nothing promotes a revision | Every write of those states outside tests is `packages/evals/.../synthetic_incidents.py:162-163`, a fixture generator. Approval execution does not touch quotes — `approvals.py:418` writes only `approval_executions` |

**This is the second independent blocker on the same path.** `resolve_or_create` — the only writer of
`contact_channel_bindings` — also has no production caller, so no `bound_contact_id` exists either.
Either one alone makes order creation impossible. Closing `DEC-013` would not be enough.

### Why it is a disclosure item and not a fix

Building the promotion path is `CATALOG-PRICEBOOK-001`/`FULFILMENT-001` territory and touches how a
quote becomes binding on the shop — that is the owner's sequencing call, not a defect to patch. What
is fixable today, cheaply, is that **the console does not say so.**

`orders.js:672-675` states the rule — an order comes only from a customer-accepted, exactly-approved
quote — but not that no quote in this system can reach that state. `STAFF_CONSOLE_ENGINEERING_SPEC_V1.md`
§4 lists the order board as built, and the completion program §10 describes an operator who "takes an
order" today. An operator pastes a quote id, receives a 409, and cannot tell it was not their typo.
`IMPLEMENTATION_ROADMAP_V1.md:394`'s "no hidden unsupported default" passes in letter and fails in
spirit.

## Required design

- A `#/gaps` entry in the "Duyệt và phiên" group naming the exact condition and what still works.
- One sentence added to the `orders.js` create-order guardrail, at the point of use, saying the
  command will be refused and that it is not the operator's mistake.
- A **binding** in `specs/contracts/console-disclosures-v1.yaml` of a new kind asserting the code fact:
  no production module writes `APPROVED_EXACT`. When a promotion path lands, the test fails and the
  disclosure must change — which is the whole point, because that is the day it stops being true.
- The `Tiếp nhận` screen has the same shape one level down: `CONTACT_BINDING_UNKNOWN` is well
  disclosed on `#/gaps` but the screen gives no pointer at the moment of refusal. Fold in if cheap;
  say so if not.

## Constraints

- **Add disclosure; remove none.** No existing string is reworded.
- Regenerate the disclosure registry and `sw.js` after the change; coordinate the `sw.js` regeneration
  with the concurrent session holding `apps/web`.
- Do not build the quote-promotion path under this item. Disclosing a gap and closing it are different
  acts with different authority.

## Required tests

- the new disclosure is registered and bound, and the binding fails when a production module writes
  `APPROVED_EXACT`, proven by mutation;
- the console contract suite and the interaction verifier stay green;
- no existing disclosure string changed, proven by diff.

## Done when

- an operator meeting the 409 can read why on the screen they are standing on;
- the disclosure is bound to the fact that makes it true;
- the full gate battery passes with no required skips.
