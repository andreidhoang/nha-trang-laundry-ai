# TASK-test-isolation-001 — stop the integration suite from lying about order

**Goal:** make every database integration test start from a known state, so a failure means a
defect.

**Domains:** `platform`

**Stable work item:** `TEST-ISOLATION-001`

**Stage:** M4A
**Risk:** MEDIUM — the risk is destabilising a green suite while trying to make it trustworthy.

## Why this exists

The integration tests share one PostgreSQL database and never clean up, so state accumulates across
tests and across runs. In a single session that produced five order-dependent failures, none of
which was a defect:

- a pipeline test claimed whichever job the queue held rather than the one it enqueued;
- the unknown-send exception queue filled its page with older rows, hiding the receipt under test;
- a retention class was already under a legal hold left by an earlier test;
- a retention class was already published, so the "no schedule" path could not be reached;
- an audit timeline contained a neighbour run's events.

Each was patched around with a drain loop, a release loop, or a weakened assertion. **Those
workarounds are now permanent noise in the tests**, and they encode the accumulation rather than
removing it. `ENV-INTEGRITY-001` made the same argument about a harness producing false red: a suite
that cries wolf teaches its readers to discount it, and every completion claim in this repository
rests on a gate result meaning what it says.

## What was already tried, and why it is not simply done

Truncation is the right mechanism. `DELETE` cannot work, because most tables carry
`reject_ledger_mutation` as a `BEFORE DELETE ... FOR EACH ROW` trigger; `TRUNCATE` does not fire
row-level delete triggers, so it clears state without weakening the ledger guarantee for application
code.

**The attempt hung, and the reason is the useful part of this packet.** `TRUNCATE` takes an
`ACCESS EXCLUSIVE` lock on every named table. Several tests deliberately open additional connections
to exercise concurrency — the inbox replay race, the send-claim race, the two-worker claim race — and
a connection left open by a joined-but-not-closed thread holds locks that make the next test's
truncate wait indefinitely.

Any implementation must therefore handle:

- a bounded `lock_timeout` and `statement_timeout` on the reset, so a stuck reset fails loudly
  instead of hanging the suite;
- deterministic teardown of every extra connection a concurrency test opens, including on the
  failure path;
- the reset running once per test, not once per connection, so a test that opens three connections
  does not truncate under its own feet.

## Constraints

- Do not weaken or drop a ledger trigger to make cleanup easier. The immutability guarantee is the
  reason this system can be trusted about money and consent.
- Do not make tests pass by loosening assertions; the assertions that were weakened during the
  workaround period should be tightened again once isolation holds.
- Keep the concurrency tests genuinely concurrent. They are the only proof that the claim lease, the
  dedupe constraint and the suppression race behave under contention.
- Preserve `schema_migrations`; truncating it breaks the migration ledger the fixtures depend on.

## Required tests

- the suite passes when modules are run in reverse order and individually;
- a test that leaves a legal hold, a pending job or an unresolved receipt does not affect the next;
- the reset fails loudly rather than hanging when a lock cannot be acquired;
- the drain and release loops added as workarounds are removed, and their tests still pass.

## Done when

- no test depends on what another test left behind;
- the workaround loops are gone;
- the full gate battery passes twice in succession with no reordering of the second run;
- rollback is restoring the shared-state fixtures, which returns the suite to its current behaviour
  rather than breaking it.
