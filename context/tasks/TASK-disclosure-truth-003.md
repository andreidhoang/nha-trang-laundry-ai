# TASK-disclosure-truth-003 — the console tells the owner their own decision is still open

**Goal:** correct the one console disclosure that describes a `RESOLVED` decision as still open, and
delete the `xfail(strict=True)` marker that has been carrying it as a reported-not-fixed incident.

**Domains:** `platform`, `privacy_consent`

**Stable work item:** `DISCLOSURE-TRUTH-003`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW to change, MEDIUM to leave. Nothing breaks; the shop owner is told, in Vietnamese, on
the screen where they take money, that a decision they made four days earlier is still pending.

## Why this exists

Corrective item in the `DISCLOSURE-CONTRACT-001` / `DISCLOSURE-BIND-002` series. The assertion
already existed and already failed — `test_no_policy_bound_disclosure_describes_a_resolved_decision_as_open`
was written as `xfail(strict=True)` with a reason that names the incident and says explicitly that
"a string found false today is an incident to report, not a refactor". This item is the fix that
marker was waiting for.

**The false sentence.** `apps/web/src/screens/orderDetail.js`, the Tất toán (settlement) panel:

> Trả thiếu, trả thừa, đặt cọc, trả góp và ghi nợ đều bị từ chối **kèm mã quyết định đang mở**

"…are all refused **with an open decision code**." The decision is `DEC-010`, and `DEC-010` is
`RESOLVED`, decided 2026-08-18: partial payment, deposits, instalments and `ON_ACCOUNT` credit stay
`NOT_SUPPORTED` **by decision, deliberately deferred** — the registry's own words are "by decision,
not by omission".

**Why the distinction matters to the person reading it.** "A decision is still open" invites the
owner to wait for an answer that has already been given. "The owner decided to defer this" tells them
the refusal is the intended behaviour and there is nothing pending. Same refusal, opposite
instruction to the reader.

**What is not wrong.** The server genuinely returns a decision code —
`SettlementNotSupported.decision` (`packages/domain/src/.../settlement.py:87-95`) travels to the
client intact. The console is right that a code accompanies the refusal. Only the word "đang mở"
(still open) is false.

## What must be true when this is done

1. No `POLICY_BOUND` disclosure whose bound decision is `RESOLVED` contains "đang mở", "chưa chốt" or
   "chưa quyết". Audited across all 165 slots, not just the one found.
2. The `xfail` marker is deleted, and the test guards the class going forward rather than recording
   one instance of it.
3. The disclosure registry is regenerated rather than hand-edited — slot ids are content hashes, so
   the authored `POLICY_BOUND` binding is rekeyed to the new hash in the same change.
4. `apps/web/sw.js` is regenerated, because a console string that changes without its fingerprint
   changing is a string that does not reach a returning operator.

## Boundary

**It must not decide anything, or change any behaviour.** `DEC-010` is already resolved; this item
reports its status accurately. No route, request model, response shape, reason code or refusal path
is touched — the settlement endpoint refuses exactly what it refused before, for exactly the same
reason. The only change is what the operator is told about why.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
