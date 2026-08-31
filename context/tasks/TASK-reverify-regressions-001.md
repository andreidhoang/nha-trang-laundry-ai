# TASK-reverify-regressions-001 — the fixes that carried their own defects

**Goal:** close the six worst findings of the second nine-lens verification against `f9c5140`, ten
of whose twenty-five surviving findings were regressions of fixes made the same day.

**Domains:** `orders_audit`

**Stable work item:** `REVERIFY-REGRESSIONS-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. One migration is rewritten before it has ever been applied to a populated
database; five are authorization or refusal-shape corrections.

## Why this exists

`CROSS-STORE-INTEGRITY-001` and `BOUNDS-AND-TRUTH-001` landed on 2026-08-31. A re-verification the
same day found that some of their fixes had introduced defects of their own, and that one older
defect they had walked past was worse than anything they closed.

1. **Migration `0034` could not be applied to any populated database.** Its own comment said
   "ADD COLUMN is DDL and does not fire `reject_ledger_mutation`" — correct — and the next two
   statements were `UPDATE`s, which are DML and do. It applied cleanly to every test, because
   migrations there always run against a fresh database where the backfill touches zero rows. It
   would have failed on every real deployment. The error it raised named a remedy that was also
   impossible: "resolve or delete them deliberately", when the same trigger blocks `DELETE`.
2. **`POST /internal/v1/approvals` answered 500 for a non-member.** `ApprovalAuthorizationError`
   was not in the route's except tuple, so the membership check added that morning crashed instead
   of refusing. A security fix that 500s is not a security fix.
3. **The resource check added with it was an existence oracle** — two distinct refusal strings told
   a member of any store whether a UUID was a real resource elsewhere. The same one-bit leak the
   sibling function in the same commit closed, reintroduced two functions away.
4. **The audit-timeline scoping blinded its own users.** `approval_requests` was missing from the
   ownership union, so an owner reading an approval they had just decided saw nothing.
5. **A member of any store could append a priced revision to another store's quote.** Not that
   day's regression — older, and worse. `create_quote` proved membership against the store in the
   URL path, then the reprice `UPDATE` found the quote by id alone. A confused deputy: authority
   established over one resource, exercised on another.
6. **Counter-ticket numbering had no lock**, so two tills issuing at the same moment could compute
   the same `max(...) + 1`.

## What must be true when this is done

1. `0034` applies to a database holding approval requests, and its error names a remedy that works.
2. No route answers a membership refusal with 500.
3. A refusal cannot distinguish "does not exist" from "not yours" on the approval path.
4. An owner reading their own store's approval sees it in the audit timeline.
5. A reprice writes only to a quote in the store whose membership was proven.
6. Two concurrent ticket issues produce two different numbers.

## Boundary

Not fixed here: the remaining findings of the same verification, which are carried by
`REVERIFY-REGRESSIONS-002` and `COUNTER-DEFECTS-001`.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
