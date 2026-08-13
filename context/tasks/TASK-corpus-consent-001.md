# TASK-corpus-consent-001 — lawful basis and reviewed anonymization of message history

**Goal:** make real customer language usable as evaluation material without making it a privacy
incident.

**Domains:** `privacy_consent`, `business_truth`, `evaluation_release`

**Stable work item:** `CORPUS-CONSENT-001`

**Stage:** M4A
**Risk:** HIGH — this item decides whether real people's messages can be used at all.

## Why this exists

`EVAL-LANGUAGE-CORPUS-001` needs 300 cases at the manifest's declared distribution, and half of that
distribution — missing diacritics, typos, abbreviations — cannot be authored credibly by an engineer
at a keyboard. Real history is the only honest source, and real history is personal data.

This item establishes the basis and the method. It produces no corpus; that is
`EVAL-LANGUAGE-CORPUS-001`.

## What must be established

- **A lawful basis or consent** for using existing customer message history for system evaluation.
  Written, dated, and specific about the purpose. "We have the messages" is not a basis.
- **An identifier substitution method that preserves linguistic shape.** A Vietnamese name becomes a
  different Vietnamese name of similar length and diacritic density; a phone number becomes a
  plausible non-routable number of the same format. Replacing them with `NAME_1` and `PHONE_1`
  destroys exactly the signal the corpus exists to capture, and produces a corpus that tests the
  wrong thing.
- **A mapping table that never enters the repository** — not in a fixture, not in a test, not in an
  evidence file, not in a comment, not in a commit that is later reverted.
- **Human review of free text.** Substitution catches identifiers in fields. It does not catch
  "the lady who runs the pharmacy opposite the market", which is identifying and is exactly how
  people actually write.

## Constraints

- No raw PII fixture reaches the repository under any circumstance, at any stage, including
  intermediate working files.
- Free text is reviewed by a human before it is eligible. An automated redactor's output is a
  candidate, not a clearance.
- If the lawful basis cannot be established for some subset of history, that subset is excluded.
  Excluding data is an acceptable outcome; using it because excluding it is inconvenient is not.
- This item does not resolve `DEC-008`. Retention of the source history is a separate obligation
  owned by `RETENTION-001`.
- Do not begin substituting before the basis exists.

## Done when

- the lawful basis or consent is recorded, dated, and scoped to the purpose;
- the substitution method is documented and demonstrated on a sample, preserving linguistic shape;
- the mapping table is stored outside the repository and its location and custodian are recorded;
- a human has reviewed the free text of every cleared item;
- no raw PII fixture exists anywhere in the repository, proven by scan;
- `EVAL-LANGUAGE-CORPUS-001` can start.
