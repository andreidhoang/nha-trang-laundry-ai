# Decision request — the tiered-inference and multimodal path

**Date:** 2026-08-13
**Status:** Decision B (DEC-009) ratified 2026-08-18 by the security/privacy owner against the
recommendation in §3 — see `context/DECISION_REGISTRY.yaml` DEC-009 and the signature block in §7.
Decisions A (ADR-0008) and C (DEC-006 for a second provider) remain unanswered — neither was part of
the 2026-08-18 ratification.
**Trigger:** an NVIDIA build-platform API credential became available to the owner on 2026-08-13.
**Assessment this rests on:** `docs/TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md`
**Decision anchor:** `docs/adr/0008-inference-topology-and-multimodal-scope.md` (**proposed**, not accepted)

`MODEL-ROUTE-001` and `MULTIMODAL-PERCEPTION-001` are queued `BLOCKED`. Both blocking conditions
name decisions, not engineering. This document turns those blocking conditions into questions that
can be answered and signed, so the items either unblock or close.

---

## 0. What this request does not do

- It does not add an NVIDIA dependency, transport, or model candidate.
- It does not edit `runtime/model-registry-v1.yaml` or any hash-pinned file.
- It does not change `DEC-005`, `DEC-006`, or `DEC-009` — all three stay `OPEN`.
- It does not store, name, or transmit a credential value. See
  `docs/runbooks/provider-credentials.md`.
- It authorizes no provider call. Every capability remains `NOT_AUTHORIZED`.

## 1. The correction that changes the arithmetic

`docs/TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md` §2 argues that the real case for NVIDIA is not
cost but `DEC-006`: **self-hosted inference on owner hardware collapses six of `DEC-006`'s seven
questions by removing the third party the questions are about.**

A credential for the hosted NVIDIA build platform is not that. It is an API key for a **second
third-party inference provider**. It therefore:

- collapses **none** of the six counterparty questions — it duplicates them for a new counterparty;
- touches at most the seventh, the credential question, and only partly: the registry gate requires
  `dedicated_service_credential_verified`, and a build or trial key issued to a personal account does
  not satisfy "dedicated service credential" even after rotation;
- **adds** a required artifact: a provider data-controls review record for NVIDIA, the counterpart of
  `evidence/provider/openai-data-controls-review-v1.yaml`.

That last item is blocked at the contract layer, not the paperwork layer:
`specs/contracts/provider-data-evidence-v1.schema.json` pins `scope.provider` to `const: "openai"`
and `scope.api` to `const: "Responses"`, and the schema file is itself one of the 21 files hash-pinned
by `evidence/agent-shadow/local-synthetic-suite-v1.json`. A second provider therefore needed a schema
version *and* an evidence re-pin before its posture could be recorded at all. The re-pin was delivered
on 2026-08-13; the schema version has not been, so the posture still cannot be recorded.

**Net effect of having the key:** the engineering distance is unchanged and one new artifact is owed.
The key is not an unblock. Treat it as evidence that the owner is willing to fund a provider, which is
useful input to §4 and nothing more.

**Tiếng Việt:** key NVIDIA dạng hosted **không** làm giảm bảy câu hỏi của `DEC-006` — nó thêm một đối
tác thứ ba mới, nên phải trả lời lại sáu câu hỏi đó cho NVIDIA. Chỉ có suy luận tự vận hành
(self-hosted) mới xóa được sáu câu hỏi ấy, và ADR-0008 đã tính: ở quy mô một cửa hàng, GPU đắt hơn
nhân viên CSKH 2,5–6 lần.

---

## 2. Decision A — accept or reject ADR-0008

**Owner:** `PRODUCT_ENGINEERING_OWNER`
**Question:** does one conversation turn use **one** model, or a **route** of models with different
roles?

| Answer | Consequence |
|---|---|
| **One model per turn** | `MODEL-ROUTE-001` closes as **not-required**, not built. `MULTIMODAL-PERCEPTION-001` then needs a multimodal *single* model, or closes too. This is the cheaper answer and nothing in the current workload contradicts it. |
| **A route** | `MODEL-ROUTE-001` proceeds: per-role pinning, per-call attribution in `ResponsesRuntimeEvidence`, and a decision on `limits.max_model_calls`, currently `3` — perception + execution + one escalation consumes the entire budget with nothing left for a retry. |
| **Defer** | Both items stay `BLOCKED`. This is the current state and costs nothing today. |

Answering "one model per turn" is a real outcome, not a failure. It removes two items from the queue.

## 3. Decision B — `DEC-009`, may customer media reach an inference endpoint at all

**Owner:** `SECURITY_PRIVACY_OWNER`
**Registry status:** `OPEN`, fail-closed `NOT_SUPPORTED`
**Why it exists:** `ChannelContent` already carries an `IMAGE` kind
(`packages/contracts/src/nha_trang_laundry_contracts/channel_envelope.py:58`) and attachment
references, and nothing in the specification states whether those bytes may ever be fetched.

This decision is independent of Decision A and independent of any vendor. Admissible answers:

1. **No.** Media is never fetched. Attachments are acknowledged and routed to a human. `MULTIMODAL-PERCEPTION-001` closes.
2. **Staff-supplied only.** A staff member photographing an intake slip is inside the business; a
   customer photographing their laundry is not. This is the narrowest answer that still delivers the
   document-intelligence use case the assessment selected.
