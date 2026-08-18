# Customer Memory Specification v1

**Ngày phát hành:** 2026-08-15
**Trạng thái:** `SPEC_DRAFT_AWAITING_OWNER_APPROVAL`
**Nguồn quyết định:** `AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` §1, §4.1, §12, §13, §14; `context/INVARIANTS.md`
1, 3, 5, 9, 11, 13, 14, 16, 19; ADR-0002, ADR-0003
**Contracts:** kế thừa `contracts/agent-tools-v1.openapi.yaml`; các contract mới
(`conversation-turn-v1`, `conversation-summary-v1`, `context-packet-v1`, `public-corpus-release-v1`)
phải được đưa vào `specs/contracts/` trước khi implementation bắt đầu — spec này ở dạng prose,
**không tự nó cấp phép bất kỳ implementation, capability hay public ingress nào.**

This specification defines how the **customer-facing application** (public Concierge agent serving
end customers of the laundry business) stores, assembles and forgets memory. It does not cover the
engineering delivery-loop memory (`delivery/`, `context/`), which is an internal concern and already
has its own protocol (`context/CONTINUATION_PROTOCOL.md`).

Prose here explains intent. Where prose and a structured contract disagree, the contract wins and CI
must report drift (`specs/README.md`).

## 1. Nguyên tắc

The model has no memory. Everything the agent appears to "remember" is a **server-built,
server-signed context packet** assembled from PostgreSQL at turn time. Four consequences:

1. **PostgreSQL is the only durable customer memory** (`AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` §14).
   There is no agent-side memory store, no provider-side memory, no framework session memory that
   carries authority. Provider/framework session state is recoverable routing state, never authority
   (§12).
2. **Memory is read-only for the model.** The model never writes memory directly. It may *propose*
   content (a draft reply, a candidate extraction); only deterministic server code, after policy and
   human-approval gates, persists anything.
3. **Recovery resumes from inbox/outbox state, never from model memory** (§1). A process restart,
   model swap or runtime replacement must lose nothing except in-flight model latency.
4. **Fail closed on absence.** Missing consent, expired corpus, stale summary version, hash mismatch
   or unresolved contact binding all produce `REQUIRE_HUMAN` / `NOT_SUPPORTED`, never a guess.

A memory system that obeys these four rules can be audited turn-by-turn and replaced without data
migration of any model artifact. That is the entire point.

## 2. Taxonomy — bốn tầng memory

| Tầng | Tên | Durability | Owner | Model access |
|---|---|---|---|---|
| 1 | Customer/business memory | Durable (PostgreSQL) | domain code | read via typed tools only |
| 2 | Conversation memory | Durable, retention-bounded | server (this spec §4) | read via packet only |
| 3 | Working memory (context packet) | Ephemeral, per-turn | context assembler (§5) | this is the model's entire world |
| 4 | Public knowledge corpus | Release-managed | two-party release (§6) | read via packet only |

Mọi thiết kế mới phải xếp được vào đúng một trong bốn tầng. Một store không xếp được vào đây là
generic memory và bị cấm (invariant 19).

## 3. Tier 1 — Customer/business memory (đã tồn tại, không xây mới)

Tier 1 is the existing transactional schema: `orders`, quote snapshots, `contacts`, consent state,
incidents, settlement. This spec changes nothing about it and restates the boundary:

- "What the customer ordered last time" is a **versioned deterministic query** over order history,
  never a learned profile.
- Personalization (e.g. "usual service", "usual pickup address") is derived by deterministic code
  from Tier 1 data and injected into the packet as a signed fact with provenance. The model may
  explain it; it may not originate it (invariant 3, invariant 18).
- Raw address/phone never enters any memory artifact readable by the model; they cross the packet
  boundary only as **opaque tokens** resolvable by tools server-side (§14).
- Consent withdrawal or suppression is evaluated deterministically **before** any packet is built
  (invariant 10). A withdrawn consent means the packet contains no customer facts at all.

## 4. Tier 2 — Conversation memory (mới)

### 4.1 `conversation_turns` (append-only)

One row per finalized turn, scoped to a server-resolved `conversation_binding`
(channel + contact + thread). A turn is written **only after** its outcome is durable:

- inbound turns: written by the channel adapter / agent-runner path after inbox persistence and
  dedupe (invariant 6);
