# Decision request — model-provider data use, training, retention, and region (DEC-006, primary provider)

**Date:** 2026-08-18
**Status:** stance ratified 2026-08-18 by the business/security owner against the recommendation in
§4 — see the signature block, §5. **DEC-006 itself stays OPEN in `context/DECISION_REGISTRY.yaml`** —
this document cannot resolve it; see §3.
**Trigger:** `PROVIDER-ACCESS-001` is `BLOCKED` on DEC-006; `AGENT-002` and everything after it in
Phase 3 sits behind `PROVIDER-ACCESS-001`.
**Assessments this rests on:** `evidence/provider/openai-data-controls-review-v1.yaml`
(`OPENAI-DATA-CONTROLS-REVIEW-2026-07-28`), `context/DECISION_REGISTRY.yaml` DEC-006/DEC-007.

---

## 0. What this request does not do

- It does not create an OpenAI organization, project, or credential — that is `PROVIDER-ACCESS-001`,
  and it is separately blocked.
- It does not resolve DEC-006. Full resolution needs verified account settings (`store:false` behind
  a real credential, Zero Data Retention account approval) that do not exist yet — see the evidence
  file's own `NOT_VERIFIED` rows. What this document *can* get signed is the owner's risk-acceptance
  **stance**, so that once `PROVIDER-ACCESS-001` produces a credential, verification has a target to
  confirm rather than a fresh negotiation.
- It does not give legal advice. Vietnamese customer data leaving Vietnam to a US-hosted processor is
  a real personal-data-protection question (Vietnam's Nghị định 13/2023/NĐ-CP and related rules), and
  this document is not qualified to tell you it is compliant — see §4.

## 1. What the existing review already found

`evidence/provider/openai-data-controls-review-v1.yaml`, dated 2026-07-28, documentation-reviewed but
**not account-verified**:

| Question | Documented position | Verified? |
|---|---|---|
| Training use | Not used to train by default, unless the org opts in | Documentation only |
| Retention / abuse-log exception | Default abuse-monitoring logs may retain content up to 30 days | Documentation only |
| Region / subprocessors | Not reviewed | `NOT_APPROVED` |
| Deletion path | Not reviewed | `NOT_APPROVED` |
| Incident terms | Not reviewed | `NOT_APPROVED` |
| Zero Data Retention (`store:false`) | Depends on account approval and request features; the current runtime (OpenClaw 2026.7.1-2) forces `store:true` unless the model's compatibility set says otherwise | `NOT_VERIFIED` |
| Dedicated non-personal credential | Required by policy | `NOT_VERIFIED` — no credential exists |
| Security / Privacy approval | — | `NOT_APPROVED` on both |

The release-effect block already fails closed correctly: `real_customer_data_allowed: false`,
`public_ingress_allowed: false`, `automatic_send_allowed: false`. Signing nothing keeps that.

## 2. The stance question this document asks

Before spending PROVIDER-ACCESS-001's engineering effort chasing account-level verification, it is
worth confirming the owner is willing **in principle** to proceed on OpenAI's standard terms at all,
given what is already known and not yet known:

| Answer | Consequence |
|---|---|
| **Proceed toward verification** (conditional) | `PROVIDER-ACCESS-001` becomes worth doing: obtain a dedicated project credential, configure and prove `store:false`, and get a named Security/Privacy sign-off on region/subprocessors/deletion/incident terms — all still required before `real_customer_data_allowed` can flip. This stance does not itself flip it. |
| **Do not proceed on current terms** | `PROVIDER-ACCESS-001`, `PROVIDER-TRANSPORT-001`, `MODEL-PIN-001`, `AGENT-002`, and everything after them in the Phase 3 chain stay blocked. The custom-runtime path (`DEC-007`) has no alternative provider currently evaluated for the primary role — this would need a new provider assessment, not a resigned OpenAI one. |
| **Proceed only with a named legal check first** (recommended) | Same engineering unlock as row 1, plus: before real customer conversation/photo data ever reaches the endpoint (i.e., before this gates `real_customer_data_allowed: true`, not before `PROVIDER-ACCESS-001` starts), get an actual reading — even a brief one — on whether sending Vietnamese customers' personal data to a US-hosted processor satisfies Vietnam's personal-data-protection rules for this business. This document is not that reading. |

## 3. Why this can't just be decided by re-reading the evidence file harder

The two hardest rows — region/subprocessors and the legal basis for cross-border transfer — are not
engineering facts I can look up and confirm; they are (a) OpenAI's actual current enterprise/API
contract terms, which change and which only the account holder can pull up authoritatively, and (b) a
Vietnamese-law compliance question, which needs a qualified read, not an inference from a documentation
page. Treat rows 3–5 of the table above as **owed to a person**, not to more reading.

## 4. Recommendation

Sign the stance as **"proceed toward verification, with a legal check gating real customer data, not
gating `PROVIDER-ACCESS-001` itself."** This lets the engineering chain move (credential, transport,
model pin) without pretending the region/legal question is answered — the fail-closed
`real_customer_data_allowed: false` stays exactly where it is until that specific gate is separately
cleared.

## 5. Signature block

Fill and commit. Unsigned keeps the current behaviour: `PROVIDER-ACCESS-001` stays `BLOCKED`, DEC-006
stays `OPEN`, every capability stays `NOT_AUTHORIZED`.

```yaml
decision_dec_006_stance_primary_provider:
  answer: PROCEED_TOWARD_VERIFICATION
  owner: SECURITY_PRIVACY_OWNER
  decided_at: '2026-08-18'
  legal_check_commissioned: NOT_YET — required before real_customer_data_allowed flips; open item
  rationale: >-
    Owner ratified proceeding with PROVIDER-ACCESS-001's engineering chain (credential,
    store:false verification, transport, model pin) on OpenAI's documented terms, conditional on
    a real legal read on cross-border Vietnamese customer PII gating real_customer_data_allowed
    specifically — not gating PROVIDER-ACCESS-001 itself. DEC-006 stays OPEN until that
    verification and legal check both land.
```