3. **Customer-supplied, with a stated consent basis, retention window, and deletion path.** This
   answer requires `DEC-008` (retention schedule, currently `OPEN`) to be answered first, because
   there is no deletion path to point at.

Answer 2 is the recommendation on record, because the selected use case — intake slip, care label,
invoice — is satisfied by it and it does not depend on `DEC-008`.

## 4. Decision C — `DEC-006` for a second provider

**Owner:** `SECURITY_PRIVACY_OWNER`
**Registry status:** `OPEN`, fail-closed `NOT_SUPPORTED`

The seven questions, with what a hosted NVIDIA credential does and does not change. Field names are
the `verification` keys in `specs/contracts/provider-data-evidence-v1.schema.json`.

| # | Question | Field | Changed by having the key? |
|---|---|---|---|
| 1 | Is submitted content used to train the provider's models? | `provider_training_terms` | No — must be reviewed for NVIDIA |
| 2 | What is retained, for how long, and under what abuse-log exception? | `retention_and_abuse_log_exception` | No |
| 3 | Which region processes the request, and which subprocessors are involved? | `region_and_subprocessors` | No |
| 4 | What is the deletion path and its proof? | `deletion_path` | No |
| 5 | What are the incident notification terms? | `incident_terms` | No |
| 6 | Is there a recorded Security and Privacy approval? | `security_approval`, `privacy_approval` | No |
| 7 | Is the credential a dedicated service credential, not a personal one? | `dedicated_service_credential` | Partly — a dedicated project credential can now be issued, but a build/trial key does not qualify |

**Prerequisite before this decision can even be recorded:** a `provider-data-evidence-v2` schema that
admits a provider other than OpenAI. Answering Decision C before that produces an approval with
nowhere to live. As of 2026-08-13 the pin no longer blocks writing that schema —
`EVIDENCE-REPIN-001` is complete — but the schema itself does not exist yet, and
`runtime_registry.py:436` additionally asserts `scope["provider"] == registry.model.provider`.

## 5. What each answer unblocks

```
Decision A (ADR-0008) ─┬─ "one model"  → MODEL-ROUTE-001 closes not-required
                       └─ "a route"    → MODEL-ROUTE-001 buildable, after:
                                          EVIDENCE-REPIN-001  ✓ done 2026-08-13
                                          PROVIDER-TRANSPORT-001
                                          MODEL-PIN-001
Decision B (DEC-009) ──┬─ "no"         → MULTIMODAL-PERCEPTION-001 closes
                       └─ "staff only" → buildable, after MODEL-ROUTE-001 + CONSENT-STOP-001 ✓
Decision C (DEC-006) ──── needed only if a non-OpenAI provider is actually adopted;
                          requires provider-data-evidence v2 (not written)
```

`EVIDENCE-REPIN-001` appeared in every branch that ends in built code, which is why it was step 1 and
why it was done first. `CONSENT-STOP-001` in the second branch is already `COMPLETE`, so it gates
nothing. What remains in every branch is a decision, not an engineering item:
`PROVIDER-TRANSPORT-001` and `MODEL-PIN-001` sit behind `PROVIDER-ACCESS-001` and `DEC-006`.

## 6. Recommended sequence

1. ~~**`EVIDENCE-REPIN-001`** first, unconditionally.~~ **Done 2026-08-13** — the owner chose option 1,
   the current bundle is `local-synthetic-suite-v2.json`, the superseded one is retained and asserted
   byte-for-byte, and a pinned file now changes by re-deriving in the same change.
2. **Decision A**, because "one model per turn" removes two queued items and costs nothing to say.
   This is now the next step.
3. **Decision B**, because answer 2 is narrow, cheap, and independent of `DEC-008`.
4. **Decision C last**, and only if a second provider is genuinely being adopted. Reviewing a
   provider's data terms is real work; do not spend it on a provider that a §2 answer may make
   unnecessary.

Do **not** start with Decision C because a credential arrived. That is the order the credential
suggests and the most expensive of the four.

## 7. Signature block

Fill and commit. Unsigned rows keep their fail-closed default, which is the current behaviour.

```yaml
decision_a_adr_0008:
  answer:            # ONE_MODEL_PER_TURN | MODEL_ROUTE | DEFER
  owner:             # PRODUCT_ENGINEERING_OWNER
  decided_at:
  rationale:
decision_b_dec_009:
  answer: STAFF_SUPPLIED_ONLY
  owner: SECURITY_PRIVACY_OWNER
  decided_at: '2026-08-18'
  consent_basis:     # not required — STAFF_SUPPLIED_ONLY does not need CUSTOMER_SUPPLIED_WITH_BASIS
  retention_window:  # not required — see above
decision_c_dec_006_second_provider:
  answer:            # NOT_NOW | REVIEW_REQUESTED | APPROVED
  owner:             # SECURITY_PRIVACY_OWNER
  decided_at:
  provider:
  evidence_record:   # path to the provider data-controls review, requires schema v2
```

Fail-closed defaults while unsigned: media is not fetched (`NOT_SUPPORTED`), no second provider is
admissible, the runtime remains single-model, and every capability remains `NOT_AUTHORIZED`.