- outbound turns: written after the outbox send is recorded — a draft that was never approved is
  **not** conversation memory (it already lives immutably in `agent_drafts` for review/eval).

Fields (normative shape; final form is contract `conversation-turn-v1`):

- `turn_id` (ULID), `conversation_binding_id`, `direction ∈ {INBOUND, OUTBOUND}`, `occurred_at`;
- `body_redacted` — text after `packages/observability/redaction.py`; **raw phone/address/payment
  data is prohibited** (invariant 13, §14);
- `language`, `channel_message_ref` (idempotency/dedupe key back to inbox/outbox);
- `run_id` nullable FK to `agent_runs` for outbound turns (audit join);
- no embedding column. **Unrestricted transcript embedding is prohibited** (§14).

### 4.2 `conversation_summaries` (versioned rolling summary)

When a conversation exceeds the packet's turn window, the system compacts older turns into a rolling
summary. Rules:

- summaries are **system artifacts**: produced by a pinned summarization path (prompt + model +
  schema pinned in the model registry as release evidence), then **sanitized and validated against a
  typed schema** before persistence;
- every summary carries `summary_version`, `source_turn_range`, `created_at`, and
  `sanitized: true` attestation;
- summaries are **append-only, never overwritten**; the packet references exactly one active summary
  version, so audit can reconstruct what the model saw;
- a summary must not contain: raw PII, prices/totals (facts live in Tier 1 signed results, not in
  prose memory), policy interpretations, or chain-of-thought (invariant 13);
- summarization failure or validation failure ⇒ the packet falls back to raw recent turns only, and
  if that still exceeds budget ⇒ `REQUIRE_HUMAN`. Never truncate silently.

### 4.3 Retention and erasure

- Both tables are subject to `packages/db/retention.py`; retention windows are published
  configuration, not code constants (invariant 4, 11).
- Consent withdrawal triggers deterministic erasure/anonymization of Tier 2 rows for that contact;
  erasure itself is an atomic mutation + audit event (invariant 5).
- Tier 2 data is **never** exported into eval fixtures without the anonymization procedure in
  `PUBLIC_CUSTOMER_POLICY_SPEC_V1.md`.

## 5. Tier 3 — Context packet assembler (deterministic compiler, mới)

The assembler is a server-side module (proposed home: `apps/worker` packet assembly stage +
`packages/contracts/context_packet.py`) that compiles the per-turn packet described in
`AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` §12:

```text
run binding (from agent_runs)
  -> resolve conversation binding, contact, consent (server-side, fail-closed)
  -> Tier 1 signed facts (order/quote/SLA/policy results from domain code)
  -> Tier 2 slice (last 4-6 turns + active summary version)
  -> Tier 4 chunks (0-2, only from signed corpus release, §6)
  -> immutable system policy + tool schemas + budgets (from pinned release artifacts)
  -> context packet (typed schema, packet_hash, per-fact provenance)
  -> persist packet_hash + schema_version into agent_runs BEFORE model invocation
```

Non-negotiable properties:

- **Deterministic:** identical DB state + identical release pins ⇒ byte-identical packet. Any
  nondeterminism (clock, random order) is pinned as explicit fields.
- **Provenance:** every fact in the packet records its source (table + snapshot id / corpus chunk
  hash / release artifact hash). The model never receives an unattributed fact.
- **Server-signed authority fields:** stage, contact binding, consent result, authorization are
  server-derived and never model inputs in the other direction (invariant 9).
- **Budgets enforced before invocation:** token/tool/cost budgets from the model registry; overflow
  fails closed, it does not trim authority fields.
- The assembler runs in the Business Control Plane. The agent runtime receives the finished packet;
  it cannot fetch, extend or cache memory itself (invariant 14, 19).

## 6. Tier 4 — Public knowledge corpus (mới, release-gated)

Only for soft knowledge (service explanations, care instructions, public FAQ). Never for prices,
promotions, fees, SLA, capacity, order status or consent — those are typed tools only (§13).

- Corpus publication is a **signed release manifest**: each chunk has `chunk_hash`, `approver`,
  `effective_from/to`, and `audience`; publication follows two-party release authorization
  (ADR-0006).
- The assembler verifies the manifest signature, chunk hashes and effective period on every packet
  build; missing/expired/hash-mismatch corpus ⇒ zero chunks, fail-closed (invariant 11).
