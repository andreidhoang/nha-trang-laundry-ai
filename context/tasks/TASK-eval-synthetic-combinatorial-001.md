# TASK-eval-synthetic-combinatorial-001 — the 500-case combinatorial suite

**Goal:** raise the largest declared dataset minimum using the one source of expected values that
needs no owner decision and no customer data: the deterministic domain itself.

**Domains:** `evaluation_release`, `agent_tools`, `pricing`, `promotion_delivery_sla`

**Stable work item:** `EVAL-SYNTHETIC-COMBINATORIAL-001`

**Stage:** M4A
**Risk:** MEDIUM — the risk is generating cases whose expected values were written by hand and are
therefore a second, divergent pricing implementation.

## Why this exists

`specs/evals/eval-manifest-v1.yaml` declares `REQUIRED_DATASET_MINIMA_NOT_MET` as a release blocker
and sets `synthetic_combinatorial_suite.minimum_cases: 500` with
`maximum_wrong_monetary_values: 0`. `actual_dataset_inventory.SYNTHETIC_COMBINATORIAL` is `0`, and
no work item carried it. `EVAL-CORPUS-001` covers the frozen regression and adversarial layers only.

This is the largest single bucket in the manifest, it is **500 of the 1,300 required cases**, and it
is the only corpus item with no external dependency. `CORPUS-CONSENT-001` gates mined human
language; `PUBLIC-POLICY-001` gates the public corpus. This one is available immediately.

## Required design

Generate, do not author:

- enumerate the input space from `packages/domain` — service codes, quantity bases, weight bands
  around every tier and minimum, promotion windows and eligibility states, delivery distance and
  vehicle boundaries, SLA and capacity states;
- compute every expected monetary value, policy outcome and disclosure by **calling the
  deterministic engines**, never by writing a number into a fixture;
- freeze the generated set with a content hash so a domain change that moves an expected value shows
  up as a corpus diff rather than as a silently rewritten expectation.

The 6 kg pricing cliff and the tier minimums are confirmed business rules. A generator that smooths
them is wrong even if every case then passes.

## Constraints

- No provider call and no credential; this item produces cases, not model results.
- No real customer data, no raw PII fixture, no mined utterance — that is
  `EVAL-LANGUAGE-CORPUS-001`.
- Do not modify `packages/domain` to make generation convenient. If the domain cannot express a
  boundary, that is a finding to record, not a domain edit to smuggle in.
- Update `actual_dataset_inventory` in the manifest to the real count. Never write a count the
  corpus does not contain.
- Cases covering an unresolved decision (`DEC-001` through `DEC-004`) must expect the fail-closed
  outcome, not a guessed policy.

## Required tests

- the generated corpus contains at least 500 cases and the manifest inventory matches the file;
- every expected monetary value is reproduced by re-running the deterministic engines;
- every declared boundary appears at, just below and just above the boundary;
- a deliberately corrupted expected value is rejected by the corpus validator;
- no fixture contains a phone number, name, address or any other personal identifier.

## Done when

- `SYNTHETIC_COMBINATORIAL` in the manifest inventory is at or above 500 and matches the corpus;
- the full gate battery passes with no required skips;
- every capability still reports `NOT_AUTHORIZED` and the release blocker list is unchanged except
  where this dataset minimum genuinely no longer binds;
- rollback is deleting the generated corpus and restoring the inventory count, which removes no
  other evidence.
