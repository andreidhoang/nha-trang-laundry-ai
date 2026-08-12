# Public Customer Policy and Corpus Specification v1

**Ngày phát hành:** 2026-08-12
**Trạng thái:** `SPEC_APPROVED_NO_BUNDLE_PUBLISHED`
**Nguồn:** `specs/IMPLEMENTATION_ROADMAP_V1.md` §11 (public customer policy publication gate),
ADR-0005
**Contract:** `contracts/public-policy-bundle-v1.schema.json`
**Work item:** `PUBLIC-POLICY-001`

`G2_PUBLIC_ASSISTED_ENTRY` requires *"published public corpus and applicable customer policy"*, and
`CAPABILITY_STATUS.yaml` lists a published disclosure policy as a `LIST_PRICE_INFO` blocker. This
specification makes that publication gate executable.

## 1. Vấn đề

Everything the public agent can say to a customer must come from a bundle that a human approved, that
is versioned and hashed, and that can be withdrawn. Two failure modes make this non-negotiable here.

**The corpus contains material that must never reach a customer.** `POLICY_RISK_REVIEW.md` holds the
shop's current liability clauses, which contradict each other: weight-based washing has no per-item
check-in, yet the terms claim joint inspection at handover. A retrieval system with no audience
boundary would quote those terms to a customer during a damage dispute.

**Unresolved policy must not be improvised.** `DEC-001` to `DEC-004` are open — weight rounding,
promotion eligibility event, delivery beyond 6km, rewash/loss/damage. The agent must be structurally
unable to answer them, not merely instructed not to.

## 2. Audience classes

| Class | Contents | Public agent access |
|---|---|---|
| `PUBLIC_CUSTOMER` | Owner-approved published facts | yes, only from an active bundle |
| `INTERNAL_OPERATIONS` | Staff procedures, capacity, cost | never |
| `INTERNAL_RISK` | `POLICY_RISK_REVIEW.md`, legal analysis, drafts | never |
| `DRAFT` | Unpublished or withdrawn bundle versions | never |

The public runtime must be **technically unable** to retrieve the last three, per roadmap §11. This
is an allowlist keyed on the active bundle hash, not a filter applied after retrieval. A filter can
be bypassed by a prompt; an allowlist cannot return what it does not contain.

## 3. Publication pipeline

```text
approved structured business truth      (owner-published configuration)
  -> PUBLIC_CUSTOMER draft bundle
  -> prohibited/internal-content scan   (§3.1, automated, fail-closed)
  -> business review                    (owner reads every fact)
  -> legal review where required        (§3.2)
  -> deterministic fact/eval dry run    (§3.3)
  -> owner publish approval
  -> immutable version + effective period + SHA-256
  -> public-agent allowlist
```

### 3.1 Prohibited-content scan

Automated and fail-closed. It rejects a bundle containing: any text traceable to an
`INTERNAL_RISK` document; staff names, phone numbers or internal identifiers; capacity or cost
figures; any claim about an open decision (`DEC-001`–`DEC-004`); any commitment language for a
capability not yet authorized; and any absolute promise about turnaround for item classes marked
`HUMAN_ETA_REQUIRED` in `BUSINESS_TRUTH_INTAKE.md`.

A scan that cannot classify a fact rejects it. Ambiguity is never resolved toward publication.

### 3.2 Legal review

Required when the bundle contains liability, compensation, data-processing or contractual terms.
Given the contradiction noted in §1, the first bundle **must not** contain liability terms at all
until `DEC-004` resolves them. It ships with facts the shop can state without qualification: price
list, opening hours, address, contact, service catalogue, published promotion dates.

### 3.3 Deterministic dry run

Every priced fact in the bundle is re-derived from the deterministic domain engines and must match
exactly. A bundle claiming a price the pricing engine does not produce is rejected — including the
6kg cliff, which the bundle must state as the rule produces it, never smoothed.

### 3.4 Owner approval

The owner approves the bundle as a whole, having read every fact. Approval is recorded with the
bundle hash. An owner approving a hash they have not seen the contents of is not approval.

## 4. Bundle identity

Per `public-policy-bundle-v1.schema.json`: immutable `bundle_id`, monotonic `version`, `audience`,
`effective_from` / `effective_until`, `content_sha256`, `approved_by`, `approved_at`, and the list of
capabilities it authorizes content for.

The public agent resolves content **only** by active bundle hash. A bundle outside its effective
period is not active, and an expired bundle degrades the agent to `REQUIRE_HUMAN` rather than falling
back to an older version — silently serving stale prices is worse than not answering.

## 5. Correction workflow

Roadmap §11, made concrete. Triggered when a published fact is discovered to be wrong.

1. **Disable the faulty version.** It becomes non-active immediately; no re-approval needed to stop
   using something wrong.
2. **Block new sends and invalidate unexecuted approvals** derived from it. Approved-but-unsent
   outbox rows referencing the bad bundle are cancelled, not sent.
3. **Publish** the last valid version or an owner-approved correction through the full §3 pipeline.
   The pipeline is not shortened because the situation is urgent.
4. **Identify the affected set** — messages, quotes and orders that used the bad version, by bundle
   hash. This is why every outbound message records the hash it was derived from.
5. **Create an owner-approved customer correction task** where a customer acted on the bad fact.
   Whether to notify is the owner's decision; whether the system can identify who to notify is not
   optional.
6. **Preserve in audit**: source version, affected-set query, decisions, and every send.

A correction drill runs before G2 with a deliberately wrong price, proving steps 1, 2 and 4 execute
correctly. Untested correction machinery is the reason a small pricing error becomes a large one.

## 6. Corpus construction and the eval set

The regression corpus and the public bundle are different artifacts with different rules.

The **bundle** is what the agent may say. The **corpus** (`EVAL-CORPUS-001`) is what customers
actually asked, used to test whether the agent says the right thing.

Corpus construction from the shop's real Zalo and Facebook history is governed by
`CORPUS-CONSENT-001` and is a prerequisite, not a footnote:

- customer consent or a lawful basis is established before any message is copied;
- names, phone numbers, addresses, order codes and amounts are replaced with synthetic values that
  preserve linguistic shape — a Vietnamese address must still look like one, so tokenization
  behaviour is unchanged;
- the mapping table never enters the repository;
- output is reviewed by a human before it becomes a fixture, because anonymization tooling
  reliably misses free-text mentions;
- `AGENTS.md` forbids raw PII fixtures; an anonymization step that has not been reviewed has not
  satisfied it.

## 7. Required verification before `PUBLIC-POLICY-001` completes

- one complete `PUBLIC_CUSTOMER` bundle published, versioned, hashed, effective-dated;
- the public runtime is **technically unable** to retrieve `INTERNAL_RISK`, `INTERNAL_OPERATIONS` or
  `DRAFT` content — proven by attempted retrieval, not configuration review;
- prohibited-content scan rejects a seeded `POLICY_RISK_REVIEW.md` excerpt;
- deterministic dry run rejects a seeded wrong price, including a smoothed 6kg value;
- correction drill executes steps 1, 2 and 4 against a deliberately wrong published fact;
- an expired bundle degrades the agent to `REQUIRE_HUMAN` rather than serving an older version;
- every outbound message records the bundle hash it was derived from.