- Retrieval over the approved corpus is permitted (bounded, top-k ≤ 2 chunks per packet). Embedding
  anything outside the signed corpus — above all Tier 1/Tier 2 data — is prohibited.
- `POLICY_RISK_REVIEW.md` and any internal review/risk document must never enter this corpus
  (`specs/README.md`).

## 7. Prohibitions (tổng hợp, normative)

1. No chain-of-thought storage anywhere (invariant 13); `agent_runs` stores `result_safe_summary`
   only.
2. No raw PII in Tier 2/Tier 4 or any model-readable artifact (§14).
3. No vector retrieval over structured facts (§13).
4. No provider-side memory: `store=false` equivalent, shortest configurable retention, transcripts
   and memory-search disabled in any runtime configuration (invariant 16).
5. No long-term learned user profile outside Tier 1 deterministic derivations.
6. No memory write path callable by the model; all persistence is server code under the standard
   atomic mutation + event + audit + outbox discipline (invariant 5).
7. No generic memory/tool capability inside the agent runtime (invariant 19).
8. No use of Tier 2 real customer data before the DEC-006 provider-data review is approved.

## 8. Acceptance checks (điều kiện spec này được coi là implemented)

Mỗi check là một lệnh/test chạy được, sẽ khai báo trong `delivery/WORK_QUEUE.yaml`:

- schema contracts `conversation-turn-v1`, `conversation-summary-v1`, `context-packet-v1`,
  `public-corpus-release-v1` tồn tại trong `specs/contracts/` và `verify_contracts.py` pass;
- migration tests: `conversation_turns`/`conversation_summaries` append-only (UPDATE/DELETE bị
  trigger chặn, theo pattern `0010_agent_run_binding.sql`);
- determinism test: cùng fixture DB + pins ⇒ `packet_hash` giống hệt nhau qua 2 lần build;
- negative tests: missing consent ⇒ `REQUIRE_HUMAN`; summary schema-invalid ⇒ fallback turn-only;
  corpus hash-mismatch ⇒ zero chunks; PII canary trong `body_redacted`/summary ⇒ reject;
- audit test: với một `run_id` bất kỳ, tái dựng được chính xác nội dung model đã thấy từ
  `packet_hash` + provenance;
- retention test: erasure sau consent withdrawal xoá/anonymize Tier 2 và ghi audit event;
- eval: thêm adversarial cases vào `packages/evals` (prompt-injection cố ép model "nhớ" giá, cố
  yêu cầu model ghi memory) — model phải fail closed.

## 9. Phân rã work item (đề xuất đưa vào delivery queue)

1. `MEM-SPEC-001` — chốt 4 JSON contracts trong `specs/contracts/` + cập nhật `CONTEXT_MAP.yaml`
   domain `agent_tools`/`privacy_consent`. **Blocked by:** owner approval của spec này
   (trạng thái hiện tại `SPEC_DRAFT_AWAITING_OWNER_APPROVAL`).
2. `MEM-DB-001` — migrations + append-only triggers + retention wiring cho Tier 2. Depends on
   MEM-SPEC-001.
3. `MEM-ASM-001` — context packet assembler + `packet_hash` ghi vào `agent_runs`. Depends on
   MEM-DB-001. Cần kiểm tra va chạm EVIDENCE-REPIN-001 nếu phải sửa file đang bị hash-pin.
4. `MEM-CORPUS-001` — public corpus release pipeline + manifest verify trong assembler. Depends on
   MEM-ASM-001; không unblock bất kỳ capability nào — `PUBLIC_FAQ` vẫn đi qua G1→G4.
5. `MEM-EVAL-001` — adversarial/eval cases + negative test suite. Depends on MEM-ASM-001.

## 10. Traceability

- §1, §4.1, §12, §13, §14 `AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` — nguồn normative của mọi con số
  (4–6 turns, top-k chunks, packet components).
- Invariants 1, 3, 4, 5, 6, 9, 10, 11, 13, 14, 16, 18, 19 — mapping tại các section tương ứng.
- ADR-0002 (Python authority, provider-data gate), ADR-0003 (bounded Responses runtime),
  ADR-0006 (two-party release).
- `SECURITY_RELIABILITY_SPEC_V1.md` — trust boundary giữa Control Plane và public runtime.
- `PUBLIC_CUSTOMER_POLICY_SPEC_V1.md` — anonymization trước khi dữ liệu thật thành fixture.
