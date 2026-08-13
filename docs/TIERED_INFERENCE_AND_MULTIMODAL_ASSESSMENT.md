# Tiered inference and multimodal perception — assessment against this codebase

**Date:** 2026-08-13
**Subject:** the proposed NVIDIA tiered stack (Nemotron Nano Omni / Lightning / Super / Ultra via
NIM, Switchyard routing, NeMo customization and evaluation)
**Decision anchor:** `docs/adr/0008-inference-topology-and-multimodal-scope.md` (proposed)
**Verdict:** the principle is right, the use case is right, and **no part of it is executable now.**
The specification is written; the implementation is gated on decisions that already existed.

**Kết luận:** nguyên tắc đúng, use case đúng, nhưng **chưa thực thi được phần nào**. Spec đã viết
xong; phần triển khai bị chặn bởi những quyết định vốn đã tồn tại từ trước, không phải quyết định mới.

---

## 1. Where the framework and this codebase already agree

This is worth stating first, because it is the largest part and it is easy to miss under a proposal
that reads as a rearchitecture.

The reference loop the framework describes — perceive, execute, gate, then hand the action to a
human — is not a change to this system. It is a description of it.

| Framework element | Already built here |
|---|---|
| "Content Safety gate → action" | `packages/policy`, a typed fail-closed decision point |
| "Read-and-flag before read-and-act" | Every one of 13 capabilities is `NOT_AUTHORIZED`; the terminal outcomes are `DRAFT_REQUIRES_HUMAN` and `REQUIRE_HUMAN`, and there is no send path |
| "Don't put a VLM in the tool-calling loop" | The loop is capped at 3 model calls and 6 tool calls with a 20 s wall clock; there is no room to re-send anything |
| "Force structured output at the boundary" | `ResponsesRequest.text.format` is `json_schema` with `strict: True`; prose is not an accepted terminal shape |
| "Match model tier to entropy of the step" | The premise of the whole design: 90% of this system's work is deterministic domain code that uses no model at all |

The framework's own strongest claim — that most enterprise agent work is low-entropy execution that
should never touch a frontier model — this repository took further than the framework proposes. It
did not route the low-entropy work to a cheaper model. It removed the model from that work entirely.
Pricing, promotion eligibility, SLA, delivery and state transitions are deterministic Python in
`packages/domain`, and the model is forbidden from computing any of them.

**So the gap is a perception tier, not a rearchitecture.** That is a much smaller and much more
tractable statement than "adopt NVIDIA".

---

## 2. The one argument that changes an open decision

The framework sells the tiered stack on cost: LangChain's Switchyard result, 74% cheaper by routing
7% of calls to a frontier model. At this system's volume — one laundry, a few hundred conversations
a month — 74% of a negligible number is a negligible number. **Cost is not the reason to look at
this stack.**

The reason is `DEC-006`, and the framework never mentions it.

`DEC-006` is the longest pole in the project. It is seven questions — training, retention, region,
deletion, subprocessors, incident terms, credential — and until a Security/Privacy owner answers all
seven, the entire agent path is `NOT_SUPPORTED` and every one of 33 recorded eval results carries
`status: SKIP`. Six of those seven questions are questions about **a third party**.

Self-hosted inference on hardware the owner controls collapses six of the seven. Not by answering
them well — by removing the counterparty they are about.

**Đây mới là lý do thực sự để xem xét NVIDIA:** không phải tiết kiệm chi phí, mà là suy luận tại chỗ
(self-hosted) làm biến mất sáu trong bảy câu hỏi của `DEC-006` — nút thắt dài nhất của dự án.

And then the arithmetic in ADR-0008 §"Cost arithmetic" runs the other way: at one shop, the GPU to
host that posture costs 2.5–6× the CSKH staff member the whole product is meant to replace. A GPU
bills while the shop sleeps; a person does not.

That is the honest shape of it. The strongest technical argument for this stack is real, and at
current scale it loses to a spreadsheet. It stops losing when the fixed cost divides across tenants,
or when a compliance obligation names on-premises processing at any price. Neither is true today,
and the 2026-08 strategic memo independently placed "own model" on its not-now list.

---

## 3. Five collisions with the frozen runtime

