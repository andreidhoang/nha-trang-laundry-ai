# TASK-bounds-and-truth-001 — a number too large is a refusal, and the panel says what the server does

**Goal:** close the last three HIGH findings from the 2026-08-29 verification that need no owner
decision — two integer-bound defects with one cause, and three false sentences on the settlement
screen.

**Domains:** `orders_audit`

**Stable work item:** `BOUNDS-AND-TRUTH-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW. It refuses inputs that already had no valid outcome, and corrects text. No behaviour
that previously succeeded stops succeeding.

## Why this exists

### Five routes answered a bad number with a crash

JCS canonicalisation follows IEEE-754, so an integer at or above 2⁵³ has no canonical form and
`rfc8785.dumps` raises. Python integers have no width, so a field bounded only below accepted one,
carried it into an idempotency payload, and `CanonicalizationError` escaped as HTTP 500:

```
settlement with paid_amount_vnd = 2^53        -> 500
transition with If-Match = 2^53               -> 500
intake-transition with If-Match = 2^53        -> 500
production-transition with If-Match = 2^53    -> 500
approval with resource_version = 2^53         -> 500
```

The money field is the one that matters most: an API that answers a malformed amount with "we
broke" invites a retry storm on the one route that takes money. The number was never valid. The
only defect was **where** it was refused.

### The settlement panel stated three things that were false

1. **Guardrail:** *"Chỉ một trường hợp được hỗ trợ… và tự lấy đồ về"* — only one case is supported.
   There have been two since `DEC-023` was ratified on 2026-08-26.
2. **Checkbox hint:** *"Giao bằng chặng giao hàng thuộc DEC-003 và chưa xây"* — delivery belongs to
   `DEC-003` and is not built. `DEC-003` is resolved and `FULFILMENT-001` shipped delivery legs.
3. **Success line:** *"đã ghi nhận khách tự lấy đồ"* on **every** settlement. False for a prepaid
   delivery, where `self_collection_recorded` stays false on purpose — the customer has paid and
   nobody has received anything yet.

Disclosure strings are compliance surface in this repository. A staff member reading (2) concludes
the shop cannot deliver; reading (3) concludes the customer has their laundry when the database says
otherwise.

## What must be true when this is done

1. All five entry points refuse 2⁵³ with a 4xx naming the field, not a 500.
2. The bound is defined once, beside the function whose precondition it is.
3. The guardrail describes both settlement shapes and names the decision that added the second.
4. The checkbox hint tells an operator what to do for a delivery order, and says the server refuses
   a mark that contradicts the order's mode.
5. The success line **reads** `self_collection_recorded` from the response rather than asserting it,
   and for a delivery order says what still has to happen.

## Where the bound lives, and why there

`MAX_CANONICAL_INT` is defined in `packages/domain/.../canonical.py`, next to `canonical_document`,
because it is that function's precondition rather than a fact about money or row versions. Any
request field whose value reaches a canonical document belongs inside it, and the API's Pydantic
models cite it instead of repeating a literal. Defining it in `main.py` would have made it a number
somebody could change without noticing which rule it encoded.

## A consequence worth expecting

Correcting the guardrail changes its **content digest**, and a disclosure slot id is a hash of its
text — so the registry's authored `DEC-010` binding had to be re-keyed deliberately. That is the
mechanism working, not fighting it: a reworded compliance string should force somebody to reconfirm
which decision it discloses. The reason is recorded in the generator beside the new key.

## Boundary

Not fixed here: `CONFIRMED → CANCELLED` is unguarded, which needs an owner decision about custody
and refund, not code. The promotion remains unwired; its window closed 2026-08-31, so it belongs
with the next campaign. `policy_version` on an approval envelope is still unverified.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
