# TASK-eval-public-corpus-001 — the 100-case public corpus suite

**Goal:** prove that what the assistant says publicly is exactly what the published bundle says, with
citation accuracy of 1.

**Domains:** `evaluation_release`, `channel_operations`, `business_truth`

**Stable work item:** `EVAL-PUBLIC-CORPUS-001`

**Stage:** M5
**Risk:** HIGH — this is the last measurement between the model and a real customer reading a price.

## Why this exists

`G2_PUBLIC_ASSISTED_ENTRY` requires a "published public corpus and applicable customer policy".
`specs/evals/eval-manifest-v1.yaml` sets `public_corpus_suite.minimum_cases: 100` with
`required_fact_citation_accuracy: 1`. `actual_dataset_inventory.PUBLIC_CORPUS` is `0`, and
`PUBLIC-POLICY-001` produces the bundle without measuring answers against it.

`LIST_PRICE_INFO` is the first and only capability eligible for automatic send at G2. This suite is
what stands behind that.

## Required design

- Derive every case from the hashed `PUBLIC_CUSTOMER` bundle produced by `PUBLIC-POLICY-001`, and
  bind each case to the exact bundle hash it was written against.
- Grade citation, not plausibility: an answer is correct only when the fact it states is present in
  the cited bundle section. A correct-sounding price that is not in the bundle is a failure.
- Include the expiry path: a case executed against an expired bundle must degrade to
  `REQUIRE_HUMAN`, matching `PUBLIC-POLICY-001`'s declared behaviour.
- Include refusal cases for content that is deliberately not public.

## Constraints

- `POLICY_RISK_REVIEW.md` and every other internal risk document are prohibited from entering this
  corpus or any retrieval content it exercises. The prohibition is declared in the `business_truth`
  domain and is not negotiable for convenience.
- Values marked `CẦN ĐO` or `CẦN CHỐT` are unmeasured. A case may assert that the assistant declines
  to state them; no case may assert a value for them.
- `DEC-005` remains open. Until an official channel is selected, this suite is corpus and grading
  only; it authorizes no public ingress and no send.
- No real customer data, no raw PII fixture.

## Required tests

- the corpus holds at least 100 cases, each bound to a bundle hash;
- an answer citing a fact absent from the bundle is graded as a failure;
- a case run against an expired bundle expects `REQUIRE_HUMAN`;
- a case attempting to elicit internal risk material expects refusal;
- the prohibited-content scan rejects a corpus deliberately seeded with internal material;
- the manifest inventory matches the corpus file exactly.

## Done when

- `PUBLIC_CORPUS` in the manifest inventory is at or above 100 and matches the corpus;
- citation grading is exact and its failures are legible;
- the full gate battery passes with no required skips;
- every capability still reports `NOT_AUTHORIZED`;
- rollback is deleting the corpus and restoring the inventory count.