Each is a file, not an opinion.

**3.1 The runtime is text-only by construction, not by omission.**

```python
class ResponsesUserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: tuple[ResponsesInputText, ...] = Field(min_length=1, max_length=1)
```

`apps/worker/src/nha_trang_laundry_worker/responses_runtime.py`. Exactly one text item, no
`input_image` variant in the `ResponsesInputItem` union, and `extra="forbid"` on every model in the
chain. Multimodal input is a contract change with a schema version, not a parameter.

**3.2 The evidence record holds one model, so a route is unattributable.**

`ResponsesRuntimeEvidence` carries a single `model_id` and a single `immutable_model_release`. A run
that used a perception model and an execution model can be recorded as having used one of them or
neither. `MODEL-PIN-001` requires that "an eval result carries the pin it ran against"; under a
route, the current schema cannot satisfy that requirement even in principle.

**3.3 The registry holds one model, and it is hash-pinned.**

`runtime/model-registry-v1.yaml` has a scalar `model:` block, `provider: openai`, and
`fallback_model_refs: []`. It is one of the 21 files hash-pinned by
`evidence/agent-shadow/local-synthetic-suite-v1.json` — as is
`evidence/provider/openai-data-controls-review-v1.yaml`, so **the provider posture itself is frozen.**

Consequence worth stating plainly: any provider-candidate change, NVIDIA or otherwise, terminates at
`EVIDENCE-REPIN-001`. This direction adds no new blocker. It raises the value of the one already on
the critical path — the item described in its own packet as "the cheapest unblock in the project: a
decision and a re-derivation, not weeks."

**3.4 Three model calls is the whole budget.**

`limits.max_model_calls: 3`. Perception, execution and one escalation is exactly three, leaving zero
for a retry. Either the budget rises — which changes the cost ceiling, the 20 s deadline arithmetic
and the frozen runtime shape — or the route is two tiers with no escape hatch. Both are decisions.
Neither is free.

**3.5 The wire protocol is Responses-shaped and the mapping is unverified.**

`provider_transport: responses`. The request model requires `store: false`, a strict `json_schema`
text format, and `include: ("reasoning.encrypted_content",)` for stateless tool continuation. Rather
than assert what any particular vendor endpoint accepts, state the invariant and make it an
acceptance item: **any transport must preserve `store: false`, strict schema-constrained output, and
stateless continuation at the `ResponsesProviderTransport` seam, or it is not admissible.** Verifying
that mapping against a live endpoint belongs to `PROVIDER-TRANSPORT-001` and cannot be done from
documentation.

---

## 4. Use case selection: document intelligence, and why not the other four

The framework ranks five use cases and recommends starting at #1. For this system that
recommendation holds, and the reasoning is stronger here than the framework's generic version.

**Selected — document intelligence.** Three artifacts in this business are documents:

- the handwritten intake slip a staff member fills at the counter;
- the garment care label, which determines whether an item is washable at all;
- the invoice, which the 2026-08 memo identified as the compliance forcing function — its finding
  F2 was that urgency in this market is priced by compliance, not efficiency.

Its failure mode is a wrong reading, and a wrong reading is gateable by the human approval step this
system already requires for everything. That is the property that makes it admissible first.

**Not now, with reasons specific to here:**

| Use case | Why not |
|---|---|
| Industrial SOP / quality verification | The evidence cited is Foxconn's manufacturing line. A laundry has no SOP instrumentation, and `SHOP-INSTRUMENT-001` is 4–6 weeks of work before any observation exists to verify against |
| Video analytics | Requires cameras, a retention answer, and consent from everyone in frame. `DEC-008` does not have a schedule for text yet |
| Computer-use automation | Actions against a production system. This repository's entire posture is that the model does not act |
| Deep research / code agents | The framework itself rates this the weakest evidence, and there is no such workload here |

---

## 5. The framework's own validation steps cannot be run here

This matters more than the technical collisions, because these are the steps that would tell you
whether any of it works.

**Step 3, shadow routing.** "Put Switchyard in `observe_only`, run two weeks of real traffic." There
is no traffic. The system has served zero customers and made zero model calls. A router tuned on
synthetic fixtures measures the fixture author's expectations, not the workload. The framework also
notes Switchyard is pre-alpha and not for production — correct, and beside the point when the input
it needs does not exist.

