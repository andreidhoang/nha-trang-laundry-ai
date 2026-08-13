# ADR-0008: Inference topology, model-role decomposition, and multimodal scope

**Status:** proposed

**Date:** 2026-08-13
**Extends:** ADR-0003, which reserved exactly this case: "If requirements expand to broad
personal-assistant channels, browser/host tools, dynamic plugins, resumable multi-agent
orchestration or many independent agent profiles, OpenClaw or another maintained agent framework
must be reconsidered through a new ADR." Tiered routing and customer media are expansions of the
same kind. ADR-0002's trust-boundary, business-authority, provider-data and egress constraints
remain binding and are not reopened here.

## Context

A tiered-inference framework has been proposed for this system: size each step of an agent trace to
the entropy of that step rather than to the difficulty of the task, using a small multimodal model
for perception, a high-throughput small model for execution, and a frontier model only as an escape
hatch on failure or ambiguity. The concrete stack named is NVIDIA's — Nemotron Nano Omni for
perception, Lightning for execution, Super/Ultra for escalation, served through NIM, with Switchyard
for routing and NeMo for customization and evaluation.

The proposal is coherent and the underlying principle is sound. It is also, as written, a proposal
about a system that does not yet exist in the shape it assumes. The runtime this repository actually
froze under `RUNTIME-FREEZE-001` is narrower in five specific ways, and each one is a decision
rather than an oversight:

| Frozen property | Where | Consequence for the proposal |
|---|---|---|
| One model per run | `runtime/model-registry-v1.yaml` `model:` is a scalar block with `fallback_model_refs: []` | A route has no representation |
| One model per evidence record | `ResponsesRuntimeEvidence.model_id`, `.immutable_model_release` | A route is unrecordable, so unattributable |
| Text-only input | `ResponsesUserMessage.content: tuple[ResponsesInputText, ...]`, `max_length=1`, `extra="forbid"` | Perception has no input channel |
| Three model calls, six tool calls, 20 s | `limits:` in the registry | Perception + execution + escalation is three calls before any retry |
| Provider posture is hash-pinned | `evidence/agent-shadow/local-synthetic-suite-v1.json` pins the registry and `evidence/provider/openai-data-controls-review-v1.yaml` | Any provider-candidate change terminates at `EVIDENCE-REPIN-001` |

None of these is an argument against the framework. They are the reason it is an ADR and not a
configuration change.

### The question that is genuinely new

Two of the three questions below already have homes. Only the third is unowned:

- **Which provider** is `DEC-006`. Choosing NVIDIA's hosted API rather than OpenAI's changes the
  counterparty and leaves all seven `DEC-006` questions standing, unanswered, against a new vendor.
- **Where inference runs** is `DECISION-HOSTING-001`. Self-hosted inference is a hosting decision
  with a GPU line item, and that decision is already open and already owner-owned.
- **Whether customer media may reach a model at all** is owned by nobody. `ChannelContent` already
  carries `ChannelContentType.IMAGE` and up to ten `ChannelAttachmentRef` values, and the envelope
  deliberately holds references rather than bytes. Nothing in the repository says whether those
  bytes may ever be fetched and sent to an inference endpoint. That silence is the gap this ADR
  opens as `DEC-009`.

## Decision

Three separable decisions are proposed. They are separable on purpose: answering the cheap one does
not commit the expensive one, and the current bundling of "adopt NVIDIA" hides that.

### 1. Inference topology — `DECISION-HOSTING-001`, owner `BUSINESS_OWNER`

Three admissible topologies, in ascending cost and descending third-party exposure:

- **A. Managed third-party API** (status quo shape; OpenAI, NVIDIA-hosted, or another). All seven
  `DEC-006` questions apply to whoever is chosen. Marginal cost tracks volume and is negligible at
  one shop's message rate.
- **B. Self-hosted inference on owner-controlled hardware.** Collapses six of seven `DEC-006`
  questions — training, retention, region, deletion, subprocessors and incident terms all become
  answerable from the owner's own infrastructure posture rather than a vendor contract. The seventh,
  credential handling, remains. This is the strongest argument in the proposal and the framework
  does not make it.
- **C. Hybrid** — self-hosted perception over media, managed API for text reasoning. Keeps the
  highest-sensitivity artifact (a photographed invoice or ID) inside the owner's boundary while
  paying managed-API rates for the low-sensitivity majority.

