# TASK-multimodal-perception-001 — a perception tier that emits observations, never conclusions

**Goal:** let a model read a document the business already handles on paper, and let nothing it reads
become a price, a policy or a decision.

**Domains:** `runtime_architecture`, `privacy_consent`, `evaluation_release`

**Stable work item:** `MULTIMODAL-PERCEPTION-001`

**Stage:** M4B
**Risk:** HIGH — this is the first path by which a customer's photograph would leave this system.

## Why this exists

Three artifacts in this business are documents: the handwritten intake slip filled at the counter,
the garment care label that determines whether an item is washable at all, and the invoice, which the
2026-08 strategic memo identified as the compliance forcing function in this market.

`ChannelContent` already models the input. `ChannelContentType.IMAGE` exists,
`ChannelAttachmentRef` exists, and up to ten may arrive on one message. The envelope holds
**references, not bytes** — deliberately, so that nothing in this repository casually acquires a
customer's photograph. Nothing consumes them today.

`docs/adr/0008-inference-topology-and-multimodal-scope.md` proposes closing that gap and, in the same
motion, opens `DEC-009`, because whether those bytes may ever be fetched and submitted to an
inference endpoint is a question nobody in this repository currently owns.

## Required design

**The perception boundary emits a schema, not prose.** If perception returns natural language, the
execution tier re-parses it, the system pays twice, and a lossy hop is introduced between the
document and the decision. Structured output is forced at the boundary, using the same strict
`json_schema` mechanism the runtime already requires.

**Perception emits observations. It never emits conclusions.** This is the invariant the whole item
turns on:

| Admissible observation | Inadmissible conclusion |
|---|---|
| `weight_as_written: "5.8"`, `weight_unit_as_written: "kg"` | any price, subtotal or total |
| `service_code_printed: "GIAT_SAY"` | which catalog service applies |
| `legibility: PARTIAL`, `field_confidence: LOW` | that a field may be inferred from context |
| `text_regions: [...]` verbatim | a normalized, corrected or translated value |

Every observation carries a legibility state, and **`UNREADABLE` is a first-class outcome, not an
error.** A model that reports it cannot read a field is behaving correctly; a model that guesses is
the failure this table exists to prevent.

**The 6 kg cliff is the design point.** `price_lines()` in `packages/domain` remains the sole
authority over money. An observed weight near a tier boundary must reach a human: a reading of
`5.9 kg` and a reading of `6.1 kg` differ by a rounding error and by a pricing tier, and the model
does not get to decide which side it landed on. The boundary band is a domain constant, not a
perception parameter.

**A perception call is a model call.** It counts against `limits.max_model_calls` and against the
20 s wall clock. It gets no exemption for being extraction.

**Media handling, in order:**

1. Fetch is a provider call and requires its own authorization, separate from the inference
   authorization. Until `DEC-009` is `RESOLVED`, no byte is fetched.
2. Bytes never enter the envelope, never enter evidence, never enter a log or span attribute, and
   are never persisted beyond the bounded turn.
3. The retention class for fetched media is `DEC-008`'s to set, and `RETENTION-001` already records
   that 5 of 9 existing classes cannot be purged at all. A media class that lands in a ledger-backed
   table is a class that cannot be deleted; that must be established before the first fetch, not
   discovered after.
4. A declared media type is a claim by the sender, not a fact. Validate the actual content;
   `declared_media_type` is untrusted input.

## Constraints

- **Blocked on `DEC-009`** (`OPEN`, fail-closed `NOT_SUPPORTED`), **`DEC-006`** (no model may be
  invoked at all) and **`DEC-008`** (no retention schedule for what would be fetched).
- **Blocked on `MODEL-ROUTE-001`.** A perception model is a second role; without per-role pinning its
  release is unattributable, and `MODEL-PIN-001`'s rule would be violated by construction.
- `ResponsesUserMessage.content` is `max_length=1` with only `ResponsesInputText`, and every model in
  the chain sets `extra="forbid"`. Adding an image input item is a contract version, not a parameter.
  Version it; do not widen v1.
- **A photograph carries more than the field being extracted** — an address, a face, a bystander's
  belongings, an ID lying on the same counter. The text path has a normalization step that bounds
  what enters the system. Media has none. Do not assume the text-path consent basis covers media.
- No public benchmark counts as acceptance evidence. Vietnamese diacritics in handwriting under a
  shop's lighting is not a benchmark. The eval is task completion on this business's own documents.
- The corpus needed for that eval is blocked on `CORPUS-CONSENT-001`. Do not substitute synthetic
  handwriting; it measures the fixture author.
- No capability moves. Read-and-flag only: perception output reaches a human review surface, never
  an action and never a send.

## Required tests

- an image input item is accepted only under the versioned contract, and a v1 request carrying one is
  rejected rather than silently coerced;
- with `DEC-009` `OPEN`, an inbound `IMAGE` message produces `NOT_SUPPORTED` and **no fetch is
  attempted** — asserted against the transport, not by inspection;
- a perception response containing any monetary field is refused by the domain layer;
- an observed weight inside the 6 kg boundary band produces `REQUIRE_HUMAN`, on both sides of the
  boundary;
- `UNREADABLE` and `PARTIAL` legibility propagate to a human rather than being filled from context;
- a `declared_media_type` that disagrees with the actual content is rejected;
- no image byte, no derived thumbnail and no OCR text appears in evidence, logs or span attributes;
- the perception call counts against the model-call budget and the 20 s deadline, proven by a run
  that exhausts both.

## Done when

- perception is expressible, versioned, and refuses to conclude;
- the fetch path is provably inert while `DEC-009` is `OPEN`;
- a task-completion eval exists over this business's own documents, or the item is held at exactly
  that point and the hold is recorded rather than papered over with a benchmark score;
- rollback is reverting to the text-only contract, which removes a capability that was never
  authorized and destroys no evidence.