**Step 4, the customization ladder.** Prompt → LoRA SFT → GRPO. The diagnostic is right and useful:
"it can't read the field" is SFT, "it gives up on step 4" is RL. Both rungs need labelled examples
from this business. There are none, and `CORPUS-CONSENT-001` blocks collecting them — a Vietnamese
customer-language corpus requires a consent basis that does not exist.

**Step 5, build the eval before the agent.** This is the step this repository agrees with most and
can act on least. `packages/evals` already holds 669 combinatorial cases generated from
`price_lines()` rather than hand-written, plus 12 synthetic suites. What it cannot hold is a
Vietnamese perception eval, because that needs real intake slips in real handwriting, which needs
`CORPUS-CONSENT-001`, which needs an owner.

No public benchmark substitutes. Vietnamese diacritics on handwritten paper under a shop's lighting
is not a benchmark, and the framework's closing line applies with full force: nobody's leaderboard is
your workload.

---

## 6. What is executable now

Everything in this section is done as of this document. Nothing else was.

| Artifact | Status |
|---|---|
| `docs/adr/0008-inference-topology-and-multimodal-scope.md` | Written, status **proposed** — not accepted |
| `DEC-009` — customer media exposure to a model | Added to `context/DECISION_REGISTRY.yaml` as `OPEN`, owner `SECURITY_PRIVACY_OWNER`, fail-closed `NOT_SUPPORTED` |
| `MODEL-ROUTE-001` | Queued `BLOCKED`, packet written |
| `MULTIMODAL-PERCEPTION-001` | Queued `BLOCKED`, packet written |

Deliberately **not** done, each for a stated reason:

- **No perception contract in `packages/contracts`.** The document classes to support are the
  owner's call, tied to `DEC-005` and the compliance driver. An unconsumed contract would be carried
  by every lint, type-check and test run from now until the decision arrives.
- **No NVIDIA dependency in `pyproject.toml`.** A dependency is a supply-chain commitment; ADR-0003
  requires it be justified by a decision, and there is none.
- **No scaffold transport, even with `provider_backed = False`.** `AGENT-PIPELINE-001` explicitly
  refuses provider-backed transports pending `PROVIDER-TRANSPORT-001`, and a scaffold that exists
  only to be replaced reads as progress while adding maintenance.
- **No edit to `runtime/model-registry-v1.yaml`**, not even a commented candidate. It is hash-pinned;
  editing it invalidates frozen evidence, which is `EVIDENCE-REPIN-001`'s decision to make.
- **No change to `DEC-005` or `DEC-006`.** Both stay `OPEN`. This assessment is an input to them, not
  an answer.

---

## 7. Answering the question directly

**Should we build this expansion on the current codebase now?**

**No — and the codebase is the reason it will be cheap later.**

The architecture the framework describes is the architecture this system already has: deterministic
authority, a bounded model loop, a safety gate, a human at the end. Adding a perception tier to it
is a contract version and a registry version, not a rewrite. That work is small **because** the
runtime was frozen narrow.

What blocks it is not difficulty. It is that four things must be true first, none of which an
engineer can make true:

1. `EVIDENCE-REPIN-001` decided, so the pinned core can change at all;
2. `DEC-006` resolved, so any model can be invoked;
3. `DEC-009` answered, so customer media may — or may not — be fetched;
4. real intake slips under a consent basis, so a perception eval can exist.

Building perception before (4) produces a model that reads the fixture author's handwriting.

**Trả lời trực tiếp: chưa.** Không phải vì khó, mà vì bốn điều kiện trên phải đúng trước, và không
điều nào trong số đó là việc kỹ sư có thể tự làm. Kiến trúc hiện tại đã đúng hướng — chính vì runtime
được đóng băng ở phạm vi hẹp mà sau này việc thêm tầng perception sẽ rẻ.

**The one recommendation:** resolve `EVIDENCE-REPIN-001`. It was the highest-leverage item before
this proposal and this proposal raises its value — it now unblocks six existing items *and* both new
ones, and it costs a decision plus a re-derivation, not weeks.
