# TASK-quote-approval-integrity-001 — an exact price cites an approval that need not exist

**Goal:** give `quote_revisions.approval_id` a foreign key to `approval_requests(id)`, so the check
that already requires an approved exact price to cite an approval also requires that approval to be
real.

**Domains:** `orders_audit`, `platform`

**Stable work item:** `QUOTE-APPROVAL-INTEGRITY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Nothing is broken today because no command path can produce the row this protects.
The risk is the other direction: the gap is the mechanism by which a completed order with no human
behind it can be written into any database this repository's own tooling can reach.

## Why this exists

Opened by the owner on 2026-08-22 with the instruction to decide and execute. It is the one piece of
`DEC-021` that is not a policy question, and `docs/DECISION_REQUEST_QUOTE_APPROVAL_2026-08.md` says so
in its own recommendation:

> whatever is chosen, `approval_id` should gain a foreign key to `approval_requests(id)`. Today the
> column must be filled but nothing checks that what fills it is real (…) That is a schema
> correction, not a policy question, and it is worth doing under whichever option is signed.

**The gap.** `0005_quote_snapshots.sql:68` requires `approval_id IS NOT NULL` whenever
`finality = 'APPROVED_EXACT'`, and `0005_quote_snapshots.sql:48` declares the column as a bare
`UUID NULL` with no reference. So the constraint reads as *an approved exact price cites its
approval* and enforces only *an approved exact price cites something shaped like a UUID*.

**Why it is load-bearing.** `packages/db/src/nha_trang_laundry_db/orders.py:126-137` treats a non-null
`approval_id` as one of the four conditions that make a quote orderable. Every option in the `DEC-021`
packet — staff attestation, two-person approval, auto-approve — routes the authority through an
approval envelope. All three are weaker than they read while the reference is unchecked.

**The gap is reachable, not theoretical.**
`packages/evals/src/nha_trang_laundry_evals/synthetic_incidents.py` builds a revision with
`approval_id=uuid4()` and writes it through the real `QuoteRepository`. It is shipped source in a
workspace package, not a test under `tests/`, and it is reachable from
`runner.py --mode synthetic-incidents`, which connects to whatever `DATABASE_URL` names.

## What must be true when this is done

1. A revision whose `approval_id` names no envelope is rejected by PostgreSQL, not by convention.
2. A revision whose `approval_id` names a real envelope still lands — the fix must not be "reject
   every exact quote", which would pass a naive negative test and be a much worse change.
3. The shipped fixture generator earns its envelope rather than inventing one, and the eval suites
   that depend on it still pass without any assertion being weakened.
4. Every fixture in the repository that manufactured an `APPROVED_EXACT` revision goes through one
   shared helper, so the next one written cannot quietly reintroduce the invention.

## Boundary — what this must not do

**It must not decide `DEC-021`.** The foreign key proves the referenced envelope exists. It does not
constrain the envelope's `action`, `resource_type` or `resource_id` to match the citing revision.
Binding those requires knowing which approval action finalises a quote, and that is precisely the
open question. The residual gap is named in the migration, in the shared helper's docstring, and in
the evidence record rather than closed by guesswork.

**It must not add a producer.** No code path that creates an `APPROVED_EXACT` revision is added.
`POST /internal/v1/stores/{store_id}/orders` still returns 409 for every quote this system can
produce, and `CONSOLE-ORDER-GAP-001`'s `#/gaps` disclosure stays true.

**It must not be converted to `NOT VALID` to get past a failure.** The migration fails only on a row
whose `approval_id` names no envelope, and no command path can produce one. A failure is a finding.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus the before/after measurement of the fixture generator against a live database, which is the
evidence this item rests on rather than the test run alone.