The cost arithmetic below is what makes B hard at this scale, and it belongs in the decision rather
than in a footnote. **Recommended default: A, revisited at B when either a compliance obligation
names on-premises processing or the tenant count makes C's fixed cost divide sensibly.**

### 2. Model-role decomposition — `PRODUCT_ENGINEERING_OWNER`

Whether one turn may address more than one model release. If yes, the pinning rule in `MODEL-PIN-001`
extends rather than relaxes: **a route is only as pinned as its loosest member.** Every role in the
route carries its own immutable release identifier, every identifier appears in the terminal
evidence, and an eval result produced under route R is not comparable to one produced under route R'.
An unpinned role is a moving alias wearing a different name, and the `runtime_architecture`
prohibition against moving aliases already covers it.

Fail-closed default if undecided: **one model per run**, as today.

### 3. Multimodal scope — `DEC-009`, owner `SECURITY_PRIVACY_OWNER`

Whether customer-supplied media may be fetched from the channel provider and submitted to an
inference endpoint, and if so for which document classes.

This is not a smaller version of the text question. A photograph carries more than the field being
extracted: a customer's invoice photo may include their address, their neighbour's laundry, a face,
a national ID number lying on the same counter. The text path has a normalization step that bounds
what enters the system; media has no equivalent, and `ChannelContent` was written to hold references
precisely so that nothing in this repository would casually acquire the bytes.

Fail-closed default: **`NOT_SUPPORTED`.** Media is referenced, never fetched, never sent.

## Cost arithmetic

Order of magnitude only. The owner should substitute real quotes; the point is the ratio, not the
figure. At roughly 26,000 VND/USD and 730 hours per month:

| Topology | Unit | Monthly USD | Monthly VND | Versus one CSKH staff member (7–8M VND gross) |
|---|---|---|---|---|
| Managed API, one shop's volume | per-token | single-digit | ~100–200k | ~2% |
| Self-hosted, A10-class 24 GB | ~$0.35/hr | ~256 | ~6.7M | ~0.9× — but too small to hold a perception model, an execution model and an escalation model at once |
| Self-hosted, L40S-class 48 GB | ~$1.00/hr | ~730 | ~19M | ~2.5× |
| Self-hosted, H100-class 80 GB | ~$2.50/hr | ~1,825 | ~47M | ~6× |

**The finding: at one shop, self-hosting inference to satisfy a privacy posture costs several times
the labour it is meant to replace.** The GPU bill does not fall when the shop is quiet; a staff
member's hours do. Sovereign inference becomes rational when the fixed cost divides across tenants
or when a compliance obligation makes managed processing inadmissible at any price — not before. The
strategic memo of 2026-08 reached the compatible conclusion from the pricing side and placed "own
model" on its explicit not-now list.

## Consequences

**Accepted if this ADR is accepted:**

- A fourth open decision (`DEC-009`) enters the registry, and the honest count of owner decisions
  standing between here and production rises from eight to nine.
- Two work items enter the queue `BLOCKED`, and neither can start before `EVIDENCE-REPIN-001`
  resolves — the same decision already on the critical path.
- The registry schema gains a version, because a per-role pin cannot be expressed in v1.

**Unchanged regardless of how these are decided:**

- Perception emits observations, never conclusions. An extracted weight is a reading, not a price.
  `price_lines()` in `packages/domain` remains the sole authority over money, and the 6 kg cliff
  makes a reading near a tier boundary a `REQUIRE_HUMAN` design point rather than a rounding choice.
- The model never decides policy, state, permission or recipient, and never sends.
- A perception step is a model call and counts against `max_model_calls`. It does not get an
  exemption for being "just extraction".
- Public and customer-facing automation stays disabled until a signed release gate authorizes it.
  Nothing in this ADR authorizes a capability.

**Rejected:**

- Routing by observed traffic before there is traffic. The framework's own step 3 asks for two weeks
  of shadow routing over real requests; this system has served zero. A router tuned on synthetic
  data measures the fixture author's expectations.
- Any provider benchmark as acceptance evidence. No public leaderboard covers Vietnamese-diacritic
  OCR on a handwritten intake slip or Vietnamese conversational quality in a laundry's register.
  The framework says this itself: nobody's leaderboard is your workload.

## Compliance

An implementation of this ADR must show:

1. every role in a route carries an immutable release identifier, and startup refuses an alias in
   any role;
2. terminal evidence attributes every model call to the role and release that served it;
3. no media byte is fetched while `DEC-009` is `OPEN`;
4. a perception output that would set a price is refused by the domain layer rather than accepted
   and rounded.
