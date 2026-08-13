# TASK-eval-language-corpus-001 — the 300-case normal-language suite

**Goal:** cover how customers in Nha Trang actually write, at the distribution the manifest declares.

**Domains:** `evaluation_release`, `agent_tools`, `privacy_consent`

**Stable work item:** `EVAL-LANGUAGE-CORPUS-001`

**Stage:** M4A
**Risk:** HIGH — this corpus is built from real customer history, so it is the item most likely to
put personal data somewhere it must never be.

## Why this exists

`specs/evals/eval-manifest-v1.yaml` sets `normal_language_suite.minimum_cases: 300` with a required
task success rate of 0.95 and a required safe-abstention rate of 0.98 on ambiguous cases.
`actual_dataset_inventory.NORMAL_LANGUAGE` is `0`. `EVAL-CORPUS-001` does not carry it — its
declared evidence is the frozen regression minimum, the adversarial layer and grader calibration.

The manifest also fixes the distribution, and it is not uniform:

| Slice | Share |
|---|---:|
| `colloquial_vietnamese` | 25% |
| `polite_formal` | 20% |
| `missing_diacritics` | 15% |
| `typo_or_abbreviation` | 15% |
| `ambiguous_service` | 15% |
| `multi_intent` | 10% |

Half of that — missing diacritics, typos and abbreviations — is exactly what a corpus written by an
engineer at a keyboard will under-represent, because engineers type correctly.

## Required design

- Source utterances from the consent-cleared, reviewed history produced by `CORPUS-CONSENT-001`.
  This item does not establish the lawful basis and must not proceed ahead of it.
- Substitute identifiers while preserving linguistic shape: a Vietnamese name becomes a different
  Vietnamese name of similar length and diacritic density, not `NAME_1`. A corpus that reads like a
  placeholder file tests the wrong thing.
- Label each case with its distribution slice and, for ambiguous cases, with the specific
  fail-closed outcome that counts as a safe abstention.
- Calibrate the grader against a human-labelled hold-out before quoting any success rate.

## Constraints

- The identifier mapping table never enters the repository, in any form, including tests.
- No raw PII fixture, no raw provider payload, no chain-of-thought.
- An ambiguous case expects `REQUIRE_HUMAN` or `NOT_SUPPORTED`; it never expects a guessed answer,
  and a grader that rewards guessing is a defect in the grader.
- Cases touching `DEC-001` through `DEC-004` expect the fail-closed outcome.
- No provider call is made by this item; it produces cases and grading, not model results.

## Required tests

- the corpus holds at least 300 cases and matches the declared distribution within a stated
  tolerance;
- every case carries a slice label and ambiguous cases carry an abstention expectation;
- the grader reproduces the human hold-out labels at the calibration threshold;
- a case containing an unsubstituted identifier is rejected by the corpus validator;
- the manifest inventory matches the corpus file exactly.

## Done when

- `NORMAL_LANGUAGE` in the manifest inventory is at or above 300 and matches the corpus;
- grader calibration is recorded with its hold-out and its disagreement rate;
- the full gate battery passes with no required skips;
- rollback is deleting the corpus and restoring the inventory count; no consent record, audit entry
  or other evidence is touched.
